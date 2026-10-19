import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

from mcp.types import CallToolResult, TextContent

from scripts.smoke import CHECKS, _has_organizations, _probe, _report


class SmokeChecksTest(unittest.TestCase):
    def setUp(self) -> None:
        self.check = _has_organizations

    def test_logs_check_runs_for_each_openobserve_environment(self) -> None:
        checks = CHECKS["mcp-server-logs"]
        self.assertEqual([label for label, *_ in checks], ["dev", "stg", "prod"])
        self.assertEqual({tool for _, tool, _, _ in checks}, {"logs__list_openobserve_organizations"})
        self.assertEqual([args for _, _, args, _ in checks], [{"env": "dev"}, {"env": "stg"}, {"env": "prod"}])

    def test_probe_calls_the_read_only_check_for_each_environment(self) -> None:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.list_tools.return_value = SimpleNamespace(tools=[SimpleNamespace(name="logs__list_openobserve_organizations")])
        client.list_prompts.return_value = SimpleNamespace(prompts=[SimpleNamespace(name="mcp-server-logs")])
        client.call_tool.return_value = CallToolResult(content=[TextContent(type="text", text='{"organizations":[]}')])

        with patch("scripts.smoke.stdio_client", return_value=object()), patch("scripts.smoke.Client", return_value=client):
            connected, calls = asyncio.run(_probe(["mcp-server-logs"]))

        self.assertEqual(connected, {"mcp-server-logs"})
        self.assertEqual(calls["mcp-server-logs"], {"dev": True, "stg": True, "prod": True})
        self.assertEqual(client.call_tool.await_args_list, [
            call("logs__list_openobserve_organizations", {"env": "dev"}),
            call("logs__list_openobserve_organizations", {"env": "stg"}),
            call("logs__list_openobserve_organizations", {"env": "prod"}),
        ])

    def test_logs_check_accepts_organizations_payload(self) -> None:
        result = CallToolResult(content=[TextContent(type="text", text='{"organizations":[]}')])
        self.assertTrue(self.check(result))

    def test_logs_check_rejects_malformed_or_wrong_payload(self) -> None:
        for text in ("not json", '{"organizations":"unexpected"}', "{}"):
            with self.subTest(text=text):
                result = CallToolResult(content=[TextContent(type="text", text=text)])
                self.assertFalse(self.check(result))

    def test_logs_check_rejects_error_result_even_with_valid_payload(self) -> None:
        result = CallToolResult(content=[TextContent(type="text", text='{"organizations":[]}')], isError=True)
        self.assertFalse(self.check(result))

    def test_report_shows_status_for_all_environments(self) -> None:
        ok, detail = _report(
            "mcp-server-logs",
            {"mcp-server-logs"},
            {"mcp-server-logs": {"dev": True, "stg": True, "prod": True}},
            [{"server": "mcp-server-logs", "event": "server.connected", "tools_count": 12}],
        )
        self.assertTrue(ok)
        self.assertEqual(detail, "12 tools, logs__list_openobserve_organizations dev=ok stg=ok prod=ok")

    def test_report_fails_when_one_environment_is_unavailable(self) -> None:
        ok, detail = _report(
            "mcp-server-logs",
            {"mcp-server-logs"},
            {"mcp-server-logs": {"dev": True, "stg": False, "prod": True}},
            [{"server": "mcp-server-logs", "event": "server.connected", "tools_count": 12}],
        )
        self.assertFalse(ok)
        self.assertIn("dev=ok stg=failed prod=ok", detail)


if __name__ == "__main__":
    unittest.main()
