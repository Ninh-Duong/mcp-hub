# Research

- Decision: extend Plan instead of adding a session store. Rationale: bounded identity/lifecycle already exists. Durable jobs were rejected for the client-controlled pilot.
- Decision: record execution facts separately from client-reported findings. Arbitrary child text cannot prove cache coverage or root cause; no extra model loop is added.
- Decision: stage/tool lists in playbook frontmatter, not interpretation of Markdown prose.
- Decision: per-workflow execution lock, rejecting report/finish while busy. Batch items in one stage can run in parallel.
