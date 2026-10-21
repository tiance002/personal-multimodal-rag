# P6 streaming and references parity

## Scope and source map

This slice implements the request-local source registry, incremental citation marker decoder, append-only event protocol, Redis-list adapter, replay/coalescing primitives, and final persistence ordering contract. It is a Python service-layer port; no HTTP router, frontend, database adapter, model runtime, or real Redis service was changed or exercised.

Fixed source: Tencent/WeKnora `3e8b0bfc80b845b2d4b2ed683994748741450a97`, MIT, with project attribution in `docs/weknora-port/UPSTREAM_LICENSE.md`.

| Fixed-source function | Port | Behavior carried into this slice |
|---|---|---|
| `internal/modelcontext/sources.go:newSourceRegistry`, `knownHandle`, `registerChunk`, `registerWeb`, `RegisterSearchResults` | `backend/weknora_port/references/registry.py:SourceRegistry` | Per-response `cN/dN/bN/wN`; known handle strings cannot become new durable IDs; current chunk/web evidence is citable; history can register a chunk with `evidence=False`; web URLs deduplicate without fragments while keeping the original URL/title. `clone_for_attempt()` preserves these deterministic maps while resetting candidate observations; `snapshot()` returns immutable map state. |
| `internal/modelcontext/citations.go:CompactPublicCitations`, `ExpandText`, `citationStreamExpander.Feed/Flush` | `references/registry.py:compact_public_citations`, `expand_text`; `references/decoder.py:CitationStreamDecoder` | Replayed public `<kb>/<web>` citations compact into private handles with `evidence=False` by default. Model-authored public tags are discarded; citations disabled removes `<ref>`; valid current handles expand to escaped canonical tags; malformed, unknown and historical-only handles are consumed and removed. Split tags are held between fragments and incomplete recognized tags are dropped at flush. `used_handles`, `invalid_handles` and `invalid_count` are isolated per candidate and never reject an answer. |
| `internal/modelcontext/resources.go:EncodeText`, `DecodeText`, `EncodeMessages` | `references/registry.py:ResourceRegistry`; `SourceRegistry.encode_resource_text/decode_output_text`; `references/decoder.py:CitationStreamDecoder` | Storage URLs, `resource://` values, and `summary/<uuid>` receive low-entropy `res://NNNN` handles; clone snapshots preserve mapping; stream decoder restores a known resource handle even when split across provider fragments. Only explicit text is transformed; binary/image payloads are not traversed. `protocol_prompt` includes source and resource handle rules, preserving the image URL exception and citation-disabled variant. |
| `internal/types/interfaces/stream_manager.go:StreamEvent` | `streaming/events.py:StreamEvent` | Stored event JSON uses `id/type/content/done/timestamp/data/usage`; internal event ID remains separate from request ID in the HTTP response. |
| `internal/handler/session/helpers.go:buildStreamResponse` and `resource_urls.go:emitStreamEvent` | `streaming/events.py:response_for`, `to_sse` | Wire frame uses `event: message`; JSON `id` is the request ID and preserves `response_type/content/done/data/usage`. References data is also surfaced as `knowledge_references`. Segment identity remains in `data.event_id`. |
| `internal/handler/session/continue_stream_coalesce.go:coalesceReplayEvents` | `streaming/events.py:coalesce_replay_events` | Only adjacent unfinished `answer/thinking/reflection` deltas with same type and internal event ID merge; content concatenates, last timestamp wins, metadata keys merge later-wins; done/other-segment events pass through. Inputs are immutable. |
| `internal/stream/redis_manager.go:buildKey`, `AppendEvent`, `GetEvents`, `touchLiveRun` | `streaming/manager.py:RedisListStreamStore` | Default `stream:events:<session>:<message>`, RPUSH serialized event, 24-hour TTL refresh on append, raw list offset reads, malformed rows skipped while offset counts raw rows, optional injected live-run lease touch on append and polling (best-effort, matching source error handling). |
| `internal/handler/session/stream.go:ContinueStream` | `streaming/replay.py:ReplayService.replay` | Identity checks precede the stream read; replay begins at raw offset 0, initial history is coalesced, then appended rows are polled until a `complete` event; iterator cancellation only stops this reader. It never starts or retries generation. |
| Root-owned atomic answer/reference storage boundary | `streaming/replay.py:FinalCommit`, `finalize_turn` | Typed callback contract. The final answer and references must be committed atomically before `complete` is appended. A callback failure propagates and leaves no terminal event. |

## Fixed fixtures and observed result

`tests/weknora_port/test_references.py` covers current vs historical citable status, native handle rebinding rejection, public replay citation compaction to navigation-only status, URL fragment deduplication, public-tag forgery, disabled citation prompt/policy, unknown-handle consumption, split citation tags, deterministic Cheap/Expensive clone mappings with independent used/invalid observations, and text-only storage-resource masking with split stream restoration.

`tests/weknora_port/test_streaming.py` covers exact SSE framing and request/segment IDs, replay delta coalescing and immutable stored rows, Redis-protocol offset behavior including malformed raw rows and TTL refresh, offset-zero replay through complete, reconnecting the same assistant run after reader disconnect, and final-commit-before-complete ordering including commit failure.

The final focused test command below passed. The in-memory store and injected fake Redis client are deterministic protocol fixtures; they do not establish actual Redis behavior. No model, external provider, HTTP route, real Redis, or DB calls occurred.

## Commands and results

Command (PowerShell, isolated worktree virtualenv):

```powershell
& 'C:\Users\22088\.codex\worktrees\weknora-python-port\RAG quention\var\weknora-port\p2-venv\Scripts\python.exe' -m pytest --noconftest -q tests/weknora_port/test_references.py tests/weknora_port/test_streaming.py
```

Exit code `0`; output: `14 passed in 0.12s`.

Earlier executions returned exit `1` during implementation: an initial missing package export, then fixture expectation/type errors while adding candidate cloning and historical citation compaction. These were corrected before the final passing run above.

`git diff --check -- backend/weknora_port/references tests/weknora_port/test_references.py docs/weknora-port/P6_STREAM_REFERENCES_PARITY.md`: exit code `0`.

Real Redis integration: `NOT RUN`. HTTP/API integration: `NOT RUN`. Provider/model behavior: `NOT RUN`.

## Integration contracts and remaining boundaries

The route should validate the caller’s session ownership and assistant message, then iterate `ReplayService.replay(session_id, message_id, principal)`, serialize each event with `to_sse(event, persisted_request_id)`, and stop on request cancellation. The required query contract confirmed in fixed source is `message_id`; the route is `GET /sessions/continue-stream/{session_id}`. No `Last-Event-ID` recovery or generation retry is part of this implementation.

The storage owner should implement `FinalCommitter.commit_final(FinalCommit)` with the required transaction for answer plus references, then invoke `finalize_turn`. `complete` is written only after that callback succeeds. The context/generation owner should create one base `SourceRegistry` for the shared prepared evidence and use independent `clone_for_attempt()` instances for Cheap and Expensive; each clone retains same input mappings but candidate citations/invalid counts cannot leak into the other. Use default `evidence=False` when compacting replayed public citations; current result registration is the only operation that should authorize current citations.

The replay service raises `LookupError("STREAM_EVENTS_NOT_FOUND")` for absent/empty streams and leaves HTTP status mapping to the API owner. This slice restores known resource handles and drops orphan-shaped resource tokens for completed output, but its stream decoder does not yet compose issue/MCP handle decoding or the full unknown-resource orphan stream filter from upstream `modelcontext.StreamDecoder`. No claim is made that a real API reconnect or Redis-backed distributed run has been exercised.
