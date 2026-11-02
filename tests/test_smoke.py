import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

from mcp.types import CallToolResult, TextContent

from scripts.smoke import (CHECKS, _has_organizations, _has_project_aliases, _hub_report, _is_connected, _not_set, _probe,
                           _report, main)


class SmokeChecksTest(unittest.TestCase):
    def setUp(self) -> None:
        self.check = _has_organizations

    def test_no_ready_children_is_a_valid_hub_startup(self) -> None:
        with patch("scripts.smoke.load_config", return_value=SimpleNamespace(servers={})), patch("builtins.print") as output:
            with self.assertRaises(SystemExit) as result:
                main()
        self.assertEqual(result.exception.code, 0)
        output.assert_called_once()

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

    def test_probe_skips_environments_without_an_account(self) -> None:
        providers = '{"openobserve_environments":[{"env":"dev","configured":true},{"env":"stg","configured":false}]}'
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.list_tools.return_value = SimpleNamespace(tools=[
            SimpleNamespace(name="logs__list_log_providers"), SimpleNamespace(name="logs__list_openobserve_organizations"),
        ])
        client.list_prompts.return_value = SimpleNamespace(prompts=[SimpleNamespace(name="mcp-server-logs")])
        client.call_tool.side_effect = lambda tool, _args: CallToolResult(content=[TextContent(
            type="text", text=providers if tool == "logs__list_log_providers" else '{"organizations":[]}',
        )])

        with patch("scripts.smoke.stdio_client", return_value=object()), patch("scripts.smoke.Client", return_value=client):
            _, calls = asyncio.run(_probe(["mcp-server-logs"]))

        self.assertEqual(calls["mcp-server-logs"], {"dev": True, "stg": None, "prod": True})
        self.assertNotIn(call("logs__list_openobserve_organizations", {"env": "stg"}), client.call_tool.await_args_list)

    def test_report_passes_with_unset_environment_and_warns_when_all_are_unset(self) -> None:
        connected = [{"server": "mcp-server-logs", "event": "server.connected", "tools_count": 12}]
        state, detail = _report("mcp-server-logs", {"mcp-server-logs"}, {"mcp-server-logs": {"dev": True, "stg": None, "prod": True}}, connected)
        self.assertEqual(state, "PASS")
        self.assertIn("dev=ok stg=unset prod=ok", detail)
        state, _ = _report("mcp-server-logs", {"mcp-server-logs"}, {"mcp-server-logs": {"dev": None, "stg": None, "prod": None}}, connected)
        self.assertEqual(state, "WARN")  # no account yet: the user fills it in the child's repository

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

    def test_pr_companion_check_uses_the_bitbucket_health_tool(self) -> None:
        self.assertEqual(CHECKS["mcp-pr-companion"], (("bitbucket", "pr__check_bitbucket_connection", {}, _is_connected),))

    def test_pr_companion_check_requires_connected_true(self) -> None:
        ok = CallToolResult(content=[TextContent(type="text", text='{"connected":true}')])
        self.assertTrue(_is_connected(ok))
        for text in ('{"connected":false}', '{"connected":"true"}', "{}", "not json"):
            with self.subTest(text=text):
                self.assertFalse(_is_connected(CallToolResult(content=[TextContent(type="text", text=text)])))
        self.assertFalse(_is_connected(CallToolResult(content=[TextContent(type="text", text='{"connected":true}')], isError=True)))

    def test_source_check_accepts_aliases_but_never_requires_a_registered_root(self) -> None:
        self.assertEqual(CHECKS["mcp-read-source"][0][1], "source__check_connection")
        self.assertTrue(_has_project_aliases(CallToolResult(content=[TextContent(type="text", text='{"configured_projects":[]}')])))
        self.assertTrue(_has_project_aliases(CallToolResult(content=[TextContent(type="text", text='{"configured_projects":["app"]}')])))
        self.assertFalse(_has_project_aliases(CallToolResult(content=[TextContent(type="text", text='{"configured_projects":"root/path"}')])))

    def test_report_shows_status_for_all_environments(self) -> None:
        state, detail = _report(
            "mcp-server-logs",
            {"mcp-server-logs"},
            {"mcp-server-logs": {"dev": True, "stg": True, "prod": True}},
            [{"server": "mcp-server-logs", "event": "server.connected", "tools_count": 12}],
        )
        self.assertEqual(state, "PASS")
        self.assertEqual(detail, "12 tools, logs__list_openobserve_organizations dev=ok stg=ok prod=ok")

    def test_report_fails_when_one_environment_is_unavailable(self) -> None:
        state, detail = _report(
            "mcp-server-logs",
            {"mcp-server-logs"},
            {"mcp-server-logs": {"dev": True, "stg": False, "prod": True}},
            [{"server": "mcp-server-logs", "event": "server.connected", "tools_count": 12}],
        )
        self.assertEqual(state, "FAIL")
        self.assertIn("dev=ok stg=failed prod=ok", detail)


