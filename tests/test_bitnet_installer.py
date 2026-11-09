from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

from mcp_hub.bitnet import installer
from mcp_hub.bitnet.installer import Installer, InstallError
from mcp_hub.bitnet.sidecar import _read_install
from mcp_hub.settings import BitnetSettings

API = "https://api.github.com/repos/owner/mcp-hub/releases"
MANIFEST_URL = "https://github.com/owner/mcp-hub/releases/download/v1.2.3/bitnet-manifest.json"
RUNTIME_URL = "https://github.com/owner/mcp-hub/releases/download/v1.2.3/bitnet-runtime-win-x64.zip"
MODEL_URL = "https://huggingface.co/example/model/resolve/abc123/ggml-model-i2_s.gguf"


def runtime_zip(names: tuple[str, ...] = ("bin/llama-server.exe", "bin/ggml.dll")) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, b"binary " + name.encode())
    return buffer.getvalue()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeResponse(io.BytesIO):
    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


class FakeOpener:
    """URL -> bytes, or an exception to raise; records every URL asked for."""

    def __init__(self, responses: dict[str, object]) -> None:
        self.responses, self.calls = responses, []

    def __call__(self, request, timeout: float) -> FakeResponse:
        url = request.full_url
        self.calls.append(url)
        value = self.responses.get(url, HTTPError(url, 404, "Not Found", {}, None))
        if isinstance(value, Exception):
            raise value
        return FakeResponse(value)


def release_files(runtime: bytes, model: bytes, *, model_sha: str | None = None) -> dict[str, object]:
    manifest = {
        "version": 1,
        "runtime": {"sha256": sha(runtime), "size": len(runtime)},
        "model": {"file": "ggml-model-i2_s.gguf", "url": MODEL_URL, "sha256": model_sha or sha(model), "size": len(model)},
    }
    release = {"tag_name": "v1.2.3", "assets": [
        {"name": "bitnet-manifest.json", "browser_download_url": MANIFEST_URL},
        {"name": "bitnet-runtime-win-x64.zip", "browser_download_url": RUNTIME_URL},
    ]}
    return {
        f"{API}/tags/v1.2.3": json.dumps(release).encode(),
        MANIFEST_URL: json.dumps(manifest).encode(),
        RUNTIME_URL: runtime,
        MODEL_URL: model,
    }


class InstallerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.settings = BitnetSettings(release_repository="owner/mcp-hub", min_free_bytes=10)
        self.runtime, self.model = runtime_zip(), b"gguf model bytes" * 1000
        self.steps: list[tuple[str, int | None]] = []

    def installer(self, opener: FakeOpener, *, free: int = 10**12, platform: str = "win32",
                  version: str | None = "1.2.3") -> Installer:
        return Installer(self.root, self.settings, opener=opener, version=lambda: version,
                         disk_usage=lambda _path: SimpleNamespace(free=free), platform=platform)

    def run_install(self, opener: FakeOpener, **kwargs: object) -> Path:
        return self.installer(opener, **kwargs).run(lambda step, pct: self.steps.append((step, pct)))

    def assert_fails(self, code: str, opener: FakeOpener, **kwargs: object) -> None:
        with self.assertRaises(InstallError) as caught:
            self.run_install(opener, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertIsNone(_read_install(self.root))

    def test_installs_the_runtime_and_model_and_the_sidecar_finds_them(self) -> None:
        path = self.run_install(FakeOpener(release_files(self.runtime, self.model)))
        self.assertEqual(path, self.root / "vendor" / "bitnet" / "install.json")
        server, model = _read_install(self.root)
        self.assertEqual(server.name, "llama-server.exe")
        self.assertEqual(model.read_bytes(), self.model)
        self.assertTrue((server.parent / "ggml.dll").is_file())
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertFalse(Path(data["server"]).is_absolute())  # relative to the Hub root, never a machine path
        self.assertFalse((self.root / "vendor" / "bitnet" / ".partial").exists())
        self.assertEqual([step for step, _ in self.steps][0], "prepare")
        self.assertIn(("model", 100), self.steps)
        self.assertEqual(self.steps[-1], ("extract", None))

    def test_an_unreleased_version_uses_the_latest_release(self) -> None:
        files = release_files(self.runtime, self.model)
        files[f"{API}/latest"] = files.pop(f"{API}/tags/v1.2.3")
        opener = FakeOpener(files)
        self.run_install(opener)
        self.assertEqual(opener.calls[:2], [f"{API}/tags/v1.2.3", f"{API}/latest"])

    def test_a_wrong_checksum_deletes_the_file_and_installs_nothing(self) -> None:
        opener = FakeOpener(release_files(self.runtime, self.model, model_sha="0" * 64))
        self.assert_fails("checksum", opener)
        model_dir = self.root / "vendor" / "bitnet" / "models" / self.settings.model
        self.assertEqual(list(model_dir.iterdir()), [])

    def test_a_body_larger_than_the_manifest_says_is_refused(self) -> None:
        files = release_files(self.runtime, self.model)
        files[MODEL_URL] = self.model + b"extra"
        self.assert_fails("checksum", FakeOpener(files))

    def test_network_errors_are_reported_without_details(self) -> None:
        files = release_files(self.runtime, self.model)
        files[MODEL_URL] = URLError("offline")
        self.assert_fails("network", FakeOpener(files))
        self.assert_fails("network", FakeOpener({f"{API}/tags/v1.2.3": OSError("reset")}))

    def test_too_little_disk_space_stops_before_any_download(self) -> None:
        opener = FakeOpener(release_files(self.runtime, self.model))
        self.assert_fails("disk", opener, free=1)
        self.assertEqual(opener.calls, [])

    def test_only_windows_is_supported(self) -> None:
        self.assert_fails("platform", FakeOpener({}), platform="linux")

    def test_a_release_without_the_bitnet_files_is_reported(self) -> None:
        files = release_files(self.runtime, self.model)
        files[f"{API}/tags/v1.2.3"] = json.dumps({"assets": []}).encode()
        self.assert_fails("release", FakeOpener(files))

    def test_a_manifest_with_a_bad_model_name_is_refused(self) -> None:
        files = release_files(self.runtime, self.model)
        manifest = json.loads(files[MANIFEST_URL])
        manifest["model"]["file"] = "../../evil.gguf"
        files[MANIFEST_URL] = json.dumps(manifest).encode()
        self.assert_fails("release", FakeOpener(files))

    def test_a_runtime_zip_that_escapes_its_folder_is_refused(self) -> None:
        bad = runtime_zip(("../outside.exe", "llama-server.exe"))
        self.assert_fails("extract", FakeOpener(release_files(bad, self.model)))
        self.assertFalse((self.root / "vendor" / "bitnet" / "outside.exe").exists())

    def test_a_runtime_without_llama_server_is_refused(self) -> None:
        self.assert_fails("extract", FakeOpener(release_files(runtime_zip(("readme.txt",)), self.model)))

    def test_a_second_run_reuses_files_that_already_match(self) -> None:
        files = release_files(self.runtime, self.model)
        self.run_install(FakeOpener(files))
        (self.root / "vendor" / "bitnet" / "install.json").unlink()
        opener = FakeOpener(files)
        self.run_install(opener)
        self.assertNotIn(MODEL_URL, opener.calls)
        self.assertIsNotNone(_read_install(self.root))

    def test_cancel_stops_the_download(self) -> None:
        job = self.installer(FakeOpener(release_files(self.runtime, self.model)))
        job.cancel.set()
        with self.assertRaises(InstallError) as caught:
            job.run(lambda *_: None)
        self.assertEqual(caught.exception.code, "cancelled")
        self.assertIsNone(_read_install(self.root))

    def test_hub_version_reads_the_package(self) -> None:
        self.assertIsInstance(installer.hub_version(), (str, type(None)))


if __name__ == "__main__":
    unittest.main()
