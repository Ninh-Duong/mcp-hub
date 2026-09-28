# mcp-hub

This folder is the local MCP gateway used by an AI agent. The Hub exposes only enabled, allowlisted tools from configured child MCP servers and records operational events without logging credentials or full tool payloads.

## Current scaffold

This first base establishes the configuration, documentation, secret, and logging boundaries. Runtime code and a language-specific package are intentionally not added yet.

## Layout

- `AGENTS.md` — operating rules for agents working in this repository.
- `config/hub.example.json` — safe template for child servers, features, and exposed tools.
- `config/mcps/` — safe per-MCP templates and notes.
- `config/private/` — local per-MCP configuration; ignored by Git.
- `docs/` — architecture, security, logging, and child MCP contracts.
- `src/` — planned runtime areas; see `src/README.md`.
- `logs/` — local runtime logs; ignored by Git.

## Local setup

1. Copy `config/hub.example.json` to `config/private/hub.json`.
2. Copy each needed `config/mcps/<id>.example.json` to `config/private/<id>.json` and fill it according to that MCP's documentation.
3. Put repository paths and credential values in local environment variables or private local files. Never commit them.
4. Add the child MCP contract under `docs/mcps/<id>.md` before enabling its tools.

The runtime implementation will define and validate the exact config schema before any child server is launched.
