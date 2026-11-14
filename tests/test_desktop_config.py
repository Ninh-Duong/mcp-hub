"""The Config tab's logic: the Hub's settings fields, the row states, the child warning, and the file-open call."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mcp_hub.desktop import config_page, hub_api, status
from tests.test_desktop import TOKEN, FakeOpener
from tests.test_desktop_client import write


def switch(**changes: object) -> hub_api.ServerSwitch:
    fields = {"server_id": "mcp-server-logs", "enabled": True}
    fields.update(changes)
    return hub_api.ServerSwitch(**fields)  # type: ignore[arg-type]


class StatusTest(unittest.TestCase):
    def test_a_press_outcome_wins_until_the_next_read(self) -> None:
        item = switch(missing=("openobserve.dev.token",), requirements="declared")
        self.assertEqual(config_page.config_status(item, False, ("error", "x")), config_page.ConfigStatus("error", "x"))

    def test_an_invalid_file_is_an_error_before_any_gap(self) -> None:
        item = switch(missing=("a.b",), requirements="invalid")
        self.assertEqual(config_page.config_status(item, False, None).key, "desktop.settings.config.invalid")

    def test_missing_keys_are_named_and_warn(self) -> None:
        item = switch(missing=("openobserve.dev.token", "seq.api_key"), requirements="declared")
        status_ = config_page.config_status(item, False, None)
        self.assertEqual((status_.tone, status_.key), ("wait", "desktop.settings.config.missing"))
        self.assertEqual(status_.values, (("keys", "openobserve.dev.token, seq.api_key"),))

    def test_a_filled_file_waits_for_a_restart_when_the_running_hub_has_the_gap(self) -> None:
        item = switch(requirements="declared")
        self.assertEqual(config_page.config_status(item, True, None).key, "desktop.settings.config.readyRestart")
        self.assertEqual(config_page.config_status(item, False, None), config_page.ConfigStatus("ok", "desktop.settings.config.ready"))

    def test_no_declaration_is_off_not_an_error(self) -> None:
        self.assertEqual(config_page.config_status(switch(), False, None),
                         config_page.ConfigStatus("off", "desktop.settings.config.none"))


class HubSettingsFieldsTest(unittest.TestCase):
    def test_the_new_fields_are_read_and_bad_values_fall_back(self) -> None:
        settings = hub_api.settings_from({"editable": True, "servers": [
            {"id": "mcp-server-logs", "enabled": True, "missing": ["openobserve.dev.token", 7],
             "requirements": "declared", "config_file_exists": True},
            {"id": "mcp-jira-sync", "enabled": False, "missing": "nope", "requirements": "weird"},
        ]})
        first, second = settings.servers
        self.assertEqual(first.missing, ("openobserve.dev.token",))
        self.assertEqual((first.requirements, first.config_file_exists), ("declared", True))
        self.assertEqual((second.missing, second.requirements, second.config_file_exists), ((), "none", False))


class SnapshotTest(unittest.TestCase):
    def test_a_connected_child_with_a_gap_is_a_warning_and_others_are_not(self) -> None:
        snapshot = status.snapshot_from_status({
            "children": {"mcp-server-logs": "connected", "mcp-jira-sync": "connected"},
            "config_incomplete": {"mcp-server-logs": ["openobserve.dev.token"]},
        })
        rows = {row.server_id: row for row in snapshot.children}
        self.assertEqual((rows["mcp-server-logs"].tone, rows["mcp-server-logs"].incomplete), ("wait", True))
        self.assertEqual((rows["mcp-jira-sync"].tone, rows["mcp-jira-sync"].incomplete), ("ok", False))

    def test_a_disabled_child_with_a_gap_stays_off(self) -> None:
        snapshot = status.snapshot_from_status({
            "children": {"mcp-server-logs": "disabled"}, "config_incomplete": {"mcp-server-logs": ["a.b"]},
        })
        self.assertEqual(snapshot.children[0].tone, "off")


class PrepareChildConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = write(Path(self.tmp.name) / "connection.json",
                          {"version": 1, "url": "http://127.0.0.1:8765", "token": TOKEN})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_the_client_asks_for_the_path_only_and_sends_the_bearer(self) -> None:
        opener = FakeOpener({"/dashboard/api/settings/servers/mcp-server-logs/config-file": {"path": "example/child-config.json"}})
        client = hub_api.HubClient(opener=opener, connection_file=self.path)
        self.assertEqual(client.prepare_child_config("mcp-server-logs"), "example/child-config.json")
        self.assertEqual(opener.requests[-1][:3], ("POST", "/dashboard/api/settings/servers/mcp-server-logs/config-file",
                                                   f"Bearer {TOKEN}"))

    def test_a_reply_without_a_path_is_refused(self) -> None:
        opener = FakeOpener({"/dashboard/api/settings/servers/mcp-server-logs/config-file": {}})
        client = hub_api.HubClient(opener=opener, connection_file=self.path)
        with self.assertRaises(ValueError):
            client.prepare_child_config("mcp-server-logs")


if __name__ == "__main__":
    unittest.main()
