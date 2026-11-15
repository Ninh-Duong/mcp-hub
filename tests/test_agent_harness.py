from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from mcp.types import CallToolResult, TextContent

from mcp_hub.agents import specialists
from mcp_hub.agents.harness import ContextBudget, Exchange
from mcp_hub.agents.providers import AnthropicProvider, ModelProvider, ModelToolCall, ModelTurn, OpenAIProvider
from mcp_hub.agents.specialists import SpecialistExecutor
from mcp_hub.settings import JobSettings


def _exchanges(count: int, size: int) -> list[Exchange]:
    return [
        Exchange(
            tool_call_id=f"call-{index}",
            source_id=f"logs-{index:08x}",
            text=f"row {index}\n" + "x" * size,
        )
        for index in range(count)
    ]


def _total(exchanges: list[Exchange]) -> int:
    return sum(len(exchange.text) for exchange in exchanges)


class ContextBudgetTest(unittest.TestCase):
    def test_transcript_over_the_budget_is_brought_under_it(self) -> None:
        compacted = ContextBudget(max_chars=3000).compact(_exchanges(10, 1000))
        self.assertLessEqual(_total(compacted), 3000)

    def test_every_source_and_call_id_survives(self) -> None:
        exchanges = _exchanges(10, 1000)
        compacted = ContextBudget(max_chars=500).compact(exchanges)
        self.assertEqual(len(compacted), len(exchanges))
        self.assertEqual([e.source_id for e in compacted], [e.source_id for e in exchanges])
        self.assertEqual([e.tool_call_id for e in compacted], [e.tool_call_id for e in exchanges])

    def test_last_exchanges_stay_verbatim_when_the_budget_allows(self) -> None:
        exchanges = _exchanges(10, 1000)
        compacted = ContextBudget(max_chars=5000, keep_last_rounds=3).compact(exchanges)
        self.assertEqual([e.text for e in compacted[-3:]], [e.text for e in exchanges[-3:]])
        for older in compacted[:-3]:
            self.assertTrue(older.text.startswith("[cleared; evidence "), older.text)

    def test_cleared_note_keeps_the_source_id_and_first_line(self) -> None:
        exchanges = [
            Exchange("call-0", "logs-abc", "first line\nsecond line"),
            Exchange("call-1", "logs-def", "last result"),
        ]
        compacted = ContextBudget(max_chars=10000, keep_last_rounds=1).compact(exchanges)
        self.assertEqual(compacted[0].text, "[cleared; evidence logs-abc: first line]")
        self.assertEqual(compacted[1].text, "last result")

    def test_cleared_first_line_is_cut_for_one_long_line(self) -> None:
        exchanges = [Exchange("call-0", "logs-abc", "z" * 5000)]
        compacted = ContextBudget(max_chars=10000, keep_last_rounds=0).compact(exchanges)
        self.assertEqual(compacted[0].text, "[cleared; evidence logs-abc: " + "z" * 120 + "]")

    def test_result_without_evidence_is_cleared_without_a_source(self) -> None:
        exchanges = [Exchange("call-0", "", "Error: bad arguments")]
        compacted = ContextBudget(max_chars=10000, keep_last_rounds=0).compact(exchanges)
        self.assertEqual(compacted[0].text, "[cleared: Error: bad arguments]")
        self.assertEqual(compacted[0].source_id, "")

    def test_input_list_is_not_changed(self) -> None:
        exchanges = _exchanges(6, 800)
        before = [e.text for e in exchanges]
        ContextBudget(max_chars=1000).compact(exchanges)
        self.assertEqual([e.text for e in exchanges], before)

    def test_budget_too_small_for_the_notes_is_best_effort(self) -> None:
        exchanges = _exchanges(4, 300)
        compacted = ContextBudget(max_chars=0).compact(exchanges)
        self.assertEqual(len(compacted), 4)
        self.assertTrue(all(e.text.startswith("[cleared; evidence ") for e in compacted))

    def test_empty_transcript_stays_empty(self) -> None:
        self.assertEqual(ContextBudget(max_chars=10).compact([]), [])


