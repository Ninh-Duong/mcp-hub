"""The desktop app's whole-system update card.

Compact by design: a title (with "What's new" beside it), one status note, one summary line of the
components, a rolling log of at most theme.LOG_ROWS progress lines, and one main button plus a check glyph.
"""
from __future__ import annotations

from collections.abc import Callable
import re

from PySide6.QtWidgets import QHBoxLayout, QSizePolicy

from . import theme
from .hub_api import UpdateStatus
from .widgets import Card, FadingLog, button, label, set_props, show_elided

READY, CHECKING, UPDATING, DOWNLOADING, INSTALLING, FAILED = (
    "ready", "checking", "updating", "downloading", "installing", "failed",
)
_ID = re.compile(r"[a-zA-Z0-9_-]{1,64}")
_PHASE_KEYS = {
    "check": "desktop.update.phase.check", "fetch": "desktop.update.phase.fetch",
    "pull": "desktop.update.phase.pull", "stop_hub": "desktop.update.phase.stopHub",
    "restart": "desktop.update.phase.restart", "smoke": "desktop.update.phase.smoke",
    "current": "desktop.update.phase.current",
}
_STATE_KEYS = {
    "checking": "desktop.update.state.checking", "available": "desktop.update.state.available",
    "updating": "desktop.update.state.updating", "complete": "desktop.update.state.complete",
    "skipped": "desktop.update.state.skipped", "failed": "desktop.update.state.failed",
}
_ERROR_KEYS = {
    "git_unavailable": "desktop.update.error.gitUnavailable", "git_failed": "desktop.update.error.gitFailed",
    "repo_missing": "desktop.update.error.repoMissing",
    "worktree_dirty": "desktop.update.error.worktreeDirty", "upstream_missing": "desktop.update.error.upstreamMissing",
    "branch_ahead_or_diverged": "desktop.update.error.branchAheadOrDiverged",
    "hub_stop_failed": "desktop.update.error.hubStopFailed", "hub_start_failed": "desktop.update.error.hubStartFailed",
    "hub_update_failed": "desktop.update.error.hubUpdateFailed", "health_check_failed": "desktop.update.error.healthCheckFailed",
    "process_start_failed": "desktop.update.error.processStartFailed", "config_unavailable": "desktop.update.error.configUnavailable",
    "timeout": "desktop.update.error.timeout",
    "unknown": "desktop.update.error.unknown",
}
# Finished states are only counted in the summary; the others are named there.
_DONE_STATES = {"complete", "skipped"}
_BUSY = {CHECKING, UPDATING, DOWNLOADING, INSTALLING}
_SUMMARY_WIDTH = theme.WINDOW_WIDTH - 2 * theme.SPACE_M


def _components(status: UpdateStatus | None) -> list[tuple[str, str, object]]:
    """Valid `(id, state, error_code)` rows; anything malformed is dropped."""
    found = []
    for component in status.components if status else ():
        component_id, state = component.get("id"), component.get("state")
        if isinstance(component_id, str) and _ID.fullmatch(component_id) and state in _STATE_KEYS:
            found.append((component_id, state, component.get("error_code")))
    return found


def _detail(state: str, error: object, t: Callable[..., str]) -> str:
    if error:
        return t(_ERROR_KEYS.get(error, _ERROR_KEYS["unknown"])) if isinstance(error, str) else t(_ERROR_KEYS["unknown"])
    return t(_STATE_KEYS[state])


def _component_summary(status: UpdateStatus | None, t: Callable[..., str]) -> tuple[str, str]:
    """One line `done/total done · id: detail …` naming only what is not finished, and the full list as tooltip."""
    components = _components(status)
    if not components:
        return "", ""
    done = sum(state in _DONE_STATES for _id, state, _error in components)
    named = [f"{component_id}: {_detail(state, error, t)}" for component_id, state, error in components
             if state not in _DONE_STATES]
    line = " · ".join([t("desktop.update.summary", done=done, total=len(components)), *named])
    full = "\n".join(f"{component_id}: {t(_STATE_KEYS[state])}" + (f" — {_detail(state, error, t)}" if error else "")
                     for component_id, state, error in components)
    return line, full


