from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

# The service's stderr (Python tracebacks, SDK warnings) goes to logs/mcp-hub-service.err.
# A file over this size is rotated to .err.1 at start, so it stays bounded across restarts.
SERVICE_ERR_MAX_BYTES = 5 * 1024 * 1024
# The sign-in task and a crash restart start the service from here, not through hubctl, so a `git pull` that changed
# uv.lock would leave a stale .venv and a child that cannot import its packages. `start.ps1 -Prepare`, the one sync
# path, writes this stamp (the SHA-256 of the uv.lock the .venv was synced from) after every successful sync.
LOCK_STAMP_NAME = ".mcp-hub-lock.sha256"
SYNC_TIMEOUT_SECONDS = 300


def open_service_err(logs_dir: Path):
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / "mcp-hub-service.err"
    if path.is_file() and path.stat().st_size > SERVICE_ERR_MAX_BYTES:
        os.replace(path, path.with_name(path.name + ".1"))
    return open(path, "ab")


def environment_stale(repo_root: Path) -> bool:
    """Whether uv.lock differs from the one the .venv was last synced from (no stamp counts as different)."""
    lock = repo_root / "uv.lock"
    if not lock.is_file():
        return False
    try:
        stamp = (repo_root / ".venv" / LOCK_STAMP_NAME).read_text(encoding="ascii").strip()
    except (OSError, ValueError):
        return True
    return stamp != hashlib.sha256(lock.read_bytes()).hexdigest()


def sync_environment(repo_root: Path, service_err, run=subprocess.run) -> bool:
    """Run the locked sync (`start.ps1 -Prepare`); False, with a line in the service log, when it did not finish."""
    system_root = os.environ.get("SystemRoot")
    if not system_root:
        return False
    powershell = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    command = [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
               str(repo_root / "scripts" / "start.ps1"), "-Prepare"]
    try:
        result = run(command, cwd=repo_root, stdin=subprocess.DEVNULL, stdout=service_err, stderr=service_err,
                     timeout=SYNC_TIMEOUT_SECONDS, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def main(repo_root: Path | None = None) -> int:
    repo_root = repo_root or Path(__file__).resolve().parent.parent
    # Run the entry point exe so the background process is named mcp-hub-service.
    service_exe = repo_root / ".venv" / "Scripts" / "mcp-hub-service.exe"
    python_exe = repo_root / ".venv" / "Scripts" / "python.exe"
    command = [str(service_exe)] if service_exe.is_file() else [str(python_exe), "-m", "mcp_hub.service"]
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with open_service_err(repo_root / "logs") as service_err:
        # Never block the Hub on this: a failed sync (offline, a file in use) leaves the current environment to run.
        if environment_stale(repo_root) and not sync_environment(repo_root, service_err):
            service_err.write(b"mcp-hub: the environment sync did not finish; starting with the current environment.\n")
            service_err.flush()
        result = subprocess.run(
            command,
            cwd=repo_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=service_err,
            creationflags=creation_flags,
            check=False,
        )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
