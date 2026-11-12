"""The desktop app's update check and install (desktop/update.py): offline, with fake GitHub answers."""
from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from mcp_hub.desktop import constants, update
from mcp_hub.desktop import update_card
from mcp_hub.desktop.status import DesktopError
from mcp_hub.desktop.hub_api import UpdateStatus

try:
    from mcp_hub.desktop import app, i18n
    QT_MISSING = None
except ImportError as exc:
    QT_MISSING = f"Qt is unavailable: {exc}"

DOWNLOAD = f"https://github.com/{constants.UPDATE_REPOSITORY}/releases/download/v0.2.0"
PAGE = f"https://github.com/{constants.UPDATE_REPOSITORY}/releases/tag/v0.2.0"
SETUP = "mcp-hub-status-setup-0.2.0.exe"
INSTALLER = b"installer-bytes" * 1000
GOOD_HASH = hashlib.sha256(INSTALLER).hexdigest()


def release_json(**changes: object) -> dict:
    data = {
        "tag_name": "v0.2.0", "html_url": PAGE, "draft": False, "prerelease": False,
        "assets": [
            {"name": SETUP, "browser_download_url": f"{DOWNLOAD}/{SETUP}"},
            {"name": f"{SETUP}.sha256", "browser_download_url": f"{DOWNLOAD}/{SETUP}.sha256"},
        ],
    }
    return {**data, **changes}


