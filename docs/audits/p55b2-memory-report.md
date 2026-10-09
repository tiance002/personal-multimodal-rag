# P5.5-B2：上下文边界与跨会话长期记忆交付

状态：**CHECKS_PASSED / NEEDS_OWNER_REVIEW**。功能代码 IMPLEMENTED；本轮模型路径 SIMULATED_VERIFIED；真实模型及生产启用 REAL_BLOCKED。本报告不授予里程碑验收或上线批准。未实现的命令不得报告通过。

## 1. 基线、范围与保护

- 分支 `codex/local-first-rag-v1-20260930`，HEAD `e6cca0971d0f1875ca562a6b953fd5ec252b28e0`。工作目录 `E:\RAG quention`，Python 3.13.0 / pytest 9.1.1；使用项目 `.venv\Scripts\python.exe`。
- 开始快照 `var/reports/p55b-memory-r1/start-snapshot.json`：63 个 dirty 路径，67 份相关文件字节快照，93 份 B1 旧证据哈希。没有 reset、stash、clean、stage、commit、push、部署或业务库迁移。
- 期间发生外部漂移：Langfuse adapter 及八份 paired-evaluation 文件变化/缺失，Quick 的三个 paired-evaluation 辅助方法被撤回，另出现独立 P7 文档。没有执行这些删除/回退；外部执行者身份 UNKNOWN。详细路径与前后哈希在 `parallel-drift.json`，原始开始快照保留。不能宣称全工作树在本轮独占或字节完全一致。
- 增量审查同时提供 `start-to-final.diff`（本轮相关路径的真实起止差异，含共享 Quick 外部撤回）与 `task-only.diff`（排除已记录的三项外部辅助方法撤回）；协调基线保存在 `reconciled-before/`，不改原始快照。
- B1 已认可的 PG/Redis 生命周期、P4 检索/引用核心及 P5 特征分值、0.35 阈值不改。精确修改清单和全部 SHA-256 在 `owned-paths.json`、`final-manifest.json`。
- 本轮未请求任何子代理。实际服务模型/effort UNKNOWN，执行约束记为 MANUALLY_SUPERVISED_TRIAL，不声称控制平面已自动证明单写者。

## 2. 固定 WeKnora 依据

固定 commit `3e8b0bfc80b845b2d4b2ed683994748741450a97`，八份源码逐份读取并校验 Git Blob 与 SHA-256。公开下载只保存到 `D:\RAG-task-downloads\p55b-memory-weknora-3e8b0bf`，证据在 `upstream-manifest.json`。

| 固定源码/符号 | 借鉴与本项目边界 |
|---|---|
| `internal/types/memory.go`：MemoryKind、Origin、Status、WriteMode、MemoryContentMaxRunes、DefaultMemoryMaxItems | 五类记忆、explicit_only/auto、pending/active/替代；单条 300 字符、有效/待确认合计 200 条。字符上限是存储限制，不是 Token 数。新增 1000 条历史版本上限，仅拒绝新增，不自动删历史。 |
| `internal/types/interfaces/memory.go`、`service/memory/scope.go::ResolveScope` | 主体由服务端确定，不允许客户端选择用户。上游 workspace+principal；本项目只有明确配置的本地单用户，再加精确 KB/document Scope，不伪称企业级多租户认证。 |
| `service/memory/service.go::Remember/writeReplacing/ConfirmItem/DeleteItem` | 显式保存、待确认、替代、删除和拒绝记录；保留旧版本/来源，采用软删除，不复制全后台任务和自动遗忘流程。 |
| `service/memory/extract.go::ScheduleExtraction/collectSessionSegments/applyDecisions` | 已完成合法问答增量合批、持久来源游标、独立提取；本项目不引入 Asynq/Job Queue，生产派发关闭，模拟路径验证一次执行。 |
| `service/memory/search.go::SearchMemory`、`lexical.go::tokenize` | 独立主体内词法召回，CJK 字符及英文/数字词匹配；稳定偏好/资料/兴趣可作为常驻候选，事实/事项需查询匹配。未宣称实现上游全部排序、主题或向量能力。 |
| `internal/application/repository/memory.go` | PostgreSQL 为权威、Scope 过滤、替代和来源合同。Redis 不是永久记忆存储。 |

本项目当前没有已核准的独立 memory 向量索引和兼容缓存，因此本轮不借用知识库 chunk_embeddings、不调用 Embedding。语义召回 REAL_BLOCKED；稳定 `MemoryRetriever.recall(query, scope)` 接口可在后续授权中扩展。

