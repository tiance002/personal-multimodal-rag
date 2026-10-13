# Fixed WeKnora source mapping / G0

Local clean main baseline: b4aa3ec5ff82497fdf9f9f00cdacf75e3128edd3. Unique upstream reference: [Tencent/WeKnora@3e8b0bfc](https://github.com/Tencent/WeKnora/tree/3e8b0bfc80b845b2d4b2ed683994748741450a97). All requested files actually read; raw bytes and SHA256 manifest stored in ignored var/weknora-reference-20261012. Raw downloads had TLS failures; missing stream.go obtained via GitHub file connector at same fixed ref, blob SHA 734d618316f1cd71fd5fecb0a89cbf324ff5fdf3. No drifting main or signature-verification claim.

| Upstream direct fact / function | Existing code and migrated behavior | Do not copy | Acceptance |
|---|---|---|---|
| config/config.yaml:32 output1024; session_knowledge_qa.go SummaryConfig; chat_pipeline/common.go prepareChatModel; api/options.go CompletionBudget | Ordinary QuickSettings policy flows through prompt, window, envelope, capacity, single max_tokens and reservation. Length tiers1024/2048/4096 are a project extension. | Default1024 and context256K are not hosted output capability proof. | Wire counts/reservations agree; unknown caps denied. |
| api/openaicompletions/request.go body[field] and duplicate-field deletion; providers/siliconflow.go max_tokens | Separate ordinary counter paths retain fixed renderer and sole wire field. | Frozen V6 and vision/caption512 remain unchanged. | Frozen contracts plus ordinary1024 offline tests. |
| chat_pipeline/progress.go Begin/EndRetrievalProgress and ShouldCloseRetrievalProgress | Actual retrieval returns candidate count before final transaction; prepared evidence count is separate. | No new EventBus, fake rerank stage or premature frozen evidence. | Process precedes final facts; failures close pending. |
| chat_completion_stream.go length marker; session_knowledge_qa_truncation_test.go empty-truncation handling | Empty and nonempty length remain non-success, at most two attempts. | No unvalidated partial output, validator or final transaction changes. | Truncation and citation/unit regressions. |
| handler/session/stream.go ContinueStream scoped session/message replay and offset continuation; stream/redis_manager.go GetEvents LRange | PG authoritative state and seq/scoped GET retained; cache disposable. | No Redis-only completion authority or regenerated POST. | Four-plus normal segments, lostPOST, cancel/scope tests. |
| useChatStreamHandler.ts tool states; RagPipelineProgress.vue pending/model waiting; botmsg.vue embedded progress/truncated indicator | React assistant-side Run timeline and terminal collapse. | No Vue dependency, thought text, or progress as factual history. | Browser simulations and sameRun history replay. |

## Runtime boundary

Git status -sb and rev-parse HEAD exit0. docker ps --format '{{.Names}} {{.Image}} {{.ID}}' exit1: Docker Desktop Linux engine pipe missing. RUNTIME_IDENTITY_BLOCKED: current image digest, configuration revision, DB migration head and scoped PG attempts NOT RUN. This blocks deployment and real calls, not identified-source offline edits. Previous reports are historical only.

Original-question scope: KB d78f3434-0e8f-40bf-afcb-f1c2c311ff51; document1fd5a928-d3d7-4bae-aa00-fc74701f61e5. Historical successful Run d1eee158-93d3-4bac-b78a-5aab154d4221; historical failed Run56f2e64b-fdda-4387-81c2-57a854b54ff4. They are not new-budget/progress validation. Existing preview/name/diagnostic fixes are retained without repeating that task.

License: WeKnora main project MIT plus independently licensed third-party components. Only interfaces/behavior ideas adapted; no substantive upstream Go/Vue code copied into runtime, no vendor dependency.


## Current live checkpoint

Historical results above are retained. Current seven-probe/two-question outcomes, actual running identity, local unit-prompt repair and pending limits are in [LIVE_ACCEPTANCE_20261013.md](LIVE_ACCEPTANCE_20261013.md). Full Goal remains incomplete; G4 current samples FAIL/FAIL/NOT RUN and G5 NOT RUN.
