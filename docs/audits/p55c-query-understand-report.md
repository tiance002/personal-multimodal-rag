# P5.5-C — B2 收口与统一 Query Understand

状态：**CHECKS_PASSED / NEEDS_OWNER_REVIEW**。这是本阶段代码、模拟 Provider、隔离 PostgreSQL 和模拟 API 浏览器证据；不是生产模型准入或跨 KB 个性化验收。

## 1. 基线、实际停止阶段与保护

- 开始和结束 HEAD：`befd5478a680ffa779a1e8ede736260d9bac1060`，分支 `codex/local-first-rag-v1-20260930`。开始工作树干净，远端只读查询返回同一 SHA。
- 已实际进入并完成 C Stage 0–4 的限定实现，阶段测试完成。根据 Owner 顺序调整，继续 C 后停止，后续为 **P5.5-B2.1 Global＋Scoped Memory**；不在本轮实施。
- 未 reset、stash、clean、pull、修改历史证据、迁移业务库、创建 commit、push、部署。未读取真实 Key。真实商业 API 请求 **0**。
- P4 检索核心、P5 原始特征提取贡献值、0.35 阈值、Citation/最终提交校验、原始消息/快照、Migration、P7/Langfuse 文件未改。未清理旧 UNKNOWN Job、预算占用或收据。
- 执行：MANUALLY_SUPERVISED_TRIAL；实际 serving 模型/effort 无可核实来源，记 UNKNOWN。未使用 Worker。

## 2. Stage 0 两项反例

### 2.1 未配置模型不消费历史来源

`MemoryService.availability/settings/after_completed/schedule` 与 `PostgresMemoryRepository.schedule` 拒绝未绑定或 UNKNOWN 的正式提取身份。默认 composition root 无提取窗口时，完成会话后不会创建 `memory_extraction_jobs`，也不会提前占用 `memory_extracted_runs`。API 返回 `MODEL_IDENTITY_NOT_CONFIGURED`，UI 显示尚不可用。

修复前 `p55c-stage0-baseline` 的两个反例均 FAIL。修复后 Stage 0 单元关联 20 PASS；真实隔离 PG 的 `test_default_root_no_source_claim_then_trusted_model_can_backfill` 验证默认零任务/零来源占用，随后可信模拟身份可从同一已完成原始历史建立任务。这不是自动补发，也不改变旧 UNKNOWN Job 身份；旧任务独立处理合同仍待审批。

### 2.2 管理 Scope 和会话一致

`App → MemoryPanel(kbId, documentScope) → api.memory*` 传递当前会话真实文档限定；设置页区分当前 KB 和文档限定。effect 和 mutation 用 epoch 拒绝旧 Scope 异步结果，切换时清除旧列表/编辑态。settings 写入仅白名单 `read_enabled/write_mode`，不回传新增只读 availability 字段。

PG 反例实际验证 Document A 的待确认记忆不能在 B 读取、确认或删除；A 的合法确认/删除可执行。Edge 浏览器用模拟 API 验证 A/B 保存、迟到 A 响应不覆盖 B、B 删除，1 PASS。后端原 Scope 权限规则未放松。

## 3. 固定 WeKnora 源码依据

