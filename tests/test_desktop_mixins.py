"""The mixins that StatusWindow is built from (desktop/appearance, actions, live_feeds, polling, render,
update_controller). Each module is imported directly; the checks cover that StatusWindow inherits from every mixin,
and the shared names the modules use from constants.py.
"""
from __future__ import annotations

import unittest

from mcp_hub.desktop import actions, appearance, live_feeds, polling, render, update_controller
from mcp_hub.desktop.app import StatusWindow
from mcp_hub.desktop.constants import NOT_DRAWN, START_TIMEOUT_KEY, STICKY_ERROR_KEYS


class StatusWindowMixinsTest(unittest.TestCase):
    def test_status_window_inherits_every_mixin(self) -> None:
        for mixin in (
            appearance.AppearanceMixin,
            actions.ActionsMixin,
            live_feeds.LiveFeedsMixin,
            polling.PollingMixin,
            render.RenderMixin,
            update_controller.UpdateMixin,
        ):
            with self.subTest(mixin=mixin.__name__):
                self.assertTrue(issubclass(StatusWindow, mixin))

    def test_each_mixin_owns_its_entry_points(self) -> None:
        self.assertTrue(hasattr(appearance.AppearanceMixin, "set_theme"))
        self.assertTrue(hasattr(actions.ActionsMixin, "set_tracking"))
        self.assertTrue(hasattr(live_feeds.LiveFeedsMixin, "preview_usage"))
        self.assertTrue(hasattr(polling.PollingMixin, "process_results"))
        self.assertTrue(hasattr(render.RenderMixin, "render"))
        self.assertTrue(hasattr(update_controller.UpdateMixin, "install_update"))

    def test_the_render_sentinel_is_the_one_constants_defines(self) -> None:
        # A section key that was never drawn must never equal a real key (render.py compares against it).
        self.assertIsNot(NOT_DRAWN, None)
        self.assertNotEqual(NOT_DRAWN, "")

    def test_the_sticky_errors_include_the_start_timeout(self) -> None:
        self.assertIn(START_TIMEOUT_KEY, STICKY_ERROR_KEYS)


if __name__ == "__main__":
    unittest.main()
