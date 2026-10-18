# Active migration execution instructions

Owner task: `docs/weknora-port/task/02_Codex完整执行Goal.md` and its companion plan/decision files. These owner instructions supersede conflicting old QuickChain, online regex-validator and query-driven output-budget architecture guidance for this isolated worktree only.

- Fixed business baseline: Tencent/WeKnora `3e8b0bfc80b845b2d4b2ed683994748741450a97`. Python/FastAPI and React remain required.
- Implement the new formal chain under `backend/weknora_port/`; do not wire it to the running old service during offline implementation. Preserve old runtime/data/history/evaluation artifacts. New code must not import old QuickChain, generation hardening, retrieval routing, experimental grants or output capability probes.
- The two accepted business deviations are frozen P5 six-feature model selection and controlled Cheap-to-Expensive escalation. Baseline bypasses P5. Keep threshold0.35, feature-wise max(q0,rewrite), quality/truncation escalation on, provider fallback/abstention off. Final Context is shared across attempts.
- Minimal scope/egress/identity/idempotency/monthly-budget/UNKNOWN wrappers remain mandatory and cannot change retrieval, prompt, output-length or routing algorithms.
- Default network send gate is closed. No real model request, old service start/replacement, old DB DDL/DML, data migration, UNKNOWN release, Push/Tag/Release without new owner approval.
- New synthetic integration data must use isolated `weknora_python_port_20261013` database/storage and `weknora-python-port:20261013:` Redis prefix; fail before any write when identity matches old business resources.
- Root coordinates writes/commits; other workers own explicitly assigned files, do not recursively dispatch or revert others. Keep stages independently reviewable with source mapping, exact commands/exits and local commits.
- Report SOURCE_READ, IMPLEMENTED, OFFLINE_PASS, ISOLATED_INTEGRATION_PASS, REAL_PROVIDER_PASS and CUTOVER_PASS separately. Existing31layering errors stay PREEXISTING, not PASS.
- **未实现的命令不得报告通过。**

## Active Phase 01 (Owner execution pack, 2026-10-18)

`RAG_Phase_01_Execution_Pack.zip` supersedes older diagnosis-only freezes for
local evidence selection, merging and context construction. Related bounded
improvements and regression tests are permitted; experimental settings remain
opt-in until review. Preserve current model bindings, DYNAMIC, MinerU-only OCR,
tenant/source/version boundaries and all historical UNKNOWN. Old account/month
authority mechanisms retired in P31 must not be restored. New Provider sends
require explicit Phase 01 approval; the package's suggested envelope is not a grant.
