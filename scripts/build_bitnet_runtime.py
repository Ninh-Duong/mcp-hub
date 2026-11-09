"""Build the prebuilt BitNet runtime that the desktop app installs (CI only; see docs/bitnet.md).

Writes to `--out` (default `dist/`):
- bitnet-runtime-win-x64.zip: llama-server.exe from microsoft/BitNet (pinned commit) with every DLL it needs
  beyond Windows itself, including the Visual C++ runtime, so a PC without build tools can run it;
- bitnet-runtime-win-x64.zip.sha256;
- bitnet-manifest.json: the runtime's SHA-256 and size, and the pinned model URL with its SHA-256 and size.

Before writing the manifest it unpacks the zip into an empty folder, starts llama-server there with the model,
and waits for `GET /health` to answer 200, so a runtime that cannot start is never published.

    uv run python scripts/build_bitnet_runtime.py --out dist

Needs git, CMake, uv, and Visual Studio 2022 with its Clang (ClangCL) tools: the windows-latest runner has them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

BITNET_REPOSITORY = "https://github.com/microsoft/BitNet"
BITNET_COMMIT = "0b341e582afbf9e1011f24744b554c96a3477eb5"
MODEL_REPOSITORY = "microsoft/BitNet-b1.58-2B-4T-gguf"
MODEL_REVISION = "a1f2f1c765812aa8af3f6eda4a313707064bba15"
MODEL_FILE = "ggml-model-i2_s.gguf"
MODEL_SHA256 = "4221b252fdd5fd25e15847adfeb5ee88886506ba50b8a34548374492884c2162"
MODEL_SIZE = 1187801280
MODEL_URL = f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/{MODEL_REVISION}/{MODEL_FILE}"
RUNTIME_ZIP = "bitnet-runtime-win-x64.zip"
MANIFEST = "bitnet-manifest.json"
SERVER_EXE = "llama-server.exe"
# Shipped next to llama-server: Windows has these only when a Visual C++ redistributable is installed.
REDISTRIBUTABLE_PREFIXES = ("vcruntime", "msvcp", "concrt", "vcomp", "libomp")
HEALTH_TIMEOUT_SECONDS = 180
CHUNK = 1 << 20


def run(command: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_source(work: Path) -> Path:
    source = work / "BitNet"
    if not source.exists():
        run(["git", "clone", "--recursive", BITNET_REPOSITORY, str(source)])
    run(["git", "checkout", BITNET_COMMIT], cwd=source)
    run(["git", "submodule", "update", "--init", "--recursive"], cwd=source)
    return source


def download_model(model_dir: Path) -> Path:
    """The pinned model, checked against its SHA-256 (also kept where setup_env.py looks for it)."""
    model_dir.mkdir(parents=True, exist_ok=True)
    target = model_dir / MODEL_FILE
    if not (target.is_file() and target.stat().st_size == MODEL_SIZE and sha256_file(target) == MODEL_SHA256):
        print(f"Downloading {MODEL_FILE} ...", flush=True)
        request = urllib.request.Request(MODEL_URL, headers={"User-Agent": "mcp-hub-bitnet-build"})
        with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as handle:  # noqa: S310
            shutil.copyfileobj(response, handle, CHUNK)
    if target.stat().st_size != MODEL_SIZE or sha256_file(target) != MODEL_SHA256:
        raise SystemExit("The downloaded model does not match its pinned SHA-256.")
    return target


def build_server(source: Path, model_dir: Path) -> Path:
    """setup_env.py generates the BitNet kernels and builds llama.cpp with ClangCL; the model is already there.

    Its requirements.txt (torch, transformers) only converts models, which the prebuilt GGUF makes unnecessary;
    setup_env.py needs pip itself (it installs gguf-py), so the venv is seeded with pip.
    """
    venv = source / ".venv"
    run(["uv", "venv", "--seed", str(venv), "--python", "3.11"])
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    try:
        run([str(python), "setup_env.py", "-md", str(model_dir.resolve()), "-q", "i2_s"], cwd=source)
    except subprocess.CalledProcessError:
        print_setup_logs(source / "logs")
        raise
    servers = sorted((source / "build").rglob(SERVER_EXE))
    if not servers:
        raise SystemExit(f"{SERVER_EXE} was not built.")
    return servers[0]


def print_setup_logs(folder: Path, tail: int = 80) -> None:
    """setup_env.py writes each step's output to logs/<step>.log; show their ends in the CI log."""
    for log in sorted(folder.glob("*.log")):
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        print(f"----- {log.name} (last {tail} lines) -----", flush=True)
        print("\n".join(lines[-tail:]), flush=True)


def dll_dependencies(binary: Path) -> list[str]:
    """DLL names a binary imports, read with dumpbin from Visual Studio."""
    output = subprocess.run([find_dumpbin(), "/dependents", str(binary)], check=True, capture_output=True,
                            text=True).stdout
    return parse_dependents(output)


