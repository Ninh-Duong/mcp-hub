from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any
from collections.abc import Awaitable, Callable, Iterable, Mapping

from jsonschema import Draft202012Validator
from mcp.types import CallToolResult, TextContent

from ..audit import SENSITIVE_FIELD_RE, redact
from ..config import DOCS_ROOT
from ..extensions import ResultCondenser
from ..tool_meta import is_read_only_tool
from ..settings import BitnetSettings, JobSettings
from .harness import ContextBudget, Exchange
from .providers import ModelProvider, ModelToolCall, ModelTurn


ROLE_PREFIXES = {"logs": "logs__", "source": "source__", "jira": "jira__", "pr": "pr__", "sql": "sql__"}


def roles_for_servers(servers: Iterable[str], routes: Mapping[str, Any]) -> list[str]:
    """Return the specialist agent roles whose tools route to any of the named child servers."""
    server_set = set(servers)
    matched: list[str] = []
    for role, prefix in ROLE_PREFIXES.items():
        for tool_name, route in routes.items():
            if tool_name.startswith(prefix) and getattr(route, "server_id", None) in server_set:
                if role not in matched:
                    matched.append(role)
                break
    return matched
# Kept as a name for existing imports; the value is settings.jobs.tool_result_chars.
MAX_TOOL_RESULT_CHARS = JobSettings().tool_result_chars
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
_ERROR_NOTE = "Error: {error}. No result was produced; adjust the call or answer with the evidence you have."
_DEFAULT_PROMPT = (
    "Use only your provided read-only tools and cite evidence. "
    "When the task has a `window`, use exactly that UTC window for every log and SQL time filter."
)


def tools_for_role(role: str, tools: list[Any], allow: Callable[[Any], bool] = is_read_only_tool) -> list[Any]:
    """The role's own tools that `allow` accepts (by default only tools the child marks read-only)."""
    prefix = ROLE_PREFIXES.get(role)
    if prefix is None:
        return []
    return [
        tool for tool in tools
        if isinstance(getattr(tool, "name", None), str) and tool.name.startswith(prefix) and allow(tool)
    ]


def validate_tool_call(call: ModelToolCall, tools: list[Any]) -> Any:
    tool = next((item for item in tools if getattr(item, "name", None) == call.name), None)
    if tool is None:
        raise SpecialistError("This tool is not offered to you; use only the tools you were given")
    schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {"type": "object"}
    try:
        Draft202012Validator(schema).validate(call.arguments)
    except Exception:
        raise SpecialistError("The tool arguments do not match the tool schema") from None
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


def _role_prompt(role: str) -> str:
    role_doc = DOCS_ROOT / "agents" / f"{role}.md"
    return role_doc.read_text(encoding="utf-8") if role_doc.is_file() else _DEFAULT_PROMPT


def _assistant_message(turn: ModelTurn) -> dict[str, Any]:
    return {
        "role": "assistant", "content": turn.text,
        "tool_calls": [{"id": call.call_id, "name": call.name, "arguments": call.arguments} for call in turn.tool_calls],
    }


def _tool_message(call: ModelToolCall, content: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call.call_id, "name": call.name, "content": content}


@dataclass
class _Round:
    """One model turn and the exchanges of its tool calls, in call order."""
    turn: ModelTurn
    exchanges: list[Exchange]


def _build_messages(base: list[dict[str, Any]], rounds: list[_Round], texts: list[str]) -> list[dict[str, Any]]:
    """The base messages, then each round's assistant message and tool messages; `texts` follow call order."""
    messages = list(base)
    remaining = iter(texts)
    for rnd in rounds:
        messages.append(_assistant_message(rnd.turn))
        for call in rnd.turn.tool_calls:
            messages.append(_tool_message(call, next(remaining)))
    return messages


def _transcript_chars(messages: list[dict[str, Any]]) -> int:
    """Characters the model reads: message content plus the JSON of each tool call's arguments."""
    total = 0
    for message in messages:
        total += len(str(message.get("content") or ""))
        for call in message.get("tool_calls") or []:
            total += len(json.dumps(call.get("arguments", {}), ensure_ascii=False))
    return total


