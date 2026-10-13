# P5 formal generation and frozen routing evidence

## Scope / status

**SOURCE_READ / IMPLEMENTED / OFFLINE_PASS**. This phase implements actual Python generation consuming P3 `FinalizedContext`, original completion/system/history/memory and source protocol prompts, then four-mode frozen P5 model selection and at most two uniform provider streams. It publishes only the selected buffered candidate and calls the P6 atomic final-commit callback before `complete`. This is an implementation handoff, not milestone acceptance.

**ISOLATED_INTEGRATION_PASS / REAL_PROVIDER_PASS / CUTOVER_PASS: NOT RUN** by this worker. No actual model, DB, Redis, old service, user credential, grant, UNKNOWN row, deployment, push or tag was accessed/changed. Synthetic budget gates in tests are not real billing receipts. Semantic answer quality is **NOT_REVIEWED**; mocked responses are regression fixtures, not human-approved factual gold. No savings/cost/latency claim is inferred from them. 未实现的命令不得报告通过。

Owned implementation:

- `backend/weknora_port/generation/{features,router,types,context,coordinator,__init__}.py`.
- `tests/weknora_port/test_generation.py`, `test_generation_wire.py`, `test_generation_protocol.py`.
- Authorized P1 ABI append: `models/provider.py` tracks actual streaming response model separately from requested configuration; targeted `test_provider_stream.py` retains reasoning metadata assertions and tests the new identity fields.
- Authorized P6 fix: **only `SourceRegistry.protocol_prompt`** in `references/registry.py` was changed by this worker, with exact fixed-source literal strings and targeted SHA fixtures. The P6 owner separately owns its snapshots/clones/resources/decoder additions.
- This report. P3/provider transport/storage/fusion/conversation/React work by other workers was preserved; no worker commit.

## Source/function mapping

| New mechanism | Exact authority |
|---|---|
| `features.py` pure T/C/J/D/L/M extraction | Existing frozen `backend/app/application/router_feature_extraction.py` at Git55372f7; copied bytes, no legacy runtime import |
| `router.decide` / max-per-feature combine / public metrics | Existing frozen `rule_router_v1.py` decide at Git55372f7; version `rule-router-v1/draft-v0.4`, threshold0.35 |
| `context.prepare_generation`, system/options/history | Fixed WeKnora `chat_pipeline/common.go` prepareMessagesWithHistory/prepareChatModel/AppendHistoryMessages; config default_kb YAML; exact SourceDataBoundaryPrompt and SourcedAnswerOutputPrompt |
| source-centric model retrieval output / IDs | `chat_pipeline/references.go` prepareMessagesWithModelContext/orderedPipelineReferences; `modelcontext/model_output.go` modelKnowledgeOutput/modelWebSearchOutput/renderKnowledgeChunks |
| resource/citation encoding boundary | `modelcontext/registry.go` ModelToolResult/EncodeMessages; sources.go CompactKnownText and historical citation compaction; P6 registry/decoder |
| `is_length`, final empty-truncated text, clean EOF / duplicate done | `chat_completion_stream.go` IsLengthFinishReason/EmptyTruncatedAnswerFallback/answerCompleted/answerProduced |
| uniform provider stream + typed timeout/identity/usage | New P1 `ProviderModel.chat_stream` through the single async HTTP/SSE transport, with the existing closed send gate |
| publish / commit-before-complete | Source stream handler event shapes; new P6 `finalize_turn` + root-injected `FinalCommitter` |

Frozen feature Git blob is `2f7df37144ba002102c2d92c1a0a2710bde48f46`; new file SHA256 is `b9cbf8421c64c4bc9b822631ee43d4b1f0f8ef6150e701ed8f91693d7f3fc12a`. No feature regex, contribution, threshold, condition grammar, AST dependency grammar, date/version masking, output-count grammar or UNKNOWN status was changed. This is bounded frozen model-selection grammar, not a semantic answer validator.

