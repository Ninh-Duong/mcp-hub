from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock


TOOL = {"name": "logs__search", "description": "Search logs", "input_schema": {"type": "object"}}


class ModelProviderTest(unittest.TestCase):
    def test_openai_normalizes_final_text_and_multiple_function_calls(self) -> None:
        from mcp_hub.agents.providers import OpenAIProvider

        response = SimpleNamespace(output=[
            SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text="Found evidence")]),
            SimpleNamespace(type="function_call", call_id="c1", name="logs__search", arguments='{"q":"500"}'),
            SimpleNamespace(type="function_call", call_id="c2", name="source__search", arguments='{"q":"retry"}'),
        ])
        client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=response)))
        provider = OpenAIProvider("test-model", "test-only-key", client=client)

        turn = asyncio.run(provider.complete([{"role": "user", "content": "find the cause"}], [TOOL]))

        self.assertEqual(turn.text, "Found evidence")
        self.assertEqual([call.call_id for call in turn.tool_calls], ["c1", "c2"])
        sent = client.responses.create.await_args.kwargs
        self.assertEqual(sent["model"], "test-model")
        self.assertEqual(sent["tools"][0]["name"], "logs__search")

    def test_anthropic_normalizes_text_and_tool_use(self) -> None:
        from mcp_hub.agents.providers import AnthropicProvider

        response = SimpleNamespace(content=[
            SimpleNamespace(type="text", text="Need one lookup"),
            SimpleNamespace(type="tool_use", id="tool-1", name="logs__search", input={"q": "500"}),
        ])
        client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(return_value=response)))
        provider = AnthropicProvider("test-model", "test-only-key", client=client)

        turn = asyncio.run(provider.complete([{"role": "user", "content": "find errors"}], [TOOL]))

        self.assertEqual(turn.text, "Need one lookup")
        self.assertEqual(turn.tool_calls[0].arguments, {"q": "500"})
        self.assertEqual(client.messages.create.await_args.kwargs["tools"][0]["name"], "logs__search")

    def test_gemini_normalizes_text_and_function_calls(self) -> None:
        from mcp_hub.agents.providers import GeminiProvider

        response = SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[
            SimpleNamespace(text="Found a matching service"),
            SimpleNamespace(function_call=SimpleNamespace(id="g1", name="logs__search", args={"q": "500"})),
        ]))])
        models = SimpleNamespace(generate_content=AsyncMock(return_value=response))
        client = SimpleNamespace(aio=SimpleNamespace(models=models))
        provider = GeminiProvider("test-model", "test-only-key", client=client)

        turn = asyncio.run(provider.complete([{"role": "user", "content": "find errors"}], [TOOL]))

        self.assertEqual(turn.text, "Found a matching service")
        self.assertEqual(turn.tool_calls[0].call_id, "g1")
        self.assertEqual(models.generate_content.await_args.kwargs["model"], "test-model")

    def test_malformed_arguments_and_provider_errors_are_sanitized(self) -> None:
        from mcp_hub.agents.providers import OpenAIProvider, ProviderError

        malformed = SimpleNamespace(output=[SimpleNamespace(
            type="function_call", call_id="bad", name="logs__search", arguments="{bad",
        )])
        provider = OpenAIProvider(
            "test-model", "do-not-leak-this-key",
            client=SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=malformed))),
        )
        with self.assertRaises(ProviderError):
            asyncio.run(provider.complete([], [TOOL]))

        failing = OpenAIProvider(
            "test-model", "do-not-leak-this-key",
            client=SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("do-not-leak-this-key")))),
        )
        with self.assertRaises(ProviderError) as caught:
            asyncio.run(failing.complete([], [TOOL]))
        self.assertNotIn("do-not-leak-this-key", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
