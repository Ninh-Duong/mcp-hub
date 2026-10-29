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
        self.assertIn('[switch]$Service', windows)
        self.assertIn('[switch]$Bridge', windows)
        self.assertIn('[string]$ClientName', windows)
        self.assertIn('MCP_HUB_CLIENT_NAME', windows)
        self.assertIn('-ClientName can only be used with -Bridge', windows)
        self.assertIn('"mcp-hub-service"', windows)
        self.assertIn('"mcp-hub-bridge"', windows)
        self.assertIn("[switch]$Prepare", windows)
        self.assertIn("sync --locked", windows)
        for script in (windows, unix):
            with self.subTest(script=script[:30]):
                self.assertIn("UV_INSTALL_DIR", script)
                self.assertIn("UV_NO_MODIFY_PATH", script)
                self.assertIn("python install", script)
                self.assertIn("run --locked", script)

    def test_shared_service_control_uses_per_user_task_and_local_health(self) -> None:
        script = (HUB_ROOT / "scripts" / "hubctl.ps1").read_text(encoding="utf-8")
        launcher = (HUB_ROOT / "scripts" / "service_launcher.pyw").read_text(encoding="utf-8")
        self.assertIn('"install", "status", "start", "stop", "restart", "open"', script)
        self.assertIn("New-ScheduledTaskTrigger -AtLogOn", script)
        self.assertIn("-RunLevel Limited", script)
        self.assertIn("pythonw.exe", script)
        self.assertIn("-AllowStartIfOnBatteries", script)
        self.assertIn("-DontStopIfGoingOnBatteries", script)
        self.assertIn("service_launcher.pyw", script)
        self.assertIn("sys._base_executable", script)
        self.assertIn("CREATE_NO_WINDOW", launcher)
        self.assertIn('from mcp_hub.service import main; main()', launcher)
        self.assertIn("Prepare-HubEnvironment", script)
        self.assertIn('"$url/health"', script)
        self.assertIn('http://127.0.0.1:8765', script)
        self.assertIn('"$url/dashboard/pair"', script)
        self.assertIn('Start-Process "$url/dashboard/?pair=$pair"', script)
        self.assertIn('"$url/dashboard/api/service/shutdown"', script)
        self.assertNotIn("Stop-ScheduledTask", script)
        self.assertIn("Get-Content -LiteralPath $serviceConfigPath -Raw", script)
        self.assertIn("return [string]$serviceConfig.token", script)
        self.assertIn('"Bearer $(Get-ServiceToken)"', script)


if __name__ == "__main__":
    unittest.main()
