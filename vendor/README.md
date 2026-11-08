# Vendor directory

This directory holds third-party sources, builds, and models downloaded by
`python -m mcp_hub.preflight --install`, such as `vendor/bitnet/` when the
BitNet extension is enabled.

- Nothing in this directory is tracked by git (except this README file).
- The contents are safe to delete; they will be re-created on the next install.
- Agents must never read or edit files inside this directory.