class SpecialistExecutor:
    """Runs one specialist's bounded model/tool loop.

    A failed tool call (unknown or withheld tool, schema mismatch, child exception, call cap) is
    returned to the model as an error tool message; only a provider error ends the specialist early.
    """

    def __init__(
        self,
        *,
        runtime: Any,
        provider: ModelProvider,
        save_result: Callable[[str, str, Any], None],
        call_tool: Callable[[str, str, str, dict[str, Any], Any, str], Awaitable[CallToolResult | ToolCallOutcome]],
        tool_filter: Callable[[Any], bool] = is_read_only_tool,
        max_rounds: int = JobSettings().specialist_max_rounds,
        context_chars: int = JobSettings().context_chars,
        tool_result_chars: int = JobSettings().tool_result_chars,
        condenser: ResultCondenser | None = None,
        condense_min_chars: int = BitnetSettings().condense_min_chars,
        condense_timeout_seconds: float = BitnetSettings().condense_timeout_seconds,
        provider_output_tokens: int = JobSettings().provider_output_tokens,
    ) -> None:
        self.runtime = runtime
        self.provider = provider
        self.save_result = save_result
        self.call_tool = call_tool
        self.tool_filter = tool_filter
        self.max_rounds = max_rounds
        self.context_chars = context_chars
        self.tool_result_chars = tool_result_chars
        self.condenser = condenser
        self.condense_min_chars = condense_min_chars
        self.condense_timeout_seconds = condense_timeout_seconds
        self.provider_output_tokens = provider_output_tokens

    async def run(self, job_id: str, role: str, task: str) -> SpecialistResult:
        tools = tools_for_role(role, self.runtime.public_tools, self.tool_filter)
        if not tools:
            return SpecialistResult(role, "", (), "No connected tools are available for this specialist.")
        base = await self._initial_messages(role, task)
        rounds: list[_Round] = []
        evidence: list[EvidenceRef] = []
        for _ in range(self.max_rounds):
            turn = await self.provider.complete(
                self._transcript(base, rounds), tools, max_output_tokens=self.provider_output_tokens,
            )
            if not turn.tool_calls:
                return SpecialistResult(role, turn.text.strip(), tuple(evidence))
            exchanges: list[Exchange] = []
            for call in turn.tool_calls:
                exchange, ref = await self._handle_call(job_id, role, call, tools)
                exchanges.append(exchange)
                if ref is not None and ref not in evidence:
                    evidence.append(ref)
            rounds.append(_Round(turn, exchanges))
        return SpecialistResult(role, "", tuple(evidence), "Specialist reached its response-round limit.")

    def _transcript(self, base: list[dict[str, Any]], rounds: list[_Round]) -> list[dict[str, Any]]:
        """The messages for the next model call; older tool results are cleared until they fit context_chars."""
        flat = [exchange for rnd in rounds for exchange in rnd.exchanges]
        fixed = _transcript_chars(_build_messages(base, rounds, [""] * len(flat)))
        compacted = ContextBudget(max(0, self.context_chars - fixed)).compact(flat)
        return _build_messages(base, rounds, [exchange.text for exchange in compacted])

    async def _initial_messages(self, role: str, task: str) -> list[dict[str, Any]]:
        system = await asyncio.to_thread(_role_prompt, role)
        audit = getattr(self.runtime, "audit", None)
        redactor = (
            (lambda text: audit.redact_content(text, limit=self.tool_result_chars))
            if audit is not None else None
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": minimize_model_content(
                task, limit=self.tool_result_chars, redactor=redactor,
            )},
        ]

    async def _handle_call(
        self, job_id: str, role: str, call: ModelToolCall, tools: list[Any],
    ) -> tuple[Exchange, EvidenceRef | None]:
        """Run one requested call; any failure becomes an error exchange (no evidence) so the model can continue."""
        try:
            tool = validate_tool_call(call, tools)
            return await self._run_tool_call(job_id, role, call, tool)
        except SpecialistError as exc:
            error = str(exc)
        except Exception as exc:
            error = f"Child call failed ({type(exc).__name__})"
        return Exchange(call.call_id, "", _ERROR_NOTE.format(error=error)), None

    async def _run_tool_call(
        self, job_id: str, role: str, call: ModelToolCall, tool: Any,
    ) -> tuple[Exchange, EvidenceRef]:
        """Call one validated tool, store its result encrypted, and return a minimized copy for the conversation.

        A result reused from an identical earlier call in the job keeps that call's source ID
        and is neither stored again nor resent to the model.
        """
        source_id = f"{role}-{uuid.uuid4().hex[:8]}"
        outcome = await self.call_tool(job_id, role, call.name, call.arguments, tool, source_id)
        if isinstance(outcome, CallToolResult):
            outcome = ToolCallOutcome(outcome, source_id)
        if outcome.reused:
            content = _REUSED_NOTE.format(source_id=outcome.source_id)
        else:
            payload = outcome.result.model_dump(mode="json")
            await asyncio.to_thread(self.save_result, job_id, outcome.source_id, payload)
            target: Any = outcome.result
            if not outcome.result.is_error and self.condenser is not None:
                raw_text = "\n".join(b.text for b in (outcome.result.content or []) if isinstance(b, TextContent))
                if len(raw_text) >= self.condense_min_chars:
                    try:
                        condensed = await asyncio.wait_for(
                            self.condenser.condense(raw_text, call.name),
                            timeout=self.condense_timeout_seconds,
                        )
                        target = (
                            f"{condensed.text}\n"
                            f"[Condensed: kept {condensed.kept_lines} of {condensed.total_lines} lines; full evidence {outcome.source_id}.]"
                        )
                    except Exception:
                        target = outcome.result
            audit = getattr(self.runtime, "audit", None)
            redactor = (lambda t: audit.redact_content(t, limit=self.tool_result_chars)) if audit is not None else None
            content = minimize_model_content(target, limit=self.tool_result_chars, redactor=redactor)
            if outcome.result.is_error:
                content = f"The tool returned an error result [{outcome.source_id}]:\n{content}"
        return Exchange(call.call_id, outcome.source_id, content), EvidenceRef(outcome.source_id, call.name, role)
