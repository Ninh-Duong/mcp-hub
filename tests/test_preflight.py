from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from mcp_hub import preflight
from mcp_hub.config import HubConfig, PrepareSpec, RuntimeRequirement, ServerConfig

NODE_22 = RuntimeRequirement(name="node", min_version=(22, 0, 0), winget_id="OpenJS.NodeJS.LTS")


class FakeRunner:
    """Answers `node --version` with the current version and records every command."""

    def __init__(self, node_versions: list[str], *, winget_code: int = 0, prepare_code: int = 0) -> None:
        self.node_versions = list(node_versions)
        self.winget_code = winget_code
        self.prepare_code = prepare_code
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, command, **kwargs) -> subprocess.CompletedProcess:
        command = list(command)
        self.calls.append((command, kwargs))
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, stdout=self.node_versions.pop(0), stderr="")
        if Path(command[0]).name == "winget":
            return subprocess.CompletedProcess(command, self.winget_code, stdout="", stderr="")
        return subprocess.CompletedProcess(command, self.prepare_code, stdout="secret output", stderr="")

    def commands(self) -> list[list[str]]:
        return [command for command, _kwargs in self.calls]


def which_all(name: str) -> str:
    return f"/bin/{name}"


def server(cwd: Path, prepare: PrepareSpec | None, server_id: str = "mcp-server-logs") -> ServerConfig:
    return ServerConfig(server_id, "node", ("start.mjs",), cwd, {"CENTRAL_CONFIG_PATH": "hub.json"}, (), prepare=prepare)


class VersionTest(unittest.TestCase):
    def test_parse_version_reads_node_style_output(self) -> None:
        self.assertEqual(preflight.parse_version("v22.11.0\n"), (22, 11, 0))
        self.assertEqual(preflight.parse_version("20.12"), (20, 12))
        self.assertIsNone(preflight.parse_version("not a version"))
        self.assertEqual(preflight.format_version((20, 12, 0)), "20.12.0")


class RuntimeTest(unittest.TestCase):
    def test_satisfied_runtime_needs_no_install(self) -> None:
        runner = FakeRunner(["v22.3.0"])
        result = preflight.ensure_runtime("child", NODE_22, install=True, runner=runner, which=which_all, platform="win32")
        self.assertTrue(result.ok)
        self.assertEqual(len(runner.calls), 1)

    def test_missing_runtime_is_installed_with_winget(self) -> None:
        runner = FakeRunner(["v24.1.0"])
        found = iter([None, "/bin/winget", "/bin/node"])
        with patch.object(preflight, "refresh_windows_path") as refresh:
            result = preflight.ensure_runtime(
                "child", NODE_22, install=True, runner=runner, which=lambda _name: next(found), platform="win32",
            )
        self.assertTrue(result.ok, result.detail)
        winget = runner.commands()[0]
        self.assertEqual(winget[1:4], ["install", "--id", "OpenJS.NodeJS.LTS"])
        refresh.assert_called_once()

    def test_old_runtime_is_upgraded(self) -> None:
        runner = FakeRunner(["v18.0.0", "v22.1.0"])
        with patch.object(preflight, "refresh_windows_path"):
            result = preflight.ensure_runtime("child", NODE_22, install=True, runner=runner, which=which_all, platform="win32")
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(runner.commands()[1][1], "upgrade")

    def test_check_only_mode_never_installs(self) -> None:
        runner = FakeRunner(["v18.0.0"])
        result = preflight.ensure_runtime("child", NODE_22, install=False, runner=runner, which=which_all, platform="win32")
        self.assertFalse(result.ok)
        self.assertIn("older than 22.0.0", result.detail)
        self.assertEqual(len(runner.calls), 1)

    def test_winget_is_not_used_outside_windows(self) -> None:
        runner = FakeRunner([])
        result = preflight.ensure_runtime("child", NODE_22, install=True, runner=runner, which=lambda _name: None, platform="linux")
        self.assertFalse(result.ok)
        self.assertEqual(runner.calls, [])


class PrepareTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = Path(self.tmp.name)

    def test_prepare_runs_in_child_directory_with_child_env_and_hides_output(self) -> None:
        spec = PrepareSpec(command=("node", "start.mjs", "--preflight"), runtime=NODE_22)
        runner = FakeRunner(["v22.0.0"])
        results = preflight.check_server(server(self.cwd, spec), install=True, runner=runner, which=which_all, platform="win32")
        self.assertEqual([(result.step, result.ok) for result in results], [("node", True), ("prepare", True)])
        command, kwargs = runner.calls[-1]
        self.assertEqual(command, ["/bin/node", "start.mjs", "--preflight"])
        self.assertEqual(kwargs["cwd"], str(self.cwd))
        self.assertEqual(kwargs["env"]["CENTRAL_CONFIG_PATH"], "hub.json")
        self.assertNotIn("secret output", " ".join(result.detail for result in results))

    def test_failed_runtime_skips_prepare_but_other_children_still_run(self) -> None:
        bad = server(self.cwd, PrepareSpec(command=("node", "start.mjs"), runtime=NODE_22), "first")
        good = server(self.cwd, PrepareSpec(command=("npm", "ci")), "second")
        config = HubConfig(self.cwd / "hub.json", {"first": bad, "second": good}, ())
        runner = FakeRunner(["v18.0.0"])
        results = preflight.run_preflight(config, install=False, runner=runner, which=which_all, platform="linux")
        self.assertEqual([(r.server_id, r.step, r.ok) for r in results], [("first", "node", False)])

        runner = FakeRunner(["v18.0.0"], winget_code=1)
        results = preflight.run_preflight(config, install=True, runner=runner, which=which_all, platform="win32")
        self.assertEqual([(r.server_id, r.step, r.ok) for r in results], [
            ("first", "node", False), ("second", "prepare", True),
        ])

    def test_prepare_failure_and_timeout_are_reported(self) -> None:
        spec = PrepareSpec(command=("npm", "ci"), timeout_seconds=5)
        failing = FakeRunner([], prepare_code=2)
        result = preflight.run_prepare(server(self.cwd, spec), spec, runner=failing, which=which_all)
        self.assertFalse(result.ok)
        self.assertIn("exit 2", result.detail)

        timing_out = MagicMock(side_effect=subprocess.TimeoutExpired("npm", 5))
        result = preflight.run_prepare(server(self.cwd, spec), spec, runner=timing_out, which=which_all)
        self.assertIn("timed out", result.detail)

    def test_prepare_gets_only_the_child_environment(self) -> None:
        spec = PrepareSpec(command=("npm", "ci"))
        child = ServerConfig("child", "node", (), self.cwd, {"CHILD_SETTING": "1"}, ("MCP_HUB_TEST_INHERITED",))
        runner = FakeRunner([])
        hub_only = {"MCP_HUB_TEST_INHERITED": "yes", "MCP_HUB_TEST_HUB_ONLY": "hub-secret"}
        with patch.dict(preflight.os.environ, hub_only):
            preflight.run_prepare(child, spec, runner=runner, which=which_all)
        env = runner.calls[-1][1]["env"]
        self.assertEqual((env["CHILD_SETTING"], env["MCP_HUB_TEST_INHERITED"]), ("1", "yes"))
        self.assertNotIn("MCP_HUB_TEST_HUB_ONLY", env)

    def test_children_without_prepare_are_skipped(self) -> None:
        self.assertEqual(preflight.check_server(server(self.cwd, None), install=True, runner=FakeRunner([])), [])


class FindExecutableTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()

    def make(self, path: Path) -> Path:
        path.write_text("", encoding="utf-8")
        path.chmod(0o755)
        return path

    def test_never_searches_the_current_or_relative_directories(self) -> None:
        name = "tool.exe" if preflight.os.name == "nt" else "tool"
        self.make(self.root / name)
        cwd = preflight.os.getcwd()
        preflight.os.chdir(self.root)
        self.addCleanup(preflight.os.chdir, cwd)
        self.assertIsNone(preflight.find_executable("tool", path=preflight.os.pathsep.join([".", "bin", ""])))
        expected = self.make(self.bin / name)
        found = preflight.find_executable("tool", path=f".{preflight.os.pathsep}{self.bin}")
        self.assertTrue(found and Path(found).samefile(expected))

    def test_names_with_a_directory_are_not_resolved(self) -> None:
        self.assertIsNone(preflight.find_executable(str(Path("bin") / "tool"), path=str(self.bin)))
        self.assertIsNone(preflight.find_executable("", path=str(self.bin)))

    def test_windows_uses_pathext_and_skips_the_bare_script(self) -> None:
        self.make(self.bin / "npm")  # the POSIX shell script npm installs next to npm.cmd
        expected = self.make(self.bin / "npm.cmd")
        with patch.dict(preflight.os.environ, {"PATHEXT": ".exe;.cmd"}):
            self.assertEqual(preflight.find_executable("npm", path=str(self.bin), platform="win32"), str(expected))
            self.assertEqual(preflight.find_executable("npm.cmd", path=str(self.bin), platform="win32"), str(expected))


class PathRefreshTest(unittest.TestCase):
    def test_new_registry_entries_are_appended_once(self) -> None:
        env = {"PATH": "C1;C2" if preflight.os.pathsep == ";" else "C1:C2"}
        sep = preflight.os.pathsep
        preflight.refresh_windows_path(env, read_registry=lambda: ["C2", "Node", "node"], platform="win32")
        self.assertEqual(env["PATH"].split(sep), ["C1", "C2", "Node"])

    def test_other_platforms_are_left_alone(self) -> None:
        env = {"PATH": "a"}
        preflight.refresh_windows_path(env, read_registry=lambda: ["b"], platform="linux")
        self.assertEqual(env["PATH"], "a")


class MainTest(unittest.TestCase):
    def test_exit_code_and_report_reflect_results(self) -> None:
        failed = [preflight.CheckResult("child", "node", False, "missing")]
        audit = MagicMock()
        with (
            patch.object(preflight, "load_config"),
            patch.object(preflight, "refresh_windows_path"),
            patch.object(preflight, "run_preflight", return_value=failed),
            patch.object(preflight, "AuditLog", return_value=audit),
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(preflight.main(["--install"]), 1)
        self.assertIn("child", output.getvalue())
        audit.write.assert_called_once()
        self.assertEqual(audit.write.call_args.kwargs, {"level": 30, "server": "child", "step": "node", "ok": False})


if __name__ == "__main__":
    unittest.main()
