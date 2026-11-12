from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from mcp_hub import service
from mcp_hub.activity_ui import ActivityUi
from mcp_hub.bitnet import BitnetExtension
from mcp_hub.bitnet.condense import BitnetCondenser
from mcp_hub.bitnet.planner import BitnetPlanAssistant
from mcp_hub.bitnet.setup import SETUP_EXTENSION, BitnetSetup
from mcp_hub.bitnet.summarize import BitnetDraftSummarizer
from mcp_hub.extensions import ACTIVITY_UI, BITNET
from mcp_hub.full_results import FullResultStore
from mcp_hub.settings import BitnetSettings, Settings


class ServiceExtensionWiringTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.audit = MagicMock()
        self.settings = Settings(bitnet=BitnetSettings(port=8080))
        self.runtime = SimpleNamespace(
            settings=self.settings,
            plan_assistant=None,
            condenser=None,
            full_results=None,
            child_log_dir=self.root / "logs" / "children",
            observers=(),
            add_observer=lambda o: None,
            activity_source=None,
            processing_observer=None,
            audit=self.audit,
        )
        self.job_runner = SimpleNamespace(
            plan_assistant=None,
            condenser=None,
            summarizer=None,
            processing_observer=None,
            close=AsyncMock(),
        )

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()

    def test_bitnet_installs_and_wires_runtime_and_job_runner(self) -> None:
        installed = service.install_extensions(
            frozenset({BITNET}),
            self.runtime,
            job_runner=self.job_runner,
            root=self.root,
            audit=self.audit,
        )
        self.assertIn(BITNET, installed)
        ext = installed[BITNET]
        self.assertIsInstance(ext, BitnetExtension)
        self.assertIsInstance(self.runtime.plan_assistant, BitnetPlanAssistant)
        self.assertIsInstance(self.runtime.condenser, BitnetCondenser)
        self.assertIsInstance(self.runtime.full_results, FullResultStore)
        self.assertIs(self.runtime.processing_observer, ext.setup.processing_feed)
        self.assertIs(self.job_runner.plan_assistant, self.runtime.plan_assistant)
        self.assertIs(self.job_runner.condenser, self.runtime.condenser)
        self.assertIsInstance(self.job_runner.summarizer, BitnetDraftSummarizer)
        self.assertIs(self.job_runner.processing_observer, ext.setup.processing_feed)

    def test_no_bitnet_wires_core_condenser_and_leaves_llm_hooks_none(self) -> None:
        installed = service.install_extensions(
            frozenset(),
            self.runtime,
            job_runner=self.job_runner,
            root=self.root,
            audit=self.audit,
        )
        from mcp_hub.condense import RuleCondenser
        # Only BitNet's install state and route, plus deterministic core condensing; no LLM hooks.
        self.assertEqual(list(installed), [SETUP_EXTENSION])
        self.assertIsInstance(installed[SETUP_EXTENSION], BitnetSetup)
        self.assertIsNone(installed[SETUP_EXTENSION].sidecar)
        self.assertIsNone(self.runtime.plan_assistant)
        self.assertIsNone(self.runtime.processing_observer)
        self.assertIsInstance(self.runtime.condenser, RuleCondenser)
        self.assertIsInstance(self.runtime.full_results, FullResultStore)
        self.assertIsNone(self.job_runner.plan_assistant)
        self.assertIs(self.job_runner.condenser, self.runtime.condenser)
        self.assertIsNone(self.job_runner.summarizer)
        self.assertIsNone(self.job_runner.processing_observer)

    def test_activity_ui_and_bitnet_together_both_install(self) -> None:
        installed = service.install_extensions(
            frozenset({ACTIVITY_UI, BITNET}),
            self.runtime,
            job_runner=self.job_runner,
            root=self.root,
            audit=self.audit,
        )
        self.assertIn(ACTIVITY_UI, installed)
        self.assertIn(BITNET, installed)
        self.assertIsInstance(installed[ACTIVITY_UI], ActivityUi)
        self.assertIsInstance(installed[BITNET], BitnetExtension)

    async def test_serve_close_order_runner_then_extensions(self) -> None:
        order: list[str] = []
        runner = SimpleNamespace(
            tool_definitions=lambda: [],
            close=AsyncMock(side_effect=lambda: order.append("runner_close")),
        )
        ext1 = SimpleNamespace(
            start=AsyncMock(side_effect=lambda: order.append("ext1_start")),
            close=AsyncMock(side_effect=lambda: order.append("ext1_close")),
            dashboard_extension=lambda: MagicMock(),
        )
        ext2 = SimpleNamespace(
            start=lambda: order.append("ext2_start"),
            close=AsyncMock(side_effect=lambda: order.append("ext2_close")),
            dashboard_extension=lambda: MagicMock(),
        )
        extensions = {"ext1": ext1, "ext2": ext2}
        runtime = SimpleNamespace(
            audit=MagicMock(),
            config=SimpleNamespace(servers={}),
            public_tools=[],
            settings=self.settings,
        )

        async def fake_serve(*args: Any, **kwargs: Any) -> None:
            order.append("serve")

        with patch.object(service, "_build_http_app", return_value=object()), \
                patch.object(service, "_serve_until_stopped", fake_serve):
            await service._serve(runtime, runner, extensions, "127.0.0.1", 8765, ("tok", "adm"), object())

        self.assertEqual(order, ["ext1_start", "ext2_start", "serve", "runner_close", "ext1_close", "ext2_close"])
        runtime.audit.write.assert_any_call("hub.started", servers=[], tool_count=0, transport="streamable-http", host="127.0.0.1", port=8765, extensions=["ext1", "ext2"])
        runtime.audit.write.assert_any_call("hub.stopped")


if __name__ == "__main__":
    unittest.main()
