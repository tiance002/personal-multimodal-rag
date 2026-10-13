# Codex Goal：WeKnora 固定版 Python 等价移植 + P5 动态模型路由正式主链

> **执行方式：**本文件是可直接派发的完整 Goal，不是单纯研究提纲，也不是针对旧项目长题 4096 的继续补丁。阅读配套 `01_实施计划.md` 和 `03_决策边界与源码清单.md` 后，在**隔离工作区内**实际编写代码、测试、生成文档和阶段性本地提交。除下文明确需要负责人批准的真实外发/部署/数据修改外，各代码阶段可以自主推进，避免重复审计与反复请示。

## G0. 固定目标与不可违背的边界

### G0.1 主体与唯一上游

- 产品：`tiance002/personal-multimodal-rag`；已检查远端 `main@55372f7bae8e30a8150d16e0eb12262a038cbea2`，**执行前必须以实际工作树和 Git 状态重新确认**，如已更新立即记录差异；不要默默回退或覆盖用户未提交文件。
- WeKnora 业务行为唯一基准：`Tencent/WeKnora@3e8b0bfc80b845b2d4b2ed683994748741450a97`，不能随意跟踪上游最新 main、另找教程或改变基准。
- 技术实现：**Python/FastAPI 后端 + React 前端**。WeKnora 原有 `docreader/` 就是 Python，允许按版权约束直接参考/复用；对 Go 的业务逻辑进行 Python **语义等价移植**。最终后端不得改成 Go，前端不得改成 Vue。
- 只允许两项业务算法差异：**原 P5 Cheap/Expensive 动态模型路由；Cheap 失败后的受控升级 Expensive**。不能在迁移过程中新增另一套检索选择器、在线语义 Validator、输出长度分类器、特殊备选模型链、再生成自我修复等。
- 六个公开数据集/评测中心保留在主链外，用来验证上述两项创新，不是普通问答的运行门槛。
- 安全例外只允许**最小边界包装**：逐知识库外发同意、必要密钥/租户/Scope 权限、持久请求幂等、按月费用上限和用量不明时的保守处理。WeKnora 已有的直接移植；缺失的少量保留有代码证据的最小适配，不许继续保留完整历史实验链或改变 RAG 算法。

### G0.2 必须停止的旧工作

- 停止 `weknora-ordinary-output-progress` 下的 G1–G5 补丁式研发。现有已提交但未验收部署的七个流式候选文件、旧 `quick_chain` 双路径、`ordinary_generation_budget`、独立容量探针/饱和 Token 凭据、`AnswerValidator`/`HardeningPolicy` 在线强制校验、旧自研 RetrievalRouter 仅作历史参考，不直接并入新产品主链。
- **保护旧系统**：原始文档、数据库/卷、版本/索引、Run/Attempt、引用、已结算与 UNKNOWN 账本、旧 Grant、Gold 和验收报告不可删除或覆盖。旧 V4 新 UNKNOWN 停止条件仍生效：未经负责人明确许可，绝不重新发送旧 Run/Attempt、释放 UNKNOWN 或发起新的真实云端请求。
- 不因源码对齐而擅自撤销已有真正的权限/隐私/费用保护；如不能与上游行为同时满足，先报告 `SECURITY_PARITY_CONFLICT`，说明最小变更，不得靠禁用测试/绕过防线解决。
- 未批准不 Push / Merge / Tag / Release / Deploy；不得修改当前 ECS/本机正在运行的 API/Worker、数据库、配置、密钥或数据卷；不得新增付费模型试验。
- 已知既有契约检查约 31 项分层违规：保留基线记录、标明 `PREEXISTING`，不能宣称全局 PASS，也不得为了此次工作大范围清旧债。

## G1. 先做源码证据，然后动手（仅第一次，各模块迭代不要反复全部审计）

