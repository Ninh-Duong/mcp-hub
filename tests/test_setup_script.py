"""scripts/setup.ps1 (Setup.cmd): the one guided setup. Its steps are loaded by dot-sourcing and driven with
canned answers, so nothing is installed, downloaded, or asked."""
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


def run_steps(body: str, answers: list[str], timeout: float = 60) -> str:
    """Dot-source setup.ps1, answer Read-Host from `answers`, run `body`, and return its output."""
    canned = ",".join(f"'{answer}'" for answer in answers)
    script = (
        f"$ErrorActionPreference = 'Stop'; . '{SETUP}'; "
        f"$script:answers = [System.Collections.Queue]::new(@({canned})); "
        "function Read-Host { param($Prompt) if ($script:answers.Count) { $script:answers.Dequeue() } else { '' } }; "
        + body
    )
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout


class SetupScriptTextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.script = SETUP.read_text(encoding="utf-8")

    def test_setup_cmd_runs_the_guided_setup_with_windows_powershell(self) -> None:
        cmd = (HUB_ROOT / "Setup.cmd").read_text(encoding="utf-8")
        self.assertIn(r'"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"', cmd)
        self.assertIn(r'-File "%~dp0scripts\setup.ps1"', cmd)
        self.assertTrue(self.script.isascii())  # Windows PowerShell 5.1 reads a BOM-less script as ANSI

    def test_the_steps_run_in_order_and_reuse_hubctl(self) -> None:
        flow = self.script[self.script.index("function Invoke-Setup"):]
        order = [flow.index(step) for step in (
            "Select-Children", "Get-ChildRepositories", "Install-StatusApp", '@("install")', "Set-Accounts",
            '@("restart")', "Show-Check", "Copy-ClientPrompt",
        )]
        self.assertEqual(order, sorted(order))
        self.assertIn('if ($MyInvocation.InvocationName -ne ".") { Invoke-Setup }', self.script)

    def test_accounts_go_only_into_each_childs_own_setup(self) -> None:
        """The script launches the children's own login screens and files; it never reads a secret itself."""
        for launch in ("& $hubPython cli.py", "& node bin/cli.js", "& node start.mjs --cli", "Start-Process notepad.exe"):
            self.assertIn(launch, self.script)
        for forbidden in ("service.json", "connection.json", "agents.json", "-AsSecureString", "OPENOBSERVE_TOKEN="):
            self.assertNotIn(forbidden, self.script)


@unittest.skipUnless(POWERSHELL, "Windows PowerShell only")
class SetupStepsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name)

    def test_yes_no_keeps_the_default_on_enter_and_asks_again_on_nonsense(self) -> None:
        output = run_steps("Read-YesNo 'Q' $true; Read-YesNo 'Q' $false; Read-YesNo 'Q' $false", ["", "", "maybe", "y"])
        self.assertEqual(output.split()[:2], ["True", "False"])
        self.assertIn("Please answer y or n.", output)
        self.assertEqual(output.split()[-1], "True")

    def test_children_already_present_are_suggested(self) -> None:
        (self.parent / "mcp-jira-sync").mkdir()
        body = f"$parent = '{self.parent}'; (Select-Children) -join ','"
        self.assertEqual(run_steps(body, ["", "", "", ""]).strip(), "mcp-jira-sync")

    def test_source_projects_are_added_without_a_bom_and_keep_existing_ones(self) -> None:
        folder = self.parent / "orders"
        folder.mkdir()
        config = self.parent / "mcp-read-source" / "config" / "private"
        config.mkdir(parents=True)
        (config / "projects.json").write_text(json.dumps({"version": 1, "projects": {"old": "projects/old"}}), encoding="utf-8")
        body = (f"$parent = '{self.parent}'; function Select-Folder {{ param($Title) '{folder}' }}; "
                "Set-SourceProjects")
        run_steps(body, ["y", "bad name!", "y", "orders-api", "n"])
        raw = (config / "projects.json").read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))  # mcp-read-source reads plain UTF-8
        saved = json.loads(raw.decode("utf-8"))
        self.assertEqual(saved, {"version": 1, "projects": {"old": "projects/old", "orders-api": str(folder).replace("\\", "/")}})

    def test_account_steps_skip_children_that_are_not_downloaded(self) -> None:
        (self.parent / "mcp-read-source").mkdir()
        body = (f"$parent = '{self.parent}'; function Set-SourceProjects {{ 'source step ran' }}; "
                "function Set-JiraAccount { 'jira step ran' }; Set-Accounts -Chosen @('mcp-jira-sync','mcp-read-source')")
        output = run_steps(body, ["y"])
        self.assertIn("source step ran", output)
        self.assertNotIn("jira step ran", output)

    def test_the_client_prompt_names_this_clone(self) -> None:
        output = run_steps("function Set-Clipboard { throw 'no clipboard' }; Copy-ClientPrompt", [])
        self.assertIn(f"Read {str(HUB_ROOT).replace(chr(92), '/')}/docs/connecting-a-client.md", output)
        self.assertIn("install the always-on rule and the /mcp-hub command", output)


if __name__ == "__main__":
    unittest.main()
