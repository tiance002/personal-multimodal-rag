# AGENTS.md — 个人本地知识库 Agent 开发执行规范

> 用途：放在**新知识库项目根目录**，供每次 AI 开发会话首先读取。它是简短的执行入口，不替代项目计划、软件设计和具体模块契约。
> 项目负责人：用户。AI 可以提出方案、分级与延期建议，但不能代替项目负责人作范围、延期或里程碑批准。

## 1. 每次会话先做什么

1. 读取本文件、`progress.md`、`docs/adr/` 的索引和当前任务相关的设计章节；若文件不存在，明确报告，不得臆造其内容。
2. 核对当前里程碑、目标、允许改动的模块、验收标准；只做当前任务所需的最小变更。
3. 查看 Git 工作区及已有代码；不得覆盖用户或其他会话的未提交改动。确认所需命令和依赖**真实存在**，再执行。
4. 先报告本次任务的最小实施步骤；除非存在阻断问题，不扩张到下一里程碑或无关优化。

## 2. 当前产品边界（以 V4.2 计划/设计及已批准的后续决策为准）

- 独立新项目：个人、本地、多知识库；不实现登录/注册、完整 SaaS、多用户 RBAC、Wiki 或学习计划业务。
- V1.0 路线：M0 基线 → M1 真实 RAG → M2 PDF/图片与资料管理 → M3 检索质量/可选图谱 → M4 内置只读 Agent → M4.5 发布。
- V2.0 Backlog：外部 MCP、审批、独立沙箱、第三方 Skills。**不得在 V1.0 新增其运行代码、API 或表作为当前交付前置条件**。
- 前端：左侧主导航与历史会话；中栏选择知识库、拖放上传和资料列表；右侧“文档/图谱”，默认文档、选中绿色；左侧历史会话**不是**知识库目录。
- 快速问答走固定的 LangChain `Runnable` Quick Chain；智能推理调用 LangChain `create_agent`。两种执行模式共享 `KnowledgeGateway` 与项目 RAG Core，不复制查询规划、混合检索、证据覆盖、引用和答案校验逻辑。
- 旧学习规划助手本机参考目录：`E:\codex_workspace\study-plan`，**只读参考与选择性复用候选**；实际文件/版本需在用户本机核实。新项目不得依赖旧项目数据库、运行服务或业务代码。不得在本机路径不可访问时假称已读取。

## 3. 不可让步的不变量

- 原始资料和历史版本不可静默覆盖；当前版本切换与索引状态一致；回答引用只能指向真实、可回读的有效证据；图谱关系必须能回查原始片段。
- 查询必须使用服务端确定的 KB/document 范围；不得跨库串检索。`cloud_allowed=false` 或全局外发禁用时，问题、片段、图像与派生内容均不得发送云端；不可用时明确提示，不得静默回退云端。
- L0 规则/原始查询 `q0` 始终可用；L1 本地模型可关闭、超时或失败，不得阻断基本 RAG。`ornith-1.5:9b` 是本机候选标签，须运行 `ollama list/show` 和能力冒烟，以实测结果与 ADR-001 为准。
- Embedding 使用独立真实模型；HyDE 假设文本和模型生成内容不能被当成原文证据；无证据时明确不足，不编造答案或资源 ID。
- 云端调用要经过实际外发规则与月度预算门；预留占用计入额度，费用不明时保守占用，不当作免费。首版预算仅做必需主路径，不为了边角状态推迟 RAG 闭环。
- Agent 仅用已声明的内置只读知识工具，并受步数、时间、Token、成本和取消限制；不得在 V1.0 引入 shell 执行和外部工具权限。
- 数据丢失、隐私外发、跨库泄露、费用失控、无依据引用以及核心流程故障，不能以“低频”为理由降级延期。

## 4. 开发节奏和测试范围