1. `git status --short` / `git rev-parse HEAD` / `git log -1`，核对远端最新。检查之前暂停报告（`PAUSE_REVIEW_HANDOFF_20261013.md`、`LIVE_ACCEPTANCE_20261013.md`）中的**实际部署版本与旧历史结果**，不要把当前 HEAD 当成在运行的镜像。
2. 找到已有 WeKnora 固定版检出，若不存在，在独立目录取得相同 SHA，并验证 HEAD 和至少本轮相关源码 Git blob 身份；保持只读参照。记录其主项目 MIT 和第三方许可义务，实质复用时保留 attribution。
3. 生成一次性 `PARITY_MATRIX.md`：列 `feature/stage`、`WeKnora path:function + input/output/branch`、`existing Python path`、`new Python path`、`upstream effective config/precedence`、`evidence/test`、`deviation status`。优先列真实主链的模块，不需要穷举 WeKnora 4700 多文件。
4. 生成 `DATA_PRESERVATION.md`：盘点现有生产数据库和容器（只读）、对象存储卷、旧索引/原文/版本、Chat/Memory 数据、评测目录和仍为 UNKNOWN 的请求。目标数据库和 Redis prefix 独立命名，证明新实现不会覆盖旧数据。
5. 固定一份 `EXPECTED_BEHAVIOR_CASES`：从上游直接提取配置（注意 `config/config.yaml` 显式数值与 `types/retrieval_config.go` fallback 不同）、测试 fixture、事件和协议定义。未能实测的路径写 `NOT_VERIFIED`，不要猜。
6. 确保单独 migration 工作树/分支（命名自选，建议 `weknora-python-port`）；工作树本来脏时保留变更，不 stash/drop 不属于本任务的代码。

**G1 阶段提交文件：**`docs/weknora-port/P0_BASELINE.md`、`PARITY_MATRIX.md`、`DATA_PRESERVATION.md`、`ACCEPTED_DEVIATIONS.md`（初始版本）。随后实施，不因没有真实业务模型调用而停止全部编码。

## G2. Python 业务骨架与统一 Provider（P1）

**目的：**所有模型角色统一走 WeKnora 同等的 Provider 组合与协议配置，区别仅在选择具体模型。不要继续维护 Xing/DeepSeek 各自一套自定义 HTTP/限时/512/4096 能力门。

**上游源码至少读取：**
- `internal/models/api/openaicompletions/request.go`
- `internal/models/api/openaicompletions/client.go`
- `internal/models/api/transport.go`
- `internal/models/api/usage.go`
- `internal/models/providers/siliconflow.go`
- `internal/models/providers/deepseek.go`
- `internal/models/api/sse.go` 与 `stream_assembler.go`
- `internal/types/model.go`（ContextWindow 与 MaxOutputTokens 分开）

**实现：**
- 统一 `ChatModel`/`EmbeddingModel`/`RerankModel`/`VisionModel` 接口（名称可以调整）与单一 HTTP/流式 transport，provider compat 是模型配置，不是两种独立代码路径。
- `max_completion_tokens` 与供应商 `max_tokens` 字段按上游配置选择，不能两个同时发送。按上游模型目录和请求属性处理 Thinking、温度、流式 include_usage、缓存字段。
- 保持上下文截止时间与供应商读流/连接限制的职责一致；不能把历史 90/120 秒误当 WeKnora 固定协议默认，也不能只调大 timeout 而不实现流生命周期。
- `usage` 缺失保留 `UNKNOWN` 状态；不能伪造零 Token。日志保护模型 API Key、私有 Context、知识库原文。只允许配置好的 endpoint，不允许输出凭据。
- 旧 `output_capability_file` 与打满 4096 证明不是上游的模型调用前提，不接入新正常启动；模型配置基础校验、容量上限仍需有效。
- 独立最小安全包装只在实际外发前判断 scope/egress/cost/request identity；不能给上游 Prompt/Rerank/生成增加额外政策。

**测试：**同一条合成 chat 请求对比上游 Go 和 Python 的请求**语义字段**、流 chunk 解析、终止 reason、Tool delta、Usage、错误和取消。OpenAI-compatible、Xing 和 DeepSeek 使用模拟供应商协议 fixture；必要时使用本地 upstream Go 纯测试作为 oracle，不必申请真实云调用。至少一例流式长正文+未知 usage、一个错模型响应、一个断流、一个明显限时超时。`OFFLINE_PASS` 不等于供应商实际 PASS。

**禁止：**为了让 test green 重写冻结的旧历史数据，或把新候选直接部署生产。

## G3. DocReader/Chunker/数据库索引（P2）

