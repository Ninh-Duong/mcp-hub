from __future__ import annotations

import io
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from scripts import package_qc


class PackageQcTest(unittest.TestCase):
    def test_runs_offline_checks_without_live_smoke(self) -> None:
        with (
            patch.object(package_qc.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run,
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(package_qc.main([]), 0)

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual([command[1:] for command in commands], [
            ["-m", "unittest", "discover", "tests"],
            ["scripts/check_leaks.py"],
            ["scripts/check_code_standards.py"],
            ["-m", "compileall", "-q", "src", "scripts", "tests"],
        ])

    def test_live_smoke_is_opt_in_and_runs_after_offline_checks(self) -> None:
        with (
            patch.object(package_qc.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run,
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(package_qc.main(["--live-smoke"]), 0)

        self.assertEqual(run.call_args_list[-1].args[0][1:], ["scripts/smoke.py"])

    def test_stops_on_first_failed_check(self) -> None:
        with (
            patch.object(package_qc.subprocess, "run", side_effect=[
                subprocess.CompletedProcess([], 0),
                subprocess.CompletedProcess([], 7),
            ]) as run,
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(package_qc.main([]), 7)

        self.assertEqual(run.call_count, 2)

    def test_reports_subprocess_start_failure(self) -> None:
        with (
            patch.object(package_qc.subprocess, "run", side_effect=OSError("missing runner")),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(package_qc.main([]), 1)


if __name__ == "__main__":
    unittest.main()