## 3. Stage 0：真实模型身份与历史引用

`LangChainQuickChain._generate` → `ContextManager.gateway_role`：普通 Local/Cloud 按实际 gateway 的 `provider_name/chat_model` 选择窗口；Fallback 再进入 Cloud 生成时重新检查实际 Cloud 窗口。身份无匹配、窗口/tokenizer UNKNOWN 均在发送前拒绝。P5 Dynamic 继续以绑定的 Cheap/Expensive 完整身份双窗口预检，共享同一冻结 Envelope；不改变评分或升级规则。

`ports/context_budget.py::history_data/turn_messages/summary_message` 仅处理出站副本：删除旧 `[E数字]` 标签，Quick 历史引用元数据改为 `history/<old-run>/<label>`；Smart 协议和摘要同样隔离旧标签。原始 User/Assistant、历史 E1/E2 顺序、Quote、SHA、Version、SourceLocator 都保留在 PG。当前问题和本轮 EvidenceSnapshot 标签不改。

Quick 指令先于辅助数据，历史位于当前问题/证据之前。真实 Cloud 与 Fallback 误用 Local 窗口、旧文档 `[E1]` 与当前文档 `[E1]` 冲突三项，在冻结开始源码上实际 **3 FAIL**，当前源码通过。原始 baseline JUnit 保留。

注意 baseline 外层 PowerShell 命令包含随后 Remove-Item 环境变量操作，工具回执退出 0；受保护 Runner 的实际 pytest 退出码为 1。这一外层 0 不作为通过证据。

## 4. 数据模型与身份边界

新增 `0020_long_term_memory.py`，父 revision 为 `0019_context_checkpoints`，不修改 0001–0019。

| 新表 | 权威合同 |
|---|---|
| `memory_subjects` | 服务端 UUID 主体；默认读取关闭、explicit_only。配置开关不授权模型调用。 |
| `long_term_memory_items` | 主体、精确 KB/doc Scope+哈希、分类、规范化 fact_key、正文+SHA、origin、status、版本、replaces_id、时间。active 同事实部分唯一索引；pending 不参与召回。 |
| `memory_sources` | item 关联；自动来源 Run/User Message FK、来源哈希及游标、模型身份；显式来源为 Owner 操作及服务端 Request ID/时间。来源和原始消息保留。 |
| `memory_extraction_jobs` | 稳定 purpose=memory_extraction、来源快照/游标、主体/会话/Scope、模型、状态、独立 model_calls FK 和安全诊断。 |
| `memory_extracted_runs` | 主体+Scope+Run 唯一来源消费记录，不因模型别名、新任务 ID、进程重启或 UNKNOWN 自动重放。 |

已有 `0006_m4_agent.py` 的 `memory_items` 是会话级 preference/summary/user_note；首次同名建表被拒，事务回滚。查清后采用独立长记忆表，不覆盖或迁移旧表。最终只读核查旧表仍为原列结构、当前行数 0；缺少本轮开始时旧表行数快照，不把它写成前后计数验证。新 Migration 不写旧表。

`PostgresMemoryRepository` 由服务端单主体绑定，主体行锁串行化新增/确认/替代与容量判断。请求传其他主体被拒；Scope 不匹配不可读取/编辑另一 Scope 的记忆。同事实按规范化 fact_key 和精确内容 SHA 去重，不声称自然语言语义等价已可靠解决。

删除/拒绝保留 tombstone；相同来源 Run 已被消费，不能换 ID 重提取；相同事实键的新自动候选也不能无条件恢复已删/拒绝事实。Owner 再次显式保存属于新的明确操作。满 200 条时拒绝新事实，但允许原子替代已有事实；总历史达到 1000 时拒绝新版本，保留策略待 Owner 决定，不自动清理。

## 5. 显式管理调用链

前端系统设置 `MemoryPanel` → 现有 `api/client.ts` → `/api/v1/memory` DTO → 服务端 Principal/本机/same-origin 检查 → 已有 KB/document Scope 验证 → `PostgresMemoryRepository`。

