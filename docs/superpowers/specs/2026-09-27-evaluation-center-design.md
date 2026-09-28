# Cloud Evaluation Center MVP — Design

**Date:** 2026-09-27  
**Status:** implementation baseline for the user-authorized evaluation-platform goal  
**Scope:** private ECS import, experiment metadata storage, read-only comparison API, and Dashboard

## Purpose and success criteria

The center stores and compares reproducible local RAG evaluation results. Local machines continue to run retrieval, generation, and judges; the ECS stores only sanitized metrics and experiment metadata. The center is usable through an SSH tunnel, can import the same package repeatedly without duplicate rows, refuses changed content under an existing experiment ID, and visibly warns when experiments are not comparable.

The user-provided project brief remains the source of truth for broader M2–M8 work. This MVP does not claim that the local real-RAG evaluator or Gold Set is complete.

## Constraints

- Do not run Qwen or BGE-M3 on the ECS.
- Do not expose the existing RAG product or the evaluation center to the public network.
- The management service binds only to `127.0.0.1`; access is through SSH Tunnel.
- The client sends only allowlisted manifest fields, numeric aggregate metrics, allowlisted configuration values, opaque case IDs, pass/fail status, stage names, and machine error codes.
- Never send question text, reference answers, retrieved text, chunk/document IDs, embeddings, prompts, model outputs, tracebacks, environment values, database URLs, tokens, or host/user names.
- No new runtime dependency. Use Python standard library, SQLite, systemd, and existing SSH/SFTP tooling.
- Keep this module outside production `backend/` code. Do not use the existing product Compose stack.
- Do not perform remote backup, stop, delete, security-group, or deployment operations until the user approves the concrete cleanup list.

## Data flow

1. The local evaluator writes its full local report and run artifacts.
2. A package command reads the report, manifest, and allowlisted configuration, then projects them into a versioned sanitized bundle. It must not copy arbitrary source fields.
3. The user transfers that bundle with SFTP to an incoming path and invokes a remote CLI over SSH. Upload uses a temporary name and an atomic rename.
4. The CLI validates the bundle, hashes its canonical representation, and imports it in a SQLite transaction. Same ID + same digest is an idempotent no-op; same ID + different digest is a hard conflict.
5. The service exposes read-only JSON and static HTML endpoints on loopback. It never accepts HTTP writes.
6. The Dashboard lists runs, displays selected metrics and failed opaque case IDs, and compares compatible runs. A mismatch in dataset version, corpus hash, or evaluation mode is shown explicitly.

## Bundle schema v1

A bundle contains exactly these top-level fields: `schema_version`, `manifest`, `config`, `metrics`, `cases`, and `errors`. Unknown fields are rejected by the importer. The client exporter is responsible for removing private fields before transfer.

`manifest` contains: UUID `experiment_id`; hexadecimal `git_sha`; `dataset_version`; SHA-256 `corpus_hash` and `config_hash`; model and embedding profile names; timezone-aware start/end timestamps; allowlisted OS/Python/architecture metadata; `evaluation_mode`; overall `status`; and `sample_count`.

`config` contains only allowlisted scalar retrieval/chunking/model-profile parameters. It cannot contain prompts, provider URLs, arbitrary environment settings, or credentials. The importer verifies `config_hash` against canonical JSON.

`metrics` and per-case metrics contain finite numeric values from a fixed metric-name allowlist. Each case contains an opaque `case_<12 lowercase hex>` ID, `passed`/`failed`/`not_evaluated` status, optional allowlisted stage and machine error code, and numeric metrics. Error rows contain only case ID, stage, and machine error code. Free text is rejected.

The importer uses SQLite schema version 1. Future schema changes require an explicit new version and conversion; historical rows are never silently rewritten.

## Components and interfaces

- `eval_center/contracts.py`: `normalize_bundle(raw)`, canonical JSON, digest, and strict privacy/schema checks.
- `eval_center/store.py`: initialize the SQLite schema; transactionally import; query list/detail; compare selected experiment IDs.
- `eval_center/cli.py`: validate and import a sanitized bundle; write generated report metadata without retaining raw local reports.
- `eval_center/server.py`: threaded standard-library HTTP server with `/healthz`, read-only experiment list/detail/compare endpoints, and one static Dashboard page. The host argument must resolve to loopback.
- `eval_center/static/index.html`: small responsive UI; render untrusted labels with `textContent`, never HTML interpolation.
- `scripts/package_eval_bundle.py`: project existing local reports into the allowlisted bundle. Original full reports remain local.
- `scripts/upload_eval_bundle.ps1`: strict-host-key SFTP upload and SSH-triggered import; identity file is a runtime argument, never stored in the repository.
- `deploy/eval_center/`: idempotent installer and systemd service template. Use a dedicated unprivileged account and the recommended `/srv/rag-eval/{app,configs,datasets,experiments,reports,logs,backups}` layout.

## Error handling and security

Reject malformed JSON, unsupported schema versions, invalid UUID/hash/timestamps, duplicate opaque case IDs, unknown fields, non-finite metrics, private/free-text fields, oversized bundles, and mismatched hashes with stable machine error codes. Do not echo rejected values in error responses or logs. Database writes use parameterized SQL and `BEGIN IMMEDIATE`; failures roll back fully. Static serving is limited to one bundled HTML resource. No CORS, public listener, write HTTP route, or model provider is configured.

The deployment must preserve existing SSH access. Aliyun security-group rules are outside the guest SSH view and require owner-side console review; the new service must not rely on opening an inbound port.

## Verification

- Unit tests cover schema/privacy rejection, config hash, case-ID opacity, canonical digest, import idempotence/conflict/rollback, compatible and incompatible comparison, and the loopback-only server behavior.
- An end-to-end local smoke starts the real standard-library server with a temporary SQLite database, imports a sanitized sample, lists and compares it over HTTP, and confirms a privacy-canary question/answer/chunk string is absent from the uploaded bundle, database, and response.
- Deployment verification after the user's cleanup approval covers systemd health, restart behavior, localhost binding, tunnel access, and SQLite persistence. No server cleanup or deploy happens before approval.

## Explicitly deferred

This MVP does not finish the full Gold Set expansion, stable evidence mapping, complete stage-level real PostgreSQL/pgvector evaluator, LLM Judge/Ragas, Langfuse Cloud integration, large-scale parameter search, or release/restore certification. Those remain separate goal phases and must not be claimed as complete by the Dashboard implementation.
