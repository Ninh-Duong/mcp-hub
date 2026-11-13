"""Desktop settings switches: Track AI clients, BitNet, and the two sign-in entries are Fluent switches.

The MCP rows and the appearance segments stay segmented and still report their choices.
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from qfluentwidgets import SegmentedWidget, SwitchButton

    from mcp_hub.desktop import app, hub_page, settings, theme, widgets
    from mcp_hub.desktop.hub_api import HubSettings, ServerSwitch
    from mcp_hub.desktop.i18n import Translator
    QT_MISSING = None
except ImportError as exc:  # Qt's system libraries are missing (for example a bare Linux host)
    QT_MISSING = f"Qt is unavailable: {exc}"


def setUpModule() -> None:
    if QT_MISSING is None:
        app.application()


def noop(*_args: object) -> None:
    """A callback that does nothing."""


def hub_settings(*, activity_ui: bool = False, bitnet: bool = False, editable: bool = True) -> HubSettings:
    return HubSettings(activity_ui=activity_ui, servers=(), editable=editable, bitnet=bitnet)


def toggle(switch: SwitchButton) -> None:
    """Click a switch as the user does: its indicator is the clickable part, and it ignores clicks while disabled."""
    switch.indicator.click()


def pick(segmented: SegmentedWidget, value: object) -> None:
    """Click one segment, as the user does."""
    widgets.segment(segmented, value).click()


@unittest.skipIf(QT_MISSING, QT_MISSING or "")
class HubSwitchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[tuple] = []
        self.page = hub_page.HubPage(
            on_start=noop,
            on_restart=noop,
            on_stop=noop,
            on_tracking=lambda value: self.calls.append(("tracking", value)),
            on_autostart=lambda target, value: self.calls.append(("autostart", target, value)),
            on_bitnet=lambda value: self.calls.append(("bitnet", value)),
        )
        self.page.retranslate(Translator("en"))

    def switches(self) -> list[SwitchButton]:
        return [self.page.tracking_switch, self.page.bitnet_switch, *self.page.autostart_switches.values()]

    def sync_running(self, *, activity_ui: bool = False, bitnet: bool = False, editable: bool = True,
                     autostart: dict | None = None) -> None:
        saved = {"hub": False, "app": False} if autostart is None else autostart
        self.page.sync("running", hub_settings(activity_ui=activity_ui, bitnet=bitnet, editable=editable), saved)

    def test_track_ai_clients_switch_reports_the_new_value(self) -> None:
        self.sync_running(activity_ui=False)
        self.assertIsInstance(self.page.tracking_switch, SwitchButton)
        self.assertFalse(self.page.tracking_switch.isChecked())
        toggle(self.page.tracking_switch)
        self.assertEqual(self.calls, [("tracking", True)])
        self.assertTrue(self.page.tracking_switch.isChecked())

    def test_bitnet_switch_reports_the_new_value(self) -> None:
        self.sync_running(bitnet=False)
        toggle(self.page.bitnet_switch)
        self.assertEqual(self.calls, [("bitnet", True)])
        self.assertTrue(self.page.bitnet_switch.isChecked())

    def test_each_autostart_switch_reports_its_target(self) -> None:
        self.sync_running(autostart={"hub": False, "app": True})
        toggle(self.page.autostart_switches["hub"])
        toggle(self.page.autostart_switches["app"])
        self.assertEqual(self.calls, [("autostart", "hub", True), ("autostart", "app", False)])

    def test_sync_shows_saved_values_without_reporting_them(self) -> None:
        self.sync_running(activity_ui=True, bitnet=True, autostart={"hub": True, "app": False})
        self.assertTrue(self.page.tracking_switch.isChecked())
        self.assertTrue(self.page.bitnet_switch.isChecked())
        self.assertTrue(self.page.autostart_switches["hub"].isChecked())
        self.assertFalse(self.page.autostart_switches["app"].isChecked())
        self.assertEqual(self.calls, [])

    def test_unknown_values_are_disabled_and_off(self) -> None:
        self.page.sync("stopped", None, None)
        for switch in self.switches():
            self.assertFalse(switch.isChecked())
            self.assertFalse(switch.isEnabled())
        toggle(self.page.tracking_switch)
        self.assertEqual(self.calls, [])

    def test_unknown_autostart_entry_is_disabled_while_the_other_follows_its_value(self) -> None:
        self.page.sync("running", hub_settings(), {"hub": None, "app": True})
        self.assertFalse(self.page.autostart_switches["hub"].isEnabled())
        self.assertFalse(self.page.autostart_switches["hub"].isChecked())
        self.assertTrue(self.page.autostart_switches["app"].isEnabled())
        self.assertTrue(self.page.autostart_switches["app"].isChecked())

    def test_read_only_hub_shows_values_but_disables_its_switches(self) -> None:
        self.sync_running(activity_ui=True, bitnet=True, editable=False)
        for switch in (self.page.tracking_switch, self.page.bitnet_switch):
            self.assertTrue(switch.isChecked())
            self.assertFalse(switch.isEnabled())
        toggle(self.page.tracking_switch)
        self.assertEqual(self.calls, [])

    def test_switch_texts_follow_the_translator(self) -> None:
        for language in ("en", "vi"):
            translate = Translator(language)
            self.page.retranslate(translate)
            for switch in self.switches():
                self.assertEqual(switch.onText, translate("desktop.settings.on"))
                self.assertEqual(switch.offText, translate("desktop.settings.off"))
        self.page.retranslate(Translator("en"))
        self.assertEqual(self.page.tracking_switch.onText, "On")
        self.assertEqual(self.page.tracking_switch.offText, "Off")

    def test_switch_shows_the_translated_text_of_its_current_state(self) -> None:
        translate = Translator("vi")
        self.page.retranslate(translate)
        self.sync_running(activity_ui=True)
        self.assertEqual(self.page.tracking_switch.getText(), translate("desktop.settings.on"))


@unittest.skipIf(QT_MISSING, QT_MISSING or "")
class RemainingSegmentsTest(unittest.TestCase):
    def test_glass_style_segments_report_the_choice(self) -> None:
        calls: list[dict] = []
        page = settings.AppearancePage(on_change=lambda **change: calls.append(change), on_language=noop, on_reset=noop)
        page.sync(theme.Appearance(glass=True), "en", True)
        pick(page.style_segments, "clear")
        self.assertEqual(calls, [{"glass_style": "clear"}])
        self.assertEqual(page.style_segments.currentRouteKey(), "clear")

    def test_glass_style_segments_are_disabled_without_glass(self) -> None:
        page = settings.AppearancePage(on_change=noop, on_language=noop, on_reset=noop)
        page.sync(theme.Appearance(glass=False), "en", True)
        self.assertFalse(widgets.segment(page.style_segments, "clear").isEnabled())

    def test_mcp_rows_are_switches_and_report_the_choice(self) -> None:
        calls: list[tuple] = []
        page = hub_page.ChildrenPage(on_toggle=lambda server_id, value: calls.append((server_id, value)))
        servers = (ServerSwitch("mcp-a", True),)
        page.sync(HubSettings(activity_ui=False, servers=servers, editable=True), True)
        switch, _reason = page.switches["mcp-a"]
        self.assertIsInstance(switch, SwitchButton)
        self.assertTrue(switch.isChecked())
        toggle(switch)
        self.assertEqual(calls, [("mcp-a", False)])

    def test_mcp_row_is_disabled_but_keeps_its_value_when_not_editable(self) -> None:
        page = hub_page.ChildrenPage(on_toggle=lambda *_args: None)
        page.sync(HubSettings(activity_ui=False, servers=(ServerSwitch("mcp-a", True),), editable=False), True)
        switch, _reason = page.switches["mcp-a"]
        self.assertTrue(switch.isChecked())
        self.assertFalse(switch.isEnabled())


if __name__ == "__main__":
    unittest.main()
