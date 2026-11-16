"""The mixins that HubRuntime is built from (call_observers, child_lifecycle, planning/hub_plan, results/runtime).

Each module is imported here directly. The checks cover the behaviour each mixin owns after the split, and that
HubRuntime really inherits from it.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mcp.types import Tool

from mcp_hub.audit import AuditLog
from mcp_hub.call_observers import CallObserverMixin, ChildCallState
from mcp_hub.child_lifecycle import ChildLifecycleMixin
from mcp_hub.config import HubConfig, ServerConfig, ToolRoute
from mcp_hub.planning.hub_plan import PlanningMixin
from mcp_hub.results.runtime import FULL_RESULT_TOOL, ResultsMixin, full_result_tool
from mcp_hub.server import HubRuntime
from mcp_hub.settings import PolicySettings, Settings

ALPHA = "alpha-server"
ROUTE = ToolRoute("feature", ALPHA, "count", "alpha-server__count")


class _FailingObserver:
    def call_started(self, *args, **kwargs):
        raise RuntimeError("secret detail that must not reach the log")


class _Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log_path = Path(self.tmp.name) / "audit.jsonl"
        self.audit = AuditLog(self.log_path)
        self.addCleanup(self.audit.close)
        child = ServerConfig(ALPHA, "python", (), Path(self.tmp.name), {}, (), timeout_seconds=60.0)
        config = HubConfig(Path(self.tmp.name) / "hub.json", {ALPHA: child}, (ROUTE,))
        self.runtime = HubRuntime(config, self.audit, Settings(policy=PolicySettings(mode="warn")))
        self.addCleanup(self.runtime.events.close)

    def log_lines(self) -> list[dict]:
        self.audit.close()
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines() if line.strip()]


class HubRuntimeMixinsTest(_Fixture):
    def test_hub_runtime_is_built_from_every_mixin(self) -> None:
        for mixin in (CallObserverMixin, ChildLifecycleMixin, PlanningMixin, ResultsMixin):
            with self.subTest(mixin=mixin.__name__):
                self.assertTrue(issubclass(HubRuntime, mixin))


class CallObserverMixinTest(_Fixture):
    def test_a_failing_observer_is_logged_by_type_only_and_the_call_goes_on(self) -> None:
        self.runtime.observers = (_FailingObserver(),)
        tokens = self.runtime._observe_start(ROUTE, "Cursor IDE", None)
        self.assertEqual(tokens, (None,))  # the call still starts; the failed observer contributes no token
        events = [entry for entry in self.log_lines() if entry.get("event") == "extension.observer.failed"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["error_type"], "RuntimeError")
        self.assertNotIn("secret detail", json.dumps(events[0]))

    def test_the_call_state_carries_route_request_tokens_and_start(self) -> None:
        state = ChildCallState(ROUTE, "req-1", None, 1.5)
        self.assertEqual((state.route, state.request_id, state.tokens, state.started), (ROUTE, "req-1", None, 1.5))


class PlanningMixinTest(_Fixture):
    def test_the_policy_mode_comes_from_settings(self) -> None:
        self.assertEqual(self.runtime.policy_mode, "warn")


class ResultsMixinTest(_Fixture):
    def test_the_full_result_tool_has_the_documented_name(self) -> None:
        self.assertEqual(full_result_tool().name, FULL_RESULT_TOOL)
        self.assertIsInstance(full_result_tool(), Tool)


if __name__ == "__main__":
    unittest.main()
