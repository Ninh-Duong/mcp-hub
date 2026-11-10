"""The desktop app's whole-system update card."""
from __future__ import annotations

from collections.abc import Callable
import re

from PySide6.QtWidgets import QHBoxLayout

from . import theme
from .hub_api import UpdateStatus
from .widgets import Card, button, label, set_props

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


def _component_lines(status: UpdateStatus | None, t: Callable[..., str]) -> str:
    if status is None:
        return ""
    lines = []
    for component in status.components:
        component_id = component.get("id")
        state = component.get("state")
        if isinstance(component_id, str) and _ID.fullmatch(component_id) and state in {"checking", "available", "updating", "complete", "skipped", "failed"}:
            error = component.get("error_code")
            suffix = f" — {t(_ERROR_KEYS.get(error, _ERROR_KEYS['unknown']))}" if error else ""
            lines.append(f"{component_id}: {t(_STATE_KEYS[state])}{suffix}")
    return "\n".join(lines)


def _event_lines(status: UpdateStatus | None, t: Callable[..., str]) -> str:
    if status is None:
        return ""
    lines = []
    for event in status.events[-8:]:
        component_id, phase = event.get("component"), event.get("phase")
        if phase in _PHASE_KEYS:
            prefix = f"{component_id}: " if isinstance(component_id, str) and _ID.fullmatch(component_id) else ""
            lines.append(prefix + t(_PHASE_KEYS[phase]))
    return "\n".join(lines)


class UpdateCard(Card):
    def __init__(self, on_check: Callable[[], None], on_update: Callable[[], None],
                 on_retry: Callable[[], None], on_notes: Callable[[], None]) -> None:
        super().__init__(theme.WINDOW_WIDTH)
        self.title = label(role="headline", wrap=True)
        self.note = label(role="caption", wrap=True)
        self.components = label(role="caption", wrap=True)
        self.events = label(role="caption", wrap=True)
        self.check_button = button(on_check, small=True)
        self.update_button = button(on_update)
        self.retry_button = button(on_retry, small=True)
        self.notes_button = button(on_notes, small=True)
        buttons = QHBoxLayout()
        buttons.setSpacing(theme.SPACE_S)
        buttons.addWidget(self.check_button)
        buttons.addWidget(self.update_button)
        buttons.addWidget(self.retry_button)
        buttons.addStretch(1)
        buttons.addWidget(self.notes_button)
        for widget in (self.title, self.note, self.components, self.events):
            self.content.addWidget(widget)
        self.content.addSpacing(theme.SPACE_XS)
        self.content.addLayout(buttons)

    def sync(self, t: Callable[..., str], version: str | None, current: str, state: str,
             status: UpdateStatus | None, hub_running: bool) -> None:
        self.title.setText(t("desktop.update.available", version=version) if version else t("desktop.update.title"))
        self.check_button.setText(t("desktop.update.check"))
        self.update_button.setText(t("desktop.update.button"))
        self.retry_button.setText(t("desktop.update.retry"))
        self.notes_button.setText(t("desktop.update.whatsNew"))
        self.components.setText(_component_lines(status, t))
        self.events.setText(_event_lines(status, t))
        run_state = status.state if status else "idle"
        if state == CHECKING:
            note = t("desktop.update.checking")
        elif state == UPDATING:
            note = t("desktop.update.running")
        elif state == DOWNLOADING:
            note = t("desktop.update.downloading")
        elif state == INSTALLING:
            note = t("desktop.update.installing")
        elif state == FAILED or run_state == "partial":
            note = t("desktop.update.failed")
        elif run_state == "running":
            note = t("desktop.update.running")
        elif version:
            note = t("desktop.update.current", current=current)
        else:
            note = t("desktop.update.current", current=current) if current else t("desktop.update.hubOnly")
        if not hub_running and run_state != "running":
            note = " ".join(filter(None, (note, t("desktop.update.hubNotRunning"))))
        self.note.setText(note)
        set_props(self.note, role="error" if state == FAILED or run_state == "partial" else "caption")
        failed = (status is not None and any(item.get("state") == "failed" for item in status.components)) or state == FAILED
        running = state in {CHECKING, UPDATING, DOWNLOADING, INSTALLING} or run_state == "running"
        self.check_button.setEnabled(not running)
        self.update_button.setEnabled(not running and (version is not None or status is not None))
        self.retry_button.setVisible(failed)
        self.retry_button.setEnabled(not running and failed)
        self.notes_button.setVisible(version is not None)
