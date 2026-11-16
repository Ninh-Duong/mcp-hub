"""The update flow of the status window: check, download, install, retry, and the release notes."""

from __future__ import annotations
import time
from functools import partial
from typing import Any
from . import system, update
from .update_card import CHECKING, DOWNLOADING, FAILED, INSTALLING, READY, UPDATING




class UpdateMixin:
    """Update flow of StatusWindow (moved out of app.py)."""

    def check_updates(self) -> None:
        if self.update_state in {CHECKING, UPDATING, DOWNLOADING, INSTALLING}:
            return
        self.update_state = CHECKING
        self.render()

        def job() -> None:
            release = self.updater.check() if self.updater else None
            try:
                status_value = self.hub_store.check_updates()
            except (OSError, ValueError):
                status_value = self.hub_store.read_saved_update()
            self.results.put(("update_check", (release, status_value)))

        self.run_worker(job)

    def refresh_update(self) -> None:
        """Refresh durable status while a run is active, including after Hub restarts."""
        if self._update_polling or not (self.update_status and self.update_status.state == "running"):
            return
        self._update_polling = True

        def job() -> None:
            try:
                current = self.hub_store.read_update()
            except (OSError, ValueError):
                current = self.hub_store.read_saved_update() or self.update_status
            self.results.put(("update_status", current))

        self.run_worker(job)

    def _update_changed(self, kind: str, value: Any) -> None:
        if kind == "update_check":
            self.update_release, self.update_status = value
            self.update_state = READY
        elif kind == "update_status":
            self._update_polling = False
            self.update_status = value
            if value is not None and value.state in {"complete", "partial"}:
                self.update_state = FAILED if value.state == "partial" else READY
                if self._user_update:
                    self._user_update = False
                    self._install_desktop_update()
            else:
                self.update_state = UPDATING
        else:
            self.update_state = value
        self.render()
        if self.update_state == INSTALLING:
            self.close()

    def _start_system_update(self, retry: bool = False) -> None:
        self.update_state = UPDATING
        self._user_update = True
        self.render()

        def job() -> None:
            try:
                if not self.snapshot or self.snapshot.hub != "running":
                    system.start_hub()
                    deadline = time.monotonic() + system.START_TIMEOUT_SECONDS
                    while time.monotonic() < deadline:
                        try:
                            self.hub_store.read_update()
                            break
                        except (OSError, ValueError):
                            time.sleep(0.5)
                current = self.update_status
                failed = tuple(item.get("id") for item in (current.components if current else ())
                               if item.get("state") == "failed" and isinstance(item.get("id"), str))
                result = self.hub_store.retry_update(failed) if retry else self.hub_store.start_update()
                self.results.put(("update_status", result))
            except (OSError, ValueError):
                self._user_update = False  # no run started, so no later status may install
                self.results.put(("update_state", FAILED))
                if self.update_release:
                    self.run_worker(self._install_desktop_update)

        self.run_worker(job)

    def install_update(self) -> None:
        if self.update_state not in {READY, FAILED}:
            return
        if self.update_state == FAILED and self.update_status and self.update_status.state == "complete":
            self._install_desktop_update()
            return
        self._start_system_update()

    def retry_update(self) -> None:
        if self.update_state != FAILED:
            return
        if self.update_status and self.update_status.state == "complete":
            self._install_desktop_update()
            return
        self._start_system_update(retry=True)

    def _install_desktop_update(self) -> None:
        release, updater = self.update_release, self.updater
        if release is None or updater is None:
            return
        self.update_state = DOWNLOADING
        self.render()

        def job() -> None:
            try:
                updater.install(release)
            except (OSError, RuntimeError):
                self.results.put(("update_state", FAILED))
            else:
                self.results.put(("update_state", INSTALLING))

        self.run_worker(job)

    def preview_update(self, release: update.Release | None) -> None:
        """Screenshot mode: show (or hide) the update card for a sample release."""
        self.update_release, self.update_state = release, READY
        self.render()

    def show_update_notes(self) -> None:
        if self.update_release is not None and self.updater is not None:
            self.run_worker(partial(self.updater.open_notes, self.update_release))
