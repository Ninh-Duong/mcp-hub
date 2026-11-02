"""Keep the docs in step with the code: fail when one says something the other no longer has.

    uv run python scripts/check_docs.py

It checks:
- links and anchors between the repo's Markdown files;
- that the docs the Hub loads at runtime exist;
- the audit events against the table in docs/security-and-logging.md, both ways;
- the environment variables and HTTP routes against docs/reference.md, both ways;
- the hubctl commands against the runbook;
- the settings keys against docs/settings.md;
- the default children in config/hub.example.json: each has a repository address (what Setup clones) and a contract;
- a short list of retired wording.

It prints file and line or the missing name, and exits 1 on any problem.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "mcp_hub"
DOCS = ROOT / "docs"
REFERENCE = DOCS / "reference.md"
EVENTS_DOC = DOCS / "security-and-logging.md"
SETTINGS_DOC = DOCS / "settings.md"
RUNBOOK = DOCS / "operations" / "shared-agent-hub.md"
HUBCTL = ROOT / "scripts" / "hubctl.ps1"
# Wording of features that are gone; docs must not describe them again.
RETIRED = (
    "-Target hub|app", "discards child stderr", "mcp-hub-status-setup.exe", "GLYPH_COLUMN_CHARS", "Get-ServiceToken",
    "start.ps1 -Service", "same bearer token",
)
_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_ENV = re.compile(r"\bMCP_HUB_[A-Z0-9_]*[A-Z0-9]\b")
_EVENT = re.compile(r"^[a-z_]+(?:\.[a-z_]+)+$")


def markdown_files() -> list[Path]:
    return sorted([ROOT / "README.md", ROOT / "AGENTS.md", *DOCS.rglob("*.md")])


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"[`*_]", "", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    found: set[str] = set()
    in_code = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_code = not in_code
        elif not in_code and line.startswith("#"):
            found.add(slug(line.lstrip("#")))
    return found


def link_problems(files: Iterable[Path]) -> list[str]:
    problems = []
    for path in files:
        in_code = False
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            in_code = in_code != line.startswith("```")
            if in_code or line.startswith("```"):
                continue
            for target in _LINK.findall(line):
                problem = _link_problem(path, target)
                if problem:
                    problems.append(f"{path.relative_to(ROOT).as_posix()}:{number}: {problem}")
    return problems


def _link_problem(path: Path, target: str) -> str | None:
    if re.match(r"^[a-z]+:", target) or target.startswith("<") or target.startswith("../../releases"):
        return None  # external links, placeholders, and GitHub's Releases page relative to the repository
    file_part, _, anchor = target.partition("#")
    linked = (path.parent / file_part).resolve() if file_part else path
    if not linked.exists():
        return f"link to a missing file: {target}"
    if anchor and linked.suffix == ".md" and anchor not in anchors(linked):
        return f"link to a missing heading: {target}"
    return None


def runtime_doc_problems() -> list[str]:
    from mcp_hub.agents.config import AGENT_ROLES

    servers = json.loads((ROOT / "config" / "hub.example.json").read_text(encoding="utf-8"))["servers"]
    needed = [DOCS / "client-rules.md", *(DOCS / "mcps" / f"{sid}.md" for sid in servers),
              *(DOCS / "agents" / f"{role}.md" for role in AGENT_ROLES)]
    return [f"the Hub loads {path.relative_to(ROOT).as_posix()} at runtime, but it is missing"
            for path in needed if not path.is_file()]


def logged_events() -> set[str]:
    """Event names passed as a literal to `<log>.write(...)` or to `_complete_call(..., event, ...)`."""
    events = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, (ast.Attribute, ast.Name)):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            index = {"write": 0, "_complete_call": 3}.get(name)
            if index is not None and len(node.args) > index and isinstance(node.args[index], ast.Constant):
                value = node.args[index].value
                if isinstance(value, str) and _EVENT.match(value):
                    events.add(value)
    return events


def table_names(path: Path, column: int = 0) -> set[str]:
    """Backticked names in one column of every Markdown table row of `path`."""
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) > column and not set(cells[column]) <= {"-", " ", ":"}:
            names.update(re.findall(r"`([^`]+)`", cells[column]))
    return names


def event_problems() -> list[str]:
    documented = {name for name in table_names(EVENTS_DOC) if _EVENT.match(name)}
    source = "\n".join(path.read_text(encoding="utf-8") for path in SRC.rglob("*.py"))
    missing = sorted(logged_events() - documented)
    stale = sorted(name for name in documented if f'"{name}"' not in source)
    return ([f"audit event {name} is logged but not in {EVENTS_DOC.name}" for name in missing]
            + [f"audit event {name} is in {EVENTS_DOC.name} but never logged" for name in stale])


def env_problems() -> list[str]:
    used = set()
    for folder, pattern in ((SRC, "*.py"), (ROOT / "scripts", "*"), (ROOT / "packaging", "*"), (ROOT / "tests", "*.py")):
        for path in folder.rglob(pattern):
            if path.is_file() and path.suffix in {".py", ".ps1", ".sh", ".pyw", ".spec"}:
                used.update(_ENV.findall(path.read_text(encoding="utf-8")))
    used = {name for name in used if not name.startswith("MCP_HUB_TEST_")}  # test fixtures, not settings
    documented = {name for name in table_names(REFERENCE) if _ENV.fullmatch(name)}
    return ([f"environment variable {name} is used but not in docs/reference.md" for name in sorted(used - documented)]
            + [f"environment variable {name} is in docs/reference.md but unused" for name in sorted(documented - used)])


def served_routes() -> set[str]:
    from types import SimpleNamespace

    from mcp_hub.activity_ui.routes import ActivityRoutes
    from mcp_hub.dashboard.api import create_dashboard_routes
    from mcp_hub.dashboard.sessions import DashboardSessions

    runtime = SimpleNamespace(audit=None)
    routes = [*create_dashboard_routes(runtime, SimpleNamespace(), DashboardSessions()),
              *ActivityRoutes(runtime, None, None).routes()]
    return {"/health", "/mcp", *(re.sub(r"\{(\w+):\w+\}", r"{\1}", route.path) for route in routes)}


def route_problems() -> list[str]:
    documented = {name for name in table_names(REFERENCE, column=1) if name.startswith("/")}
    served = served_routes()
    return ([f"route {path} is served but not in docs/reference.md" for path in sorted(served - documented)]
            + [f"route {path} is in docs/reference.md but not served" for path in sorted(documented - served)])


def hubctl_problems() -> list[str]:
    script = HUBCTL.read_text(encoding="utf-8")
    commands = re.findall(r'"(\w+)"', re.search(r'ValidateSet\(([^)]*)\)\]\s*\[string\]\$Command', script).group(1))
    runbook = RUNBOOK.read_text(encoding="utf-8")
    return [f"hubctl {command} is not described in {RUNBOOK.name}"
            for command in commands if not re.search(rf"hubctl(?:\.ps1)?`? {command}\b", runbook)]


def settings_problems() -> list[str]:
    from dataclasses import fields

    from mcp_hub.settings import Settings

    rows = {(group, key) for group, key in re.findall(r"^\| `(\w+)` \| `(\w+)` \|",
                                                       SETTINGS_DOC.read_text(encoding="utf-8"), re.M)}
    camel = lambda name: re.sub(r"_([a-z])", lambda m: m.group(1).upper(), name)  # noqa: E731
    keys = {(camel(group.name), camel(item.name)) for group in fields(Settings) for item in fields(group.default_factory)}
    return ([f"setting settings.{g}.{k} is not in docs/settings.md" for g, k in sorted(keys - rows)]
            + [f"docs/settings.md lists settings.{g}.{k}, which does not exist" for g, k in sorted(rows - keys)])


def child_problems() -> list[str]:
    servers = json.loads((ROOT / "config" / "hub.example.json").read_text(encoding="utf-8"))["servers"]
    problems = []
    for server_id, server in servers.items():
        if not str(server.get("repository", "")).startswith("https://"):
            problems.append(f"default child {server_id} has no https `repository` in config/hub.example.json")
        if not (DOCS / "mcps" / f"{server_id}.md").is_file():
            problems.append(f"default child {server_id} has no contract docs/mcps/{server_id}.md")
    return problems


def retired_problems(files: Iterable[Path]) -> list[str]:
    problems = []
    for path in files:
        if "history" in path.relative_to(ROOT).parts:
            continue  # archived plans describe what was true then
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            problems.extend(f"{path.relative_to(ROOT).as_posix()}:{number}: retired wording '{term}'"
                            for term in RETIRED if term in line)
    return problems


def all_problems() -> list[str]:
    files = markdown_files()
    return [*link_problems(files), *runtime_doc_problems(), *event_problems(), *env_problems(), *route_problems(),
            *hubctl_problems(), *settings_problems(), *child_problems(), *retired_problems(files)]


def main() -> int:
    problems = all_problems()
    for problem in problems:
        print(problem)
    print("docs_check=" + ("FAIL" if problems else "PASS"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
