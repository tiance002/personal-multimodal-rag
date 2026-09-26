# Personal Multimodal RAG V1.0 Delivery Report

**Date:** 2026-09-26

**Scope:** local V1.0 delivery; no release tag or remote push performed in this session.

## 1. Goal and changes

- Enforced graph opt-in end to end: disabled knowledge bases return structured `GRAPH_DISABLED`, graph SQL is defense-in-depth filtered, Smart omits the graph tool by default, and the frontend hides only the graph entry while keeping document readback available.
- Enforced server-side conversation scope: document IDs must belong to the selected knowledge-base scope; changing the knowledge base or selecting “仅此文档” patches the active conversation scope.
- Restored selected knowledge-base/document scope when switching history conversations and preserved the active-version/citation readback path.
- Added regression coverage for graph isolation, cross-KB document scope, graph-enabled Smart tools, frontend graph visibility, and scope synchronization.
- Updated the M1 gate to reference the current Quick Chain tests, and updated real UI/Compose smoke checks to use an explicit graph-enabled fixture.
- Updated README and ADR-003 to document default-off graph behavior and server-owned scope.

## 2. Executed commands and evidence

| Command | Exit | Evidence |
|---|---:|---|
| `\.venv\Scripts\python.exe -m pytest -q --tb=short -p no:cacheprovider` | 0 | `141 passed`, 6 existing deprecation warnings |
| `\.venv\Scripts\python.exe -m compileall -q backend scripts` | 0 | Python syntax check |
| `npm --prefix frontend run build` | 0 | TypeScript check and Vite production build |
| `$env:PLAYWRIGHT_CHROME_PATH='C:\Program Files\Google\Chrome\Application\chrome.exe'; npm --prefix frontend test -- --reporter=line` | 0 | 7 Playwright tests passed |
| `& .\scripts\verify-m0.ps1` | 0 | `var/reports/verify-m0.json` PASS |
| `& .\scripts\verify-m1.ps1` | 0 | real PostgreSQL/Ollama/API/SSE; `var/reports/verify-m1.json` PASS |
| `& .\scripts\verify-m2.ps1` | 0 | PDF/OCR; `var/reports/verify-m2.json` PASS |
| `& .\scripts\verify-m3.ps1` | 0 | evaluation and graph readback; `var/reports/verify-m3.json` PASS |
| `& .\scripts\verify-m4.ps1` | 0 | Smart allowlist and Agent smoke; `var/reports/verify-m4.json` PASS |
| `& .\scripts\contract_test.ps1` | 0 | OpenAPI/SSE snapshot and layering contract PASS |
| `& .\scripts\verify-release.ps1 -Fresh` | 0 | `var/reports/verify-release.json` PASS; Compose build/runtime/browser, backup and restore PASS |
| `& .\scripts\release_report.ps1` | 0 | `var/reports/v1-release-report.md` and `.json` status PASS |

The repository has no `Makefile`; `make verify-*` is therefore **NOT IMPLEMENTED**, not treated as passed.

## 3. Result

**PASS — locally deliverable V1.0 implementation.** The real local path covers text upload/index/retrieval/answer/citation readback, PDF and OCR failure handling, scoped multi-document retrieval, document-only scope, graph opt-in, Smart read-only tools, frontend browser behavior, Compose/Nginx proxying, and backup/restore checks.

## 4. Risks and leftovers

- Owner acceptance is still required before calling this a project milestone; no tag was created.
- VLM captioning and broad open-ended semantic evidence judging remain outside the current V1 implementation; OCR and conservative evidence checks are the supported path.
- The worktree contains the implementation changes uncommitted so the project owner can review them. No remote push was performed by this session.

## 5. Version and next step

- Commit: **not created in this session**.
- Tag: **not created**.
- Next minimal action: owner reviews this report and `var/reports/verify-release.json`, then decides whether to commit and tag the accepted V1.0 state.
