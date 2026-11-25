"""Child lifecycle on HubRuntime: connect, the child's tool changes and losses, route rebuilds, and tools/list_changed announcements."""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from contextlib import AsyncExitStack
from mcp import Client
from mcp.types import (
    Tool,
)
from .audit_events import TOOLS_LIST_CHANGED_PUBLISHED
from .children import ChildSupervisor
from .config import HubConfig, ServerConfig, is_sensitive_key, load_config
from .planning.policy import PlanCatalog


class ChildLifecycleMixin:
    """Methods of HubRuntime, moved out of server.py."""

    def _new_supervisor(self, server_config: ServerConfig) -> ChildSupervisor:
        return ChildSupervisor(
            server_config, self.audit, self.child_connected, self.child_lost,
            log_dir=self.child_log_dir, opener=self.child_opener, backoff=self.child_backoff,
            log_max_bytes=self.settings.logs.max_bytes, on_tools_changed=self.child_tools_changed,
        )

    async def connect(self, stack: AsyncExitStack) -> None:
        """Start one supervisor per child and wait until each has connected or failed its first attempt.

        Children connect in parallel, each bounded by its `connectTimeoutSeconds`; a failing child is
        retried in the background so the remaining children stay usable. `stack` stops them all.
        """
        for server_id, keys in self.config.incomplete.items():
            self.audit.write("child_config_incomplete", level=logging.WARNING, server_id=server_id, keys=list(keys))
        for server_config in self.config.servers.values():
            self._add_child_secrets(server_config)
            self.supervisors[server_config.server_id] = self._new_supervisor(server_config)
        stack.push_async_callback(self.close_children)
        if self.pins is not None:
            await asyncio.to_thread(self.pins.load)  # before the first check of any child's tools
        self._starting = True
        for supervisor in self.supervisors.values():
            supervisor.start()
        await self._wait_for_first_attempts()
        self._starting = False
        signature = self._register_routes()
        if self.plans.persist_path is not None:
            catalog = PlanCatalog(
                self._call_facts, self.playbooks, self.projects,
                max_window_hours=self.settings.jobs.max_window_hours,
                policy_settings=self.settings.policy,
            )
            if await asyncio.to_thread(self.plans.load, catalog, self.audit) and self.settings.protocol.tool_exposure == "planned":
                signature = self._exposed_signature()
        self.events.prime(signature)
        self.audit.write(
            "hub.tools.registered",
            tool_count=len(self.public_tools),
            tools=[tool.name for tool in self.public_tools],
        )

    async def reload_server(self, server_id: str, config_loader: Callable[[], HubConfig] = load_config) -> bool:
        """Live hot-toggle one child MCP from reloaded config without restarting the Hub."""
        async with self._reload_lock:
            new_config = await asyncio.to_thread(config_loader)
            old_supervisor = self.supervisors.pop(server_id, None)
            if old_supervisor is not None:
                await old_supervisor.stop()
            old_tools = [name for name, route in self.routes.items() if route.server_id == server_id]
            if old_tools:
                self.result_cache.invalidate(old_tools)
            self.clients.pop(server_id, None)
            self._discovered.pop(server_id, None)
            self.discovered_tool_counts.pop(server_id, None)
            self.config = new_config
            enabled = server_id in new_config.servers
            if enabled:
                server_cfg = new_config.servers[server_id]
                self._add_child_secrets(server_cfg)
                supervisor = self._new_supervisor(server_cfg)
                self.supervisors[server_id] = supervisor
                supervisor.start()
                waiter = asyncio.ensure_future(supervisor.wait_first_attempt())
                try:
                    await asyncio.wait([waiter], timeout=min(2.0, self.settings.children.startup_wait_seconds))
                finally:
                    waiter.cancel()
            await self.announce_tools(self._rebuild_child_routes(server_id))
            self.audit.write("server.reloaded", server=server_id, enabled=enabled)
            return enabled

    async def _wait_for_first_attempts(self) -> None:
        """Wait for each child's first attempt, but no longer than `children.startupWaitSeconds` (SC-004).

        A child still connecting then joins later, through `child_connected`. The waiters are cancelled, not the
        supervisors, so a child keeps connecting in the background.
        """
        if not self.supervisors:
            return
        waiters = [asyncio.ensure_future(supervisor.wait_first_attempt()) for supervisor in self.supervisors.values()]
        try:
            await asyncio.wait(waiters, timeout=self.settings.children.startup_wait_seconds)
        finally:
            for waiter in waiters:
                waiter.cancel()

    def _add_child_secrets(self, server_config: ServerConfig) -> None:
        """Redact the child's secrets: sensitive config values, headers, and env/inherited values under sensitive names.

        Plain values such as PATH, TEMP, or USERPROFILE are not secrets; redacting them would garble errors.
        """
        self.audit.add_secrets(server_config.secret_values)
        self.audit.add_secrets(value for name, value in server_config.env.items() if is_sensitive_key(name))
        self.audit.add_secrets(value for name, value in server_config.headers.items() if is_sensitive_key(name))
        self.audit.add_secrets(
            os.environ.get(name, "") for name in server_config.inherit_env if is_sensitive_key(name)
        )

    def child_connected(self, server_id: str, client: Client, tools: dict[str, Tool]) -> None:
        """Publish a child's live session and its tools (called by its supervisor)."""
        self._discovered[server_id] = tools
        self.discovered_tool_counts[server_id] = len(tools)
        self.clients[server_id] = client
        self._schedule_config_probe(server_id, client, tools)
        if self._starting:
            return  # `connect` registers all routes once the first attempts are done
        self._schedule_announcement(self._rebuild_child_routes(server_id))

    def _schedule_config_probe(self, server_id: str, client: Client, tools: dict[str, Tool]) -> None:
        """Ask the child where its settings come from, in the background, off the tool-call path."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:  # no loop (sync tests): the file-only gaps stay
            return
        self._keep_background(asyncio.ensure_future(self.config_sources.probe(server_id, client, tools)))

    async def child_tools_changed(self, server_id: str) -> None:
        """A child sent tools/list_changed and its supervisor relisted it: rebuild its routes and announce them."""
        tools = self.supervisors[server_id].tools
        self._discovered[server_id] = tools
        self.discovered_tool_counts[server_id] = len(tools)
        if self._starting:
            return  # `connect` registers all routes once the first attempts are done
        await self.announce_tools(self._rebuild_child_routes(server_id))

    def _rebuild_child_routes(self, server_id: str) -> str:
        """Rebuild one child's routes; `hub.tools.registered` is written when the exposed names change."""
        before = [tool.name for tool in self.public_tools]
        signature = self._register_routes(server_id)
        after = [tool.name for tool in self.public_tools]
        if after != before:
            self.audit.write("hub.tools.registered", tool_count=len(after), tools=after)
        return signature

    def child_lost(self, server_id: str) -> None:
        """Withdraw a child's session (called by its supervisor)."""
        # Routes stay: calls fail fast with ChildUnavailable until the child reconnects.
        self.clients.pop(server_id, None)

    def _schedule_announcement(self, signature: str) -> None:
        """Announce a changed tool set from sync code: the debounced publish runs as a task on the event loop."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:  # no loop: nothing can be sent, so only the baseline moves
            self.events.prime(signature)
            return
        self._keep_background(asyncio.ensure_future(self.announce_tools(signature)))

    def _keep_background(self, task: asyncio.Task[None]) -> None:
        """Hold a task until it ends: the event loop keeps only weak references to tasks."""
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def announce_tools(self, signature: str) -> None:
        """Schedule one debounced tools/list_changed for a new tool signature (FR-006)."""
        await self.events.tools_changed(signature)

    def _audit_tools_published(self) -> None:
        """Called by the event bus once per publish, with or without a listener: one `tools.list_changed.published` line."""
        self.audit.write(TOOLS_LIST_CHANGED_PUBLISHED, tools_count=len(self.public_tools))
