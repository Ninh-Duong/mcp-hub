# PyInstaller spec for the desktop status app. Run it through scripts/build_desktop.py, not by hand.
#
# The app imports only mcp_hub.desktop (tests/test_desktop_client.py enforces it), so the bundle holds
# the app, Qt (QtCore, QtGui, QtWidgets), and the two locale files, and nothing of the Hub.
import os
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parents[1]
LOCALES = ROOT / "src" / "mcp_hub" / "dashboard" / "locales"
# i18n.LOCALES_DIR resolves to mcp_hub/dashboard/locales inside the bundle, as it does in the checkout.
LOCALE_DEST = "mcp_hub/dashboard/locales"

analysis = Analysis(
    [str(Path(SPECPATH) / "entry.py")],
    pathex=[str(ROOT / "src")],
    datas=[(str(LOCALES / "en.json"), LOCALE_DEST), (str(LOCALES / "vi.json"), LOCALE_DEST)],
    excludes=["tkinter", "unittest", "pydoc"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="mcp-hub-status",
    console=False,
    icon=os.environ.get("MCP_HUB_STATUS_ICON"),
)
COLLECT(exe, analysis.binaries, analysis.datas, name="mcp-hub-status")
