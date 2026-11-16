"""Actions of the status window that change the Hub's settings or read a child's configuration, each run off the Qt thread."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any
from . import hub_api, system


class ActionsMixin:
    """Settings and configuration actions of StatusWindow (moved out of app.py)."""

    def _run(self, kind: str, action: Callable[..., Any], *args: Any, error_key: str) -> None:
        """Run Hub calls and system work off the Qt thread; the result (or an error message) comes back through the queue."""
        def job() -> None:
            try:
                self.results.put((kind, action(*args)))
            except (OSError, ValueError):
                self.results.put(("message", error_key))

        self.run_worker(job)

    def _write_hub_settings(self, write: Callable[[], None]) -> hub_api.HubSettings:
        write()
        return self.hub_store.read_settings()

    def _settings_loaded(self, settings: hub_api.HubSettings, *, changed: bool) -> None:
        self.hub_settings = settings
        if changed and self.snapshot is not None and self.snapshot.hub == "running":
            self.restart_needed = True
        self.render()

    def set_tracking(self, enabled: bool) -> None:
        if self.hub_settings is not None and self.hub_settings.activity_ui == enabled:
            return
        write = partial(self.hub_store.set_activity_ui, enabled)
        self._run("hub_changed", self._write_hub_settings, write, error_key="desktop.error.settings")

    def set_bitnet(self, enabled: bool) -> None:
        if self.hub_settings is not None and self.hub_settings.bitnet == enabled:
            return
        write = partial(self.hub_store.set_bitnet, enabled)
        self._run("hub_changed", self._write_hub_settings, write, error_key="desktop.error.settings")

    def open_child_config(self, server_id: str) -> None:
        """Create the child's private config file if needed, then open it in the editor (off the Qt thread)."""
        if server_id in self.config_busy:
            return
        self.config_busy.add(server_id)
        self.config_feedback.pop(server_id, None)
        self.render()

        def job() -> None:
            try:
                path = self.hub_store.prepare_child_config(server_id)
            except (OSError, ValueError):
                self.results.put(("config_failed", (server_id, "desktop.error.configOpen.hub")))
                return
            try:
                system.open_file(path)
            except OSError:
                self.results.put(("config_failed", (server_id, "desktop.error.configOpen.editor")))
                return
            self.results.put(("config_opened", server_id))

        self.run_worker(job)

    def _config_result(self, kind: str, value: Any) -> None:
        """A press finished: the file opened (wait until the Hub restarts) or it failed (the reason key)."""
        if kind == "config_opened":
            server_id, feedback = value, ("wait", "desktop.settings.config.opened")
        else:
            server_id, feedback = value[0], ("error", value[1])
        self.config_busy.discard(server_id)
        self.config_feedback[server_id] = feedback
        self.render()

    def _loaded_gaps(self) -> frozenset[str]:
        """Children the running Hub started with a settings gap (their rows carry the flag)."""
        children = self.snapshot.children if self.snapshot else ()
        return frozenset(child.server_id for child in children if child.incomplete)

    def show_config(self, server_id: str) -> None:
        """The Child MCPs warning leads here: Settings on the Config tab, with that child's row focused."""
        self.show_tab("config")
        self.config_page.focus_row(server_id)

    def set_child(self, server_id: str, enabled: bool) -> None:
        write = partial(self.hub_store.set_server_enabled, server_id, enabled)
        self._run("hub_changed", self._write_hub_settings, write, error_key="desktop.error.settings")

    def load_tab(self, name: str) -> None:
        """Read what the opened tab shows; the Appearance tab needs nothing."""
        if name != "config":
            self.config_feedback.clear()  # an outcome belongs to the tab it was shown on
        if name == "appearance" or self.settings.isHidden():
            return
        if self.snapshot is not None and self.snapshot.hub == "running":
            self._run("hub_settings", self.hub_store.read_settings, error_key="desktop.error.settings")
        if name == "hub":
            self._run("autostart", self.read_autostart, error_key="desktop.error.autostart")