def parse_dependents(output: str) -> list[str]:
    return [line.strip() for line in output.splitlines() if line.strip().lower().endswith(".dll")]


def find_dumpbin() -> str:
    found = shutil.which("dumpbin")
    if found:
        return found
    vswhere = Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    install = subprocess.run([str(vswhere), "-latest", "-property", "installationPath"], check=True,
                             capture_output=True, text=True).stdout.strip()
    tools = sorted((Path(install) / "VC" / "Tools" / "MSVC").glob("*/bin/Hostx64/x64/dumpbin.exe"))
    if not tools:
        raise SystemExit("dumpbin was not found.")
    return str(tools[-1])


def needs_shipping(name: str, beside: Path, system: Path) -> bool:
    """Ship a DLL that is next to the build or is a Visual C++ runtime; Windows' own DLLs stay out."""
    lower = name.lower()
    if lower.startswith(("api-ms-win-", "ext-ms-")):
        return False
    return (beside / name).is_file() or lower.startswith(REDISTRIBUTABLE_PREFIXES) or not (system / name).is_file()


def locate_dll(name: str, beside: Path, system: Path) -> Path:
    for folder in (beside, system, *(Path(item) for item in os.environ.get("PATH", "").split(os.pathsep) if item)):
        candidate = folder / name
        if candidate.is_file():
            return candidate
    raise SystemExit(f"{name} is needed by llama-server but was not found.")


def collect_runtime(server: Path, stage: Path) -> list[Path]:
    """llama-server and, transitively, every DLL it needs that Windows does not provide."""
    system = Path(os.environ["SystemRoot"]) / "System32"
    stage.mkdir(parents=True, exist_ok=True)
    shipped, pending = [shutil.copy2(server, stage / SERVER_EXE)], [server]
    seen = {SERVER_EXE.lower()}
    while pending:
        for name in dll_dependencies(pending.pop()):
            if name.lower() in seen or not needs_shipping(name, server.parent, system):
                continue
            seen.add(name.lower())
            source = locate_dll(name, server.parent, system)
            shipped.append(shutil.copy2(source, stage / name))
            pending.append(source)
    return [Path(item) for item in shipped]


def write_zip(files: list[Path], target: Path) -> Path:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, path.name)
    return target


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:  # noqa: S310
            return response.status == 200
    except OSError:
        return False


def smoke_test(runtime_zip: Path, model: Path) -> None:
    """Unpack into an empty folder and start it there: a missing DLL or a crash fails the build."""
    with tempfile.TemporaryDirectory() as folder:
        with zipfile.ZipFile(runtime_zip) as archive:
            archive.extractall(folder)
        port = free_port()
        process = subprocess.Popen([str(Path(folder) / SERVER_EXE), "-m", str(model), "--host", "127.0.0.1",
                                    "--port", str(port), "-c", "512"], cwd=folder)
        try:
            deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise SystemExit(f"llama-server exited with code {process.returncode} during the smoke test.")
                if healthy(port):
                    print("llama-server answered /health with 200.", flush=True)
                    return
                time.sleep(2)
            raise SystemExit("llama-server did not become healthy in time.")
        finally:
            process.kill()
            process.wait(timeout=30)


def manifest(runtime_zip: Path) -> dict:
    return {
        "version": 1,
        "runtime": {"sha256": sha256_file(runtime_zip), "size": runtime_zip.stat().st_size},
        "model": {"file": MODEL_FILE, "url": MODEL_URL, "sha256": MODEL_SHA256, "size": MODEL_SIZE,
                  "license": "MIT"},
        "source": {"repository": BITNET_REPOSITORY, "commit": BITNET_COMMIT, "model_revision": MODEL_REVISION},
    }


def write_outputs(runtime_zip: Path, out: Path) -> None:
    digest = sha256_file(runtime_zip)
    Path(f"{runtime_zip}.sha256").write_text(f"{digest}  {runtime_zip.name}", encoding="ascii")
    (out / MANIFEST).write_text(json.dumps(manifest(runtime_zip), indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("dist"))
    parser.add_argument("--work", type=Path, default=Path("build") / "bitnet")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        raise SystemExit("The BitNet runtime is built on Windows only.")
    args.out.mkdir(parents=True, exist_ok=True)
    source = fetch_source(args.work)
    model_dir = source / "models" / "BitNet-b1.58-2B-4T"
    model = download_model(model_dir)
    server = build_server(source, model_dir)
    files = collect_runtime(server, args.work / "stage")
    runtime_zip = write_zip(files, args.out / RUNTIME_ZIP)
    smoke_test(runtime_zip, model)
    write_outputs(runtime_zip, args.out)
    print(f"Wrote {runtime_zip.name}, its .sha256, and {MANIFEST} to {args.out}.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
