"""Namespaced MCP resource proxying (resources.py)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from mcp.shared.exceptions import MCPError
from mcp.types import (
    BlobResourceContents,
    ListResourcesResult,
    ListResourceTemplatesResult,
    ReadResourceRequestParams,
    ReadResourceResult,
    Resource,
    ResourceTemplate,
    TextResourceContents,
)

from mcp_hub.audit import AuditLog
from mcp_hub.config import HubConfig, ServerConfig
from mcp_hub.resources import namespace_template, namespace_uri, parse_namespaced_uri
from mcp_hub.server import HubRuntime
from mcp_hub.settings import PolicySettings, Settings


class ResourceNamespaceTest(unittest.TestCase):
    def test_uri_and_template_round_trip(self) -> None:
        wrapped = namespace_uri("alpha", "file:///docs/a.md?v=1#top")
        self.assertEqual(parse_namespaced_uri(wrapped), ("alpha", "file:///docs/a.md?v=1#top"))
        tpl = namespace_template("alpha", "docs://{id}")
        self.assertIn("{id}", tpl)
        self.assertEqual(parse_namespaced_uri(tpl.replace("{id}", "42")), ("alpha", "docs://42"))

    def test_invalid_uris_raise_invalid_params(self) -> None:
        for bad in ("file:///etc/passwd", "mcp-hub://", "mcp-hub://alpha", "mcp-hub://alpha/", "not-a-uri"):
            with self.subTest(bad=bad), self.assertRaises(MCPError):
                parse_namespaced_uri(bad)


class ResourcesRuntimeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.audit = AuditLog(root / "audit.jsonl")
        self.addCleanup(self.audit.close)
        self.audit.add_secrets(["super-secret-resource-token"])
        alpha = ServerConfig("alpha", "python", (), root, {}, ())
        beta = ServerConfig("beta", "python", (), root, {}, ())
        self.runtime = HubRuntime(
            HubConfig(root / "hub.json", {"alpha": alpha, "beta": beta}, ()),
            self.audit,
            Settings(policy=PolicySettings(mode="off")),
        )
        self.addCleanup(self.runtime.events.close)
        self.ctx = SimpleNamespace(request_id="req-res-1")

    async def test_lists_resources_and_templates_skipping_unsupported_children(self) -> None:
        alpha_client = SimpleNamespace(
            list_resources=AsyncMock(return_value=ListResourcesResult(resources=[
                Resource(name="readme", uri="file:///README.md"),
            ])),
            list_resource_templates=AsyncMock(return_value=ListResourceTemplatesResult(resource_templates=[
                ResourceTemplate(name="doc", uri_template="docs://{slug}"),
            ])),
        )
        beta_client = SimpleNamespace(
            list_resources=AsyncMock(side_effect=RuntimeError("not supported")),
            list_resource_templates=AsyncMock(side_effect=RuntimeError("not supported")),
        )
        self.runtime.clients = {"alpha": alpha_client, "beta": beta_client}

        listed = await self.runtime.list_resources(self.ctx, None)
        self.assertEqual(len(listed.resources), 1)
        self.assertEqual(str(listed.resources[0].uri), namespace_uri("alpha", "file:///README.md"))

        templates = await self.runtime.list_resource_templates(self.ctx, None)
        self.assertEqual(len(templates.resource_templates), 1)
        self.assertEqual(templates.resource_templates[0].uri_template, namespace_template("alpha", "docs://{slug}"))

    async def test_read_resource_routes_redacts_secrets_and_rewrites_uri(self) -> None:
        child_uri = "file:///secret.txt"
        alpha_client = SimpleNamespace(
            read_resource=AsyncMock(return_value=ReadResourceResult(contents=[
                TextResourceContents(uri=child_uri, text="token=super-secret-resource-token"),
                BlobResourceContents(uri=child_uri, blob="YWJj"),
            ])),
        )
        self.runtime.clients = {"alpha": alpha_client}

        wrapped = namespace_uri("alpha", child_uri)
        result = await self.runtime.read_resource(self.ctx, ReadResourceRequestParams(uri=wrapped))
        alpha_client.read_resource.assert_awaited_once_with(child_uri)
        self.assertEqual(str(result.contents[0].uri), wrapped)
        self.assertNotIn("super-secret-resource-token", result.contents[0].text)
        self.assertIn("[REDACTED]", result.contents[0].text)
        self.assertEqual(result.contents[1].blob, "YWJj")

    async def test_read_resource_handles_unknown_unavailable_and_failing_child(self) -> None:
        with self.assertRaisesRegex(MCPError, "Unknown child MCP"):
            await self.runtime.read_resource(
                self.ctx, ReadResourceRequestParams(uri=namespace_uri("missing", "file:///x")),
            )

        with self.assertRaisesRegex(MCPError, "unavailable"):
            await self.runtime.read_resource(
                self.ctx, ReadResourceRequestParams(uri=namespace_uri("alpha", "file:///x")),
            )

        self.runtime.clients = {"alpha": SimpleNamespace(read_resource=AsyncMock(side_effect=RuntimeError("boom")))}
        with self.assertRaisesRegex(MCPError, "request_id=req-res-1"):
            await self.runtime.read_resource(
                self.ctx, ReadResourceRequestParams(uri=namespace_uri("alpha", "file:///x")),
            )


if __name__ == "__main__":
    unittest.main()