class FakeResponse(io.BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


class FakeGitHub:
    """An opener answering by address; records every request. A value that is an Exception is raised."""

    def __init__(self, answers: dict[str, object]) -> None:
        self.answers, self.requests = answers, []

    def __call__(self, request, timeout: float):
        self.requests.append((request.full_url, dict(request.header_items()), timeout))
        answer = self.answers[request.full_url]
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(answer if isinstance(answer, bytes) else json.dumps(answer).encode("utf-8"))


def github(latest: object, **assets: object) -> FakeGitHub:
    answers = {update.API_URL: latest, f"{DOWNLOAD}/{SETUP}": INSTALLER,
               f"{DOWNLOAD}/{SETUP}.sha256": f"{GOOD_HASH}  {SETUP}".encode()}
    return FakeGitHub({**answers, **assets})


class VersionTest(unittest.TestCase):
    def test_versions_are_three_numbers_with_an_optional_v(self) -> None:
        self.assertEqual(update.parse_version("v0.2.0"), (0, 2, 0))
        self.assertEqual(update.parse_version(" 1.10.3 "), (1, 10, 3))
        for text in ("0.2", "0.2.0-rc1", "v0.2.0.1", "latest", "", "v", None, 3):
            with self.subTest(text=text):
                self.assertIsNone(update.parse_version(text))
        self.assertGreater(update.parse_version("0.10.0"), update.parse_version("0.9.9"))  # numbers, not text

    def test_the_app_reads_its_version_from_the_package_metadata(self) -> None:
        dist = MagicMock(version="0.1.1")
        with patch.object(update.metadata, "distributions", return_value=[dist]) as found:
            self.assertEqual(update.app_version(), "0.1.1")
        found.assert_called_once_with(name="mcp-hub")
        with patch.object(update.metadata, "distributions", return_value=[]):
            self.assertIsNone(update.app_version())

    def test_stale_metadata_of_an_older_version_never_wins(self) -> None:
        # An in-place upgrade left 0.2.0's dist-info beside 0.4.0's; the older one was found first.
        found = [MagicMock(version=text) for text in ("0.2.0", "0.10.0", "0.4.0", "odd")]
        with patch.object(update.metadata, "distributions", return_value=found):
            self.assertEqual(update.app_version(), "0.10.0")


@unittest.skipIf(QT_MISSING, QT_MISSING or "")
class ReleaseCheckScheduleTest(unittest.TestCase):
    def setUp(self) -> None:
        app.application()
        self.tmp = tempfile.TemporaryDirectory()
        self.checks = 0
        self.hub_store = MagicMock()
        self.hub_store.check_updates.return_value = None
        self.hub_store.read_saved_update.return_value = None
        with patch.object(app.hub_api.HubClient, "read_saved_update", return_value=None):
            self.window = app.StatusWindow(
                i18n.Translator("en"), prefs_path=Path(self.tmp.name) / "desktop.json",
                fetch=lambda: None, open_dashboard=lambda: None, launch=lambda _args: None,
                style=lambda *_args, **_kwargs: False, hub_store=self.hub_store,
                read_autostart=lambda: {"hub": True, "app": True},
                write_autostart=lambda _target, _enabled: {"hub": True, "app": True},
                run_worker=lambda job: job(), stream=lambda _put, _stop: None,
                updater=update.Updater("0.1.1", self.check_release, lambda _release: None, lambda _url: None),
            )

    def check_release(self):
        self.checks += 1
        return None

    def tearDown(self) -> None:
        self.window.poll_timer.stop()
        self.window.update_timer.stop()
        self.window.update_check_timer.stop()
        self.window.drain_timer.stop()
        self.window.close()
        self.window.deleteLater()
        self.tmp.cleanup()

    def test_release_discovery_repeats_hourly_and_startup_and_manual_checks_are_immediate(self) -> None:
        self.assertEqual(self.window.update_check_timer.interval(), 60 * 60 * 1000)
        with patch.object(self.window, "refresh"):
            self.window.start()
        self.assertTrue(self.window.update_check_timer.isActive())
        self.assertEqual(self.checks, 1)
        self.window.process_results()
        self.window.check_updates()
        self.assertEqual(self.checks, 2)
        self.window.process_results()
        self.window.update_check_timer.timeout.emit()
        self.assertEqual(self.checks, 3)
        self.assertEqual(self.hub_store.check_updates.call_count, 3)


def keys(key: str, **values: object) -> str:
    return key + "".join(f"[{name}={value}]" for name, value in values.items())


class UpdateErrorDisplayTest(unittest.TestCase):
    def test_known_errors_are_mapped_and_unknown_errors_use_generic_text(self) -> None:
        status = UpdateStatus("run-1", "partial", (
            {"id": "mcp-a", "state": "failed", "error_code": "repo_missing"},
            {"id": "mcp-b", "state": "failed", "error_code": "timeout"},
            {"id": "mcp-c", "state": "failed", "error_code": "unexpected_sensitive_value"},
        ), ())
        line, full = update_card._component_summary(status, keys)
        for text in (line, full):
            self.assertIn("desktop.update.error.repoMissing", text)
            self.assertIn("desktop.update.error.timeout", text)
            self.assertIn("desktop.update.error.unknown", text)
            self.assertNotIn("unexpected_sensitive_value", text)


class UpdateProgressDisplayTest(unittest.TestCase):
    def test_the_summary_counts_finished_items_and_names_only_the_others(self) -> None:
        status = UpdateStatus("run-1", "partial", (
            {"id": "mcp-a", "state": "complete"}, {"id": "mcp-b", "state": "skipped"},
            {"id": "mcp-c", "state": "available"}, {"id": "mcp-hub", "state": "failed", "error_code": "worktree_dirty"},
            {"id": "bad id!", "state": "failed"}, {"id": "mcp-d", "state": "exploded"},
        ), ())
        line, full = update_card._component_summary(status, keys)
        self.assertEqual(line, "desktop.update.summary[done=2][total=4] · mcp-c: desktop.update.state.available"
                               " · mcp-hub: desktop.update.error.worktreeDirty")
        self.assertEqual(len(full.splitlines()), 4)  # the tooltip lists every valid component
        self.assertIn("mcp-a: desktop.update.state.complete", full)
        self.assertEqual(update_card._component_summary(None, keys), ("", ""))

    def test_the_log_keeps_the_newest_line_per_component_newest_first(self) -> None:
        events = tuple({"component": name, "phase": phase, "at": str(index)} for index, (name, phase) in enumerate([
            ("mcp-hub", "check"), ("mcp-a", "check"), ("mcp-a", "check"), ("mcp-b", "fetch"),
            ("mcp-a", "pull"), ("mcp-c", "unknown-phase"), ("mcp-hub", "smoke"),
        ]))
        rows = update_card._event_rows(UpdateStatus("run-1", "running", (), events), keys)
        self.assertEqual([text for _key, text in rows], [
            "mcp-hub: desktop.update.phase.smoke", "mcp-a: desktop.update.phase.pull", "mcp-b: desktop.update.phase.fetch",
        ])
        self.assertEqual(len({key for key, _text in rows}), 3)
        self.assertEqual(update_card._event_rows(None, keys), ())


class CheckLatestTest(unittest.TestCase):
    def test_a_higher_release_with_an_installer_and_its_hash_is_offered(self) -> None:
        opener = github(release_json())
        release = update.check_latest("0.1.1", opener)
        self.assertEqual((release.label, release.notes_url), ("0.2.0", PAGE))
        self.assertEqual(release.setup_url, f"{DOWNLOAD}/{SETUP}")
        self.assertEqual(release.sha_url, f"{DOWNLOAD}/{SETUP}.sha256")
        url, headers, timeout = opener.requests[0]
        self.assertEqual(url, update.API_URL)
        self.assertEqual(update.API_URL, f"https://api.github.com/repos/{constants.UPDATE_REPOSITORY}/releases/latest")
        self.assertLessEqual(timeout, 10)
        self.assertNotIn("Authorization", headers)  # nothing private is sent

    def test_the_same_or_an_older_release_offers_nothing(self) -> None:
        for current in ("0.2.0", "0.3.0", "1.0.0"):
            with self.subTest(current=current):
                self.assertIsNone(update.check_latest(current, github(release_json())))

    def test_a_release_without_a_usable_installer_offers_nothing(self) -> None:
        odd = [
            release_json(assets=[]),
            release_json(assets=release_json()["assets"][:1]),  # no hash
            release_json(assets=[{"name": "setup.exe", "browser_download_url": f"{DOWNLOAD}/setup.exe"}]),
            release_json(assets=[dict(a, browser_download_url="https://example.com/x") for a in release_json()["assets"]]),
            release_json(assets=[{"name": SETUP}, "text", None]),
            release_json(assets="none"),
            release_json(tag_name="v0.2.0-rc1"), release_json(tag_name="nightly"), release_json(tag_name=None),
            release_json(draft=True), release_json(prerelease=True),
            release_json(html_url="https://example.com/release"), release_json(html_url=None),
        ]
        for data in odd:
            with self.subTest(data=str(data)[:60]):
                self.assertIsNone(update.check_latest("0.1.1", github(data)))

    def test_every_failure_is_nothing_and_never_raised(self) -> None:
        failures = [urllib.error.URLError("offline"), urllib.error.HTTPError(update.API_URL, 403, "rate limit", {}, None),
                    TimeoutError(), ConnectionResetError(), b"not json", b"\xff\xfe", ["a list"], 3, None]
        for failure in failures:
            with self.subTest(failure=repr(failure)):
                self.assertIsNone(update.check_latest("0.1.1", FakeGitHub({update.API_URL: failure})))

    def test_an_unknown_current_version_never_asks_github(self) -> None:
        opener = github(release_json())
        for current in (None, "", "dev"):
            self.assertIsNone(update.check_latest(current, opener))
        self.assertEqual(opener.requests, [])

    def test_the_answer_is_size_limited(self) -> None:
        huge = b"{" + b" " * (update.MAX_RELEASE_JSON_BYTES + 10)
        self.assertIsNone(update.check_latest("0.1.1", FakeGitHub({update.API_URL: huge})))


class DownloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name) / "update"
        self.release = update.check_latest("0.1.1", github(release_json()))

    def test_the_installer_is_returned_only_when_its_hash_matches(self) -> None:
        opener = github(release_json())
        path = update.download_verified(self.release, self.folder, opener)
        self.assertEqual(path, self.folder / SETUP)
        self.assertEqual(path.read_bytes(), INSTALLER)
        self.assertEqual([name.name for name in self.folder.iterdir()], [SETUP])  # no leftover partial file
        self.assertEqual([url for url, _h, _t in opener.requests], [f"{DOWNLOAD}/{SETUP}.sha256", f"{DOWNLOAD}/{SETUP}"])

    def test_a_wrong_hash_deletes_the_file_and_never_returns_it(self) -> None:
        opener = github(release_json(), **{f"{DOWNLOAD}/{SETUP}.sha256": ("0" * 64).encode()})
        with self.assertRaisesRegex(DesktopError, "SHA-256"):
            update.download_verified(self.release, self.folder, opener)
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_a_missing_or_malformed_hash_is_refused_before_anything_is_downloaded(self) -> None:
        for text in (b"", b"not-a-hash  file", b"abc", ("g" * 64).encode()):
            with self.subTest(text=text):
                opener = github(release_json(), **{f"{DOWNLOAD}/{SETUP}.sha256": text})
                with self.assertRaises(DesktopError):
                    update.download_verified(self.release, self.folder, opener)
                self.assertEqual([url for url, _h, _t in opener.requests], [f"{DOWNLOAD}/{SETUP}.sha256"])

    def test_a_hash_in_capitals_matches(self) -> None:
        opener = github(release_json(), **{f"{DOWNLOAD}/{SETUP}.sha256": GOOD_HASH.upper().encode()})
        self.assertEqual(update.download_verified(self.release, self.folder, opener).name, SETUP)

    def test_network_and_disk_failures_are_one_fixed_error(self) -> None:
        for failure in (urllib.error.URLError("offline"), TimeoutError()):
            with self.subTest(failure=failure):
                opener = github(release_json(), **{f"{DOWNLOAD}/{SETUP}": failure})
                with self.assertRaisesRegex(DesktopError, "could not be downloaded") as caught:
                    update.download_verified(self.release, self.folder, opener)
                self.assertNotIn(self.tmp.name, str(caught.exception))  # no path in the message
        self.assertEqual([p.name for p in self.folder.iterdir()], [])

    def test_a_download_larger_than_the_limit_is_stopped(self) -> None:
        with patch.object(update, "MAX_INSTALLER_BYTES", 100):
            with self.assertRaisesRegex(DesktopError, "larger"):
                update.download_verified(self.release, self.folder, github(release_json()))
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_the_file_name_comes_from_the_version_not_from_the_address(self) -> None:
        sneaky = update.Release((0, 2, 0), f"{DOWNLOAD}/..%5C..%5Cevil.exe", f"{DOWNLOAD}/{SETUP}.sha256", PAGE)
        opener = github(release_json(), **{sneaky.setup_url: INSTALLER})
        self.assertEqual(update.download_verified(sneaky, self.folder, opener), self.folder / SETUP)