class _RecordingProvider(ModelProvider):
    """Returns scripted turns and records the size of each transcript it is sent."""

    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = list(turns)
        self.sizes: list[int] = []
        self.transcripts: list[list[dict]] = []

    async def complete(self, messages: list[dict], tools: list, *, max_output_tokens: int = 2048) -> ModelTurn:
        self.transcripts.append(list(messages))
        self.sizes.append(sum(len(str(message.get("content") or "")) for message in messages))
        return self.turns.pop(0)


def _query_turns(count: int) -> list[ModelTurn]:
    turns = [
        ModelTurn(text="", tool_calls=(ModelToolCall(f"call-{index}", "logs__query", {"n": index}),))
        for index in range(count)
    ]
    return [*turns, ModelTurn(text="All checked", tool_calls=())]


def _logs_runtime() -> SimpleNamespace:
    tool = SimpleNamespace(name="logs__query", annotations=SimpleNamespace(read_only_hint=True))
    return SimpleNamespace(public_tools=[tool], audit=None)


class SpecialistContextBudgetTest(unittest.IsolatedAsyncioTestCase):
    async def test_transcript_stays_within_context_chars_over_ten_rounds(self) -> None:
        provider = _RecordingProvider(_query_turns(10))

        async def fake_call(job_id, role, name, arguments, tool, source_id) -> CallToolResult:
            text = f"row {arguments['n']}\n" + "x" * 1000
            return CallToolResult(content=[TextContent(type="text", text=text)], is_error=False)

        executor = SpecialistExecutor(
            runtime=_logs_runtime(), provider=provider, save_result=lambda *args: None, call_tool=fake_call,
            max_rounds=11, context_chars=3000,
        )
        result = await executor.run("job-1", "logs", "Analyze logs")
        self.assertEqual(result.summary, "All checked")
        self.assertEqual(len(provider.sizes), 11)
        self.assertLessEqual(max(provider.sizes), 3000, provider.sizes)
        first_ref = result.evidence_refs[0].source_id
        self.assertIn(f"[cleared; evidence {first_ref}:", str(provider.transcripts[-1]))
        self.assertEqual(len(result.evidence_refs), 10)


class SpecialistLimitsTest(unittest.IsolatedAsyncioTestCase):
    async def test_tool_result_chars_truncates_the_text_the_model_reads(self) -> None:
        call = ModelToolCall("call-0", "logs__query", {})
        provider = _RecordingProvider([ModelTurn(text="", tool_calls=(call,)), ModelTurn(text="Done", tool_calls=())])

        async def fake_call(job_id, role, name, arguments, tool, source_id) -> CallToolResult:
            return CallToolResult(content=[TextContent(type="text", text="word " * 200)], is_error=False)

        executor = SpecialistExecutor(
            runtime=_logs_runtime(), provider=provider, save_result=lambda *args: None, call_tool=fake_call,
            tool_result_chars=100,
        )
        await executor.run("job-1", "logs", "Analyze logs")
        tool_messages = [m for m in provider.transcripts[-1] if m["role"] == "tool"]
        self.assertEqual(len(tool_messages[0]["content"]), 100)

    def test_limits_default_to_the_settings_values(self) -> None:
        executor = SpecialistExecutor(
            runtime=_logs_runtime(), provider=_RecordingProvider([]),
            save_result=lambda *args: None, call_tool=lambda *args: None,
        )
        self.assertEqual(executor.max_rounds, JobSettings().specialist_max_rounds)
        self.assertEqual(executor.tool_result_chars, JobSettings().tool_result_chars)
        self.assertEqual(executor.context_chars, JobSettings().context_chars)
        self.assertEqual(specialists.MAX_TOOL_RESULT_CHARS, JobSettings().tool_result_chars)


