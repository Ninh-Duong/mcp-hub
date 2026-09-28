# Child MCP contracts

Add one `<server-id>.md` file for every child MCP. Include:

- Purpose and owning repository.
- How the Hub starts/connects to it.
- Exposed tools and their input/output shape.
- Read-only or write side effects for each tool.
- Required environment variable names or private config path, never secret values.
- Common errors and how to diagnose them using the Hub request ID.
- Any access limits or operations requiring extra care.
