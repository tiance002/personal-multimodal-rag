# 知识库问答项目：学习入口与固定快照

本文件记录 2026-10-02 的教学快照。它保存当前实现，供阅读、比较和讲解；不是正式发布，不代表全系统测试通过。没有部署或迁移数据库，也没有建立发布 tag。

## 先读哪些代码

| 层次 | 入口 | 负责什么 |
|---|---|---|
| 前端 | `frontend/src/app/App.tsx`、`frontend/src/components/ChatPanel.tsx` | 会话、知识库/文档范围、提问、历史及引用点击 |
| HTTP | `backend/app/api/routes.py` | 请求校验、会话/消息/运行/引用接口 |
| 组合根 | `backend/app/bootstrap.py`、`backend/app/main.py` | 创建各适配器并注入服务，避免业务代码直接拼数据库或模型 |
| 问答服务 | `backend/app/application/answer_service.py` | 运行生命周期、范围、取消、完成及持久化 |
| 快速问答 | `backend/app/application/quick_chain.py` | LangChain Runnable 固定执行：准备证据、生成、校验 |
| 共享 RAG 核心 | `backend/app/application/knowledge_gateway.py` | QueryPlan、检索、证据覆盖、上下文与最终答案审核 |
| 智能模式 | `backend/app/application/langchain_agent.py`、`knowledge_tools.py` | LangChain Agent 及受范围/预算限制的内置只读工具 |
| 存储与摄取 | `backend/app/adapters/postgres/knowledge_repository.py`、`application/ingestion.py` | 文档版本、任务领取/租约、索引与问答记录 |
| 解析与切块 | `backend/app/adapters/parsers/`、`backend/app/domain/chunking.py` | 原始文件转结构化资料，再生成可回溯的块 |

读代码时区分“业务规则”和“适配器”：前者决定是否有充分证据，后者连接模型、文件或数据库。一个模块存在，不等于它已获准调用外部服务。

## 一次 Quick 请求如何走完

1. 前端通过 `frontend/src/api/client.ts` 提交消息及预期范围。服务端校验会话实际 KB/document 范围；不能只信浏览器传来的文档 ID。
2. `AnswerService` 创建运行，保存原问题 q0，协调检索与生成。已有模型理解路径可以提供独立检索解释；q0 和来源范围仍保持可追踪。
3. `LangChainQuickChain._prepare` 调用 `KnowledgeGateway.retrieve_question`。`QueryRouter` 拆分明确主体、字段、月份；`HybridRetriever` 在有效文档版本及范围内检索。
4. `EvidenceService.bundle` 和 `QualityGate` 检查证据覆盖。表格金额使用 `structured_evidence.row_facts`：同一来源表格的表头、行、列、单位和数值必须对应。相似度高只表示候选相关，不能代替事实支持。
5. `ContextBuilder` 选择支持块，`CitationService` 冻结 E1 等标签及原始 quote/locator/version。模型看到的是这些证据。
6. `_generate` 调用已允许的回答网关；`_validate` 再经 `EvidenceService.finalize_answer`、`AnswerValidator` 和 `HardeningPolicy` 检查实际输出。通过检索前证据门不代表生成后的答案一定正确。
7. 校验通过且运行仍可完成时才保存 assistant、`answer.completed` 和使用到的 `answer_evidence`。引用接口按运行和标签回读冻结来源；历史刷新从持久化结果加载。

## 已解决问题：用症状定位具体层

| 症状 | 根因 | 修复入口 | 验证与边界 |
|---|---|---|---|
| 月度表格已召回，但回答前报 `INSUFFICIENT_EVIDENCE` | 主体和月份拼接，旧正文规则要求主体/字段/数字同句，无法理解分列表格 | `query_router.py` 独立月份；`structured_evidence.py` 严格同行/表头见证；`quality.py`、`answer_validation.py` 共用 | 既有 64 项离线回归通过；未知表头、错行列、冲突值、跨来源拆拼仍拒绝 |
| 表格证据已支持，模型正确输出却报 `UNSUPPORTED_ANSWER` | 问题中主体与月份连续，输出在两者之间加空格，生成后金额匹配失配 | `subject_period_pattern` 由证据匹配和金额校验共用，只允许两者之间有限横向空白 | 148 项相关离线回归通过；四份保存输出通过完整 Quick 链路，保留原始引用身份 |
| 引用或消息的较晚响应覆盖了用户新选择 | 页面/会话/范围已切换，旧异步请求仍可能返回 | `frontend/src/app/App.tsx` 的请求身份与失效控制，相关 stale-response 测试 | 已有修复与测试纳入快照；本次推送不重新声称所有浏览器用例通过 |

