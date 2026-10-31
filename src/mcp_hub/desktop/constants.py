"""Names the desktop app shares with its installer, its build, and hubctl.

packaging/desktop/installer.iss, packaging/desktop/mcp-hub-status.spec, scripts/build_desktop.py, and
scripts/hubctl.ps1 cannot import Python, so they repeat these values; tests/test_bootstrap_scripts.py checks
that every copy matches.
"""
from __future__ import annotations

# The bundle folder and exe name, and the installer's file name prefix.
APP_NAME = "mcp-hub-status"
EXE_NAME = f"{APP_NAME}.exe"
# The running app holds this mutex: a second start exits, and setup and uninstall ask to close the app.
MUTEX_NAME = f"Local\\{APP_NAME}"
# Shared by the process and its Start Menu and desktop shortcuts, so the taskbar groups them.
APP_USER_MODEL_ID = "mcp-hub.status"
# The app's own sign-in entry: this value under the user's Run key.
RUN_VALUE = APP_NAME
# `%APPDATA%/<this>/desktop.json` keeps the remembered look; the uninstaller removes the folder.
APP_DATA_DIR = APP_NAME
# The Hub's scheduled task, which Start Hub runs exactly as a sign-in does.
HUB_TASK = "mcp-hub"
