"""Fail if committable files break the code standards in AGENTS.md.

Checks: English-only text (UI translations live only in dashboard locale files),
functions of at most MAX_FUNCTION_LINES lines, and a unit test importing every module.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

MAX_FUNCTION_LINES = 50
# Letters used by Vietnamese but not by English or common Western European words.
_VIETNAMESE_RE = re.compile(
    "[\u0103\u0102\u0111\u0110\u01a1\u01a0\u01b0\u01af\u0129\u0128\u0169\u0168"
    "\u1ea0-\u1ef9]"
)
_JS_FUNCTION_RE = re.compile(r"^(?:export\s+)?(?:async\s+)?function\s*\*?\s*(\w+)")
LANGUAGE_ALLOWLIST = {"src/mcp_hub/dashboard/locales/vi.json"}
FUNCTION_ROOTS = ("src/", "scripts/")
MODULE_ROOT = PurePosixPath("src")
UNTESTABLE_MODULES = {"__init__", "__main__"}


def non_english_lines(lines: list[str]) -> list[int]:
    return [number for number, line in enumerate(lines, 1) if _VIETNAMESE_RE.search(line)]


def long_python_functions(source: str, limit: int = MAX_FUNCTION_LINES) -> list[tuple[int, str, int]]:
    long: list[tuple[int, str, int]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            length = node.end_lineno - node.lineno + 1
            if length > limit:
                long.append((node.lineno, node.name, length))
    return sorted(long)


def long_js_functions(lines: list[str], limit: int = MAX_FUNCTION_LINES) -> list[tuple[int, str, int]]:
    """Measure top-level function declarations: from the declaration to the next column-0 `}`."""
    long: list[tuple[int, str, int]] = []
    start: tuple[int, str] | None = None
    for number, line in enumerate(lines, 1):
        match = _JS_FUNCTION_RE.match(line)
        if match and start is None and not line.rstrip().endswith("}"):
            start = (number, match.group(1))
        elif start is not None and line.rstrip() == "}":
            length = number - start[0] + 1
            if length > limit:
                long.append((start[0], start[1], length))
            start = None
    return long


def module_name(path: str) -> str | None:
    relative = PurePosixPath(path).relative_to(MODULE_ROOT)
    if relative.stem in UNTESTABLE_MODULES:
        return None
    return ".".join(relative.with_suffix("").parts)


def imported_modules(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _tracked_files() -> list[str]:
    git = ["git", "ls-files", "--cached", "--others", "--exclude-standard"]
    files = subprocess.run(git, capture_output=True, text=True, check=True).stdout.splitlines()
    return [path for path in files if Path(path).is_file()]


def _read_lines(path: str) -> list[str] | None:
    try:
        return Path(path).read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, OSError):
        return None


def _language_problems(files: list[str]) -> list[str]:
    problems: list[str] = []
    for path in files:
        lines = None if path in LANGUAGE_ALLOWLIST else _read_lines(path)
        for number in non_english_lines(lines or []):
            problems.append(f"{path}:{number}: non-English text (UI translations belong in dashboard/locales/)")
    return problems


def _function_problems(files: list[str]) -> list[str]:
    problems: list[str] = []
    for path in files:
        if not path.startswith(FUNCTION_ROOTS) or not path.endswith((".py", ".js")):
            continue
        lines = _read_lines(path) or []
        found = long_python_functions("\n".join(lines)) if path.endswith(".py") else long_js_functions(lines)
        problems.extend(
            f"{path}:{line}: function {name} has {length} lines (max {MAX_FUNCTION_LINES})"
            for line, name, length in found
        )
    return problems


def _test_coverage_problems(files: list[str]) -> list[str]:
    imported: set[str] = set()
    for path in files:
        if path.startswith("tests/") and path.endswith(".py"):
            imported |= imported_modules("\n".join(_read_lines(path) or []))
    modules = [path for path in files if path.startswith("src/mcp_hub/") and path.endswith(".py")]
    return [
        f"{path}: no unit test imports {name}"
        for path in modules
        if (name := module_name(path)) and name not in imported
    ]


def main() -> int:
    files = _tracked_files()
    problems = _language_problems(files) + _function_problems(files) + _test_coverage_problems(files)
    print("\n".join(problems) or "code_standards=PASS")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
