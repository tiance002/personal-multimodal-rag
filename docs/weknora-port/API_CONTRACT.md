# WeKnora Python React adapter API contract

This contract belongs to `frontend/src/weknora_port/*`. The existing application remains the default entry. The alternate UI is selected only when `VITE_RAG_IMPLEMENTATION=weknora_python`; every mutating operation first verifies `GET /api/v1/runtime` returns `data.implementation === "weknora-python-port"`. A mismatch blocks the operation and is displayed to the user. The client never falls back to the prior backend.

All non-stream JSON responses are `{ "data": <payload>, "meta": { "request_id": "..." } }`. Errors are `{ "error": { "code": "...", "message": "..." } }`. The API base is `VITE_WEKNORA_API_BASE` when configured, otherwise same-origin `/api/v1`. No bearer token is required by this local first-party adapter; the browser sends same-origin credentials when present.

## Endpoints

| Method and path | Request | `data` response |
|---|---|---|
| `GET /runtime` | none | `{implementation: "weknora-python-port", api_version: "v1"}` |
| `GET /knowledge-bases` | none | `[{id,name,cloud_allowed,document_count}]` |
| `POST /knowledge-bases` | `{name}` | `{id,name,cloud_allowed,document_count}` |
| `GET /knowledge-bases/{kb_id}/documents` | none | `[{id,knowledge_base_id,filename,status}]` |
| `POST /knowledge-bases/{kb_id}/documents` | multipart `file` | `{id,knowledge_base_id,filename,status}` |
| `GET /settings/model-configuration` | none | `{revision,roles:[{role,provider,model,base_url,key_configured}]}`; never returns stored key material |
| `PUT /settings/model-configuration` | `{revision,roles:[{role,provider,model,base_url,api_key?}]}` | same masked response shape; blank `api_key` keeps existing key |
| `GET /sessions` | none | `[{id,title,knowledge_base_scope,document_scope,updated_at}]` |
| `POST /sessions` | `{knowledge_base_scope:[id],document_scope:[id],title?}` | session shape above |
| `GET /sessions/{session_id}/messages` | none | `[{id,role,content,request_id,created_at}]` |
| `POST /sessions/{session_id}/messages` | `{content,request_id,mode:"quick"|"smart",knowledge_base_scope:[id],document_scope:[id]}` | `text/event-stream`; exactly one generation POST |
| `GET /sessions/continue-stream/{session_id}?message_id={assistant_message_id}` | replay existing message | `text/event-stream`; offset-zero replay and continuation of the same run |
| `GET /chunks/{chunk_id}` | none | `{chunk_id,document_id,filename,text,source_url}`; server checks the caller's effective KB/document scope |

The model configuration UI exposes only `embedding`, `rerank`, `cheap`, `expensive`, and `vlm`; each editable role has `provider`, `model`, `base_url`, and a write-only `api_key`. Internal principal, tokenizer, ledger, provider routing, and safety fields are not configuration-form fields.

## SSE message event

Both message POST and continuation GET use `event: message`, with the data line containing JSON:

```json
{
  "id": "request-id",
  "response_type": "answer",
  "content": "delta text",
  "done": false,
  "data": {"event_id": "answer-segment-id"},
  "usage": null
}
```

The first `agent_query` message must include `data.assistant_message_id`, `data.request_id`, and `data.session_id`; `data.request_id` must equal the JSON `id` and `data.session_id` must equal the requested session. The client captures these stable values and rejects changes within one run. `data.event_id` identifies an answer/thinking/reflection segment. Repeated deltas with one event ID append to the same segment. `answer` with `done:true` closes that segment only. Only `response_type:"complete"` terminates the run, after final answer/reference persistence succeeded.

If POST stream transport ends before `complete`, the client reconnects only with GET continuation and the original `assistant_message_id`. GET starts from offset 0 and replays the same run; before applying that replay, the client resets its locally reconstructed segment/progress projection, then rebuilds it from events. The client deduplicates event IDs/segments and will never send a second POST for a timed-out, interrupted, or malformed SSE stream. A GET stream ending before `complete` may be retried as GET for the same original message, with bounded retries.

## Errors and unavailable scope

An HTTP or runtime-identity error is rendered in an assistant error bubble for chat and an inline error for settings/library. The `smart` option remains visible but the UI shows “暂不可用” and disables its submit path until the API advertises support. A failed `GET /chunks/{chunk_id}` displays an inline unavailable-source state; it does not fabricate a citation target.


## Knowledge base external processing consent

`PATCH /api/v1/knowledge-bases/{knowledge_base_id}/external-processing` accepts only `{"cloud_allowed": true|false}`. StrictBool rejects string/numeric/null coercion and the DTO rejects extra owner/scope fields. The injected runtime authorizes the trusted principal and current KB before changing its stored consent. All PATCH writes use the existing same-Origin guard, including missing/null/cross-Origin denial. The response is the current KnowledgeBase row (id/name/cloud_allowed/document_count).

The library UI shows the current selected KB's persisted permission and an explicit privacy switch. Enabling permits questions, retrieved passages and images to be processed by configured cloud models; disabling prohibits their external processing. The switch updates only after a successful save, and failure leaves the prior displayed value with an inline error. Saving permission does not run model probes or approve real model calls for this Goal. Default new-KB consent stays false.
