"""scripts/setup.ps1 (Setup.cmd): the unattended setup. Its steps are loaded by dot-sourcing and driven with fake git,
winget, hubctl, and smoke, so nothing is installed, downloaded, or asked, and nothing is written outside a temp folder."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HUB_ROOT = Path(__file__).resolve().parents[1]
SETUP = HUB_ROOT / "scripts" / "setup.ps1"
POWERSHELL = shutil.which("powershell.exe") if os.name == "nt" else None
DEFAULTS = json.loads((HUB_ROOT / "config" / "hub.example.json").read_text(encoding="utf-8"))["servers"]
REPOSITORIES = {name: server["repository"] for name, server in DEFAULTS.items()}

# A fake `git` that records every call and fails for repositories named in $failClone.
FAKE_GIT = """
$script:gitCalls = @(); $script:failClone = @()
function git { $script:gitCalls += ($args -join ' '); $global:LASTEXITCODE = 0
    foreach ($bad in $script:failClone) { if (($args -join ' ') -like "*$bad*") { $global:LASTEXITCODE = 1 } } }
"""


def run_steps(body: str, timeout: float = 60) -> str:
    """Dot-source setup.ps1, make any prompt fail the run, run `body`, and return its output."""
    script = (
        f"$ErrorActionPreference = 'Stop'; . '{SETUP}'; "
        "function Read-Host { throw 'a prompt was shown' }; "
        + body
    )
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout


class SetupScriptTextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.script = SETUP.read_text(encoding="utf-8")

    def test_setup_cmd_runs_the_setup_with_windows_powershell(self) -> None:
        cmd = (HUB_ROOT / "Setup.cmd").read_text(encoding="utf-8")
        self.assertIn(r'"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"', cmd)
        self.assertIn(r'-File "%~dp0scripts\setup.ps1"', cmd)
        self.assertTrue(self.script.isascii())  # Windows PowerShell 5.1 reads a BOM-less script as ANSI

    def test_the_steps_run_in_order_and_reuse_hubctl_and_smoke(self) -> None:
        flow = self.script[self.script.index("function Invoke-Setup"):]
        order = [flow.index(step) for step in (
            "Get-ChildRepositories", "Install-StatusApp", '@("install")', "Show-Check", "Copy-ClientPrompt",
            "Show-AccountNextSteps",
        )]
        self.assertEqual(order, sorted(order))
        self.assertIn('if ($MyInvocation.InvocationName -ne ".")', self.script)
        self.assertIn("scripts\\smoke.py", self.script)

    def test_setup_never_asks_and_never_touches_accounts(self) -> None:
        """Accounts are filled in each child's repository by the user; Setup only says where."""
        for forbidden in (
            "Read-Host", "Read-YesNo", "Start-Process", "notepad", "cli.py\"", "& node", "& $hubPython cli.py",
            "-AsSecureString", "ConvertTo-Json", "Set-Content", "WriteAllText", "Copy-Item", "New-Item",
            "service.json", "connection.json", "agents.json", "OPENOBSERVE_TOKEN=",
        ):
            self.assertNotIn(forbidden, self.script, forbidden)

    def test_the_default_children_come_only_from_the_hub_config(self) -> None:
        self.assertIn("config\\hub.example.json", self.script)
        self.assertNotIn("github.com/", self.script)  # the repository addresses live in the config, once
        for server_id in DEFAULTS:
            self.assertIn(f'"{server_id}"', self.script, f"{server_id} has no account hint in setup.ps1")


