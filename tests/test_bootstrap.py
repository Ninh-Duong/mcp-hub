"""bootstrap: the start-up inputs shared by the shared service and serve() (moved from service.py and server.py)."""
from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mcp_hub.bootstrap import load_settings_logged, load_start_inputs
from mcp_hub.settings import Settings


class _RecordingAudit:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def write(self, event: str, **fields) -> None:
        self.events.append((event, fields))


class StartInputsTest(unittest.TestCase):
    def test_every_input_is_read_once_and_returned(self) -> None:
        settings, config = Settings(), SimpleNamespace(name="config")
        with patch("mcp_hub.bootstrap.load_settings", return_value=settings) as load_settings, \
                patch("mcp_hub.bootstrap.load_config", return_value=config), \
                patch("mcp_hub.bootstrap.load_playbooks", return_value={"pb": 1}), \
                patch("mcp_hub.bootstrap.load_projects", return_value={"proj": 2}):
            inputs = load_start_inputs(_RecordingAudit())
        load_settings.assert_called_once()
        self.assertIs(inputs.settings, settings)
        self.assertIs(inputs.config, config)
        self.assertEqual(inputs.playbooks, {"pb": 1})
        self.assertEqual(inputs.projects, {"proj": 2})

    def test_settings_already_read_are_not_read_again(self) -> None:
        settings = Settings()
        with patch("mcp_hub.bootstrap.load_settings") as load_settings, \
                patch("mcp_hub.bootstrap.load_config", return_value=SimpleNamespace()), \
                patch("mcp_hub.bootstrap.load_playbooks", return_value={}), \
                patch("mcp_hub.bootstrap.load_projects", return_value={}):
            inputs = load_start_inputs(_RecordingAudit(), settings)
        load_settings.assert_not_called()
        self.assertIs(inputs.settings, settings)

    def test_a_bad_input_is_logged_once_and_raised(self) -> None:
        audit = _RecordingAudit()
        with patch("mcp_hub.bootstrap.load_settings", return_value=Settings()), \
                patch("mcp_hub.bootstrap.load_config", side_effect=ValueError("bad config")):
            with self.assertRaises(ValueError):
                load_start_inputs(audit)
        self.assertEqual(
            audit.events,
            [("config.load.failed", {"level": logging.ERROR, "error_type": "ValueError", "error": "bad config"})],
        )


class LoadSettingsLoggedTest(unittest.TestCase):
    def test_a_bad_settings_value_is_logged_to_a_default_audit_log_and_raised(self) -> None:
        audit = MagicMock()
        with patch("mcp_hub.bootstrap.load_settings", side_effect=ValueError("bad value")), \
                patch("mcp_hub.bootstrap.AuditLog", return_value=audit):
            with self.assertRaises(ValueError):
                load_settings_logged()
        audit.write.assert_called_once_with(
            "config.load.failed", level=logging.ERROR, error_type="ValueError", error="bad value",
        )
        audit.close.assert_called_once()

    def test_good_settings_open_no_audit_log(self) -> None:
        with patch("mcp_hub.bootstrap.load_settings", return_value=Settings()), \
                patch("mcp_hub.bootstrap.AuditLog") as audit_class:
            self.assertIsInstance(load_settings_logged(), Settings)
        audit_class.assert_not_called()


if __name__ == "__main__":
    unittest.main()