**上游主要文件：**
- `docreader/parser/{pdf_parser,image_parser,excel_parser,docx_parser}.py` 及 `docreader/parser/registry.py`（按实际支持格式增补）
- `internal/application/service/knowledge_process.go`、`knowledge_process_config.go`
- `internal/infrastructure/chunker/{strategy,splitter,heading_splitter,heuristic_splitter}.go`
- `internal/application/service/retriever/keywords_vector_hybrid_indexer.go`
- `internal/application/repository/retriever/postgres/repository.go`

**实现：**
1. 解析抽象和不同文件类型调度按上游：文字 PDF、扫描 OCR、图片/Caption、表格保留行和 header 来源；编程语言自带库不同允许，但输入输出和失败处理要对齐。不引入与上游不同的自定义强制 TableRowProof 问答拒绝机制。
2. Splitter 按上游 `StrategyAuto / Heading / Heuristic / Recursive` 实际解析；Parent/Child 的选用、创建及关联条件要以源码为准。`parent_size=4096`、`child_size=384` 只是用户当前期望配置（需核对固定版接口及配置来源），**不允许把它们误称为 WeKnora 所有场景的固定内置默认**。
3. 存储模型要区分原文、抽取文本、chunk、向量索引、关键词索引、父块、文件附件、元数据、所属知识库和版本；新 PostgreSQL 独立库/migration，新原文卷。记录什么时候写入、什么时候查询、怎样约束用户权限。
4. 如果上游 PG 关键词检索使用 ParadeDB，而旧项目使用独立 `chunk_terms` BM25，必须标为 `SEARCH_ENGINE_PARITY_DIFFERENCE`。优先在隔离 PG 中复现上游引擎，再比较相同 tokenizer/query 的排序；不能以“同是 BM25”判定等价，也不在旧库装扩展。
5. 对索引创建/版本替换、去重、索引 ready 状态、失败重试使用上游语义。保留旧文件只读证明，不迁移在线业务资料。

**测试：**少量合成 PDF（文本/扫描）、图片、长表、Markdown、多层标题及跨 Parent/Child 边界。对同一配置比较 chunk 文本、offset、上下文头、Parent/Child identity、检索可见性、回退与空结果。必要外部 VLM/OCR API `NOT RUN` 并保留审批门。

## G4. 检索、Rerank、Merge、Query、Context（P3）

**上游至少读取：**
- `internal/types/retrieval_config.go`、`config/config.yaml`
- `internal/application/service/knowledgebase_search*.go`（尤其 `knowledgebase_search_fusion.go`）
- `internal/application/service/chat_pipeline/{query_understand,search,search_parallel,query_expansion,rerank,merge,merge_expand,merge_history,filter_top_k,into_chat_message}.go`
- `internal/application/service/session_knowledge_qa.go` 中真正的插件注册顺序

**特别注意这些已核实的差异：**
- `config/config.yaml` 中的显式检索值与 `types/retrieval_config.go` fallback 并不一样，要按有效配置入口计算，不是抄一组数字。RRF 平滑系数 60、默认权重向量 0.7/关键词 0.3 有源码，但只适用于取到该有效配置的场景。
- 上游混合检索有 **vector-only / keyword-only / hybrid** 三种融合分支；vector-only 保留原分，keyword-only 可重标度，hybrid 才使用 Weighted RRF。不要永远执行 RRF，也不要以旧 `RetrievalRouter` “普通语义默认 Vector、编号走 Hybrid”替代上游策略。
- `QueryUnderstand` 是否调用 LLM 根据 `EnableRewrite`、图片等实际条件；结构化解析失败可用上游原始查询退化，不能为消除成本额外新增新的自研规则。
- `search.go` 对 Query Expansion 有具体启用及检索结果数量门槛；`Rerank` 可以因模型缺失跳过，出现失败或无候选有上游的降级方案；`Merge` 含历史来源、Parent、邻近扩展和 FAQ 等，`FilterTopK` 在 Merge 后。
- Context 模板、历史引用、图片文本后备和保存 rendered context 要按固定源职责实现，不再默认保留 `ContextBuilder(max_chars=8000)` 的自研决策。

**测试：**相同合成库和配置验证关键词与向量候选、Weighted RRF 算分/排序、阈值、Rerank fallback、Parent 展开、相邻正文合并、最终 topK、Query rewrite 有/无、扩展触发/不触发、空 KB/no candidates、引用在 Context 的映射，比较可确定结构/顺序，不要求 LLM 自由文本逐字一致。

