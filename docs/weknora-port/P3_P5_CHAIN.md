# P3 / P4 / P5 ordinary chain

The service now composes the existing Python history/memory, retrieval, generation and replay mechanisms. P5 core is root commit `06d54c8`; configured roles are root commit `18a0309`. This worker made no commit. Only `service.py`, the existing-stage observation hooks in `retrieval/pipeline.py`, `test_service.py` and this report belong to this phase.

## Runtime and durable boundaries

`RuntimeService.accept_turn(request, trusted_principal)` calls injected `TurnRepository.accept` to authorize the effective server owner/KB/document scope and atomically persist the stable request, user and assistant identities before any retrieval/model work. Equal repeated payloads return replay-only turns, including after restart; conflicting payloads must fail. This protects the earlier P3 model calls, independently of the later P5 generation claim. The service alone publishes initial `agent_query`, starts and retains the new background task, and returns stable identities. HTTP only reads/replays; disconnect does not cancel the background run.

`run_id` equals request `request_id`. Accepted server `CallContext` and P4 `MemoryContext` share an owner; request JSON cannot supply trusted context, tenant, owner or credentials. Replay-only turns do not enter P3/P5. `run_turn` passes existing P4 history into immutable generation `History` objects and query understanding, uses the existing P3 `RetrievalPipeline.run`, snapshots current evidence, and passes the immutable `FinalizedContext` to the existing P5 coordinator. No retrieval, scoring, validator, role routing, output tiers or citation algorithms are copied into service.

`OrdinaryMemoryLoader` uses existing P4 `recall` and `retrieval_context` reads. Its exact source `<asker_background>` wrapper gives advisory profile/interests/documents to query understanding without narrowing trusted scope. Relevant recall prompt goes separately to the original P5 system assembly. Standing background does not become used-memory citations. The original query-understanding intent and q0 fallback remain the source path; q0 plus its existing rewrite feed the frozen max-feature rule without another model call.

Source mapping: pinned `3e8b0bfc80b845b2d4b2ed683994748741450a97`, `chat_pipeline/load_history.go:PluginLoadHistory.OnEvent`, `memory_recall.go:PluginMemoryRecall.OnEvent`, `query_understand.go:memoryBackground`, existing P3 stage order/P5 completion assembly and P6 ContinueStream replay. No direct Go chain execution is claimed.

## Injection ABI

Exports from `backend.weknora_port.service`:

- `OrdinaryTurnRequest(session_id, content, request_id, mode='quick', knowledge_base_scope=(), document_scope=())`.
- `AcceptedTurn(run_id, session_id, user_message_id, assistant_message_id, context, memory_context, request, is_new=False, replay_only=True)`.
- `TurnRepository.accept(request, principal)`, `validate_run(session_id, assistant_message_id, principal)`, `commit_failure(accepted, safe_code)`.
- `TurnConfiguration(pipeline_state, generation_options)` from server `configuration(accepted)`; generation identity/content fields cannot be overridden.
- `pipeline_factory(accepted, on_progress)` and `generation_factory(accepted, on_progress)`, sync or async, binding actual scoped repositories, registry, gates, lifecycle and final committer.
- `OrdinaryMemoryLoader(memory_service).load(memory_context, query)` or trusted loader returning `MemoryBundle(background, prompt)`.
- `RuntimeService.accept_turn`, `validate_run`, `replay`, `run_turn`, `publish_error`, `drain_background`.

HTTP imports the ordinary request and calls accept/replay. Service replay directly reuses P6 `ReplayService` with a trusted identity adapter. Initial cached unfinished adjacent deltas coalesce through that existing implementation before same-run polling. Closing replay changes no run token.

P3 adds only optional `on_progress`, around actual understand/search/rerank/merge/context stages, with counts from actual result lists/finalized IDs. Ordinary observer failures are nonfatal; cancellation and run deadline propagate. Service publishes history/memory stages and safe counts/role/attempt metadata, without prompt/error text.

On chain failure, `commit_failure` must atomically persist assistant failure before error and terminal complete events. User messages are not error targets. Failure persistence rejection cannot manufacture completion. commit_failure returns True only after actual failure persistence, or False when an already successful atomic final commit must be preserved. False prevents contradictory failure events after SSE-store errors; missing receipt fails closed with TURN_FAILURE_COMMIT_UNVERIFIED. Existing successful final commit cannot be overwritten by the root callback. Sent attempts and UNKNOWN cost occupancy remain preserved; service adds no reserve/settle or retry. P1 actual-send gate is the sole budget authority. `GenerationReplay` only attaches existing events. Lost-worker recovery must not blindly resend.

## Executed evidence

From the managed worktree:

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest -q tests/weknora_port/test_service.py tests/weknora_port/test_pipeline.py tests/weknora_port/test_generation.py tests/weknora_port/test_generation_wire.py tests/weknora_port/test_generation_protocol.py tests/weknora_port/test_streaming.py tests/weknora_port/test_http_api.py --tb=short -p no:cacheprovider
```

Actual latest result: exit **0**, **187 passed in 1.81s**. Earlier targeted service run: exit0,17 passed; source replay regression adds one case; final-commit/failure-receipt regressions add two (20 service cases total). The suite executes real Python P3/P5/P6 mechanisms against explicit fake repositories/history/memory and synthetic providers; P5 wire cases use HTTPX MockTransport. It checks shared finalized evidence, Cheap buffering/Expensive selection, commit ordering, one initial event, repeated/restart replay without model sends, disconnect isolation, cached delta coalescing, assistant failure ownership, private error redaction, UNKNOWN sent failure, failure callback refusal, scope/payload denial and progress cancellation.

This is offline mechanism **PASS**. PostgreSQL/Redis binding, public models, hosted quality/cost and direct Go chain oracle: **NOT RUN by this worker**. Root owns actual isolated database evidence. No dependency installation in this phase. 未实现的命令不得报告通过。

A report-generation Python command failed with SyntaxError before writing the report (exit1 for Python; a subsequent pytest in the same shell returned0). The report was then saved through apply_patch. This failure is not a test failure or a claimed passing command.

## Handoff

Root must bind durable admission/failure repository, configured model registry, actual P4 memory/history, P3 scoped knowledge, P1 send gate/cost and P5 atomic final committer. HTTP must retain the single service initial-event publisher. Production readiness, cutover, real provider equivalence and milestone/tag acceptance are not claimed.

Safe independent commit paths: `backend/weknora_port/service.py`, `backend/weknora_port/retrieval/pipeline.py`, `tests/weknora_port/test_service.py`, this report. Exclude other workers' HTTP/documents/send-gate/configuration/STATUS changes. No old QuickChain import or patch.
