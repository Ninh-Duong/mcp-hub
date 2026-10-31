"""Run `scripts/hubctl.ps1` in the background; shared by the dashboard controls and the desktop status app."""
from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from .config import HUB_ROOT

HUBCTL_SCRIPT = HUB_ROOT / "scripts" / "hubctl.ps1"
HUBCTL_TIMEOUT_SECONDS = 60
# `hubctl autostart -Target <name>`: the Hub service task and the desktop status app task.
AUTOSTART_TARGETS = ("hub", "app")


def windows_powershell() -> str:
    """Windows PowerShell by absolute path, so a `powershell.exe` in the working directory or on PATH never runs."""
    system_root = os.environ.get("SystemRoot")
    if not system_root:
        raise OSError("SystemRoot is not set; service control requires Windows")
    return str(Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def _hubctl_command(arguments: list[str]) -> list[str]:
    return [
        windows_powershell(), "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
        "-ExecutionPolicy", "Bypass", "-File", str(HUBCTL_SCRIPT), *arguments,
    ]


def launch_hubctl(arguments: list[str]) -> None:
    """Run `hubctl.ps1 <command> [flags]` detached, without a window. Raises OSError off Windows."""
    subprocess.Popen(
        _hubctl_command(arguments),
        cwd=HUB_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def run_hubctl(arguments: list[str], timeout: float = HUBCTL_TIMEOUT_SECONDS) -> str:
    """Run `hubctl.ps1` without a window and return its output. Raises OSError on failure or timeout."""
    try:
        result = subprocess.run(
            _hubctl_command(arguments),
            cwd=HUB_ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise OSError("hubctl did not finish in time") from exc
    if result.returncode != 0:
        raise OSError(f"hubctl exited with code {result.returncode}")
    return result.stdout


def autostart_state(run: Callable[[list[str]], str] = run_hubctl) -> dict[str, bool]:
    """Whether the Hub (`hub`) and the desktop status app (`app`) start at sign-in."""
    return _parse_autostart(run(["autostart"]))


def set_autostart(target: str, enabled: bool, run: Callable[[list[str]], str] = run_hubctl) -> dict[str, bool]:
    if target not in AUTOSTART_TARGETS:
        raise ValueError(f"Unknown autostart target: {target}")
    return _parse_autostart(run(["autostart", "-Target", target, "-State", "on" if enabled else "off"]))


def _parse_autostart(output: str) -> dict[str, bool]:
    lines = [line for line in output.splitlines() if line.strip()]
    try:
        value = json.loads(lines[-1])
    except (IndexError, ValueError) as exc:
        raise OSError("hubctl autostart returned no state") from exc
    if not isinstance(value, dict) or not all(isinstance(value.get(target), bool) for target in AUTOSTART_TARGETS):
        raise OSError("hubctl autostart returned an unexpected state")
    return {target: value[target] for target in AUTOSTART_TARGETS}
