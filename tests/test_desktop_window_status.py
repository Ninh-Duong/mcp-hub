"""Desktop window tests: Status window (StatusWindow, its default actions, and the Fluent widgets)."""
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, UTC
from pathlib import Path
from unittest.mock import MagicMock, patch



from mcp_hub.desktop import i18n, prefs, status, theme
from mcp_hub.desktop.status import ChildRow, HubSnapshot
from tests.helpers.window import Qt, QTest, app, mini, screenshot, system, widgets, HyperlinkButton, PrimaryPushButton, TransparentToolButton, QT_MISSING, qt_app, running, assert_rows_fit, texts, checked, pick, enabled, FakeStore


def setUpModule() -> None:
    qt_app()


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
        self.utc = datetime(2026, 10, 31, 9, 30, tzinfo=UTC)
        self.streams: list[tuple] = []
        self.stream_started = threading.Event()
        self.processing_streams = []
        self.processing_started = threading.Event()
        self.window = app.StatusWindow(
            i18n.Translator("en"), fetch=self.fake_fetch,
            launch=self.launched.append, clock=lambda: self.now, style=self.fake_style, prefs_path=self.prefs_path,
            hub_store=self.store, read_autostart=lambda: dict(self.autostart), write_autostart=self.fake_write_autostart,
            run_worker=lambda job: job(),
            stream=self.fake_stream, processing_stream=self.fake_processing_stream, now=lambda: self.utc,
        )

    def fake_processing_stream(self, put, stop):
        self.processing_streams.append((put, stop))
        self.processing_started.set()

    def test_processing_rows_localize_fade_and_preserve_children(self):
        from mcp_hub.desktop import processing
        from tests.helpers.desktop import event, snapshot
        child = ChildRow("mcp-server-logs", "connected", "ok")
        self.window.apply(replace(running(child), bitnet=status.BitnetInfo(True, True, "ready")))
        self.assertTrue(self.processing_started.wait(2))
        self.assertIsNone(self.window._stream_stop)
        row = self.window.child_rows[child.server_id]
        put, _stop = self.processing_streams[-1]
        put(("processing", processing.parse_message("snapshot", snapshot(0, events=[]))))
        for sequence in range(1, 6):
            put(("processing", processing.parse_message("event", event(sequence))))
        self.window.process_results()
        self.assertIs(self.window.child_rows[child.server_id], row)
        panel = self.window.processing_log
        self.assertEqual([item.sequence for item in panel.events], [5, 4, 3])
        self.assertEqual(len(panel.rows), 3)
        self.assertEqual([item.graphicsEffect().opacity() for item in panel.rows], [1, .65, .4])
        index = self.window.children_list.indexOf(self.window.bitnet_row)
        self.assertIs(self.window.children_list.itemAt(index + 1).widget(), panel)
        english = panel.rows[0].text()
        self.window.set_language("vi")
        self.assertNotEqual(self.window.processing_log.rows[0].text(), english)
        self.window.set_compact(True)
        self.assertTrue(self.window.compact)
        self.assertFalse(self.window.processing_log.isVisible())
        self.window.set_compact(False)
        self.assertEqual(len(self.window.processing_log.rows), 3)

    def test_processing_absent_route_clears_rows_without_changing_footer(self):
        from mcp_hub.desktop import processing
        from tests.helpers.desktop import snapshot
        ready = replace(running(), bitnet=status.BitnetInfo(True, True, "ready"))
        self.window.apply(ready)
        self.assertTrue(self.processing_started.wait(2))
        put, _stop = self.processing_streams[-1]
        put(("processing", processing.parse_message("snapshot", snapshot())))
        self.window.process_results()
        self.assertEqual(len(self.window.processing_board.events), 1)
        put(("processing_unavailable", None))
        self.window.process_results()
        self.assertEqual(self.window.processing_board.events, ())
        self.assertTrue(self.window.processing_log.isHidden())
        self.assertIsNone(self.window.message_key)

    def test_processing_disable_restart_close_drops_stale_generation(self):
        from mcp_hub.desktop import processing
        from tests.helpers.desktop import snapshot
        ready = replace(running(), bitnet=status.BitnetInfo(True, True, "ready"))
        self.window.apply(ready)
        self.assertTrue(self.processing_started.wait(2))
        put, stop = self.processing_streams[-1]
        message = ("processing", processing.parse_message("snapshot", snapshot()))
        put(message)
        self.window.process_results()
        self.assertEqual(len(self.window.processing_board.events), 1)
        self.window.apply(replace(ready, bitnet=status.BitnetInfo(True, False, "off")))
        self.assertTrue(stop.is_set())
        put(message)
        self.window.process_results()
        self.assertEqual(self.window.processing_board.events, ())
        self.window.apply(ready)
        self.window.restart_hub()
        self.assertIsNone(self.window._processing_stop)
        self.assertEqual(self.window.processing_board.events, ())
        self.window.apply(ready)
        current = self.window._processing_stop
        self.window.close()
        self.assertTrue(current.is_set())

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

    def test_initial_window_is_checking_with_no_enabled_action(self) -> None:
        self.assertEqual(self.window.windowTitle(), "mcp-hub")
        self.assertEqual(self.window.hub_row.text.text(), "Checking the Hub…")
        self.assertEqual(self.window.hub_row.glyph.property("tone"), "wait")
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
        self.assertEqual(self.window.mode_label.text(), "activity UI")
        self.assertEqual(self.window.hub_row.glyph.property("tone"), "ok")
        self.assertEqual(texts(self.window.children_list, whole=True),
                         ["ok mcp-server-logs", "wait mcp-jira-sync Reconnecting"])
        assert_rows_fit(self, self.window.children_list)  # cut text never overflows, whatever the font
        self.assertEqual(self.window.children_count.text(), "1/2")
        self.assertEqual(texts(self.window.clients_list), ["ok claude-code ×2"])
        self.assertEqual(self.window.clients_count.text(), "1")
        self.assertFalse(self.window.start_button.isEnabled())
        self.assertEqual(self.window.footer.text(), "Updated 10:42:05")
        self.assertEqual(self.window.footer.property("role"), "caption")

    def test_fast_mode_and_empty_lists_show_notes(self) -> None:
        self.window.apply(running())
        self.assertEqual(texts(self.window.children_list), ["No child MCPs detected."])
        self.assertEqual(self.window.mode_label.text(), "fast mode")
        self.assertIn("Track AI clients", texts(self.window.clients_list)[0])
        self.window.apply(running(activity_ui=True))
        self.assertEqual(texts(self.window.clients_list), ["No AI clients connected yet"])
        self.window.apply(running(activity_ui=True, clients=("claude-code",), recent=("codex",)))
        self.assertEqual(texts(self.window.clients_list), ["ok claude-code", "off codex Recently active"])
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
        self.assertTrue(self.window.start_button.isEnabled())
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

    def test_buttons_call_their_actions(self) -> None:
        self.window.apply(HubSnapshot(hub="stopped"))
        self.window.start_button.click()
        self.assertEqual(self.launched, [["start"]])
        self.window.settings_button.click()
        self.assertFalse(self.window.settings.isHidden())
        self.assertTrue(self.window.settings_button.isChecked())
        pick(self.window.appearance_page.language_segments, "vi")
        self.assertEqual(self.window.t.language, "vi")
        pick(self.window.appearance_page.theme_segments, "dark")
        self.assertEqual(self.window.theme_name, "dark")
        self.window.settings_button.click()
        self.assertTrue(self.window.settings.isHidden())

    def test_language_choice_rerenders_and_is_remembered(self) -> None:
        self.window.apply(running())
        self.window.set_language("vi")
        vi = i18n.load_strings("vi")
        self.assertEqual(self.window.appearance_page.reset_button.text(), vi["desktop.settings.reset"])
        self.assertTrue(checked(self.window.appearance_page.language_segments, "vi"))
        self.assertTrue(self.window.save_timer.isActive())
        self.window.flush_prefs()
        self.assertFalse(self.window.save_timer.isActive())
        self.assertEqual(prefs.load_prefs(self.prefs_path), {"theme": "light", "language": "vi", "glass": True, "glass_style": "frosted", "compact": False})
        self.window.set_language("en")
        self.assertTrue(checked(self.window.appearance_page.language_segments, "en"))

    def test_theme_switch_restyles_without_rebuilding_and_is_remembered(self) -> None:
        self.window.apply(running(ChildRow("mcp-server-logs", "connected", "ok")))
        cards = list(self.window.cards)
        self.window.set_theme("dark")
        dark = theme.Appearance(theme="dark")
        self.assertEqual(self.window.theme_name, "dark")
        self.assertEqual(self.window.styleSheet(), theme.stylesheet(dark, glass_active=False))
        self.assertEqual(self.styled[-1], (True, theme.backdrop_tint(dark), True))
        self.assertTrue(checked(self.window.appearance_page.theme_segments, "dark"))
        self.assertEqual(self.window.cards, cards)
        self.assertEqual(set(self.window._icons), {("light", "wait"), ("light", "ok"), ("dark", "ok")})
        self.assertEqual(texts(self.window.children_list), ["ok mcp-server-logs"])
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
        pick(self.window.appearance_page.glass_segments, False)
        self.assertFalse(self.window.glass)
        self.assertEqual(self.styled[-1][2], False)
        self.assertEqual(self.window.styleSheet(), theme.stylesheet(theme.Appearance(glass=False), glass_active=False))
        self.assertFalse(self.window.appearance_page.opacity.isEnabled())
        self.assertFalse(self.window.appearance_page.tint.isEnabled())
        self.assertTrue(self.window.glass_available)
        pick(self.window.appearance_page.glass_segments, True)
        self.assertTrue(self.window.glass)

    def test_unavailable_glass_disables_its_controls(self) -> None:
        self.glass = False
        self.window.apply_theme()
        self.assertFalse(self.window.glass_available)
        self.assertFalse(self.window.appearance_page.unavailable.isHidden())
        self.assertFalse(enabled(self.window.appearance_page.glass_segments, True))
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
        self.assertFalse(page.tracking_switch.isChecked())
        self.assertFalse(page.bitnet_switch.isChecked())
        self.assertTrue(page.autostart_switches["app"].isChecked())
        self.assertTrue(page.loading.isHidden())
        self.assertFalse(page.start_button.isEnabled())
        self.assertTrue(page.stop_button.isEnabled())
        self.assertTrue(self.window.settings.pending.isHidden())

    def test_tracking_is_saved_and_a_running_hub_offers_a_confirmed_restart(self) -> None:
        self.window.apply(running())
        self.window.show_tab("hub")
        self.window.process_results()
        self.window.hub_page.tracking_switch.toggleChecked()
        self.window.process_results()
        self.assertTrue(self.store.activity_ui)
        self.assertTrue(self.window.hub_page.tracking_switch.isChecked())
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
        self.assertFalse(page.bitnet_switch.isChecked())
        page.bitnet_switch.toggleChecked()
        self.window.process_results()
        self.assertTrue(self.store.bitnet)
        self.assertTrue(page.bitnet_switch.isChecked())
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
        self.assertTrue(page.bitnet_switch.isHidden())  # On/Off only once installed
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
        self.assertFalse(page.bitnet_switch.isHidden())
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

    def test_an_unsupported_bitnet_cpu_shows_a_fixed_localized_message(self) -> None:
        page = self.window.hub_page
        self.window.apply(replace(running(), bitnet=status.BitnetInfo(error="cpu")))
        self.window.show_tab("hub")
        self.window.process_results()
        self.assertEqual(page.bitnet_status.text(), "BitNet requires AVX2 support. This CPU cannot run its runtime.")

    def test_bitnet_has_its_own_dot_under_the_children_and_is_not_counted(self) -> None:
        info, logs = status.BitnetInfo, ChildRow("mcp-server-logs", "connected", "ok")
        self.window.apply(replace(running(logs), bitnet=info(installed=True, enabled=True, state="ready")))
        self.assertEqual(texts(self.window.children_list), ["ok mcp-server-logs", "ok BitNet"])
        self.assertEqual(self.window.children_count.text(), "1/1")
        self.window.apply(replace(running(logs), bitnet=info(installed=True, enabled=True, state="unavailable")))
        self.assertEqual(texts(self.window.children_list)[-1], "error BitNet Unavailable")
        self.assertEqual(self.window.bitnet_row.glyph.property("tone"), "error")
        self.window.apply(replace(running(logs), bitnet=info(state="installing", installing=True, step="model", progress=7)))
        self.assertEqual(texts(self.window.children_list)[-1], "wait BitNet 7%")
        self.window.apply(replace(running(logs), bitnet=info()))
        self.assertEqual(texts(self.window.children_list)[-1], "off BitNet Not installed")
        self.window.apply(replace(running(logs), bitnet=info(installed=True, state="off")))
        self.assertEqual(texts(self.window.children_list)[-1], "off BitNet Off")
        self.window.apply(running(logs))  # a Hub too old to report BitNet
        self.assertEqual(texts(self.window.children_list), ["ok mcp-server-logs"])

    def test_the_spinner_turns_only_while_its_glyph_is_shown(self) -> None:
        from PySide6.QtWidgets import QLabel

        def frame(glyph) -> object:
            return glyph.pixmap().toImage()

        glyph = QLabel()
        spinner = widgets.Spinner(glyph)
        spinner.start()
        first = frame(glyph)
        spinner.tick()  # hidden after it ran: left alone
        self.assertEqual(frame(glyph), first)
        glyph.show()
        spinner.tick()
        self.assertNotEqual(frame(glyph), first)
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
        self.assertFalse(page.tracking_switch.isEnabled())
        self.assertTrue(page.start_button.isEnabled())
        page.start_button.click()
        self.assertEqual(self.launched, [["start"]])
        self.window.apply(running())  # the Hub answers: the open tab loads its settings
        self.window.process_results()
        self.assertEqual(self.store.reads, 1)
        self.assertTrue(page.needs_hub.isHidden())
        self.assertTrue(page.tracking_switch.isEnabled())

    def test_running_from_source_has_no_app_sign_in_switch(self) -> None:
        self.autostart = {"hub": None, "app": None}
        self.window.show_tab("hub")
        self.window.process_results()
        page = self.window.hub_page
        self.assertFalse(page.app_hint.isHidden())
        self.assertFalse(page.autostart_switches["app"].isEnabled())
        self.assertFalse(page.autostart_switches["hub"].isEnabled())

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
        switch, reason = page.switches["mcp-jira-sync"]
        self.assertFalse(switch.isChecked())
        self.assertTrue(reason.isHidden())
        self.assertFalse(page.switches["mcp-server-logs"][1].isHidden())
        switch.toggleChecked()
        self.window.process_results()
        self.assertTrue(self.store.enabled["mcp-jira-sync"])
        self.assertTrue(page.switches["mcp-jira-sync"][0].isChecked())
        self.assertTrue(self.window.restart_needed)
        self.window.apply(HubSnapshot(hub="stopped"))
        self.assertFalse(self.window.restart_needed)

    def test_a_custom_hub_config_is_shown_read_only(self) -> None:
        self.store.editable = False
        self.window.apply(running())
        self.window.show_tab("mcps")
        self.window.process_results()
        self.assertFalse(self.window.mcps_page.switches["mcp-server-logs"][0].isEnabled())
        self.assertIn("MCP_HUB_CONFIG", self.window.mcps_page.note.text())
        self.assertFalse(self.window.hub_page.tracking_switch.isEnabled())
        self.assertFalse(self.window.hub_page.bitnet_switch.isEnabled())

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

    def open_config_tab(self) -> None:
        self.window.apply(running())
        self.window.show_tab("config")
        self.window.process_results()

    def test_the_config_tab_names_the_gap_and_opens_the_file_on_press(self) -> None:
        self.open_config_tab()
        page = self.window.config_page
        self.assertTrue(any("Missing: openobserve.dev.token" in text for text in texts(page.rows, whole=True)))
        opened: list[str] = []
        with patch.object(system, "open_file", side_effect=opened.append):
            page.items["mcp-server-logs"][1].click()
            self.window.process_results()
        self.assertEqual(self.store.prepared, ["mcp-server-logs"])
        self.assertEqual(opened, [str(self.store.config_file)])
        self.assertTrue(any("Opened. Save the file" in text for text in texts(page.rows, whole=True)))
        self.assertNotIn(str(self.store.config_file), " ".join(texts(page.rows, whole=True)))

    def test_a_failed_hub_request_or_editor_is_reported_on_the_row(self) -> None:
        self.open_config_tab()
        page = self.window.config_page
        self.store.prepare_error = OSError("refused")
        page.items["mcp-server-logs"][1].click()
        self.window.process_results()
        self.assertTrue(any("Hub could not create the config file" in text for text in texts(page.rows, whole=True)))
        self.store.prepare_error = None
        with patch.object(system, "open_file", side_effect=OSError("no app")):
            page.items["mcp-server-logs"][1].click()
            self.window.process_results()
        self.assertTrue(any("Windows could not open the file" in text for text in texts(page.rows, whole=True)))

    def test_a_custom_hub_config_disables_the_open_button_with_its_reason(self) -> None:
        self.store.editable = False
        self.open_config_tab()
        button = self.window.config_page.items["mcp-server-logs"][1]
        self.assertFalse(button.isEnabled())
        self.assertIn("MCP_HUB_CONFIG", button.toolTip())

    def test_a_child_started_with_a_gap_is_a_warning_in_the_children_card(self) -> None:
        self.window.apply(running(ChildRow("mcp-server-logs", "connected", "wait", incomplete=True)))
        self.window.process_results()
        self.assertIn("wait mcp-server-logs Config incomplete", texts(self.window.children_list, whole=True))

    def test_clicking_the_config_warning_opens_the_config_tab_on_that_child(self) -> None:
        self.window.apply(running(ChildRow("mcp-server-logs", "connected", "wait", incomplete=True),
                                  ChildRow("mcp-jira-sync", "connected", "ok")))
        self.window.process_results()
        self.window.show_tab("config")  # builds the Config rows; they exist once the tab has been read
        self.window.process_results()
        self.window.show_settings(False)
        warning = self.window.children_list.itemAt(0).widget().row
        with patch.object(self.window.config_page.items["mcp-server-logs"][1], "setFocus") as focus:
            QTest.mouseClick(warning, Qt.MouseButton.LeftButton)
            self.window.process_results()
        self.assertFalse(self.window.settings.isHidden())
        self.assertEqual(self.window.settings.tab, "config")
        focus.assert_called_once_with()

    def test_the_warning_row_is_a_button_only_when_the_child_has_a_gap(self) -> None:
        self.window.apply(running(ChildRow("mcp-server-logs", "connected", "wait", incomplete=True),
                                  ChildRow("mcp-jira-sync", "connected", "ok")))
        self.window.process_results()
        rows = [self.window.children_list.itemAt(index).widget().row for index in range(2)]
        self.assertEqual(rows[0].cursor().shape(), Qt.CursorShape.PointingHandCursor)
        self.assertEqual(rows[1].cursor().shape(), Qt.CursorShape.ArrowCursor)

    def test_autostart_switches_call_hubctl(self) -> None:
        self.window.show_tab("hub")
        self.window.process_results()
        self.window.hub_page.autostart_switches["hub"].toggleChecked()
        self.window.process_results()
        self.assertEqual(self.autostart_calls, [("hub", False)])
        self.assertFalse(self.window.hub_page.autostart_switches["hub"].isChecked())
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
        self.assertEqual(texts(self.window.children_list, whole=True),
                         ["ok mcp-server-logs", "wait mcp-jira-sync Reconnecting", "off mcp-read-source Disabled"])
        assert_rows_fit(self, self.window.children_list)  # cut text never overflows, whatever the font

    def test_clear_glass_style_is_applied_saved_and_reset(self) -> None:
        self.glass = True
        self.window.apply_theme()
        pick(self.window.appearance_page.style_segments, "clear")
        self.assertEqual(self.window.appearance.glass_style, "clear")
        self.assertTrue(self.clear_styles[-1])
        self.assertTrue(checked(self.window.appearance_page.style_segments, "clear"))
        self.window.flush_prefs()
        self.assertEqual(prefs.load_prefs(self.prefs_path)["glass_style"], "clear")
        self.window.appearance_page.opacity.setValue(0)
        self.assertIn(theme.rgba(theme.LIGHT["surface"], 0.0), self.window.styleSheet())
        self.window.reset_appearance()
        self.assertEqual(self.window.appearance.glass_style, "frosted")
        self.assertFalse(self.clear_styles[-1])
        self.window.set_appearance(glass=False)
        self.assertFalse(enabled(self.window.appearance_page.style_segments, "clear"))

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
        self.assertEqual(bar.dots["mcp-jira-sync"].property("tone"), "live")
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


