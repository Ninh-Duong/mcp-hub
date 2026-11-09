from __future__ import annotations

import importlib.machinery
import importlib.util
import hashlib
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mcp_hub.config import HUB_ROOT


def load_launcher():
    path = HUB_ROOT / "scripts" / "service_launcher.pyw"
    loader = importlib.machinery.SourceFileLoader("service_launcher", str(path))
    spec = importlib.util.spec_from_loader("service_launcher", loader)
    launcher = importlib.util.module_from_spec(spec)
    loader.exec_module(launcher)
    return launcher


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
        self.assertNotIn('$Service', windows)  # the service starts from its task, never through start.ps1
        self.assertIn('[switch]$Bridge', windows)
        self.assertIn('[string]$ClientName', windows)
        self.assertIn('MCP_HUB_CLIENT_NAME', windows)
        self.assertIn('-ClientName can only be used with -Bridge', windows)
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
        self.assertNotIn("mcp-hub-shared-service", script)
        self.assertIn("function Initialize-HubEnvironment", script)
        self.assertIn("$startInfo.CreateNoWindow = $true", script)
        self.assertIn('"-m mcp_hub.preflight"', script)
        self.assertIn('" --install"', script)
        self.assertIn('"preflight" { exit (Invoke-Preflight) }', script)
        self.assertNotIn("Start-Process -FilePath $powershell", script)
        self.assertIn('Join-Path $env:SystemRoot "System32', script)
        self.assertNotIn("Get-Command powershell.exe", script)
        # The URL follows settings.http.port through the connection file; the default applies before a first start.
        url = script[script.index("function Get-HubUrl"):script.index("function Test-HubHealth")]
        self.assertIn("Get-ConnectionPath", url)
        self.assertIn(r"'^http://127\.0\.0\.1:\d{4,5}$'", url)
        self.assertIn('return "http://127.0.0.1:8765"', url)
        self.assertNotIn("$url", script)
        for route in ('/health"', '/dashboard/pair"', '/dashboard/?pair=$pair"', '/dashboard/api/service/shutdown"',
                      '/dashboard/api/status"'):
            self.assertIn(f'"$(Get-HubUrl){route}', script)
        self.assertIn('"Clients: {0}{1}"', script)
        self.assertNotIn("Stop-ScheduledTask", script)
        # The dashboard API takes the admin token from the connection file, never the client token in service.json.
        self.assertNotIn("Get-ServiceToken", script)
        self.assertNotIn(r"config\private\service.json", script)
        self.assertIn("Get-Content -LiteralPath $connectionPath -Raw", script)
        self.assertIn("return [string]$connection.token", script)
        self.assertEqual(script.count('"Bearer $(Get-AdminToken)"'), 4)
        lookup = script[script.index("function Get-ConnectionPath"):script.index("function Get-AdminToken")]
        for source in ("$env:MCP_HUB_CONNECTION_FILE", "$env:MCP_HUB_PRIVATE_DIR", r'"mcp-hub\connection.json"'):
            self.assertIn(source, lookup)


    def test_service_launcher_rotates_an_oversized_stderr_file_at_start(self) -> None:
        launcher = load_launcher()
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

    def make_checkout(self, tmp: str, *, lock: bytes | None = b"lock v1") -> Path:
        root = Path(tmp)
        (root / ".venv").mkdir()
        if lock is not None:
            (root / "uv.lock").write_bytes(lock)
        return root

    def test_the_launcher_sees_a_stale_environment_by_the_lock_stamp(self) -> None:
        launcher = load_launcher()
        digest = hashlib.sha256(b"lock v1").hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_checkout(tmp)
            stamp = root / ".venv" / launcher.LOCK_STAMP_NAME
            self.assertTrue(launcher.environment_stale(root))  # never synced by start.ps1 -Prepare
            stamp.write_text(digest + "\n", encoding="ascii")
            self.assertFalse(launcher.environment_stale(root))  # a trailing newline is fine
            (root / "uv.lock").write_bytes(b"lock v2")  # a git pull changed the dependencies
            self.assertTrue(launcher.environment_stale(root))
            stamp.unlink()
            stamp.mkdir()  # unreadable: refresh rather than trust it
            self.assertTrue(launcher.environment_stale(root))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(launcher.environment_stale(self.make_checkout(tmp, lock=None)))  # no lock, nothing to sync

    def test_the_launcher_runs_the_one_sync_path_and_reports_whether_it_finished(self) -> None:
        launcher = load_launcher()
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"SystemRoot": "WinRoot"}):
            root = Path(tmp)
            run = MagicMock(return_value=SimpleNamespace(returncode=0))
            self.assertTrue(launcher.sync_environment(root, "log", run))
            command = run.call_args.args[0]
            self.assertTrue(command[0].lower().endswith("powershell.exe"))
            self.assertEqual(command[-3:], ["-File", str(root / "scripts" / "start.ps1"), "-Prepare"])
            self.assertEqual(run.call_args.kwargs["cwd"], root)
            self.assertEqual(run.call_args.kwargs["timeout"], launcher.SYNC_TIMEOUT_SECONDS)
            self.assertEqual(run.call_args.kwargs["stderr"], "log")  # its messages land in logs/mcp-hub-service.err
            for failure in (SimpleNamespace(returncode=1), subprocess.TimeoutExpired("sync", 1), OSError("no powershell")):
                run = MagicMock(side_effect=failure) if isinstance(failure, Exception) else MagicMock(return_value=failure)
                self.assertFalse(launcher.sync_environment(root, "log", run))
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(launcher.sync_environment(Path("."), "log", MagicMock()))

    def run_main(self, root: Path, sync_ok: bool) -> list[str]:
        """Run the launcher's main with the sync and the service faked; returns what ran, in order."""
        launcher = load_launcher()
        ran: list[str] = []

        def sync(_root, _err):
            ran.append("sync")
            return sync_ok

        def service(*_args, **_kwargs):
            ran.append("service")
            return SimpleNamespace(returncode=0)

        with patch.object(launcher, "sync_environment", sync), patch.object(launcher.subprocess, "run", service):
            self.assertEqual(launcher.main(root), 0)
        return ran

    def test_a_stale_environment_is_synced_before_the_service_starts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_main(self.make_checkout(tmp), sync_ok=True), ["sync", "service"])

    def test_a_current_environment_starts_the_service_without_a_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_checkout(tmp)
            (root / ".venv" / load_launcher().LOCK_STAMP_NAME).write_text(hashlib.sha256(b"lock v1").hexdigest(), encoding="ascii")
            self.assertEqual(self.run_main(root, sync_ok=True), ["service"])

    def test_a_failed_sync_never_keeps_the_hub_from_starting_and_is_logged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_checkout(tmp)
            self.assertEqual(self.run_main(root, sync_ok=False), ["sync", "service"])
            log = (root / "logs" / "mcp-hub-service.err").read_text(encoding="utf-8")
        self.assertIn("environment sync did not finish", log)

    def test_start_script_stamps_the_lock_only_after_a_successful_sync(self) -> None:
        launcher = load_launcher()
        script = (HUB_ROOT / "scripts" / "start.ps1").read_text(encoding="utf-8")
        prepare = script[script.index("if ($Prepare) {"):script.index("$previousClientName")]
        self.assertLess(prepare.index("sync --locked"), prepare.index("if ($LASTEXITCODE -eq 0)"))
        self.assertLess(prepare.index("if ($LASTEXITCODE -eq 0)"), prepare.index("ComputeHash"))
        self.assertNotIn("Get-FileHash", prepare)  # not available in every powershell.exe that hubctl starts
        self.assertIn("[Security.Cryptography.SHA256]::Create()", prepare)
        self.assertIn(f'".venv\\{launcher.LOCK_STAMP_NAME}"', prepare)  # the same name the launcher reads
        self.assertIn("exit $LASTEXITCODE", prepare)  # the sync's own exit code is what the caller sees
        self.assertEqual(script.count(launcher.LOCK_STAMP_NAME), 1)  # one writer
        hubctl = (HUB_ROOT / "scripts" / "hubctl.ps1").read_text(encoding="utf-8")
        self.assertIn("-Prepare", hubctl)  # hubctl's sync goes through the same path, so it stamps too

    def test_install_restricts_a_checkout_other_accounts_can_write(self) -> None:
        script = (HUB_ROOT / "scripts" / "hubctl.ps1").read_text(encoding="utf-8")
        register = script[script.index("function Register-HubTask"):script.index("function Write-ConnectedClients")]
        self.assertLess(register.index("Protect-HubCheckout"), register.index("Register-ScheduledTask"))
        self.assertIn("if (Test-OthersCanWrite -Path $repoRoot)", script)
        self.assertIn('icacls $repoRoot /inheritance:r /grant:r "${UserId}:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F"', script)
        self.assertIn("(Test-OthersCanWrite -Path $repoRoot)) { throw", script)

    def test_install_leaves_the_desktop_app_to_its_own_installer(self) -> None:
        script = (HUB_ROOT / "scripts" / "hubctl.ps1").read_text(encoding="utf-8")
        self.assertNotIn("Register-StatusAppTask", script)
        remove = script[script.index("function Remove-LegacyStatusAppTask"):script.index("function Get-InstalledStatusApp")]
        self.assertIn('$statusAppTaskName = "mcp-hub-status"', script)
        self.assertIn("Unregister-ScheduledTask -TaskName $statusAppTaskName -Confirm:$false", remove)
        install = script[script.index('"install" {'):script.index('"status" {')]
        self.assertLess(install.index("Register-HubTask"), install.index("Remove-LegacyStatusAppTask"))
        # The running service locks its entry-point exe, so it stops before the environment sync.
        self.assertLess(install.index("Stop-HubTask"), install.index("Initialize-HubEnvironment"))
        self.assertLess(install.index("Start-HubTask"), install.index("Open-StatusApp"))
        self.assertIn('"preflight", "app", "autostart")]', script)
        self.assertIn('if ($Install) { Install-StatusApp } else { Open-StatusApp; Write-StatusAppHint }', script)

    def test_app_opens_the_installed_exe_or_else_runs_from_source(self) -> None:
        script = (HUB_ROOT / "scripts" / "hubctl.ps1").read_text(encoding="utf-8")
        self.assertIn('$statusAppKey = "HKCU:\\Software\\mcp-hub-status"', script)
        installed = script[script.index("function Get-InstalledStatusApp"):script.index("function Start-SourceStatusApp")]
        self.assertIn("-Name InstallPath", installed)
        self.assertIn('Join-Path $installPath "mcp-hub-status.exe"', installed)
        source = script[script.index("function Start-SourceStatusApp"):script.index("function Open-StatusApp")]
        self.assertIn('".venv\\Scripts\\pythonw.exe"', source)
        self.assertIn('-ArgumentList "-m mcp_hub.desktop"', source)
        opener = script[script.index("function Open-StatusApp"):script.index("function Write-ConnectedClients")]
        self.assertLess(opener.index("Get-InstalledStatusApp"), opener.index("Start-SourceStatusApp"))

    def test_environment_sync_stops_the_status_app_first_and_reopens_it(self) -> None:
        script = (HUB_ROOT / "scripts" / "hubctl.ps1").read_text(encoding="utf-8")
        initialize = script[script.index("function Initialize-HubEnvironment"):script.index("function Set-LaunchMode")]
        self.assertLess(initialize.index("Stop-StatusApp"), initialize.index("Sync-HubEnvironment"))
        # A sync blocked by a bridge holding its exe still starts the Hub, but only on an environment that imports.
        self.assertIn("if (-not (Test-HubEnvironment)) { throw }", initialize)
        self.assertLess(initialize.index("Sync-HubEnvironment"), initialize.index("Test-HubEnvironment"))
        check = script[script.index("function Test-HubEnvironment"):script.index("function Initialize-HubEnvironment")]
        self.assertIn('-Arguments \'-c "import mcp_hub.service"\'', check)
        stop = script[script.index("function Stop-StatusApp"):script.index("function Restore-StatusApp")]
        # Only this checkout's app: its venv launcher and the interpreter that launcher started.
        self.assertIn("$_.CommandLine.Contains($venv)", stop)
        self.assertIn('$_.CommandLine.Contains("-m mcp_hub.desktop")', stop)
        self.assertIn("$launcherIds -contains $_.ParentProcessId", stop)
        start = script[script.index("function Start-HubTask"):script.index("function Stop-HubTask")]
        self.assertLess(start.index("Start-ScheduledTask -TaskName $taskName"), start.index("Restore-StatusApp"))

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


    def test_autostart_switches_only_the_logon_trigger_and_install_keeps_it(self) -> None:
        script = (HUB_ROOT / "scripts" / "hubctl.ps1").read_text(encoding="utf-8")
        self.assertIn("$autostartTasks = [ordered]@{ hub = $taskName }", script)
        self.assertIn('[ValidateSet("hub")]', script)
        self.assertIn('[ValidateSet("on", "off")]', script)
        switch = script[script.index("function Set-Autostart"):script.index("function Write-Autostart")]
        # The task stays enabled so hubctl start and the app's Start button can still run it.
        self.assertIn("foreach ($trigger in $task.Triggers) { $trigger.Enabled = $Enabled }", switch)
        self.assertIn("Set-ScheduledTask -InputObject $task", switch)
        self.assertNotIn("Disable-ScheduledTask", script)
        self.assertIn("ConvertTo-Json -Compress", script)
        trigger = script[script.index("function New-LogonTrigger"):script.index("function Register-HubTask")]
        self.assertIn("New-ScheduledTaskTrigger -AtLogOn -User $UserId", trigger)
        self.assertIn("$trigger.Enabled = -not $wasOff", trigger)
        hub = script[script.index("function Register-HubTask"):script.index("function Remove-LegacyStatusAppTask")]
        self.assertIn("New-LogonTrigger -Name $taskName", hub)
        command = script[script.index('"autostart" {'):]
        self.assertLess(command.index("Set-Autostart"), command.index("Write-Autostart"))


