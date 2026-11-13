# Third-party notices

The mcp-hub Hub is MIT (see [LICENSE](LICENSE)). The desktop status app (`src/mcp_hub/desktop`) and its installer are
licensed under the GNU General Public License version 3 only (see [src/mcp_hub/desktop/LICENSE](src/mcp_hub/desktop/LICENSE)),
because the app uses the libraries below, which are GPL-3.0.

The complete corresponding source code of the desktop app and its installer is this repository.

| Component | Used by | License |
|---|---|---|
| PySide6, Shiboken6 (Qt for Python, Qt 6) | desktop app | LGPL-3.0-only, or GPL-2.0-only, or GPL-3.0-only. The app uses the GPL-3.0-only option. |
| PySide6-Fluent-Widgets 1.11.3 (zhiyiYo) | desktop app | GPL-3.0 |
| PySideSix-Frameless-Window 0.8.2 (zhiyiYo) | desktop app (through PySide6-Fluent-Widgets) | LGPL-3.0 |
| darkdetect 0.8.0 | desktop app (through PySide6-Fluent-Widgets) | BSD-3-Clause |

The license text of each component is in its own distribution, shipped with the package.
