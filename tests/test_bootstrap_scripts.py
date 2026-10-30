from __future__ import annotations

import importlib.machinery
import importlib.util
import re
import tempfile
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
        self.assertIn('"$uv_installer" >&2', unix)
        self.assertIn('"$uv_bin" python install "$python_version" >&2', unix)
        self.assertIn('if ! sh "$uv_installer" >&2; then', unix)
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

    def test_uv_installer_is_pinned_and_verified_before_it_runs(self) -> None:
        windows = (HUB_ROOT / "scripts" / "start.ps1").read_text(encoding="utf-8")
        unix = (HUB_ROOT / "scripts" / "start.sh").read_text(encoding="utf-8")
        version = re.search(r'\$uvVersion = "(\d+\.\d+\.\d+)"', windows).group(1)
        self.assertIn(f'uv_version="{version}"', unix, "both launchers install the same uv version")
        for script, hash_line in ((windows, r'\$uvInstallerSha256 = "([0-9a-f]{64})"'),
                                  (unix, r'uv_installer_sha256="([0-9a-f]{64})"')):
            with self.subTest(script=script[:30]):
                self.assertRegex(script, hash_line)
                self.assertNotIn("https://astral.sh/uv/install.", script)
        self.assertIn('"https://astral.sh/uv/$uvVersion/install.ps1"', windows)
        self.assertIn('"https://astral.sh/uv/$uv_version/install.sh"', unix)
        self.assertLess(windows.index("-ne $uvInstallerSha256"), windows.index("Invoke-Expression $installer"))
        self.assertLess(unix.index('!= "$uv_installer_sha256"'), unix.index('sh "$uv_installer"'))
        self.assertNotIn("| sh", unix)

    def test_shared_service_control_uses_per_user_task_and_local_health(self) -> None:
        script = (HUB_ROOT / "scripts" / "hubctl.ps1").read_text(encoding="utf-8")
        launcher = (HUB_ROOT / "scripts" / "service_launcher.pyw").read_text(encoding="utf-8")
        self.assertIn('"install", "status", "start", "stop", "restart", "open"', script)
        self.assertIn("New-ScheduledTaskTrigger -AtLogOn", script)
        self.assertIn("-RunLevel Limited", script)
        self.assertIn("pythonw.exe", script)
        self.assertIn("-AllowStartIfOnBatteries", script)
        self.assertIn("-DontStopIfGoingOnBatteries", script)
        self.assertIn("-RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)", script)
        self.assertIn("stdout=subprocess.DEVNULL", launcher)
        self.assertIn("stderr=service_err", launcher)
        self.assertIn("service_launcher.pyw", script)
        self.assertIn("sys._base_executable", script)
        self.assertIn("CREATE_NO_WINDOW", launcher)
        self.assertIn('"mcp-hub-service.exe"', launcher)
        self.assertIn('$taskName = "mcp-hub"', script)
        self.assertIn('Unregister-ScheduledTask -TaskName $legacyTaskName', script)
        self.assertIn("function Initialize-HubEnvironment", script)
        self.assertIn("$startInfo.CreateNoWindow = $true", script)
        self.assertIn('"-m mcp_hub.preflight"', script)
        self.assertIn('" --install"', script)
        self.assertIn('"preflight" { exit (Invoke-Preflight) }', script)
        self.assertNotIn("Start-Process -FilePath $powershell", script)
        self.assertIn('Join-Path $env:SystemRoot "System32', script)
        self.assertNotIn("Get-Command powershell.exe", script)
        self.assertIn('"$url/health"', script)
        self.assertIn('http://127.0.0.1:8765', script)
        self.assertIn('"$url/dashboard/pair"', script)
        self.assertIn('Start-Process "$url/dashboard/?pair=$pair"', script)
        self.assertIn('"$url/dashboard/api/service/shutdown"', script)
        self.assertIn('"$url/dashboard/api/status"', script)
        self.assertIn('"Clients: {0}{1}"', script)
        self.assertNotIn("Stop-ScheduledTask", script)
        self.assertIn("Get-Content -LiteralPath $serviceConfigPath -Raw", script)
        self.assertIn("return [string]$serviceConfig.token", script)
        self.assertIn('"Bearer $(Get-ServiceToken)"', script)


    def test_service_launcher_rotates_an_oversized_stderr_file_at_start(self) -> None:
        path = HUB_ROOT / "scripts" / "service_launcher.pyw"
        loader = importlib.machinery.SourceFileLoader("service_launcher", str(path))
        spec = importlib.util.spec_from_loader("service_launcher", loader)
        launcher = importlib.util.module_from_spec(spec)
        loader.exec_module(launcher)
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            logs.mkdir()
            (logs / "mcp-hub-service.err").write_bytes(b"x" * (launcher.SERVICE_ERR_MAX_BYTES + 1))
            with launcher.open_service_err(logs) as err:
                err.write(b"new run")
            self.assertEqual((logs / "mcp-hub-service.err").read_bytes(), b"new run")
            self.assertEqual((logs / "mcp-hub-service.err.1").stat().st_size, launcher.SERVICE_ERR_MAX_BYTES + 1)
            with launcher.open_service_err(logs) as err:
                err.write(b" appended")
            self.assertEqual((logs / "mcp-hub-service.err").read_bytes(), b"new run appended")

    def test_install_restricts_a_checkout_other_accounts_can_write(self) -> None:
        script = (HUB_ROOT / "scripts" / "hubctl.ps1").read_text(encoding="utf-8")
        register = script[script.index("function Register-HubTask"):script.index("function Write-ConnectedClients")]
        self.assertLess(register.index("Protect-HubCheckout"), register.index("Register-ScheduledTask"))
        self.assertIn("if (Test-OthersCanWrite -Path $repoRoot)", script)
        self.assertIn('icacls $repoRoot /inheritance:r /grant:r "${UserId}:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F"', script)
        self.assertIn("(Test-OthersCanWrite -Path $repoRoot)) { throw", script)

    def test_ui_flag_writes_a_one_shot_launch_file_and_default_start_removes_it(self) -> None:
        script = (HUB_ROOT / "scripts" / "hubctl.ps1").read_text(encoding="utf-8")
        self.assertIn("[switch]$Ui", script)
        self.assertIn('$launchPath = Join-Path $repoRoot "config\\private\\launch.json"', script)
        self.assertIn("""[IO.File]::WriteAllText($launchPath, '{"extensions": ["activity-ui"]}')""", script)
        self.assertIn("Remove-Item -LiteralPath $launchPath -Force", script)
        self.assertIn("if (-not $Ui) { Set-LaunchMode }", script)
        # The launch file is written right before the task starts, never while a Hub is running.
        self.assertLess(script.index("Set-LaunchMode -Ui:$Ui"), script.index("Start-ScheduledTask -TaskName $taskName"))
        self.assertIn("-not (Test-HubActivityUi)", script)
        for command in (
            '"start" {\n        Start-HubTask -Ui:$Ui',
            'Start-HubTask -Ui:$Ui\n        Write-Output "Restarted',
            '"open" { Open-HubDashboard -Ui:$Ui }',
            "Start-HubTask -SkipPrepare -Ui:$Ui",
        ):
            self.assertIn(command, script)


if __name__ == "__main__":
    unittest.main()
