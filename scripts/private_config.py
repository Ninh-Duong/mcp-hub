"""Inspect or edit config/private/mcps/<id>.json without printing secret values.

  uv run python scripts/private_config.py keys <id>
  uv run python scripts/private_config.py set <id> <dotted.key> <json-value>
  uv run python scripts/private_config.py project-status
"""
from __future__ import annotations

import json
import sys
from typing import Any

from mcp_hub.config import _SENSITIVE_KEY_RE, PRIVATE_ROOT, SECRET_PLACEHOLDER

SHOWN = {"transport", "command", "cwd", "inheritEnv", "timeoutSeconds"}


def _walk(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(child, f"{prefix}{key}.")
    else:
        yield prefix[:-1], value


def main(argv: list[str]) -> None:
    if argv == ["agent-status"]:
        from mcp_hub.agents.config import load_agent_configs

        configs = load_agent_configs(path=PRIVATE_ROOT / "agents.json", private_root=PRIVATE_ROOT)
        for role, config in configs.items():
            provider = config.provider or "<unset>"
            model = config.model or "<unset>"
            print(f"{role}: provider={provider}, model={model}, state={'configured' if config.configured else 'not configured'}")
        return
    if argv == ["project-status"]:
        from mcp_hub.jobs.projects import load_projects

        projects = load_projects(path=PRIVATE_ROOT / "projects.json", private_root=PRIVATE_ROOT)
        if not projects:
            print("No projects registered.")
            return
        for alias, project in projects.items():
            default = project.default_env or "<ask per job>"
            print(f"{alias}: default_env={default}, allowed_envs={','.join(project.allowed_envs)}")
        return
    if len(argv) < 2 or argv[0] not in {"keys", "set"} or (argv[0] == "set" and len(argv) != 4):
        raise SystemExit(__doc__)
    path = (PRIVATE_ROOT / "mcps" / f"{argv[1]}.json").resolve()
    if path.parent != (PRIVATE_ROOT / "mcps").resolve() or not path.is_file():
        raise SystemExit(f"No private config for server '{argv[1]}'")
    data = json.loads(path.read_text(encoding="utf-8"))

    if argv[0] == "keys":
        for key, value in _walk(data):
            shown = json.dumps(value) if key in SHOWN else SECRET_PLACEHOLDER if value == SECRET_PLACEHOLDER else "<set>"
            print(f"{key} = {shown}")
        return

    key_path = argv[2].split(".")
    *parents, leaf = key_path
    value = json.loads(argv[3])
    if any(_SENSITIVE_KEY_RE.search(key) for key in key_path) and value != SECRET_PLACEHOLDER:
        raise SystemExit(f"'{leaf}' looks sensitive; agents may only set it to {SECRET_PLACEHOLDER}")
    target = data
    for key in parents:
        target = target.setdefault(key, {})
    target[leaf] = value
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"set {argv[2]}")


if __name__ == "__main__":
    main(sys.argv[1:])
