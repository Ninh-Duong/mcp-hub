import unittest

from mcp.types import CallToolResult, TextContent

from scripts.smoke import CHECKS


class SmokeChecksTest(unittest.TestCase):
    def setUp(self) -> None:
        self.check = CHECKS["mcp-server-logs"][2]

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


if __name__ == "__main__":
    unittest.main()
