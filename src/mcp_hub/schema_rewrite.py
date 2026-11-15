"""Rewrites of a tool's JSON input schema: the `hub_plan_id` argument that the Hub adds to every published tool,
and the argument check that runs in warn mode. Pure functions; they keep no Hub state."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jsonschema import Draft202012Validator, SchemaError, ValidationError

PLAN_ID_ARG = "hub_plan_id"
_PLAN_ID_SCHEMA = {"type": "string", "description": "The plan ID that hub__plan returned; required."}
# Root keys that compose or reference other schemas: such a root is wrapped, not extended.
_COMPOSED_KEYS = ("allOf", "anyOf", "oneOf", "$ref")
# Definitions move to the wrapper, or the "$ref" pointers inside the wrapped root stop resolving.
_DEFINITION_KEYS = ("$defs", "definitions")


def with_plan_id(schema: Mapping[str, Any] | None) -> dict[str, Any]:
    """A copy of a tool's input schema that also accepts `hub_plan_id`, valid for flat and composed roots."""
    root = dict(schema or {"type": "object"})
    if any(key in root for key in _COMPOSED_KEYS):
        return _wrap_composed(root)
    root["properties"] = {**(root.get("properties") or {}), PLAN_ID_ARG: _PLAN_ID_SCHEMA}
    return root


def _wrap_composed(root: dict[str, Any]) -> dict[str, Any]:
    """An outer object that declares the plan ID and holds the original root under `allOf`. A closed root keeps
    its closure on the outer object, because the inner copy must not forbid the plan ID."""
    definitions = {key: root.pop(key) for key in _DEFINITION_KEYS if key in root}
    if root.get("additionalProperties") is not False:
        return {"type": "object", "properties": {PLAN_ID_ARG: _PLAN_ID_SCHEMA}, "allOf": [root], **definitions}
    inner = {key: value for key, value in root.items() if key != "additionalProperties"}
    properties = {**(root.get("properties") or {}), PLAN_ID_ARG: _PLAN_ID_SCHEMA}
    return {
        "type": "object", "properties": properties, "additionalProperties": False,
        "allOf": [inner], **definitions,
    }


def validate_args(schema: Mapping[str, Any] | None, args: Mapping[str, Any]) -> list[str]:
    """The dotted paths of the arguments that break a child's input schema, in the order the errors come.
    A missing or broken schema gives no paths, because the check is advisory."""
    if schema is None:
        return []
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        return []
    return [_field_path(error) for error in Draft202012Validator(schema).iter_errors(args)]


def _field_path(error: ValidationError) -> str:
    return ".".join(str(part) for part in error.absolute_path) or "<root>"
