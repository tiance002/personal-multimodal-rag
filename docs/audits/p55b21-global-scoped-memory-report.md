# P5.5-B2.1 — Global＋Scoped 长期记忆

状态：**CHECKS_PASSED / NEEDS_OWNER_REVIEW**。Global＋Scoped 数据与接线 IMPLEMENTED；模型相关行为 SIMULATED_VERIFIED；真实商业模型与业务库启用 REAL_BLOCKED。执行者不授予 Owner ACCEPTED。

## 1. 基线与保护

HEAD 从开始到结束仍为 `befd5478a680ffa779a1e8ede736260d9bac1060`，分支 `codex/local-first-rag-v1-20260930`。开始时 21 个 dirty 路径与 C 的 final-manifest 原始 SHA 全部相符；快照保留于 `var/reports/p55b21-global-scoped-r1/start-snapshot.json` 和 `before/`。

只增量修改本轮记忆及对应 API/UI/测试入口。C Stage0 的默认提取模型身份和 Document Scope 修复没有重做；Query Understand Schema、Rewrite/Intent/Tasks、独立 Attempt、P5 特征与 0.35 阈值未改。P4 核心检索、Citation/Validator、Gate、Redis、原始会话记录、P7/Langfuse 和历史 0001–0020 Migration 未改。没有 reset/stash/clean、业务库迁移、商业 API、commit、push、部署或 Tag。

实际模型/effort 无服务端证据，UNKNOWN。无 Worker；执行状态 MANUALLY_SUPERVISED_TRIAL。复用 efficient-goal-execution 证据/快照，复用此前浏览器测试技能工作流，未调用 Langfuse 或供应商。

## 2. 最终数据模型与 0021

实际 Alembic head 为 `0021_global_scoped_memory`，down_revision 为 `0020_long_term_memory`；没有另一个 0021 分支。`alembic heads` 实际退出 0。

在既有 `long_term_memory_items` 增加 `scope_type text NOT NULL DEFAULT 'SCOPED'`：

| 类型 | 正式身份与允许数据 |
|---|---|
| GLOBAL | 显式 scope_type，scope 为 `{}`；独立类型标记生成 scope_hash；本机受信主体通用 profile/preference/interest，仅 origin=explicit |
| SCOPED | scope_type=SCOPED，保留原精确非空 KB 数组＋Document 数组和旧 scope_hash；所有旧记录默认此类型 |

GLOBAL 不通过空 KB 的隐式语义、假 document_id 或特殊文字表示：未显式选择 GLOBAL 的空 KB 请求仍拒绝；GLOBAL 请求带 KB/Document 绑定也拒绝。正式类型同时用于 SQL 查询与完整性核对。

数据库 CHECK 限制类型、Scope 形状、Global 分类/来源；Active 唯一索引和读取索引增加 scope_type。原 id、version、replaces_id、status、内容/哈希、来源/FK 不重建。数量限制仍全主体最多 200 active/pending、1000 历史条目；单条 300 字符，fact_key 120 字符。

迁移仅对已核验的 `p55a_lifecycle_test` 隔离测试库应用。实际 266 条旧记忆、66 条来源在迁移前后全行摘要一致：

- items：`2cadeff6322bcedf63f3d35f2def1b3196143eee9fd4f28beee906d470b0bdb9`；排除唯一新增 scope_type 列，其余全行内容的规范化摘要一致；此项不是物理存储字节比较。
- sources：`17e013e88c36b2c982fb82504c81d1b57ff914b97a57a3900c0dec0d85372d8c`。
- 所有旧记忆仍 SCOPED。正文 Attempt role/ordinal CHECK 前后相同。证据：`var/reports/p55b-memory-r1/p55b21-pg-1/schema.json`。

downgrade 在存在 GLOBAL 行时明确拒绝，不把 Global 静默变为 Scoped 或删除历史。业务库迁移 NOT RUN / REAL_BLOCKED，不能据测试库结果宣称生产已升级。

## 3. 统一召回规则与调用链

`PostgresMemoryRepository.recall_items` 在单个事务中校验固定 Principal 并持有主体行锁，读取 Active GLOBAL 与当前精确 SCOPED。全局读取开关关闭时两者均返回空；内容 hash、Scope/hash、Global 来源分类验证失败明确拒绝。

