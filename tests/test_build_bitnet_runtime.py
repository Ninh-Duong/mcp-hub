from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

HUB_ROOT = Path(__file__).resolve().parents[1]


def load_script():
    spec = importlib.util.spec_from_file_location("build_bitnet_runtime", HUB_ROOT / "scripts" / "build_bitnet_runtime.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = load_script()


class BuildBitnetRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_the_model_and_source_are_pinned(self) -> None:
        self.assertRegex(build.BITNET_COMMIT, r"^[0-9a-f]{40}$")
        self.assertRegex(build.MODEL_REVISION, r"^[0-9a-f]{40}$")
        self.assertRegex(build.MODEL_SHA256, r"^[0-9a-f]{64}$")
        self.assertIn(f"/resolve/{build.MODEL_REVISION}/{build.MODEL_FILE}", build.MODEL_URL)
        self.assertTrue(build.MODEL_URL.startswith("https://huggingface.co/"))

    def test_dumpbin_output_is_read_as_dll_names(self) -> None:
        output = ("Dump of file llama-server.exe\n\n  Image has the following dependencies:\n\n"
                  "    llama.dll\n    KERNEL32.dll\n    VCRUNTIME140.dll\n\n  Summary\n")
        self.assertEqual(build.parse_dependents(output), ["llama.dll", "KERNEL32.dll", "VCRUNTIME140.dll"])

    def test_only_dlls_windows_lacks_are_shipped(self) -> None:
        beside, system = self.root / "build", self.root / "System32"
        beside.mkdir()
        system.mkdir()
        for name in ("KERNEL32.dll", "VCRUNTIME140.dll"):
            (system / name).write_bytes(b"x")
        (beside / "ggml.dll").write_bytes(b"x")
        self.assertTrue(build.needs_shipping("ggml.dll", beside, system))
        self.assertTrue(build.needs_shipping("VCRUNTIME140.dll", beside, system))  # a redistributable
        self.assertTrue(build.needs_shipping("libomp140.x86_64.dll", beside, system))
        self.assertFalse(build.needs_shipping("KERNEL32.dll", beside, system))
        self.assertFalse(build.needs_shipping("api-ms-win-crt-runtime-l1-1-0.dll", beside, system))

    def test_the_zip_is_flat_and_the_manifest_matches_it(self) -> None:
        files = []
        for name in ("llama-server.exe", "ggml.dll"):
            path = self.root / name
            path.write_bytes(name.encode())
            files.append(path)
        out = self.root / "dist"
        out.mkdir()
        runtime_zip = build.write_zip(files, out / build.RUNTIME_ZIP)
        with zipfile.ZipFile(runtime_zip) as archive:
            self.assertEqual(sorted(archive.namelist()), ["ggml.dll", "llama-server.exe"])
        build.write_outputs(runtime_zip, out)
        manifest = json.loads((out / build.MANIFEST).read_text(encoding="utf-8"))
        self.assertEqual(manifest["runtime"], {"sha256": build.sha256_file(runtime_zip), "size": runtime_zip.stat().st_size})
        self.assertEqual((manifest["model"]["file"], manifest["model"]["sha256"]), (build.MODEL_FILE, build.MODEL_SHA256))
        sha_line = Path(f"{runtime_zip}.sha256").read_text(encoding="ascii")
        self.assertEqual(sha_line, f"{build.sha256_file(runtime_zip)}  {build.RUNTIME_ZIP}")

    def test_the_manifest_matches_what_the_hub_installer_reads(self) -> None:
        from mcp_hub.bitnet import installer

        self.assertEqual((build.RUNTIME_ZIP, build.MANIFEST, build.SERVER_EXE),
                         (installer.RUNTIME_ASSET, installer.MANIFEST_ASSET, installer.SERVER_EXE))
        out = self.root / "dist"
        out.mkdir()
        runtime_zip = build.write_zip([self._file("llama-server.exe")], out / build.RUNTIME_ZIP)
        build.write_outputs(runtime_zip, out)
        release = {"assets": [
            {"name": build.MANIFEST, "browser_download_url": "https://example.com/m.json"},
            {"name": build.RUNTIME_ZIP, "browser_download_url": "https://example.com/r.zip"},
        ]}
        body = (out / build.MANIFEST).read_bytes()

        class Response:
            def __init__(self, data: bytes) -> None:
                self.data = data

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self, _limit: int) -> bytes:
                return self.data

        plan = installer.resolve_plan(lambda _request, timeout: Response(body), release, 5)
        self.assertEqual((plan.model.name, plan.model.size), (build.MODEL_FILE, build.MODEL_SIZE))
        self.assertEqual(plan.runtime.url, "https://example.com/r.zip")

    def test_setup_logs_are_printed_on_failure(self) -> None:
        logs = self.root / "logs"
        logs.mkdir()
        (logs / "compile.log").write_text("line\n" * 100 + "error: boom\n", encoding="utf-8")
        with patch("builtins.print") as printed:
            build.print_setup_logs(logs, tail=3)
        shown = "\n".join(str(call.args[0]) for call in printed.call_args_list)
        self.assertIn("compile.log", shown)
        self.assertIn("error: boom", shown)

    def test_main_refuses_other_platforms(self) -> None:
        with patch.object(build.sys, "platform", "linux"), self.assertRaises(SystemExit):
            build.main(["--out", str(self.root / "dist")])

    def _file(self, name: str) -> Path:
        path = self.root / name
        path.write_bytes(b"x")
        return path


if __name__ == "__main__":
    unittest.main()
