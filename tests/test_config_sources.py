"""config_sources: a child's own `config_status` report clears required-settings gaps it covers."""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mcp.types import CallToolResult, TextContent
from starlette.applications import Starlette
from starlette.testclient import TestClient

from mcp_hub import child_requirements as req
from mcp_hub import hub_settings
from mcp_hub.audit import AuditLog
from mcp_hub.config import HubConfig, ServerSwitch
from mcp_hub.config_sources import CONFIG_STATUS_TOOL, ConfigSources, gaps_after_reports
from mcp_hub.dashboard.api import DashboardHandlers
from mcp_hub.dashboard.settings_api import SettingsHandlers, settings_payload
from mcp_hub.server import HubRuntime

LOGIN = req.MissingGroup("mssql_login", ("mssql.server", "mssql.password"))
UNNAMED = req.MissingGroup(None, ("x.one",))


def text_result(payload: object, *, is_error: bool = False) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload))], is_error=is_error)


class ConfigSourcesFakeClient:
    def __init__(self, result: object = None, *, delay: float = 0.0, error: Exception | None = None) -> None:
        self.result, self.delay, self.error = result, delay, error
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict) -> object:
        self.calls.append((name, arguments))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.result


class AuditCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.audit = AuditLog(Path(self.tmp.name) / "audit.jsonl")

    def tearDown(self) -> None:
        self.audit.close()
        self.tmp.cleanup()

    def events(self) -> list[dict]:
        path = Path(self.tmp.name) / "audit.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        return [event for event in map(json.loads, lines) if event.get("event") == "child_config_status"]


class ProbeTest(AuditCase):
    async def probe(self, client: ConfigSourcesFakeClient, tools: object = (CONFIG_STATUS_TOOL,), timeout: float = 1.0) -> ConfigSources:
        sources = ConfigSources(self.audit, timeout)
        await sources.probe("child", client, tools)
        return sources

    async def test_a_text_report_is_stored_and_audited_by_names_only(self) -> None:
        client = ConfigSourcesFakeClient(text_result({"groups": {"mssql_login": "local"}}))
        sources = await self.probe(client)
        self.assertEqual(client.calls, [(CONFIG_STATUS_TOOL, {})])
        self.assertEqual(sources.report("child"), {"mssql_login": "local"})
        self.assertEqual(self.events()[0]["groups"], {"mssql_login": "local"})
        self.assertTrue(self.events()[0]["ok"])

    async def test_structured_content_wins_over_text(self) -> None:
        result = CallToolResult(content=[TextContent(type="text", text="not json")],
                                structured_content={"groups": {"bitbucket": "env"}})
        self.assertEqual((await self.probe(ConfigSourcesFakeClient(result))).reports(), {"child": {"bitbucket": "env"}})

    async def test_a_child_without_the_tool_is_not_called(self) -> None:
        client = ConfigSourcesFakeClient(text_result({"groups": {"a": "local"}}))
        sources = await self.probe(client, tools=("other_tool",))
        self.assertEqual((client.calls, sources.reports(), self.events()), ([], {}, []))

    async def test_failures_keep_no_report_and_name_the_error_type(self) -> None:
        cases = {
            "timeout": ConfigSourcesFakeClient(text_result({"groups": {}}), delay=0.5),
            "RuntimeError": ConfigSourcesFakeClient(error=RuntimeError("boom secret")),
            "invalid_result": ConfigSourcesFakeClient(text_result({"groups": {"a": "local"}}, is_error=True)),
        }
        for error_type, client in cases.items():
            sources = await self.probe(client, timeout=0.05)
            self.assertEqual(sources.reports(), {}, error_type)
        self.assertEqual([event["error_type"] for event in self.events()], list(cases))
        self.assertNotIn("boom secret", (Path(self.tmp.name) / "audit.jsonl").read_text(encoding="utf-8"))

    async def test_bad_shapes_are_refused(self) -> None:
        for payload in ([], {"groups": []}, {"groups": {"a": "somewhere"}}, {"groups": {"Bad-Id": "local"}}):
            self.assertEqual((await self.probe(ConfigSourcesFakeClient(text_result(payload)))).reports(), {}, payload)
        no_text = CallToolResult(content=[])
        self.assertEqual((await self.probe(ConfigSourcesFakeClient(no_text))).reports(), {})
        broken = CallToolResult(content=[TextContent(type="text", text="{")])
        self.assertEqual((await self.probe(ConfigSourcesFakeClient(broken))).reports(), {})

    async def test_a_reconnect_replaces_the_previous_report(self) -> None:
        sources = await self.probe(ConfigSourcesFakeClient(text_result({"groups": {"a": "local"}})))
        await sources.probe("child", ConfigSourcesFakeClient(text_result({"groups": {"a": "unset"}})), (CONFIG_STATUS_TOOL,))
        self.assertEqual(sources.report("child"), {"a": "unset"})
        await sources.probe("child", ConfigSourcesFakeClient(), ())
        self.assertEqual(sources.report("child"), {})


