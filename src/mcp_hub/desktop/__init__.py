"""Desktop status app: a small window showing whether the Hub runs and which child MCPs are connected.

A client of the local HTTP service, like the stdio bridge; core modules never import it.
Run with `python -m mcp_hub.desktop`; `hubctl install` starts it at every logon. See docs/desktop-app.md.
"""
