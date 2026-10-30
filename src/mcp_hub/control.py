"""Run `scripts/hubctl.ps1` in the background; shared by the dashboard controls and the desktop status app."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .config import HUB_ROOT

HUBCTL_SCRIPT = HUB_ROOT / "scripts" / "hubctl.ps1"


def windows_powershell() -> str:
    """Windows PowerShell by absolute path, so a `powershell.exe` in the working directory or on PATH never runs."""
    system_root = os.environ.get("SystemRoot")
    if not system_root:
        raise OSError("SystemRoot is not set; service control requires Windows")
    return str(Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def launch_hubctl(arguments: list[str]) -> None:
    """Run `hubctl.ps1 <command> [flags]` detached, without a window. Raises OSError off Windows."""
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [
            windows_powershell(), "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
            "-ExecutionPolicy", "Bypass", "-File", str(HUBCTL_SCRIPT), *arguments,
        ],
        cwd=HUB_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creation_flags,
    )
