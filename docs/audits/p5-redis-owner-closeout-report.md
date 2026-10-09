# P5.5-A Owner 最小收口修复

任务：`p5-redis-owner-closeout-r1`，2026-10-09（Asia/Shanghai）。

交付状态：**NEEDS_OWNER_REVIEW**。本轮必要后端、隔离集成及客户端离线检查为 CHECKS_PASSED；未授予完整 P5 验收或生产启用。浏览器测试 NOT RUN，原两项间歇失败的根因仍 UNKNOWN。真实商业 API 请求为 0；没有提交、push、部署、Tag 或业务库迁移。

## 1. 基线与范围

- HEAD：`e6cca0971d0f1875ca562a6b953fd5ec252b28e0`；分支 `codex/local-first-rag-v1-20260930`。
- 实际工作目录：`E:\RAG quention`。开始 40 个 dirty 路径全部与 Owner 已审 manifest 逐字节匹配，已保存独立 before 快照。没有 reset、stash、clean、暂存或覆盖旧报告。
- 开始快照：`var/reports/p5-redis-owner-closeout-r1/start-snapshot.json`。最终清单：同目录 `final-manifest.json`；精确本轮增量：`task-only.diff`；相对 HEAD 的追踪文件 diff：`relative-to-head.diff`。完整未追踪文件内容也列入本轮 diff/文件哈希，不将 git diff 当作包含所有新文件。
- 本轮 12 个项目文件有变化，最终 42 个 dirty 路径。原 40 路径全部保留，30 个未改；其余 10 个只做获准增量。另修改原干净 client.ts、新增本报告。
- client.ts 的 before 复用上一轮 before 原始字节（4232 字节），与 HEAD blob（4180 字节）仅 CRLF/LF 不同，换行规范化后文本一致；开始 Git 状态为干净。明确区别于本轮独立保存的 40 份 dirty 原始字节，不声称本轮修改前另行捕获了它的工作树哈希。两种原始哈希均记录在 provenance。
- 原 3242 个已枚举证据文件逐字节保护核验。本轮 attempt 均追加到新名称目录；没有覆写旧 JUnit、失败、收据或 UNKNOWN。
- `progress.md` 的旧 main/里程碑描述与现阶段授权不一致，以本次 Owner 固定分支和任务为准，不改该历史文件。适用 AGENTS、ADR 索引、P5 设计与上一轮报告已读取。
- 应用 efficient-goal-execution 技能；未派发 Worker，单一写者。本轮 MANUALLY_SUPERVISED_TRIAL，actual serving model/effort UNKNOWN。

## 2. 修改文件与实现证据

| 文件/符号 | 本轮最小改动 |
|---|---|
| `backend/app/adapters/postgres/run_lifecycle.py::fail_run`（128） | 锁 Run → lease、核对 owner，在同一 PG 事务内提交 FAILED/error/终态事件/lease，并只把未结 IN_PROGRESS Attempt 保守转 UNKNOWN |
| 同文件 `read_run`（147） | 使用已提交 answer.completed.citations 的原顺序；逐项匹配同 Run EvidenceSnapshot、精确 chunk/version、quote hash 和原文可读性 |
| `backend/app/ports/run_lifecycle.py` | 声明 fail_run 合同 |
| `backend/app/application/run_lifecycle.py::executing`（73） | 未处理异常触发上述原子终态；仍传播异常；终态缓存补写和原 CAS 清理，不重发或退款 |
| 同文件 `read_committed_events`（7） | 共享 PG 权威回读；终态提交位于两次读取之间时二次读取，过滤未形成权威终态的终态事件；Redis 不可用时 PG 恢复 |
| `backend/app/adapters/postgres/knowledge_repository.py::_publish_committed_events`（58） | append/finalize/cancel 的 PG 事务成功退出后才调用 cache sink；缓存异常不改变提交、预算或模型状态 |
| 同文件 `read_committed_run`（1408） | 未配置 Redis 也可只读恢复 PG 中既有 Run，不进入生成路径 |
| `backend/app/bootstrap.py` | composition root 绑定 store 与 RunLifecycle 的提交后事件 sink |
| `backend/app/api/routes.py::run_events` | 使用相同会话/Scope/Run 校验；无 Redis coordinator 时仍支持只读 PG SSE |
| `backend/app/adapters/redis_stream.py::append_event`（71） | Lua 原子比较 seq 对应的事件、拒绝冲突、幂等重写相同事件；同时限制 UTF-8 单事件大小、Run 数量及总字节 |
| `frontend/src/api/sse.ts::readRunEvents`（37） | AbortSignal，已知 ID 首次 claim 竞态的有界 GET 等待，保留 Last-Event-ID 与最多 3 次流连接尝试；无重复 POST |
| `frontend/src/api/client.ts::sendMessageWithEvents`（57） | 显式创建一个 Request ID；一个同步 POST 与同 ID SSE 并行；POST 响应丢失时只读取已提交终态；不重发生成 |
| `frontend/src/app/App.tsx::send`（691） | 真实 UI 调用上述 helper，POST 未完成时可显示阶段通知；最终 transcript 只接收 POST 或已提交终态结果 |
| `backend/tests/test_p55a_lifecycle_integration.py` | 新增异常/提交与回滚/顺序/无 Redis/容量/实时 POST 与恢复反例，保留原全部断言 |
| 本报告 | 新建，不覆盖上一轮审计报告 |