## Request and lifecycle ABI

`GenerationRequest` is frozen: run_id/session_id/message_id/q0, finalized P3 context, tuple evidence/history snapshots, memory prompt, language/system prompt, common vision/citation/FAQ settings, one `ChatOptions`, frozen `RouterPolicy`, optional `ValidatedRewrite`. `Evidence.from_result` captures source IDs/body/metadata and the source-enriched model passage. IDs must match the finalized result tuple; current KB/document scope is checked before calls. Source input and prompts stay out of public metrics.

`RoleBinding(role,provider,model_requested,endpoint,model)` binds configured `ProviderModel` identity. Cheap, Expensive and baseline provider/model/base_url are configured role bindings; frozen Xing/DeepSeek names are fixture defaults only. OFF uses the configured baseline model and bypasses the router. Requested/configured/reported identity remains distinct. Endpoint identity is checked; the P1 gate still validates actual send URL. Reported model absent is None/UNKNOWN, never copied from requested. Public metadata emits sanitized endpoint origin plus endpoint SHA; the exact private binding remains available to lifecycle persistence.

`AttemptLifecycle` has `claim_run(run_id,payload_sha256,context)`, `begin_attempt(record,context)`, `finish_attempt(record,context)`. Default implementation is closed. Root's scoped durable adapter must reject replay/mismatch before a new generation attempt; an existing run is attached for recovery. Request payload hashes bind session/message/scope/mode/options/q0/prompt/context. Attempt IDs are stable `run_id/answer/ordinal/role`, with ordinal1/2 only. Each attempt has a distinct immutable envelope identity; prompt/context/options are shared.

**Budget authority remains only P1 `SendGate.authorize/finish` at actual HTTP send.** The coordinator/lifecycle does not reserve, release, settle or invent receipts. Missing/partial usage retains UNKNOWN. Attempt `cost_status` is UNKNOWN without a real receipt; reported token counters are not billing proof. Actual model/endpoint/usage metadata is exposed without raw exception messages, headers, prompt, question or discarded draft.

## Frozen routing and approved strategy

- OFF: original configured baseline, no router/extractor/estimator invocation.
- CHEAP_ONLY / EXPENSIVE_ONLY: fixed single attempt, no escalation.
- DYNAMIC: score >=0.35 chooses Expensive; otherwise Cheap. Only q0 or already validated rewrite is classified. When provided, each feature uses max(q0,rewrite), never sum. Evidence content/retrieval scores do not affect T/C/J/D/M.
- Default complete-prompt token estimate is None, estimator UNKNOWN, L status UNKNOWN with zero **contribution**; no fake actual input count0. An injected estimator must supply its reviewed tokenizer/source identity and positive estimate as the frozen contract requires. No real generation tokenizer was installed or fabricated here; tests use explicitly named synthetic estimators.
- A completed Cheap candidate's objective `length/max_tokens/max_output_tokens`, empty answer, or invalid current-source handle signal causes one Expensive attempt. No lexical/semantic contradiction/relevance/refusal validator or LLM judge is introduced.
- Latest owner override: actual typed `ModelCallTimeout` (or trusted P1 error event with both matching class/code) causes one Expensive attempt with **no Cheap retry**. HTTP5xx, API/JSON parser error, identity mismatch, permission/egress/budget denial, user/global cancellation, client disconnection, generic TimeoutError and settlement/storage failure do not upgrade. An API error whose text says MODEL_CALL_TIMEOUT is not promoted.
- A nonempty stop refusal has no objective signal and naturally stays single-attempt. There is no anchored-refusal classifier. Explicit objective signals are not suppressed by semantic guesses.
- At most Cheap1+Expensive1; directly selected Expensive never upgrades. The exact same prepared messages/options/context are used, with no extra retrieval/query-model call and no Cheap text appended to Expensive input.

