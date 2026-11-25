"""Rendering of the status window: each section draws only when its data changed, so a poll that changes nothing redraws nothing."""

from __future__ import annotations

from .constants import NOT_DRAWN
from collections.abc import Callable
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from . import status, theme, usage
from .widgets import ProcessingLog, Row, Spinner, UsageRow, button, dot_icon, label, set_props, show_elided




class RenderMixin:
    """Render pipeline of StatusWindow (moved out of app.py)."""

    def render(self) -> None:
        """Redraw only the sections whose content changed since the last render."""
        hub = self.snapshot.hub if self.snapshot else None
        for name, key, draw in self._sections(hub):
            if self._keys.get(name, NOT_DRAWN) != key:
                draw(hub)
                self._keys[name] = key
        self._render_footer()

    def _sections(self, hub: str | None) -> tuple[tuple[str, object, Callable[[str | None], None]], ...]:
        snapshot, language = self.snapshot, self.t.language
        activity_ui = bool(snapshot and snapshot.activity_ui)
        return (
            ("texts", language, self._render_texts),
            ("hub", (language, hub, activity_ui), self._render_hub),
            ("children", (language, hub, snapshot.children if snapshot else (), snapshot.bitnet if snapshot else None),
             self._render_children),
            ("approvals", (language, hub, self.compact, snapshot.pending_approvals if snapshot else ()),
             self._render_approvals),
            ("usage", (language, activity_ui, self._usage_lines(activity_ui)), self._render_usage),
            ("clients", (language, hub, activity_ui, snapshot.clients if snapshot else (),
                         snapshot.recent if snapshot else ()), self._render_clients),
            ("client_usage", (language, self._client_usage_key(activity_ui)), self._render_client_usage),
            ("update", (language, self.update_release, self.update_state, self.update_status, hub == "running"),
             self._render_update),
            ("actions", hub, self._render_actions),
            ("mini", (language, self._mini_state(activity_ui)), self._render_mini),
            ("settings", (language, hub, self.hub_settings, self._autostart_key(), self.restart_needed,
                          snapshot.bitnet_state if snapshot else None, snapshot.bitnet if snapshot else None,
                          frozenset(self.config_busy), tuple(sorted(self.config_feedback.items())),
                          self._loaded_gaps()),
             self._render_settings),
            ("icon", snapshot.tone if snapshot else "wait", self._render_icon),
        )

    def _render_texts(self, _hub: str | None) -> None:
        for widget, key in self._texts:
            widget.setText(self.t(key))
        for widget, key in ((self.settings_button, "desktop.settings.open"), (self.compact_button, "desktop.compact.on"),
                            (self.mini.expand_button, "desktop.compact.off"), (self.refresh_button, "desktop.refresh")):
            widget.setToolTip(self.t(key))
            widget.setAccessibleName(self.t(key))
        self.settings.retranslate(self.t)

    def _render_icon(self, _hub: str | None) -> None:
        # The taskbar icon is a dot in the Hub's tone, so the state is visible without opening the window.
        tone = self.snapshot.tone if self.snapshot else "wait"
        key = (self.theme_name, tone)
        if key not in self._icons:
            self._icons[key] = dot_icon(theme.tone_color(theme.PALETTES[self.theme_name], tone))
        self.setWindowIcon(self._icons[key])

    def _render_hub(self, hub: str | None) -> None:
        mode = ""
        if self.snapshot is None:
            self.hub_row.show_state("wait", self.t("desktop.checking"))
        else:
            self.hub_row.show_state(self.snapshot.tone, self.t.hub_label(self.snapshot.hub))
            if hub == "running":
                mode = self.t("desktop.mode.activityUi" if self.snapshot.activity_ui else "desktop.mode.fast")
        self.mode_label.setText(mode)
        self.mode_label.setVisible(bool(mode))

    def clear_rows(self, rows: QVBoxLayout) -> None:
        while rows.count():
            widget = rows.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _note(self, rows: QVBoxLayout, text: str) -> None:
        rows.addWidget(label(text, role="caption", wrap=True))

    def _render_children(self, hub: str | None) -> None:
        self.clear_rows(self.children_list)
        self.child_rows = {}
        self.bitnet_row = None
        self.processing_log = None
        # New rows have empty usage lines: draw them again in this render.
        self._keys.pop("usage", None)
        self.children_count.setText("")
        if hub != "running":
            self._note(self.children_list, self.t("desktop.waitingForHub"))
            return
        children = self.snapshot.children
        if not children:
            self._note(self.children_list, self.t("desktop.children.empty"))
        else:
            self.children_count.setText(f"{sum(child.tone == 'ok' for child in children)}/{len(children)}")
        for child in children:
            item = UsageRow()
            # A connected child needs no word: its green dot and the count say it. Other states keep theirs.
            if child.incomplete:
                detail = self.t("connection.configIncomplete")
            else:
                detail = "" if child.tone == "ok" else self.t.state_label(child.state)
            item.row.show_state(child.tone, child.server_id, detail)
            if child.incomplete:
                item.row.set_action(lambda chosen=child.server_id: self.show_config(chosen))
            self.children_list.addWidget(item)
            self.child_rows[child.server_id] = item
        self._render_bitnet_row(self.snapshot.bitnet)

    def _render_approvals(self, hub: str | None) -> None:
        self.clear_rows(self.approvals_list)
        pending = self.snapshot.pending_approvals if (hub == "running" and self.snapshot) else ()
        self.approvals_card.setVisible(bool(pending) and not self.compact)
        self.approvals_count.setText(str(len(pending)) if pending else "")
        for item in pending:
            box = QWidget()
            row = QHBoxLayout(box)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(theme.SPACE_XS)
            detail = f"{item.tool_name} ({', '.join(item.argument_keys)})" if item.argument_keys else item.tool_name
            row.addWidget(label(detail, role="caption", wrap=True), 1)
            approve = button(lambda jid=item.job_id: self.decide_approval(jid, True))
            approve.setText(self.t("desktop.approvals.approve"))
            reject = button(lambda jid=item.job_id: self.decide_approval(jid, False))
            reject.setText(self.t("desktop.approvals.reject"))
            row.addWidget(approve)
            row.addWidget(reject)
            self.approvals_list.addWidget(box)

    def _render_bitnet_row(self, info: status.BitnetInfo | None) -> None:
        """BitNet under the children, not counted with them: green only when llama-server really answers."""
        if info is None:  # a Hub too old to report BitNet
            return
        row = Row()
        detail = "" if info.tone == "ok" else self._bitnet_detail(info)
        row.show_state(info.tone, self.t("desktop.bitnet.name"), detail)
        self.children_list.addWidget(row)
        self.bitnet_row = row
        self.processing_log = ProcessingLog()
        self.children_list.addWidget(self.processing_log)
        self._render_processing()
        if info.installing:
            Spinner(row.glyph).start()

    def _bitnet_detail(self, info: status.BitnetInfo) -> str:
        if info.installing:
            return f"{info.progress}%" if info.step in ("runtime", "model") and info.progress is not None \
                else self.t("desktop.bitnet.state.installing")
        if info.installed and not info.enabled:
            return self.t("desktop.bitnet.state.off")
        return self.t(f"desktop.bitnet.state.{info.state}")

    def _usage_lines(self, activity_ui: bool) -> tuple[tuple[str, usage.ChildUsage | None], ...]:
        if not activity_ui:
            return ()
        now = self.now()
        return tuple((server_id, self.usage_board.usage(server_id, now)) for server_id in self.child_rows)

    def _render_usage(self, _hub: str | None) -> None:
        """Set the line under each child; rows are never rebuilt for it, so calls never flicker the list."""
        lines = dict(self._usage_lines(bool(self.snapshot and self.snapshot.activity_ui)))
        for server_id, item in self.child_rows.items():
            line = lines.get(server_id)
            if line is None:
                item.show_usage("", "", "caption")
                continue
            clients = ", ".join(line.clients)
            when = self._when(line)
            text = f"{theme.USAGE_GLYPH} {self.t('desktop.usage.line', clients=clients, when=when)}"
            detail = self.t("desktop.usage.detail", clients=clients, tool=line.tool, when=when) if line.tool else ""
            item.show_usage(text, detail, "live" if line.kind == "active" else "caption")

    def _when(self, line: usage.ChildUsage) -> str:
        if line.kind == "active":
            return self.t("desktop.usage.now")
        if line.minutes_ago == 0:
            return self.t("desktop.usage.justNow")
        return self.t("desktop.usage.minutesAgo", count=line.minutes_ago)

    def _render_clients(self, hub: str | None) -> None:
        self.clear_rows(self.clients_list)
        self.client_rows = {}
        # New rows show their base detail: draw the usage details again in this render.
        self._keys.pop("client_usage", None)
        self.clients_count.setText("")
        if hub != "running":
            self._note(self.clients_list, self.t("desktop.waitingForHub"))
            return
        if not self.snapshot.activity_ui:
            self._note(self.clients_list, self.t("desktop.clients.hint"))
            return
        if not self.snapshot.clients and not self.snapshot.recent:
            self._note(self.clients_list, self.t("clients.empty"))
            return
        self.clients_count.setText(str(len(self.snapshot.clients)))
        live = self.snapshot.clients
        names = self.snapshot.client_names if len(self.snapshot.client_names) == len(live) else live
        for name, text in zip(names, live, strict=True):
            self._client_row(name, "ok", text, "")
        for name in self.snapshot.recent:
            self._client_row(name, "off", name, self.t("clients.recent"))

    def _client_row(self, name: str, tone: str, text: str, base_detail: str) -> None:
        row = Row()
        row.show_state(tone, text, base_detail)
        self.clients_list.addWidget(row)
        self.client_rows[name] = (row, base_detail)

    def _client_usage_key(self, activity_ui: bool) -> tuple[tuple[str, tuple[str, tuple[str, ...]]], ...]:
        if not activity_ui:
            return ()
        return tuple(sorted(self.usage_board.client_usage(self.now()).items()))

    def _render_client_usage(self, _hub: str | None) -> None:
        """Show on each AI client row the children it uses now (blue) or used a moment ago; rows stay as they are."""
        uses = dict(self._client_usage_key(bool(self.snapshot and self.snapshot.activity_ui)))
        width = (theme.WINDOW_WIDTH - 2 * theme.SPACE_M) // 2
        for name, (row, base_detail) in self.client_rows.items():
            kind, children = uses.get(name, ("", ()))
            set_props(row.detail, role="live" if kind == "active" else "caption")
            if children:
                show_elided(row.detail, ", ".join(children), width)
            else:
                row.detail.setText(base_detail)
                row.detail.setToolTip("")

    def _autostart_key(self) -> tuple[tuple[str, bool | None], ...] | None:
        return tuple(sorted(self.autostart.items())) if self.autostart is not None else None

    def _render_settings(self, hub: str | None) -> None:
        # Settings live in the Hub, so they can be read and changed only while it runs.
        settings = self.hub_settings if hub == "running" else None
        bitnet_state = self.snapshot.bitnet_state if self.snapshot else None
        bitnet_info = self.snapshot.bitnet if self.snapshot else None
        self.hub_page.sync(hub, settings, self.autostart, bitnet_state, bitnet_info)
        self.mcps_page.sync(settings, hub == "running")
        self.config_page.sync(settings, hub == "running", busy=frozenset(self.config_busy),
                              feedback=self.config_feedback, loaded_gaps=self._loaded_gaps())
        self.settings.show_pending(self.restart_needed and hub == "running")

    def _render_update(self, hub: str | None) -> None:
        release = self.update_release
        self.update_card.setVisible(not self.compact)
        self.update_card.sync(self.t, release.label if release else None, self.updater.current if self.updater else "",
                              self.update_state, self.update_status, hub == "running")

    def _render_actions(self, hub: str | None) -> None:
        running = hub == "running"
        self.start_button.setVisible(not running)
        self.start_button.setEnabled(hub == "stopped")

    def _render_footer(self) -> None:
        if self.message_key:
            state = (self.t(self.message_key), "error")
        elif self.snapshot is not None:
            state = (self.t("desktop.updated", time=self.snapshot.checked_at.strftime("%H:%M:%S")), "caption")
        else:
            state = ("", "caption")
        if state != self._footer_state:
            self.footer.setText(state[0])
            set_props(self.footer, role=state[1])
            self._footer_state = state
