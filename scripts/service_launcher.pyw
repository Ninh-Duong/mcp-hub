from __future__ import annotations

import os
import subprocess
from pathlib import Path

# The service's stderr (Python tracebacks, SDK warnings) goes to logs/mcp-hub-service.err.
# A file over this size is rotated to .err.1 at start, so it stays bounded across restarts.
SERVICE_ERR_MAX_BYTES = 5 * 1024 * 1024


def open_service_err(logs_dir: Path):
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / "mcp-hub-service.err"
    if path.is_file() and path.stat().st_size > SERVICE_ERR_MAX_BYTES:
        os.replace(path, path.with_name(path.name + ".1"))
    return open(path, "ab")


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    # Run the entry point exe so the background process is named mcp-hub-service.
    service_exe = repo_root / ".venv" / "Scripts" / "mcp-hub-service.exe"
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with open_service_err(repo_root / "logs") as service_err:
        result = subprocess.run(
            [str(service_exe)],
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
