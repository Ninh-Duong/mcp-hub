"""Fill-once task context and deterministic tool-specific scope checks."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from .workflow_definition import INPUTS, SOURCE_ROLES


class WorkflowError(ValueError):
    """A safe refusal with no child content."""


def absolute(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        raise WorkflowError("Use absolute timestamps with timezones; ask the user for the time window.")
    return parsed.astimezone(UTC)


def fill_context(context: dict[str, Any], inputs: Any, max_chars: int, max_hours: int) -> dict[str, Any]:
    if not isinstance(inputs, dict) or set(inputs) - INPUTS:
        raise WorkflowError("Unknown context input; use the workflow's missing inputs.")
    updated = dict(context)
    for key, value in inputs.items():
        if not isinstance(value, str) or not value.strip() or len(value) > max_chars:
            raise WorkflowError(f"Input {key} must be a bounded, non-empty string.")
        clean = absolute(value).isoformat() if key in {"since", "until"} else value.strip()
        if key == "related_issue_keys":
            keys = [key.strip().upper() for key in clean.split(",")]
            if not all(re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+", key) for key in keys):
                raise WorkflowError("Provide selected related ticket keys separated by commas.")
            clean = ",".join(dict.fromkeys(keys))
        if key == "path" and not safe_path(clean):
            raise WorkflowError("Source path must be relative and stay in the registered project.")
        if key in updated and updated[key] != clean:
            raise WorkflowError(f"Input {key} is locked; ask permission and create a new scoped plan.")
        updated[key] = clean
    if ("since" in updated) != ("until" in updated):
        raise WorkflowError("Supply both since and until together.")
    if "since" in updated:
        span = (absolute(updated["until"]) - absolute(updated["since"])).total_seconds()
        if not 0 < span <= max_hours * 3600:
            raise WorkflowError("Time window must be ordered and within maxWindowHours.")
    return updated


def safe_path(value: str) -> bool:
    path = PurePosixPath(value.replace("\\", "/"))
    return bool(value) and not path.is_absolute() and ".." not in path.parts and ":" not in value


def missing_inputs(context: dict[str, Any], required: list[str] | tuple[str, ...]) -> list[str]:
    missing = []
    for key in dict.fromkeys(required):
        if key == "source_selection":
            if not source_projects(context):
                missing.append("source_selection")
        elif key == "window":
            missing.extend(name for name in ("since", "until") if name not in context)
        elif key == "log_target":
            if not all(context.get(name) for name in ("organization", "stream", "field")):
                missing.append("log_target")
        elif not context.get(key):
            missing.append(key)
    return list(dict.fromkeys(missing))


def source_projects(context: dict[str, Any]) -> set[str]:
    return {context[key] for key in SOURCE_ROLES if context.get(key)}


def related_issues(context: dict[str, Any]) -> set[str]:
    return set(context.get("related_issue_keys", "").split(",")) - {""}


def check_scope(tool: str, arguments: dict[str, Any], context: dict[str, Any], max_limit: int,
                paths: set[str], projects: set[str] | None = None, issues: set[str] | None = None) -> None:
    for key in ("project", "env", "rcid", "issue_key", "pr_url", "organization", "stream", "field", "service"):
        if key == "project" and tool.startswith("source__") and projects is not None:
            continue
        if key == "issue_key" and tool.startswith("jira__") and issues is not None:
            continue
        if key in arguments and key in context and arguments[key] != context[key]:
            raise WorkflowError(f"Pass exactly the plan's {key}.")
    if arguments.get("force", False) is not False or arguments.get("refresh", False) is not False:
        raise WorkflowError("Forced refresh requires a separate explicit request and custom scoped plan.")
    if tool.startswith("source__"):
        check_source(tool, arguments, context, paths, projects)
    if tool.startswith("pr__") and tool != "pr__list_synced_prs":
        require_bound(arguments, context, ("pr_url",))
    if tool.startswith("jira__"):
        if issues is None:
            require_bound(arguments, context, ("issue_key",))
        elif arguments.get("issue_key") not in issues:
            raise WorkflowError("Read only the explicitly confirmed related tickets.")
    if tool == "logs__list_ai_context_snapshots":
        require_bound(arguments, context, ("env",))
    if tool in {"logs__find_openobserve_logs_by_rcid", "logs__get_openobserve_service_logs"}:
        check_logs(tool, arguments, context, max_limit)
    if tool == "logs__list_openobserve_services":
        require_bound(arguments, context, ("env", "organization", "stream"))
    if tool == "logs__read_ai_context" and not all(isinstance(arguments.get(k), str) and arguments[k] for k in ("snapshot", "file")):
        raise WorkflowError("Cache reads require snapshot and file from listed evidence.")


def require_bound(arguments: dict[str, Any], context: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        if key not in context or arguments.get(key) != context[key]:
            raise WorkflowError(f"Supply the bound {key}; ask for missing context first.")


def check_source(tool: str, arguments: dict[str, Any], context: dict[str, Any], paths: set[str],
                 projects: set[str] | None = None) -> None:
    if projects is None:
        require_bound(arguments, context, ("project",))
    elif arguments.get("project") not in projects:
        raise WorkflowError("Use only a confirmed source project alias.")
    if tool == "source__search":
        if not isinstance(arguments.get("query"), str) or not arguments["query"].strip():
            raise WorkflowError("Provide a bounded source search query.")
        if "query" in context and projects is None:
            require_bound(arguments, context, ("query",))
        if "glob" in context:
            require_bound(arguments, context, ("glob",))
    if tool == "source__read":
        path = arguments.get("path")
        user_paths = {context.get("path")} if projects is None else set()
        if not isinstance(path, str) or not safe_path(path) or path not in paths | user_paths:
            raise WorkflowError("Read only a searched path or the user's bound source path.")


def check_logs(tool: str, arguments: dict[str, Any], context: dict[str, Any], max_limit: int) -> None:
    require_bound(arguments, context, ("env", "organization", "stream"))
    if tool == "logs__find_openobserve_logs_by_rcid":
        require_bound(arguments, context, ("rcid", "field"))
    else:
        require_bound(arguments, context, ("service",))
        if arguments.get("level") not in {"Error", "Warning"}:
            raise WorkflowError("Read Error or related Warning rows before other levels.")
    actual = (absolute(arguments.get("from")), absolute(arguments.get("to")))
    expected = (absolute(context.get("since")), absolute(context.get("until")))
    if actual != expected:
        raise WorkflowError("Use exactly the plan's absolute from/to window.")
    limit = arguments.get("limit")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= max_limit:
        raise WorkflowError(f"Pass an explicit log limit between 1 and {max_limit}.")


def result_observations(result: Any, max_items: int, max_chars: int) -> dict[str, Any]:
    """Keep only bounded scope anchors and completeness flags, never arbitrary result text."""
    data = getattr(result, "structured_content", None)
    if not isinstance(data, dict):
        for block in getattr(result, "content", ()) or ():
            try:
                candidate = json.loads(getattr(block, "text", ""))
            except (ValueError, TypeError):
                continue
            if isinstance(candidate, dict):
                data = candidate
                break
    assets = asset_names(result, max_items, max_chars)
    if not isinstance(data, dict):
        return {"complete": None, "paths": (), "revision": None, "assets": assets}
    matches = data.get("matches")
    paths = [item.get("path") for item in matches if isinstance(item, dict)] if isinstance(matches, list) else []
    paths = tuple(dict.fromkeys(p for p in paths if isinstance(p, str) and len(p) <= max_chars and safe_path(p)))[:max_items]
    revision = data.get("source_commit") or data.get("revision")
    revision = str(revision) if isinstance(revision, (str, int)) else None
    revision = revision if revision and len(revision) <= max_chars else None
    total = data.get("total")
    cut = isinstance(matches, list) and isinstance(total, int) and total > len(matches)
    incomplete = bool(data.get("truncated") or data.get("next_offset") is not None or data.get("warning") or data.get("warnings") or cut)
    complete = data.get("complete") if isinstance(data.get("complete"), bool) else None
    return {"complete": False if incomplete else complete, "paths": paths, "revision": revision, "assets": assets}


def asset_names(result: Any, max_items: int, max_chars: int) -> tuple[str, ...]:
    names = []
    for block in getattr(result, "content", ()) or ():
        text = getattr(block, "text", "")
        names.extend(re.findall(r"assets/([^\s\)\]\"<>]+)", text))
    data = getattr(result, "structured_content", None)
    if isinstance(data, dict):
        for item in data.get("attachments", []) if isinstance(data.get("attachments"), list) else []:
            if isinstance(item, dict) and isinstance(item.get("filename"), str):
                names.append(item["filename"].removeprefix("assets/"))
    return tuple(dict.fromkeys(name for name in names if len(name) <= max_chars and safe_path(name)))[:max_items]


def error_kind(result: Any) -> str | None:
    text = "\n".join(getattr(block, "text", "") for block in getattr(result, "content", ()) or ()).casefold()
    if any(word in text for word in ("no access", "access denied", "cannot decrypt", "unauthorized", "not configured", "forbidden")):
        return "access"
    if any(word in text for word in ("not cached", "not synced", "cache missing", "cache is missing")):
        return "cache_missing"
    return None