@unittest.skipIf(QT_MISSING, QT_MISSING or "")
class FluentWidgetsTest(unittest.TestCase):
    """The Fluent conversion: each tone has its own shape, icon-only buttons are named, primary buttons are Fluent's."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.window = app.StatusWindow(
            i18n.Translator("en"), fetch=lambda: HubSnapshot(hub="stopped"),
            launch=lambda _arguments: None, style=lambda *_a, **_k: False, prefs_path=Path(self.tmp.name) / "desktop.json",
            hub_store=FakeStore(), read_autostart=lambda: {"hub": True, "app": True},
            write_autostart=lambda _target, _enabled: {"hub": True, "app": True}, run_worker=lambda job: job(),
            stream=lambda _put, _stop: None,
        )

    def tearDown(self) -> None:
        self.window.poll_timer.stop()
        self.window.update_timer.stop()
        self.window.update_check_timer.stop()
        self.window.drain_timer.stop()
        self.window.close()
        self.window.deleteLater()
        self.tmp.cleanup()

    def test_each_tone_has_its_own_shape(self) -> None:
        shapes = {tone: widgets.tone_pixmap(tone).toImage() for tone in ("ok", "wait", "off", "error")}
        for first, second in (("ok", "wait"), ("ok", "off"), ("ok", "error"), ("wait", "off"), ("wait", "error"), ("off", "error")):
            with self.subTest(first=first, second=second):
                self.assertNotEqual(shapes[first], shapes[second])

    def test_icon_only_buttons_are_named_by_a_tooltip(self) -> None:
        self.window.apply(running())
        for name, widget in (("settings", self.window.settings_button), ("compact", self.window.compact_button),
                             ("refresh", self.window.refresh_button), ("expand", self.window.mini.expand_button),
                             ("check", self.window.update_card.check_button)):
            with self.subTest(button=name):
                self.assertTrue(widget.toolTip())
                self.assertIsInstance(widget, TransparentToolButton)

    def test_the_hub_actions_are_fluent_primary_buttons(self) -> None:
        self.window.apply(HubSnapshot(hub="stopped"))
        self.assertIsInstance(self.window.start_button, PrimaryPushButton)
        self.assertFalse(self.window.start_button.isHidden())
        self.window.apply(running())
        self.assertTrue(self.window.start_button.isHidden())
        self.assertIsInstance(self.window.update_card.notes_button, HyperlinkButton)
