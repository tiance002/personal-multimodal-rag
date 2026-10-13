# WeKnora Python + P5 Goal status

Full Goal active. Fixed upstream3e8b0bfc, clean starting main55372f7, isolated branch `codex/weknora-python-port`.

| Phase | Actual status | Evidence / next work |
|---|---|---|
| P0 | Source/worktree baseline captured; live identity BLOCKED |55verified blobs; initial behavior/legacyP5 snapshots; existing contractFAIL31PREEXISTING; no writes/calls. Local baseline commit a6ddb16. |
| P1 | IMPLEMENTED / OFFLINE_PASS for direct native and protocol subset; full proxy parity pending |146checks (76fake-network native,65protocol,5executedGoSSEcases). Default send gate denied. Configured proxy fail-closed; fullGoRequestoracle WindowsCGObuildFAIL. No live call. |
| P2 | IMPLEMENTATION_IN_PROGRESS, full parity pending | 8 chunk oracle fixtures; parser mechanisms still being aligned; PostgreSQL query adapter offline only, schema/index integration pending. |
| P3 | IMPLEMENTATION_IN_PROGRESS, full parity pending | Effective config and fusion implemented with offline checks; remaining query/rerank/merge/context stages pending. |
| P4 | SOURCE_READ; IMPLEMENTATION_IN_PROGRESS | Exact history/memory/source-handle source notes; ordinary history and memory worker active; storage wiring pending. |
| P5 | Frozen legacy snapshots captured; new-chain wiring NOT_IMPLEMENTED | Required selection and upgrade, including explicit model-timeout upgrade; not optional/experimental. |
| P6 | NOT_IMPLEMENTED | React/new SSE integration, onePOST/recovery/source opening/Smart boundary. |
| P7 | NOT_IMPLEMENTED | Six dataset representative end-to-end adapters and paired results with Gold boundaries. |
| P8 | AWAITING_OWNER_APPROVAL for production/real calls | Isolated shadow proof/rollback can be prepared; production and cleanup not executed. |

No current REAL_PROVIDER_PASS, CUTOVER_PASS or full parity claim. Go1.26.0 portable verified; actual SSEReader5case oracle PASS; full Provider oracle blocked by WindowsCGO. Docker unavailable does not block pure-code/mock development.

Owner instructions require autonomous progression through implementable phases; do not end the Goal after P0/P1 or redefine completion around them. Each phase records exact sources/functions, commands/exits, missing evidence and a local commit. No Push/Tag/Release.