- 提供保存、列表、编辑、确认、拒绝、删除、读取开关及 explicit_only/auto 模式。显式保存不依赖 LLM。
- 五条 OpenAPI 路径包含 GET/PATCH settings、POST list/items、PATCH item、POST item operation；新 DTO 禁止额外 subject_id/user_id 等字段。独立合同为 `contracts/memory.openapi.json`。已有 OpenAPI 路径无删除；旧全项目合同快照仍有 A/B 阶段增量差异，不据此宣称完整发布合同全绿。
- `Settings.memory_local_principal` 默认 None；没有可信主体时入口拒绝。主体只来自受信服务端配置，非请求头或 UI；组合根要求 loopback bind，API 拒绝非 loopback Client/Host 与跨 Origin。
- 不支持公网、多用户、未审查反向代理的身份转发。Docker/网络部署需要另行审查认证与主体派生，不能靠伪造客户端 IP 或 user_id 开启。
- UI 内联管理，不增加弹窗；切换 Scope 作废旧请求，防迟到列表覆盖当前 Scope。构建通过，真实浏览器交互 NOT RUN（Playwright Chromium 文件缺失，无下载）。

## 6. 自动提取状态与费用

成功的 `AnswerService` 最终 PG 提交之后才调用 `MemoryService.after_completed`。explicit_only 不调度；auto 只建立持久 PENDING 任务。调度失败不重写已提交答案、不重新生成，返回固定 `MEMORY_SCHEDULE_FAILED` 诊断。

调度只读取完成/无 error、存在 answer.completed、原 User 与 q0 一致且恰有完整 User/Assistant 配对的 Run，验证当前会话与来源 Scope。每批最多 8 轮、UTF-8 来源数据最多 32000 字节；原子超大来源拒绝，不截断伪装成完整证据。

`MemoryService.extract_once` 目前仅接受显式注入的 SIMULATED Provider：

1. 一次原子 PENDING→IN_PROGRESS claim；受信模型身份核对、完整输入容量检查。
2. 正常 PostgresBudgetGate 以独立 purpose 预占；绑定并校验预算的 Run/provider/model/purpose。
3. 发送前再核对 mode、会话 Scope、原始消息/完成时间；仅一次调用，无自动重试。
4. stop 完成、列表上限、五类字段、source_index（拒绝 bool）及正文大小全部校验；任何推断候选均 pending。
5. 候选+完成状态同一事务落库；UNKNOWN 保守占用，不能当作费用为 0。失败保留来源消费记录。

失败/截断/非法结构/存储异常保留固定原因、phase、RESPONSE_RECEIVED/UNKNOWN/NOT_SENT、可信模拟 Usage 来源，不回显异常正文。预算拒绝或来源改变为 NOT_SENT，可靠未发送仅释放本任务自己的预占；可能已发送保持 UNKNOWN，不退款。收据/状态持久化失败明确 `MEMORY_FAILURE_PERSISTENCE_UNKNOWN`，保留 IN_PROGRESS 和占用，不声称闭锁成功，也不允许重复发送。

两种正文 Cheap/Expensive Attempt 上限不变；提取不用 rag_model_attempts 的两个正文角色。`model_usage` 新增独立 memory_extraction stage。商业请求 **0**，真实商业费用 NOT_INCURRED；供应商真实 Tokens NOT RUN。模拟成功使用返回 13/8 Tokens 的 fixture，标 SIMULATED_PROVIDER_RETURNED；模拟预算费用 UNKNOWN，不伪装为实际扣费或免费供应商调用。

最终隔离库累计测试记录：27 COMPLETED、4 IN_PROGRESS（故意注入状态持久化失败）、7 NOT_SENT、8 PENDING、16 UNKNOWN；独立预算 47 行 UNKNOWN，预占 329 微单位，4 行可靠未发送 released 共 28 微单位。仅为本轮多轮隔离测试累计，不能当作真实商业发送计数。详见 `accounting-readback.json`。

## 7. 召回、上下文和删除即时性

Quick/Smart 共用 `MemoryRetriever`。每次向 PostgreSQL 查询精确主体+Scope，只选 active 且读取开启的条目，验证内容哈希；最多 5 条、默认单独 512 Token 预算（需要可信 ModelWindow.counter），随后再检查完整模型窗口。不能用字符数量替代真实 Token；本轮计数 fixture 明确是 SIMULATED JSON scalar units。

装配顺序：系统安全指令 → 标为不可信辅助数据的长期记忆 → 合法摘要/短期历史 → 当前问题 → 当前知识证据/工具流程。记忆先被预算筛选，不能挤掉当前问题、核心证据、工具协议或输出预留。

