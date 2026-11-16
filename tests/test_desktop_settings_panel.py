"""Desktop window tests: Settings panel (SettingsPanel and its pages)."""
from __future__ import annotations

import unittest



from mcp_hub.desktop import i18n, theme
from mcp_hub.desktop.hub_api import HubSettings
from tests.helpers.window import hub_page, settings, widgets, QT_MISSING, qt_app, checked, pick


def setUpModule() -> None:
    qt_app()


@unittest.skipIf(QT_MISSING, QT_MISSING or "")
class SettingsPanelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.changes: list[dict[str, object]] = []
        self.languages: list[str] = []
        self.resets: list[bool] = []
        self.panel = settings.AppearancePage(
            on_change=lambda **change: self.changes.append(change),
            on_language=self.languages.append, on_reset=lambda: self.resets.append(True),
        )

    def tearDown(self) -> None:
        self.panel.deleteLater()

    def test_controls_report_choices_through_the_callbacks(self) -> None:
        pick(self.panel.theme_segments, "dark")
        pick(self.panel.language_segments, "vi")
        pick(self.panel.glass_segments, False)
        self.panel.opacity.setValue(70)
        self.panel.tint.setValue(5)
        self.panel.reset_button.click()
        self.assertEqual(self.changes, [{"theme": "dark"}, {"glass": False}, {"card_opacity": 70}, {"tint": 5}])
        self.assertEqual(self.languages, ["vi"])
        self.assertEqual(self.resets, [True])

    def test_sync_shows_the_appearance_without_reporting_it(self) -> None:
        self.panel.retranslate(i18n.Translator("vi"))
        self.panel.sync(theme.Appearance(theme="dark", card_opacity=60, tint=30), "vi", glass_available=True)
        self.assertEqual(self.changes, [])
        self.assertTrue(checked(self.panel.theme_segments, "dark"))
        self.assertTrue(checked(self.panel.language_segments, "vi"))
        self.assertEqual((self.panel.opacity.value(), self.panel.opacity_value.text()), (60, "60%"))
        self.assertEqual(self.panel.tint.value(), 30)
        self.assertEqual(widgets.segment(self.panel.language_segments, "en").text(), "EN")
        self.assertEqual(widgets.segment(self.panel.theme_segments, "dark").text(), i18n.load_strings("vi")["desktop.settings.themeDark"])

    def test_tabs_show_one_page_and_report_the_open_tab(self) -> None:
        opened: list[str] = []
        restarts: list[bool] = []
        pages = {name: widgets.Page() for name in settings.TABS}
        panel = settings.SettingsPanel(theme.WINDOW_WIDTH, pages, on_tab=opened.append, on_restart=lambda: restarts.append(True))
        try:
            panel.retranslate(i18n.Translator("vi"))
            self.assertEqual(widgets.segment(panel.tab_segments, "mcps").text(), "MCP con")
            pick(panel.tab_segments, "mcps")
            self.assertEqual(opened, ["appearance", "mcps"])
            self.assertTrue(pages["appearance"].isHidden())
            self.assertFalse(pages["mcps"].isHidden())
            self.assertTrue(panel.restart_button.isHidden())
            panel.show_pending(True)
            self.assertFalse(panel.restart_button.isHidden())
        finally:
            panel.deleteLater()

    def test_children_page_without_children_says_so(self) -> None:
        page = hub_page.ChildrenPage(on_toggle=lambda *_args: None)
        try:
            page.sync(HubSettings(activity_ui=False, servers=(), editable=True))
            page.retranslate(i18n.Translator("en"))
            self.assertEqual(page.note.text(), "No child MCPs found next to the Hub.")
        finally:
            page.deleteLater()