## G5. 会话、记忆、引用、SSE（P4/P6 的业务核心）

**上游至少读取：**
- `internal/application/service/chat_pipeline/{load_history,memory_recall,into_chat_message,chat_completion_stream,progress,references}.go`
- `internal/application/service/memory/{service,extract,search,vector,consolidate}.go`
- `internal/modelcontext/citations.go`
- `internal/stream/redis_manager.go`、`internal/handler/session/stream.go`
- `frontend/src/composables/useChatStreamHandler.ts`、`frontend/src/views/chat/components/RagPipelineProgress.vue`

**实现：**
- 长期记忆：按上游 owner/workspace scope、启用开关、自动提取、词法/向量回忆、归并、遗忘及用户管理机制；旧 `GLOBAL/SCOPED` schema 如语义不同，不可将旧记录直接自动注入新记忆。
- 短期历史和上下文窗口：遵循上游轮次、压缩、已有引用和当前问答组合；不得将历史回答自动当作当前知识证据。模型输入预算按新的 WeKnora provider 机制。
- 引用：使用源注册/句柄验证与受控公开格式；源码拒绝无效句柄，不能让模型构造任意外部引用，不再把旧 `[E1]`、`FinalAnswerCommitCheck` 逐字/数值规则当作唯一成功条件。
- SSE：统一阶段进度、answer/thought/tool/complete 语义、已有消息恢复、掉线和重复事件处理；事件恢复只续接当前 Run，**不重新 POST 生成**。历史消息持久化要有可见的版本/Scope 与安全的来源打开路径。
- React 呈现对应 Vue 的真实进度、引用、对话历史交互；避免大面积新 UI 改动，不引入 Vue 运行时。

**测试：**同一小型会话合成多轮、引用历史、用户记忆读/写/删除、两个 scope 隔离、长历史压缩、断线恢复/正常 EOF/取消、终态唯一性、引用打开回读；不得通过关闭权限保护换取 PASS。

## G6. 正式接入用户的动态路由与升级（P5）

**这是唯二业务差异，必须最后接入成熟的 WeKnora Python 主链，不能删，也不能作为旁路实验。**

### 决策规则锁定

- 将现有 `backend/app/application/{router_feature_extraction,rule_router_v1}.py` 中的**问题分类 + 六项 T/C/J/D/L/M 特征贡献 + 输入负载 + score threshold=0.35** 提取为薄的 `Router`。保留“Q0 与经校验 Rewrite 的特征贡献逐项取 max 而非求和”的历史规则，保留现有有证据的阈值、评分及四种模式 `OFF(WEKNORA_BASELINE) / CHEAP_ONLY / EXPENSIVE_ONLY / DYNAMIC`，旧历史决策回放不得无意变更。
- 当前普通 `bootstrap.py` 没有向 `LangChainQuickChain` 注入 `input_token_estimator`。新 Router 应使用已有、明确验证来源的本地估算器填入输入负载，不准把未知当 0，也不额外调用 LLM 预判复杂度。
- 路由只在最终 Context/prompt 准备后选择最终 Chat 模型；其他 WeKnora 业务阶段使用其规定配置与模型。
- Cheap/Expensive 统一使用 G2 的同一个 Provider/ChatStream interface，Vendor 参数仅通过配置不同。**不能创建 Cheap 专用 SSE、Expensive 专用非流式 HTTP 两条模型路径。**

### 升级规则锁定

| 信号 | 行为 | 默认说明 |
|---|---|---|
| Cheap 完整返回 stop 且基本协议/来源合法 | 不升级 | 沿用 WeKnora 用户可见答案语义 |
| Cheap 输出 `length` 等明确截断 | 允许一次升级 | 继承已开启的旧 `truncated_escalation` 普通策略 |
| Cheap 空答复、明确无效引用句柄 | 可按原 `quality_escalation` 升级 | 只使用客观可检测信号，不移植旧 Regex Validator 做升级依据 |
| Cheap 整句拒答 | 依历史 `abstention_escalation=false` 默认不升级 | 是否改变需用户另行批准 |
| Cheap 网络超时/HTTP5xx/供应商响应解析错误或服务异常 | 依历史 `provider_fallback=false` 默认不升级 | **不要偷改为 true**；标明这是用户后续可选决策。UNKNOWN 保守占用而非证明供应商未生成 |
| 身份错误/权限拒绝/请求取消/月度硬预算拒绝/知识库外发未同意 | 禁止升级 | 不是可用 Expensive 绕过的质量错误 |

