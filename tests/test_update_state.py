"""The small persistent status shared with the desktop app."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mcp_hub import update_state


class UpdateStateTest(unittest.TestCase):
    def test_round_trip_keeps_only_bounded_public_fields(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "update.json"
            value = {"run_id": "run", "state": "running", "started_at": "now", "components": [
                {"id": "child", "kind": "child", "state": "failed", "phase": "fetch",
                 "error_code": "git_failed", "repo": "private/path"}],
                "events": [{"component": "child", "phase": "fetch", "state": "failed",
                            "error_code": "git_failed", "at": "now", "stdout": "private output"}]}
            update_state.write(value, path)
            result = update_state.read(path)
            self.assertEqual(result["components"], [{"id": "child", "kind": "child", "state": "failed",
                                                      "phase": "fetch", "error_code": "git_failed"}])
            self.assertNotIn("stdout", result["events"][0])
            self.assertNotIn("repo", result["components"][0])

    def test_corrupt_and_unknown_state_returns_idle(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "update.json"
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(update_state.read(path), update_state.empty_state())
            update_state.write({"state": "surprise"}, path)
            self.assertEqual(update_state.read(path)["state"], "idle")

    def test_events_are_capped(self) -> None:
        value = update_state.empty_state()
        for index in range(50):
            update_state.add_event(value, str(index), "check", "complete")
        self.assertEqual(len(value["events"]), update_state.MAX_EVENTS)
        self.assertEqual(value["events"][0]["component"], "10")


if __name__ == "__main__":
    unittest.main()
