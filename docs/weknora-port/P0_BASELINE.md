# P0 — fixed source and isolated implementation baseline

Date: 2026-10-13. Owner-approved target: fixed WeKnora Python/FastAPI equivalent core, React, mandatory original P5 selection and controlled Cheap-to-Expensive upgrade. Full supplied instructions are archived without changes in `task/`.

## Actual identities

- Original checkout: `E:\RAG quention`, clean `main@55372f7bae8e30a8150d16e0eb12262a038cbea2`. `git ls-remote origin refs/heads/main` returned the same SHA (exit0). No reset, stash, push, or existing source modification.
- Managed worktree: `C:\Users\22088\.codex\worktrees\weknora-python-port\RAG quention`, branch `codex/weknora-python-port`, same starting SHA. This is the only implementation checkout for this Goal.
- Read-only upstream: `E:\RAG-reference\WeKnora-3e8b0bfc`, detached HEAD `3e8b0bfc80b845b2d4b2ed683994748741450a97`. Pinned shallow fetch/checkout exit0. Relevant55Git blobs independently hashed and matched, including working-tree Git filters; manifest `source/BASELINE_SOURCE_MANIFEST.json`. No unpinned tutorial/main replaces this source.
- Upstream LICENSE: Tencent2025 MIT for main project, independent third-party notices/licenses remain applicable. Translated substantive implementations retain attribution; DocReader direct reuse must retain its license headers and dependency notices.
- Current Docker runtime: NOT_VERIFIED. `docker ps --format '{{.Names}} {{.Image}} {{.ID}}'` exit1, Desktop Linux engine pipe unavailable. No engine/service was started, replaced or reconfigured for this Goal. Historical `ce3398a` image and receipts are not relabelled as current observations.
- Go executable: not found via `Get-Command go -ErrorAction SilentlyContinue`; actual upstream Go oracle execution NOT RUN. Python fixtures alone cannot establish full behavioral parity.

## Scope decisions

The latest owner instructions explicitly supersede old QuickChain/RegexValidator/query-driven-output architecture for this isolated implementation. Old code stays untouched until a separately approved cutover and independent cleanup. `backend/weknora_port/` is the new product core, not a second selectable legacy QuickChain algorithm. Formal activation occurs in P6/P8 after its pipeline is implemented and tested.

Only two business deviations: P5 model selection and controlled escalation. Context preparation is shared and precedes selection. P5 threshold0.35/six contributions and feature-wise max(q0,rewrite) are frozen. **Owner update in this conversation:** explicit Cheap model-call timeout causes one Expensive upgrade, no Cheap retry; cancel/SSE disconnection/permissions/budget/general provider failure do not qualify. This narrow override supersedes the attachment's timeout-default-off row. No real-provider approval is implied.

The Smart/Agent UI entry is retained. Its detailed fixed-upstream orchestration is not silently removed or presented as ready. Its exact P6 compatibility state and any unported product surface must be reported before cutover; core RAG parity is the immediate target.

## Actual commands and evidence

From the isolated worktree, using the existing verified local Python executable:

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' scripts/weknora_port_baseline.py --upstream 'E:\RAG-reference\WeKnora-3e8b0bfc' --task-zip 'C:\Users\22088\Downloads\WEKNORA_PYTHON_RAG_P5_HANDOFF_20261013.zip' --report docs/weknora-port/source/BASELINE_SOURCE_MANIFEST.json
```

Exit0:55verified blobs, missing paths0, provider calls0, old DB writes0. First attempt exit1 due Windows CRLF checkout bytes differing from Git blobs; corrected by hashing canonical object bytes plus checking the working-tree filtered hash. No upstream code or Git-global setting altered to make the check pass.

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' scripts/contract_test.py --report var/reports/weknora-port/P0-contract.json
```

Exit1: overall FAIL,31PREEXISTING layering violations; OpenAPI paths/snapshot, forbidden-term and SSE sample checks have no drift. Preserve this baseline; do not disable assertions or refactor unrelated legacy modules.

Source YAML/effective-fallback expectations and18pure legacy decision snapshots captured in `tests/weknora_port/fixtures/`. These are deterministic SOURCE_EXPECTATIONS, not a claim that the Python port or upstream Go executed equivalent RAG. Input load is UNKNOWN in those legacy snapshots; it is not zero.

## P0 status and next action

Identified-source/worktree isolation: PASS. Current production container/DB inspection: BLOCKED/NOT RUN while Docker is unavailable. Pure-source fixtures and source mapping: captured. New chain behavioral parity: NOT_IMPLEMENTED at P0. Proceed with P1 unified Provider mock tests; continue P2/P3 source reading and implementation independently of paid models and old runtime access.

未实现的命令不得报告通过。
