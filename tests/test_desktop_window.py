"""Desktop status window tests on Qt's offscreen platform: no window opens, and they run on every OS."""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from mcp_hub.desktop import app, hub_page, mini, screenshot, settings, update_card, widgets
    QT_MISSING = None
except ImportError as exc:  # Qt's system libraries are missing (for example a bare Linux host)
    QT_MISSING = f"Qt is unavailable: {exc}"

from mcp_hub.desktop import i18n, prefs, status, theme, update
from mcp_hub.desktop.hub_api import HubSettings, ServerSwitch, UpdateStatus
from mcp_hub.desktop.status import ChildRow, HubSnapshot


def setUpModule() -> None:
    if QT_MISSING is None:
        app.application()


def running(*children: ChildRow, activity_ui: bool = False, clients: tuple[str, ...] = (),
            recent: tuple[str, ...] = ()) -> HubSnapshot:
    return HubSnapshot(hub="running", activity_ui=activity_ui, children=children, clients=clients, recent=recent,
                       checked_at=datetime(2026, 10, 30, 10, 42, 5))


class FakeStore:
    """HubClient stand-in: keeps the saved choices in memory."""

    def __init__(self) -> None:
        self.activity_ui, self.editable, self.fail = False, True, False
        self.bitnet = False
        self.reads = 0
        self.enabled = {"mcp-server-logs": True, "mcp-jira-sync": False}
        self.update = UpdateStatus("", "idle", (), ())
        self.retries: list[tuple[str, ...]] = []

    def check_updates(self):
        return self.update

    def read_update(self):
        return self.update

    def read_saved_update(self):
        return self.update

    def start_update(self):
        self.update = UpdateStatus("run-1", "complete", (), ())
        return self.update

    def retry_update(self, components):
        self.retries.append(components)
        self.update = UpdateStatus("run-1", "complete", (), ())
        return self.update

    def read_settings(self) -> HubSettings:
        self.reads += 1
        if self.fail:
            raise OSError("locked")
        servers = tuple(ServerSwitch(server_id, enabled, "credentials required" if server_id == "mcp-server-logs" else None)
                        for server_id, enabled in self.enabled.items())
        return HubSettings(self.activity_ui, servers, self.editable, bitnet=self.bitnet)

    def set_activity_ui(self, enabled: bool) -> None:
        self.activity_ui = enabled

    def set_bitnet(self, enabled: bool) -> None:
        self.bitnet = enabled

    installs = 0
    fail_install = False

    def install_bitnet(self) -> None:
        if self.fail_install:
            raise OSError("refused")
        self.installs += 1

    def set_server_enabled(self, server_id: str, enabled: bool) -> None:
        self.enabled[server_id] = enabled


def texts(rows) -> list[str]:
    """Visible text of each item in a rows layout: `glyph name detail` for a Row, else the label text."""
    found: list[str] = []
    for index in range(rows.count()):
        widget = rows.itemAt(index).widget()
        if isinstance(widget, widgets.UsageRow):
            widget = widget.row
        if isinstance(widget, widgets.Row):
            parts = (widget.glyph.text(), widget.text.text(), widget.detail.text())
            found.append(" ".join(part for part in parts if part))
        elif widget is not None:
            found.append(widget.text())
    return found