- 最多一条 Cheap + 一条 Expensive 调用，且两个不同 attempt_id；对已有可能已发送的请求绝不盲目重发；若 Cheap 用量不确定，照账本的既定安全政策处理。每次升级需要重新检查 Expensive 外发权限、预算、模型可用性。
- Cheap 内容是否暂存及何时发布必须**明确标记为为升级能力所必需的上游差异**，不能既发布了失败 Cheap 全文又把 Expensive 当同一答案继续拼接。其用户体验和延迟要用 React 模拟检验。
- 旧 `AnswerValidator`、`QualityGate`、`HardeningPolicy`、`FinalAnswerCommitCheck` 不能作为新链强制真实语义判据；若要保留原文数值专项校验，仅在离线评测中运行，不能拒绝 WeKnora 基线的正常回答。
- 输出预算使用 WeKnora 的配置字段与模型能力信息，不再使用短题/普通题/长题关键词分档和饱和输出探针。旧“1024/2048/4096”历史验收只作历史事实，新链对外行为按上游配置；不自行引入 query-driven output budget。

**测试和数据输出：**相同 evidence/prompt，记录六特征、评分、路由、Cheap 失败类型、升级条件、最终选中角色、模型调用次数、输入输出 Token、延迟、使用费用估算来源；四模式可对照，非真值 cost 不冒充实际账单。`WEKNORA_BASELINE` 模式必须完全绕过自研 Router（除了非语义的安全封装），作为可独立回放的基线。

## G7. 离线评测与受限验收（P7）

1. 保留此前六个公开数据集全部可用适配。所有样本仍统一走文档入库→检索→Rerank→Context→生成→引用的完整链路；Gold 只计算确有标注的指标，不为缺答案 Gold 的检索数据集伪造官方问答准确率。
2. 建三组固定生成角色 + 动态路由成对配置；检索结果和上下文从同一次上游等价产物复用，避免把候选集差异当成路由收益。
3. 报告检索 Recall@K/MRR/nDCG（在相应 Gold 允许时）、Answer 质量、有效引用比例、时延（至少 p50/p95）、实际调用次数、升级触发原因和费用估计来源；无独立生成答案 Gold 就只报检索指标或明确标注独立人审样本。
4. 单轮日常测评争取控制在 40 分钟内；首次合法数据加载+索引控制在 4 小时内（如无法达到先优化批量/缓存，不去无限增大计算配额）。完成样本/模型预算说明后，另申请真实供应商测试批准。
5. 不因为开源项目实现相同就宣称对齐成功：要有程序行为证据、必要 DB 实测及独立结果；不能通过“只检查返回不为空”判 PASS。

## G8. 最终替换、生产迁移与冗余清理（P8；需另行审批）

- 在独立 PG/Redis/对象存储的影子环境运行 Python 新主链，记录镜像 SHA/代码 SHA/数据库迁移号/模型配置/索引身份。
- 需要负责人批准才能：真实模型探针/长题测试、任何付费请求、安装索引扩展到已有库、迁移/覆盖生产库、替换服务、清理业务数据、释放旧 UNKNOWN、Push/Tag/Release。
- **新主链功能验收和切换通过后，另一个独立 commit 清理旧代码**。重点核对：旧检索 Router、旧 QuickChain/Smart 编排、双套 `CloudAdapter`、专用饱和输出凭据、旧强制 Validator、临时 TaskFence、重复 SSE stage notices、历史兼容工具；按真实引用关系删除，不按文件名一删了之。
- **永不删除**：历史账本/UNKNOWN/Run/Attempt/Gold/原始文档/版本/引用证据/备份、旧 Git 可追溯历史。过时的实际运行文档要标“历史”，不能删除事实。
- 输出可回滚的部署及迁移步骤，给出撤回到旧镜像/旧数据卷的明确条件。不要在缺少迁移证据时声称全功能可用。

## G9. 验收矩阵（必须逐项落实）

