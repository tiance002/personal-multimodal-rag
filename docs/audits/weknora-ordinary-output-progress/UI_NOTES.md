# G2 frontend / G3 scoped stream recovery

Status: `OFFLINE_PASS`; real API, database, model calls and deployment `NOT RUN`.

## Scope and implementation

- `frontend/src/api/sse.ts`: default 240-second overall wait, configurable with a 210-second minimum; normal server EOF reconnects without spending the abnormal retry budget. Transport failures back off (250/500 ms), with three consecutive abnormal attempts maximum. Scoped startup 404 grace is limited to ten seconds, and history replay uses no startup grace. `Last-Event-ID` advances only for valid monotonically increasing safe integer sequences. Abort fences both fetch and reader completion, including simulated IO that ignores its signal.
- `frontend/src/api/client.ts`: a single generation POST races GET recovery for the generated Run ID and conversation. A successful POST must echo the exact Run identity and answer/citation shape. Typed 4xx rejection is shown promptly; uncertain transport/5xx responses retain GET recovery. GET terminal data is supplied by the existing backend scoped committed-event reader; recovered successful answers additionally match the exact Run in committed assistant history. Failed presentation comes only from the same user Run. Candidate tokens, another Run, late POST and duplicate terminal events cannot insert an answer.
- `frontend/src/app/state.ts`: bounded `run-progress/v1` fields and a per-Run sequence/state reducer. Actual stages are aggregated by stage/attempt/role, with distinct candidate/evidence counts. Terminal or stopped Runs close pending rows without inventing stage success. Unknown fields, including thoughts, are never retained.
- `frontend/src/app/App.tsx`, `frontend/src/components/ChatPanel.tsx`, new `RunProgress.tsx`, and `frontend/src/styles.css`: progress occupies the assistant-side left edge outside message content. Pending timelines expand by default; terminal timelines collapse and can be reopened. Model roles are labelled `轻量模型` / `高能力模型`, with `正在连接模型并生成回答` while generation is pending. History GET replays the same Run under the current conversation/server scope. A previously running user-only history reloads committed messages at terminal. Request epochs and abort controllers fence conversation/KB/document changes, cancellation and late history responses.
- The visible `停止等待` action stops client waiting and closes its timeline; it does not claim that the backend model has stopped. Existing backend cancellation semantics are unchanged. Progress never creates successful assistant/history content or changes `evidence.frozen` / final transactions.
- Focused mock fixtures now echo the POST `request_id`, matching the real Run identity contract. Assertions and failure cases remain intact. All tested API requests are fulfilled locally; no provider requests are made.

## Fixed-source mapping

Read the fixed local reference at `var/weknora-reference-20261012`, commit `3e8b0bfc80b845b2d4b2ed683994748741450a97`:

| Reference | Adaptation |
|---|---|
| `frontend/src/views/chat/components/RagPipelineProgress.vue` | Real stage rows, neutral waiting, collapsed completed timeline; implemented in React, without Vue or model thoughts. |
| `frontend/src/views/chat/components/botmsg.vue` | Progress placed at the assistant answer position and separated from answer rendering. |
| `frontend/src/composables/useChatStreamHandler.ts` | Exact message identity, duplicate/late completion fences, distinction between token completion and persisted terminal. |
| `internal/handler/session/stream.go` | Read/replay a known scoped identity and continue its cursor without regenerating. |
| `internal/stream/redis_manager.go` | Continue the event cursor; retained this project's PostgreSQL terminal authority and disposable Redis cache. |

This adapts behavior and interface ideas. No substantive upstream implementation code was copied. The stream source was initially absent and was read after the root agent completed the fixed reference fetch.

## Executed checks

Working directory for the commands below: `E:\RAG quention\frontend`.

