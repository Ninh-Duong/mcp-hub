# MCP Contract

hub__plan accepts rcid for the pilot, requires project/env, fixes an absolute UTC since/until window (preceding hour by default), and returns initial workflow status.

hub__workflow takes hub_plan_id and action status/report/finish. report accepts decision cache_hit/cache_miss/cache_insufficient/findings/access_blocked, receipt refs and needs_source for findings. finish accepts outcome supported/insufficient/blocked. Semantic decisions are client-reported. Invalid input is a Policy error and changes no state.

Child results retain their shape and gain mcp-hub/workflow metadata: plan ID, receipt ID, stage and allowed tools. Batch metadata maps indexes to receipts. Existing full-result retrieval stays available; expired references never trigger implicit replay.
