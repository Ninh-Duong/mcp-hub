# Vendor directory

This directory holds third-party runtimes and models that the Hub downloads,
such as `vendor/bitnet/` when BitNet is installed from the desktop app
(Settings > Hub > Install BitNet; see docs/bitnet.md).

- Nothing in this directory is tracked by git (except this README file).
- The contents are safe to delete; they will be re-created on the next install.
- Agents must never read or edit files inside this directory.
