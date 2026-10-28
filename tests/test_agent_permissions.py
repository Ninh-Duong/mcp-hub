from __future__ import annotations

import unittest
from types import SimpleNamespace


class AgentPermissionsTest(unittest.TestCase):
    def test_specialists_receive_only_their_child_tools_and_coordinator_gets_none(self) -> None:
        from mcp_hub.agents.specialists import tools_for_role

        tools = [
            SimpleNamespace(name="logs__query_logs"),
            SimpleNamespace(name="source__search"),
            SimpleNamespace(name="jira__jira_read_ticket"),
            SimpleNamespace(name="pr__get_pr_context_pack"),
            SimpleNamespace(name="hub__status"),
        ]
        self.assertEqual([tool.name for tool in tools_for_role("logs", tools)], ["logs__query_logs"])
        self.assertEqual([tool.name for tool in tools_for_role("source", tools)], ["source__search"])
        self.assertEqual([tool.name for tool in tools_for_role("jira", tools)], ["jira__jira_read_ticket"])
        self.assertEqual([tool.name for tool in tools_for_role("pr", tools)], ["pr__get_pr_context_pack"])
        self.assertEqual(tools_for_role("coordinator", tools), [])

    def test_unknown_role_has_no_tool_access(self) -> None:
        from mcp_hub.agents.specialists import tools_for_role

        self.assertEqual(tools_for_role("unknown", [SimpleNamespace(name="logs__query_logs")]), [])


if __name__ == "__main__":
    unittest.main()
