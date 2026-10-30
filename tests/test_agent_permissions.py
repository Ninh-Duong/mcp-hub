from __future__ import annotations

import unittest
from types import SimpleNamespace


READ_ONLY = SimpleNamespace(read_only_hint=True)
WRITE = SimpleNamespace(read_only_hint=False)


def _tool(name: str, annotations=READ_ONLY) -> SimpleNamespace:
    return SimpleNamespace(name=name, annotations=annotations)


class AgentPermissionsTest(unittest.TestCase):
    def test_specialists_receive_only_their_child_tools_and_coordinator_gets_none(self) -> None:
        from mcp_hub.agents.specialists import tools_for_role

        tools = [
            _tool("logs__query_logs"),
            _tool("source__search"),
            _tool("jira__jira_read_ticket"),
            _tool("pr__get_pr_context_pack"),
            _tool("hub__status"),
        ]
        self.assertEqual([tool.name for tool in tools_for_role("logs", tools)], ["logs__query_logs"])
        self.assertEqual([tool.name for tool in tools_for_role("source", tools)], ["source__search"])
        self.assertEqual([tool.name for tool in tools_for_role("jira", tools)], ["jira__jira_read_ticket"])
        self.assertEqual([tool.name for tool in tools_for_role("pr", tools)], ["pr__get_pr_context_pack"])
        self.assertEqual(tools_for_role("coordinator", tools), [])

    def test_unknown_role_has_no_tool_access(self) -> None:
        from mcp_hub.agents.specialists import tools_for_role

        self.assertEqual(tools_for_role("unknown", [_tool("logs__query_logs")]), [])

    def test_tools_without_a_read_only_hint_are_withheld_by_default(self) -> None:
        from mcp_hub.agents.specialists import tools_for_role

        tools = [
            _tool("logs__query_logs"),
            _tool("logs__export_openobserve_logs", WRITE),
            SimpleNamespace(name="logs__unannotated"),
            {"name": "logs__dict_tool", "annotations": {"readOnlyHint": True}},
        ]
        self.assertEqual([tool.name for tool in tools_for_role("logs", tools)], ["logs__query_logs"])

    def test_policy_offers_read_only_tools_and_the_cache_refresh_allowlist_only(self) -> None:
        from mcp_hub.agents.specialists import tools_for_role
        from mcp_hub.jobs.policy import SideEffectPolicy

        policy = SideEffectPolicy()
        tools = [
            _tool("jira__read_ticket"),
            _tool("jira__sync_ticket", WRITE),
            _tool("jira__update_ticket", WRITE),
            SimpleNamespace(name="jira__comment"),
        ]
        offered = [tool.name for tool in tools_for_role("jira", tools, policy.offers_tool)]
        self.assertEqual(offered, ["jira__read_ticket", "jira__sync_ticket"])
        self.assertTrue(policy.offers_tool({"name": "pr__sync_my_open_prs"}))
        self.assertFalse(policy.offers_tool({"name": "pr__merge", "annotations": {"readOnlyHint": False}}))


if __name__ == "__main__":
    unittest.main()
