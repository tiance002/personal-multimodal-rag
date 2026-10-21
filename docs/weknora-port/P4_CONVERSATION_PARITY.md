# P4 ordinary history and personal memory: implementation evidence

Status: **ordinary history, memory read/write and maintenance core implemented; offline fixtures PASS; full P4 integration NOT COMPLETE**. Python is the product implementation. Go is used only to compare pure functions against the fixed reference without network, modules, database or providers. This report does not establish runtime equivalence or PostgreSQL atomicity. 未实现的命令不得报告通过。

## Identity and scope

- Worktree: `C:\Users\22088\.codex\worktrees\weknora-python-port\RAG quention`, branch `codex/weknora-python-port`; HEAD at final verification `2d8ac73279d25e6581e73b5df20b6a9f2cc5458c`. This worker did not commit.
- Readonly upstream: `E:\RAG-reference\WeKnora-3e8b0bfc`, fixed commit `3e8b0bfc80b845b2d4b2ed683994748741450a97`. Exact line/source inventory is in [P4/P6 source notes](source/P4_P6_SOURCE.md).
- Changes: `backend/weknora_port/conversation/{types,history,unicode15,ranking,memory,management,maintenance_types,extraction,maintenance,topics,turns,prompts,__init__}.py`, `conversation/NOTICE`, `tests/weknora_port/test_conversation.py`, `test_conversation_maintenance.py`, `test_conversation_oracle.py`, and this report. Other workers' models, storage, retrieval, ingestion and frontend edits were preserved.
- This module receives trusted server tenant and principal storage identity, not client-selected owner/KB. Existing KB/document scope remains separately enforced by the retrieval layer. No old KB-scoped/GLOBAL memory import occurs.

## Implemented source behavior

| Python surface | Fixed upstream source | Implemented branch |
|---|---|---|
| `OrdinaryHistoryService.load`, `process_history` | `chat_pipeline/load_history.go:35–64`, `common.go:137–185` | Disabled max rounds never fetches; positive limit fetches `2*rounds+10`. Group by request ID, raw user Content plus saved captions/attachments, strip complete assistant think spans, retain references, require complete nonempty pairs, newest user time selection then chronological replay. Optional load failure degrades to no history. |
| `attachment_prompt` | `internal/types/message.go:154–195` | Source metadata, HTML escaping, untrusted attachment envelope, closing-tag escape, truncation/missing-content notes. Does not replay RenderedContent as user intent. |
| `MemoryContext`, `enabled_scope`, settings | `memory/scope.go:28–41`, `service.go:85–107,887–924`, `types/memory.go:449–481` | Tenant+principal owner scope, workspace enabled/user opt-out/agent gate, missing config disabled, absent subject allows first write. Settings effective view stays workspace && user; actual reads also apply agent gate. |
| Recall/search/retrieval context | `memory/service.go:121–258,940–1056`, `search.go:68–139` | Active nonexpired rows only; resident60, profile/preference/interest and explicit rows; relevant interests plus standing defaults; 900-rune block and source cached-block fallback; situational fact/task max5/600; search default10/max20/2000; capacity200/default capped2000; retrieval profile/interest30 within240 and affinity title hit minimum2. |
| Lexical/vector selection | `memory/lexical.go:21–218`, `vector.go:60–89,208–255`, `recall_trace.go:251–300` | Go Unicode15 Han and alphanumeric tokenization, adjacent Han bigrams, overlap weights1/2, importance0.01, floor0.15, recency ties; equal RRF `1/(60+zeroBasedRank)`. Oversized candidates are skipped. Optional workspace embedding has 2s timeout, fixed model space, cosine minimum0.5, vector can extend beyond lexical window. Errors use lexical/no memory without fabricated usage or vectors. |
| Management and lifecycle contracts | `memory/service.go:317–486,794–883,1310–1334`, `repository/memory_lifecycle.go:15–123` | Sanitization300 runes, fixed sensitive redaction, topic keys/fingerprints, containment dedup200, immutable replacement, manual/explicit active vs inferred pending, capacity/archive, confirmation/rejection, edits, scoped deletion/clear, exact forgotten fingerprint suppression and extracted source-message suppression1h. Pending proposal preserves active target until confirmation. Atomic adapter returns actual saved row for idempotent identity. |
| Integration hooks | `chat_pipeline/memory_recall.go:14–26,55–82`, `memory/service.go` | Injected `on_recalled(scope, used_rows)` reports actual relevant/fitting memories; failures do not block QA. Touch is asynchronous best effort with drain hook. `after_write(scope,config,row)` supplies real embedding/storage integration; no fake write vectors are generated. |
| `ExtractionWorker.schedule/handle/collect/apply` | `memory/extract.go:90–175,259–553,855–1162`; `repository/memory_extraction.go:81–224` | Durable per-session revision queue, debounce/min-interval delay instead of dropped turns, lease ownership/retry/release, timestamp+ID cursor, user-only segments and checkpoint of assistant rows. Explicit source target wins over changed topic, inferred rows await confirmation, delete decision supersedes rather than user-forgets. |
| `MemoryMaintenance.review/backfill` | `memory/consolidate.go:112–309,390–540`; `vector.go:95–198,262–297` | Whole active store up to capacity; expiry first, stale task demotion, primary-based same-kind local candidate groups; model alone decides merge. Separate manual/daily clocks; bounded stored-vector reuse, source text+topic+interest aliases for embedding, pinned-space backfill and vector-column sync. |
| `TopicService.resolve/observe/promote/delete` | `memory/topic_resolve.go:69–355`, `service.go:538–611,673–735,1101–1299` | Exact normalized identity/aliases, specificity-gated Dice bigram match, one batched model adjudication for unresolved labels, protected target indices and anchored improved label. Count, alias retention/invalidation, recurrence promotion, manual promotion and tombstones for canonical label plus aliases. |
| `record_turn_memory` | `handler/session/qa.go:1919–1945`, `types/memory.go:1144–1179`, `memory/service.go:1059–1081` | Post-persistence hook stores literal explicit remember importance4 with user provenance, records verified document affinity, then schedules auto extraction. Explicit-only mode invokes no extraction model. |

