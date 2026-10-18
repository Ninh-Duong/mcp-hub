"""Fail if committable files contain machine paths, real emails, or private files."""
from __future__ import annotations

import re
import subprocess
import sys

PATTERNS = {
    "absolute path": re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]|/Users/|/home/"),
    "email": re.compile(r"[\w.%+-]+@(?!example\.com\b)[\w-]+(\.[\w-]+)*\.[A-Za-z]{2,}"),
}
FORBIDDEN_PREFIXES = ("config/private/", ".local/", "logs/", ".env")
SKIP = {"uv.lock", "scripts/check_leaks.py"}


def main() -> int:
    git = ["git", "ls-files", "--cached", "--others", "--exclude-standard"]
    files = subprocess.run(git, capture_output=True, text=True, check=True).stdout.splitlines()
    problems = [f"{path}: private file is tracked" for path in files if path.startswith(FORBIDDEN_PREFIXES)]
    for path in files:
        if path in SKIP:
            continue
        try:
            lines = open(path, encoding="utf-8").read().splitlines()
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for number, line in enumerate(lines, 1):
            for label, pattern in PATTERNS.items():
                if pattern.search(line):
                    problems.append(f"{path}:{number}: {label}")
    print("\n".join(problems) or "leak_check=PASS")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