Both candidates are buffered until selection, as owner-authorized necessary business deviation. Current base registry is cloned per attempt, yielding identical stable c/d/b/w/res mappings but isolated used/invalid observations; answer and reasoning decoders are separate. Discarded Cheap content/references/invalid counters never enter Expensive or final stream. Selected thinking/answer/reference events use P6 DTOs. Final callback atomically persists answer/references before complete; callback failure or observed cancellation never emits complete. The injected callback owns the DB transaction and cancellation/ownership race semantics; buffering is not a distributed transaction.

## Exact original completion / codec behavior

Original `default_kb` system YAML, source-data boundary, sourced-answer output rules, request-local citation/resource protocol and MCP routing line are embedded/reused byte-for-byte. MCP prompt text does not add a tool runtime. System override precedes default; memory is appended after placeholder rendering; prior user/assistant rounds stay chronological; current user content is P3-finalized content. Shared image capability is chosen before P5 and must be compatible with any selected role. Role switching does not rebuild context/image evidence.

FAQ-first ordering follows source. Positional P3 contexts are replaced only in source system/current-user locations by model-facing `<retrieval><document><chunk>` current evidence. Known-ID compaction is applied at `ModelToolResult` output boundary, not globally to ordinary user/system/history prose. Resource encoding and historical public-citation compaction retain fixed source sequencing; historic handles are navigation only, not current evidence. Resource encoding of structured Data occurs after source XML rendering in this facade, while Output/Error are protected before rendering. The regression preserves that source behavior, including legacy physical-URL/XML entity ordering; it does not silently invent a new renderer.

The exact `SourceRegistry.protocol_prompt` enabled/disabled byte hashes are pinned in `test_generation_protocol.py`, including the full upstream MCP routing text. No shortened/paraphrased protocol is used by formal generation.

Source `IsLengthFinishReason` recognizes three aliases case-insensitively. Length is recorded before empty handling; final empty length uses the exact upstream fallback string. Source non-length empty completion is **not** turned into an invented final error/fallback: `chat_completion_stream.go` emits empty Done, and `agent_stream_handler.go` only accumulates nonempty content but still appends Done. Empty Cheap is an approved quality-upgrade signal; if final attempt is still empty, source empty completion is retained. Invalid final handles are stripped by P6, counts recorded, with no whole-answer semantic rejection. Clean EOF with no finish delta is source normal termination. Duplicate provider Done deltas do not duplicate final text. No ordinary output-tier classifier,180-character/100-word ceiling,3-suggestion clamp, old512 cap, old90/120 HTTP fork or old regex Validator is imported.

## Offline evidence

All checks use existing `E:\RAG quention\.venv\Scripts\python.exe`, from the isolated worktree with `--noconftest`.

First generation-only suite: exit0,62PASS. Includes all18 preexisting `p5_legacy_decisions.json` snapshots without changing that fixture, frozen input-load boundaries and max-feature combination, four modes, objective and typed-timeout upgrades, excluded error/cancellation cases, identical prompts, selected-only publication, resource/citation split handling, replay denial, final persistence ordering and UNKNOWN metadata.

Full wire fixtures use the actual P1 `ProviderModel` + single shared `AsyncHTTPTransport` + `httpx.MockTransport`, no socket. SSE is fragmented in3-byte chunks. Actual httpx.ReadTimeout mapping produces ModelCallTimeout and exactly two guarded sends, with different attempt IDs, identical messages, one wire max-token field and1024 source default. Missing reported identity/usage remain None/UNKNOWN; HTTP500, malformed JSON, wrong model and lexical timeout text do not upgrade. Settlement failure after finish never publishes/upgrades.

Initial associated suite: exit1,1FAIL/148PASS. Existing reasoning metadata exact-data assertion did not include authorized model_requested/model_reported ABI append; updated that assertion to retain its complete reasoning-metadata check and assert requested m / reported None. Next suite: exit1,1FAIL/150PASS; the endpoint-redaction fixture searched for the generic substring token and matched normal token counter keys. Changed the synthetic secret sentinel to an unambiguous value; the implementation was not loosened. Subsequent checks passed; both failures are preserved here rather than called PASS.