The module does not change document score threshold **0.35**, selection weights, evidence/citation semantics or answer retry policy. Latest owner P5 timeout policy (Cheap -> one Expensive, no Cheap retry) belongs to P5; this module adds no answer-model strategy. Smart Agent entry is retained in the architecture; ordinary round replay does not implement or substitute for its token-budget compaction.

Maintenance preserves source limits: extract page40 plus lookahead1; run3 segments; segment8 applied decisions; one-hour user gap; user line1000 runes; prior context4 from16 earlier rows; existing candidates15 (semantic whole-store when prefix16 proves overflow), tombstones30 and tracked topics40 with12 shown. Completion options retain exact source system prompts and JSON schemas, temperature0/thinkingfalse; extraction1200 tokens retries **only empty/truncated output** once at4000. General provider/permission/budget errors preserve cursor and do not trigger another call. This background-source behavior is separate from the P5 answer upgrade policy. Missing model is an error with cursor retained; resolution order is configured extraction model, task conversation model, first usable workspace knowledge-QA model supplied by the injected adapter.

Durable extraction adapter must coalesce rather than replace sessions, increment revision on enqueue, sort claim batch by updated_at then session ID with cap32, preserve pending if arrivals changed the captured revision, fence all checkpoint/finish/failure updates by lease ownership, and bootstrap fallback payload only once. Lease600s, worker bound540s, cleanup5s; busy redelivery schedules at lease expiry+1s, follow-up15s. Invalid output records start/end cursor and safe code; **only the third recorded failure** for that start cursor permits evidenced skip. Models are injected; no private content appears in exception text or job identity. Decision and topic operation IDs deduplicate successful side effects on partial replay. SQL must persist operation identity atomically with the write/bump, never before a failed operation.

Consolidation retains daily24h/manual1min independent clocks, automatic minimum6 items, candidate Jaccard0.55/cosine0.86 (forced0.30/0.75), model groups3 (forced8), stale tasks45days, completion600 tokens, embedding backfill200 with10s timeout and vector sync2000. No overlap-only merge is substituted. Topic fuzzy Dice threshold0.80 requires normalized length>=4, alias cap12, default recurrence3 capped20, model budget800. Rename preserves manually edited interest prose and invalidates stale vectors. Post-turn cited affinity must receive server-verified sources; source permissions are enforced by root integration, not inferred from a model label.

## Storage ABI and privacy invariant

All repository reads must filter tenant+subject and applicable lifecycle/kind/expiry. Vector queries additionally filter the configured embedding model and actual vector dimension. Trusted scope must be checked for each mutation and returned row. There is no in-memory production repository fallback.

`MemorySubject` columns are `tenant_id`, `subject_id`, `enabled`, `block_text`, `item_count`, plus internal `cache_revision` (default0). `MemoryItem` includes source fields, `normalized_key`, `status`, `replaces_id` and `superseded_by`. No UI setting exposes cache revisions. `MemoryTombstone(topic,fingerprint,source_message_id)` stores suppression metadata without forgotten raw content.

The adapter contracts are:

```python
save_item(scope: MemoryScope, item: MemoryItem, replaces_id: str) -> MemoryItem
update_item_content(scope, item_id, content, key, importance) -> None
confirm_pending_item(scope, item_id) -> None
archive_lowest_ranked(scope, keep) -> None
delete_item(scope, item_id, tombstone: MemoryTombstone) -> None
delete_all(scope, tombstones: tuple[MemoryTombstone, ...]) -> int
update_subject_block(scope, block, item_count, expected_revision: int) -> bool
```

Additional maintenance protocols are declared in `maintenance_types.py` (`MaintenanceRepository`, `MaintenanceModel`, `ExtractionEnqueuer`, typed payload/session/cursor/batch/request/response/topic/result). Content lifecycle callbacks `supersede_item`, `expire_overdue`, topic/interest changes and deletion extend the same cache invalidation/revision contract. Embedding upsert takes the full item snapshot: adapter must compare its source content/topic and live status before saving, so a vector that arrives after edit/delete cannot revive old data. The `operation_id` on MemoryItem is internal idempotency metadata, not a client-authored owner or model identity. Queue adapters provide durable transaction/lease behavior; the fixture is explicitly not a production fallback.

