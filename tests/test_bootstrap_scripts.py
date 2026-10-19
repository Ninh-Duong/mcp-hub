from __future__ import annotations

import unittest
from pathlib import Path

from mcp_hub.config import HUB_ROOT


class BootstrapScriptsTest(unittest.TestCase):
    def test_platform_launchers_bootstrap_without_prompt_and_keep_locked_entrypoint(self) -> None:
        windows = (HUB_ROOT / "scripts" / "start.ps1").read_text(encoding="utf-8")
        unix = (HUB_ROOT / "scripts" / "start.sh").read_text(encoding="utf-8")

        self.assertNotIn("Read-Host", windows)
        self.assertNotIn("[[ ! -t 0 ]]", unix)
        self.assertLess(windows.index("Bootstrap failed:"), windows.index("run --locked"))
        self.assertLess(unix.index("Bootstrap failed:"), unix.index('run --locked'))
        self.assertIn("[Console]::Error.WriteLine", windows)
        self.assertIn("$output = Invoke-Expression $installer 2>&1", windows)
        self.assertIn("if ($installExitCode -ne 0)", windows)
        self.assertIn("sh >&2", unix)
        self.assertIn('"$uv_bin" python install "$python_version" >&2', unix)
        self.assertIn('if ! curl -LsSf https://astral.sh/uv/install.sh | sh >&2; then', unix)
        for script in (windows, unix):
            with self.subTest(script=script[:30]):
                self.assertIn("UV_INSTALL_DIR", script)
                self.assertIn("UV_NO_MODIFY_PATH", script)
                self.assertIn("python install", script)
                self.assertIn("run --locked", script)


if __name__ == "__main__":
    unittest.main()