class LaunchTest(unittest.TestCase):
    def test_a_hidden_powershell_waits_installs_silently_and_reopens_the_app(self) -> None:
        run = MagicMock()
        with patch.dict("os.environ", {"SystemRoot": "WinRoot"}):
            update.launch_update(Path("tmp/setup.exe"), "app/mcp-hub-status.exe", 4242, run)
        command = run.call_args.args[0]
        self.assertTrue(command[0].lower().endswith("powershell.exe"))
        self.assertEqual(command[1:4], ["-NoProfile", "-NonInteractive", "-Command"])
        script = command[4]
        self.assertLess(script.index("Wait-Process -Id $env:STATUS_UPDATE_PID"), script.index("/SILENT"))
        self.assertLess(script.index("/SILENT"), script.index("Start-Process -FilePath $env:STATUS_UPDATE_EXE"))
        self.assertIn("'/SUPPRESSMSGBOXES'", script)
        self.assertIn("-Wait", script)
        env = run.call_args.kwargs["env"]
        self.assertEqual((env["STATUS_UPDATE_PID"], env["STATUS_UPDATE_EXE"]), ("4242", "app/mcp-hub-status.exe"))
        self.assertTrue(env["STATUS_UPDATE_INSTALLER"].endswith("setup.exe"))
        self.assertNotIn("setup.exe", script)  # paths travel in the environment, never inside the script text
        self.assertEqual(run.call_args.kwargs["creationflags"], getattr(update.subprocess, "CREATE_NO_WINDOW", 0))

    def test_without_windows_nothing_starts(self) -> None:
        run = MagicMock()
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(OSError):
            update.launch_update(Path("setup.exe"), "app.exe", 1, run)
        run.assert_not_called()


