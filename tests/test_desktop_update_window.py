"""Desktop window tests: Update card and update flow in the window."""
from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch



from mcp_hub.desktop import i18n, theme, update
from mcp_hub.desktop.hub_api import UpdateStatus
from mcp_hub.desktop.status import ChildRow, HubSnapshot
from tests.helpers.window import QApplication, app, update_card, QT_MISSING, qt_app, running, elided, full_text, FakeStore


def setUpModule() -> None:
    qt_app()


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
            i18n.Translator("en"), fetch=lambda: HubSnapshot(hub="stopped"),
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
        self.assertEqual(self.card().title.text(), "UPDATES")
        self.assertEqual(self.card().version_line.text(), "Version 0.2.0 is available")
        self.assertFalse(self.card().update_primary.isHidden())  # the Hub runs: Update is the one primary action
        self.assertTrue(self.card().update_button.isHidden())
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
        self.assertEqual(self.card().title.text(), vi["desktop.update.title"])
        self.assertEqual(self.card().version_line.text(), vi["desktop.update.available"].replace("{version}", "0.2.0"))
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

    @staticmethod
    def partial_run() -> UpdateStatus:
        return UpdateStatus("run-1", "partial", (
            {"id": "mcp-jira-sync", "kind": "child", "state": "failed", "phase": "pull",
             "error_code": "worktree_dirty"},
        ), ({"component": "mcp-jira-sync", "phase": "pull"},))

    def test_partial_system_update_still_installs_app_and_keeps_failed_retry_state(self) -> None:
        self.offer(running(ChildRow("mcp-jira-sync", "connected", "ok")))
        failed = self.partial_run()
        self.window._user_update = True  # the user pressed Update
        self.window.update_status = failed
        self.window._update_changed("update_status", failed)
        self.assertEqual(self.installed, [self.release])
        self.assertEqual(self.window.update_status, failed)
        self.assertIn("mcp-jira-sync", self.card().summary.text() + self.card().summary.toolTip())
        self.assertIn("Local changes need attention", self.card().summary.text() + self.card().summary.toolTip())
        self.assertEqual(full_text(self.card().log.rows[0]), "mcp-jira-sync: Downloading code")
        self.assertFalse(self.card().retry_button.isHidden())
        self.assertTrue(self.card().update_button.isHidden())  # one main button at a time

    def test_a_check_that_ends_never_installs_or_closes_the_app(self) -> None:
        # The loop seen in the field: a startup/hourly check ended partial or complete and the app reinstalled itself.
        self.offer(running(ChildRow("a", "connected", "ok")))
        for state in ("partial", "complete"):
            with self.subTest(state=state), patch.object(self.window, "close") as close:
                self.window._update_changed("update_status", replace(self.partial_run(), state=state))
                self.window.process_results()
                close.assert_not_called()
        self.assertEqual(self.installed, [])

    def test_a_hub_check_that_needs_attention_is_not_shown_as_an_install_failure(self) -> None:
        self.store.update = self.partial_run()
        self.offer(running(ChildRow("a", "connected", "ok")))
        self.assertEqual(self.card().note.text(), "Some items need attention.")
        self.assertEqual(self.card().note.property("role"), "caption")
        summary = self.card().summary
        self.assertEqual(summary.text(), elided(summary, "0/1 done · mcp-jira-sync: Local changes need attention",
                                                theme.WINDOW_WIDTH - 2 * theme.SPACE_M))
        self.assertIn("mcp-jira-sync: Failed — Local changes need attention", summary.toolTip())

    def test_the_progress_log_rolls_three_fading_lines(self) -> None:
        events = tuple({"component": f"mcp-{index}", "phase": "check", "at": str(index)} for index in range(5))
        self.store.update = UpdateStatus("run-1", "running", (), events)
        self.offer(running(ChildRow("a", "connected", "ok")))
        log = self.card().log
        self.assertEqual([full_text(row) for row in log.rows],
                         [f"mcp-{index}: Checking for updates" for index in (4, 3, 2)])
        self.assertEqual([row.graphicsEffect().opacity() for row in log.rows], [1, .65, .4])
        self.assertEqual(log.height(), log.row_height() * 3)

    def test_the_buttons_show_their_whole_label_in_every_language(self) -> None:
        self.store.update = self.partial_run()
        self.offer(running(ChildRow("a", "connected", "ok")))
        self.window.show()
        for language in ("en", "vi"):
            with self.subTest(language=language):
                self.window.set_language(language)
                QApplication.processEvents()
                for name in ("retry_button", "notes_button"):
                    widget = getattr(self.card(), name)
                    self.assertFalse(widget.isHidden(), name)
                    # The layout gives each button at least what its whole label needs, in every language.
                    self.assertGreaterEqual(widget.width(), widget.sizeHint().width(), name)

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
        self.assertEqual(self.card().title.text(), "UPDATES")
