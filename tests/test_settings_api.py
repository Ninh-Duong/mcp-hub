"""The settings API the desktop app uses instead of editing Hub files (dashboard/settings_api.py)."""
from __future__ import annotations

import json
import unittest

from mcp.server import Server
from starlette.testclient import TestClient

from mcp_hub import config, hub_settings
from mcp_hub.config import ServerSwitch
from mcp_hub.dashboard.api import create_dashboard_routes
from mcp_hub.dashboard.sessions import DashboardSessions
from mcp_hub.dashboard.settings_api import SettingsHandlers, settings_payload
from mcp_hub.http import create_http_app
from tests.test_dashboard_api import FakeJobRunner, FakeRuntime

TOKEN = "test-settings-bearer-token-value"
HEADERS = {"Host": "127.0.0.1:8765", "Authorization": f"Bearer {TOKEN}"}


class FakeStore:
    """hub_settings stand-in: two children, one of them waiting for credentials."""

    def __init__(self) -> None:
        self.activity_ui, self.editable, self.fail_read, self.fail_write = False, True, False, False
        self.bitnet = False
        self.enabled = {"sample-child": True, "other-child": False}

    def read_settings(self) -> hub_settings.HubSettings:
        if self.fail_read:
            raise OSError("unreadable")
        servers = tuple(ServerSwitch(server_id, enabled, "credentials required" if server_id == "sample-child" else None)
                        for server_id, enabled in self.enabled.items())
        return hub_settings.HubSettings(self.activity_ui, servers, self.editable, bitnet=self.bitnet)

    def _check(self) -> None:
        if self.fail_write or not self.editable:
            raise config.ConfigError("MCP_HUB_CONFIG is set; change that file instead")

    def set_activity_ui(self, enabled: bool) -> None:
        self._check()
        self.activity_ui = enabled

    def set_extension(self, name: str, enabled: bool) -> None:
        self._check()
        if name == "bitnet":
            self.bitnet = enabled
        elif name == "activity-ui":
            self.activity_ui = enabled

    def set_server_enabled(self, server_id: str, enabled: bool) -> None:
        self._check()
        self.enabled[server_id] = enabled


class SettingsApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeStore()
        self.autostart = {"hub": True}
        self.autostart_fails = False
        handlers = SettingsHandlers(store=self.store, read_autostart=self.read_autostart, write_autostart=self.write_autostart)
        routes = create_dashboard_routes(FakeRuntime(), FakeJobRunner(), DashboardSessions(), settings=handlers)
        app = create_http_app(
            Server("settings-test"), "test-client-bearer-token-value-0000", custom_routes=routes,
            sessions=DashboardSessions(), admin_token=TOKEN,
        )
        # One client per test: the app's MCP session manager runs once.
        self.client = TestClient(app).__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def read_autostart(self) -> dict[str, bool]:
        if self.autostart_fails:
            raise OSError("hubctl failed")
        return dict(self.autostart)

    def write_autostart(self, target: str, enabled: bool) -> dict[str, bool]:
        self.assertEqual(target, "hub")
        self.autostart[target] = enabled
        return self.read_autostart()

    def call(self, method: str, path: str, body: object = None, headers: dict[str, str] = HEADERS):
        if method == "GET":
            return self.client.get(path, headers=headers)
        return self.client.post(path, headers=headers, content=json.dumps(body) if body is not None else b"{")

    def test_settings_are_read_as_booleans_and_ids_only(self) -> None:
        response = self.call("GET", "/dashboard/api/settings")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "editable": True, "activity_ui": False, "bitnet": False,
            "servers": [{"id": "sample-child", "enabled": True, "reason": "credentials required"},
                        {"id": "other-child", "enabled": False, "reason": None}],
        })

    def test_every_settings_route_needs_the_bearer(self) -> None:
        bare = {"Host": "127.0.0.1:8765"}
        for method, path in (("GET", "/dashboard/api/settings"), ("POST", "/dashboard/api/settings/tracking"),
                             ("POST", "/dashboard/api/settings/bitnet"),
                             ("POST", "/dashboard/api/settings/servers/other-child"),
                             ("GET", "/dashboard/api/settings/autostart")):
            with self.subTest(path=path):
                self.assertEqual(self.call(method, path, {"enabled": True}, headers=bare).status_code, 401)
        self.assertTrue(self.store.enabled["sample-child"] and not self.store.activity_ui and not self.store.bitnet)

    def test_tracking_and_children_are_changed_and_answered_with_the_new_settings(self) -> None:
        tracking = self.call("POST", "/dashboard/api/settings/tracking", {"enabled": True})
        self.assertEqual((tracking.status_code, tracking.json()["activity_ui"]), (200, True))
        child = self.call("POST", "/dashboard/api/settings/servers/other-child", {"enabled": True})
        self.assertEqual(child.status_code, 200)
        self.assertTrue(child.json()["servers"][1]["enabled"])
        self.assertTrue(self.store.enabled["other-child"])

    def test_bitnet_is_changed_and_answered_with_the_new_settings(self) -> None:
        bitnet = self.call("POST", "/dashboard/api/settings/bitnet", {"enabled": True})
        self.assertEqual((bitnet.status_code, bitnet.json()["bitnet"]), (200, True))
        self.assertTrue(self.store.bitnet)
        # Bad body
        self.assertEqual(self.call("POST", "/dashboard/api/settings/bitnet", {"enabled": "yes"}).status_code, 400)
        # Conflict when not editable
        self.store.editable = False
        refused = self.call("POST", "/dashboard/api/settings/bitnet", {"enabled": False})
        self.assertEqual(refused.status_code, 409)

    def test_bad_bodies_unknown_children_and_custom_configs_are_refused(self) -> None:
        for body in (None, {"enabled": "yes"}, {"enabled": 1}, ["enabled"]):
            with self.subTest(body=body):
                self.assertEqual(self.call("POST", "/dashboard/api/settings/tracking", body).status_code, 400)
                self.assertEqual(self.call("POST", "/dashboard/api/settings/servers/other-child", body).status_code, 400)
        self.assertEqual(self.call("POST", "/dashboard/api/settings/servers/missing", {"enabled": True}).status_code, 404)
        self.store.editable = False
        refused = self.call("POST", "/dashboard/api/settings/tracking", {"enabled": True})
        self.assertEqual(refused.status_code, 409)
        self.assertNotIn("MCP_HUB_CONFIG", refused.text)  # fixed messages only
        self.assertFalse(self.store.activity_ui)

    def test_unreadable_settings_are_a_server_error(self) -> None:
        self.store.fail_read = True
        self.assertEqual(self.call("GET", "/dashboard/api/settings").status_code, 500)
        self.assertEqual(self.call("POST", "/dashboard/api/settings/servers/other-child", {"enabled": True}).status_code, 500)

    def test_hub_sign_in_task_is_read_and_changed(self) -> None:
        self.assertEqual(self.call("GET", "/dashboard/api/settings/autostart").json(), {"hub": True})
        changed = self.call("POST", "/dashboard/api/settings/autostart", {"enabled": False})
        self.assertEqual(changed.json(), {"hub": False})
        self.assertEqual(self.call("POST", "/dashboard/api/settings/autostart", {"enabled": "off"}).status_code, 400)
        self.autostart_fails = True
        self.assertEqual(self.call("GET", "/dashboard/api/settings/autostart").status_code, 503)

    def test_the_default_handlers_are_the_hub_owners(self) -> None:
        from mcp_hub import control

        handlers = SettingsHandlers()
        self.assertIs(handlers.store, hub_settings)
        self.assertIs(handlers.read_autostart, control.autostart_state)
        self.assertIs(handlers.write_autostart, control.set_autostart)
        self.assertEqual(settings_payload(hub_settings.HubSettings(True, (), False)),
                         {"editable": False, "activity_ui": True, "bitnet": False, "servers": []})


if __name__ == "__main__":
    unittest.main()
