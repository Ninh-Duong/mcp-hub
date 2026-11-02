"""Build the desktop status app's installer: dist/mcp-hub-status-setup-<version>.exe.

    uv run --isolated --group build python scripts/build_desktop.py

Steps: draw the app icon with Qt, bundle the app with PyInstaller (packaging/desktop/mcp-hub-status.spec),
check that the bundle carries the Visual C++ runtime, starts, and reports the project version, then compile packaging/desktop/installer.iss with Inno Setup 6 (winget install JRSoftware.InnoSetup).
Everything is written below build/ and dist/, which git ignores; nothing of config/private/ is read.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from mcp_hub.desktop.constants import APP_NAME, EXE_NAME

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging" / "desktop"
BUILD = ROOT / "build" / "desktop"
BUNDLE = BUILD / "dist" / APP_NAME
DIST = ROOT / "dist"
ICON_SIZE = 256
BUNDLE_CHECK_SECONDS = 60
# The Microsoft Visual C++ runtime that Python and Qt load, shipped inside the bundle so the PC needs no install.
BUNDLED_RUNTIME = (
    "VCRUNTIME140.dll", "VCRUNTIME140_1.dll",
    "PySide6/MSVCP140.dll", "PySide6/MSVCP140_1.dll", "PySide6/MSVCP140_2.dll",
)
INNO_HINT ="Inno Setup 6 is missing. Install it once with: winget install JRSoftware.InnoSetup"
# Where winget (per user) and the classic installer put ISCC.exe when it is not on PATH.
INNO_LOCATIONS = (
    ("LOCALAPPDATA", "Programs/Inno Setup 6/ISCC.exe"),
    ("ProgramFiles(x86)", "Inno Setup 6/ISCC.exe"),
    ("ProgramFiles", "Inno Setup 6/ISCC.exe"),
)


def project_version(pyproject: Path = ROOT / "pyproject.toml") -> str:
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]


def draw_icon(path: Path) -> Path:
    """The app icon: a hub dot linked to three child dots, white on the theme's blue rounded square."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPen
    from PySide6.QtWidgets import QApplication

    from mcp_hub.desktop import theme

    # A QApplication (not a bare QGuiApplication), so a later widget app in the same process can reuse it.
    _app = QApplication.instance() or QApplication([])
    image = QImage(ICON_SIZE, ICON_SIZE, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(theme.LIGHT["blue"]))
    painter.drawRoundedRect(QRectF(8, 8, ICON_SIZE - 16, ICON_SIZE - 16), 56, 56)
    center, children = QPointF(128, 140), (QPointF(128, 64), QPointF(62, 186), QPointF(194, 186))
    painter.setPen(QPen(QColor("white"), 14))
    for child in children:
        painter.drawLine(center, child)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("white"))
    painter.drawEllipse(center, 34, 34)
    for child in children:
        painter.drawEllipse(child, 22, 22)
    painter.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(path), "ICO"):
        raise SystemExit("The app icon could not be written.")
    return path


def find_iscc(which=shutil.which, environ: dict[str, str] | None = None) -> Path:
    environ = os.environ if environ is None else environ
    found = which("ISCC")
    if found:
        return Path(found)
    for variable, relative in INNO_LOCATIONS:
        folder = environ.get(variable)
        if folder and (Path(folder) / relative).is_file():
            return Path(folder) / relative
    raise SystemExit(INNO_HINT)


def pyinstaller_command() -> list[str]:
    return [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", str(BUILD / "dist"), "--workpath", str(BUILD / "work"),
        str(PACKAGING / f"{APP_NAME}.spec"),
    ]


def iscc_command(iscc: Path, version: str, icon: Path) -> list[str]:
    return [
        str(iscc), f"/DAppVersion={version}", f"/DSourceDir={BUNDLE}",
        f"/DOutputDir={DIST}", f"/DIconFile={icon}", str(PACKAGING / "installer.iss"),
    ]


def check_runtime_libraries(bundle: Path = BUNDLE) -> None:
    """The installer tells the user nothing else needs installing: the Visual C++ runtime must be in the bundle."""
    missing = [name for name in BUNDLED_RUNTIME if not (bundle / "_internal" / name).is_file()]
    if missing:
        raise SystemExit(f"The bundle lacks the Visual C++ runtime: {', '.join(missing)}")


def check_bundle(run=subprocess.run) -> None:
    """Start the bundled exe once in screenshot mode: a frozen-only failure stops the build, not the user."""
    out = BUILD / "check"
    shutil.rmtree(out, ignore_errors=True)
    exe = BUNDLE / EXE_NAME
    command = [str(exe), "--screenshot", str(out), "--states", "running", "--lang", "en", "--theme", "light"]
    try:
        # The real platform: draw_icon set QT_QPA_PLATFORM=offscreen for this process only.
        environ = {key: value for key, value in os.environ.items() if key != "QT_QPA_PLATFORM"}
        result = run(command, cwd=ROOT, env=environ, timeout=BUNDLE_CHECK_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise SystemExit("The bundled app did not finish its self-check in time.") from exc
    if result.returncode != 0 or not list(out.glob("*.png")):
        raise SystemExit("The bundled app failed its self-check; run it from build/desktop/dist to see the error.")


def check_bundle_version(version: str, run=subprocess.run) -> None:
    """The bundled exe must report the project version: the update check compares it with the latest Release."""
    try:
        result = run([str(BUNDLE / EXE_NAME), "--version"], cwd=ROOT, capture_output=True, text=True,
                     timeout=BUNDLE_CHECK_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise SystemExit("The bundled app did not report its version in time.") from exc
    reported = (result.stdout or "").strip()
    if result.returncode != 0 or reported != version:
        raise SystemExit(f"The bundled app reports version {reported or 'nothing'}, not {version}; "
                         "the package metadata is missing from the bundle.")


def main() -> int:
    iscc = find_iscc()  # fail before the slow bundle step
    version = project_version()
    icon = draw_icon(BUILD / f"{APP_NAME}.ico")
    subprocess.run(pyinstaller_command(), check=True, cwd=ROOT, env={**os.environ, "MCP_HUB_STATUS_ICON": str(icon)})
    check_runtime_libraries()
    check_bundle()
    check_bundle_version(version)
    subprocess.run(iscc_command(iscc, version, icon), check=True, cwd=ROOT)
    print(f"Built {DIST / f'{APP_NAME}-setup-{version}.exe'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