def _event_rows(status: UpdateStatus | None, t: Callable[..., str]) -> tuple[tuple[object, str], ...]:
    """The newest progress line of each component, newest first, at most theme.LOG_ROWS of them."""
    rows: list[tuple[object, str]] = []
    seen: set[str] = set()
    for event in reversed(status.events if status else ()):
        component_id, phase = event.get("component"), event.get("phase")
        if phase not in _PHASE_KEYS:
            continue
        name = component_id if isinstance(component_id, str) and _ID.fullmatch(component_id) else ""
        if name in seen:
            continue
        seen.add(name)
        prefix = f"{name}: " if name else ""
        rows.append(((name, phase, event.get("at")), prefix + t(_PHASE_KEYS[phase])))
        if len(rows) == theme.LOG_ROWS:
            break
    return tuple(rows)


def _note(t: Callable[..., str], state: str, run_state: str, current: str) -> tuple[str, str]:
    """The status sentence and its role. Red only when installing the app itself failed."""
    if state in _BUSY:
        keys = {CHECKING: "desktop.update.checking", UPDATING: "desktop.update.running",
                DOWNLOADING: "desktop.update.downloading", INSTALLING: "desktop.update.installing"}
        return t(keys[state]), "caption"
    if state == FAILED:
        return t("desktop.update.failed"), "error"
    if run_state == "partial":
        return t("desktop.update.needsAttention"), "caption"
    if run_state == "running":
        return t("desktop.update.running"), "caption"
    return (t("desktop.update.current", current=current) if current else t("desktop.update.hubOnly")), "caption"


class UpdateCard(Card):
    def __init__(self, on_check: Callable[[], None], on_update: Callable[[], None],
                 on_retry: Callable[[], None], on_notes: Callable[[], None]) -> None:
        super().__init__(theme.WINDOW_WIDTH)
        self.title = label(role="headline", wrap=True)
        self.notes_button = button(on_notes, small=True)
        self.note = label(role="caption", wrap=True)
        self.summary = label(role="caption")
        self.log = FadingLog()
        self.update_button = button(on_update)
        self.retry_button = button(on_retry)
        self.check_button = button(on_check, small=True)
        self.check_button.setText(theme.REFRESH_GLYPH)
        heading = QHBoxLayout()
        heading.setSpacing(theme.SPACE_S)
        heading.addWidget(self.title, 1)
        heading.addWidget(self.notes_button)
        buttons = QHBoxLayout()
        buttons.setSpacing(theme.SPACE_S)
        for main in (self.update_button, self.retry_button):
            main.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            buttons.addWidget(main, 1)
        buttons.addWidget(self.check_button)
        self.content.addLayout(heading)
        for widget in (self.note, self.summary, self.log):
            self.content.addWidget(widget)
        self.content.addSpacing(theme.SPACE_XS)
        self.content.addLayout(buttons)

    def sync(self, t: Callable[..., str], version: str | None, current: str, state: str,
             status: UpdateStatus | None, hub_running: bool) -> None:
        self.title.setText(t("desktop.update.available", version=version) if version else t("desktop.update.title"))
        self.update_button.setText(t("desktop.update.button"))
        self.retry_button.setText(t("desktop.update.retry"))
        self.notes_button.setText(t("desktop.update.whatsNew"))
        self.check_button.setToolTip(t("desktop.update.check"))
        self._sync_progress(t, status)
        run_state = status.state if status else "idle"
        note, role = _note(t, state, run_state, current)
        if not hub_running and run_state != "running":
            note = " ".join(filter(None, (note, t("desktop.update.hubNotRunning"))))
        self.note.setText(note)
        set_props(self.note, role=role)
        self._sync_buttons(version, state, status, run_state)

    def _sync_progress(self, t: Callable[..., str], status: UpdateStatus | None) -> None:
        line, full = _component_summary(status, t)
        show_elided(self.summary, line, _SUMMARY_WIDTH, full)
        self.summary.setVisible(bool(line))
        self.log.show_lines(_event_rows(status, t))

    def _sync_buttons(self, version: str | None, state: str, status: UpdateStatus | None, run_state: str) -> None:
        failed = state == FAILED or any(item.get("state") == "failed" for item in (status.components if status else ()))
        running = state in _BUSY or run_state == "running"
        self.check_button.setEnabled(not running)
        # One main button at a time: Retry takes Update's place while something failed.
        self.update_button.setVisible(not failed)
        self.update_button.setEnabled(not running and (version is not None or status is not None))
        self.retry_button.setVisible(failed)
        self.retry_button.setEnabled(not running and failed)
        self.notes_button.setVisible(version is not None)
