"""Validated data-only workflow graphs embedded in individual playbook files."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from ..config import ConfigError

_NAME = {"type": "string", "pattern": "^[a-z][a-z0-9_-]*$"}
_LIST = {"type": "array", "items": {"type": "string"}, "uniqueItems": True}
_TARGET = {"oneOf": [{"type": "string"}, {"type": "object", "additionalProperties": False,
           "properties": {"input": _NAME, "present": {"type": "string"}, "missing": {"type": "string"}},
           "required": ["input", "present", "missing"]}]}
_NODE = {
    "type": "object", "additionalProperties": False,
    "properties": {"tools": _LIST, "requires": _LIST, "interaction": {"type": "boolean"}, "evidence": _LIST,
                   "source_coverage": {"type": "boolean"},
                   "ticket_scope": {"enum": ["primary", "related"]}, "ticket_coverage": {"type": "boolean"},
                   "allow_missing_cache": {"type": "boolean"},
                   "after": {"type": "object", "additionalProperties": {"type": "string"}},
                   "arguments": {"type": "object", "additionalProperties": {"type": "object", "additionalProperties": {"type": ["string", "integer", "boolean"]}}},
                   "reports": {"type": "object", "additionalProperties": _TARGET}},
    "required": ["tools", "reports"],
}
_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"entry": _NAME, "inputs": _LIST, "subflows": _LIST, "multi_source": {"type": "boolean"}, "multi_ticket": {"type": "boolean"},
                   "entry_choices": {"type": "object", "propertyNames": _NAME, "additionalProperties": _NAME},
                   "nodes": {"type": "object", "minProperties": 1, "propertyNames": _NAME,
                             "additionalProperties": _NODE}},
    "required": ["entry", "inputs", "nodes"],
}
SOURCE_ROLES = frozenset({"be_project", "db_project", "fe_project"})
REVIEW_SELECTION = SOURCE_ROLES | {"related_issue_keys"}
INPUTS = frozenset({"project", "env", "rcid", "service", "issue_key", "pr_url", "query", "glob", "path", "since", "until", "related_issue_keys"}) | SOURCE_ROLES
REQUIREMENTS = INPUTS | {"window", "log_target", "source_selection"}
TERMINALS = frozenset({"ready", "blocked", "finished", "superseded"})


@dataclass(frozen=True)
class WorkflowDefinition:
    entry: str
    inputs: tuple[str, ...]
    nodes: dict[str, dict[str, Any]]
    subflows: tuple[str, ...] = ()
    entry_choices: dict[str, str] | None = None
    multi_source: bool = False
    multi_ticket: bool = False


def parse_definition(body: str, file_id: str) -> WorkflowDefinition | None:
    blocks = re.findall(r"^```workflow\s*\n(.*?)^```\s*$", body, re.M | re.S)
    if not blocks:
        return None
    try:
        if len(blocks) != 1:
            raise ValueError
        data = json.loads(blocks[0])
        if not Draft202012Validator(_SCHEMA).is_valid(data):
            raise ValueError
        definition = WorkflowDefinition(data["entry"], tuple(data["inputs"]), data["nodes"], tuple(data.get("subflows", [])), data.get("entry_choices", {}), data.get("multi_source", False), data.get("multi_ticket", False))
        _validate_local(definition)
        return definition
    except (ValueError, TypeError, KeyError):
        raise ConfigError(f"Playbook {file_id}: invalid workflow JSON definition") from None


def _validate_local(definition: WorkflowDefinition) -> None:
    if definition.entry not in definition.nodes or set(definition.inputs) - INPUTS:
        raise ValueError
    if set(definition.entry_choices or {}) - INPUTS or set((definition.entry_choices or {}).values()) - set(definition.nodes):
        raise ValueError
    if set(definition.nodes) & TERMINALS:
        raise ValueError
    for node in definition.nodes.values():
        if set(node.get("requires", [])) - REQUIREMENTS:
            raise ValueError
        if not node["reports"] or (not node["tools"] and not node.get("interaction")):
            raise ValueError
        if node.get("interaction") and (node["tools"] or not node.get("evidence")):
            raise ValueError
        if node.get("source_coverage") and not definition.multi_source:
            raise ValueError
        if (node.get("ticket_scope") == "related" or node.get("ticket_coverage")) and not definition.multi_ticket:
            raise ValueError
        if any(not re.fullmatch(r"[a-z][a-z0-9_]*", event) for event in node["reports"]):
            raise ValueError
        if any(isinstance(target, dict) and target["input"] not in INPUTS for target in node["reports"].values()):
            raise ValueError


def map_target(target: Any, convert: Any) -> Any:
    if isinstance(target, str):
        return convert(target)
    return {"input": target["input"], "present": convert(target["present"]), "missing": convert(target["missing"])}


def targets(node: dict[str, Any]) -> list[str]:
    return [value for target in node["reports"].values()
            for value in ([target] if isinstance(target, str) else [target["present"], target["missing"]])]


def compile_definition(name: str, definitions: dict[str, WorkflowDefinition | None], tools: set[str]) -> WorkflowDefinition:
    """Explicit one-level subflows; includes never silently imports executable stage definitions."""
    own = definitions[name]
    if own is None:
        raise ConfigError(f"Playbook {name}: missing workflow")
    nodes = {key: {**node, "reports": dict(node["reports"])} for key, node in own.nodes.items()}
    for reference in own.subflows:
        child = definitions.get(reference)
        if reference == name or child is None or child.subflows:
            raise ConfigError(f"Playbook {name}: unknown, cyclic or nested workflow reference")
        if child.multi_source != own.multi_source or child.multi_ticket != own.multi_ticket:
            raise ConfigError(f"Playbook {name}: subflows must use the same source-scope mode")
        for key, node in child.nodes.items():
            nodes[f"{reference}.{key}"] = {
                **node, "requires": list(dict.fromkeys([*child.inputs, *node.get("requires", [])])),
                "reports": {event: map_target(target, lambda t, prefix=reference: t if t in TERMINALS else f"{prefix}.{t}")
                                    for event, target in node["reports"].items()},
            }
        for node in nodes.values():
            node["reports"] = {event: map_target(target, lambda t, ref=reference, entry=child.entry: f"{ref}.{entry}" if t == f"@{ref}" else t)
                               for event, target in node["reports"].items()}
    _validate_graph(name, nodes, tools)
    return WorkflowDefinition(own.entry, own.inputs, nodes, entry_choices=own.entry_choices, multi_source=own.multi_source, multi_ticket=own.multi_ticket)


def _validate_graph(name: str, nodes: dict[str, dict[str, Any]], tools: set[str]) -> None:
    for node in nodes.values():
        if set(node["tools"]) - tools or set(targets(node)) - set(nodes) - {"ready", "blocked"}:
            raise ConfigError(f"Playbook {name}: workflow tool or transition is not declared")
        if set(node.get("after", {})) - tools or set(node.get("after", {}).values()) - tools:
            raise ConfigError(f"Playbook {name}: undeclared workflow prerequisite tool")
        if set(node.get("arguments", {})) - set(node["tools"]):
            raise ConfigError(f"Playbook {name}: argument constraint must name a tool in its stage")
        if set(node.get("evidence", [])) - tools:
            raise ConfigError(f"Playbook {name}: interaction evidence must name declared tools")
    visited, visiting = set(), set()

    def visit(stage: str) -> None:
        if stage in visiting:
            raise ConfigError(f"Playbook {name}: cyclic workflow transitions")
        if stage in visited or stage in TERMINALS:
            return
        visiting.add(stage)
        for target in targets(nodes[stage]):
            visit(target)
        visiting.remove(stage)
        visited.add(stage)

    for stage in nodes:
        visit(stage)
