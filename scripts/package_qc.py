"""Run the repeatable offline QC checks for a locked mcp-hub source checkout."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


HUB_ROOT = Path(__file__).resolve().parents[1]
OFFLINE_CHECKS = (
    ("unit tests", ("-m", "unittest", "discover", "tests")),
    ("leak check", ("scripts/check_leaks.py",)),
    ("code standards", ("scripts/check_code_standards.py",)),
    ("compile check", ("-m", "compileall", "-q", "src", "scripts", "tests")),
)


def checks(live_smoke: bool) -> tuple[tuple[str, tuple[str, ...]], ...]:
    result = list(OFFLINE_CHECKS)
    if live_smoke:
        result.append(("live smoke", ("scripts/smoke.py",)))
    return tuple(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-smoke",
        action="store_true",
        help="also connect to enabled child MCPs and their upstream services",
    )
    args = parser.parse_args(argv)

    for label, command in checks(args.live_smoke):
        print(f"[QC] {label}: running")
        try:
            result = subprocess.run([sys.executable, *command], cwd=HUB_ROOT, check=False)
        except OSError as exc:
            print(f"[QC] {label}: could not start ({type(exc).__name__})", file=sys.stderr)
            return 1
        if result.returncode:
            print(f"[QC] {label}: failed (exit {result.returncode})", file=sys.stderr)
            return result.returncode
        print(f"[QC] {label}: passed")

    if not args.live_smoke:
        print("[QC] live smoke: skipped (pass --live-smoke when child credentials are configured)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
