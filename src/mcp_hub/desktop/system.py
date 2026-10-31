"""What the app does on Windows itself: start the Hub's sign-in task, and its own sign-in entry.

Starting the Hub runs the scheduled task `hubctl install` registered, exactly as a sign-in does (through the
ScheduledTasks cmdlets, which report the task state without localized text); the app never runs Hub scripts or
reads Hub files. The app's own sign-in entry is the `mcp-hub-status` value
under the user's Run key, which the installer also writes and its uninstaller removes. Running from
source there is no installed exe to start, so that switch is unavailable.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from .constants import HUB_TASK, RUN_VALUE

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# A stopping Hub closes its port at once but its task runs on while it drains (up to 20 s); the task ignores a new
# run until then, so the start waits for that instance to end first.
DRAIN_WAIT_SECONDS = 40
START_TIMEOUT_SECONDS = DRAIN_WAIT_SECONDS + 15
START_SCRIPT = (
    "$ErrorActionPreference = 'Stop'; "
    f"$deadline = (Get-Date).AddSeconds({DRAIN_WAIT_SECONDS}); "
    f"while ((Get-ScheduledTask -TaskName '{HUB_TASK}').State -eq 'Running' -and (Get-Date) -lt $deadline) "
    "{ Start-Sleep -Milliseconds 250 }; "
    f"Start-ScheduledTask -TaskName '{HUB_TASK}'"
)


def start_hub(run: Callable[..., Any] = subprocess.run) -> None:
    """Run the Hub's scheduled task without a window, after a stopping instance ends. Raises OSError on failure."""
    system_root = os.environ.get("SystemRoot")
    if not system_root:
        raise OSError("Starting the Hub requires Windows")
    powershell = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    command = [str(powershell), "-NoProfile", "-NonInteractive", "-Command", START_SCRIPT]
    try:
        result = run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=START_TIMEOUT_SECONDS,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise OSError("The Hub task did not answer in time") from exc
    if result.returncode != 0:
        raise OSError("The Hub task is missing; run hubctl install")


def installed_exe() -> str | None:
    """The installed app's exe, or None when the app runs from source."""
    return sys.executable if getattr(sys, "frozen", False) else None


def _registry() -> Any:
    import winreg  # Windows only; imported when the switch is used

    return winreg


def app_autostart(*, exe: str | None = None, registry: Any = None) -> bool | None:
    """Whether the installed app opens at sign-in; None when running from source."""
    exe = exe or installed_exe()
    if exe is None:
        return None
    registry = registry or _registry()
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, RUN_KEY) as key:
            registry.QueryValueEx(key, RUN_VALUE)
    except FileNotFoundError:
        return False
    return True


def set_app_autostart(enabled: bool, *, exe: str | None = None, registry: Any = None) -> bool:
    exe = exe or installed_exe()
    if exe is None:
        raise OSError("Only the installed app can open at sign-in")
    registry = registry or _registry()
    with registry.CreateKey(registry.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            registry.SetValueEx(key, RUN_VALUE, 0, registry.REG_SZ, f'"{exe}"')
        else:
            try:
                registry.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass
    return enabled