Every content/lifecycle mutation must hold the same scoped subject lock and **clear block_text + increment cache_revision in the same transaction** as item mutation/vector cleanup. Delete callbacks must also persist supplied suppression metadata in that transaction; failure rolls back instead of claiming successful forgetting. Bulk suppression preserves upstream status priority and 500-item cap. Topic/affinity cleanup remains explicit scoped work after the atomic item deletion.

Rebuild captures the scoped subject revision **before** fetching active rows, rendering and counting. `update_subject_block` must compare-and-set against that revision atomically under the same subject lock. False means a newer mutation won; the caller cannot overwrite the cache. A rebuild failure cannot undo invalidation. This minimal privacy/concurrency wrapper is an explicit approved exception to upstream best-effort separate tombstone/cache writes, not a new recall or consolidation algorithm. Upstream cached-block fallback itself is preserved.

Offline fault fixtures demonstrate: delete/clear/edit/replacement followed by failed resident fetch/rebuild cannot return old private cached text; tombstone failure aborts deletion; an old rebuild paused after rendering, followed by successful deletion and failed fresh rebuild, cannot restore text when its stale CAS resumes. **These fixtures validate the protocol and stand-in only. PostgreSQL locking/rollback/CAS/vector cleanup are NOT RUN here and remain root storage integration work.** Privacy failure is not approved for deferral.

## Executed checks

Working directory for every command below is the isolated worktree above. All data and embedding vectors are synthetic; no provider/database/service calls occurred.

| Exact command | Exit | Evidence |
|---|---:|---|
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -c 'from backend.weknora_port.conversation import MemoryService,MemoryManager; print("CONVERSATION_IMPORT_PASS")'` | 0 | `CONVERSATION_IMPORT_PASS` (initial import smoke) |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest tests/weknora_port/test_conversation.py tests/weknora_port/test_conversation_oracle.py -q --tb=short` | 0 | Initial core run: **34 passed in 14.10s**. Includes exact-function Go comparison, ordinary replay, gates/scope/cancellation, lexical/vector degradation and bounds, pending lifecycle, forgetting and stale rebuild CAS. |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest tests/weknora_port/test_conversation.py tests/weknora_port/test_conversation_maintenance.py tests/weknora_port/test_conversation_oracle.py -q --tb=short` | 0 | Final maintenance run: **48 passed in 14.54s**. Adds schedule coalescing and delays, user-only segments, timestamp/ID and assistant checkpoint, truncation-only retry, source provenance/confirmation, partial replay, invalid-output evidence, error/cancel cursor preservation, lease/revision/follow-up, missing-model error, whole-store semantic candidates, consolidation model judgement/clocks/expiry/demotion, pinned backfill/late-vector rejection, topic resolver/index/label guards/promotion/replay/alias forgetting, post-turn hooks and exact copied prompts/schema. |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -m compileall -q backend/weknora_port/conversation` | 0 | No diagnostics. |

`test_conversation_oracle.py` extracts unchanged pure functions from the fixed Go source into a temporary standalone stdlib program and compares synthetic Unicode/Han tokens, scores/ranks/RRF, sanitized/redacted content, fingerprints, rendered prompts and attachments. It uses the readonly reference Go binary with network/module lookup disabled. It skips when the optional reference/toolchain is absent on another machine; the recorded final run executed and passed, not skipped. Go15 Unicode tables carry upstream BSD attribution in NOTICE alongside WeKnora MIT attribution. Go is not a dependency of the Python application.

## Pending integration and unsupported surfaces

**IMPLEMENTED as Python orchestration with typed storage/provider protocols:** automatic extraction scheduling, durable queue/lease/cursor/checkpoint coordination, output parsing and decision application, consolidation/candidates/review/backfill/sync, topic resolution/count/aliases/promotion/deletion, and post-turn explicit/source/scheduling hooks. Fixed source prompts and schemas are copied verbatim and checked against the actual source in offline fixtures. No new summarizer or retrieval strategy is substituted.

**NOT IMPLEMENTED by this module:** full Smart Agent token-budget compaction, still explicitly exposed in `PENDING_FEATURES` and retained as a separate architectural entry. This report does not approve deferral of required memory integration; actual adapter execution is the remaining step.

**NOT RUN:** actual PostgreSQL queue/lease/revision/idempotency/atomicity, trusted auth context wiring, real embedding/provider identity/egress/budget, live post-turn scheduling/cited-document recording, live QA pipeline and frontend/P6 replay. Root owns storage and integration. Real extraction/consolidation require authorized provider/storage adapters before execution. Historical reconstructed UI stages remain presentation-only and must not be described as recorded events or proof of calls/cost.

No acceptance/tag/commit is claimed. Next smallest integration task is wire the scoped repository and CAS callbacks, then run PostgreSQL fault/interleaving and owner-isolation checks before declaring P4 complete.
