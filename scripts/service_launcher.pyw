from __future__ import annotations

import subprocess
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    venv_python = repo_root / ".venv" / "Scripts" / "python.exe"
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        [str(venv_python), "-c", "from mcp_hub.service import main; main()"],
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