`MemoryRetriever._visible → recall → _select/quick_context/smart_context` 统一处理 union、冲突、词法相关性与模型窗口：

1. 当前有效 Scoped 对同规范化 fact_key 的 Global 优先，无论 SQL 返回顺序。
2. 不修改 Global 原行；Scoped 更新/删除后，下次回读重新决定是否显示 Global。
3. 不凭自然语言近义猜测合并不同 fact_key。
4. 词法相关性降序，然后 fact_key/id 稳定排序；profile/preference/interest 可辅助一般问题，fact/task 需有词法匹配；默认最多 5 条，可配置 1–20。
5. Quick/Smart 按每个实际 ModelWindow 的完整消息计数选择，独立记忆预算默认 512（须由可信 counter 定义），并检查完整请求、输出预留和安全余量。超限跳过整个记忆条目，不截断原始证据、不挤占核心证据/输出。
6. Query Understand 保持 C 接口：recall 返回有界候选后，原模型窗口准入检查完整解析请求；超限明确拒绝，不虚构 token，也不新增检索器。

调用链：受信本机 API → PostgresMemoryRepository 保存/确认/拒绝/删除 → MemoryRetriever 联合回读 → 共享 ContextManager → Quick/Smart；QueryUnderstandService 仍通过同一 `context.memory_retriever.recall` 获取背景。

Scoped 管理列表与 Global 管理列表分别显示；联合召回仅给上下文使用，不混入 KB Chunk/Parent/Child 或向量索引。KB 文档检索 Scope 始终由原服务端路径确定。Global 记忆不授予 KB 权限，不成为当前 EvidenceSnapshot/Citation，历史 q0/Run/Quote/Version/Locator 保持原合同。

Quick 发送前 `validate_frozen` 用同一 union/冲突规则重新核对 id/version/content；删除、关闭、更新或新的 Scoped override 使旧冻结候选失效。Smart 每步移除旧 long_term_memory 消息并从 PG 回读；记忆不进入历史协议或 Compaction 输入/Checkpoint。Query Understand 保留 C 的发送前来源重检和独立防重。

## 4. 自动提取与本地主体边界

自动提取依旧从已完成、成功提交的合法会话调度独立 `memory_extraction` purpose，结果仅为 **SCOPED pending**。未确认不生效，确认后也不跨 KB；不自行晋升 GLOBAL。PG adapter 和数据库 CHECK 双层拒绝 extracted GLOBAL 或 Global fact/task。

本项目没有新增登录或企业多租户。Principal 必须由本机服务器 composition 绑定；客户端不得提交 subject_id；本机、同源访问要求保持，公网客户端拒绝。直接仓储的其他 Principal 操作也拒绝。生产公网/多用户开放必须另有审查后的身份合同，本轮 REAL_BLOCKED。

本轮商业模型请求数 0。Query Understand 模拟身份继续为 Cheap（SiliconFlow / XingChenAGI/Xing4.0-29B），不意味着商业传输获准。提取、压缩、改写的独立 purpose/Attempt 与预占 UNKNOWN 不并入正文 Cheap/Expensive 两次额度，不自动重试、不退款旧未知占用。模拟 token/窗口仅测试夹具，不是可信供应商容量/费用验证。

## 5. 前端管理

沿用 MemoryPanel，默认 SCOPED。增加“用户级全局记忆／当前知识库或文档限定记忆”显式选择；GLOBAL 请求必须携带正式类型且 KB/Document 绑定为空，只提供通用资料、偏好、兴趣分类。保留保存、编辑、确认、拒绝、删除，增加折叠来源查看。

Scope key 包含类型和文档集合；effect 继续使用 `[kbId, scopeKey]`，epoch 拒绝旧列表/编辑结果。保留 Scoped 的实际 documentScope 绑定，原 C 断言未修改。Edge 实测 Global/Scoped 切换、迟到 Scoped 响应、显式 Global 保存、编辑/来源/删除、切回旧 Scoped 原行仍在；API 全部 SIMULATED，浏览器本身真实，chromiumSandbox 保持 true。

