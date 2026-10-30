"""Investigation readiness: jobs need Windows DPAPI, and each provider needs its SDK package installed."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mcp_hub.agents import providers
from mcp_hub.agents.config import AGENT_ROLES, AgentConfig
from mcp_hub.jobs.projects import ProjectConfig
from mcp_hub.jobs.runner import JobRunner, create_provider
from mcp_hub.jobs.store import JobStore
from mcp_hub.security import dpapi
from mcp_hub.security.dpapi import WINDOWS_ONLY_MESSAGE, DPAPIProtector


class _PlainProtector:
    available = True

    def protect(self, data: bytes) -> bytes:
        return data

    def unprotect(self, data: bytes) -> bytes:
        return data


def _agents(provider: str | None = None) -> dict[str, AgentConfig]:
    return {role: AgentConfig(role, provider, "example-model" if provider else None,
                              "test-key" if provider else None) for role in AGENT_ROLES}


class _RunnerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def runner(self, protector, agents: dict[str, AgentConfig] | None = None, **kwargs) -> JobRunner:
        store = JobStore(Path(self.tmp.name) / "jobs.sqlite3", protector=protector)
        self.addCleanup(store.close)
        return JobRunner(
            store=store, runtime=SimpleNamespace(), agents=agents or _agents(),
            projects={"orders": ProjectConfig("orders", ("dev",), default_env="dev")}, **kwargs,
        )


class WindowsOnlyJobsTest(_RunnerCase):
    def test_jobs_fail_fast_off_windows_with_a_clear_message(self) -> None:
        with patch.object(dpapi, "os", SimpleNamespace(name="posix")):
            runner = self.runner(DPAPIProtector())
            self.assertFalse(runner.store.encryption_available)
            result = asyncio.run(runner.call_tool("hub__submit_issue", {"project": "orders", "question": "why?"}))
            status = runner.agent_status()

        self.assertTrue(result.is_error)
        self.assertEqual(result.content[0].text, WINDOWS_ONLY_MESSAGE)
        self.assertEqual(runner.list_jobs(), [])
        self.assertEqual({entry["unavailable"] for entry in status.values()}, {"windows-only"})
        self.assertEqual({entry["reason"] for entry in status.values()}, {WINDOWS_ONLY_MESSAGE})
        self.assertFalse(any(entry["configured"] for entry in status.values()))

    def test_protector_availability_follows_the_platform(self) -> None:
        with patch.object(dpapi, "os", SimpleNamespace(name="nt")):
            self.assertTrue(DPAPIProtector().available)
        with patch.object(dpapi, "os", SimpleNamespace(name="posix")):
            self.assertFalse(DPAPIProtector().available)


class ProviderPackageTest(_RunnerCase):
    def test_missing_package_is_reported_instead_of_failing_later(self) -> None:
        with patch.object(providers.importlib.util, "find_spec", return_value=None):
            self.assertFalse(providers.provider_package_installed("openai"))
            with self.assertRaisesRegex(ValueError, providers.PACKAGE_MISSING_MESSAGE):
                create_provider(AgentConfig("logs", "openai", "example-model", "test-key"))
            runner = self.runner(_PlainProtector(), _agents("openai"))
            status = runner.agent_status()

        self.assertEqual(runner.providers, {})
        self.assertEqual({entry["unavailable"] for entry in status.values()}, {"package-missing"})
        self.assertEqual({entry["reason"] for entry in status.values()}, {providers.PACKAGE_MISSING_MESSAGE})

    def test_installed_package_is_ready_and_unknown_or_broken_parents_are_not(self) -> None:
        self.assertFalse(providers.provider_package_installed("unknown"))
        with patch.object(providers.importlib.util, "find_spec", side_effect=ModuleNotFoundError("google")):
            self.assertFalse(providers.provider_package_installed("gemini"))
        with patch.object(providers.importlib.util, "find_spec", return_value=object()):
            runner = self.runner(_PlainProtector(), _agents("anthropic"))
            status = runner.agent_status()
        self.assertEqual(set(runner.providers), set(AGENT_ROLES))
        self.assertEqual({(entry["configured"], entry["unavailable"]) for entry in status.values()}, {(True, None)})


if __name__ == "__main__":
    unittest.main()