@unittest.skipUnless(POWERSHELL, "Windows PowerShell only")
class SetupStepsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name)
        self.prelude = f"$parent = '{self.parent}'; function Test-Command {{ param($Name) $true }}; " + FAKE_GIT

    def steps(self, body: str) -> str:
        return run_steps(self.prelude + body)

    def test_status_app_is_refreshed_even_when_registry_entry_exists(self) -> None:
        output = self.steps(
            "function Get-ItemProperty { [pscustomobject]@{ InstallPath = 'unused' } }; "
            "function Invoke-Hubctl { param($Arguments) 'CALL=' + ($Arguments -join ' ') | Write-Host; $true }; "
            "Install-StatusApp"
        )
        self.assertIn("CALL=app -Install", output)

    def test_status_app_failure_warns_and_setup_can_continue(self) -> None:
        output = self.steps(
            "function Get-ItemProperty { $null }; function Invoke-Hubctl { $false }; "
            "Install-StatusApp; 'CONTINUED'"
        )
        self.assertIn("WARNING:", output)
        self.assertIn("CONTINUED", output)

    def clone_calls(self, body: str = "") -> list[str]:
        output = self.steps(f"{body} $failed = @(Get-ChildRepositories); $script:gitCalls | ForEach-Object {{ \"CALL $_\" }}")
        return [line[5:] for line in output.splitlines() if line.startswith("CALL ")]

    def test_every_missing_child_is_cloned_from_its_configured_address(self) -> None:
        calls = self.clone_calls()
        self.assertEqual(len(calls), len(REPOSITORIES))
        for url in REPOSITORIES.values():
            self.assertTrue(any(call.endswith(url) and "clone" in call for call in calls), url)
        self.assertEqual(list(self.parent.iterdir()), [])  # the fake git created nothing: Setup writes no files itself

    def test_only_the_missing_children_are_cloned_and_a_complete_set_clones_nothing(self) -> None:
        names = list(REPOSITORIES)
        (self.parent / names[0]).mkdir()
        (self.parent / names[1]).mkdir()
        calls = self.clone_calls()
        self.assertEqual(len(calls), len(names) - 2)
        self.assertFalse(any(names[0] in call or names[1] in call for call in calls))
        for name in names:
            (self.parent / name).mkdir(exist_ok=True)
        self.assertEqual(self.clone_calls(), [])

    def test_one_failed_download_is_reported_and_the_others_are_still_downloaded(self) -> None:
        bad = list(REPOSITORIES)[0]
        output = self.steps(
            f"$script:failClone = @('{bad}'); $failed = @(Get-ChildRepositories 3>$null); "
            "\"FAILED=$($failed -join ',')\"; \"CLONES=$($script:gitCalls.Count)\""
        )
        self.assertIn(f"FAILED={bad}", output)
        self.assertIn(f"CLONES={len(REPOSITORIES)}", output)

    def test_git_is_installed_when_missing_and_setup_stops_when_that_does_not_help(self) -> None:
        body = (
            "$script:winget = @(); function winget { $script:winget += ($args -join ' ') }; "
            "function Test-Command { param($Name) $false }; function Update-SessionPath { }; "
            "try { Assert-Git } catch { \"STOPPED: $($_.Exception.Message)\" }; \"WINGET=$($script:winget.Count)\""
        )
        output = self.steps(body)
        self.assertIn("STOPPED: Git could not be installed", output)
        self.assertIn("WINGET=1", output)

    def test_ai_clients_are_found_by_folder_or_command(self) -> None:
        profile = self.parent / "profile"
        for folder in (".codex", ".cursor", ".gemini/antigravity"):
            (profile / folder).mkdir(parents=True)
        body = (f"$env:USERPROFILE = '{profile}'; function Test-Command {{ param($Name) $Name -eq 'claude' }}; "
                "(Find-AiClients) -join ','")
        self.assertEqual(self.steps(body).strip(), "Claude Code,Codex,Antigravity,Cursor")
        empty = f"$env:USERPROFILE = '{self.parent / 'none'}'; function Test-Command {{ param($Name) $false }}; @(Find-AiClients).Count"
        self.assertEqual(self.steps(empty).strip(), "0")

    def test_the_client_prompt_names_this_clone_and_the_clients_found(self) -> None:
        output = self.steps("function Set-Clipboard { throw 'no clipboard' }; Copy-ClientPrompt -Clients @('Codex','Cursor')")
        self.assertIn(f"Read {str(HUB_ROOT).replace(chr(92), '/')}/docs/connecting-a-client.md", output)
        self.assertIn("install the always-on rule and the /mcp-hub command", output)
        self.assertIn("AI clients found on this PC: Codex, Cursor.", output)

    def test_the_client_prompt_still_helps_when_no_client_is_installed(self) -> None:
        output = self.steps("function Set-Clipboard { param($Value) }; Copy-ClientPrompt -Clients @()")
        self.assertIn("No AI client was found on this PC", output)
        self.assertIn("It is already copied", output)

    def test_account_next_steps_name_each_downloaded_child_and_no_value(self) -> None:
        output = self.steps("Show-AccountNextSteps -Cloned @('mcp-jira-sync','mcp-read-source')")
        self.assertIn("../mcp-jira-sync: run: uv run python cli.py", output)
        self.assertIn("../mcp-read-source: add your project folders", output)
        self.assertNotIn("mcp-pr-companion", output)  # not downloaded, so nothing to fill in
        self.assertIn("never through Setup or an AI chat", output)


@unittest.skipUnless(POWERSHELL, "Windows PowerShell only")
class SetupFlowTest(unittest.TestCase):
    """Invoke-Setup with every step faked: the order, the exit code, and what a failure does not stop."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def flow(self, *, failed: str = "@()", hubctl_ok: str = "$true", check_exit: int = 0) -> list[str]:
        body = (
            f"$parent = '{self.tmp.name}'; $script:order = @(); "
            f"function Get-ChildRepositories {{ $script:order += 'clone'; {failed} }}; "
            "function Install-StatusApp { $script:order += 'status' }; "
            f"function Invoke-Hubctl {{ param($Arguments) $script:order += ('hubctl ' + $Arguments[0]); {hubctl_ok} }}; "
            f"function Show-Check {{ $script:order += 'check'; {check_exit} }}; "
            "function Find-AiClients { $script:order += 'find' }; function Copy-ClientPrompt { $script:order += 'prompt' }; "
            "function Show-AccountNextSteps { $script:order += 'accounts' }; "
            "try { $code = Invoke-Setup | Select-Object -Last 1; \"CODE=$code\" } catch { \"THROWN: $($_.Exception.Message)\" }; "
            "\"ORDER=$($script:order -join ',')\""
        )
        return run_steps(body).splitlines()

    def test_everything_runs_in_order_without_a_prompt_and_exits_zero(self) -> None:
        lines = self.flow()
        self.assertIn("CODE=0", lines)
        self.assertIn("ORDER=clone,status,hubctl install,check,find,prompt,accounts", lines)

    def test_a_failed_check_still_reaches_the_client_and_account_steps_and_exits_one(self) -> None:
        lines = self.flow(check_exit=1)
        self.assertIn("CODE=1", lines)
        self.assertIn("ORDER=clone,status,hubctl install,check,find,prompt,accounts", lines)

    def test_a_child_that_could_not_be_downloaded_exits_one_but_the_rest_still_runs(self) -> None:
        lines = self.flow(failed="@('mcp-jira-sync')")
        self.assertIn("CODE=1", lines)
        self.assertIn("ORDER=clone,status,hubctl install,check,find,prompt,accounts", lines)

    def test_a_hub_that_cannot_be_installed_stops_before_any_check(self) -> None:
        lines = self.flow(hubctl_ok="$false")
        self.assertTrue(any(line.startswith("THROWN: The Hub could not be installed") for line in lines))
        self.assertIn("ORDER=clone,status,hubctl install", lines)


if __name__ == "__main__":
    unittest.main()
