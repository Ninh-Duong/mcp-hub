from __future__ import annotations

import json
import unittest

from mcp_hub.agents.coordinator import synthesize
from mcp_hub.agents.providers import ModelToolCall, ModelTurn, ProviderError
from mcp_hub.agents.specialists import EvidenceRef, SpecialistResult


class _Provider:
    def __init__(self, turn: ModelTurn | None = None, error: Exception | None = None) -> None:
        self.turn = turn or ModelTurn("  Root cause is X [logs-1].  ")
        self.error = error
        self.calls: list[tuple[list, list, int]] = []

    async def complete(self, messages, tools, *, max_output_tokens=2048):
        self.calls.append((messages, tools, max_output_tokens))
        if self.error:
            raise self.error
        return self.turn


def _findings() -> list[SpecialistResult]:
    return [
        SpecialistResult("logs", "Errors since 10:00 [logs-1].", (EvidenceRef("logs-1", "logs__query_logs", "logs"),)),
        SpecialistResult("source", "", (), "Agent is not configured."),
    ]


async def _run(provider: _Provider, **overrides) -> str:
    arguments = {
        "question": "Why do orders fail?", "project": "shop", "env": "dev",
        "issue_key": "ABC-1", "findings": _findings(), **overrides,
    }
    return await synthesize(provider, **arguments)


class SynthesizeTest(unittest.IsolatedAsyncioTestCase):
    async def test_sends_findings_with_evidence_and_returns_stripped_summary(self) -> None:
        provider = _Provider()
        summary = await _run(provider)

        self.assertEqual(summary, "Root cause is X [logs-1].")
        messages, tools, max_tokens = provider.calls[0]
        self.assertEqual(tools, [])
        self.assertEqual(max_tokens, 1200)
        self.assertEqual([message["role"] for message in messages], ["system", "user"])
        self.assertIn("[source_id]", messages[0]["content"])
        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["environment"], "dev")
        self.assertEqual(payload["issue_key"], "ABC-1")
        self.assertEqual(payload["findings"][0]["evidence"],
                         [{"source_id": "logs-1", "tool": "logs__query_logs", "agent": "logs"}])
        self.assertEqual(payload["findings"][1]["limitations"], "Agent is not configured.")

    async def test_redactor_and_email_masking_apply_to_the_model_input(self) -> None:
        provider = _Provider()
        await _run(provider, question="Ask user@example.com about secret-value",
                   redactor=lambda text: text.replace("secret-value", "[REDACTED]"))

        user_text = provider.calls[0][0][1]["content"]
        self.assertNotIn("secret-value", user_text)
        self.assertNotIn("user@example.com", user_text)
        self.assertIn("[REDACTED_EMAIL]", user_text)

    async def test_summary_is_limited(self) -> None:
        summary = await _run(_Provider(ModelTurn("x" * 5000)))
        self.assertEqual(len(summary), 4000)

    async def test_provider_failure_hides_the_underlying_message(self) -> None:
        provider = _Provider(error=RuntimeError("private-provider-detail"))
        with self.assertRaises(ProviderError) as caught:
            await _run(provider)
        self.assertIn("RuntimeError", str(caught.exception))
        self.assertNotIn("private-provider-detail", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    async def test_tool_call_from_coordinator_is_rejected(self) -> None:
        turn = ModelTurn("", (ModelToolCall("call-1", "logs__query_logs", {}),))
        with self.assertRaisesRegex(ProviderError, "unexpected tool call"):
            await _run(_Provider(turn))


if __name__ == "__main__":
    unittest.main()
