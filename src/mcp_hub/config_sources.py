"""Where each child's required settings come from, as the child itself reports through `config_status`.

The Hub's own check reads only `config/private/mcps/<id>.json`, but a child may keep its accounts in its own
local files. After a child connects, the Hub asks its `config_status` tool once (no input, no network on the
child's side) for `{"groups": {"<group-id>": "env|hub|local|default|unset"}}`. A group the child reports as
set no longer counts as a gap. The tool is never routed to agents; only this module calls it.

Any failure (no tool, an error result, a timeout, another shape) keeps the gaps read from the Hub file, so an
error never hides a real gap. Only group ids and source words are stored or logged, never values.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Any

from .audit import AuditLog, describe_exception
from .child_requirements import MissingGroup, parse_source_report, remaining_keys

CONFIG_STATUS_TOOL = "config_status"


class ConfigSources:
    """The latest source report per child, replaced at each connect."""

    def __init__(self, audit: AuditLog, timeout_seconds: float) -> None:
        self.audit = audit
        self.timeout_seconds = timeout_seconds
        self._reports: dict[str, dict[str, str]] = {}

    def report(self, server_id: str) -> dict[str, str]:
        return dict(self._reports.get(server_id, {}))

    def reports(self) -> dict[str, dict[str, str]]:
        return {server_id: dict(report) for server_id, report in self._reports.items()}

    async def probe(self, server_id: str, client: Any, tool_names: Any) -> None:
        """Ask a freshly connected child where its settings come from. Never raises."""
        self._reports.pop(server_id, None)
        if CONFIG_STATUS_TOOL not in tool_names:
            return
        try:
            result = await asyncio.wait_for(client.call_tool(CONFIG_STATUS_TOOL, {}), self.timeout_seconds)
        except Exception as exc:  # the child may be gone, slow, or broken: keep the file-only gaps
            error_type, _message = describe_exception(exc)
            if isinstance(exc, TimeoutError):
                error_type = "timeout"
            self.audit.write("child_config_status", level=logging.WARNING, server_id=server_id, ok=False,
                             error_type=error_type)
            return
        report = parse_source_report(_payload(result))
        if report is None:
            self.audit.write("child_config_status", level=logging.WARNING, server_id=server_id, ok=False,
                             error_type="invalid_result")
            return
        self._reports[server_id] = report
        self.audit.write("child_config_status", server_id=server_id, ok=True, groups=report)


def gaps_after_reports(
    keys: Mapping[str, tuple[str, ...]],
    groups: Mapping[str, tuple[MissingGroup, ...]],
    reports: Mapping[str, Mapping[str, str]],
) -> dict[str, list[str]]:
    """Each child's remaining missing key paths once its own report is applied; children with none left are omitted."""
    remaining: dict[str, list[str]] = {}
    for server_id, file_keys in keys.items():
        gaps = groups.get(server_id)
        left = remaining_keys(gaps, reports.get(server_id)) if gaps is not None else file_keys
        if left:
            remaining[server_id] = list(left)
    return remaining


def _payload(result: Any) -> Any:
    """The JSON object a tool result carries: its structured content, else its first text block."""
    if getattr(result, "is_error", False):
        return None
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    for block in getattr(result, "content", None) or ():
        text = getattr(block, "text", None)
        if isinstance(text, str):
            try:
                return json.loads(text)
            except ValueError:
                return None
    return None
