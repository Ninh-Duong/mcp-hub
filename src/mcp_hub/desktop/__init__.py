"""Desktop status app: a small window showing whether the Hub runs and which child MCPs are connected.

A client of the local HTTP service, like the stdio bridge; core modules never import it.
Installed with its own setup (packaging/desktop/), or run from source with `python -m mcp_hub.desktop`.
See docs/desktop-app.md.
"""
