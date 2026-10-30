from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from jsonschema import Draft202012Validator
from mcp.types import CallToolResult, TextContent

from ..audit import SENSITIVE_FIELD_RE, redact
from ..config import HUB_ROOT
from .providers import ModelProvider, ModelToolCall, ProviderError


ROLE_PREFIXES = {"logs": "logs__", "source": "source__", "jira": "jira__", "pr": "pr__"}
MAX_TOOL_RESULT_CHARS = 6000
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")


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


@dataclass(frozen=True)
class ToolCallOutcome:
    """A child result and its evidence ID; `reused` means an identical earlier call in the job supplied it."""
    result: CallToolResult
    source_id: str
    reused: bool = False


_REUSED_NOTE = (
    "This identical call already ran in this job; its result is the earlier tool message [{source_id}]. "
    "Do not repeat identical calls."
)


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
    return text[:limit]


def _filter_sensitive_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _filter_sensitive_fields(child)
            for key, child in value.items()
            if not SENSITIVE_FIELD_RE.search(str(key))
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
        call_tool: Callable[[str, str, str, dict[str, Any], Any, str], Awaitable[CallToolResult | ToolCallOutcome]],
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
        messages = self._initial_messages(role, task)
        evidence: list[EvidenceRef] = []
        for _ in range(self.max_rounds):
            turn = await self.provider.complete(messages, tools)
            if not turn.tool_calls:
                return SpecialistResult(role, turn.text.strip(), tuple(evidence))
            tool_calls = []
            for call in turn.tool_calls:
                validate_tool_call(call, tools)
                tool_calls.append({"id": call.call_id, "name": call.name, "arguments": call.arguments})
            messages.append({"role": "assistant", "content": turn.text, "tool_calls": tool_calls})
            for call in turn.tool_calls:
                tool = validate_tool_call(call, tools)
                ref = await self._run_tool_call(job_id, role, call, tool, messages)
                if ref not in evidence:
                    evidence.append(ref)
        return SpecialistResult(role, "", tuple(evidence), "Specialist reached its response-round limit.")

    def _initial_messages(self, role: str, task: str) -> list[dict[str, Any]]:
        role_doc = HUB_ROOT / "docs" / "agents" / f"{role}.md"
        system = role_doc.read_text(encoding="utf-8") if role_doc.is_file() else "Use only your provided read-only tools and cite evidence."
        audit = getattr(self.runtime, "audit", None)
        redactor = (
            (lambda text: audit.redact_content(text, limit=MAX_TOOL_RESULT_CHARS))
            if audit is not None else None
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": minimize_model_content(
                task, limit=MAX_TOOL_RESULT_CHARS, redactor=redactor,
            )},
        ]

    async def _run_tool_call(
        self, job_id: str, role: str, call: ModelToolCall, tool: Any, messages: list[dict[str, Any]],
    ) -> EvidenceRef:
        """Call one validated tool, store its result encrypted, and append a minimized copy to the conversation.

        A result reused from an identical earlier call in the job keeps that call's source ID
        and is neither stored again nor resent to the model.
        """
        source_id = f"{role}-{uuid.uuid4().hex[:8]}"
        try:
            outcome = await self.call_tool(job_id, role, call.name, call.arguments, tool, source_id)
        except SpecialistError:
            raise
        except Exception as exc:
            raise SpecialistError(f"Child call failed ({type(exc).__name__})") from None
        if isinstance(outcome, CallToolResult):
            outcome = ToolCallOutcome(outcome, source_id)
        if outcome.reused:
            content = _REUSED_NOTE.format(source_id=outcome.source_id)
        else:
            self.save_result(job_id, outcome.source_id, outcome.result.model_dump(mode="json"))
            content = minimize_model_content(
                outcome.result,
                redactor=lambda text: self.runtime.audit.redact_content(text, limit=MAX_TOOL_RESULT_CHARS),
            )
        messages.append({"role": "tool", "tool_call_id": call.call_id, "name": call.name, "content": content})
        return EvidenceRef(outcome.source_id, call.name, role)