class InstallTest(unittest.TestCase):
    release = update.Release((0, 2, 0), f"{DOWNLOAD}/{SETUP}", f"{DOWNLOAD}/{SETUP}.sha256", PAGE)

    def frozen(self, exe: str = "apps/mcp-hub-status.exe"):
        return patch.multiple(update.sys, frozen=True, executable=exe, create=True)

    def test_the_installer_is_verified_before_it_is_launched(self) -> None:
        order: list[str] = []
        with self.frozen(), \
                patch.object(update, "download_verified", side_effect=lambda *_a: order.append("verified") or Path("s.exe")), \
                patch.object(update, "launch_update", side_effect=lambda *_a: order.append("launched")):
            update.install(self.release)
        self.assertEqual(order, ["verified", "launched"])

    def test_a_failed_verification_never_launches_anything(self) -> None:
        with self.frozen(), patch.object(update, "download_verified", side_effect=DesktopError("bad")), \
                patch.object(update, "launch_update") as launch:
            with self.assertRaises(DesktopError):
                update.install(self.release)
        launch.assert_not_called()

    def test_a_launch_failure_is_one_fixed_error(self) -> None:
        with self.frozen(), patch.object(update, "download_verified", return_value=Path("s.exe")), \
                patch.object(update, "launch_update", side_effect=OSError("secret/path")):
            with self.assertRaisesRegex(DesktopError, "could not be started") as caught:
                update.install(self.release)
        self.assertNotIn("secret", str(caught.exception))

    def test_only_the_installed_app_replaces_itself(self) -> None:
        with patch.object(update, "download_verified") as download:
            with self.assertRaises(DesktopError):  # from source (not frozen)
                update.install(self.release)
            with self.frozen("python/python.exe"), self.assertRaises(DesktopError):  # not our exe
                update.install(self.release)
        download.assert_not_called()

    def test_the_default_updater_exists_only_for_the_installed_app_that_knows_its_version(self) -> None:
        with patch.object(update, "app_version", return_value="0.1.1"):
            self.assertIsNone(update.default_updater())  # from source
            with self.frozen():
                updater = update.default_updater()
        self.assertEqual(updater.current, "0.1.1")
        with patch.object(update, "check_latest", return_value=None) as check:
            self.assertIsNone(updater.check())
        check.assert_called_once_with("0.1.1")
        with patch.object(update, "app_version", return_value=None), self.frozen():
            self.assertIsNone(update.default_updater())

    def test_the_release_page_opens_through_the_given_opener(self) -> None:
        opened = MagicMock()
        update.open_notes(self.release, opened)
        opened.assert_called_once_with(PAGE)

    def test_github_uses_its_own_opener_and_never_the_hubs(self) -> None:
        from mcp_hub.desktop import status

        self.assertIsNot(update.github_opener(), status.OPENER)
        handlers = {type(handler).__name__ for handler in update.github_opener().handlers}
        self.assertIn("HTTPSHandler", handlers)
        self.assertNotIn("HTTPSHandler", {type(handler).__name__ for handler in status.OPENER.handlers})


if __name__ == "__main__":
    unittest.main()
