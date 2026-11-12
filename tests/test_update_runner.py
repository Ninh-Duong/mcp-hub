"""Offline tests for detached update orchestration."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_hub import config, update_runner, update_state


class UpdateRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temp.name) / "update.json"
        self.addCleanup(self.temp.cleanup)

    def test_child_discovery_includes_credential_required_and_excludes_disabled(self) -> None:
        root = Path(self.temp.name) / "checkout"
        hub = root / "mcp-hub"
        enabled = root / "enabled-child"
        disabled = root / "disabled-child"
        hub.mkdir(parents=True)
        enabled.mkdir()
        disabled.mkdir()
        switches = (
            config.ServerSwitch("enabled-child", True, "credentials required"),
            config.ServerSwitch("disabled-child", False, None),
        )
        with patch.object(update_runner, "HUB_ROOT", hub), patch.object(update_runner, "server_switches", return_value=switches):
            result = update_runner._load_children()
        self.assertEqual([item["id"] for item in result], ["enabled-child"])

    def test_child_discovery_accepts_unresolved_checkout_path(self) -> None:
        # A checkout reached through a relative, short (8.3), or linked path must still find its siblings.
        root = Path(self.temp.name) / "checkout"
        (root / "mcp-hub").mkdir(parents=True)
        (root / "enabled-child").mkdir()
        switches = (config.ServerSwitch("enabled-child", True, None),)
        previous = Path.cwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, previous)
        with patch.object(update_runner, "HUB_ROOT", Path("checkout") / "mcp-hub"), \
             patch.object(update_runner, "server_switches", return_value=switches):
            result = update_runner._load_children()
        self.assertEqual([item["id"] for item in result], ["enabled-child"])

    def test_check_reports_hub_and_enabled_children(self) -> None:
        children = [{"id": "child", "kind": "child", "repo": self.state_path.parent, "state": "checking",
                     "phase": "check", "error_code": None}]
        with patch.object(update_runner, "_load_children", return_value=children), \
             patch.object(update_runner, "HUB_ROOT", self.state_path.parent), \
             patch.object(update_runner, "_check_repo", side_effect=lambda item: (item, None)), \
             patch.object(update_state, "state_path", return_value=self.state_path):
            result = update_runner._check("run")
        self.assertEqual(result, 0)
        self.assertEqual({item["id"] for item in update_state.read(self.state_path)["components"]}, {"child", "mcp-hub"})

    def test_failed_child_isolated_and_final_state_is_partial(self) -> None:
        children = [
            {"id": name, "kind": "child", "repo": self.state_path.parent, "state": "checking",
             "phase": "check", "error_code": None}
            for name in ("broken", "current")
        ]

        def check(item):
            return item, "git_failed" if item["id"] == "broken" else None

        with patch.object(update_runner, "_load_children", return_value=children), \
             patch.object(update_runner, "_check_repo", side_effect=check), \
             patch.object(update_runner, "_restart_hub"), \
             patch.object(update_runner, "_smoke", return_value=0), \
             patch.object(update_state, "state_path", return_value=self.state_path):
            result = update_runner.run_update("run")
        status = update_state.read(self.state_path)
        self.assertEqual(result, 1)
        self.assertEqual(status["state"], "partial")
        self.assertEqual(next(item for item in status["components"] if item["id"] == "broken")["error_code"], "git_failed")

    def test_current_repositories_do_not_stop_or_restart_hub(self) -> None:
        children = [{"id": "current", "kind": "child", "repo": self.state_path.parent,
                     "state": "checking", "phase": "check", "error_code": None}]
        with patch.object(update_runner, "_load_children", return_value=children), \
             patch.object(update_runner, "_check_repo", side_effect=lambda item: (item, None)), \
             patch.object(update_runner, "_restart_hub") as restart, \
             patch.object(update_state, "state_path", return_value=self.state_path):
            result = update_runner.run_update("run")
        self.assertEqual(result, 0)
        restart.assert_not_called()
        self.assertEqual(update_state.read(self.state_path)["state"], "complete")

    def test_retry_limits_child_updates_to_selected_ids(self) -> None:
        children = [
            {"id": name, "kind": "child", "repo": self.state_path.parent, "state": "checking",
             "phase": "check", "error_code": None}
            for name in ("first", "second")
        ]
        with patch.object(update_runner, "_load_children", return_value=children), \
             patch.object(update_runner, "_check_repo", side_effect=lambda item: (item, None)), \
             patch.object(update_runner, "_update_repo", side_effect=lambda item: (item, None)), \
             patch.object(update_runner, "_restart_hub"), \
             patch.object(update_runner, "_smoke", return_value=0), \
             patch.object(update_state, "state_path", return_value=self.state_path):
            result = update_runner.run_update("run", {"second"})
        status = update_state.read(self.state_path)
        self.assertEqual(result, 0)
        self.assertEqual({item["id"] for item in status["components"]}, {"second", "mcp-hub"})

    def test_child_retry_restarts_hub_without_updating_hub_checkout(self) -> None:
        child = {"id": "child", "kind": "child", "repo": self.state_path.parent,
                 "state": "checking", "phase": "check", "error_code": None}

        def available(item):
            item["available"] = True
            return item, None

        with patch.object(update_runner, "_load_children", return_value=[child]), \
             patch.object(update_runner, "_check_repo", side_effect=available) as check, \
             patch.object(update_runner, "_update_repo", side_effect=self.successful_pull) as pull, \
             patch.object(update_runner, "_restart_hub", return_value=True) as restart, \
             patch.object(update_runner, "_smoke", return_value=0), \
             patch.object(update_state, "state_path", return_value=self.state_path):
            result = update_runner.run_update("run", {"child"})
        self.assertEqual(result, 0)
        self.assertEqual(check.call_count, 1)
        self.assertEqual(pull.call_count, 1)
        self.assertEqual(restart.call_args.args, ("restart",))

    @staticmethod
    def successful_pull(item):
        item.update(state="complete", phase="pull")
        return item, None

    def test_failed_hub_update_reports_failed_hub_start(self) -> None:
        def available(item):
            item["available"] = True
            return item, None

        def pull(item):
            item.update(state="failed", phase="pull")
            return item, "git_failed"

        with patch.object(update_runner, "_load_children", return_value=[]), \
             patch.object(update_runner, "_check_repo", side_effect=available), \
             patch.object(update_runner, "_update_repo", side_effect=pull), \
             patch.object(update_runner, "_restart_hub", side_effect=(True, False)), \
             patch.object(update_state, "state_path", return_value=self.state_path):
            result = update_runner.run_update("run")
        hub = next(item for item in update_state.read(self.state_path)["components"] if item["id"] == "mcp-hub")
        self.assertEqual(result, 1)
        self.assertEqual((hub["state"], hub["error_code"]), ("failed", "hub_start_failed"))


if __name__ == "__main__":
    unittest.main()
