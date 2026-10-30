from __future__ import annotations

import subprocess
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    # Run the entry point exe so the background process is named mcp-hub-service.
    service_exe = repo_root / ".venv" / "Scripts" / "mcp-hub-service.exe"
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        [str(service_exe)],
        cwd=repo_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