- 功能纵切：先完成“拖拽上传 → 解析/索引 → 检索 → 回答 → 引用可点”，再增强解析、图谱与 Agent。
- 常规迭代应以实现和集成为主要投入；65% 开发/集成、30%～35% 测试/调试仅作健康信号，**不能**用来跳过 P0/P1 验证。
- 每次只运行**受影响模块**的必要测试及关联不变量检查；检索改动跑对应评测，Schema/API/SSE 修改跑相应契约；里程碑才跑该阶段端到端，发布前做完整回归和恢复演练。
- 难以触发、影响局部、可绕过且不触及数据/隐私/证据/费用/核心功能的 P2/P3 可以提出延期。对同一局部 P2 连续两轮修复无实质进展，应停止无限调试，提交最小复现、日志、影响、绕过方式与建议。
- **延期判定须经项目负责人确认；AI 会话可提出分级建议与证据，但不得自行批准延期。** P0/P1 必须在相关交付前处理。
- 不为低风险缺陷擅自进行跨模块重构，不主动引入灰度、多环境、企业权限、复杂链路平台或未来阶段的基础设施。

## 5. 机器护栏：只认实际命令结果

- 必须保留原句：**未实现的命令不得报告通过。** 不得把“已写测试”“理论上应通过”“模拟器通过”写成真实验证通过。
- 计划中的命令（如 `make schema-check`、`make contract-test`、`make eval`、`make verify-m0`…`make verify-m4`、`make verify-release`）只有在仓库存在对应 target 且实际执行后，才能报告结果。不存在则标记 `NOT IMPLEMENTED`，按当前阶段需要建立最小实现。
- M0 建最小运行/模型冒烟和评测脚本基础；OpenAPI、SSE 快照随真实接口和事件逐步建立，不要求 M0 为未来接口提前写全量测试。
- 评测 JSONL Schema 从第一天冻结：必需字段 `question`、`expected_chunk_ids`、`answer_points`、`kb_scope`；建议附加稳定的 `expected_sources`、`dataset_version`。变更 Schema 需版本化转换，不得静默破坏历史结果。
- 初期用小型真实数据集逐步成长；同时保留固定核心样本和增量难例；检索报告记录语料/切块/Embedding/Prompt/模型版本，不能仅凭一个总分做重构决定。
- 数据库结构使用迁移与必要快照；接口以实际 OpenAPI/DTO 为准；SSE 用固定事件样例检查；跨库、外发、引用以及成本边界保持可执行的不变量测试。
- 任何新依赖先验证包名、锁定版本，并完成安装/import 或对应构建冒烟；不能把猜测中的包、参数或工具作为事实。

## 6. 验收、Tag 和回滚

- 每个 `verify-mX` 必须在存在并执行后满足真实退出条件；失败或未运行不能标为通过。
- 里程碑通过、工作区变更已提交、关键验收证据已保存、P0/P1 已关闭，且项目负责人确认后，才建立相应 Git tag（建议 `v0.x-mX`，实际命名写入 ADR/发布记录）。
- Tag 之外还要记录 commit SHA、迁移版本、评测数据集版本和模型/索引配置；有数据变更时保留备份与恢复说明。
- 不为单个 P2 阻断后续阶段，但不得绕过负责人的延期确认。

## 7. 每次交付必须给出可核查报告

按以下固定格式简洁汇报：

1. **目标与改动**：当前任务、实际修改文件、功能状态。
2. **执行命令**：原样列出命令、退出码、关键输出或报告路径；没有运行必须写 `NOT RUN`。
3. **结果**：`PASS / FAIL / NOT RUN / NOT IMPLEMENTED / BLOCKED`，模拟测试与真实服务分开标注。
4. **风险与遗留**：复现条件、影响、建议分级、绕过方式、是否需要负责人批准延期。
5. **版本与下一步**：commit SHA（若有）、验收状态、下一个最小任务；不得在未经确认时自称已验收或打 Tag。

**“通过”的证据标准：命令是什么？退出码是什么？关键输出在哪里？** 不得用看似完成的文字代替真实运行。


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