2026-10-02 修复后，已有合成资料的 **TXT、MD、HTML、XLSX 各一次真实问答均通过**：答案 312 千元，E1 指向当前文档版本的原始对应行；即时引用点击、刷新后的历史答案/引用、历史引用再次打开及数据库持久化均通过。每个格式各用一次回答模型及一次查询嵌入，无重传或自动重试。旧失败记录保留，新成功作为独立运行记录；“四题通过”不等于所有资料和问题都已通过。

相关回归入口：`backend/tests/test_structured_evidence.py`、`test_structured_answer_spacing.py`。测试使用合成来源、冻结响应及内存替代网关；离线重放和真实模型验证必须分别解释。

## 尚未闭合的问题

**PDF/DOCX 的可信表头路径。** 原合成文件实际有完整 3×3 表格，列为门店/月份/带单位的金额；文字与行列并没有丢失。PDF 的 PyMuPDF 表头是启发式结果，当前适配器把语义标为 UNKNOWN；DOCX 没有 `w:tblHeader`，`w:tblLook.firstRow` 只是样式开关。二者在 parser/chunker 中保留 `column_header=null`、未知表头及 partial 状态。

当前 `row_facts` 的结构化接受分支还仅支持 HTML/XLSX 和同块完整 TSV；PDF/DOCX 是产品尚未支持的表头确认/证据适配能力。源文件没有声明不等于永远不能解析，但不能仅把 partial 改 complete、猜第一行或增加格式名单来放行。最小正确方向是可核验的表头确认规则或明确来源声明，并让确认依据、对应表/行/列/单位贯穿解析→切块→引用→校验。原文件离线重解析仍正确拒答；未用旧数据库内容证明新解析生效。

**追问测试。** 当前 `resolve_follow_up` 是保留 q0、不自动拼接历史的兼容入口。旧测试期待“上一轮背景/只回答当前问题”包装文本；快照中的两个测试文件已按现兼容契约调整，增加 q0/clarify/无历史注入断言。q0 不可变不等于产品禁止所有有来源的历史辅助理解；完整追问需求是否满足仍需独立确认。

后续聚合测试未取得有效计数：本地离线测试运行器在 pytest 写 JUnit 时阻止了用于主机名探测的 Windows `ver` 命令。此前 148 PASS 是空间匹配修复的已保存结果；不能用它替代后续聚合。未调整进程白名单或重跑被阻止的测试。准备阶段曾新增一个 `used` 变量未绑定错误，本教学快照仅修正该绑定，并做静态核对；原开发工作树保留现场，聚合仍 **NOT CONFIRMED**。

既有 32 题质量实验的完整质量门从 20/32 到 19/32，历史报告未证明整体提升。这里仅保留脱敏结论，未纳入原始用户材料、模型响应、私有日志或逐题来源身份。

## 出错时怎样排查

- **找不到证据**：先核对服务端范围、active version、索引状态和实际候选，再看 `missing_targets`。不要先降低阈值或省略月份。
- **有证据仍拒答**：保存实际候选、原始 quote/locator 和具体错误码，区分准备阶段与生成后校验；用冻结来源离线复现，加入能击中错误行列/单位/引用的负例。
- **引用错配或刷新丢失**：对比 run_id、label、chunk_id、version_id、quote hash；分别检查浏览器请求身份和持久化完成事件。引用可回读只证明身份，不自动证明语义支持。
- **解析为 partial**：读 parser warnings 和原始单元格来源；数据完整但表头语义 UNKNOWN 仍须保守处理，不能把状态字段当开关改掉。
- **测试运行器失败**：区分断言失败、收集失败和报告收尾失败。没有有效计数就记录未确认；不要用增加权限或扩大外部进程访问来掩盖问题。

## 本教学快照的检查与使用

推送前只执行静态检查：Python 源码语法/编译、JSON/TOML 格式、TypeScript 类型、暂存差异及敏感文件边界。不执行模型/API、数据库、部署或被阻止的聚合测试。实际检查结果记录在 `docs/teaching-snapshot-checks-20261002.md`。

建议教学顺序：先解释一次 Quick 请求，再追踪表格证据与引用，最后讲范围隔离、失败拒答和取消/持久化。先阅读 `.env.example` 与 README 的模板；真实凭据和本机运行数据不在仓库。该快照固定在独立 Git 分支；后续开发与教学阅读应各自明确 commit 身份。