固定提交 `3e8b0bfc80b845b2d4b2ed683994748741450a97` 的 [query_understand.go](https://github.com/Tencent/WeKnora/blob/3e8b0bfc80b845b2d4b2ed683994748741450a97/internal/application/service/chat_pipeline/query_understand.go) 已实际读取。

- `OnEvent`（61）、`loadHistory`（196）：独立问题与历史重用/改写入口。
- `selectModel`（230）、`buildPrompts`（287）、`memoryBackground`（335）：改写模型选择、历史和记忆背景。
- `parseOutput`（380）、结构化解析（398/421）：改写和 Intent 输出。
- 精确 Git Blob `c8c29777229f2d4af90b89b136d628db4b2e894a`，17547 字节，SHA-256 `4b30c67f6b59bcd74c6cba4bca1c6a5ea0a6327e7a04fa8fd587870864b86e63`。
- 精确副本位于 D 盘 `D:\RAG-task-downloads\p55c-query-understand-weknora-3e8b0bf\query_understand.verified.go`。首次导出多一个 LF 的文件保留，不能作为精确字节核验副本。来源是 GitHub 固定源码，不代表本机部署版本。

本项目采用统一历史入口、背景信息边界、结构化输出思路；没有复制上游自动模型 fallback、图片改写或宽松输出解析。

## 4. 输入/输出与最终调用链

输入：原问题 q0、Conversation、服务端 Scope、当前 Run、KB 外发许可。输出契约为 `backend/app/ports/query_understand.py::QueryUnderstanding.public()`，版本 `query-understand/v1`：

| 字段 | 合同 |
|---|---|
| q0 / rewrite_query | q0 永久保持原文；rewrite 只做经已完成历史验证的合法表达替换 |
| needs_clarification / reason | 多对象或缺失来源返回澄清，不依检索相似度选对象 |
| intent | lookup / compare / compare_select / select / calculate / summarize / apply_rule |
| tasks | 原 q0 文本及 start/end、operation；与 Intent 分开 |
| task_status | COMPLETE；超上限为 INCOMPLETE_TASK_LIMIT，不静默丢弃条件 |
| history_run_ids / referents | 实际解引用的已完成历史 Run 与原用户问题中的对象 |
| memory_identities | 实际模型输入的记忆 id/version，不是本轮知识库证据 |
| checkpoint_identity | 实际模型输入的有效 Checkpoint 身份；未使用时 null |
| model_status / attempt_id / diagnostics | 零调用、SIMULATED_COMPLETED 及 NOT_SENT/RESPONSE_RECEIVED/UNKNOWN 发送观测；终态 UNKNOWN 另见持久事件和预算状态 |

JSON Schema 及模拟示例位于 `var/reports/p55c-query-understand-r1/query-understand-schema.json`。内部服务输入中的 Scope 仍由服务端确认，Schema 不构成授权。

`bootstrap → QueryUnderstandService → AnswerService._answer → FollowUpResolution → QuickChain 或 Smart Agent → 既有 Retrieval/Context/Validator/Commit`。两种模式共享一份解释结果。q0 的 Run 持久化不变；模型/检索使用合法 rewrite；trace 保存结构化解释和来源。

历史读取复用 `ContextManager.history → completed_history_context`；Checkpoint 复用 `PostgresContextStore.load_checkpoint`，核对覆盖 Run/hash。没有第三套历史读取器。引用标签通过既有 `history_data` 处理为历史数据，不成为本轮 E1；当前 Quote/SHA/Version/Locator 在 PG Quick/Smart 反例中保持一致。

记忆唯一入口为 **MemoryRetriever.recall(q0, scope)**。Query Understand 不硬编码 KB-only 过滤，不自行实现 Global/Scoped 联合检索。记忆和摘要是低优先级背景，不能成为实体、数字、权限或事实约束证明。

## 5. 解释能力和边界

- 独立问题使用确定性零调用路径，q0 原文保留。
- 单轮/多轮唯一对象、前一种/后一种及已有方法引用采用有界语法和已完成用户问题来源。ASCII 实体与中文后缀之间补合法词边界，避免现有 Keyword tokenizer 将 `A的成本` 视为整体；不修改 P4 tokenizer。
- 比较发布日期/性能/价格/中文检索选择案例保留四项 Tasks；默认最多 8 项，可配置 1–16。超限显式澄清，q0 全文保留。
- 模拟模型只允许核验已有来源证明的结构化解释；未实现任意自然语言语义 Rewrite。语法外或不能唯一解析的问题安全澄清，不能称为完整任意语言理解能力。
- 含“总和”的方法名可能按并列对象造成歧义；保留澄清反例，另用无歧义“相减计算方法”验证依赖。未降低原断言。

## 6. Router、独立 Attempt、费用与 UNKNOWN

`rule_router_v1.decide` 对 q0 和经验证 rewrite 的原 T/C/J/D/L/M 特征逐项取 max，不把 Tasks 数量另行加分。原问题风险不能被模型“简单”判定降分，同一条件不重复累计；原贡献值及阈值保持。L 没有可信 token 来源时仍 UNKNOWN。

`PostgresQueryAttemptStore` 复用现有 `rag_runs` 行锁、lease fence、`retrieval_events` 序号及 `model_calls`。独立 purpose 为 `query_understand`，Attempt 身份固定为 Run/purpose/ordinal1；同一 Run 不能通过换模型/输入再派发。未修改正文 `rag_model_attempts` 的 Cheap/Expensive 角色或两次上限，没有新增 Migration。

状态链：CLAIMED → RESERVED → DISPATCHING → COMPLETED / NOT_SENT / UNKNOWN。发送前来源/记忆撤销重检，模型窗口与实际 Provider/Model 匹配、Scope 外发许可及预算门全部保留。可靠未发送仅释放本次预占；已返回非法输出、截断、超时或持久化不明保留 UNKNOWN，不退款、不自动重试。

使用量按独立 `query_understand` stage/purpose 保存，不冒充正文 Attempt。测试模型返回的 17/9 token 仅 SIMULATED Provider 数值；模拟费用仍 UNKNOWN，成功调用也保守保留预占。测试预占 7 microunits 是合成预算输入，不是供应商报价或真实结算。真实隔离 PG 断言用量表 purpose/state、并发只派发一次、重建 adapter 后拒绝重复、持久化失败保留 DISPATCHING/UNKNOWN。

Owner 后续指令：改写测试使用 Cheap。已将模拟 Parser 身份对齐冻结 `chat_cheap`：SiliconFlow / `XingChenAGI/Xing4.0-29B`，**execution_kind 仍 SIMULATED**；模型窗口/计数器是测试夹具，不是该模型真实容量验证。34 项改写定向和 10 项 PG 再验通过。真实 Cheap 请求没有发生。

UsageCapture 新增独立 stage 与 business_total；原 RunMetrics 顶层旧 total 不是全部额外调用的总账，不应据其声称已计算全部成本。消费者应读取独立 model_calls/diagnostics 与权威预算账本。

## 7. 实际命令与证据

以下命令均从 `E:\RAG quention` 执行；原始 argv、pytest 退出码、源码前后哈希在各目录 `execution.json/source-before.json/source-after.json`，JUnit 不覆盖。

```powershell
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py p55c-query-owner-final-1 backend/tests/test_p55c_query_understand.py backend/tests/test_p55c_stage0.py backend/tests/test_answer_service.py backend/tests/test_bounded_history.py backend/tests/test_p5_router_features.py backend/tests/test_p5_router_generation.py backend/tests/test_p5_router_boundaries.py backend/tests/test_p5_router_replay.py backend/tests/test_p55b_context_window.py backend/tests/test_p55b2_memory.py backend/tests/test_final_answer_commit.py backend/tests/test_smart_citation_replay.py backend/tests/test_bootstrap.py
.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55b2_memory_isolated.py p55c-query-pg-repair-1 backend/tests/test_p55c_query_integration.py
.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55b2_memory_isolated.py p55c-scope-checkpoint-pg-1 backend/tests/test_p55c_scope_checkpoint_integration.py
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py p55c-query-cheap-identity-1 backend/tests/test_p55c_query_understand.py
.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55b2_memory_isolated.py p55c-query-cheap-pg-1 backend/tests/test_p55c_query_integration.py
npm --prefix frontend run build
# cwd E:\RAG quention\frontend; 正常审批执行，chromiumSandbox:true
node node_modules/@playwright/test/cli.js test --config=p55c-playwright.config.ts
git diff --check
.venv/Scripts/python.exe -B -X utf8 var/reports/p55c-query-understand-r1/closeout.py
```

| 轮次 | PASS / FAIL / ERROR / SKIP | 退出码 / 依据 |
|---|---|---|
| Stage0 原反例 | 0 / 2 / 0 / 0 | 1，p5-rule-router-r1/p55c-stage0-baseline |
| Stage0 修复关联 | 20 / 0 / 0 / 0 | 0，p55c-stage0-fixed |
| Stage0 PG 初次 | 2 / 0 / 0 / 0 | 0，p55b-memory-r1/p55c-stage0-pg-1 |
| Query 初轮 | 206 / 30 / 0 / 0 | 1，fixture 传入不存在的 window(tokens=...) |
| Query 首次修复 | 235 / 1 / 0 / 0 | 1，新方法名 fixture 歧义 |
| Owner 额外限定验证 | 352 / 0 / 0 / 0 | 0，p55c-query-owner-final-1 |
| PG Query 初轮 | 7 / 3 / 0 / 0 | 1，datetime/UUID 序列化及混合实体词边界 |
| PG Query 修复 | 10 / 0 / 0 / 0 | 0，p55c-query-pg-repair-1 |
| PG 文档 Scope＋Checkpoint | 2 / 0 / 0 / 0 | 0，p55c-scope-checkpoint-pg-1 |
| Cheap 身份定向再验 | 34 / 0 / 0 / 0 | 0，p55c-query-cheap-identity-1 |
| Cheap 身份 PG 再验 | 10 / 0 / 0 / 0 | 0，p55c-query-cheap-pg-1 |
| Chrome 浏览器初次 | 测试体 NOT RUN，启动 FAIL | crashpad 管道失败，收尾卡住；Owner 改用 Edge 后仅终止本 Runner，进程退出 1，pytest 不适用 |
| Edge 初次 | 0 / 1 / 0 / 0 | 1，测试遗漏会话切换后重开设置页；保留 error-context 和 JUnit |
| Edge 限定修复 | 1 / 0 / 0 / 0 | 0，browser-edge-repair-1；API 全部 SIMULATED |
| 最终前端 build | PASS | 0，tsc＋vite，已有 bundle >500KB 告警保留 |

不得把不同轮次或重复用例相加作为独立测试总数。352 项关联回归生产源码未再改变；之后仅按 Owner 指令更改模拟 Cheap 身份，34 项及 PG 10 项分别复验，不重复全量历史回归。

真实隔离测试目标：127.0.0.1:52352 / `p55a_lifecycle_test`，OID 16384，system identifier 7694635764170571814；Redis 52355。现场 Schema 仍 `0020_long_term_memory`。没有连接业务 Clean-slate 25438，没有执行迁移。测试保留合成业务行供核查，不清空测试库。

未实现的命令不得报告通过。本轮未运行全 P4/P5 历史回归、真实商业 Rewrite、Global Memory、语义记忆向量索引、BM25 或 Query Expansion。

## 8. 修改范围、精确 diff 与最终身份

生产修改：memory.py 服务/PG 仓储、API routes、AnswerService、QuickChain、RuleRouter 辅助接线、bootstrap、model_usage、前端 client/App/MemoryPanel；新增 QueryUnderstand 服务、输出 DTO 和 PG Attempt adapter。

新增四份定向测试：query_understand、query_integration、scope_checkpoint_integration、stage0；另有模拟浏览器 config/spec 和本报告。完整路径、字节长度及 SHA-256 在 `var/reports/p55c-query-understand-r1/final-manifest.json`。

`task-only.diff` 包括 tracked 增量和全部新增项目文件；`git-checks.json` 记录最终 HEAD、dirty、空暂存区及 diff --check。`tested-source-match.json` 核验改动的 backend 生产源码仍与通过轮次源码快照一致。`test-results.json` 保留各轮实际失败名称和原始 JUnit 哈希。没有新 commit，最终 SHA 仍基线；全部改动等待 Owner 复核。

## 9. Global Memory 接口交接与生产阻断

1. **必做依赖**：B2.1 接通 Global 用户记忆与 Scoped 联合召回，由 MemoryRetriever 负责主体可见性、确认/删除和版本身份。C 的模拟联合召回反例证明抽象兼容，不证明真实跨 KB 个性化已实现。
2. **后续必做独立阶段**：记忆语义向量索引；本轮未实施。Query Understand 只消费已经筛选的 recall 结果，不接管知识库 Chunk/Parent/Child。
3. **真实模型 BLOCKED**：生产 composition root 仅接通确定性零调用，不绑定 Query Understand 模型发送器。后续真实 Cheap 需要独立 purpose 授权、真实 Token/window、价格与预占、Scope 外发和持久化防重准入；不能复用 DeepSeek Answer Grant。
4. **旧 UNKNOWN 提取任务**：保持原身份及来源占用，需要独立迁移/处理合同；不能直接改模型或自动补发。
5. **理解覆盖边界**：本轮是有界来源证明型 Rewrite/Intent/Tasks，语法外澄清。真实模型输出质量、任意自然语言覆盖和实际 Token/费用未验证。
6. **Owner 审查事项**：本阶段语义结果、Scope/引用/预算证据、增量 diff；Owner 决定接受及下一阶段授权。本执行者不授予 ACCEPTED、不进入 B2.1、BM25 或 Query Expansion。
