# MCP Contract

hub__plan adds issue_key, pr_url, service, query, glob, path and continue_from. Its existing rcid/project/env/since/until/writes/confirmation contract remains. A workflow plan may wait for missing context; no default time is inferred. continue_from must name an idle unfinished plan with the same task/playbook and immutable scope. Only additional declared write permissions may differ.

hub__workflow preserves status/report/finish and adds provide_input with an input object. Report exposes the current node's decisions and may fill only explicitly requested bindings. Status includes required inputs and permissions, allowed tools, transitions, receipts and result completeness. All interpretations and client-supplied bindings are client-reported. Final supported requires successful relevant evidence; insufficient/blocked remain available.

Tool calls retain normal routing, schema, side-effect, timeout, cache and result-shape handling. Batch validation occurs before execution and never crosses a dependency. Full-result paging does not replay expired calls.
