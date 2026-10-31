"""Fail if committable files contain machine paths, real emails, or private files."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import PurePosixPath

_PATH_RE = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]|/Users/|/home/")
_EMAIL_RE = re.compile(r"[\w.%+-]+@(?P<domain>[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,})", re.IGNORECASE)
SKIP = {"scripts/check_leaks.py"}


def _is_private_path(path: str) -> bool:
    parts = [part.casefold() for part in PurePosixPath(path.replace("\\", "/")).parts]
    return (
        ".local" in parts
        or "logs" in parts
        or any(part == ".env" or part.startswith(".env.") for part in parts)
        or any(part == "config" and index + 1 < len(parts) and parts[index + 1] == "private" for index, part in enumerate(parts))
    )


def _has_non_example_email(line: str) -> bool:
    return any(match.group("domain").casefold() != "example.com" for match in _EMAIL_RE.finditer(line))


def main() -> int:
    git = ["git", "ls-files", "--cached", "--others", "--exclude-standard"]
    files = subprocess.run(git, capture_output=True, text=True, check=True).stdout.splitlines()
    problems = [f"{path}: private file is tracked" for path in files if _is_private_path(path)]
    for path in files:
        if path in SKIP:
            continue
        try:
            with open(path, encoding="utf-8") as source:
                lines = source.read().splitlines()
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for number, line in enumerate(lines, 1):
            if _PATH_RE.search(line):
                problems.append(f"{path}:{number}: absolute path")
            if _has_non_example_email(line):
                problems.append(f"{path}:{number}: email")
    print("\n".join(problems) or "leak_check=PASS")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
