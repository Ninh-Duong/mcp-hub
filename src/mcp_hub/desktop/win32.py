"""Windows integration of the desktop app: window styling (DWM), single instance, theme, screen capture.

Every function degrades to a harmless default off Windows or on older builds, so the app still runs.
"""
from __future__ import annotations

import ctypes
from collections.abc import Callable
from ctypes import wintypes
from typing import Any

MUTEX_NAME = "Local\\mcp-hub-status"
ERROR_ALREADY_EXISTS = 183
_PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"

# DwmSetWindowAttribute ids and values (Windows 11 22H2+ for the backdrop).
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWCP_ROUND = 2
DWMSBT_TRANSIENTWINDOW = 3  # acrylic: a blurred, tinted view of what is behind the window
SRCCOPY = 0x00CC0020


def acquire_single_instance(kernel32: Any = None, last_error: Callable[[], int] | None = None) -> Any:
    """A named mutex held for the app's lifetime; None when another copy already holds it."""
    if kernel32 is None:
        if not hasattr(ctypes, "WinDLL"):
            return True
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        last_error = ctypes.get_last_error
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return True
    return None if last_error() == ERROR_ALREADY_EXISTS else handle


def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def system_theme(read_value: Callable[[], int] | None = None) -> str:
    """`dark` when Windows apps use the dark theme, else `light` (also off Windows)."""
    if read_value is None:
        read_value = _apps_use_light_theme
    try:
        return "light" if read_value() else "dark"
    except OSError:
        return "light"


def _apps_use_light_theme() -> int:
    import winreg  # Windows only; an ImportError off Windows is reported as OSError below

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PERSONALIZE_KEY) as key:
        return int(winreg.QueryValueEx(key, "AppsUseLightTheme")[0])


def _dwm() -> Any:
    dwm = ctypes.WinDLL("dwmapi")
    dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    return dwm


def set_window_attribute(hwnd: int, attribute: int, value: int, dwm: Any = None) -> bool:
    try:
        dwm = dwm or _dwm()
    except (AttributeError, OSError):
        return False
    data = ctypes.c_int(value)
    return dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data)) == 0


def style_window(hwnd: int, dark: bool, set_attribute: Callable[[int, int, int], bool] = set_window_attribute) -> bool:
    """Dark or light title bar, rounded corners, and the acrylic backdrop. True when the backdrop is on."""
    set_attribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0)
    set_attribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND)
    return set_attribute(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_TRANSIENTWINDOW)


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """The visible window bounds on screen (without the invisible resize border)."""
    rect = wintypes.RECT()
    dwm = _dwm()
    if dwm.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
        ctypes.WinDLL("user32").GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


def _gdi() -> tuple[Any, Any]:
    user32, gdi32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
    handle = ctypes.c_void_p
    user32.GetDC.restype, user32.GetDC.argtypes = handle, [handle]
    user32.ReleaseDC.argtypes = [handle, handle]
    gdi32.CreateCompatibleDC.restype, gdi32.CreateCompatibleDC.argtypes = handle, [handle]
    gdi32.CreateCompatibleBitmap.restype = handle
    gdi32.CreateCompatibleBitmap.argtypes = [handle, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype, gdi32.SelectObject.argtypes = handle, [handle, handle]
    gdi32.BitBlt.argtypes = [handle, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, handle, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi32.GetDIBits.argtypes = [handle, handle, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [handle]
    gdi32.DeleteDC.argtypes = [handle]
    return user32, gdi32


def capture_screen(rect: tuple[int, int, int, int]) -> tuple[int, int, bytes]:
    """Top-down BGRA pixels of a screen area, as composed by DWM (so the backdrop blur is included)."""
    left, top, right, bottom = rect
    width, height = right - left, bottom - top
    user32, gdi32 = _gdi()
    screen = user32.GetDC(None)
    memory = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
    previous = gdi32.SelectObject(memory, bitmap)
    try:
        gdi32.BitBlt(memory, 0, 0, width, height, screen, left, top, SRCCOPY)
        header = _BitmapInfoHeader(ctypes.sizeof(_BitmapInfoHeader), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        pixels = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetDIBits(memory, bitmap, 0, height, pixels, ctypes.byref(header), 0)
        return width, height, pixels.raw
    finally:
        gdi32.SelectObject(memory, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(None, screen)
