from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from jsonschema import Draft202012Validator
from mcp.types import CallToolResult, TextContent

from ..audit import redact
from ..config import HUB_ROOT
from .providers import ModelProvider, ModelToolCall, ProviderError


ROLE_PREFIXES = {"logs": "logs__", "source": "source__", "jira": "jira__", "pr": "pr__"}
MAX_TOOL_RESULT_CHARS = 6000
_SENSITIVE_FIELD = re.compile(r"(?i)(?:token|secret|password|authorization|api[_-]?key|credential|email|user(?:name|_id)?)")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_TOKEN_SHAPE = re.compile(r"(?i)\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b")


class SpecialistError(RuntimeError):
    """Safe specialist failure."""


@dataclass(frozen=True)
class EvidenceRef:
    source_id: str
    tool: str
    role: str


@dataclass(frozen=True)
class SpecialistResult:
    role: str
    summary: str
    evidence_refs: tuple[EvidenceRef, ...]
    limitation: str | None = None


def tools_for_role(role: str, tools: list[Any]) -> list[Any]:
    prefix = ROLE_PREFIXES.get(role)
    if prefix is None:
        return []
    return [tool for tool in tools if isinstance(getattr(tool, "name", None), str) and tool.name.startswith(prefix)]


def validate_tool_call(call: ModelToolCall, tools: list[Any]) -> Any:
    tool = next((item for item in tools if getattr(item, "name", None) == call.name), None)
    if tool is None:
        raise SpecialistError("Agent requested a tool outside its role allowlist")
    schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {"type": "object"}
    try:
        Draft202012Validator(schema).validate(call.arguments)
    except Exception:
        raise SpecialistError("Agent tool arguments do not match the tool schema") from None
    return tool


def minimize_model_content(
    value: Any,
    *,
    limit: int = MAX_TOOL_RESULT_CHARS,
    redactor: Callable[[str], str] | None = None,
) -> str:
    """Remove sensitive fields and redact credentials before external provider calls."""
    if isinstance(value, CallToolResult):
        pieces = []
        for block in value.content or []:
            if isinstance(block, TextContent):
                pieces.append(block.text)
        text = "\n".join(pieces)
    elif isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if parsed is not None:
        text = json.dumps(_filter_sensitive_fields(parsed), ensure_ascii=False, separators=(",", ":"))
    text = redactor(text) if redactor else redact(text, limit=limit)
    text = _EMAIL.sub("[REDACTED_EMAIL]", text)
    text = _TOKEN_SHAPE.sub("[REDACTED_TOKEN]", text)
    return text[:limit]


def _filter_sensitive_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _filter_sensitive_fields(child)
            for key, child in value.items()
            if not _SENSITIVE_FIELD.search(str(key))
        }
    if isinstance(value, list):
        return [_filter_sensitive_fields(item) for item in value]
    return value


class SpecialistExecutor:
    def __init__(
        self,
        *,
        runtime: Any,
        provider: ModelProvider,
        save_result: Callable[[str, str, Any], None],
        call_tool: Callable[[str, str, str, dict[str, Any], Any, str], Awaitable[CallToolResult]],
        max_rounds: int = 8,
    ) -> None:
        self.runtime = runtime
        self.provider = provider
        self.save_result = save_result
        self.call_tool = call_tool
        self.max_rounds = max_rounds

    async def run(self, job_id: str, role: str, task: str) -> SpecialistResult:
        tools = tools_for_role(role, self.runtime.public_tools)
        if not tools:
            return SpecialistResult(role, "", (), "No connected tools are available for this specialist.")
        role_doc = HUB_ROOT / "docs" / "agents" / f"{role}.md"
        system = role_doc.read_text(encoding="utf-8") if role_doc.is_file() else "Use only your provided read-only tools and cite evidence."
        audit = getattr(self.runtime, "audit", None)
        redactor = (
            (lambda text: audit.redact_content(text, limit=MAX_TOOL_RESULT_CHARS))
            if audit is not None else None
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": minimize_model_content(
                task, limit=MAX_TOOL_RESULT_CHARS, redactor=redactor,
            )},
        ]
        evidence: list[EvidenceRef] = []
        for _ in range(self.max_rounds):
            turn = await self.provider.complete(messages, tools)
            if not turn.tool_calls:
                return SpecialistResult(role, turn.text.strip(), tuple(evidence))
            tool_calls = []
            for call in turn.tool_calls:
                tool = validate_tool_call(call, tools)
                tool_calls.append({"id": call.call_id, "name": call.name, "arguments": call.arguments})
            messages.append({"role": "assistant", "content": turn.text, "tool_calls": tool_calls})
            for call in turn.tool_calls:
                tool = validate_tool_call(call, tools)
                source_id = f"{role}-{uuid.uuid4().hex[:8]}"
                try:
                    result = await self.call_tool(job_id, role, call.name, call.arguments, tool, source_id)
                except SpecialistError:
                    raise
                except Exception as exc:
                    raise SpecialistError(f"Child call failed ({type(exc).__name__})") from None
                self.save_result(job_id, source_id, result.model_dump(mode="json"))
                evidence.append(EvidenceRef(source_id, call.name, role))
                messages.append({
                    "role": "tool", "tool_call_id": call.call_id, "name": call.name,
                    "content": minimize_model_content(
                        result,
                        redactor=lambda text: self.runtime.audit.redact_content(text, limit=MAX_TOOL_RESULT_CHARS),
                    ),
                })
        return SpecialistResult(role, "", tuple(evidence), "Specialist reached its response-round limit.")
