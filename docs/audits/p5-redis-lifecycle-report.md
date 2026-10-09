# P5.5-A Redis 与持久化幂等替换报告

任务：`p5-redis-lifecycle-r1`。交付状态：**NEEDS_OWNER_REVIEW**。

限定功能的真实隔离集成门禁通过；不表示完整 P5 验收、真实模型授权或生产启用。关联回归保留三项首轮失败，两项限定复查通过但原因未确认，另有既有分层失败。未创建 commit、push、Tag 或部署。真实商业模型/API 请求为 **0**。

## 1. 基线、范围和历史保护

- HEAD：`e6cca0971d0f1875ca562a6b953fd5ec252b28e0`；分支：`codex/local-first-rag-v1-20260930`。
- 起始 23 个 dirty 路径已逐字节快照，保存在 `var/reports/p5-redis-lifecycle-r1/start-snapshot.json` 和 `before/`。本轮不 reset、stash、clean、暂存或覆盖旧报告。
- 修改前后字节、任务增量 diff、相对 HEAD diff、实际 Git 状态及所有交付文件 SHA-256：同目录 `final-manifest.json`、`task-only.diff`、`relative-to-head.diff`、`git-checks.json`。
- App.tsx 与前端 SSE 测试的 before 来自已核对 HEAD（开始 Git 状态干净），不是独立保存的初始工作树原始字节；provenance 明确区分。增量文本 diff 规范化换行供阅读，SHA 始终按原始字节。
- RuleRouter 特征/贡献值/0.35、AnswerHardening、R2/R2.1 Gate、原两份收据测试、replay fixture/脚本与此前审计报告按开始哈希核对未变。既有 42 份交接 artifact 单独核验；历史 Migration 0001–0017 未改。
- 全局 DeepSeek 真实账本、凭据、旧业务库未读取或操作。不能据此宣称取得了它们的前后数据库计数或凭据哈希。
- 请求模型身份/实际 serving identity 未由控制面证实，记为 UNKNOWN；没有代理递归派发。本轮为 MANUALLY_SUPERVISED_TRIAL。

## 2. 实际修改与删除

本轮改动 24 个项目文件，包括本报告；其中 7 个为开始已 dirty 的文件，另外 17 个为新增或此前干净的文件。最终工作树 40 个 dirty 路径，完整清单以 final-manifest 为准。

| 文件 | 改动 |
|---|---|
| `alembic/versions/0018_run_attempt_lifecycle.py` | 仅新增 Run lease、Attempt 表和约束；有历史时拒绝 downgrade |
| `backend/app/ports/run_lifecycle.py` | Run/Attempt 端口、固定错误、当前执行 ContextVar；没有历史身份集合 |
| `backend/app/ports/stream_manager.py` | LiveRun/CAS/事件回读接口 |
| `backend/app/adapters/postgres/run_lifecycle.py` | 原子 Request claim、角色/序号唯一 Attempt、租约/owner fencing、历史结果及 Scope 回读 |
| `backend/app/adapters/redis_stream.py` | SET NX、精确 CAS、续期、序号缓存、TTL、无连接重试 |
| `backend/app/application/run_lifecycle.py` | 当前 Run heartbeat、终态清理/恢复、PG 权威事件与缓存接线 |
| `backend/app/application/answer_service.py` | 正常入口 claim/执行/恢复结果，保留原历史、取消、最终答案校验 |
| `backend/app/application/quick_chain.py` | 持久化 Attempt 准入/观察结算，删除旧历史集合与累计容量分支 |
| `backend/app/application/provider_usage.py` | 使用当前 Run 关联正常预算行，将预占 ID 关联 Attempt；不变更计价/UNKNOWN 保护 |
| `backend/app/adapters/postgres/knowledge_repository.py` | 原最终 SQL 事务锁内增加生命周期 fencing；保留原 Evidence/quote 校验 |
| `backend/app/bootstrap.py`、`backend/app/config.py` | 正常 composition root 接线、可选 Redis URL 与 TTL；无 Redis 时动态生成拒绝 |
| `frontend/src/api/sse.ts` | 会话绑定、Last-Event-ID 去重、body reader 断线重连 |
| `frontend/src/app/App.tsx` | 正常发送成功后消费独立 Run 事件，不展示未校验候选 |
| `frontend/tests/sse.spec.ts` | 保留原序号/断线断言，新增会话 URL，所有 /api 请求离线拦截 |
| `pyproject.toml` | 固定 `redis==5.2.1` |
| `backend/tests/test_p55a_lifecycle_integration.py` | 真实隔离 PG/Redis，模型传输全部 SIMULATED |
| `backend/tests/p55a_simulated_lifecycle.py` | 明确 SIMULATED 的 SQLite 单元夹具，仅在 tests，不是生产降级 |
| `backend/tests/test_p5_router_generation.py` | 单元夹具接线，原安全断言保留 |
| `backend/tests/test_p5_router_boundaries.py` | 容量策略测试替换为持久化防重/无历史上限/无生命周期零发送 |
| `scripts/verify_p55a_isolated.py` | 固定目标、隔离身份、网络/凭据/子进程护栏及可复核 JUnit/source snapshot |
| `docs/design/rule-router-v1.md` | 更新被替换的历史集合说明，保留真实角色启用阻断 |

