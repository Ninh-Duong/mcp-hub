"""`python -m mcp_hub.desktop --screenshot DIR`: render sample states and save one PNG each for UI review.

The window shows fixed sample data (never the live Hub), so a review never stops or restarts anything.
Capture reads the screen as DWM composes it, so the PNGs include the real backdrop blur (Windows only).
"""
from __future__ import annotations

import struct
import time
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import win32
from .status import ChildRow, HubSnapshot

SETTLE_SECONDS = 0.6
WINDOW_POSITION = "+80+80"
_SAMPLE_TIME = datetime(2026, 10, 31, 9, 30, 0)
_SAMPLE_CHILDREN = (
    ChildRow("mcp-server-logs", "connected", "ok"),
    ChildRow("mcp-jira-sync", "connected", "ok"),
    ChildRow("mcp-pr-companion", "reconnecting", "wait"),
    ChildRow("mcp-read-source", "disabled", "off"),
)
SAMPLES = {
    "running": HubSnapshot(hub="running", children=_SAMPLE_CHILDREN, checked_at=_SAMPLE_TIME),
    "ui": HubSnapshot(hub="running", activity_ui=True, children=_SAMPLE_CHILDREN,
                      clients=("claude-code ×2", "antigravity"), checked_at=_SAMPLE_TIME),
    "starting": HubSnapshot(hub="starting", checked_at=_SAMPLE_TIME),
    "stopped": HubSnapshot(hub="stopped", checked_at=_SAMPLE_TIME),
    "not_installed": HubSnapshot(hub="not_installed", checked_at=_SAMPLE_TIME),
    "unauthorized": HubSnapshot(hub="unauthorized", checked_at=_SAMPLE_TIME),
}
STATES = tuple(SAMPLES)


def encode_png(width: int, height: int, bgra: bytes) -> bytes:
    """An opaque RGBA PNG from top-down BGRA pixels (stdlib only)."""
    stride = width * 4
    raw = bytearray()
    for y in range(height):
        row = bgra[y * stride:(y + 1) * stride]
        rgba = bytearray(row)
        rgba[0::4], rgba[2::4], rgba[3::4] = row[2::4], row[0::4], b"\xff" * width
        raw += b"\x00" + rgba
    return b"\x89PNG\r\n\x1a\n" + b"".join((
        _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)),
        _chunk(b"IDAT", zlib.compress(bytes(raw), 9)),
        _chunk(b"IEND", b""),
    ))


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def capture_window_png(root: Any) -> bytes:
    hwnd = int(root.wm_frame(), 16)
    return encode_png(*win32.capture_screen(win32.window_rect(hwnd)))


def _settle(root: Any) -> None:
    """Let Tk draw and DWM compose the backdrop before the capture."""
    root.update()
    time.sleep(SETTLE_SECONDS)
    root.update()


def capture_all(
    window: Any,
    directory: Path,
    states: list[str],
    languages: list[str],
    themes: list[str],
    *,
    capture: Callable[[Any], bytes] = capture_window_png,
    settle: Callable[[Any], None] = _settle,
) -> list[Path]:
    """Save `<state>-<language>-<theme>.png` for every combination; returns the paths written."""
    directory.mkdir(parents=True, exist_ok=True)
    window.root.attributes("-topmost", True)
    window.root.geometry(WINDOW_POSITION)
    saved: list[Path] = []
    for theme_name in themes:
        window.set_theme(theme_name)
        for language in languages:
            window.t.set_language(language)
            for state in states:
                window.apply(SAMPLES[state])
                settle(window.root)
                path = directory / f"{state}-{language}-{theme_name}.png"
                path.write_bytes(capture(window.root))
                saved.append(path)
    return saved