记忆文本里的旧 E 标签也只对发送副本移除。记忆不进入 Chunk/Parent/Child、EvidenceSnapshot 或知识库权限，不成为知识库事实引用。Quick 冻结 Envelope 在每次发送前验证其中记忆仍 active/版本/正文一致；撤回则拒绝，不重写冻结证据。Smart 每个模型步骤移除旧 memory block、从 PG 重读并装配；长期记忆不被纳入 SESSION/LIVE Compaction 或持久 tool protocol，避免删除内容通过旧 Checkpoint 再出现。

Redis 只承接原有活动/事件。实际 TTL 到期后新仓储仍读出已确认记忆，另用新受保护 Python 进程验证 PG 恢复。没有从 Redis 重新生成答案或记忆。

后续 Query Understand 可复用 `ContextManager.history`、有效 Checkpoint 源身份及 `MemoryRetriever.recall(query, scope)`；必须继续视为辅助数据，不改变授权 Scope。本轮没有实现 Query Understand、BM25、Expansion 或长期记忆 Agent 写工具。

## 8. 实际测试与证据

所有 Python 测试均使用项目解释器和受保护入口，过程/JUnit/前后源码哈希保留；没有读取 Key、真实 DeepSeek 账本或业务库。隔离目标现场验证：127.0.0.1:52352 / p55a_lifecycle_test / OID 16384 / system identifier 7694635764170571814；Redis 52355。仅此测试库升级 0019→0020，原 Chat role/ordinal 约束不变。未连接 Clean-slate 业务目标 25438 或其他 DB。

| 轮次 | 实际结果/退出码 | 证据 |
|---|---|---|
| stage0-baseline | pytest 3 FAIL、14 deselected；pytest=1，外层 shell=0 | p5-rule-router-r1/p55b2-stage0-baseline/junit.xml、execution.json；冻结源码单独加载 |
| unit-1 | 43 PASS、1 FAIL；exit=1 | p5-rule-router-r1/p55b2-unit-1；测试 substring now 命中 knowledge，改结构消息顺序断言，保留失败 |
| pg-1 | Migration DuplicateTable、exit=1；pytest/JUnit NOT RUN | p55b-memory-r1/pg-1/source-before.json；失败在已有旧 memory_items，无覆盖 |
| pg-2 | 15 PASS；exit=0 | pg-2/junit.xml、schema.json；真实隔离 PG，模型 SIMULATED |
| offline-final-1 | 342 PASS；exit=0 | p5-rule-router-r1/p55b2-offline-final-1；后续有上下文修正，不能把它当新版完整回归 |
| pg-final-1 | 57 PASS、4 FAIL、2 deselected；exit=1 | pg-final-1；历史引用冗长替代文本放大输入，早于原 Compaction 断言失败 |
| offline-final-2 | **209 PASS**，无 FAIL/ERROR/SKIP；exit=0 | p5-rule-router-r1/p55b2-offline-final-2；上下文、Quick/Smart、P5 generation、预算、引用、最终提交 |
| pg-final-2 | **31 PASS**、32 deselected；exit=0 | pg-final-2；B2 19+B1 10+必要生命周期 2；没有改失败断言 |
| pg-restart-1 | 1 PASS；exit=0 | pg-restart-1；独立受保护进程恢复记忆 |
| pg-memory-final-3 | **21 PASS**，无 FAIL/ERROR/SKIP；exit=0 | pg-memory-final-3；完整 B2 集成含 fresh process、容量原子替代、实际 Smart 与 API |
| 前端 build（两轮） | exit=0，tsc+Vite；已有大 bundle 告警 | build-results.json；后续只改 Busy reset，已重新构建 |
| API DTO、AST、分层、Git diff | 实际结果见 static-checks.json/git-checks.json | 独立 Memory OpenAPI；不宣称旧 layering/发布合同失败已经修复 |
| Playwright 浏览器 | NOT RUN | 已查证 executablePath 对应文件缺失；未安装或换边界绕过 |

完整命令 argv 在 `test-results.json` 各 execution.json 原样记录。最终使用：