class _FakeCreate:
    """Records each request's keyword arguments and answers with a plain text reply."""

    def __init__(self) -> None:
        self.requests: list[dict] = []

    async def create(self, **kwargs) -> SimpleNamespace:
        self.requests.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], output=[], output_text="ok")


class _FakeClient:
    def __init__(self) -> None:
        self.calls = _FakeCreate()
        self.messages = self.calls
        self.responses = self.calls


def _read_only(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name, description=f"{name} tool", inputSchema={"type": "object"},
        annotations=SimpleNamespace(read_only_hint=True),
    )


LOG_TOOLS = [_read_only("logs__search"), _read_only("logs__query")]
SYSTEM_MESSAGE = {"role": "system", "content": "You are the logs specialist."}
USER_MESSAGE = {"role": "user", "content": "Analyze logs"}


class AnthropicPromptCacheTest(unittest.IsolatedAsyncioTestCase):
    async def _request(self, prompt_cache: bool) -> dict:
        client = _FakeClient()
        provider = AnthropicProvider("test-model", "test-only-key", client=client, prompt_cache=prompt_cache)
        await provider.complete([SYSTEM_MESSAGE, USER_MESSAGE], LOG_TOOLS)
        return client.calls.requests[0]

    def test_prompt_cache_defaults_to_the_settings_value(self) -> None:
        provider = AnthropicProvider("test-model", "test-only-key")
        self.assertIs(provider.prompt_cache, JobSettings().prompt_cache)

    async def test_no_cache_control_when_prompt_cache_is_off(self) -> None:
        request = await self._request(prompt_cache=False)
        self.assertNotIn("cache_control", json.dumps(request))
        self.assertEqual(request["system"], "You are the logs specialist.")

    async def test_breakpoints_on_the_last_tool_and_the_system_block(self) -> None:
        request = await self._request(prompt_cache=True)
        self.assertEqual([tool["name"] for tool in request["tools"]], ["logs__query", "logs__search"])
        self.assertNotIn("cache_control", request["tools"][0])
        self.assertEqual(request["tools"][-1]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(request["system"], [{
            "type": "text", "text": "You are the logs specialist.", "cache_control": {"type": "ephemeral"},
        }])

    async def test_no_empty_system_block_without_a_system_prompt(self) -> None:
        client = _FakeClient()
        provider = AnthropicProvider("test-model", "test-only-key", client=client, prompt_cache=True)
        await provider.complete([USER_MESSAGE], [])
        self.assertEqual(client.calls.requests[0]["system"], "")

    async def test_stable_prefix_is_the_same_for_two_jobs(self) -> None:
        client = _FakeClient()
        provider = AnthropicProvider("test-model", "test-only-key", client=client, prompt_cache=True)
        executor = SpecialistExecutor(
            runtime=SimpleNamespace(public_tools=LOG_TOOLS, audit=None), provider=provider,
            save_result=lambda *args: None, call_tool=lambda *args: None,
        )
        await executor.run("job-1", "logs", "Analyze logs")
        await executor.run("job-2", "logs", "Analyze logs")
        first, second = client.calls.requests
        self.assertEqual(json.dumps(first["tools"]), json.dumps(second["tools"]))
        self.assertEqual(json.dumps(first["system"]), json.dumps(second["system"]))


class OpenAIToolOrderTest(unittest.IsolatedAsyncioTestCase):
    async def test_tools_are_sent_sorted_by_name(self) -> None:
        client = _FakeClient()
        provider = OpenAIProvider("test-model", "test-only-key", client=client)
        await provider.complete([SYSTEM_MESSAGE, USER_MESSAGE], LOG_TOOLS)  # declared as search, then query
        request = client.calls.requests[0]
        self.assertEqual([tool["name"] for tool in request["tools"]], ["logs__query", "logs__search"])


if __name__ == "__main__":
    unittest.main()