上述表把同一文件的多个符号拆行，唯一项目文件数为 12；完整清单、字节及 SHA-256 以 final-manifest 为准。

## 3. Run 异常终态

修复前实际反例：`owner-closeout-before` 为 1 FAIL /23 DESELECTED，退出 1；observed-exception-path.json 记录 `rag_runs.status=running`、lease UNKNOWN。错误发生在 `_answer` 异常传播后的 executing/finally 路径。

修复后逻辑 Run 为 `failed/RUN_EXECUTION_FAILED`，lease FAILED；可能已发送的 Attempt 为 UNKNOWN，预算行及其 UNKNOWN 占用逐列不变。重复请求读取持久失败结果，不再进入 `_answer`。没有异常正文持久化或对外回显，没有退款、自动重发或删除证据。

owner 不符时拒绝；取消/完成先提交时不覆写既有终态。PG 写失败时仍明确拒绝，不能声称安全终态已保存。本轮没有模拟供应商 Exactly Once 保证。

本轮针对能被 executing 捕获的未处理异常。原 abrupt process-crash/租约过期路径仍按既有 UNKNOWN 防重复合同处理；其 Run 可能保持 running，不能据此声称已经实现后台崩溃对账或自动完成历史清理。该路径未扩张修复。

## 4. 提交后缓存、实时 SSE 与恢复

- 原始阶段事件 append 的 SQL transaction 成功退出后才写 Redis。最终 answer/evidence/message/status 在原 finalizer 同事务提交，随后才发布其事件。取消也是 SQL 提交后发布。
- 真实隔离测试用独立 PG 连接在 sink 中验证事件已可读取；遇 answer.completed 还验证 Run completed 和 assistant message 可读取。另一测试在最终 UPDATE 注入异常，确认实际事务回滚后没有成功事件、EvidenceSnapshot、assistant message 或成功缓存。
- 应用正常同步 POST，Request ID 同 Run ID；SSE 是同时进行的独立 GET，没有新增 Job Queue。首次 claim 尚未提交的 404 最多等待 10 秒、间隔 250ms；此后已有流仍采用原最多 3 次有序重连。Abort 终止无用订阅。
- 实际 ASGI POST 被模拟 Provider 挂起时，真实 Redis 已存在已提交 retrieval.started；独立 ASGI SSE 已返回阶段事件，POST 仍 pending。断线后 Last-Event-ID 回读最终文本，模拟发送数仍 1。
- 实际 TypeScript helper 离线测试确认 POST pending 时处理阶段通知，以及 POST 响应丢失后只靠 GET 读取已提交结果，没有第二次 POST。非终态候选不能恢复成最终回答。App 只显示阶段通知，不把未校验候选加入消息。
- Redis 缓存到期、连接异常、写入失败/超限或 coordinator 未配置时，SSE 与结果恢复仍读取 PG；Run 及 Scope 不正确则拒绝。没有生成降级。
- 终态 SQL 若在“先读状态、再读事件”中间提交，观察终态事件后重新读取权威结果，避免空文本终态使客户端提前结束。该竞争注入明确为 SIMULATED，底层 PG/Redis 实际使用隔离实例。

## 5. Citation 原顺序及完整性

读取提交的 answer.completed.citations，不再 ORDER BY label 排序。E2→E1 测试同时验证正常生成、重复 Request 的结果、SSE、Redis 不可用及未配置情况下的 PG 回读，顺序都为 `E2,E1`。E2/E1 各自回读到对应真实合成 chunk/version/quote；篡改本轮新建合成 Snapshot 的 hash 后读取明确拒绝。没有修改旧快照、原 Citation/数值/最终提交保护。

首个 fixture 问题包含“成本各”，未命中现有离线匹配器，保留 NO_CANDIDATES 失败。改为正常匹配表达后，一轮通过；后续完整回归遇等分候选的随机 UUID 导致标签分配变动，保留 UNSUPPORTED_ANSWER 失败。源码 `_rank_descending` 按 score 后 chunk UUID 排序。最终只固定合成 UUID 的相对顺序，并显式断言真实候选排序，不调整 P4 检索或 Validator，不搜索参数以通过。