class GapsTest(unittest.TestCase):
    def test_a_reported_source_clears_only_its_group(self) -> None:
        keys = {"sql": ("mssql.server", "mssql.password", "x.one"), "old": ("a.b",)}
        groups = {"sql": (LOGIN, UNNAMED)}
        self.assertEqual(gaps_after_reports(keys, groups, {"sql": {"mssql_login": "local"}}),
                         {"sql": ["x.one"], "old": ["a.b"]})
        self.assertEqual(gaps_after_reports(keys, groups, {"sql": {"mssql_login": "unset"}})["sql"],
                         ["mssql.server", "mssql.password", "x.one"])

    def test_a_child_with_every_group_reported_has_no_gap(self) -> None:
        self.assertEqual(gaps_after_reports({"sql": LOGIN.keys}, {"sql": (LOGIN,)}, {"sql": {"mssql_login": "env"}}), {})


class RuntimeTest(AuditCase):
    def runtime(self) -> HubRuntime:
        config = HubConfig(Path(self.tmp.name) / "hub.json", {}, (), incomplete={"sql": LOGIN.keys},
                           incomplete_groups={"sql": (LOGIN,)})
        return HubRuntime(config, self.audit)

    async def test_a_connected_child_report_clears_its_gap(self) -> None:
        runtime = self.runtime()
        self.assertEqual(runtime.config_incomplete(), {"sql": list(LOGIN.keys)})
        client = ConfigSourcesFakeClient(text_result({"groups": {"mssql_login": "local"}}))
        runtime._starting = True  # only the probe matters here, not route registration
        runtime.child_connected("sql", client, {CONFIG_STATUS_TOOL: SimpleNamespace()})
        await asyncio.gather(*runtime._background)
        self.assertEqual(runtime.config_incomplete(), {})
        self.assertEqual(runtime.config_source_reports(), {"sql": {"mssql_login": "local"}})

    def test_without_an_event_loop_the_file_gaps_stay(self) -> None:
        runtime = self.runtime()
        runtime._schedule_config_probe("sql", ConfigSourcesFakeClient(), {CONFIG_STATUS_TOOL: SimpleNamespace()})
        self.assertEqual(runtime.config_incomplete(), {"sql": list(LOGIN.keys)})


class SettingsPayloadTest(unittest.TestCase):
    def settings(self) -> hub_settings.HubSettings:
        switch = ServerSwitch("sql", True, missing=LOGIN.keys, requirements="declared", missing_groups=(LOGIN,))
        old = ServerSwitch("old", True, missing=("a.b",), requirements="declared")
        return hub_settings.HubSettings(False, (switch, old), True)

    def test_reported_groups_are_not_missing(self) -> None:
        payload = settings_payload(self.settings(), {"sql": {"mssql_login": "local"}})
        self.assertEqual([item["missing"] for item in payload["servers"]], [[], ["a.b"]])
        self.assertEqual(settings_payload(self.settings())["servers"][0]["missing"], list(LOGIN.keys))

    def test_the_dashboard_hands_the_runtime_reports_to_the_settings_route(self) -> None:
        store = SimpleNamespace(read_settings=self.settings)
        runtime = SimpleNamespace(config_source_reports=lambda: {"sql": {"mssql_login": "hub"}})
        handlers = DashboardHandlers(runtime, settings=SettingsHandlers(store=store))
        with TestClient(Starlette(routes=handlers.settings.routes())) as client:
            servers = client.get("/dashboard/api/settings").json()["servers"]
        self.assertEqual(servers[0]["missing"], [])

    def test_an_explicit_report_source_is_kept(self) -> None:
        handlers = SettingsHandlers(source_reports=dict)
        first = handlers.source_reports
        handlers.use_source_reports(lambda: {})
        self.assertIs(handlers.source_reports, first)


if __name__ == "__main__":
    unittest.main()
