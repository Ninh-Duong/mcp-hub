from __future__ import annotations

import json
import unittest

import httpx

from mcp_hub.bitnet.client import BitnetClient


class BitnetClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_valid_json_reply(self) -> None:
        expected = {"playbook": "rcid-bug", "env": "prod"}

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v1/chat/completions")
            req_data = json.loads(request.content.decode("utf-8"))
            self.assertEqual(req_data["temperature"], 0)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(expected)}}]},
            )

        client = BitnetClient(8080, transport=httpx.MockTransport(handler))
        try:
            res = await client.complete_json("system prompt", "user query")
            self.assertEqual(res, expected)
        finally:
            await client.aclose()

    async def test_json_wrapped_in_prose(self) -> None:
        wrapped_content = "Here is your plan: {\"playbook\": \"service-errors\", \"env\": \"stg\"} and nothing else."

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": wrapped_content}}]},
            )

        client = BitnetClient(8080, transport=httpx.MockTransport(handler))
        try:
            res = await client.complete_json("system prompt", "user query")
            self.assertEqual(res, {"playbook": "service-errors", "env": "stg"})
        finally:
            await client.aclose()

    async def test_invalid_reply_returns_none(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "no json here whatsoever"}}]},
            )

        client = BitnetClient(8080, transport=httpx.MockTransport(handler))
        try:
            res = await client.complete_json("system prompt", "user query")
            self.assertIsNone(res)
        finally:
            await client.aclose()

    async def test_http_500_returns_none(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        client = BitnetClient(8080, transport=httpx.MockTransport(handler))
        try:
            res = await client.complete_json("system prompt", "user query")
            self.assertIsNone(res)
            text_res = await client.complete_text("sys", "user")
            self.assertIsNone(text_res)
        finally:
            await client.aclose()

    async def test_timeout_returns_none(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("Request timed out")

        client = BitnetClient(8080, transport=httpx.MockTransport(handler))
        try:
            res = await client.complete_json("system prompt", "user query")
            self.assertIsNone(res)
        finally:
            await client.aclose()

    async def test_healthy_returns_true_or_false(self) -> None:
        status_code = 200

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/health")
            if status_code == 200:
                return httpx.Response(200, text="OK")
            return httpx.Response(status_code, text="Unhealthy")

        client = BitnetClient(8080, transport=httpx.MockTransport(handler))
        try:
            self.assertTrue(await client.healthy())
            status_code = 503
            self.assertFalse(await client.healthy())
        finally:
            await client.aclose()
            # Double aclose should be a no-op
            await client.aclose()

    async def test_healthy_exception_returns_false(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        client = BitnetClient(8080, transport=httpx.MockTransport(handler))
        try:
            self.assertFalse(await client.healthy())
        finally:
            await client.aclose()

    async def test_complete_text_empty_choices(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": []})

        client = BitnetClient(8080, transport=httpx.MockTransport(handler))
        try:
            res = await client.complete_text("sys", "user")
            self.assertIsNone(res)
        finally:
            await client.aclose()

    async def test_extract_json_with_escaped_quotes_and_invalid_braces(self) -> None:
        content = r'Text with {"key": "value with \"escaped\" quotes"} and more.'

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

        client = BitnetClient(8080, transport=httpx.MockTransport(handler))
        try:
            res = await client.complete_json("sys", "user")
            self.assertEqual(res, {"key": 'value with "escaped" quotes'})
        finally:
            await client.aclose()


if __name__ == "__main__":
    unittest.main()
