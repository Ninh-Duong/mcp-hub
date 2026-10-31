import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import check_leaks


class CheckLeaksTest(unittest.TestCase):
    def run_check(self, root: Path, files: list[str]) -> tuple[int, str]:
        output = io.StringIO()
        original_cwd = Path.cwd()
        os.chdir(root)
        try:
            with patch.object(check_leaks.subprocess, "run", return_value=SimpleNamespace(stdout="\n".join(files) + "\n")):
                with contextlib.redirect_stdout(output):
                    code = check_leaks.main()
        finally:
            os.chdir(original_cwd)
        return code, output.getvalue()

    def test_only_exact_example_com_domain_is_allowlisted(self) -> None:
        root = Path(tempfile.mkdtemp())
        try:
            spoofed = "person@example.com" + ".evil"
            org_email = "person@corp" + ".example"
            (root / "emails.md").write_text("person@example.com\nperson@EXAMPLE.COM\n" + spoofed + "\n" + org_email + "\n", encoding="utf-8")
            code, output = self.run_check(root, ["emails.md"])
            self.assertEqual(code, 1)
            self.assertNotIn("emails.md:1: email", output)
            self.assertNotIn("emails.md:2: email", output)
            self.assertIn("emails.md:3: email", output)
            self.assertIn("emails.md:4: email", output)
            self.assertNotIn(spoofed, output)
            self.assertNotIn(org_email, output)
        finally:
            (root / "emails.md").unlink(missing_ok=True)
            root.rmdir()

    def test_nested_private_directories_are_detected(self) -> None:
        root = Path(tempfile.mkdtemp())
        try:
            paths = (
                "nested/config/private/child.json",
                "nested/.local/cache.json",
                "nested/logs/run.jsonl",
                "nested/.env.production",
            )
            for name in paths:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n", encoding="utf-8")
            code, output = self.run_check(root, list(paths))
            self.assertEqual(code, 1)
            for path in paths:
                with self.subTest(path=path):
                    self.assertIn(f"{path}: private file is tracked", output)
        finally:
            for path in sorted(root.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            root.rmdir()

    def test_uv_lock_is_scanned(self) -> None:
        root = Path(tempfile.mkdtemp())
        try:
            external = "person@corp" + ".example"
            (root / "uv.lock").write_text(external + "\n", encoding="utf-8")
            code, output = self.run_check(root, ["uv.lock"])
            self.assertEqual(code, 1)
            self.assertIn("uv.lock:1: email", output)
        finally:
            (root / "uv.lock").unlink(missing_ok=True)
            root.rmdir()


if __name__ == "__main__":
    unittest.main()