## 6. 实际测试、失败与命令

未实现的命令不得报告通过。以下轮次独立保留，重复用例不累计为独立总数。

| 轮次 | PASS / FAIL / ERROR / SKIP | 实际退出码 |
|---|---|---|
| p55b21-unit-1 | 21 / 0 / 0 / 0 | 0 |
| p55b21-pg-1 | 13 / 0 / 0 / 0 | 0 |
| p55b21-pg-final-associated-1 | 47 / 0 / 0 / 0 | 0 |
| p55b21-unit-final-associated-1 | 161 / 1 / 0 / 0 | 1 |
| p55b21-unit-final-repair-1 | 162 / 0 / 0 / 0 | 0 |
| p55b21-api-contract-1 | 1 / 0 / 0 / 0 | 0 |
| p55b21-rebound-pg-smoke-1 | 3 / 0 / 0 / 0 | 0 |
| browser-1 / browser-final-1 | 各 1 / 0 / 0 / 0 | 各 0 |
| 最终前端 build | PASS（tsc＋vite） | 0 |
| restart_readback before / after | 实际 PG 回读通过 | 各 0 |
| git diff --check / Alembic heads | PASS | 各 0 |

唯一关联失败为 `test_p55c_stage0.py::test_memory_panel_uses_actual_conversation_document_scope`：新增 Global 条件改变源码表达，使旧静态保护断言失败。保留原断言，单独保留 scopedBinding，并将 scopeType 纳入 Scope key；一次限定修复验证 162 PASS。没有改写历史断言。后端生产源码此后未变，47 项 PG 回归不重跑；测试端口重新绑定后只补 3 项冒烟。

命令（cwd `E:\RAG quention`）：

```powershell
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py p55b21-unit-1 backend/tests/test_p55b21_global_scoped.py backend/tests/test_p55b2_memory.py
.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55b2_memory_isolated.py p55b21-pg-1 backend/tests/test_p55b21_global_scoped_integration.py
.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55b2_memory_isolated.py p55b21-pg-final-associated-1 backend/tests/test_p55b21_global_scoped_integration.py backend/tests/test_p55b2_memory_integration.py backend/tests/test_p55c_query_integration.py backend/tests/test_p55c_scope_checkpoint_integration.py
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py p55b21-unit-final-repair-1 backend/tests/test_p55b21_global_scoped.py backend/tests/test_p55b2_memory.py backend/tests/test_p55c_query_understand.py backend/tests/test_p55c_stage0.py backend/tests/test_p55b_context_window.py backend/tests/test_p5_router_generation.py backend/tests/test_final_answer_commit.py backend/tests/test_smart_citation_replay.py backend/tests/test_bootstrap.py
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py p55b21-api-contract-1 backend/tests/test_p55b21_api_contract.py
.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55b2_memory_isolated.py p55b21-rebound-pg-smoke-1 backend/tests/test_p55b21_global_scoped_integration.py::test_global_cross_kb_scoped_and_document_never_cross backend/tests/test_p55b21_global_scoped_integration.py::test_query_understand_union_cannot_add_memory_fact_or_citation backend/tests/test_p55b21_global_scoped_integration.py::test_checkpoint_excludes_global_and_revoke_reloads
npm --prefix frontend run build
# cwd frontend；正常审批，真实 Edge、模拟 API
node node_modules/@playwright/test/cli.js test --config=p55b21-playwright.config.ts
.venv/Scripts/python.exe -B -m alembic -c alembic.ini heads
git diff --check
```

原始 argv、退出码、JUnit 与源码前后快照见 `var/reports/p5-rule-router-r1/p55b21-*`、`var/reports/p55b-memory-r1/p55b21-*`；浏览器结果见本轮 `browser-*`。集中索引为本轮 `test-results.json`。pytest Swig 弃用告警与前端已有 >500KB bundle 告警保留，未为清告警扩张修复。

## 7. 真实 PostgreSQL 重启与端口变更

唯一测试容器 `4db80024010c3c48b68684bb1ff28e471f5a9e72fd90f7b460a54091e425718f`，数据库 `p55a_lifecycle_test`、OID 16384、system identifier 7694635764170571814，Redis 52355。

