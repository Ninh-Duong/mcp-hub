"""The desktop status window (view only). Logic lives in status.py, text in the locales, tokens in theme.py.

Network work runs on daemon threads that hand results to the Tk thread through a queue, so a slow
or stopped Hub never freezes the window. Rendering is incremental: a poll that changes nothing
redraws nothing, so the window never flickers while it refreshes.
"""
from __future__ import annotations

import argparse
import queue
import threading
import time
import tkinter as tk
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, NamedTuple

from .. import control
from . import prefs, screenshot, status, theme, win32
from .i18n import LANGUAGES, Translator, system_language
from .widgets import Card, dot_icon


# Never equal to a section key, so every section draws once after a (re)build.
_NOT_DRAWN = object()


class Row(NamedTuple):
    frame: tk.Frame
    glyph: tk.Label
    text: tk.Label
    detail: tk.Label


class StatusWindow:
    def __init__(
        self,
        root: tk.Tk,
        translator: Translator,
        *,
        theme_name: str = "light",
        prefs_path: Path | None = None,
        fetch: Callable[[], status.HubSnapshot] = status.fetch_snapshot,
        open_dashboard: Callable[[], None] = status.open_dashboard,
        launch: Callable[[list[str]], None] = control.launch_hubctl,
        clock: Callable[[], float] = time.monotonic,
        style: Callable[[int, bool], bool] = win32.style_window,
    ) -> None:
        self.root, self.t = root, translator
        self.fetch, self.open_dashboard_action, self.launch, self.clock = fetch, open_dashboard, launch, clock
        self.style, self.prefs_path = style, prefs_path
        self.theme_name = theme_name if theme_name in theme.THEMES else "light"
        self.results: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.snapshot: status.HubSnapshot | None = None
        self.message_key: str | None = None
        self._polling = False
        self._starting_until = 0.0
        self.glass = False
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        self.root.title("mcp-hub")
        self.root.resizable(False, False)
        self._build()
        self.render()

    @property
    def colors(self) -> dict[str, str]:
        return theme.PALETTES[self.theme_name]

    def px(self, value: float) -> int:
        """A 96-DPI pixel value on this screen."""
        return round(value * self.scale)

    # Layout

    def _build(self) -> None:
        self._texts: list[tuple[tk.Widget, str]] = []
        self._keys: dict[str, object] = {}
        self._footer_state: tuple[str, str] | None = None
        self._icons: dict[str, tk.PhotoImage] = {}
        self.cards: list[Card] = []
        self._apply_window_style()
        self.root.configure(bg=self.behind, padx=self.px(theme.SPACE_S), pady=0)
        # Each card keeps a gap below it; this spacer gives the same gap above the first card.
        tk.Frame(self.root, height=self.px(theme.SPACE_S), bg=self.behind).pack()
        self._build_hub_card()
        self.children_count, self.children_list = self._list_card("desktop.section.children")
        self.clients_count, self.clients_list = self._list_card("desktop.section.clients")
        self._build_action_card()

    def _apply_window_style(self) -> None:
        """Title bar theme, rounded corners, and (Windows 11) the blurred backdrop behind the cards."""
        self.root.update_idletasks()
        try:
            hwnd = int(self.root.wm_frame(), 16)
        except (ValueError, tk.TclError):
            hwnd = 0
        if hwnd and self.style(hwnd, self.theme_name == "dark") and not self.glass:
            self.root.attributes("-transparentcolor", theme.GLASS_KEY)
            self.glass = True
        self.behind = theme.GLASS_KEY if self.glass else self.colors["canvas"]

    def _card(self) -> tk.Frame:
        card = Card(
            self.root, width=self.px(theme.WINDOW_WIDTH), pad_x=self.px(theme.SPACE_M), pad_y=self.px(theme.SPACE_M),
            gap=self.px(theme.SPACE_S), radius=self.px(theme.CARD_RADIUS),
            fill=self.colors["surface"], outline=self.colors["line"], behind=self.behind,
        )
        self.cards.append(card)
        return card.body

    def _build_hub_card(self) -> None:
        self.hub_row = self._row(self._card(), theme.FONT_HEADLINE)
        # The mode follows the state word; the theme and language buttons take the right edge.
        self.hub_row.detail.pack_configure(side="left", padx=(self.px(theme.SPACE_S), 0))
        self.language_button = self._small_button(self.hub_row.frame, self.toggle_language)
        self.theme_button = self._small_button(self.hub_row.frame, self.toggle_theme)

    def _list_card(self, title_key: str) -> tuple[tk.Label, tk.Frame]:
        body = self._card()
        header = tk.Frame(body, bg=self.colors["surface"])
        header.pack(fill="x", pady=(0, self.px(theme.SPACE_XS)))
        title = self._label(header, theme.FONT_SECTION, "muted")
        title.pack(side="left")
        self._texts.append((title, title_key))
        count = self._label(header, theme.FONT_SECTION, "muted")
        count.pack(side="right")
        rows = tk.Frame(body, bg=self.colors["surface"])
        rows.pack(fill="x")
        return count, rows

    def _build_action_card(self) -> None:
        body = self._card()
        buttons = tk.Frame(body, bg=self.colors["surface"])
        buttons.pack(fill="x")
        self.open_button = self._button(buttons, self.open_dashboard)
        self.start_button = self._button(buttons, self.start_hub)
        self.refresh_button = self._small_button(buttons, self.refresh)
        self.refresh_button.configure(text=theme.REFRESH_GLYPH, font=theme.FONT_BODY)
        self._texts += [(self.open_button, "desktop.button.openDashboard"), (self.start_button, "desktop.button.startHub")]
        self.footer = self._label(body, theme.FONT_CAPTION, "muted")
        self.footer.pack(fill="x", pady=(self.px(theme.SPACE_S), 0))

    def _button(self, parent: tk.Widget, command: Callable[[], None]) -> tk.Button:
        button = tk.Button(
            parent, command=command, font=theme.FONT_BODY, relief="flat", bd=0, cursor="hand2",
            padx=self.px(theme.SPACE_M), pady=self.px(theme.SPACE_XS), disabledforeground=self.colors["muted"],
            **theme.secondary_button(self.colors),
        )
        button.pack(side="left", padx=(0, self.px(theme.SPACE_S)))
        return button

    def _small_button(self, parent: tk.Widget, command: Callable[[], None]) -> tk.Button:
        button = tk.Button(
            parent, command=command, font=theme.FONT_CAPTION, relief="flat", bd=0, cursor="hand2",
            padx=self.px(theme.SPACE_S), pady=0, **theme.secondary_button(self.colors),
        )
        button.pack(side="right", padx=(self.px(theme.SPACE_XS), 0))
        return button

    def _label(self, parent: tk.Widget, font: tuple, token: str) -> tk.Label:
        return tk.Label(parent, font=font, fg=self.colors[token], bg=self.colors["surface"], anchor="w", justify="left")

    def _row(self, parent: tk.Widget, font: tuple = theme.FONT_BODY) -> Row:
        frame = tk.Frame(parent, bg=self.colors["surface"])
        # Rows after the first get a small gap above them.
        frame.pack(fill="x", pady=(self.px(theme.SPACE_XS) if len(parent.winfo_children()) > 1 else 0, 0))
        glyph = self._label(frame, font, "muted")
        # A fixed glyph column: the four tone glyphs differ in width, the names must still align.
        glyph.configure(width=theme.GLYPH_COLUMN_CHARS, anchor="center")
        glyph.pack(side="left", padx=(0, self.px(theme.SPACE_XS)))
        text = self._label(frame, font, "ink")
        text.pack(side="left")
        detail = self._label(frame, theme.FONT_CAPTION, "muted")
        detail.pack(side="right")
        return Row(frame, glyph, text, detail)

    # Rendering

    def render(self) -> None:
        """Redraw only the sections whose content changed since the last render."""
        hub = self.snapshot.hub if self.snapshot else None
        changed = False
        for name, key, draw in self._sections(hub):
            if self._keys.get(name, _NOT_DRAWN) != key:
                draw(hub)
                self._keys[name] = key
                changed = True
        self._render_footer()
        if changed:
            self._fit()

    def _fit(self) -> None:
        """Resize cards to their new content, then the window to the cards."""
        self.root.update_idletasks()
        for card in self.cards:
            card.layout()
        self.root.geometry("")

    def _sections(self, hub: str | None) -> tuple[tuple[str, object, Callable[[str | None], None]], ...]:
        snapshot, language = self.snapshot, self.t.language
        activity_ui = bool(snapshot and snapshot.activity_ui)
        return (
            ("texts", language, self._render_texts),
            ("hub", (language, hub, activity_ui), self._render_hub),
            ("children", (language, hub, snapshot.children if snapshot else ()), self._render_children),
            ("clients", (language, hub, activity_ui, snapshot.clients if snapshot else ()), self._render_clients),
            ("actions", hub, self._render_actions),
            ("icon", snapshot.tone if snapshot else "wait", self._render_icon),
        )

    def _render_texts(self, _hub: str | None) -> None:
        for widget, key in self._texts:
            widget.configure(text=self.t(key))
        self.language_button.configure(text=self.t("desktop.language.switch"))
        self.theme_button.configure(text=theme.THEME_TOGGLE_GLYPHS[self.theme_name])

    def _render_icon(self, _hub: str | None) -> None:
        # The taskbar icon is a dot in the Hub's tone, so the state is visible without opening the window.
        tone = self.snapshot.tone if self.snapshot else "wait"
        if tone not in self._icons:
            self._icons[tone] = dot_icon(self.root, theme.tone_color(self.colors, tone), self.px(theme.ICON_SIZE))
        self.root.iconphoto(False, self._icons[tone])

    def _set_row(self, row: Row, tone: str, text: str, detail: str = "") -> None:
        row.glyph.configure(text=theme.TONE_GLYPHS[tone], fg=theme.tone_color(self.colors, tone))
        row.text.configure(text=text)
        row.detail.configure(text=detail)

    def _render_hub(self, hub: str | None) -> None:
        if self.snapshot is None:
            self._set_row(self.hub_row, "wait", self.t("desktop.checking"))
            return
        detail = ""
        if hub == "running":
            detail = self.t("desktop.mode.activityUi" if self.snapshot.activity_ui else "desktop.mode.fast")
        self._set_row(self.hub_row, self.snapshot.tone, self.t.hub_label(self.snapshot.hub), detail)

    def _reset(self, parent: tk.Frame) -> None:
        for widget in parent.winfo_children():
            widget.destroy()

    def _note(self, parent: tk.Frame, text: str) -> None:
        note = self._label(parent, theme.FONT_CAPTION, "muted")
        note.configure(text=text, wraplength=self.px(theme.WINDOW_WIDTH - 2 * theme.SPACE_M))
        note.pack(fill="x")

    def _render_children(self, hub: str | None) -> None:
        self._reset(self.children_list)
        self.children_count.configure(text="")
        if hub != "running":
            self._note(self.children_list, self.t("desktop.waitingForHub"))
            return
        children = self.snapshot.children
        if not children:
            self._note(self.children_list, self.t("desktop.children.empty"))
            return
        self.children_count.configure(text=f"{sum(child.tone == 'ok' for child in children)}/{len(children)}")
        for child in children:
            self._set_row(self._row(self.children_list), child.tone, child.server_id, self.t.state_label(child.state))

    def _render_clients(self, hub: str | None) -> None:
        self._reset(self.clients_list)
        self.clients_count.configure(text="")
        if hub != "running":
            self._note(self.clients_list, self.t("desktop.waitingForHub"))
            return
        if not self.snapshot.activity_ui:
            self._note(self.clients_list, self.t("desktop.clients.hint"))
            return
        if not self.snapshot.clients:
            self._note(self.clients_list, self.t("clients.empty"))
            return
        self.clients_count.configure(text=str(len(self.snapshot.clients)))
        for client in self.snapshot.clients:
            self._set_row(self._row(self.clients_list), "ok", client)

    def _render_actions(self, hub: str | None) -> None:
        # One primary action: Start Hub while the Hub is down, Open dashboard otherwise.
        primary, secondary = (self.open_button, self.start_button) if hub == "running" else (self.start_button, self.open_button)
        primary.configure(**theme.primary_button(self.colors))
        secondary.configure(**theme.secondary_button(self.colors))
        self.open_button.configure(state="normal" if hub == "running" else "disabled")
        self.start_button.configure(state="normal" if hub == "stopped" else "disabled")

    def _render_footer(self) -> None:
        if self.message_key:
            state = (self.t(self.message_key), theme.tone_color(self.colors, "error"))
        elif self.snapshot is not None:
            state = (self.t("desktop.updated", time=self.snapshot.checked_at.strftime("%H:%M:%S")), self.colors["muted"])
        else:
            state = ("", self.colors["muted"])
        if state != self._footer_state:
            self.footer.configure(text=state[0], fg=state[1], wraplength=self.px(theme.WINDOW_WIDTH - 2 * theme.SPACE_M))
            self._footer_state = state

    # Behavior

    def start(self) -> None:
        """Begin polling the Hub and draining worker results on the Tk thread."""
        self._poll_forever()
        self._drain()

    def _poll_forever(self) -> None:
        self.refresh()
        self.root.after(theme.POLL_INTERVAL_MS, self._poll_forever)

    def _drain(self) -> None:
        self.process_results()
        self.root.after(theme.QUEUE_CHECK_MS, self._drain)

    def refresh(self) -> None:
        if self._polling:
            return
        self._polling = True
        threading.Thread(target=self.poll_once, name="mcp-hub-status-poll", daemon=True).start()

    def poll_once(self) -> None:
        """Worker: read one snapshot (fetch never raises) and queue it for the Tk thread."""
        self.results.put(("snapshot", self.fetch()))

    def process_results(self) -> None:
        while True:
            try:
                kind, value = self.results.get_nowait()
            except queue.Empty:
                return
            if kind == "snapshot":
                self._polling = False
                self.apply(value)
            else:
                self.message_key = value
                self._render_footer()

    def apply(self, snapshot: status.HubSnapshot) -> None:
        if snapshot.hub == "stopped" and self.clock() < self._starting_until:
            snapshot = replace(snapshot, hub="starting")
        if snapshot.hub == "running":
            self._starting_until = 0.0
        self.snapshot, self.message_key = snapshot, None
        self.render()

    def open_dashboard(self) -> None:
        threading.Thread(target=self.open_dashboard_once, name="mcp-hub-status-open", daemon=True).start()

    def open_dashboard_once(self) -> None:
        try:
            self.open_dashboard_action()
        except status.DesktopError:
            self.results.put(("message", "desktop.error.dashboard"))

    def start_hub(self) -> None:
        try:
            self.launch(["start"])
        except OSError:
            self.message_key = "desktop.error.startUnavailable"
            self._render_footer()
            return
        self._starting_until = self.clock() + theme.START_GRACE_SECONDS
        self.apply(replace(self.snapshot or status.HubSnapshot(hub="stopped"), hub="starting"))

    def toggle_language(self) -> None:
        self.t.set_language(self.t.other_language())
        self.render()
        self._save_prefs()

    def set_theme(self, name: str) -> None:
        """Switch palettes by rebuilding the widgets; the snapshot and message stay."""
        if name == self.theme_name or name not in theme.THEMES:
            return
        self.theme_name = name
        for widget in self.root.winfo_children():
            widget.destroy()
        self._build()
        self.render()

    def toggle_theme(self) -> None:
        self.set_theme(theme.THEMES[(theme.THEMES.index(self.theme_name) + 1) % len(theme.THEMES)])
        self._save_prefs()

    def _save_prefs(self) -> None:
        prefs.save_prefs(self.prefs_path, {"theme": self.theme_name, "language": self.t.language})


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m mcp_hub.desktop", description="mcp-hub desktop status app")
    parser.add_argument("--screenshot", metavar="DIR", help="save sample-state PNGs for UI review, then exit")
    parser.add_argument("--states", default=",".join(screenshot.STATES), help="comma list: " + ",".join(screenshot.STATES))
    parser.add_argument("--lang", default=",".join(LANGUAGES), help="comma list: " + ",".join(LANGUAGES))
    parser.add_argument("--theme", default=",".join(theme.THEMES), help="comma list: " + ",".join(theme.THEMES))
    args = parser.parse_args(argv)
    for name, allowed in (("states", screenshot.STATES), ("lang", LANGUAGES), ("theme", theme.THEMES)):
        values = [value.strip() for value in getattr(args, name).split(",") if value.strip()]
        unknown = sorted(set(values) - set(allowed))
        if unknown or not values:
            parser.error(f"--{name}: unknown or empty value {', '.join(unknown)} (choose from {', '.join(allowed)})")
        setattr(args, name, values)
    return args


def _run_screenshots(root: tk.Tk, args: argparse.Namespace) -> int:
    window = StatusWindow(root, Translator("en"), fetch=lambda: status.HubSnapshot(hub="stopped"))
    try:
        saved = screenshot.capture_all(window, Path(args.screenshot), args.states, args.lang, args.theme)
    except OSError as exc:
        print(f"Screenshots need Windows ({type(exc).__name__})")
        return 1
    finally:
        root.destroy()
    print("\n".join(str(path) for path in saved))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.screenshot is None and win32.acquire_single_instance() is None:
        return 0
    win32.enable_dpi_awareness()
    try:
        root = tk.Tk()
    except tk.TclError:
        return 1
    if args.screenshot is not None:
        return _run_screenshots(root, args)
    saved = prefs.load_prefs(prefs.PREFS_PATH)
    translator = Translator(saved.get("language") or system_language())
    theme_name = saved.get("theme") or win32.system_theme()
    StatusWindow(root, translator, theme_name=theme_name, prefs_path=prefs.PREFS_PATH).start()
    root.mainloop()
    return 0
