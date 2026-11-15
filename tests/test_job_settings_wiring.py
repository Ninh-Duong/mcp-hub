"""Job settings reach the code that uses them (decision D10): the specialist round limit and result size, the
specialist's own message limit, and the prompt cache of the Anthropic provider. The coordinator's time limit is
covered by test_job_runner_hardening.py."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from mcp_hub.agents.config import AgentConfig
from mcp_hub.agents.specialists import SpecialistExecutor, SpecialistResult
from mcp_hub.jobs.runner import JobRunner, create_provider
from mcp_hub.settings import JobSettings

REQUEST = {"project": "orders", "question": "why", "env": "dev"}


def _runner(**limits: object) -> JobRunner:
    agents = {"logs": AgentConfig("logs", "openai", "test-model", "logs-test-key")}
    return JobRunner(
        store=MagicMock(), runtime=MagicMock(), projects={}, agents=agents,
        provider_factory=lambda config: object(), limits=JobSettings(**limits),
    )


class SpecialistLimitsTest(unittest.IsolatedAsyncioTestCase):
    async def test_the_executor_gets_rounds_and_result_size_from_settings(self) -> None:
        runner = _runner(specialist_max_rounds=3, tool_result_chars=1234, context_chars=5000)
        with patch("mcp_hub.jobs.runner.SpecialistExecutor") as executor_type:
            executor_type.return_value.run = AsyncMock(return_value=SpecialistResult("logs", "", (), "done"))
            await runner._run_specialist("job-1", "logs", REQUEST)
        kwargs = executor_type.call_args.kwargs
        self.assertEqual(kwargs["max_rounds"], 3)
        self.assertEqual(kwargs["tool_result_chars"], 1234)
        self.assertEqual(kwargs["context_chars"], 5000)

    async def test_the_executor_minimizes_its_task_with_its_own_result_size(self) -> None:
        executor = SpecialistExecutor(
            runtime=MagicMock(audit=None), provider=object(), save_result=MagicMock(),
            call_tool=AsyncMock(), tool_result_chars=50,
        )
        messages = await executor._initial_messages("logs", "x" * 500)
        self.assertLess(len(messages[1]["content"]), 200)


class PromptCacheTest(unittest.TestCase):
    def test_create_provider_passes_the_prompt_cache_to_anthropic(self) -> None:
        config = AgentConfig("logs", "anthropic", "test-model", "logs-test-key")
        with patch("mcp_hub.jobs.runner.provider_package_installed", return_value=True), \
                patch("mcp_hub.jobs.runner.AnthropicProvider") as provider_type:
            create_provider(config, prompt_cache=False)
        self.assertFalse(provider_type.call_args.kwargs["prompt_cache"])

    def test_the_job_runner_builds_anthropic_providers_with_its_limit(self) -> None:
        agents = {"logs": AgentConfig("logs", "anthropic", "test-model", "logs-test-key")}
        with patch("mcp_hub.jobs.runner.provider_package_installed", return_value=True), \
                patch("mcp_hub.jobs.runner.AnthropicProvider") as provider_type:
            JobRunner(
                store=MagicMock(), runtime=MagicMock(), projects={}, agents=agents,
                limits=JobSettings(prompt_cache=False),
            )
        self.assertFalse(provider_type.call_args.kwargs["prompt_cache"])


if __name__ == "__main__":
    unittest.main()