def text_result(text: str, error: bool = False) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], isError=error)


def fake_client(tool_names: list[str], children: list[str], answers: dict) -> AsyncMock:
    """A Hub client whose tools answer from `answers` (a result, or an exception to raise)."""
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.list_tools.return_value = SimpleNamespace(tools=[SimpleNamespace(name=name) for name in tool_names])
    client.list_prompts.return_value = SimpleNamespace(prompts=[SimpleNamespace(name=name) for name in children])

    async def call_tool(tool, _args):
        answer = answers[tool]
        if isinstance(answer, Exception):
            raise answer
        return answer

    client.call_tool.side_effect = call_tool
    return client


def probe(client: AsyncMock, children: list[str]):
    with patch("scripts.smoke.stdio_client", return_value=object()), patch("scripts.smoke.Client", return_value=client):
        return asyncio.run(_probe(children))


def verdict(server_id: str, calls: dict) -> str:
    events = [{"server": server_id, "event": "server.connected", "tools_count": 3}]
    return _report(server_id, {server_id}, calls, events)[0]


HUB_OK = text_result('{"connected":1,"children":{}}')


class SmokeWarningTest(unittest.TestCase):
    """An account the user has not filled in is a WARN; any other problem stays a FAIL."""

    def check(self, server_id: str, tool: str, result: CallToolResult) -> str:
        client = fake_client([tool, "hub__status"], [server_id], {tool: result, "hub__status": HUB_OK})
        _, calls = probe(client, [server_id])
        return verdict(server_id, calls)

    def test_jira_without_an_account_warns_but_any_other_failure_fails(self) -> None:
        tool = "jira__check_connection"
        self.assertEqual(self.check("mcp-jira-sync", tool, text_result("❌ Jira credentials are not configured yet.")), "WARN")
        self.assertEqual(self.check("mcp-jira-sync", tool, text_result("❌ Jira rejected the credentials (HTTP 401).")), "FAIL")
        self.assertEqual(self.check("mcp-jira-sync", tool, text_result("✅ Connected to Jira.")), "PASS")

    def test_pr_companion_without_an_account_warns_but_rejected_credentials_fail(self) -> None:
        tool = "pr__check_bitbucket_connection"
        missing = text_result("Error: No Bitbucket credentials: set BITBUCKET_EMAIL and BITBUCKET_API_TOKEN.", error=True)
        self.assertEqual(self.check("mcp-pr-companion", tool, missing), "WARN")
        rejected = text_result("Error: Bitbucket rejected the credentials (HTTP 401).", error=True)
        self.assertEqual(self.check("mcp-pr-companion", tool, rejected), "FAIL")
        self.assertEqual(self.check("mcp-pr-companion", tool, text_result('{"connected":true}')), "PASS")

    def test_source_reader_warns_until_a_project_folder_is_registered(self) -> None:
        tool = "source__check_connection"
        self.assertEqual(self.check("mcp-read-source", tool, text_result('{"configured_projects":[]}')), "WARN")
        self.assertEqual(self.check("mcp-read-source", tool, text_result('{"configured_projects":["app"]}')), "PASS")
        self.assertEqual(self.check("mcp-read-source", tool, text_result("not json")), "FAIL")

    def test_not_set_detection_is_limited_to_the_documented_markers(self) -> None:
        self.assertTrue(_not_set(text_result("Jira credentials are NOT CONFIGURED")))
        self.assertFalse(_not_set(text_result("Bitbucket is unreachable")))
        self.assertFalse(_not_set(text_result('{"configured_projects":["app"]}')))

    def test_a_child_that_raises_fails_alone_and_the_others_are_still_checked(self) -> None:
        children = ["mcp-jira-sync", "mcp-read-source"]
        client = fake_client(
            ["jira__check_connection", "source__check_connection", "hub__status"], children,
            {"jira__check_connection": RuntimeError("boom"),
             "source__check_connection": text_result('{"configured_projects":["app"]}'), "hub__status": HUB_OK},
        )
        _, calls = probe(client, children)
        self.assertEqual(calls["mcp-jira-sync"], {"default": False})
        self.assertEqual(calls["mcp-read-source"], {"projects": True})
        self.assertEqual(verdict("mcp-jira-sync", calls), "FAIL")
        self.assertEqual(verdict("mcp-read-source", calls), "PASS")


