# P6 React adapter checkpoint

## Scope and behavior

The alternate React entry is selected only when `VITE_RAG_IMPLEMENTATION=weknora_python`. The existing app remains the default entry. The adapter talks only to the `/api/v1` Python API, using same-origin by default or `VITE_WEKNORA_API_BASE` when configured. It requires `GET /runtime` to identify `weknora-python-port` before any write; an identity mismatch blocks writes and never falls through to the existing backend.

The new workbench shows knowledge bases, document filenames and index status, saved sessions, and five model roles (`embedding`, `rerank`, `cheap`, `expensive`, `vlm`). API keys are write-only fields. Quick question is wired; smart reasoning remains visible but disabled as unavailable. Chat errors appear in an assistant message. Citation chunk handles open a source drawer backed by `GET /chunks/{chunk_id}`.

Chat submits one `POST /sessions/{session_id}/messages`. It captures the first `agent_query` event's stable assistant message, request, and session IDs. If the stream disconnects before `complete`, it reconnects by `GET /sessions/continue-stream/{session_id}?message_id={original_assistant_message_id}` only. Each offset-zero replay resets and rebuilds the local event projection. `answer.done` closes a segment; only `complete` marks the answer saved. Explicit reconnect also uses GET only. The client does not manufacture progress events from history.

## Validation evidence

The offline Playwright suite covers visible knowledge/document scope, five model roles, disabled smart mode, one-POST plus same-message GET replay, answer segment reconstruction, and runtime identity mismatch behavior. Its configuration uses mocked HTTP responses and makes no model/provider calls.

The exact locked frontend dependencies installed successfully. The first focused browser run identified a missing Playwright headless shell; after installing the package-pinned Chromium 1243 shell, the normal focused suite passed. The initial test also exposed that its filename assertion ran before selecting the knowledge base; the fixture now selects the KB before expecting its documents. Actual outcomes:

| Command | Exit | Result |
|---|---:|---|
| `npm ci` (from `frontend`) | 0 | 194 packages installed from the lockfile; npm reported one high-severity advisory |
| `npm run build` (from `frontend`) | 0 | TypeScript check and Vite production build succeeded |
| `npm test -- --config=weknora-port.playwright.config.ts` (first run) | 1 | Browser launch blocked because Playwright Chromium headless shell 1243 was absent |
| Pinned Chromium 1243 headless-shell download and extraction | 0 | Exact Playwright archive (120,200,717 bytes) installed in the Playwright browser cache |
| `npm test -- --config=weknora-port.playwright.config.ts` (final run) | 0 | 4 focused mocked-API browser tests passed |

No real Python HTTP integration or provider request was made. The API contract is recorded in [API_CONTRACT.md](API_CONTRACT.md); root API integration should use that DTO/event contract and run isolated HTTP integration separately.

## Remaining limitations

The adapter is a UI/API integration slice, not evidence of backend interoperability. The actual runtime, persisted history shape, chunk authorization response, and live SSE timing still require isolated HTTP integration against the Python port. The `complete` event is treated as the server's persistence boundary per the agreed API contract. Smart reasoning remains unavailable until the port advertises and implements it.
