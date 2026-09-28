# Cloud Evaluation Center MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a dependency-free private experiment registry and comparison Dashboard that imports sanitized local RAG evaluation bundles into SQLite.

**Architecture:** Keep the server package separate from `backend/`. Use a strict versioned bundle contract, transactional SQLite importer, read-only `ThreadingHTTPServer`, and static Dashboard. Local full reports are projected into an allowlist before SFTP upload; the server accepts no HTTP writes and binds only to loopback.

**Tech Stack:** Python 3.12+ standard library, SQLite, pytest already declared by the repository, PowerShell/OpenSSH SFTP, systemd.

**Spec:** `docs/superpowers/specs/2026-09-27-evaluation-center-design.md`

## Global Constraints

- Service host must be loopback; no public port or public product deployment.
- Do not add a new runtime dependency or use Docker.
- No question, answer, evidence text, chunk/document ID, embedding, prompt, model output, traceback, secret, database URL, token, hostname, or username may enter a bundle.
- Same experiment ID and digest is an idempotent no-op; same ID with another digest is a conflict.
- Database imports are atomic and parameterized; malformed imports leave no partial rows.
- Server HTTP endpoints are read-only; SFTP/SSH CLI performs import.
- Keep all full local RAG data on the local evaluation machine.

## Review Focus

- Raw reports can contain question, answer, chunk ID, traceback, and config secrets; the packer must project rather than recursively copy.
- A valid experiment ID reused with changed bytes must not overwrite the prior record.
- Two imports racing on the same ID must not create duplicates or partial case rows.
- Non-finite floats, NaN, booleans masquerading as numbers, oversized strings, and unsupported fields must be rejected without echoing them.
- A malformed or malicious path/query must not expose arbitrary files or change data over HTTP.

---

### Task 1: Versioned sanitized bundle contract

**Files:**
- Create: `eval_center/__init__.py`
- Create: `eval_center/contracts.py`
- Create: `evaluation_center/tests/test_contracts.py`
- Modify: `pyproject.toml` to include the new test path

**Interfaces:**
- `normalize_bundle(raw: object) -> dict[str, object]` returns a newly constructed allowlisted bundle or raises `BundleValidationError(code: str)`.
- `canonical_bundle_bytes(bundle: dict[str, object]) -> bytes` returns stable UTF-8 JSON with sorted keys and compact separators.
- `bundle_digest(bundle: dict[str, object]) -> str` returns lowercase SHA-256 hex.

- [ ] **Step 1: Write failing tests** for one valid minimal bundle; unknown/private fields; invalid UUID/hash/time; wrong config hash; duplicate or non-opaque case IDs; NaN/Infinity; unsupported schema; and stable digest.
- [ ] **Step 2: Run the new contract test file** using `\.venv\Scripts\python.exe -m pytest evaluation_center/tests/test_contracts.py -q`; confirm failures are missing implementation/expected validation.
- [ ] **Step 3: Implement strict allowlist normalization** in `eval_center/contracts.py`; use only standard-library `json`, `hashlib`, `math`, `re`, `uuid`, and `datetime`.
- [ ] **Step 4: Rerun the contract tests** and confirm all pass without warnings.

### Task 2: Transactional SQLite registry and comparison

**Files:**
- Create: `eval_center/store.py`
- Create: `evaluation_center/tests/test_store.py`

**Interfaces:**
- `initialize_database(path: Path) -> None` creates schema version 1 idempotently.
- `import_bundle(path: Path, bundle: dict[str, object]) -> dict[str, str]` returns `{"status": "imported"|"unchanged", "digest": ...}` and raises a stable conflict error for changed content under an existing ID.
- `list_experiments(path: Path, *, limit: int, offset: int) -> list[dict[str, object]]` returns metadata and aggregate metrics only.
- `get_experiment(path: Path, experiment_id: str) -> dict[str, object] | None` returns one sanitized record and its per-case summaries.
- `compare_experiments(path: Path, experiment_ids: list[str]) -> dict[str, object]` returns values/deltas and an explicit comparability result.

- [ ] **Step 1: Write failing tests** for schema creation/reopen, import, repeat import, conflicting digest, transaction rollback on a case collision, detail/list queries, and comparison mismatch warning.
- [ ] **Step 2: Run `\.venv\Scripts\python.exe -m pytest evaluation_center/tests/test_store.py -q`** and confirm expected red failures.
- [ ] **Step 3: Implement schema v1 and `BEGIN IMMEDIATE` imports** with parameterized SQL and no raw text columns.
- [ ] **Step 4: Run store tests** and confirm idempotence, conflict rejection, and rollback.

### Task 3: Read-only loopback API and Dashboard

**Files:**
- Create: `eval_center/server.py`
- Create: `eval_center/static/index.html`
- Create: `evaluation_center/tests/test_server.py`

