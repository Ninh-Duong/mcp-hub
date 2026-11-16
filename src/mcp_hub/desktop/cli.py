"""Command line of the desktop status app: the window, and the screenshot mode used to build the documentation images."""

from __future__ import annotations

from .app import StatusWindow, application

import argparse
from pathlib import Path
from typing import Any
from PySide6.QtCore import QPoint
from PySide6.QtGui import QGuiApplication
from . import prefs, screenshot, status, theme, update, win32
from .i18n import LANGUAGES, Translator, system_language




def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m mcp_hub.desktop", description="mcp-hub desktop status app")
    parser.add_argument("--version", action="store_true", help="print the app's version, then exit")
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


def run_screenshots(args: argparse.Namespace) -> int:
    # Sample data only: a review never reads the live Hub, changes hub.json, or touches the sign-in tasks.
    window = StatusWindow(
        Translator("en"),
        fetch=lambda: status.HubSnapshot(hub="stopped"),
        launch=lambda _arguments: None,
        hub_store=screenshot.SampleSettings(),
        read_autostart=lambda: dict(screenshot.SAMPLE_AUTOSTART),
        write_autostart=lambda _target, _enabled: dict(screenshot.SAMPLE_AUTOSTART),
        run_worker=lambda job: job(),
        stream=lambda _put, _stop: None,
        processing_stream=lambda _put, _stop: None,
        updater=update.Updater(screenshot.SAMPLE_CURRENT, lambda: None),
    )
    try:
        saved = screenshot.capture_all(window, Path(args.screenshot), args.states, args.lang, args.theme)
    except OSError as exc:
        print(f"Screenshots could not be saved ({type(exc).__name__})")
        return 1
    finally:
        window.close()
    print("\n".join(str(path) for path in saved))
    return 0

def appearance_from(saved: dict[str, Any]) -> theme.Appearance:
    """The remembered look; without a saved theme, the Windows app theme."""
    return theme.Appearance(
        theme=saved.get("theme") or win32.system_theme(),
        glass=saved.get("glass", True),
        glass_style=saved.get("glass_style", "frosted"),
        card_opacity=saved.get("card_opacity"),
        tint=saved.get("tint"),
    )

def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.version:
        print(update.app_version() or "unknown")
        return 0
    guard = None if args.screenshot is not None else win32.acquire_single_instance()
    if args.screenshot is None and guard is None:
        return 0
    win32.set_app_id()
    app = application()
    if args.screenshot is not None:
        return run_screenshots(args)
    saved = prefs.load_prefs(prefs.PREFS_PATH)
    window = StatusWindow(
        Translator(saved.get("language") or system_language()),
        appearance=appearance_from(saved),
        prefs_path=prefs.PREFS_PATH,
        updater=update.default_updater(),
    )
    window.show()
    window.restore_compact(saved, lambda x, y: QGuiApplication.screenAt(QPoint(x, y)) is not None)
    window.start()
    return app.exec()