重启前使用原 guard 回读：无其他客户端消费者，两份 Global 样本在不同 KB 可读。Docker 管道首次普通权限 inspect 退出 1；随后正常提权审批成功，仅读取该容器身份，不读取 Env/凭据。

```powershell
.venv/Scripts/python.exe -B -X utf8 var/reports/p55b21-global-scoped-r1/restart_readback.py before
docker restart --time 10 4db80024010c3c48b68684bb1ff28e471f5a9e72fd90f7b460a54091e425718f
.venv/Scripts/python.exe -B -X utf8 var/reports/p55b21-global-scoped-r1/verify_new_port.py
.venv/Scripts/python.exe -B -X utf8 var/reports/p55b21-global-scoped-r1/update_test_binding.py
.venv/Scripts/python.exe -B -X utf8 var/reports/p55b21-global-scoped-r1/restart_readback.py after
```

Docker 原 HostConfig 为随机宿主端口（HostPort 空串），重启后从 **52352 → 55729**。旧端口回读实际 ConnectionTimeout，退出 1，不能算恢复通过。第一次新端口辅助检查因保留旧 on-disk target 与新观察端口混用而在配置断言前停止，退出 1、未建立 DB 连接；随后修正仅本次只读观察绑定，退出 0。

Owner 单独授权先结束等待、只读核实新端口身份，再更新仅测试绑定。实际新端口 READ ONLY 核验全部身份一致、revision=0021 后，修改 `isolated-target.json` 的测试端口及两个必要测试入口，保留原绑定原始文件、前后哈希；未重建容器、删除数据或修改业务配置。历史证据中的旧端口保留。

Owner 授权后检查该等待会话时，它已经以 ConnectionTimeout 退出；没有另行终止其他进程。辅助命令的实际退出码与工具观测索引保存在 `observed-executions.json`，与原始测试 execution/JUnit 分开，不把观测摘要称为原始完整日志。

重启前 pg_postmaster_start_time `2026-10-09 11:56:39.095997+00:00`（北京时间 19:56）；重启后 `2026-10-09 18:21:46.438914+00:00`（北京时间 2026-10-10 02:21）。身份相同、启动时间不同，两份 seed 的 Global IDs 跨 KB 回读相同，guard 拒绝计数 0、商业调用 0；实际 after 退出 0。证据 `restart-before.json/restart-after.json/new-port-identity.json/test-binding-change.json`。

新的受保护测试目标是 **127.0.0.1:55729**，业务 Clean-slate 25438 未连接。其他历史 runner 若仍固化旧端口，不应绕过保护复用；按阶段授权核对后更新。未修改其他 runner 或重建环境。

## 8. 交付、回滚与剩余阻断

本轮新增/修改范围：0021、memory Port/服务/PG 仓储、Memory API、前端 client/MemoryPanel、Memory OpenAPI v2 快照、必要隔离测试入口、3 份后端新测试、前端 config/spec、本报告。既有 C 21 路径保留；新增修改路径和完整 raw SHA/bytes 在 final-manifest。task-only.diff 以本轮开始工作树为基线，不把 C 成果重复当成本轮改动；combined-head.diff 供核对全部未提交工作。

开始/结束快照、源码与实际通过轮次匹配、原始失败、Git 检查、增量 diff、artifact hash、逐项验收映射均位于 `var/reports/p55b21-global-scoped-r1/`。业务生产回滚应保留记忆表和记录；存在 Global 时不能降级到不认识其身份的版本；不要 reset dirty 或执行破坏性 downgrade。Owner 可基于 before/增量 diff 审核代码回退，未自动执行。

仍 REAL_BLOCKED：真实 Cheap Query Understand、自动提取/压缩商业准入、真实 tokenizer/window/费用验证、业务库迁移及生产开放；公网/多用户身份合同未实现。旧 UNKNOWN 提取 Job 仍需独立处理合同。本轮不修改 UNKNOWN，不清理账本。

P5.5-E 的独立语义向量记忆索引仍为必做后续目标，未实施。BM25、Query Expansion 未实施。完成后停止，等待 Owner 复核，不自动进入任何下一阶段。