Final command:

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest -q tests/weknora_port/test_generation.py tests/weknora_port/test_generation_wire.py tests/weknora_port/test_generation_protocol.py tests/weknora_port/test_provider_request.py tests/weknora_port/test_provider_transport.py tests/weknora_port/test_provider_stream.py tests/weknora_port/test_provider_usage.py tests/weknora_port/test_streaming.py tests/weknora_port/test_references.py --tb=short -p no:cacheprovider
```

Latest rerun after configurable-role correction: exit0, **166 PASS in 1.65s**. Previous exact-protocol checkpoint: 161 PASS in 1.12s. All offline protocol/mechanism fixtures. No current Go/provider oracle was executed by this P5 worker; exact fixed-source reading, frozen Python snapshots and source-derived prompt hashes are identified separately from runtime Go proof.

`git diff --check -- backend/weknora_port/models/provider.py tests/weknora_port/test_provider_stream.py backend/weknora_port/references/registry.py`: exit0. Generation package compileall exit0. AST parse9 generation/test Python files exit0. Langfuse local skill evidence guidance was read for report hygiene; no Langfuse service, dataset, telemetry or SDK was added and no quality-gold claim made.

The configurable-role regression covers DYNAMIC/CHEAP_ONLY/EXPENSIVE_ONLY/OFF with alternate provider/model/base_url values through the same chat_stream interface, and rejects a binding whose requested identity differs from actual provider configuration. The frozen Xing/DeepSeek snapshots remain unchanged.

## Remaining integration and version

Root owns durable lifecycle, actual monthly-cost/send gate, scoped atomic final committer, HTTP API composition, recovery wiring and isolated DB/Redis/live acceptance. Generation defaults remain closed without the lifecycle and P1 send gate. An absolute run deadline must have `deadline_kind=run`; per-model P1 timeout remains separately typed. A provided absolute model deadline is preserved rather than silently reset for Expensive; an already exhausted deadline can block its admission. The original uniform provider stream timeout stays600s, independent of role.

No worker commit/tag; parent coordinates shared-stage review/commit. Root committed the exact protocol function in `6858e14`; the generation files, provider identity metadata and targeted tests remain pending the root P5 commit. Code is ready for root integration review with the interfaces above. Production readiness, semantic answer quality, hosted model identity equivalence, actual costs and cutover are not declared PASS by this report.

## Fixed source blobs

- `internal/application/service/chat_pipeline/common.go`: `61a189af38cf710ab82e3fc81388fce86d04d4f4`.
- `internal/application/service/chat_pipeline/chat_completion_stream.go`: `0f19dfafa81702c5b5d76d217f4942d750496c34`.
- `internal/application/service/chat_pipeline/references.go`: `234149f2d1d60943f367c7bc4f83a2884ae6511c`.
- `internal/handler/session/agent_stream_handler.go`: `012fe09a9a44b1070c2deda18543ac0a44c77a11`.
- `internal/modelcontext/registry.go`: `6f6126040059e8fc68024f67cc3b31ef9815c349`.
- `internal/modelcontext/sources.go`: `1f907b870cfb8288475685eec5660bb82703de71`.
- `internal/modelcontext/model_output.go`: `84e369d5a75e628c206543f227307ba7cf4941c0`.
- `internal/modelcontext/resources.go`: `1587d18dfd31a0d75f5e4692a61f97ce23ea507e`.
- `internal/modelcontext/citations.go`: `98e690981f20140bf0d5f0182bd391c769022dd7`.
- `internal/types/prompt_instructions.go`: `b526117decbbfd4349022a17e22317f122ab8cac`.
- `config/config.yaml`: `06811ba23dcb25042fcfdf7e4720d2eba21fcdb3`.
- `config/prompt_templates/system_prompt.yaml`: `a58739fc8c432da80a2e0d9fe6d67c1b63522917`.