@unittest.skipIf(QT_MISSING, QT_MISSING or "")
class StatusWindowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.launched: list[list[str]] = []
        self.now = 1000.0
        self.fetched = threading.Event()
        self.next_snapshot = HubSnapshot(hub="stopped")
        self.styled: list[tuple[bool, int, bool]] = []
        self.clear_styles: list[bool] = []
        self.glass = False
        self.tmp = tempfile.TemporaryDirectory()
        self.prefs_path = Path(self.tmp.name) / "desktop.json"
        self.store = FakeStore()
        self.autostart = {"hub": True, "app": True}
        self.autostart_calls: list[tuple[str, bool]] = []
        self.utc = datetime(2026, 10, 31, 9, 30, tzinfo=timezone.utc)
        self.streams: list[tuple] = []
        self.stream_started = threading.Event()
        self.window = app.StatusWindow(
            i18n.Translator("en"), fetch=self.fake_fetch, open_dashboard=self.fake_open,
            launch=self.launched.append, clock=lambda: self.now, style=self.fake_style, prefs_path=self.prefs_path,
            hub_store=self.store, read_autostart=lambda: dict(self.autostart), write_autostart=self.fake_write_autostart,
            run_worker=lambda job: job(),
            stream=self.fake_stream, now=lambda: self.utc,
        )

    def fake_stream(self, put, stop: threading.Event) -> None:
        self.streams.append((put, stop))
        self.stream_started.set()

    def start_usage(self) -> object:
        """Show a running Hub with client tracking and return the activity stream's `put`."""
        self.window.apply(running(ChildRow("mcp-server-logs", "connected", "ok"),
                                  ChildRow("mcp-jira-sync", "connected", "ok"), activity_ui=True))
        self.assertTrue(self.stream_started.wait(timeout=5))
        return self.streams[-1][0]

    def usage_text(self, server_id: str) -> str:
        """The detail (tooltip) of a child's usage line, checking the visible line is the short `↳` form."""
        line = self.window.child_rows[server_id].usage
        if line.isHidden():
            return ""
        self.assertTrue(line.text().startswith(f"{theme.USAGE_GLYPH} "))
        return line.toolTip()

    def client_detail(self, name: str) -> tuple[str, str]:
        row = self.window.client_rows[name][0]
        return row.detail.toolTip() or row.detail.text(), row.detail.property("role")

    def fake_write_autostart(self, target: str, enabled: bool) -> dict[str, bool]:
        if target == "broken":
            raise OSError("hubctl failed")
        self.autostart_calls.append((target, enabled))
        self.autostart[target] = enabled
        return dict(self.autostart)

    def tearDown(self) -> None:
        self.window.poll_timer.stop()
        self.window.drain_timer.stop()
        self.window.close()
        self.window.deleteLater()
        self.tmp.cleanup()

    def fake_style(self, _hwnd: int, dark: bool, tint: int, glass: bool, clear: bool = False) -> bool:
        self.styled.append((dark, tint, glass))
        self.clear_styles.append(clear)
        return self.glass and glass

    def fake_fetch(self) -> HubSnapshot:
        self.fetched.set()
        return self.next_snapshot

    def fake_open(self) -> None:
        raise status.DesktopError("pairing failed")

    def test_initial_window_is_checking_with_no_enabled_action(self) -> None:
        self.assertEqual(self.window.windowTitle(), "mcp-hub")
        self.assertEqual(self.window.hub_row.text.text(), "Checking the Hub…")
        self.assertEqual(self.window.hub_row.glyph.property("tone"), "wait")
        self.assertFalse(self.window.open_button.isEnabled())
        self.assertFalse(self.window.start_button.isEnabled())
        self.assertFalse(self.window.glass)
        self.assertEqual(self.window.styleSheet(), theme.stylesheet(theme.Appearance(), glass_active=False))
        self.assertEqual(self.styled, [(False, theme.backdrop_tint(theme.Appearance()), True)])
        self.assertTrue(self.window.settings.isHidden())
        self.assertFalse(self.window.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint)

    def test_running_snapshot_shows_children_and_clients(self) -> None:
        self.window.apply(running(ChildRow("mcp-server-logs", "connected", "ok"), ChildRow("mcp-jira-sync", "reconnecting", "wait"),
                                  activity_ui=True, clients=("claude-code ×2",)))
        self.assertEqual(self.window.hub_row.text.text(), "Running")
        self.assertEqual(self.window.hub_row.detail.text(), "activity UI")
        self.assertEqual(self.window.hub_row.glyph.property("tone"), "ok")
        self.assertEqual(texts(self.window.children_list),
                         ["● mcp-server-logs", "◐ mcp-jira-sync Reconnecting"])
        self.assertEqual(self.window.children_count.text(), "1/2")
        self.assertEqual(texts(self.window.clients_list), ["● claude-code ×2"])
        self.assertEqual(self.window.clients_count.text(), "1")
        self.assertTrue(self.window.open_button.property("primary"))
        self.assertFalse(self.window.start_button.property("primary"))
        self.assertTrue(self.window.open_button.isEnabled())
        self.assertFalse(self.window.start_button.isEnabled())
        self.assertEqual(self.window.footer.text(), "Updated 10:42:05")
        self.assertEqual(self.window.footer.property("role"), "caption")

    def test_fast_mode_and_empty_lists_show_notes(self) -> None:
        self.window.apply(running())
        self.assertEqual(texts(self.window.children_list), ["No child MCPs detected."])
        self.assertEqual(self.window.hub_row.detail.text(), "fast mode")
        self.assertIn("Track AI clients", texts(self.window.clients_list)[0])
        self.window.apply(running(activity_ui=True))
        self.assertEqual(texts(self.window.clients_list), ["No AI clients connected yet"])
        self.window.apply(running(activity_ui=True, clients=("claude-code",), recent=("codex",)))
        self.assertEqual(texts(self.window.clients_list), ["● claude-code", "○ codex Recently active"])
        self.assertEqual(self.window.clients_count.text(), "1")

    def test_an_unchanged_poll_redraws_nothing(self) -> None:
        self.window.apply(running(ChildRow("mcp-server-logs", "connected", "ok")))
        later = replace(running(ChildRow("mcp-server-logs", "connected", "ok")), checked_at=datetime(2026, 10, 30, 10, 42, 10))
        with patch.object(self.window, "clear_rows") as clear_rows, patch.object(self.window, "setWindowIcon") as icon:
            self.window.apply(later)
        clear_rows.assert_not_called()
        icon.assert_not_called()
        self.assertEqual(self.window.footer.text(), "Updated 10:42:10")

    def test_stopped_hub_makes_start_the_primary_action(self) -> None:
        self.window.apply(HubSnapshot(hub="stopped"))
        self.assertEqual(self.window.hub_row.glyph.property("tone"), "error")
        self.assertTrue(self.window.start_button.property("primary"))
        self.assertTrue(self.window.start_button.isEnabled())
        self.assertEqual(texts(self.window.children_list), ["Shown when the Hub is running."])

    def test_start_hub_shows_starting_until_the_hub_answers(self) -> None:
        self.window.apply(HubSnapshot(hub="stopped"))
        self.window.start_hub()
        self.assertEqual(self.launched, [["start"]])
        self.assertEqual(self.window.snapshot.hub, "starting")
        self.window.apply(HubSnapshot(hub="stopped"))
        self.assertEqual(self.window.snapshot.hub, "starting")
        self.assertFalse(self.window.start_button.isEnabled())
        self.now += theme.START_GRACE_SECONDS + 1
        self.window.apply(HubSnapshot(hub="stopped"))
        self.assertEqual(self.window.snapshot.hub, "stopped")
        # A start that never came up says so, and keeps saying it while the Hub stays down.
        self.assertIn("The Hub did not start", self.window.footer.text())
        self.assertEqual(self.window.footer.property("role"), "error")
        self.window.apply(HubSnapshot(hub="stopped"))
        self.assertIn("The Hub did not start", self.window.footer.text())
        self.window.apply(running())
        self.assertNotIn("The Hub did not start", self.window.footer.text())

    def test_a_hub_that_comes_up_in_time_shows_no_start_error(self) -> None:
        self.window.apply(HubSnapshot(hub="stopped"))
        self.window.start_hub()
        self.window.apply(running())
        self.now += theme.START_GRACE_SECONDS + 1
        self.window.apply(HubSnapshot(hub="stopped"))  # stopped later, by something else
        self.assertNotIn("The Hub did not start", self.window.footer.text())

    def test_a_failed_start_drops_starting_and_says_so(self) -> None:
        def unavailable(_arguments: list[str]) -> None:
            raise OSError("no Hub task")

        self.window.apply(HubSnapshot(hub="stopped"))
        self.window.launch = unavailable
        self.window.refresh = MagicMock()
        self.window.start_hub()
        self.assertEqual(self.window.snapshot.hub, "starting")
        self.window.process_results()
        self.assertEqual(self.window.snapshot.hub, "stopped")
        self.assertIn("did not take that command", self.window.footer.text())
        self.assertEqual(self.window.footer.property("role"), "error")
        self.window.refresh.assert_called_once()
        self.window.apply(HubSnapshot(hub="stopped"))  # the next poll keeps the error while the Hub is down
        self.assertIn("did not take that command", self.window.footer.text())
        self.window.apply(running())
        self.assertNotIn("did not take that command", self.window.footer.text())

    def test_refresh_polls_on_a_worker_thread(self) -> None:
        self.next_snapshot = running(ChildRow("mcp-server-logs", "connected", "ok"))
        self.window.refresh()
        self.window.refresh()  # ignored while a poll is in flight
        self.assertTrue(self.fetched.wait(timeout=5))
        deadline = time.monotonic() + 5
        while self.window.snapshot is None and time.monotonic() < deadline:
            self.window.process_results()
            time.sleep(0.01)
        self.assertEqual(self.window.snapshot.hub, "running")
        self.assertFalse(self.window._polling)

    def test_failed_dashboard_open_is_reported_in_the_footer(self) -> None:
        self.window.open_dashboard_once()
        self.window.process_results()
        self.assertIn("Could not open the dashboard", self.window.footer.text())

    def test_buttons_call_their_actions(self) -> None:
        self.window.apply(HubSnapshot(hub="stopped"))
        self.window.start_button.click()
        self.assertEqual(self.launched, [["start"]])
        self.window.settings_button.click()
        self.assertFalse(self.window.settings.isHidden())
        self.assertTrue(self.window.settings_button.isChecked())
        self.window.appearance_page.language_buttons["vi"].click()
        self.assertEqual(self.window.t.language, "vi")
        self.window.appearance_page.theme_buttons["dark"].click()
        self.assertEqual(self.window.theme_name, "dark")
        self.window.settings_button.click()
        self.assertTrue(self.window.settings.isHidden())

    def test_language_choice_rerenders_and_is_remembered(self) -> None:
        self.window.apply(running())
        self.window.set_language("vi")
        vi = i18n.load_strings("vi")
        self.assertEqual(self.window.open_button.text(), vi["desktop.button.openDashboard"])
        self.assertEqual(self.window.appearance_page.reset_button.text(), vi["desktop.settings.reset"])
        self.assertTrue(self.window.appearance_page.language_buttons["vi"].isChecked())
        self.assertTrue(self.window.save_timer.isActive())
        self.window.flush_prefs()
        self.assertFalse(self.window.save_timer.isActive())
        self.assertEqual(prefs.load_prefs(self.prefs_path), {"theme": "light", "language": "vi", "glass": True, "glass_style": "frosted", "compact": False})
        self.window.set_language("en")
        self.assertTrue(self.window.appearance_page.language_buttons["en"].isChecked())

    def test_theme_switch_restyles_without_rebuilding_and_is_remembered(self) -> None:
        self.window.apply(running(ChildRow("mcp-server-logs", "connected", "ok")))
        cards = list(self.window.cards)
        self.window.set_theme("dark")
        dark = theme.Appearance(theme="dark")
        self.assertEqual(self.window.theme_name, "dark")
        self.assertEqual(self.window.styleSheet(), theme.stylesheet(dark, glass_active=False))
        self.assertEqual(self.styled[-1], (True, theme.backdrop_tint(dark), True))
        self.assertTrue(self.window.appearance_page.theme_buttons["dark"].isChecked())
        self.assertEqual(self.window.cards, cards)
        self.assertEqual(set(self.window._icons), {("light", "wait"), ("light", "ok"), ("dark", "ok")})
        self.assertEqual(texts(self.window.children_list), ["● mcp-server-logs"])
        self.window.flush_prefs()
        self.assertEqual(prefs.load_prefs(self.prefs_path), {"theme": "dark", "language": "en", "glass": True, "glass_style": "frosted", "compact": False})
        self.window.set_theme("neon")
        self.assertEqual(self.window.theme_name, "dark")

    def test_glass_makes_the_window_see_through_and_the_cards_frosted(self) -> None:
        self.glass = True
        self.window.apply_theme()
        self.assertTrue(self.window.glass)
        self.assertTrue(self.window.glass_available)
        self.assertEqual(self.window.styleSheet(), theme.stylesheet(theme.Appearance(), glass_active=True))
        self.assertTrue(self.window.appearance_page.opacity.isEnabled())
        self.assertTrue(self.window.appearance_page.unavailable.isHidden())

    def test_sliders_change_the_glass_live_and_are_saved_once(self) -> None:
        self.glass = True
        self.window.apply_theme()
        self.window.appearance_page.opacity.setValue(80)
        self.window.appearance_page.tint.setValue(10)
        look = theme.Appearance(card_opacity=80, tint=10)
        self.assertEqual(self.window.appearance, look)
        self.assertIn(theme.rgba(theme.LIGHT["surface"], 0.8), self.window.styleSheet())
        self.assertEqual(self.styled[-1], (False, theme.backdrop_tint(look), True))
        self.assertEqual(self.window.appearance_page.opacity_value.text(), "80%")
        self.assertTrue(self.window.save_timer.isActive())
        self.assertFalse(self.prefs_path.exists())
        self.window.close()  # a pending change is saved on close
        self.assertEqual(prefs.load_prefs(self.prefs_path),
                         {"theme": "light", "language": "en", "glass": True, "card_opacity": 80, "tint": 10, "glass_style": "frosted", "compact": False})

    def test_glass_switch_turns_the_blur_off_and_disables_the_sliders(self) -> None:
        self.glass = True
        self.window.apply_theme()
        self.window.appearance_page.glass_buttons[False].click()
        self.assertFalse(self.window.glass)
        self.assertEqual(self.styled[-1][2], False)
        self.assertEqual(self.window.styleSheet(), theme.stylesheet(theme.Appearance(glass=False), glass_active=False))
        self.assertFalse(self.window.appearance_page.opacity.isEnabled())
        self.assertFalse(self.window.appearance_page.tint.isEnabled())
        self.assertTrue(self.window.glass_available)
        self.window.appearance_page.glass_buttons[True].click()
        self.assertTrue(self.window.glass)

    def test_unavailable_glass_disables_its_controls(self) -> None:
        self.glass = False
        self.window.apply_theme()
        self.assertFalse(self.window.glass_available)
        self.assertFalse(self.window.appearance_page.unavailable.isHidden())
        self.assertFalse(self.window.appearance_page.glass_buttons[True].isEnabled())
        self.assertFalse(self.window.appearance_page.opacity.isEnabled())

    def test_reset_restores_the_theme_defaults_but_keeps_theme_and_language(self) -> None:
        self.window.set_theme("dark")
        self.window.set_language("vi")
        self.window.set_appearance(glass=False, card_opacity=30, tint=80)
        self.window.appearance_page.reset_button.click()
        self.assertEqual(self.window.appearance, theme.Appearance(theme="dark"))
        self.assertEqual(self.window.t.language, "vi")
        self.assertEqual(self.window.appearance_page.opacity.value(), theme.default_card_opacity("dark"))
        self.window.set_language("vi")  # unchanged language: nothing to do

    def test_hub_tab_shows_the_saved_settings_and_sign_in_tasks(self) -> None:
        self.window.apply(running())
        self.window.show_tab("hub")
        self.window.process_results()
        page = self.window.hub_page
        self.assertEqual(self.window.settings.tab, "hub")
        self.assertTrue(self.window.appearance_page.isHidden())
        self.assertFalse(page.isHidden())
        self.assertTrue(page.tracking_buttons[False].isChecked())
        self.assertTrue(page.bitnet_buttons[False].isChecked())
        self.assertTrue(page.autostart_buttons["app"][True].isChecked())
        self.assertTrue(page.loading.isHidden())
        self.assertFalse(page.start_button.isEnabled())
        self.assertTrue(page.stop_button.isEnabled())
        self.assertTrue(self.window.settings.pending.isHidden())

    def test_tracking_is_saved_and_a_running_hub_offers_a_confirmed_restart(self) -> None:
        self.window.apply(running())
        self.window.show_tab("hub")
        self.window.process_results()
        self.window.hub_page.tracking_buttons[True].click()
        self.window.process_results()
        self.assertTrue(self.store.activity_ui)
        self.assertTrue(self.window.hub_page.tracking_buttons[True].isChecked())
        self.assertFalse(self.window.settings.pending.isHidden())
        restart = self.window.settings.restart_button
        restart.click()
        self.assertEqual(self.launched, [])
        self.assertEqual(restart.text(), "Click again")
        restart.click()
        self.assertEqual(self.launched, [["restart"]])
        self.assertEqual(self.window.snapshot.hub, "starting")
        self.assertTrue(self.window.settings.pending.isHidden())
        self.window.set_tracking(True)  # unchanged: nothing written
        self.assertEqual(self.launched, [["restart"]])

    def test_bitnet_is_saved_and_status_caption_mapped(self) -> None:
        self.window.apply(running())
        self.window.show_tab("hub")
        self.window.process_results()
        page = self.window.hub_page
        self.assertTrue(page.bitnet_buttons[False].isChecked())
        page.bitnet_buttons[True].click()
        self.window.process_results()
        self.assertTrue(self.store.bitnet)
        self.assertTrue(page.bitnet_buttons[True].isChecked())
        self.assertTrue(self.window.restart_needed)

        # Status caption mapping
        self.window.apply(HubSnapshot(hub="running", bitnet_state="ready"))
        self.assertEqual(page.bitnet_status.text(), "Ready")
        self.window.apply(HubSnapshot(hub="running", bitnet_state="installing"))
        self.assertEqual(page.bitnet_status.text(), "Installing\u2026")
        self.window.apply(HubSnapshot(hub="running", bitnet_state="unavailable"))
        self.assertEqual(page.bitnet_status.text(), "Unavailable")

    def test_bitnet_installs_with_a_spinner_then_asks_for_a_restart(self) -> None:
        info, page = status.BitnetInfo, self.window.hub_page
        self.window.apply(replace(running(), bitnet=info()))
        self.window.show_tab("hub")
        self.window.process_results()
        self.assertEqual(page.bitnet_status.text(), "Not installed")
        self.assertEqual(page.bitnet_action.text(), "Install BitNet")
        self.assertTrue(page.bitnet_buttons[True].isHidden())  # On/Off only once installed
        page.bitnet_action.click()
        self.window.process_results()
        self.assertEqual(self.store.installs, 1)

        self.window.apply(replace(running(), bitnet=info(state="installing", installing=True, step="model", progress=42)))
        self.assertEqual(page.bitnet_status.text(), "Downloading the model… 42%")
        self.assertTrue(page.bitnet_spinner.running)
        self.assertTrue(page.bitnet_action.isHidden())
        self.assertEqual(self.window.poll_timer.interval(), theme.BUSY_POLL_INTERVAL_MS)

        self.store.bitnet = True  # the Hub switches BitNet on when the install finishes
        self.window.load_tab("hub")
        self.window.process_results()
        self.window.apply(replace(running(), bitnet=info(installed=True, state="off", restart_needed=True)))
        self.assertFalse(page.bitnet_spinner.running)
        self.assertEqual(page.bitnet_status.text(), "Installed. Restart the Hub to connect BitNet.")
        self.assertFalse(page.bitnet_buttons[True].isHidden())
        page.bitnet_action.click()
        self.assertEqual(self.launched[-1], ["restart"])

        self.window.apply(replace(running(), bitnet=info(installed=True, enabled=True, state="ready")))
        self.assertEqual(page.bitnet_status.text(), "Ready")
        self.assertTrue(page.bitnet_action.isHidden())
        self.assertEqual(self.window.poll_timer.interval(), theme.POLL_INTERVAL_MS)

    def test_a_failed_bitnet_install_offers_a_retry(self) -> None:
        page = self.window.hub_page
        self.window.apply(replace(running(), bitnet=status.BitnetInfo(error="network")))
        self.window.show_tab("hub")
        self.window.process_results()
        self.assertEqual(page.bitnet_status.text(), "The download failed. Check the network and try again.")
        self.assertEqual(page.bitnet_action.text(), "Try again")
        self.store.fail_install = True
        page.bitnet_action.click()
        self.window.process_results()
        self.assertEqual(self.window.footer.text(), "Could not start the install.")

    def test_bitnet_has_its_own_dot_under_the_children_and_is_not_counted(self) -> None:
        info, logs = status.BitnetInfo, ChildRow("mcp-server-logs", "connected", "ok")
        self.window.apply(replace(running(logs), bitnet=info(installed=True, enabled=True, state="ready")))
        self.assertEqual(texts(self.window.children_list), ["● mcp-server-logs", "● BitNet"])
        self.assertEqual(self.window.children_count.text(), "1/1")
        self.window.apply(replace(running(logs), bitnet=info(installed=True, enabled=True, state="unavailable")))
        self.assertEqual(texts(self.window.children_list)[-1], "● BitNet Unavailable")
        self.assertEqual(self.window.bitnet_row.glyph.property("tone"), "error")
        self.window.apply(replace(running(logs), bitnet=info(state="installing", installing=True, step="model", progress=7)))
        self.assertEqual(texts(self.window.children_list)[-1], "◐ BitNet 7%")
        self.window.apply(replace(running(logs), bitnet=info()))
        self.assertEqual(texts(self.window.children_list)[-1], "○ BitNet Not installed")
        self.window.apply(replace(running(logs), bitnet=info(installed=True, state="off")))
        self.assertEqual(texts(self.window.children_list)[-1], "○ BitNet Off")
        self.window.apply(running(logs))  # a Hub too old to report BitNet
        self.assertEqual(texts(self.window.children_list), ["● mcp-server-logs"])

    def test_the_spinner_turns_only_while_its_glyph_is_shown(self) -> None:
        glyph = widgets.label()
        spinner = widgets.Spinner(glyph)
        spinner.start()
        self.assertEqual(glyph.text(), theme.SPINNER_GLYPHS[0])
        spinner.tick()  # hidden: left alone
        self.assertEqual(glyph.text(), theme.SPINNER_GLYPHS[0])
        glyph.show()
        spinner.tick()
        self.assertEqual(glyph.text(), theme.SPINNER_GLYPHS[1])
        spinner.stop()
        self.assertFalse(spinner.running)
        glyph.close()

    def test_a_stopped_hub_shows_no_settings_until_it_runs(self) -> None:
        self.window.apply(HubSnapshot(hub="stopped"))
        self.window.show_tab("mcps")
        self.window.process_results()
        self.assertEqual(self.store.reads, 0)  # settings live in the Hub, so nothing is read
        self.assertEqual(self.window.mcps_page.note.text(), "Start the Hub to see and change its settings.")
        self.window.show_tab("hub")
        self.window.process_results()
        page = self.window.hub_page
        self.assertFalse(page.needs_hub.isHidden())
        self.assertTrue(page.loading.isHidden())
        self.assertFalse(page.tracking_buttons[True].isEnabled())
        self.assertTrue(page.start_button.isEnabled())
        page.start_button.click()
        self.assertEqual(self.launched, [["start"]])
        self.window.apply(running())  # the Hub answers: the open tab loads its settings
        self.window.process_results()
        self.assertEqual(self.store.reads, 1)
        self.assertTrue(page.needs_hub.isHidden())
        self.assertTrue(page.tracking_buttons[False].isEnabled())

    def test_running_from_source_has_no_app_sign_in_switch(self) -> None:
        self.autostart = {"hub": None, "app": None}
        self.window.show_tab("hub")
        self.window.process_results()
        page = self.window.hub_page
        self.assertFalse(page.app_hint.isHidden())
        self.assertFalse(page.autostart_buttons["app"][True].isEnabled())
        self.assertFalse(page.autostart_buttons["hub"][True].isEnabled())

    def test_stop_needs_a_second_click(self) -> None:
        self.window.apply(running())
        stop = self.window.hub_page.stop_button
        stop.click()
        self.assertTrue(stop.armed)
        self.assertTrue(stop.property("armed"))
        stop.disarm()  # the confirm window ran out
        self.assertEqual(stop.text(), "Stop")
        stop.click()
        stop.click()
        self.assertEqual(self.launched, [["stop"]])
        self.assertFalse(stop.armed)

    def test_child_switches_are_saved_and_show_why_a_child_cannot_run(self) -> None:
        self.window.apply(running())
        self.window.show_tab("mcps")
        self.window.process_results()
        page = self.window.mcps_page
        self.assertEqual(list(page.switches), ["mcp-server-logs", "mcp-jira-sync"])
        buttons, reason = page.switches["mcp-jira-sync"]
        self.assertTrue(buttons[False].isChecked())
        self.assertTrue(reason.isHidden())
        self.assertFalse(page.switches["mcp-server-logs"][1].isHidden())
        buttons[True].click()
        self.window.process_results()
        self.assertTrue(self.store.enabled["mcp-jira-sync"])
        self.assertTrue(page.switches["mcp-jira-sync"][0][True].isChecked())
        self.assertTrue(self.window.restart_needed)
        self.window.apply(HubSnapshot(hub="stopped"))
        self.assertFalse(self.window.restart_needed)

    def test_a_custom_hub_config_is_shown_read_only(self) -> None:
        self.store.editable = False
        self.window.apply(running())
        self.window.show_tab("mcps")
        self.window.process_results()
        self.assertFalse(self.window.mcps_page.switches["mcp-server-logs"][0][True].isEnabled())
        self.assertIn("MCP_HUB_CONFIG", self.window.mcps_page.note.text())
        self.assertFalse(self.window.hub_page.tracking_buttons[True].isEnabled())
        self.assertFalse(self.window.hub_page.bitnet_buttons[True].isEnabled())

    def test_settings_errors_are_reported_in_the_footer(self) -> None:
        self.store.fail = True
        self.window.apply(running())
        self.window.show_tab("mcps")
        self.window.process_results()
        self.assertIn("Hub settings", self.window.footer.text())
        self.assertEqual(self.window.mcps_page.note.text(), "Reading settings…")
        self.window.set_autostart("broken", False)
        self.window.process_results()
        self.assertIn("sign-in setting", self.window.footer.text())

    def test_autostart_switches_call_hubctl(self) -> None:
        self.window.show_tab("hub")
        self.window.process_results()
        self.window.hub_page.autostart_buttons["hub"][False].click()
        self.window.process_results()
        self.assertEqual(self.autostart_calls, [("hub", False)])
        self.assertTrue(self.window.hub_page.autostart_buttons["hub"][False].isChecked())
        self.window.set_autostart("hub", False)  # unchanged: hubctl is not run again
        self.assertEqual(self.autostart_calls, [("hub", False)])

    def test_closed_settings_read_nothing(self) -> None:
        self.window.load_tab("hub")
        self.assertTrue(self.window.results.empty())

    def test_usage_lines_show_who_uses_each_child_live(self) -> None:
        put = self.start_usage()
        put(("usage_snapshot", {"children": {"mcp-jira-sync": {
            "last_client": "Antigravity", "last_tool": "jira_read_ticket", "last_at": self.utc.isoformat()}}}))
        put(("usage_event", {"server_id": "mcp-server-logs", "call_id": 1, "state": "started",
                             "client": "Codex", "tool": "query_logs"}))
        with patch.object(self.window, "clear_rows") as clear_rows:
            self.window.process_results()
        clear_rows.assert_not_called()  # usage never rebuilds the rows
        self.assertEqual(self.usage_text("mcp-server-logs"), "Codex · query_logs · using now")
        self.assertEqual(self.window.child_rows["mcp-server-logs"].usage.property("role"), "live")
        self.assertEqual(self.usage_text("mcp-jira-sync"), "Antigravity · jira_read_ticket · just now")
        put(("usage_event", {"server_id": "mcp-server-logs", "call_id": 1, "state": "succeeded",
                             "client": "Codex", "tool": "query_logs", "at": self.utc.isoformat()}))
        self.window.process_results()
        self.assertEqual(self.usage_text("mcp-server-logs"), "Codex · query_logs · just now")
        self.assertEqual(self.window.child_rows["mcp-server-logs"].usage.property("role"), "caption")
        self.utc += timedelta(seconds=90)
        self.window.render()
        self.assertEqual(self.usage_text("mcp-server-logs"), "Codex · query_logs · 1 min ago")
        self.utc += timedelta(seconds=theme.RECENT_SECONDS)
        self.window.render()
        self.assertEqual(self.usage_text("mcp-server-logs"), "")
        # A hidden line gives its space back: idle rows sit evenly.
        self.assertFalse(self.window.child_rows["mcp-server-logs"].usage.sizePolicy().retainSizeWhenHidden())

    def test_usage_stream_follows_the_mode_and_drops_stale_messages(self) -> None:
        put = self.start_usage()
        stop = self.streams[-1][1]
        self.window.apply(running(ChildRow("mcp-server-logs", "connected", "ok"), activity_ui=True))
        self.assertEqual(len(self.streams), 1)  # still one stream
        self.window.apply(running(ChildRow("mcp-server-logs", "connected", "ok")))
        self.assertTrue(stop.is_set())
        self.assertFalse(self.window.child_rows["mcp-server-logs"].usage.sizePolicy().retainSizeWhenHidden())
        put(("usage_event", {"server_id": "mcp-server-logs", "call_id": 1, "state": "started", "client": "Codex"}))
        self.window.process_results()
        self.assertEqual(self.usage_text("mcp-server-logs"), "")
        self.assertIsNone(self.window.usage_board.usage("mcp-server-logs", self.utc))

    def test_client_rows_show_the_children_each_client_uses(self) -> None:
        self.window.apply(running(ChildRow("mcp-server-logs", "connected", "ok"), activity_ui=True,
                                  clients=("Codex ×2",), recent=("Antigravity",)))
        self.window.snapshot = replace(self.window.snapshot, client_names=("Codex",))
        self.window._keys.clear()
        self.window.render()
        self.assertTrue(self.stream_started.wait(timeout=5))
        put = self.streams[-1][0]
        self.assertEqual(self.client_detail("Antigravity"), ("Recently active", "caption"))
        put(("usage_event", {"server_id": "mcp-server-logs", "call_id": 1, "state": "started", "client": "Codex"}))
        put(("usage_event", {"server_id": "mcp-jira-sync", "call_id": 2, "state": "succeeded", "client": "Antigravity",
                             "at": self.utc.isoformat()}))
        with patch.object(self.window, "clear_rows") as clear_rows:
            self.window.process_results()
        clear_rows.assert_not_called()
        self.assertEqual(self.client_detail("Codex"), ("mcp-server-logs", "live"))
        self.assertEqual(self.client_detail("Antigravity"), ("mcp-jira-sync", "caption"))
        self.utc += timedelta(seconds=theme.RECENT_SECONDS)
        self.window.render()
        self.assertEqual(self.client_detail("Antigravity"), ("Recently active", "caption"))
        self.assertEqual(self.client_detail("Codex"), ("mcp-server-logs", "live"))  # still running

    def test_connected_children_need_no_word_but_other_states_keep_theirs(self) -> None:
        self.window.apply(running(ChildRow("mcp-server-logs", "connected", "ok"), ChildRow("mcp-jira-sync", "reconnecting", "wait"),
                                  ChildRow("mcp-read-source", "disabled", "off")))
        self.assertEqual(texts(self.window.children_list),
                         ["● mcp-server-logs", "◐ mcp-jira-sync Reconnecting", "○ mcp-read-source Disabled"])

    def test_clear_glass_style_is_applied_saved_and_reset(self) -> None:
        self.glass = True
        self.window.apply_theme()
        self.window.appearance_page.style_buttons["clear"].click()
        self.assertEqual(self.window.appearance.glass_style, "clear")
        self.assertTrue(self.clear_styles[-1])
        self.assertTrue(self.window.appearance_page.style_buttons["clear"].isChecked())
        self.window.flush_prefs()
        self.assertEqual(prefs.load_prefs(self.prefs_path)["glass_style"], "clear")
        self.window.appearance_page.opacity.setValue(0)
        self.assertIn(theme.rgba(theme.LIGHT["surface"], 0.0), self.window.styleSheet())
        self.window.reset_appearance()
        self.assertEqual(self.window.appearance.glass_style, "frosted")
        self.assertFalse(self.clear_styles[-1])
        self.window.set_appearance(glass=False)
        self.assertFalse(self.window.appearance_page.style_buttons["clear"].isEnabled())

    def test_compact_mode_pins_the_mini_bar_and_comes_back(self) -> None:
        self.start_usage()
        self.window.show()
        self.window.compact_button.click()
        flags = self.window.windowFlags()
        self.assertTrue(self.window.compact)
        self.assertTrue(flags & Qt.WindowType.FramelessWindowHint and flags & Qt.WindowType.WindowStaysOnTopHint)
        self.assertIsInstance(self.window.mini, mini.MiniBar)
        self.assertFalse(self.window.mini.isHidden())
        self.assertTrue(all(card.isHidden() for card in self.window.cards))
        bar = self.window.mini
        self.assertEqual(bar.count.text(), "2/2")
        self.assertEqual(bar.dots["mcp-jira-sync"].property("tone"), "ok")
        self.assertEqual(bar.dots["mcp-jira-sync"].toolTip(), "mcp-jira-sync · Connected")
        # While the Hub runs, the bar has exactly one dot per child, matching the count.
        self.assertTrue(bar.hub_dot.isHidden())
        self.assertEqual(len(bar.dots), 2)
        put = self.streams[-1][0]
        put(("usage_event", {"server_id": "mcp-jira-sync", "call_id": 1, "state": "started", "client": "Codex"}))
        self.window.process_results()
        self.assertEqual((bar.dots["mcp-jira-sync"].text(), bar.dots["mcp-jira-sync"].property("tone")),
                         (theme.LIVE_GLYPH, "live"))
        self.assertEqual(bar.dots["mcp-jira-sync"].toolTip(), "mcp-jira-sync · Codex · using now")
        self.assertEqual(bar.clients.text(), "0 AI")
        self.window.move(150, 160)
        self.window.flush_prefs()
        saved = prefs.load_prefs(self.prefs_path)
        self.assertEqual((saved["compact"], saved["compact_pos"]), (True, [150, 160]))
        self.window.set_compact(True)  # already compact: nothing changes
        bar.expand_button.click()
        self.assertFalse(self.window.compact)
        self.assertFalse(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertFalse(self.window.cards[0].isHidden())
        self.assertTrue(self.window.settings.isHidden())

    def test_compact_mode_is_restored_on_a_visible_screen_only(self) -> None:
        self.window.restore_compact({"compact": False, "compact_pos": [9000, 9000]}, lambda _x, _y: False)
        self.assertFalse(self.window.compact)
        self.assertIsNone(self.window._compact_pos)
        self.window.show()
        self.window.restore_compact({"compact": True, "compact_pos": [40, 50]}, lambda _x, _y: True)
        self.assertTrue(self.window.compact)
        self.assertEqual((self.window.x(), self.window.y()), (40, 50))

    def test_mini_bar_before_the_hub_answers_and_dragging(self) -> None:
        self.window.show()
        self.window.set_compact(True)
        self.assertEqual(self.window.mini.hub_dot.toolTip(), "Checking the Hub…")
        self.assertEqual(self.window.mini.hub_word.text(), "Checking the Hub…")
        self.assertFalse(self.window.mini.hub_dot.isHidden())
        self.assertEqual(self.window.mini.dots, {})
        self.assertTrue(self.window.mini.clients.isHidden())
        handle = MagicMock()
        event = MagicMock()
        event.button.return_value = Qt.MouseButton.LeftButton
        with patch.object(widgets.QWidget, "windowHandle", return_value=handle):
            self.window.mini.mousePressEvent(event)
        handle.startSystemMove.assert_called_once()
        event.accept.assert_called_once()

    def test_closing_the_window_stops_the_stream(self) -> None:
        self.start_usage()
        self.window.close()
        self.assertTrue(self.streams[-1][1].is_set())

    def test_preview_usage_and_elided_long_lines(self) -> None:
        self.start_usage()
        board = screenshot.sample_usage(self.utc)
        self.window.preview_usage(board)
        self.assertEqual(self.usage_text("mcp-server-logs"), "Codex, Antigravity · query_logs · using now")
        self.assertEqual(self.usage_text("mcp-jira-sync"), "claude-code · jira_read_ticket · just now")
        item = self.window.child_rows["mcp-server-logs"]
        item.show_usage("↳ " + ", ".join(f"client-{index}" for index in range(30)), "", "live")
        self.assertTrue(item.usage.text().endswith("…"))
        self.assertNotEqual(item.usage.text(), item.usage.toolTip())

    def test_window_icon_is_a_dot_in_the_hub_tone(self) -> None:
        self.window.apply(running())
        self.window.apply(HubSnapshot(hub="stopped"))
        self.assertEqual(set(self.window._icons), {("light", "wait"), ("light", "ok"), ("light", "error")})
        image = widgets.dot_icon(theme.LIGHT["red"], 16).pixmap(16, 16).toImage()
        self.assertEqual(image.pixelColor(8, 8).name(), theme.LIGHT["red"])
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)

    def test_start_runs_the_poll_and_drain_timers(self) -> None:
        with patch.object(self.window, "refresh") as refresh:
            self.window.start()
        refresh.assert_called_once()
        self.assertTrue(self.window.poll_timer.isActive())
        self.assertEqual(self.window.poll_timer.interval(), theme.POLL_INTERVAL_MS)
        self.assertEqual(self.window.drain_timer.interval(), theme.QUEUE_CHECK_MS)

    def test_show_for_capture_keeps_the_window_on_top_and_restyles_it(self) -> None:
        self.window.show_for_capture()
        self.assertTrue(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertEqual((self.window.x(), self.window.y()), app.CAPTURE_POSITION)
        self.assertEqual(len(self.styled), 2)
        self.window.show_settings(True)
        self.assertFalse(self.window.settings.isHidden())


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
        self.panel.theme_buttons["dark"].click()
        self.panel.language_buttons["vi"].click()
        self.panel.glass_buttons[False].click()
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
        self.assertTrue(self.panel.theme_buttons["dark"].isChecked())
        self.assertTrue(self.panel.language_buttons["vi"].isChecked())
        self.assertEqual((self.panel.opacity.value(), self.panel.opacity_value.text()), (60, "60%"))
        self.assertEqual(self.panel.tint.value(), 30)
        self.assertEqual(self.panel.language_buttons["en"].text(), "EN")
        self.assertEqual(self.panel.theme_buttons["dark"].text(), i18n.load_strings("vi")["desktop.settings.themeDark"])

    def test_tabs_show_one_page_and_report_the_open_tab(self) -> None:
        opened: list[str] = []
        restarts: list[bool] = []
        pages = {name: widgets.Page() for name in settings.TABS}
        panel = settings.SettingsPanel(theme.WINDOW_WIDTH, pages, on_tab=opened.append, on_restart=lambda: restarts.append(True))
        try:
            panel.retranslate(i18n.Translator("vi"))
            self.assertEqual(panel.tab_buttons["mcps"].text(), "MCP con")
            panel.tab_buttons["mcps"].click()
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


@unittest.skipIf(QT_MISSING, QT_MISSING or "")
class UpdateWindowTest(unittest.TestCase):
    """The update card discovers system updates and installs the app after Hub work."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.release = update.Release((0, 2, 0), "https://example.com/s.exe", "https://example.com/s.exe.sha256",
                                      "https://example.com/releases/tag/v0.2.0")
        self.found: update.Release | None = self.release
        self.checks, self.installed, self.opened = 0, [], []
        self.install_error: Exception | None = None
        self.window = self.make_window(update.Updater("0.1.1", self.check, self.install, self.opened.append))

    def make_window(self, updater: update.Updater | None):
        self.store = FakeStore()
        return app.StatusWindow(
            i18n.Translator("en"), fetch=lambda: HubSnapshot(hub="stopped"), open_dashboard=lambda: None,
            launch=lambda _arguments: None, style=lambda *_a, **_k: False, prefs_path=Path(self.tmp.name) / "desktop.json",
            hub_store=self.store, read_autostart=lambda: {"hub": True, "app": True},
            write_autostart=lambda _target, _enabled: {"hub": True, "app": True}, run_worker=lambda job: job(),
            stream=lambda _put, _stop: None, updater=updater,
        )

    def check(self) -> update.Release | None:
        self.checks += 1
        return self.found

    def install(self, release: update.Release) -> None:
        self.installed.append(release)
        if self.install_error is not None:
            raise self.install_error

    def tearDown(self) -> None:
        self.window.poll_timer.stop()
        self.window.update_timer.stop()
        self.window.update_check_timer.stop()
        self.window.drain_timer.stop()
        self.window.close()
        self.window.deleteLater()
        self.tmp.cleanup()

    def offer(self, snapshot: HubSnapshot | None = None) -> None:
        if snapshot is not None:
            self.window.apply(snapshot)
        self.window.check_updates()
        self.window.process_results()

    def card(self):
        return self.window.update_card

    def test_without_an_app_updater_the_card_still_checks_hub_updates(self) -> None:
        window = self.make_window(None)
        self.addCleanup(window.close)
        window.check_updates()
        window.process_results()
        self.assertFalse(window.update_card.isHidden())
        self.assertEqual(self.checks, 0)

    def test_the_check_runs_once_when_the_window_starts_and_not_at_every_poll(self) -> None:
        with patch.object(self.window, "refresh"):
            self.window.start()
        self.assertEqual(self.checks, 1)
        self.window.apply(running(ChildRow("a", "connected", "ok")))
        self.window.refresh()
        self.assertEqual(self.checks, 1)

    def test_a_newer_release_shows_the_card_with_what_the_user_has(self) -> None:
        self.assertFalse(self.card().isHidden())
        self.offer(running(ChildRow("mcp-jira-sync", "connected", "ok")))
        self.assertFalse(self.card().isHidden())
        self.assertEqual(self.card().title.text(), "Version 0.2.0 is available")
        self.assertEqual(self.card().note.text(), "You have 0.1.1.")
        self.assertEqual((self.card().update_button.text(), self.card().notes_button.text()), ("Update", "What's new"))
        self.assertTrue(self.card().update_button.isEnabled())
        self.assertIsInstance(self.card(), update_card.UpdateCard)
        self.assertNotIn(self.card(), self.window.cards)  # the full window shows it; the mini bar never does

    def test_no_newer_release_still_shows_manual_check(self) -> None:
        self.found = None
        self.offer(running(ChildRow("a", "connected", "ok")))
        self.assertFalse(self.card().isHidden())
        self.assertEqual(self.window.footer.text(), "Updated 10:42:05")  # no error either

    def test_the_card_is_in_the_chosen_language(self) -> None:
        self.offer(running(ChildRow("a", "connected", "ok")))
        vi = i18n.load_strings("vi")
        self.window.set_language("vi")
        self.assertEqual(self.card().title.text(), vi["desktop.update.available"].replace("{version}", "0.2.0"))
        self.assertEqual(self.card().update_button.text(), vi["desktop.update.button"])
        self.assertNotEqual(self.card().update_button.text(), "Update")
        self.window.set_language("en")
        self.assertEqual(self.card().update_button.text(), "Update")

    def test_a_stopped_hub_can_be_started_for_the_update(self) -> None:
        self.offer(HubSnapshot(hub="stopped"))
        self.assertTrue(self.card().update_button.isEnabled())
        self.assertEqual(self.card().note.text(), "You have 0.1.1. The Hub is not running; Update will start it.")

    def test_a_failing_child_does_not_block_the_system_update(self) -> None:
        self.offer(running(ChildRow("mcp-jira-sync", "connected", "ok"), ChildRow("mcp-read-source", "unavailable", "error"),
                           ChildRow("mcp-pr-companion", "reconnecting", "wait")))
        self.assertTrue(self.card().update_button.isEnabled())
        self.window.install_update()
        self.window.process_results()
        self.assertEqual(self.installed, [self.release])

    def test_partial_system_update_still_installs_app_and_keeps_failed_retry_state(self) -> None:
        self.offer(running(ChildRow("mcp-jira-sync", "connected", "ok")))
        failed = UpdateStatus("run-1", "partial", (
            {"id": "mcp-jira-sync", "kind": "child", "state": "failed", "phase": "pull",
             "error_code": "worktree_dirty"},
        ), ({"component": "mcp-jira-sync", "phase": "pull"},))
        self.window.update_status = failed
        self.window._update_changed("update_status", failed)
        self.assertEqual(self.installed, [self.release])
        self.assertEqual(self.window.update_status, failed)
        self.assertIn("mcp-jira-sync", self.card().components.text())
        self.assertIn("Local changes need attention", self.card().components.text())
        self.assertIn("Downloading code", self.card().events.text())
        self.assertFalse(self.card().retry_button.isHidden())

    def test_accounts_still_to_fill_in_and_disabled_children_do_not_block(self) -> None:
        self.offer(running(ChildRow("a", "credentials required", "error"), ChildRow("b", "disabled", "off"),
                           ChildRow("c", "connecting", "wait")))
        self.assertTrue(self.card().update_button.isEnabled())

    def test_update_downloads_then_installs_and_closes_the_window(self) -> None:
        self.offer(running(ChildRow("a", "connected", "ok")))
        self.card().update_button.click()
        self.assertEqual(self.card().note.text(), "Updating mcp-hub and its enabled MCPs…")
        self.assertFalse(self.card().update_button.isEnabled())
        with patch.object(self.window, "close") as close:
            self.window.process_results()
        self.assertEqual(self.installed, [self.release])
        self.assertEqual(self.card().note.text(), "Installing. The app closes and opens again by itself.")
        self.assertFalse(self.card().update_button.isEnabled())
        close.assert_called_once_with()

    def test_a_second_click_while_it_works_does_not_start_another_download(self) -> None:
        self.offer(running(ChildRow("a", "connected", "ok")))
        self.window.install_update()
        self.window.install_update()
        self.window.process_results()
        self.assertEqual(len(self.installed), 1)

    def test_a_failed_update_shows_one_fixed_sentence_and_can_be_tried_again(self) -> None:
        self.offer(running(ChildRow("a", "connected", "ok")))
        for error in (update.DesktopError("secret local-path detail"), OSError("denied")):
            with self.subTest(error=type(error).__name__):
                self.install_error = error
                self.card().update_button.click()
                self.window.process_results()
                self.assertEqual(self.card().note.text(),
                                 "The update did not install. Try again, or download it from the Releases page.")
                self.assertEqual(self.card().note.property("role"), "error")
                self.assertTrue(self.card().update_button.isEnabled())
                self.assertNotIn("secret", self.card().note.text() + self.window.footer.text())
        self.install_error = None
        with patch.object(self.window, "close") as close:
            self.card().update_button.click()
            self.window.process_results()
        close.assert_called_once_with()
        self.assertEqual(len(self.installed), 3)

    def test_whats_new_opens_the_release_page(self) -> None:
        self.window.show_update_notes()  # nothing offered yet
        self.assertEqual(self.opened, [])
        self.offer(running(ChildRow("a", "connected", "ok")))
        self.card().notes_button.click()
        self.assertEqual(self.opened, [self.release])

    def test_the_mini_bar_hides_the_card_and_the_full_window_brings_it_back(self) -> None:
        self.offer(running(ChildRow("a", "connected", "ok")))
        self.window.show()
        self.window.set_compact(True)
        self.assertTrue(self.card().isHidden())
        self.window.render()
        self.assertTrue(self.card().isHidden())
        self.window.set_compact(False)
        self.assertFalse(self.card().isHidden())

    def test_a_poll_that_changes_nothing_redraws_nothing(self) -> None:
        snapshot = running(ChildRow("a", "connected", "ok"))
        self.offer(snapshot)
        with patch.object(self.window, "_render_update") as draw:
            self.window.apply(snapshot)
            self.window.render()
        draw.assert_not_called()
        with patch.object(self.window, "_render_update") as draw:
            self.window.apply(running(ChildRow("a", "unavailable", "error")))
        draw.assert_not_called()

    def test_the_preview_shows_and_hides_the_card_for_screenshots(self) -> None:
        self.window.preview_update(self.release)
        self.assertFalse(self.card().isHidden())
        self.window.preview_update(None)
        self.assertFalse(self.card().isHidden())
        self.assertEqual(self.card().title.text(), "Updates")


@unittest.skipIf(QT_MISSING, QT_MISSING or "")
class ScreenshotTest(unittest.TestCase):
    def test_capture_all_saves_every_combination(self) -> None:
        class FakeWindow:
            def __init__(self) -> None:
                self.t, self.shown, self.captured, self.previewed = i18n.Translator("en"), [], False, []
                self.releases: list = []

            def preview_update(self, release) -> None:
                self.releases.append(release)

            def show_for_capture(self) -> None:
                self.captured = True

            def set_theme(self, name: str) -> None:
                self.theme_name = name

            def set_language(self, language: str) -> None:
                self.t.set_language(language)

            def show_settings(self, visible: bool) -> None:
                self.settings_open = visible

            def set_compact(self, on: bool) -> None:
                self.compact = on

            def show_tab(self, name: str) -> None:
                self.settings_open, self.tab = True, name

            def process_results(self) -> None:
                self.drained = True

            def now(self) -> datetime:
                return datetime(2026, 10, 31, tzinfo=timezone.utc)

            def preview_usage(self, board) -> None:
                self.previewed.append(board.usage("mcp-server-logs", self.now()))

            def apply(self, snapshot: HubSnapshot) -> None:
                self.shown.append((self.theme_name, self.t.language, snapshot.hub))

        window = FakeWindow()
        with tempfile.TemporaryDirectory() as tmp:
            saved = screenshot.capture_all(window, Path(tmp) / "shots", ["running", "settings-hub"], ["en", "vi"], ["dark"],
                                           capture=lambda _window, path: path.write_bytes(b"png"), settle=lambda _window: None)
            self.assertEqual([path.name for path in saved], [
                "running-en-dark.png", "settings-hub-en-dark.png", "running-vi-dark.png", "settings-hub-vi-dark.png",
            ])
            self.assertEqual(saved[0].read_bytes(), b"png")
        self.assertTrue(window.captured)
        self.assertEqual(window.shown[-1], ("dark", "vi", "running"))
        self.assertTrue(window.settings_open)
        self.assertEqual(window.tab, "hub")
        self.assertTrue(window.drained)
        self.assertEqual(window.previewed, [None] * 4)  # only the `ui` and `compact` samples show usage
        self.assertEqual(window.releases, [None] * 4)  # and only the update samples show the update card
        self.assertFalse(window.compact)

    def test_the_update_sample_shows_the_card(self) -> None:
        window = type("W", (), {})()
        window.t, window.shown, window.releases = i18n.Translator("en"), [], []
        for name, value in (("show_for_capture", None), ("set_theme", None), ("set_language", None), ("show_settings", None),
                            ("set_compact", None), ("preview_usage", None), ("process_results", None)):
            setattr(window, name, lambda *_a, **_k: None)
        window.now = lambda: datetime(2026, 10, 31, tzinfo=timezone.utc)
        window.apply = lambda snapshot: window.shown.append(snapshot)
        window.preview_update = window.releases.append
        with tempfile.TemporaryDirectory() as tmp:
            screenshot.capture_all(window, Path(tmp), ["update"], ["en"], ["light"],
                                   capture=lambda _w, path: path.write_bytes(b"png"), settle=lambda _w: None)
        self.assertEqual(window.releases, [screenshot.SAMPLE_RELEASE])

    def test_samples_cover_every_hub_state(self) -> None:
        self.assertEqual({sample.hub for sample in screenshot.SAMPLES.values()}, set(status.HUB_TONES))
        self.assertEqual(screenshot.STATES[-4:], ("settings", "settings-hub", "settings-mcps", "compact"))
        sample = screenshot.SampleSettings()
        self.assertIsNone(sample.set_activity_ui(True))
        self.assertIsNone(sample.set_server_enabled("mcp-server-logs", False))
        self.assertIs(sample.read_settings(), screenshot.SAMPLE_HUB_SETTINGS)

    def test_window_image_renders_or_reads_the_screen(self) -> None:
        window = app.StatusWindow(i18n.Translator("en"), style=lambda *_args, **_kwargs: False)
        try:
            self.assertFalse(screenshot.window_image(window, screen_capture=False).isNull())
            with patch.object(screenshot.win32, "window_rect", return_value=(0, 0, 2, 1)), \
                    patch.object(screenshot.win32, "capture_screen", return_value=(2, 1, b"\x10\x20\x30\x00" * 2)):
                image = screenshot.window_image(window, screen_capture=True)
            self.assertEqual((image.width(), image.height()), (2, 1))
            self.assertEqual(image.pixelColor(0, 0).name(), "#302010")
            with tempfile.TemporaryDirectory() as tmp:
                with patch.object(screenshot, "window_image", return_value=window.grab().toImage()):
                    screenshot.save_window_png(window, Path(tmp) / "shot.png")
                self.assertTrue((Path(tmp) / "shot.png").read_bytes().startswith(b"\x89PNG"))
                with patch.object(screenshot, "window_image", return_value=MagicMock(save=lambda *_args: False)), \
                        self.assertRaises(OSError):
                    screenshot.save_window_png(window, Path(tmp) / "nope.png")
        finally:
            window.close()


@unittest.skipIf(QT_MISSING, QT_MISSING or "")
class MainTest(unittest.TestCase):
    def test_parse_args_validates_lists(self) -> None:
        args = app.parse_args(["--screenshot", "out", "--states", "running, ui", "--theme", "dark"])
        self.assertEqual((args.screenshot, args.states, args.lang, args.theme), ("out", ["running", "ui"], ["en", "vi"], ["dark"]))
        self.assertIsNone(app.parse_args([]).screenshot)
        for bad in (["--states", "nope"], ["--lang", ""], ["--theme", "light,neon"]):
            with self.subTest(bad=bad), patch("sys.stderr"), self.assertRaises(SystemExit):
                app.parse_args(bad)

    def test_version_prints_the_app_version_and_starts_nothing(self) -> None:
        with patch.object(app.win32, "acquire_single_instance") as guard, patch.object(app, "application") as application, \
                patch.object(app.update, "app_version", return_value="0.1.1"), patch("builtins.print") as printed:
            self.assertEqual(app.main(["--version"]), 0)
        printed.assert_called_once_with("0.1.1")
        guard.assert_not_called()
        application.assert_not_called()
        with patch.object(app.update, "app_version", return_value=None), patch("builtins.print") as printed:
            app.main(["--version"])
        printed.assert_called_once_with("unknown")

    def test_main_exits_quietly_when_another_copy_runs(self) -> None:
        with patch.object(app.win32, "acquire_single_instance", return_value=None), \
                patch.object(app, "application") as application:
            self.assertEqual(app.main([]), 0)
        application.assert_not_called()

    def test_main_opens_the_window_with_saved_choices(self) -> None:
        fake_app = SimpleNamespace(exec=lambda: 0)
        with patch.object(app.win32, "acquire_single_instance", return_value=7), \
                patch.object(app, "application", return_value=fake_app), \
                patch.object(app.prefs, "load_prefs", return_value={"theme": "dark", "language": "vi", "tint": 20}), \
                patch.object(app, "StatusWindow") as window_class:
            self.assertEqual(app.main([]), 0)
        self.assertEqual(window_class.call_args.kwargs["appearance"], theme.Appearance(theme="dark", tint=20))
        self.assertEqual(window_class.call_args.args[0].language, "vi")
        window_class.return_value.show.assert_called_once()
        window_class.return_value.start.assert_called_once()

    def test_main_gives_the_window_the_default_updater(self) -> None:
        sentinel = object()
        with patch.object(app.win32, "acquire_single_instance", return_value=7), \
                patch.object(app, "application", return_value=SimpleNamespace(exec=lambda: 0)), \
                patch.object(app.prefs, "load_prefs", return_value={}), \
                patch.object(app.update, "default_updater", return_value=sentinel), patch.object(app, "StatusWindow") as window_class:
            app.main([])
        self.assertIs(window_class.call_args.kwargs["updater"], sentinel)

    def test_screenshot_mode_reports_success_and_failure(self) -> None:
        args = app.parse_args(["--screenshot", "out"])
        with patch.object(app, "StatusWindow") as window_class, \
                patch.object(app.screenshot, "capture_all", return_value=[Path("a.png")]), patch("builtins.print") as printed:
            self.assertEqual(app.run_screenshots(args), 0)
        self.assertEqual(printed.call_args.args[0], "a.png")
        window_class.return_value.close.assert_called_once()
        with patch.object(app, "StatusWindow"), patch.object(app.screenshot, "capture_all", side_effect=OSError("disk")), \
                patch("builtins.print") as printed:
            self.assertEqual(app.run_screenshots(args), 1)
        self.assertIn("could not be saved", printed.call_args.args[0])
        with patch.object(app, "application"), patch.object(app, "run_screenshots", return_value=0) as run:
            self.assertEqual(app.main(["--screenshot", "out"]), 0)
        run.assert_called_once()

    def test_application_sets_the_app_font(self) -> None:
        self.assertEqual(app.application().font().pointSize(), theme.FONT_POINTS["body"])
        self.assertIs(app.application(), QApplication.instance())


@unittest.skipIf(QT_MISSING, QT_MISSING or "")
class DefaultActionsTest(unittest.TestCase):
    """The window's default actions: the Hub's API for settings and control, the system for the rest."""

    def test_start_runs_the_task_and_other_commands_go_to_the_api(self) -> None:
        with patch.object(app, "HUB") as hub, patch.object(app.system, "start_hub") as start:
            app.control_hub(["start"])
            app.control_hub(["stop"])
        start.assert_called_once_with()
        hub.control.assert_called_once_with("stop")

    def test_sign_in_state_combines_the_hub_and_the_app(self) -> None:
        with patch.object(app, "HUB") as hub, patch.object(app.system, "app_autostart", return_value=None), \
                patch.object(app.system, "set_app_autostart") as set_app:
            hub.read_hub_autostart.return_value = True
            self.assertEqual(app.autostart_state(), {"hub": True, "app": None})
            hub.read_hub_autostart.side_effect = OSError("Hub stopped")
            self.assertEqual(app.autostart_state(), {"hub": None, "app": None})
            app.set_autostart("hub", False)
            hub.set_hub_autostart.assert_called_once_with(False)
            app.set_autostart("app", True)
            set_app.assert_called_once_with(True)
            with self.assertRaises(ValueError):
                app.set_autostart("everything", True)


if __name__ == "__main__":
    unittest.main()