## 6. Redis 缓存边界

默认单事件 262144 UTF-8 字节、单 Run 1024 事件、单 Run 序列化事件总量 4194304 字节（不等于 Redis allocator 实际内存字节）。这些是可丢弃缓存的工程上限，不是模型容量/Token 数或业务证据限制。没有改模型/外发预算参数。

同一 seq 已有相同事件则幂等并续期；不同事件返回固定 `STREAM_EVENT_CONFLICT` 且不插入。数量/字节超限返回 `STREAM_CACHE_LIMIT`，不修改 PG 权威记录。真实并发测试确认两个不同事件争用同 seq 时仅一个成功，缓存仅一项。单事件、事件数、总字节均有实际反例与 PG 回读保全断言。首轮总字节 fixture 设为 80、两个事件实际仅 70 字节，未触发预期拒绝；修正为 60，失败记录保留。

Lua 操作基于原有 ZSET；在单次原子写内核对 seq、数量和总字节，不增加永久缓存索引或清除历史数据。

## 7. 执行命令、退出码与结果

所有 Python 命令采用项目 `.venv/Scripts/python.exe -B -X utf8`。必要命令经正常工具审批执行，没有更改 ACL/沙盒、绕过拒绝、接触真实凭据或业务库。**未实现的命令不得报告通过。**

| 原样命令（工作目录 E:\RAG quention，除 tsc） | 退出码 /结果 |
|---|---|
| `.venv/Scripts/python.exe -B -X utf8 var/reports/p5-redis-owner-closeout-r1/start.py` | 0；40 路径交接一致、独立快照、323 Python 与3242证据哈希 |
| `.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55a_isolated.py owner-closeout-before -k owner_unhandled_exception` | 1；1 FAIL、23 DESELECTED；修复前原始终态反例 |
| `.venv/Scripts/python.exe -B -X utf8 var/reports/p5-redis-owner-closeout-r1/legacy_sequence.py` | 0；按原顺序 2 PASS；根因 UNKNOWN |
| `.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55a_isolated.py owner-closeout-fixes-1 -k owner` | 1；9 PASS、2 FAIL、22 DESELECTED；fixture 问题原始证据 |
| `.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55a_isolated.py owner-closeout-final` | 0；33 PASS；未覆盖其后新增的终态竞争代码，不作最终版本验收 |
| `.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55a_isolated.py owner-closeout-final2` | 1；33 PASS、1 FAIL；随机 UUID 标签顺序反例失败 |
| `.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55a_isolated.py owner-closeout-final3` | **0；34 PASS、0 FAIL/ERROR/SKIP**；最终完整隔离回归，原 22 项全部执行 |
| `../frontend/node_modules/.bin/tsc.cmd --noEmit`（cwd frontend） | 0；TypeScript |
| `node --test --test-reporter=junit var/reports/p5-redis-owner-closeout-r1/sse-node.test.mjs` | 0；7 PASS；实际 TS、模拟 fetch；最终重复一次绑定 SHA |
| `git diff --check` | 0；默认检查，不忽略空白、不改属性 |

必要关联离线原样命令：

```powershell
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py p55a-owner-closeout-targeted backend/tests/test_p5_router_generation.py backend/tests/test_p5_router_boundaries.py backend/tests/test_p5_router_features.py backend/tests/test_p5_router_replay.py backend/tests/test_pretransport_budget.py backend/tests/test_validation_usd_budget.py backend/tests/test_product_receipt_repair.py backend/tests/test_reviewed_product_request.py backend/tests/test_final_answer_commit.py backend/tests/test_cancellation_boundaries.py backend/tests/test_citation_resolution.py backend/tests/test_smart_citation_replay.py backend/tests/test_bootstrap.py
```

退出 0：**323 PASS、0 FAIL/ERROR/SKIP**。受保护入口 network/secret/subprocess/database 拒绝计数都为 0；不涉及真实服务。该轮之后仅完善隔离测试的 UUID fixture，生产源码与该轮 snapshot 全部一致；没有无变化地重跑这 323 项。

最终完整隔离回归 kind 为 REAL_ISOLATED_PG_REDIS_SIMULATED_PROVIDERS，实际 argv、pytest_exit_code、elapsed、source-before/after 及 JUnit 在 `var/reports/p5-redis-lifecycle-r1/owner-closeout-final3/`，`source_equal=true`。34 项组成：原 22 项、上一轮后补预算角色 1 项、本轮新增 11 项（包括参数展开）。不同轮次不累加成唯一覆盖数。