| Command | Exit | Result / evidence |
|---|---:|---|
| `& .\node_modules\.bin\tsc.cmd --noEmit` | 0 | `PASS`, affected frontend TypeScript compiles. |
| `& .\node_modules\.bin\playwright.cmd test tests/sse.spec.ts tests/run-progress.spec.ts --reporter=list` (first attempt, bundled default browser) | 1 | Launch failed because the Playwright bundled Chromium binary was absent; 11 tests did not execute. No browser installation performed. |
| `$env:PLAYWRIGHT_CHROME_PATH = 'C:\Program Files\Google\Chrome\Application\chrome.exe'; & .\node_modules\.bin\playwright.cmd test tests/sse.spec.ts tests/run-progress.spec.ts tests/clarification-response.spec.ts tests/table-header-hint.spec.ts tests/scope-sync.spec.ts tests/stale-message-scope.spec.ts --reporter=list` | 0 | `OFFLINE_PASS`: 75 passed (1.6m), before the final role wording/cancellation refinement. |
| `$env:PLAYWRIGHT_CHROME_PATH = 'C:\Program Files\Google\Chrome\Application\chrome.exe'; & .\node_modules\.bin\playwright.cmd test tests/run-progress.spec.ts --grep 'live timeline\|switching conversation' --reporter=list; npm.cmd run build` | 0 | `OFFLINE_PASS`: 2 UI/history/scope cases passed, followed by a production build, before the final refinement. |
| `$env:PLAYWRIGHT_CHROME_PATH = 'C:\Program Files\Google\Chrome\Application\chrome.exe'; & .\node_modules\.bin\playwright.cmd test tests/run-progress.spec.ts tests/sse.spec.ts --reporter=list \| Tee-Object -FilePath '../docs/audits/weknora-ordinary-output-progress/frontend-final-browser-tests.log'` | 0 | `OFFLINE_PASS`: 16 passed (26.5s), final refinement covered. [Browser output](frontend-final-browser-tests.log). |
| `npm.cmd run build \| Tee-Object -FilePath '../docs/audits/weknora-ordinary-output-progress/frontend-final-build.log'` | 0 | `PASS`: TypeScript + Vite production build, 3346 modules. [Build output](frontend-final-build.log). |
| `git diff --check -- frontend` (project root) | 0 | `PASS`: no whitespace errors; only repository CRLF advisory. |
| Real backend/DB/model E2E, deployment, provider calls | — | `NOT RUN`; Docker engine unavailable per root's runtime audit, and this worker made no live model/API/DB changes. |

The final offline cases cover seven normal SSE connections across simulated 210 seconds; monotonic resume/dedup; scoped ten-second startup grace; the overall deadline; bounded abnormal retry/backoff; lost, pending, late and mismatched POST; prompt 4xx rejection; exact committed-history verification; candidate text rejection; cancellation despite uncooperative fetch/body cleanup; invalid/unknown progress fields; cheap truncation and expensive failure; live UI collapse/history restore; and delayed replay after conversation plus KB switch.

Screenshots from the offline live/history test were visually inspected: [pending](offline-progress-pending.png), [restored timeline](offline-progress-restored.png). Their data is deliberately synthetic and is not real-service evidence.

Actual screenshot source paths from the final focused run:

- `E:\RAG quention\frontend\test-results\run-progress-live-timeline-c532e--and-restores-after-refresh\offline-progress-pending.png`
- `E:\RAG quention\frontend\test-results\run-progress-live-timeline-c532e--and-restores-after-refresh\offline-progress-restored.png`

Durable copies are `E:\RAG quention\docs\audits\weknora-ordinary-output-progress\offline-progress-pending.png` and `E:\RAG quention\docs\audits\weknora-ordinary-output-progress\offline-progress-restored.png`. The final Playwright status was copied to [frontend-final-tests.last-run.json](frontend-final-tests.last-run.json).

## Risks and handoff

- Build emits the existing bundle-size warning (main JS about 861 KB). No unrelated chunking refactor was introduced.
- End-to-end service identity, actual persisted progress, current model output and citations require the root task's separately authorized real-service verification. Offline checks cannot establish `E2E_PASS` or milestone acceptance.
- No new dependencies, deployments, provider probes, database mutations, pushes, tags or commits were performed by this worker. Root agent coordinates the final commit and acceptance.
- 未实现的命令不得报告通过。