删除的是旧 QuickChain `_router_runs`、`_router_attempts`、`_router_run_capacity`、`_router_lock`、4096/8192 累计容量参数/分支和仅属于旧容量策略的断言；没有删除原始业务数据或历史证据。不是两套生产幂等。单元 SQLite 夹具不在 production import graph。

## 3. 固定 WeKnora 源码依据

固定 commit `3e8b0bfc80b845b2d4b2ed683994748741450a97`，tree `9533ab2071e71f4bc2ebb09c85d3ac246841ff9a`。

| 源码 | 已核对 Git Blob SHA |
|---|---|
| `internal/types/interfaces/stream_manager.go` | `aa5f69512229e71b28599a85b8567e8a9fdeaa80` |
| `internal/stream/redis_manager.go` | `4e0191ac4a7479b439046df744f0983a26c0d5e7` |
| `internal/stream/memory_manager.go` | `3f1be193d929eac04e1b50ecd9eb761dbcc7244a` |
| `internal/handler/session/qa.go` | `46b7a2aa808545afa0f765ce61742a9c49aaf8e9` |
| `internal/handler/session/stream.go` | `734d618316f1cd71fd5fecb0a89cbf324ff5fdf3` |

本机 E:\WeKnora 读取被拒后未重试该路径或改权限；使用已授权 GitHub 连接读取固定文件和提交 tree，逐字节计算 Git blob，五份均匹配。公开下载和 wheel 均最终保存在 `D:\RAG-task-downloads\p5-redis-lifecycle-r1\`；provenance 与 SHA-256 在 `upstream-provenance.json`。GitHub connector 一次传输失败、一次超长命令/UTF-8 读取错误均不作为源码已核实的依据。

借鉴固定版本 append-only 回放、Session LiveRun、终态清理。`redis_manager.go::SetLiveRun`（383）、`GetLiveRun`（433）、`touchLiveRun`（456）、`ClearLiveRun`（478）与 `stream.go` 的 Session/Message 校验和 GetEvents（67–116）已读取。项目使用 PG 序号而非上游 list offset，Redis ZSET 缓存代替 RPUSH list；续期/清理比较完整 Run+owner，未采用无 owner 的 touch 或覆盖式 ClaimLiveRun。没有实现 steer、Compaction 或上游当前版本新能力。

## 4. PostgreSQL 状态机与约束

复用 `rag_runs.id`（Request UUID）与原 Answer/Evidence/事件/消息表。`claim_run` 将 conversation、精确 KB/document 数组、q0、mode 的 canonical SHA-256 绑定为 `request_hash`，INSERT ON CONFLICT 后锁定同一 Run。

- 首次登记：`IN_PROGRESS` + UUID owner + lease_until。
- 同身份相同正文/Scope/mode：只读取持久状态/最终结果；没有重新生成入口。
- 内容、Scope 或 mode 冲突：`REQUEST_ID_CONFLICT`。
- 过期仍 IN_PROGRESS：转为 UNKNOWN，仍在处理的 Attempt 同步 UNKNOWN，不当作 NOT_SENT。
- 终态由原 `rag_runs.status` 决定：COMPLETED/FAILED/CANCELLED；SQL 提交成功后崩溃也可从持久终态恢复。
- 可靠 Redis 准入拒绝且未进入生成：lease 保存 NOT_SENT，原 rag_runs 保存 FAILED/error 与终态事件，避免给后续历史读取留下永久 running 屏障。当前实现不自动重新准入或重放已消费 Run，保守读取既有终态。

`rag_run_leases.run_id` 为 PK/FK；`rag_model_attempts.id` 是稳定 `request_identity(run_id, quick.router.role, ordinal)`。新增 `UNIQUE(run_id,role)`、`UNIQUE(run_id,ordinal)`，限制 Cheap/Expensive、序号 1/2、第二次仅 Expensive。每个角色最多一次。

Attempt 发送路径前登记 IN_PROGRESS，随后仍必须走原角色、ModelAccess 和正常预算门。预算 ID 关联到 Attempt diagnostics，model_calls 带 Run FK。状态区分：可靠未发送 NOT_SENT，已收到结果并完成观察记录 COMPLETED，不确定发送/持久化/结算 UNKNOWN。COMPLETED 是模型 Attempt 完成，不等于候选通过、最终回答成功或实际费用已结算；各项原始状态分开记录。

最终 SQL commit 继续在 Run 终态锁内执行原 FinalAnswerCommitCheck、版本/quote 回读，并核对 owner、有效租约和生命周期状态。锁顺序保持 Run → lease。过期/未知旧执行者不能迟到提交。取消与原终态状态竞争仍受原 Run 锁保护。

0018 只在本轮新建隔离实例执行。**当前业务/现有 Clean-slate 库的 migration 版本未查询、更未修改，不推定已经是 0018。** 真实约束定义见 `isolated-closeout.json`。

## 5. Redis Key、TTL、CAS 与 SSE

- Key：`rag:stream:v1:session:<sha256(session)>:live`；值含 Run UUID、owner UUID。
- 事件：同 Session prefix 的 `events:<run_id>`。与活动 key 生命周期独立。
- 默认 LiveRun/PG lease 60 秒，事件 86400 秒，可通过 `RAG_LIVE_RUN_TTL_SECONDS`、`RAG_STREAM_EVENT_TTL_SECONDS` 配置；测试使用 3 秒/1 秒以实际验证到期。
- 正常 `RAG_REDIS_URL` 配置来自项目进程环境；未新增 .env.local 白名单、不读取真实 Key、不修改 Windows 全局变量。
- SET NX 拒绝同 Session 冲突；不同 Session 合法并发。续期/删除使用 Lua 完整值相等 CAS，不会删除新 owner。
- redis-py 固定 5.2.1；connect/read timeout 2 秒，Retry(NoBackoff,0)，不自动重试。
- 当前 Run 才持有 heartbeat 线程/停止信号；终态后退出，不保留历史 Run/Attempt Python 集合。
- 先保存 PG 终态，再清理 Redis；异常留下 cleanup_pending。`cleanup_terminal` 在重复终态读取时可恢复清理，CAS 不影响其他 Run。没有新增后台历史清理器。

真实 SSE API 为 `/api/v1/runs/{run_id}/events?conversation_id=...`，校验 UUID、位置以及服务器读取的 Conversation/current KB-document Scope。按 PG 权威事件 seq 回放后继续等待新增事件，不调用生成。Redis 缓存仅在与 PG 权威投影一致时消费；缓存缺失、损坏、过期或 Redis 故障均由 PG 回读覆盖。终态帧仅附上已提交最终 Answer/Citation；不发送被拒的 Cheap 候选。

前端使用固定 Session/Run 和 Last-Event-ID；丢弃已显示 seq；EOF 与 reader 异常都重连既有 Run，最多三个 stream GET attempt，不重发 POST。当前问答 POST 仍是原同步完成协议，工作台在成功响应后消费该 Run 事件；本轮没有实现新的异步启动协议。浏览器刷新后完整历史仍从原 PG messages 读取。

## 6. 真实隔离环境与执行证据

Docker Engine 29.8.0；仅创建本任务两个新容器和一个新卷，没有 prune、停止/重建其他服务。

| 对象 | 身份 |
|---|---|
| PostgreSQL | `p55a-lifecycle-r1-pg`，container `4db80024010c3c48b68684bb1ff28e471f5a9e72fd90f7b460a54091e425718f` |
| PG 镜像 | `sha256:ccc6e83d6e35e931dc7c5def2022729d5a6c370318d099181995567ff1fb4d6b` |
| 新库 | `127.0.0.1:52352/p55a_lifecycle_test`，OID 16384，system identifier `7694635764170571814` |
| Redis | `p55a-lifecycle-r1-redis`，container `cdb838c5c05f0d5fa1cf3c5a290078c33dbd90a84983deffc8e3146c6b95e4bf`，loopback 52355 |
| Redis 镜像 | `sha256:c9d92d840fd011c908f040592857c724ae6d877f2aba5c40ad963276507386b2` |
| 测试卷 | `p55a-lifecycle-r1-pgdata`，task label `personal-rag.task=p5-redis-lifecycle-r1` |

测试账号/口令是本轮创建的 SIMULATED_TEST_ONLY 值，不是业务凭据。保留隔离资源/合成数据供复核，尚未清理。最终合成计数：174 Run/lease、162 Attempt（150 COMPLETED、1 NOT_SENT、11 UNKNOWN）、293 messages、919 events、135 Evidence、161 budget rows、156 SIMULATED sends；这些不是商业 API 请求。14 cleanup_pending 包括此前轮次注入的崩溃/未发送/清理失败状态，保留，不抹平。

隔离 runner 只允许确切 PG/Redis loopback 端口、实际 DB 身份和同一受保护 worker 子进程。libpq 连接另外校验 host/port/database/user；拒绝凭据文件与真实 canonical ledger。Windows asyncio 的标准库 socketpair 只允许该 stdlib frame 创建的精确 listener 自连接，未开放其他 localhost 服务端口。护栏触发与模拟/真实分别记录。

| 轮次 | 实际结果 | 退出码 |
|---|---|---:|
| Redis wheel 首次安装 | WinError 650，不能算完整安装通过；后经正常审批固定 wheel 重装/import 5.2.1 PASS | 子命令数值退出码未单独保存 UNKNOWN；链末尾 0 |
| 固定 wheel 重装 + import | PASS，wheel SHA `ee7e1056b9aea0f04c6c2ed59452947f34c4940ee025f5dd83e6a6418b6989e4` | 0 |
| 隔离迁移到 0018 | 27 张 public 表；真实 revision/约束核对 | 0 |
| integration-d1 | 17 PASS /3 FAIL /0 ERROR /0 SKIP | 1 |
| integration-d2 | 20 PASS /0 FAIL，旧集合仍保留 | 0 |
| integration-e-final | 20 PASS /0 FAIL，删除旧集合后强制检查 | 0 |
| integration-e-boundary-final | **22 PASS /0 FAIL /0 ERROR /0 SKIP**，额外 PG 异常和迟到 SQL 提交 | 0 |
| final-fencing-exact | 仅强化的迟到提交断言复验 **1 PASS /21 DESELECTED**，合法旧 Context+owner 明确得到 RUN_LEASE_LOST；生产代码未变 | 0 |
| final-budget-role-boundary | **4 PASS /19 DESELECTED**；正常完成、Cheap/Expensive、超时 UNKNOWN、P4 Rerank 独立预算准入，真实隔离 PG/Redis | 0 |
| p55a-role-budget-final | **135 PASS /0 FAIL /0 ERROR /0 SKIP**；预算最小修正后受影响 P5/Provider 合同 | 0 |
| final-admission-terminal | **7 PASS /16 DESELECTED**；准入拒绝、故障恢复、失败/取消、并发 Session；最终生命周期实现 | 0 |
| p55a-final-offline | 255 PASS /1 FAIL，分层检查 | 1 |
| p55a-associated-final | 289 PASS /2 FAIL /0 ERROR /0 SKIP | 1 |
| legacy-diagnostic-current | 原两项当前代码限定复查 2 PASS，不能改写先前失败 | 0 |
| legacy-diagnostic-baseline | 诊断入口缺开始快照文件，未收集测试；保留 ERROR 记录 | 4 |
| legacy-diagnostic-baseline-2 | 相关 QuickChain 开始快照 + 本轮未变 Gate，两项 2 PASS；不是全仓/P4 baseline | 0 |
| TypeScript `tsc --noEmit` | PASS（最终客户端源码） | 0 |
| Node actual TS SSE client | **4 PASS**，含异常断线和去重；全 fetch 模拟 | 0 |
| Playwright SSE | 首轮配置 cwd 错；修正后缺 Chromium，浏览器用例 NOT RUN（JUnit launch FAIL） | 1 /1 |
| `git diff --check` | PASS | 0 |

开始旧版本集合已保留到 integration-d2 实际退出 0 且 source_equal=true 后才删除。所有实际集成/离线 runner 记录 argv、JUnit、测试前后 SHA 和 source_equal=true。未实现的命令不得报告通过。不同轮次不可累加成不重复样本数。

16 项合同均有真实 PG/Redis 测试：创建/完成、失败与取消清理、相同请求并发单发送、正文/Scope/mode 冲突、Cheap→Expensive 各一次、超时 UNKNOWN、TTL 到期/崩溃 UNKNOWN、重启与两个服务进程、防 Redis 失联、终态提交后清理失败、CAS 迟到清理、SSE reconnect、事件 TTL 后 PG 最终回读、连续 24 完成 Run 不留 Python 历史集合、引用/版本/Scope/历史预算保护。Redis/PG 故障由明确测试注入，不等于实测生产 Redis 故障；基础客户端和状态存储是真实实例，不是 Mock Redis/PG。跨各轮保留的合成历史总量见上表，不称为生产压力测试。

原样命令（cwd 为本工作树；完整长参数与实际 argv 同时保存在 execution.json）：

```powershell
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-redis-lifecycle-r1/migrate_isolated.py
.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55a_isolated.py integration-d1
.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55a_isolated.py integration-d2
$env:P55A_REQUIRE_OLD_REMOVED='1'
.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55a_isolated.py integration-e-final
.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55a_isolated.py integration-e-boundary-final
.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55a_isolated.py final-fencing-exact -k crashed_attempt_expiry
.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55a_isolated.py final-budget-role-boundary -k 'rerank_budget or cheap_expensive or normal_completion or timeout_unknown'
.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55a_isolated.py final-admission-terminal -k 'redis_failure or redis_connection or failure_cancel or multiple_sessions or final_postgres'
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py p55a-role-budget-final backend/tests/test_p5_router_generation.py backend/tests/test_p5_router_boundaries.py backend/tests/test_model_provider_contracts.py
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py p55a-final-offline backend/tests/test_p5_router_features.py backend/tests/test_p5_router_generation.py backend/tests/test_p5_router_boundaries.py backend/tests/test_p5_router_replay.py backend/tests/test_product_receipt_repair.py backend/tests/test_reviewed_product_request.py backend/tests/test_answer_service.py backend/tests/test_layering_preview.py
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py p55a-associated-final backend/tests/test_product_wire_anchor.py backend/tests/test_reviewed_immutable_batch.py backend/tests/test_grant60.py backend/tests/test_deepseek_policy_boundary.py backend/tests/test_pretransport_budget.py backend/tests/test_validation_usd_budget.py backend/tests/test_final_answer_commit.py backend/tests/test_cancellation_boundaries.py backend/tests/test_answer_hardening.py backend/tests/test_answer_validation.py backend/tests/test_quick_chain_budget.py backend/tests/test_p4_rag_pipeline.py backend/tests/test_quick_evidence_flow.py backend/tests/test_conversation_scope.py backend/tests/test_citation_resolution.py backend/tests/test_smart_citation_replay.py backend/tests/test_bootstrap.py backend/tests/test_context_and_locators.py
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-redis-lifecycle-r1/diagnose_legacy.py current
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-redis-lifecycle-r1/diagnose_legacy.py baseline
node --test --test-reporter=junit var/reports/p5-redis-lifecycle-r1/sse-node.test.mjs > var/reports/p5-redis-lifecycle-r1/sse-node-final-junit.xml
# cwd frontend:
& ./node_modules/.bin/tsc.cmd --noEmit
& ./node_modules/.bin/playwright.cmd test --config=../var/reports/p5-redis-lifecycle-r1/playwright.config.cjs
# 最后只读收口:
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-redis-lifecycle-r1/isolated_closeout.py
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-redis-lifecycle-r1/closeout.py
```

## 7. 失败、限制与生产阻断

- d1 三项原因有独立原始证据：asyncio 内部 socketpair 被 runner 拒绝、UUID 字符串比较错误、SIMULATED Provider 实例请求上限。只校正测试条件，没有改费用 Gate、Citation 或模型名单。d2 修正一次通过；未丢弃 d1。
- 收口静态核查发现新预算关联若错误要求所有 role 都有 Chat Attempt，会影响 P4 Rerank。最终仅 Cheap/Expensive 绑定 Chat Attempt，其他角色保留原正常预算准入；lost context 仍拒绝。新增真实 PG 反例通过，重跑受影响四项集成及 135 项离线合同，不把原 22 项旧轮次当作这个修正后的完整重跑。
- 可靠准入拒绝新增 FAILED Run / NOT_SENT lease 的原子终态，避免影响未来历史屏障；只作用于本次 owner 的新 Run，不修改此前失败记录。重跑受影响七项真实隔离检查，原 model Attempt/预算 UNKNOWN 继续保留。
- `test_declared_layering_has_no_violations` 当前仍失败三处 adapter→application import；开始原字节同样存在，证据见 layering-baseline-comparison.json。本轮新增端口/adapter 无新增此类 import。不修 P4 旧分层问题。
- `test_pretransport_budget::test_success_settles_one_reservation` 与 `test_validation_usd_budget::test_truncated_response_even_with_usage_stays_reserved_upper_bound` 在关联轮失败，随后当前及相关开始代码各 2 PASS。原轮原始原因未保存到更细 errno，**根因 UNKNOWN，不能称为已确认历史失败或永久修复**。Gate/原断言字节未改；需 Owner 审查此不稳定证据，正式启用前解决或明确批准处理方式。
- Playwright 浏览器 runtime 缺失，未下载或切换浏览器逃避执行限制。Node 使用实际 TS 客户端的四项验证与真实 ASGI SSE 测试均 PASS，但不冒充完整浏览器页面 E2E。
- 实际业务/Clean-slate 0018 部署、生产 Redis 连接/持久化/隔离运维、真实模型授权/独立容量/可信 Token 与账单端到端验证仍未执行。Default OFF/BLOCKED_REAL 仍保留。
- Redis 客户端/缓存可失效，PG 失败必须停止准入/最终提交；预算仍保守占用，不做未知退款。既有 CloudAdapter 计数/receipt 生命周期不是本轮历史集合，不擅自放大生产请求上限或清理收据。
- 不声称供应商 Exactly Once，不声称评分已校准或实际降本 10%。历史 native-runtime/PDF/Caption 缺口本轮 NOT RUN，未重新验证或抹平旧失败。

## 8. 回滚与审批

不自动启用 router；没有生产迁移可回滚。Owner 可审查 task-only.diff 与独立 before/ 对本轮文件作有界回退，必须保留开始 23 份 dirty 工作和全部证据，不可 git reset 全仓。0018 downgrade 仅在两张新表均为空时允许；有历史时拒绝，不能为回滚删除 Attempt/UNKNOWN。隔离资源保留供复核，清理须明确指定本任务对象，不使用 prune。

待 Owner 决定：代码/证据复核、两项不稳定旧 Gate 用例的后续处理、匹配浏览器 runtime 的测试安排，以及未来生产迁移/Redis/真实模型启用方案。本轮不提交、不 push、不部署、不进入后续功能。

## 9. P5.5-B 上下文与记忆接口交接

本节仅接口交接，不新增 Query Understand、Compaction、ContextCheckpoint 或长期记忆实现，不延长本轮测试范围。

| 可复用事实/接口 | 保留身份与边界 | 后续缺口 |
|---|---|---|
| `conversation_messages`、`list_messages(conversation_id)` | 完整 User/通过校验的 Assistant、conversation_id/run_id、时间；Redis 不代替历史 | 短期历史装配、模型窗口选择/压缩尚未实现 |
| `rag_runs`、`completed_history_context`、`last_completed_question_context` | q0、Session、精确 KB/document Scope、Run/终态/时间屏障 | 当前是有界历史 q0/follow-up 能力，不能冒充 Smart 历史 Compaction |
| `retrieval_events` / `list_events` / `run.metrics` | Run+seq+event、持久调用角色/Envelope/用量诊断 | ContextCheckpoint 的版本化保存与恢复入口需另行设计；本轮无该表 |
| `answer_evidence` / `get_citation` | Run、label、version_id、chunk_id、quote SHA、SourceLocator；保留版本回读 | 摘要/记忆引用必须继续绑定原始证据，不能把压缩文本当原文 |
| `rag_model_attempts` + `model_calls` + Gate/ProductReceipt | Request/Attempt role/ordinal、provider/model、Envelope SHA、budget ID、状态/UNKNOWN | Cheap/Expensive 独立真实容量/窗口/tokenizer、可信 Chat Token 及实际账单仍待授权核验；L=UNKNOWN 保留 |
| `StreamManager`、`RunLifecycle.events` | Session/Run/owner/seq，活动与短期事件 TTL；PG 可独立回读 | 不能用 Redis 唯一保存永久记忆、Checkpoint 或用户长期记忆 |

未来按会话加载、按真实模型窗口装配和持久化 Checkpoint 可复用上述稳定身份，不需要把历史驻留到 Python 集合。长期记忆需独立持久化与可验证 provenance，并与 KB 原始文档、临时 Redis 事件明确区分；本轮没有创建长期记忆索引或外发授权。Owner 的后续决定已记录，未提前实施。
