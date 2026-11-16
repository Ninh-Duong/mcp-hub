"""Import-direction rules for src/mcp_hub (data-model.md "Module Boundary"; research.md R10).

The check reads source with `ast`, so it imports nothing. Each violation is recorded as "importer -> target" (or
"importer -> target._name" for a private name). Entries in KNOWN_VIOLATIONS are the layering problems the refactor
still has to fix. The test fails for a new violation, and also for a listed one that no longer exists, so the list
shrinks as work lands. Phase 4 must leave KNOWN_VIOLATIONS empty.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "mcp_hub"
PACKAGE = "mcp_hub"

# Leaf modules import no other mcp_hub module (they are shared by every layer above them).
LEAVES = {"jsonio", "clients", "tool_meta", "progress", "textnorm"}
# The results layer and the planning layer. Names match both the current flat modules and the planned subpackages.
RESULTS = {"condense", "result_shape", "full_results", "result_cache", "results"}
PLANNING = {"policy", "playbooks", "plan_confirm", "plan_merge", "planning"}
# Core runtime modules: they must never import an extension (activity_ui, bitnet, jobs, agents).
CORE = {"server", "http", "extensions", "instructions", "batch", "tool_pins", "child_lifecycle"}
EXTENSIONS = {"activity_ui", "bitnet", "jobs", "agents"}
AGENTS = {"agents"}
JOBS = {"jobs"}
SERVER = {"server"}

KNOWN_VIOLATIONS: set[str] = set()


def module_name(path: Path) -> str:
    """Dotted name below mcp_hub: `jobs/runner.py` -> `jobs.runner`, `jobs/__init__.py` -> `jobs`, root -> ``."""
    relative = path.relative_to(SRC).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _package_parts(path: Path) -> list[str]:
    name = module_name(path)
    if path.name == "__init__.py":
        return name.split(".") if name else []
    return name.split(".")[:-1] if "." in name else []


def _resolve(node: ast.ImportFrom, package: list[str]) -> list[str]:
    """Absolute module names (below mcp_hub) that one `from ... import ...` statement refers to."""
    if node.level == 0:
        base = (node.module or "").removeprefix(PACKAGE + ".").removeprefix(PACKAGE)
        base_parts = [part for part in base.split(".") if part]
    else:
        anchor = package[: len(package) - (node.level - 1)] if node.level - 1 <= len(package) else []
        base_parts = anchor + ([node.module] if node.module else [])
    if node.module is None and node.level:
        return [".".join([*base_parts, alias.name]) for alias in node.names]
    return [".".join(base_parts)]


def imports_of(path: Path) -> list[tuple[str, str | None]]:
    """(target module, private name or None) for every import in `path`, including lazy imports inside functions."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = _package_parts(path)
    found: list[tuple[str, str | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == PACKAGE or alias.name.startswith(PACKAGE + "."):
                    found.append((alias.name.removeprefix(PACKAGE).lstrip("."), None))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and not (node.module or "").startswith(PACKAGE):
                continue
            for target in _resolve(node, package):
                for alias in node.names:
                    if alias.name.startswith("_") and not alias.name.startswith("__"):
                        found.append((target, alias.name))
                found.append((target, None))
    return found


def top_level(module: str) -> str:
    return module.split(".")[0] if module else ""


def violations_for(importer: str, target: str, private: str | None) -> list[str]:
    """Every rule that the edge `importer -> target` breaks, as the strings used in KNOWN_VIOLATIONS."""
    found: list[str] = []
    head = top_level(importer)
    tail = top_level(target)
    if importer == "bridge" and tail == "server":
        found.append("bridge -> server")
    if head in JOBS | AGENTS and tail in SERVER:
        found.append(f"{head} -> server")
    if head in RESULTS and (tail in PLANNING | AGENTS | SERVER):
        found.append(f"{head} -> {tail}")
    if head in PLANNING and tail in AGENTS | SERVER:
        found.append(f"{head} -> {tail}")
    if head in CORE and tail in EXTENSIONS:
        found.append(f"{head} -> {tail}")
    if head in LEAVES and target:
        found.append(f"{head} -> {target}")
    if head == "desktop" and target and tail != "desktop":
        found.append(f"desktop -> {target}")
    if private and target and target != importer:
        found.append(f"{importer} -> {target}.{private}")
    return found


def collect_violations() -> set[str]:
    found: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        importer = module_name(path)
        if importer.endswith("__pycache__") or "__pycache__" in path.parts:
            continue
        for target, private in imports_of(path):
            if target == importer and private is None:
                continue
            found.update(violations_for(importer, target, private))
    return found


class ModuleBoundaryTest(unittest.TestCase):
    def test_no_new_layering_violation(self) -> None:
        new = collect_violations() - KNOWN_VIOLATIONS
        self.assertEqual(sorted(new), [], "new import-direction violations (see data-model.md)")

    def test_known_violations_are_still_present_until_fixed(self) -> None:
        fixed = KNOWN_VIOLATIONS - collect_violations()
        self.assertEqual(sorted(fixed), [], "remove these from KNOWN_VIOLATIONS: they are fixed")


if __name__ == "__main__":
    unittest.main()