**Interfaces:**
- `make_server(database_path: Path, host: str = "127.0.0.1", port: int = 8787) -> ThreadingHTTPServer` rejects non-loopback hosts.
- `GET /healthz` returns `{"status":"ok"}`.
- `GET /api/v1/experiments?limit=&offset=` lists run metadata and aggregate metrics.
- `GET /api/v1/experiments/<uuid>` returns sanitized detail or 404.
- `GET /api/v1/compare?ids=<uuid>,<uuid>` compares two to four runs and reports incompatibility.
- `GET /` serves only the bundled Dashboard page; all other paths are 404.

- [ ] **Step 1: Write failing HTTP tests** using a real ephemeral loopback server and temporary SQLite file; cover health/list/detail/compare, invalid host, path traversal, unknown paths, and static page response.
- [ ] **Step 2: Run `\.venv\Scripts\python.exe -m pytest evaluation_center/tests/test_server.py -q`** and confirm the expected failure before implementation.
- [ ] **Step 3: Implement read-only routes** using `ThreadingHTTPServer`; cap query values, suppress query strings from logs, return generic errors, and render labels with DOM `textContent`.
- [ ] **Step 4: Run server tests** and confirm they pass with no non-loopback bind.

### Task 4: Privacy-safe packaging and SFTP upload

**Files:**
- Create: `scripts/package_eval_bundle.py`
- Create: `scripts/upload_eval_bundle.ps1`
- Create: `evaluation_center/tests/test_packager.py`
- Modify: `scripts/evaluate_retrieval.py` and `scripts/evaluate_rag_quality.py` to include the stable `case_id` already present in each dataset row.
- Modify: `evaluations/core.jsonl`, `evaluations/incremental.jsonl`, and `evaluations/quality_v1.jsonl` to add unique stable `case_id` values without removing any frozen required field.
- Modify: `scripts/validate_eval.py` and `backend/tests/test_quality_eval_schema.py` to validate optional/required versioned IDs without weakening existing required field semantics.

**Interfaces:**
- `build_bundle(report: dict, manifest: dict, config: dict) -> dict` copies only manifest/config/metric/case allowlists and hashes case IDs to opaque IDs.
- CLI `python scripts/package_eval_bundle.py --report <local-report.json> --manifest <manifest.json> --config <config.json> --output <bundle.json>` writes sanitized schema v1 and prints only status, experiment ID, and digest.
- PowerShell `scripts/upload_eval_bundle.ps1 -Bundle <bundle.json> -HostName <host> -User <user> -Port <port> -IdentityFile <pem>` validates locally, requires strict known-host checking, SFTPs to a temporary remote name, atomically renames, and invokes the remote importer. PowerShell does not read or print key contents; OpenSSH reads the identity file only at runtime, and the path/value is not stored in the bundle or repository config.

- [ ] **Step 1: Write failing packager tests** with canary question/answer/chunk/traceback/API-key strings; assert none occur in serialized bundle and case IDs are stable opaque hashes.
- [ ] **Step 2: Run `\.venv\Scripts\python.exe -m pytest evaluation_center/tests/test_packager.py -q`** and confirm the privacy tests fail before the packager exists.
- [ ] **Step 3: Add stable case IDs to evaluation rows and project only allowlisted fields**; do not change the frozen four required fields or prior dataset meaning.
- [ ] **Step 4: Implement the upload helper** with runtime-only credentials and `StrictHostKeyChecking=yes`; no security-group or public-port changes.
- [ ] **Step 5: Run packager/schema tests** and inspect the generated sample bundle for privacy-canary absence.

### Task 5: CLI, deployment service, and operations guide

**Files:**
- Create: `eval_center/cli.py`
- Create: `eval_center/__main__.py`
- Create: `deploy/eval_center/rag-eval.service`
- Create: `deploy/eval_center/install.sh`
- Create: `docs/evaluation-center-operations.md`
- Create: `evaluation_center/tests/test_deployment_contract.py`

**Interfaces:**
- `python -m eval_center.cli validate <bundle.json>` validates and prints no bundle contents.
- `python -m eval_center.cli import <bundle.json>` imports into the configured SQLite DB and leaves its input untouched. The SFTP helper removes the remote staged copy only after successful import.
- The service runs as user `rag-eval`, serves only `127.0.0.1:8787`, and stores state below `/srv/rag-eval/`.

- [ ] **Step 1: Write failing deployment-contract tests** for loopback-only bind, unprivileged service user, no public port, private SQLite path, strict filesystem permissions, and installer idempotence expectations.
- [ ] **Step 2: Run the deployment-contract test file** and confirm it fails before the unit/installer/CLI exist.
- [ ] **Step 3: Implement CLI and deployment artifacts** with no package install, no Docker, no environment-secret logging, and no database deletion.
- [ ] **Step 4: Run all `evaluation_center/tests`** and the relevant existing schema/evaluation tests.
- [ ] **Step 5: Run a local end-to-end smoke** with temporary SQLite and HTTP server; verify API list/detail/compare and privacy canary absence.
- [ ] **Step 6: Review `git diff --check`, project status, and the full changed-file list.** Do not connect to or modify the ECS in this phase.

## Execution Method

Native inline implementation in this session, as requested by the project's autonomous Goal instructions. The component interfaces are sequentially coupled; keep a single writer and run each test-first task in order.

