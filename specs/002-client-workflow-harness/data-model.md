# Data Model

Plan owns optional Workflow. Workflow owns stage/tool definitions, rcid, bounded receipt records, client-reported decisions, counters, time and a lock. Receipts contain ID, tool, stage, execution outcome, elapsed milliseconds and optional full-result ID, never raw arguments or evidence.

Stages: cache_list -> cache_read or logs -> source_search -> source_read -> ready -> finished. Findings after successful cache_read/logs can skip source explicitly. Failed receipts support blocked finishes. State is process-local, bounded by plans and configured receipt limit. No automatic replay after expiry, eviction or restart.