class SmokeHubTest(unittest.TestCase):
    def test_the_hub_passes_when_hub_status_answers_and_fails_when_it_does_not(self) -> None:
        _, calls = probe(fake_client(["hub__status"], [], {"hub__status": HUB_OK}), [])
        self.assertEqual(_hub_report(set(), calls, ["mcp-jira-sync"]), ("PASS", "hub__status ok, 0/1 children connected"))
        for tools, answers in ((["hub__status"], {"hub__status": text_result("{}")}),
                               (["hub__status"], {"hub__status": RuntimeError("down")}), ([], {})):
            with self.subTest(tools=tools):
                _, calls = probe(fake_client(tools, [], answers), [])
                self.assertEqual(_hub_report(set(), calls, [])[0], "FAIL")
        self.assertEqual(_hub_report(set(), {}, [])[0], "FAIL")  # the Hub exited before answering


class SmokeMainTest(unittest.TestCase):
    def run_main(self, calls: dict, connected: set[str]) -> tuple[int, list[str]]:
        servers = {name: None for name in ("mcp-jira-sync", "mcp-read-source")}
        events = [{"server": name, "event": "server.connected", "tools_count": 3} for name in servers]
        with patch("scripts.smoke.load_config", return_value=SimpleNamespace(servers=servers)), \
             patch("scripts.smoke._probe", new=AsyncMock(return_value=(connected, calls))), \
             patch("scripts.smoke._events_since", return_value=events), patch("builtins.print") as output:
            with self.assertRaises(SystemExit) as result:
                main()
        return result.exception.code, [str(item.args[0]) for item in output.call_args_list]

    def test_warnings_do_not_fail_the_run_and_point_to_the_child_repository(self) -> None:
        calls = {"mcp-jira-sync": {"default": None}, "mcp-read-source": {"projects": True}, "mcp-hub": {"status": True}}
        code, lines = self.run_main(calls, {"mcp-jira-sync", "mcp-read-source"})
        self.assertEqual(code, 0)
        self.assertRegex(lines[0], r"mcp-jira-sync\s+WARN")
        self.assertRegex(lines[2], r"mcp-hub\s+PASS")  # the Hub is reported after the children
        self.assertIn("WARN mcp-jira-sync: fill in its account or project folders in ../mcp-jira-sync", "\n".join(lines))

    def test_one_failure_fails_the_run_after_every_child_was_reported(self) -> None:
        calls = {"mcp-jira-sync": {"default": False}, "mcp-read-source": {"projects": True}, "mcp-hub": {"status": True}}
        code, lines = self.run_main(calls, {"mcp-jira-sync", "mcp-read-source"})
        self.assertEqual(code, 1)
        self.assertRegex(lines[0], r"mcp-jira-sync\s+FAIL")
        self.assertRegex(lines[1], r"mcp-read-source\s+PASS")
        self.assertRegex(lines[2], r"mcp-hub\s+PASS")

    def test_a_hub_that_exited_fails_every_line(self) -> None:
        code, lines = self.run_main({}, set())
        self.assertEqual(code, 1)
        self.assertEqual([line.split()[1] for line in lines[:3]], ["FAIL", "FAIL", "FAIL"])


if __name__ == "__main__":
    unittest.main()