```text
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py p55b2-offline-final-2 backend/tests/test_p55b2_memory.py backend/tests/test_p55b_context_window.py backend/tests/test_bounded_history.py backend/tests/test_langchain_agent.py backend/tests/test_quick_chain_budget.py backend/tests/test_p5_router_generation.py backend/tests/test_answer_service.py backend/tests/test_budget_gate.py backend/tests/test_final_answer_commit.py backend/tests/test_smart_citation_replay.py backend/tests/test_quick_citation_prompt.py backend/tests/test_bootstrap.py
.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55b2_memory_isolated.py pg-final-2 backend/tests/test_p55b2_memory_integration.py backend/tests/test_p55b_context_integration.py backend/tests/test_p55a_lifecycle_integration.py -k 'p55b2_memory_integration or p55b_context_integration or test_owner_citation_order_e2_e1_replay_and_sse or test_existing_rerank_budget_does_not_require_chat_attempt'
.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55b2_memory_isolated.py pg-memory-final-3 backend/tests/test_p55b2_memory_integration.py
npm --prefix frontend run build
git diff --check
```

分层检查实际执行 `.venv/Scripts/python.exe -B -X utf8 var/reports/p55b-memory-r1/layering_check.py`，exit=0：本轮新增违规 0；全项目仍有 knowledge_repository→context_expansion、local_caption→Ollama adapter、ports/retrieval→context_expansion 三项既有违规，完整 layering 状态仍为 FAIL，详情 `layering-check.json`。证据收口命令为 `var/reports/p55b-memory-r1/closeout.py`，只核验/生成本轮审计证据，不跑测试或改业务实现。

不是将三轮 PASS 相加：最终 B2 21 个真实隔离用例覆盖 final-2 的旧 19 个并增加进程/容量；B1 与两项关联不变量沿用 final-2 的 12 PASS，相关生产实现未再变化。209 离线的 PG adapter/隔离 Runner 不参与其模拟执行；该部分最终由 21 项真实集成覆盖。前后源码差异与用例 AST 绑定在 `test-results.json`，不把旧 342 PASS 当作新版全套通过。

Compaction 失败没有增加真实窗口：旧标签直接移除后，原 18 次重复不再触发超限；SIMULATED 24 次重复的完整输入 3312>3000、压缩输入 2806<=3000。只调整合成长历史样本以保留原分支与断言，生产容量不变；计算证据 `simulated-history-boundaries.json`。P2 frozen PDF 样本未改。

旧两项 DeepSeek Gate、业务库/真实模型、全项目发布回归均 NOT RUN，本轮不改其断言/账本。历史依赖升级影响未独立验证的限制保留。

## 9. 生产阻断、回滚与 Owner 待审

1. Cheap/Expensive/Local/Cloud 的真实窗口、tokenizer 和完整 wire counting 合同仍 UNKNOWN；本轮没有伪造容量或供应商 Tokens。真实高风险调用保持拒绝。
2. 实际商业 memory_extraction/compaction Provider、授权、额外费用准入与后台派发未开放；组合根未注入真实 extractor，memory service 默认无真实发送能力。PENDING 的 UNKNOWN 模型任务不能伪造身份后派发；未来身份绑定/准入另审。
3. 0020 只验证隔离库，业务迁移、备份/恢复和网络身份部署需 Owner 独立授权。没有真实生产启用。
4. Lexical 不是语义向量召回；兼容 memory 索引缺失，需另行授权。fact_key 的自然语言同义合并未校准，候选须确认。
5. 1000 版本上限/保留策略、进程异常后占用记录人工核查及 UNKNOWN 处置需 Owner 决定，不能自动清理或补发。存储失败测试的 IN_PROGRESS 已明确不作为成功闭锁。
6. 浏览器交互、正式发布合同、真实模型效果/记忆质量与多用户认证 NOT RUN/REAL_BLOCKED；不称全项目发布验收通过。
7. 外部 Quick/paired/P7 漂移由 Owner 协调，本报告只认可本轮边界，不替其他任务批准修改。

安全回滚：本轮未提交，按 `task-only.diff` 审核后逆向移除本轮增量，不能覆盖既有 P5/A/B1 或外部改动。业务库未迁移无需回滚。隔离 0020 downgrade 在存在历史记忆/任务时主动拒绝，保留数据，不自动删表。生产关闭保持 principal=None / read_enabled=false / extractor=None；这些开关不会删除历史或释放 UNKNOWN。

本轮首次实现与准备约 31 分钟（交付前 Goal 时钟 1859 秒），单轮测试小于 40 分钟。最终 HEAD、dirty 数、源码与证据 SHA、默认 diff --check 退出码以 final-manifest/git-checks 为准。完成后停止，等待 Owner 复核，不进入 Query Understand 或 BM25。
