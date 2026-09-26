# ADR 005 — Opt-in Langfuse Tracing

**Date:** 2026-09-26

**Status:** accepted for implementation by the project owner’s explicit Langfuse tracing request

**Scope:** Optional external observability for Quick and Smart answer runs

## Decision

Add Langfuse tracing as an explicit, opt-in observability integration. It does
not enable a cloud model provider or change the public API, database schema, or
the local-first default.

A trace is created only when all of the following are true:

1. `RAG_LANGFUSE_ENABLED=true`.
2. `RAG_CLOUD_ENABLED=true` (the global outbound policy).
3. Every knowledge base in the conversation scope has `cloud_allowed=true`.
4. Langfuse credentials are available through `LANGFUSE_PUBLIC_KEY` and
   `LANGFUSE_SECRET_KEY`, or the existing `LANGFUSE_AUTHORIZATION` Basic value.

The Langfuse Python SDK is pinned to `4.15.6`. Smart mode uses Langfuse’s
LangChain callback integration. Quick mode uses SDK observations around the
shared retriever and the direct Ollama embedding, query-expansion, and answer
calls. Each answer is a descriptive root observation; the conversation ID is
used as `session_id`. The app has no user authentication, so no `user_id` is
sent.

Content capture is separately controlled by `RAG_LANGFUSE_CAPTURE_CONTENT`
and defaults to false. In this mode, the Langfuse export-stage mask removes
observation/trace input and output, prompt/completion, and tool payload
attributes before export while preserving useful model, latency, token, status,
and count metadata. If content capture is explicitly enabled, common email,
Basic/Bearer credential, and Langfuse secret patterns are redacted before
export; the global and per-KB egress gates still apply.

The API container receives Langfuse environment variables only to support this
opt-in feature. Compose continues to default global cloud egress to false; a
local `.env` may explicitly enable it. The Worker does not receive Langfuse
credentials.

## Consequences

- Langfuse outage, missing credentials, callback initialization errors, or
  export failures must not fail an answer. Tracing is skipped or logged.
- The Langfuse client batches in the background and shuts down with the API
  process; request handling does not synchronously flush the queue.
- With content capture disabled, traces emphasize model and pipeline behavior
  without recording user question/document/answer text.
- Content capture increases the data sent to the user’s US Langfuse project and
  should only be enabled when that is acceptable for every scoped knowledge
  base.

## Evidence

- Current official Langfuse docs recommend the Python SDK, `start_as_current_observation`,
  the native LangChain callback, session IDs for conversations, and export-stage
  masking.
- `langfuse==4.15.6` was installed in the project virtual environment and its
  client, callback, and masking APIs were import/signature checked.
- App-path verification and a real Langfuse trace are tracked in the task
  report; they must not be reported as passing unless actually executed.