def load_build_script():
    path = HUB_ROOT / "scripts" / "build_desktop.py"
    spec = importlib.util.spec_from_file_location("build_desktop", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DesktopPackagingTest(unittest.TestCase):
    """The desktop app's installer: a separate, per-user product that bundles nothing of the Hub."""

    def test_spec_bundles_the_app_and_its_locales_with_relative_paths(self) -> None:
        spec = (HUB_ROOT / "packaging" / "desktop" / "mcp-hub-status.spec").read_text(encoding="utf-8")
        self.assertIn("ROOT = Path(SPECPATH).resolve().parents[1]", spec)
        self.assertIn('LOCALE_DEST = "mcp_hub/dashboard/locales"', spec)
        self.assertIn('(str(LOCALES / "en.json"), LOCALE_DEST), (str(LOCALES / "vi.json"), LOCALE_DEST)', spec)
        self.assertIn("console=False", spec)
        self.assertNotRegex(spec, r"[A-Za-z]:\\\\")
        entry = (HUB_ROOT / "packaging" / "desktop" / "entry.py").read_text(encoding="utf-8")
        self.assertIn("from mcp_hub.desktop.app import main", entry)

    def test_installer_is_per_user_and_cleans_up_its_sign_in_entry(self) -> None:
        iss = (HUB_ROOT / "packaging" / "desktop" / "installer.iss").read_text(encoding="utf-8")
        for line in (
            "PrivilegesRequired=lowest",
            r"DefaultDirName={localappdata}\Programs\mcp-hub Status",
            r"AppMutex=Local\mcp-hub-status",
            'ValueName: "InstallPath"; ValueData: "{app}"; Flags: uninsdeletekey',
            'ValueName: "mcp-hub-status"; ValueData: """{app}\\{#AppExe}"""; Flags: uninsdeletevalue; Tasks: startup',
            "RegDeleteValue(HKEY_CURRENT_USER, '{#RunKey}', 'mcp-hub-status')",
            'AppUserModelID: "mcp-hub.status"',
        ):
            self.assertIn(line, iss)
        self.assertNotIn("{commonpf}", iss)

    def test_releases_are_built_by_ci_from_master_or_a_version_tag(self) -> None:
        workflow = (HUB_ROOT / ".github" / "workflows" / "desktop-release.yml").read_text(encoding="utf-8")
        qc = (HUB_ROOT / ".github" / "workflows" / "qc.yml").read_text(encoding="utf-8")
        self.assertIn("branches: [master]", workflow)
        self.assertIn('tags: ["v*"]', workflow)
        # Write access only for the job that publishes; actions pinned to the same commits as QC.
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("contents: write", workflow)
        for action in re.findall(r"uses: (\S+@[0-9a-f]{40})", workflow):
            self.assertIn(action, qc)
        # The decision runs first; the Windows build and the publish come after QC, in order.
        order = [workflow.index(step) for step in (
            "does not match the project version", "scripts/package_qc.py", "scripts/build_desktop.py",
            "Get-FileHash", "scripts/build_bitnet_runtime.py", "gh release create",
        )]
        self.assertEqual(order, sorted(order))
        # The BitNet runtime and its manifest go into the same Release as the installer.
        self.assertIn('-like "bitnet-*"', workflow)
        self.assertIn("needs.plan.outputs.build == 'true'", workflow)
        self.assertIn("needs.plan.outputs.publish == 'true'", workflow)
        # A push to master creates the tag at the built commit; a tag push verifies the existing tag.
        self.assertIn("--target", workflow)
        self.assertIn("--verify-tag", workflow)
        self.assertIn("gh release view", workflow)
        self.assertIn("group: desktop-release", workflow)

    def test_hubctl_points_to_the_release_and_installs_only_a_checked_one(self) -> None:
        script = (HUB_ROOT / "scripts" / "hubctl.ps1").read_text(encoding="utf-8")
        install = script[script.index("function Install-ReleasedStatusApp"):script.index("function Write-ConnectedClients")]
        # The SHA-256 is compared before anything runs, and the setup is interactive (never /SILENT).
        self.assertLess(install.index("if ($actual -ne $expected)"), install.index("Start-Process -FilePath $path"))
        self.assertIn("Remove-Item -LiteralPath $path -Force", install)
        self.assertNotIn("/SILENT", install)
        # The published Release is tried before a build left in dist/, which may be older.
        status = script[script.index("function Install-StatusApp"):script.index("function Write-ConnectedClients")]
        self.assertLess(status.index("Install-ReleasedStatusApp"), status.index("Find-LocalInstaller"))
        latest = script[script.index("function Get-LatestInstaller"):script.index("function Install-StatusApp")]
        self.assertIn(r"'^mcp-hub-status-setup-\d+\.\d+\.\d+\.exe$'", latest)
        self.assertIn('"$($setup.name).sha256"', latest)
        self.assertIn("Write-StatusAppHint", script[script.index('"install" {'):script.index('"status" {')])
        self.assertIn("if ($Install) { Install-StatusApp } else { Open-StatusApp; Write-StatusAppHint }", script)

    def test_the_desktop_names_match_in_every_copy(self) -> None:
        """The installer, the spec, and hubctl cannot import desktop/constants.py, so they repeat its values."""
        from mcp_hub.desktop import constants as names

        packaging = HUB_ROOT / "packaging" / "desktop"
        iss = (packaging / "installer.iss").read_text(encoding="utf-8")
        spec = (packaging / f"{names.APP_NAME}.spec").read_text(encoding="utf-8")
        hubctl = (HUB_ROOT / "scripts" / "hubctl.ps1").read_text(encoding="utf-8")
        for copy in (
            f'#define AppExe "{names.EXE_NAME}"', f"AppMutex={names.MUTEX_NAME}",
            f'ValueName: "{names.RUN_VALUE}"', f"'{{#RunKey}}', '{names.RUN_VALUE}')",
            f'Name: "{{userappdata}}\\{names.APP_DATA_DIR}"', f'Subkey: "Software\\{names.APP_NAME}"',
            f"OutputBaseFilename={names.APP_NAME}-setup-",
        ):
            self.assertIn(copy, iss)
        self.assertEqual(iss.count(f'AppUserModelID: "{names.APP_USER_MODEL_ID}"'), 2)
        self.assertEqual(spec.count(f'name="{names.APP_NAME}"'), 2)
        for copy in (f'$taskName = "{names.HUB_TASK}"', f'"HKCU:\\Software\\{names.APP_NAME}"', f'"{names.EXE_NAME}"'):
            self.assertIn(copy, hubctl)

    def test_installer_never_installs_over_the_same_or_a_newer_version(self) -> None:
        iss = (HUB_ROOT / "packaging" / "desktop" / "installer.iss").read_text(encoding="utf-8")
        self.assertIn("AppId={{{#AppGuid}}", iss)
        self.assertIn(r"CurrentVersion\Uninstall\{{#AppGuid}}_is1", iss)
        self.assertIn("ComparePackedVersion(Old, New)", iss)
        setup = iss[iss.index("function InitializeSetup"):iss.index("function SpaceLine")]
        # Only an older installed version may be updated; anything else stops setup, silent runs included.
        self.assertIn("if CompareInstalled(Installed) < 0 then\n    Result := ConfirmUpdate(Installed)", setup)
        self.assertIn("ReportInstalled(Installed);\n    Result := False;", setup)
        self.assertIn("['Uninstall', 'Close']", iss)
        self.assertIn("['Update', 'Close']", iss)
        self.assertIn("Exec(RemoveQuotes(Uninstaller)", iss)
        self.assertNotIn("Install {#AppVersion}", iss)

    def test_installer_checks_the_system_first(self) -> None:
        iss = (HUB_ROOT / "packaging" / "desktop" / "installer.iss").read_text(encoding="utf-8")
        self.assertIn("MinVersion=10.0.17763", iss)
        self.assertIn("CreateOutputMsgMemoPage(wpWelcome, 'System check'", iss)
        self.assertIn("Result := (CurPageID <> CheckPage.ID) or EnoughSpace;", iss)
        # The Hub check looks only at whether the connection file exists; the token is never read.
        self.assertIn(r"FileExists(ExpandConstant('{localappdata}\mcp-hub\connection.json'))", iss)
        self.assertNotIn("LoadStringFromFile", iss)

    def test_build_requires_the_bundled_visual_cpp_runtime(self) -> None:
        build = load_build_script()
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            for name in build.BUNDLED_RUNTIME[:-1]:
                (bundle / "_internal" / name).parent.mkdir(parents=True, exist_ok=True)
                (bundle / "_internal" / name).write_bytes(b"")
            with self.assertRaises(SystemExit) as caught:
                build.check_runtime_libraries(bundle)
            self.assertIn(build.BUNDLED_RUNTIME[-1], str(caught.exception))
            (bundle / "_internal" / build.BUNDLED_RUNTIME[-1]).write_bytes(b"")
            build.check_runtime_libraries(bundle)

    def test_build_script_finds_inno_and_builds_the_commands(self) -> None:
        build = load_build_script()
        self.assertRegex(build.project_version(), r"^\d+\.\d+\.\d+")
        with tempfile.TemporaryDirectory() as tmp:
            iscc = Path(tmp) / "Programs" / "Inno Setup 6" / "ISCC.exe"
            iscc.parent.mkdir(parents=True)
            iscc.write_bytes(b"")
            self.assertEqual(build.find_iscc(lambda _name: None, {"LOCALAPPDATA": tmp}), iscc)
            with self.assertRaises(SystemExit) as caught:
                build.find_iscc(lambda _name: None, {"ProgramFiles": str(Path(tmp) / "none")})
            self.assertIn("winget install JRSoftware.InnoSetup", str(caught.exception))
        self.assertEqual(build.find_iscc(lambda _name: "/bin/ISCC", {}), Path("/bin/ISCC"))
        command = build.iscc_command(Path("ISCC.exe"), "1.2.3", Path("app.ico"))
        self.assertIn("/DAppVersion=1.2.3", command)
        self.assertEqual(command[-1], str(build.PACKAGING / "installer.iss"))
        self.assertTrue(all(str(build.ROOT) in part for part in command[2:4]))
        pyinstaller = build.pyinstaller_command()
        self.assertEqual(pyinstaller[1:3], ["-m", "PyInstaller"])
        self.assertTrue(pyinstaller[-1].endswith("mcp-hub-status.spec"))

    def test_build_script_self_checks_the_bundle(self) -> None:
        import subprocess
        from types import SimpleNamespace

        build = load_build_script()
        with tempfile.TemporaryDirectory() as tmp:
            build.BUILD = Path(tmp)

            def renders(command, **_kwargs):
                Path(command[2]).mkdir(parents=True, exist_ok=True)
                (Path(command[2]) / "running-en-light.png").write_bytes(b"png")
                return SimpleNamespace(returncode=0)

            build.check_bundle(renders)
            for run in (lambda *_a, **_k: SimpleNamespace(returncode=1),
                        lambda *_a, **_k: SimpleNamespace(returncode=0),  # no image written
                        lambda *_a, **_k: (_ for _ in ()).throw(subprocess.TimeoutExpired("exe", 1))):
                with self.subTest(run=run), self.assertRaises(SystemExit):
                    build.check_bundle(run)

    def test_build_script_checks_the_bundle_reports_the_project_version(self) -> None:
        import subprocess
        from types import SimpleNamespace

        build = load_build_script()
        build.check_bundle_version("1.2.3", lambda *_a, **_k: SimpleNamespace(returncode=0, stdout="1.2.3\n"))
        for run in (lambda *_a, **_k: SimpleNamespace(returncode=0, stdout="unknown\n"),  # metadata missing
                    lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=""),
                    lambda *_a, **_k: SimpleNamespace(returncode=1, stdout="1.2.3\n"),
                    lambda *_a, **_k: (_ for _ in ()).throw(subprocess.TimeoutExpired("exe", 1))):
            with self.subTest(run=run), self.assertRaises(SystemExit):
                build.check_bundle_version("1.2.3", run)
        main = Path(build.__file__).read_text(encoding="utf-8")
        self.assertLess(main.index("check_bundle()\n    check_bundle_version(version)"), main.index("iscc_command(iscc, version"))

    def test_the_spec_bundles_the_package_metadata_the_update_check_reads(self) -> None:
        spec = (HUB_ROOT / "packaging" / "desktop" / "mcp-hub-status.spec").read_text(encoding="utf-8")
        self.assertIn('copy_metadata("mcp-hub")', spec)
        name = re.search(r'^name = "([^"]+)"', (HUB_ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)[1]
        self.assertEqual(name, "mcp-hub")  # update.app_version() reads this distribution

    def test_the_update_repository_names_this_projects_releases(self) -> None:
        from mcp_hub.desktop import constants as names

        workflow = (HUB_ROOT / ".github" / "workflows" / "desktop-release.yml").read_text(encoding="utf-8")
        self.assertIn("gh release create", workflow)  # the Releases the app reads come from this workflow
        self.assertRegex(names.UPDATE_REPOSITORY, r"^[\w.-]+/[\w.-]+$")
        self.assertIn(f"{names.APP_NAME}-setup-", (HUB_ROOT / "packaging" / "desktop" / "installer.iss").read_text(encoding="utf-8"))

    def test_build_script_draws_an_icon_file(self) -> None:
        try:
            import PySide6.QtGui  # noqa: F401
        except ImportError as exc:  # Qt's system libraries are missing (for example a bare Linux host)
            self.skipTest(f"Qt is unavailable: {exc}")
        build = load_build_script()
        with tempfile.TemporaryDirectory() as tmp:
            icon = build.draw_icon(Path(tmp) / "icons" / "app.ico")
            self.assertEqual(icon.read_bytes()[:4], b"\x00\x00\x01\x00")  # the ICO header

    def test_build_output_is_ignored_by_git(self) -> None:
        ignored = (HUB_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("build/", ignored)
        self.assertIn("dist/", ignored)


if __name__ == "__main__":
    unittest.main()
