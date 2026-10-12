# 历史单次任务合同：DYNAMIC-ROUTE-01

本文件归档原 AGENTS.md 的单次协作试点附录，仅记录该任务路径、路由、旧额度与停止状态，
不作为当前全局执行规范或普通服务调用许可。下面原文逐字节保留；
新任务的明确 Owner 授权和通用隐私、预算、Scope、证据规则仍须单独执行。

---

## Dynamic task execution contract (DYNAMIC-ROUTE-01 rev1)

This is the single project source for common execution constraints. Model routing
and task responsibility are separate: FAST=gpt-6-luna/max;
NORMAL=gpt-6.1-sol/medium; HARD=gpt-6.1-sol/xhigh. These are requested routes,
not proof of serving identity. The parent/user assigns the route and responsibility
in each id/revision/attempt contract. Workers never switch models or recursively
dispatch; only the coordinating parent may dispatch. Do not install custom agents
or change config.toml under this contract. Preserve disable_response_storage and
existing auth/provider/billing/privacy/approval/sandbox settings.

Every packet must name exact allowed read/write paths. The current implementation
may write only this AGENTS.md appendix and preparation/evidence files under
E:\codex_workspace\2026-10-01\task-2\dynamic-route-01. Read-only audit workers
have no writable project targets; return their Evidence Packet to the parent.
Do not scan outside their enumerated files, read .env/auth.json/token or sensitive
sessions/private corpora, follow rejected paths, or use network/model APIs unless
separately authorized. RAG code/data/indexes, services, credentials and the global
DeepSeek ledger are outside all task write scopes. Preserve7 calls/855 tokens,
entry disabled; no reset/restore or further DeepSeek/Langfuse request.

At most3 substantive executions including the coordinating parent, with one
assigned writer. The parent tracks active tasks and confirms cancellation before
replacing them. A cancelled worker must stop new commands/writes and deliver
existing evidence. Reuse task identity to prevent duplicate execution. Initial
attempt plus at most1 evidence-driven repair; thereafter hand off or BLOCKED.
Missing permissions, environment failures or missing evidence do not justify a
stronger model. HARD requires an explicit complexity-based assignment.

Evidence Packet: task id/revision/attempt, responsibility, requested model/effort,
observable actual model/effort and exact source/limits (UNKNOWN if unavailable),
HEAD plus dirty-snapshot identity, commands/exit codes/actual check counts,
artifact hashes, failures and carried handoff evidence, available usage and elapsed
time. Separate requested, configured, app-server-confirmed settings and upstream
actual identity. Model self-report and disk config are not route proof. Never
invent token/cost/timing or call UNKNOWN/PASS evidence interchangeable.

Executors may recommend CHECKS_PASSED, BLOCKED or NEEDS_OWNER_REVIEW, never grant
ACCEPTED, milestone approval, publication or deployment. The parent/user evaluates
semantic results from raw questions/reference/context/answers. Existing automatic
checks or executor judgments cannot replace independent evaluation. Simulation
fixtures must say SIMULATED explicitly and retain their original evidence.

These instructions are not an OS/tool hard boundary. Unless cancellation, single
writer, concurrency, no-recursion and duplicate prevention are verified by control
plane/tool enforcement, report MANUALLY_SUPERVISED_TRIAL, not fully automatic.