| 测试案例 | 对等行为/通过标准 | 可用方式 |
|---|---|---|
| PDF 文本页 + 扫描页 | 同输入对应解析和 OCR 路由、元数据、错误分类 | 合成文件与上游 fixture |
| Parent/Child 无需父块 / 需要父块 | 父块创建条件、标题路径、索引中仅匹配有效子块 | 固定文本、两侧对照 |
| 普通语义 vs 精确编号 | 上游有效检索设置下候选路径一致，不偷偷启用旧 RetrievalRouter | 固定索引、比较 SQL/IDs |
| Hybrid 单/双通道 | vector-only 保持原分、keyword-only 规范分、hybrid Weighted RRF | 固定排名和数值测试 |
| Query Understand 开/关、解析失败 | 模型调用条件、q0 保留、回退一致 | mock provider |
| Rerank 得分低 / 失败 | 上游阈值、降级、TopK 在正确阶段应用 | mock rerank |
| Context + Memory | 正确历史轮次、父块合并、删除记忆后不出现 | 合成会话和隔离数据库 |
| Chat 上下文超限 / length / 空响应 | WeKnora 模型预算字段、流式终态、可见状态 | mock SSE；真实供应商另批 |
| 用户断线/刷新恢复 | 同一原始 Run 的事件序列，不生成新请求；引用可打开 | React/HTTP integration |
| P5 高/低评分及 UNKNOWN 输入负载 | 阈值、模型角色、六项得分如冻结代码；真实 estimator 非伪造 | frozen router cases |
| Cheap 正常、无效引用、截断、超时、权限拒绝 | 按 G6 唯一升级决策表执行；最多 2 attempt；费用防线有效 | mock provider + 持久化回读 |
| 禁用路由 baseline vs DYNAMIC | 除模型选择与已批准协调，不影响检索/Context/来源 | 成对 E2E |
| 六公开数据集 | Gold 可评才评；同一主链；不虚报生成准确率 | 公开 fixture/预算受限评测 |

任一关键路径不可验证：`BLOCKED`，注明具体依赖、可离线继续的其他工作和最小下一动作；不要用大量重复回归掩盖当前阻塞。

## G10. 汇报格式与提交规范

每阶段结尾更新 `docs/weknora-port/STATUS.md` 和相应阶段报告，报告只要能让负责人复核：

1. **目标/实际更改**：本阶段解决哪些“与上游不同”的行为，函数/文件差异清单。
2. **WeKnora 原始证据**：固定 `path:function`、有效配置来源、相关源码段及同输入的观察；可重放的 fixture。
3. **Python 结果**：命令、环境、退出码、PASS 数、skip 数、失败用例、任何回归/旧有失败的因果边界。Windows Mock 与 Linux 真实运行必须分开。
4. **异常与自研差异**：若新增非 Go/Python 框架差异，列 `ACCEPTED_DEVIATIONS.md`，是否用户已批准；非安全上游业务差异无批准视为 FAIL。
5. **数据/费用/身份**：证明真实线上未改、未调用模型；或已授权批次的真实 Run、费用/UNKNOWN/部署 SHA，不混淆源码 HEAD 与容器镜像。
6. **下一阶段或需授权点**：没有待决安全审批则继续离线开发；遇到付款、旧环境修改、生产迁移等须停在操作边界并说明影响。
7. **提交**：实施阶段可本地 commit；一阶段一可回滚提交，无 Push、Tag、Release。绝不通过删除保护、取消原有断言或重写历史证据使测试“转绿”。

**最终 DONE 定义**：在隔离数据环境中完成 WeKnora Python RAG + 历史/长期记忆完整业务流程，主链包含用户的 P5 动态路由和 Cheap→Expensive 受控升级，评测中心保留；路由关闭/开启行为、真实引用回读、SSE 恢复、相应测试全部按真实状态可核对。未经授权的真实收费请求或正式切换不要求擅自完成，只能写 `AWAITING_OWNER_APPROVAL`；不得以 `OFFLINE_PASS` 取代 `REAL_PROVIDER_PASS`。

### 开始执行

先执行 G1（第一次 P0 源码/运行/保护核对），随后在隔离工作树自动继续 G2–G7 的**离线及隔离数据库代码实现和必要定向验证**；旧环境仍只读。不要对已解决的旧 512/2048 修复再开启排查循环，也不要再制作一个“参考 WeKnora 的自研 RAG”。需要负责人批准的昂贵或不可逆操作一律停下请求。
