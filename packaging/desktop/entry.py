"""Entry point of the installed desktop status app (PyInstaller); the same as `python -m mcp_hub.desktop`."""
from mcp_hub.desktop.cli import main

raise SystemExit(main())