原两项 Gate/预算 JUnit traceback 已单独保存到 `original-gate-budget-failures.json`：一项模拟 request=0，一项 SESSION_LEDGER_UNAVAILABLE。原日志没有 underlying errno/数字 OS 状态，未保存独立 stdout 文件。本次一次限定顺序复现为 2 PASS，安全异常采样为空，不能确定旧失败根因，也不能将旧失败改标 PASS。323 项最终关联回归也执行了这两个原断言。

报告整理的一次非测试静态比较因绝对/相对路径字符串不同而退出 1；绝对路径正规化后确认原三项分层违规完全一致。一次 closeout 脚本因把 HEAD 的 LF blob 与旧 before 的 CRLF 原始字节直接比较而退出 1；只读核实差异仅 52 个 CR 后，复用旧原始 before 并记录两份字节哈希与来源。第二次 closeout 因证据脚本未加入项目 import root 而退出 1，修正了该脚本的模块定位；此前已生成的客户端 JUnit 原样保留，后续输出使用独立文件。这些不是项目测试失败，没有修改生产源、测试断言、全局 Git 换行配置或历史故障；辅助失败和修正原因保存在 closeout evidence。未重跑全历史分层/PDF/Caption 门禁。

## 8. 隔离环境与证据

复用上一轮任务专属隔离实例，无新建/重启/删除容器：PG 127.0.0.1:52352，数据库 p55a_lifecycle_test，OID 16384，system identifier 7694635764170571814；Redis 127.0.0.1:52355。每轮 runner 现场核对原固定身份及 `0018_run_attempt_lifecycle`；本轮没有运行 Migration。

商业 API 请求 0。Provider 传输模拟，PG/Redis 是真实隔离服务；业务/现有 Clean-slate、原全局账本及凭据未读取或修改。模拟预算的 UNKNOWN 行被保留，不能称为真实供应商账单验证。

本轮证据目录 `var/reports/p5-redis-owner-closeout-r1/`：

- start-snapshot/before：原 40 文件及开始证据哈希；
- task-only.diff、relative-to-head.diff、git-checks.json：真实增量与 Git；
- final-manifest、final-tested-source-snapshot：所有 dirty 文件 SHA、保护文件及测试源码绑定；
- test-results：所有本轮 PASS/FAIL/ERROR/SKIP、原命令、退出码及 JUnit SHA；
- junit.xml：最终34项 JUnit 的原字节副本；offline-junit.xml：323项副本；
- sse-node-final-junit.xml、sse-node-bound-junit.xml、frontend-execution.json：客户端各轮与最终 SHA 绑定、TS/浏览器边界；
- artifact-manifest：证据文件长度与 SHA，不覆写旧 evidence。

## 9. 遗留、回滚及停止

1. Playwright 默认浏览器不存在，**浏览器 NOT RUN**。只查询默认可执行路径存在性，未下载、换浏览器、启动前端服务器或用 Node/ASGI 冒充浏览器验收。真实 UI 浏览器验证仍需独立执行条件和 Owner 复核。
2. 原两项间歇 Gate/预算失败根因 UNKNOWN，旧失败保留。三处分层违规与上一轮完全一致，本轮未扩张修复。
3. 原 process-crash UNKNOWN 对账、生产 Redis/0018 部署、真实模型权限/上下文容量/用量与费用验证仍未授权；没有修改 RuleRouter 贡献值/.35、P4 检索/重排/父块/Context、原 Gate、Validator、FinalAnswerCommitCheck 或模型配置。
4. 不恢复旧进程历史集合/4096/8192 机制。生产保持 OFF/BLOCKED_REAL。若本次增量需回退，由 Owner 按本轮精确 diff、before 和哈希审核后选择性恢复；不能 reset 整个 dirty 工作树或降级/删除历史数据库。
5. 任务完成后停止，仅提交报告和证据供 Owner 复核；不创建 commit/push/Tag，不自动进入 P5.5-B。

## 10. P5.5-B 上下文与记忆接口交接

继续保留 PG 的 conversation_messages 完整 User/Assistant/Run 关联、rag_runs/retrieval_events、EvidenceSnapshot 与 Citation 的历史回读。缓存 seq、LiveRun owner/lease、Attempt 的 Run/role/ordinal/envelope、预算 reservation 与 provider/model/Token/费用状态不可混用或丢失。

`read_committed_run`、现有历史消息接口、已提交事件与 Snapshot 回读可供后续按会话装配上下文。Cheap/Expensive 独立角色和容量/用量/成本记录未合并。Redis 仍只负责活动标记和短期事件，不是会话记忆、ContextCheckpoint 或长期用户记忆的唯一存储。

短期多轮上下文、Smart 历史压缩、持久 ContextCheckpoint、真实模型 Token 窗口及跨会话长期记忆仍为缺口。本轮未实现 Query Understand/Compaction/长期记忆，也未为这些功能增加测试范围或新表。
