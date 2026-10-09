# P5.5-B1 — Context Window、短期历史与 Smart Compaction

状态：**CHECKS_PASSED / NEEDS_OWNER_REVIEW**。这表示最终代码的离线及测试专用 PostgreSQL＋Redis检查通过，不表示生产模型准入、业务迁移或阶段验收获批。生产上下文/压缩准入仍为 **BLOCKED**。

## 1. 基线与范围

- HEAD：`e6cca0971d0f1875ca562a6b953fd5ec252b28e0`；分支：`codex/local-first-rag-v1-20260930`。
- 开始时42个dirty文件；原始字节、Git状态和旧证据哈希保存在 `var/reports/p55b-context-r1/start-snapshot.json` 与 `before/`。P5.5-A最终323份Python源码逐项核对，结果见 `p55a-source-baseline-check.json`。
- 收口时发现并行新增 `docs/evaluation/P7_ECS_NIGHT_PREFLIGHT.md`，不是本轮创建或修改。保留其原文件、只记录哈希和Git状态，明确排除出B1增量diff/修改清单；相关源码及最终测试快照没有并行漂移。不宣称整个工作树是同一时刻的独占快照。当前54个dirty路径中，本轮明确归属17个，另有该外部新增文档。
- 本轮仅涉及历史、上下文预算、独立压缩持久化、实际Smart异常终态及必要测试/报告。准确文件清单、前后SHA-256和增量差异见 `final-manifest.json`、`task-only.diff`。未改Router贡献值/0.35阈值、P4检索/重排/父块/引用、模型名单、旧Gate或历史Migration。
- 未commit、stage、push、部署或Tag；未执行商业API、业务库迁移或索引重建；没有新增子代理。实际服务模型/effort无法由控制平面证明，记UNKNOWN；执行监督级别为MANUALLY_SUPERVISED_TRIAL。
- `progress.md`仍含旧main/M4.5记录，本轮以Owner指定分支、实际HEAD及P5.5-A交接为基线，未改写旧进度。

## 2. 固定上游证据

固定commit `3e8b0bfc80b845b2d4b2ed683994748741450a97`，tree `9533ab2071e71f4bc2ebb09c85d3ac246841ff9a`。七份实际源码已从GitHub读取，保存到 `D:/RAG-task-downloads/p55b-weknora-3e8b0bf/`，按 `SHA1("blob <length>\0" + bytes)` 对照固定tree验证，7/7一致；SHA-256、长度、Blob完整值见 `upstream-manifest.json`。未改本机WeKnora权限、TLS或上游版本。

| 源码 | 借鉴与本项目差异 |
|---|---|
| `internal/application/service/chat_pipeline/load_history.go:32 OnEvent` | 读取配置的MaxRounds；0表示禁用。固定插件没有硬编码5；本项目默认5来自Owner要求，构造参数允许1–64轮，未声称上游插件固定为5。 |
| `internal/application/service/agent_history.go LoadAgentHistory` | PG为历史权威、完整轮次、时间排序、Checkpoint边界及工具协议重建。本项目复用原合法历史入口，不引入第三个reader。 |
| `internal/agent/compaction/compactor.go Compact/validateSummary` | 独立压缩及finish/truncation判断。本轮不采用失败raw archive冒充成功Checkpoint，也不采用自动多次模型尝试。 |
| `internal/agent/compaction/prepare.go Apply/SummaryMessage` | 摘要是user级历史数据，系统指令和保留尾部独立。本项目额外固定当前问题并保护本轮原子证据工具组。 |
| `internal/agent/compaction/cutpoint.go FindCutPoint` | 工具调用与结果不能切开。本项目按完整组处理，原始协议持续保存在PG。 |
| `internal/agent/token/estimator.go EstimateMessage/EstimateTools` | 计入reasoning、调用/结果、工具定义和协议负载；上游cl100k近似与Usage校准不是本项目精确tokenizer证明。 |
| `internal/types/context_checkpoint.go ContextCheckpoint` | 摘要覆盖边界持久化、下轮恢复。本项目采用独立表与源Run/Scope/版本引用绑定，保留原始消息和证据。 |

## 3. 最终上下文链

### Quick

`AnswerService → LangChainQuickChain._generate → ContextManager.quick_prompt → completed_history_context(purpose=context) → ModelWindow.check → 既有GenerationRole/Provider/预算/最终校验`。

`backend/app/adapters/postgres/knowledge_repository.py:1302` 与 `backend/app/ports/persistence.py:bounded_completed_history` 只选择当前Run创建时间之前已完成、无错误、具备成功answer.completed事件及恰好一对User/Assistant消息的轮次；User原文必须等于q0。Conversation与授权KB/Document范围一致，范围切换/当时pending保留原阻断规则。引用按已提交事件顺序读取，检查真实version/chunk、quote哈希及原文回读。Scope数组在上下文用途按集合校验，旧follow-up用途保留原精确数组和LIMIT16合同。

默认最近5个完整轮次，按原始顺序放入明确标记的低优先级历史数据。根据可信协议计数从最新完整轮次向前选择，不截断问答、当前RAG证据或引用。历史回答不进入检索索引、EvidenceSnapshot或本轮允许引用集合。合法历史不足时使用当前单轮输入；无可信模型准入时在读新历史表之前明确拒绝。

Quick沿用P5共享的生成Envelope：历史选择同时检查可能生成角色各自窗口，保证质量升级复用同一问题/证据/历史输入。它是保守的容量交集策略，未将两个模型容量合并成同一虚构值，也未改变分类贡献、阈值或L=UNKNOWN。若未来需要不同角色装配不同历史，应先复核该Envelope合同。

受控追问和一般resolver继续共用 `completed_history_context` 的原follow_up用途；既有last_*兼容接口未在本轮重构。

### Smart

`LangChainAgentAdapter.run → ContextManager.smart_messages → PG合法历史＋有效Checkpoint → create_agent → before_model.fit_live → 实际工具执行/原协议PG保存 → 原EvidenceAccumulator/AnswerValidator/最终RAG与Agent联合提交`。

`backend/app/application/langchain_agent.py:165` 在每次调用前检查最终消息、工具定义与输出预留。实际LangChain middleware使用已安装SDK支持的RemoveMessage/REMOVE_ALL_MESSAGES替换上下文；工具结果与assistant调用完整配对。原始工具/推理协议逐步提交到PG，重复tool id须字节等价，不能用后来的缓存覆盖；历史tool id以原Run稳定命名空间重放，原始持久字段保持不变。

初始历史按窗口装配；超限时保留最近完整轮次，压缩旧轮次。一次长工具运行内部也可触发LIVE压缩：保留当前问题、最新完整工具组，以及search/read/graph的原子证据组；只压缩可安全折叠的数据。完整证据仍放不下时拒绝，不能截断表格/quote/SourceLocator来通过。

SESSION摘要恢复需要同Conversation/Scope、完整源轮次哈希、创建/完成时间及证据版本身份一致；已覆盖轮次从后续上下文移除。LIVE摘要只属本Run，不作为下轮会话Checkpoint。成功持久化的独立SESSION压缩可以在其生产Run随后答案失败时保留，下一轮仍重新验证覆盖的成功源问答；失败/截断/未持久化的摘要不能恢复。

当前安全读取上限为2048个Run、最多512个完整轮次；这是有界IO保护，窗口装配仍按模型协议计数。未宣称无限历史覆盖或长会话压测完成；覆盖来源超出当前读取范围或哈希不一致时拒绝Checkpoint复用。原始历史不会因此删除。

## 4. Token容量及真实准入

`backend/app/ports/context_budget.py:30 ModelWindow` 分别绑定provider/model、窗口来源、tokenizer来源、完整协议counter、安全余量及输出预留。

`输入协议Token + 输出Token预留 + 安全余量 <= 模型窗口`。

Quick最终user wire包含原系统指令、当前问题和RAG Context；Smart包含system、历史/Checkpoint、工具定义、调用/结果、reasoning等协议字段。Counter必须处理完整实际协议。任何未知容量/来源、缺counter、非法计数或超限均拒绝；不采用字符换算作为生产Token，也未采用200000兜底。

| 角色 | 当前证据与状态 |
|---|---|
| Cheap / SiliconFlow / `XingChenAGI/Xing4.0-29B` | [官方通用文档](https://docs.siliconflow.cn/docs/userguide/capabilities/text-generation)要求按具体模型查看context_length；模型原生论文/仓库窗口不能代替该供应商精确别名、协议及tokenizer准入。有效窗口/counter仍UNKNOWN。 |
| Expensive / DeepSeek / `deepseek-flash` | [官方Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/?tab=case-studies)当前说明Flash对应V4.1-Flash并标示1M窗口；[Token Usage](https://api-docs.deepseek.com/quick_start/token_usage/)明确离线计数仍为估计、Usage为实际依据。本轮未验证当前模型revision、1M的精确整数边界与完整reasoning/tool模板tokenizer，不将其直接转换为获准计数器。 |
| 既有Smart本地模型 | 原配置num_ctx不是精确tokenizer证明；同样不复制成可信计数。真实准入UNKNOWN。 |

官方页面首次直接读取的超时保留为UNKNOWN；后续公开文档检索得到的上述部分证据没有改变模型/价格/外发配置。未下载模型/tokenizer。默认composition-root的三个实际模型Window均关闭；P5原真实角色授权限制继续有效。

测试中的窗口与JSON scalar-unit counter明确为SIMULATED，代表合成协议单位，不代表真实Chat Tokens。Usage测试中的10/6是模拟Provider返回，不代表任何真实模型用量或已经校准的最佳估算。真实Usage校准/完整tokenizer准入仍为生产阻断。

## 5. Compaction状态、持久化及成本

新增 `0019_context_checkpoints.py`，down_revision为0018；0001–0018不改。三表：

- `context_compaction_attempts`：独立context_compaction purpose、稳定hash主键、Run/Conversation/Owner/Scope/模型、covered源身份、SESSION/LIVE、状态、预算FK、诊断。没有放宽正文Attempt两个角色/ordinal约束。
- `context_checkpoints`：成功Attempt FK、原会话/Run/Scope、covered完整轮次及时间/版本/quote/locator身份、摘要/哈希、schema_version与提交时间。
- `context_run_protocol`：Run FK、原始工具/推理协议与内容哈希；仅成功RAG问答可作为下轮历史消费。Redis不承担这些权威存储。

`claim唯一身份 → 正常PostgresBudgetGate预占 → 验证预算行purpose/Run/provider/model → Owner/lease发送前fence → 单次SIMULATED Provider → 检查finish/非空/有效压缩 → 保守UNKNOWN结算 → Attempt完成 → 独立Checkpoint提交`。

发送前可确认拒绝：NOT_SENT，仅释放本次明确未发送预占。可能已调用：UNKNOWN，不退款、不再发。模型成功但Checkpoint保存失败：模型Attempt继续COMPLETED，保存错误另记，预算UNKNOWN，Checkpoint不存在。失败状态或已消费hash不能自动生成新身份重试。

原PostgresBudgetGate支持独立purpose；原受信DeepSeek Product Gate仍绑定冻结Quick请求/prompt/output，不支持借该授权发送压缩。生产compactor=None，模拟实现只接受明确SIMULATED Provider；没有新增真实压缩grant、可用API入口或关闭既有Gate。

测试库只读收口：11个独立Attempt（4 COMPLETED、7 UNKNOWN），8行模拟预算、56 microunits保守UNKNOWN演练占用，2个成功Checkpoint、12个Run协议记录。三条并发claim未预占/未发送，不能把所有UNKNOWN都计为实际发送。商业API调用0、实际商业费用未产生；演练账本的UNKNOWN仍保留，不能伪称实际供应商结算。

## 6. Smart异常终态

`backend/app/adapters/postgres/run_lifecycle.py:128 fail_run` 保持RAG→lease→Agent原锁序与精确Owner限制。同一事务将本逻辑RAG Run及仍running的同ID Agent Run记为failed/RUN_EXECUTION_FAILED；该Run的在途正文/压缩Attempt保守转UNKNOWN。原费用、model_calls、历史Attempt与证据不删除/退款，其他Owner/Run不关闭。

旧0018部署下没有context表时仍能执行原失败终态保护；只有新表存在才更新独立压缩Attempt。实际隔离反例在Agent已创建且RAG.finalize_answer抛异常的边界验证RAG failed、Agent failed、lease FAILED。

## 7. 实际命令与测试

所有命令工作目录 `E:/RAG quention`，解释器 `.venv/Scripts/python.exe`（Python3.13.0/pytest9.1.1）。沿用正常批准的受保护执行方式，不改ACL、保护设置或全局环境。最终源码未在测试过程中漂移。

| attempt | PASS | FAIL | ERROR | SKIP | 进程/pytest退出码 |
|---|---:|---:|---:|---:|---|
| offline首轮 `p55b-context-checks-1` | 94 | 1 | 0 | 0 | 1/1 |
| PG首轮 `pg-checks-1` | 6 | 4 | 0 | 0 | 1/1 |
| 中间离线 `p55b-context-final-1` | 402 | 0 | 0 | 0 | 0/0 |
| 中间PG `pg-final-1` | 44 | 0 | 0 | 0 | 0/0 |
| 实际LangChain长工具补充用例 | 1 | 0 | 0 | 0 | 0/0 |
| **最终离线 `p55b-context-final-2`** | **404** | **0** | **0** | **0** | **0/0** |
| **最终PG＋Redis `pg-final-2`** | **44** | **0** | **0** | **0** | **0/0** |

最后两轮覆盖此前补充用例及UNKNOWN准入先于新表读取保护，绑定最终全部被测源文件哈希；不使用前轮402 PASS替代最终证据。PG44=新增B1 10＋原P5.5-A 34，模型全部SIMULATED。

最终离线命令：

```powershell
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py p55b-context-final-2 backend/tests/test_p55b_context_window.py backend/tests/test_bounded_history.py backend/tests/test_langchain_agent.py backend/tests/test_quick_chain_budget.py backend/tests/test_p5_router_features.py backend/tests/test_p5_router_boundaries.py backend/tests/test_p5_router_generation.py backend/tests/test_p5_router_replay.py backend/tests/test_answer_service.py backend/tests/test_agent_trace_sse.py backend/tests/test_agent_limits.py backend/tests/test_agent_tool_allowlist.py backend/tests/test_budget_gate.py backend/tests/test_final_answer_commit.py backend/tests/test_context_and_locators.py backend/tests/test_smart_citation_replay.py backend/tests/test_quick_citation_prompt.py backend/tests/test_model_provider_contracts.py backend/tests/test_bootstrap.py
```

最终真实隔离命令：

```powershell
.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55b_context_isolated.py pg-final-2 backend/tests/test_p55b_context_integration.py backend/tests/test_p55a_lifecycle_integration.py
```

Schema实际身份：127.0.0.1:52352，p55a_lifecycle_test，OID16384，system identifier7694635764170571814；Redis仅52355。pg-checks-1身份检查后应用0019；最终再次确认0019及旧Chat约束未改。没有连接Clean-slate业务开发库25438或旧业务库。实际Schema前后定义见 `pg-checks-1/schema.json`，最终见 `pg-final-2/schema.json`。

首轮离线失败保留原 `test_postgres_snapshot_queries_are_anchored_and_read_only` 断言，修正为旧follow_up分支仍LIMIT16。四项PG失败均因合成历史实际长度低于测试窗口而没触发压缩，按照确定的协议长度修正SIMULATED窗口；没有删断言、Skip或改生产容量。最后收口补入UNKNOWN窗口先拒绝再读历史的保护，因此重新执行必要完整回归。

测试覆盖：完整问答/引用、跨Conversation/KB/document隔离、窗口选择、SESSION/LIVE压缩、真实PG持久化及跨Run重建、不重复覆盖、截断/异常/保存失败、工具配对/原协议保全、双窗口、Smart异常终态、原始quote/version/locator、独立Usage/UNKNOWN/预算准入与并发唯一claim。

辅助命令：公开源码导出/Blob核验退出0；AST122份PASS；默认git diff --check退出0；只读账本收口退出0。账本辅助脚本首次移位时ROOT层级错误，退出1且连接前停止；记录在 `accounting-readback-first-failure.json`，修正后的证据 `accounting-readback.json` 保留。Manifest辅助脚本首次假定原文件统一LF/CRLF而退出1；实际原model_usage.py使用混合换行，仅复原本轮三处编辑位置后匹配P5.5-A原始SHA且规范化内容等于HEAD，未调整工作树换行来通过。记录在 `closeout-first-failure.json`、`baseline-recovery.json`。未以权限绕过处理。

测试完整argv、实际退出码、失败名称、计数、JUnit哈希与源码前后哈希统一在 `test-results.json`。原始JUnit与执行记录全部保留。依赖升级影响未独立验证。

最后范围辅助检查初次把“报告提及排除的P7文件”误判为“diff包含P7文件”，退出1；改为核验统一diff文件头后通过，未改生产代码/测试。原记录 `final-scope-first-failure.json` 与正确核验 `final-scope-check.json` 均保留。

## 8. 遗留与Owner待决

1. **生产BLOCKED**：Cheap精确供应商窗口、两个模型完整tokenizer/协议、实际Usage校准及模型revision准入尚未完整证实；禁止把SIMULATED计数作为真实容量。
2. **生产BLOCKED**：独立context_compaction的真实外发scope、ProviderFactory/AuthorizedTransport接线、受信Grant与收费预算需Owner另行授权。本轮不以已有Answer Attempt授权代替。
3. **生产BLOCKED**：业务/开发库0019上线迁移及版本配置检查未执行，需独立批准；当前只有测试库应用。不得从隔离Schema通过推断业务Schema已升级。
4. Smart仍使用既有本地LangChain模型入口，本轮只增上下文能力，未新增Smart云端角色路由。Quick使用共享Envelope的保守窗口交集，角色独立装配需后续合同复核。
5. 超长会话有界分页边界和真实模型服务重启/长工具调用未做商业实测；本轮实际LangChain与PG重建均使用SIMULATED Provider，不是供应商验证。
6. 两项旧Gate失败本轮NOT RUN；P5.5-A已保存的限定顺序复现PASS及原始根因UNKNOWN均保留，未改其断言/收据。Layering静态检查仍是已知三条原有违规，无新增；不修复无关local_caption/preview问题。浏览器端Playwright仍NOT RUN，未声称浏览器通过。
7. 本轮回滚为Owner审查 `task-only.diff` 后选择性撤销B1增量，不能reset整个dirty树。0019降级发现持久历史时明确拒绝删除，不能通过清库回滚。没有授权真实数据库回滚动作。

## 9. 后续接口交接

- 保留完整conversation_messages及Run关联；rag_runs、retrieval_events、EvidenceSnapshot/Citation/Version保持原权威身份和回读。
- 可复用 `completed_history_context(purpose=context)`、`ModelWindow`完整协议counter契约、`ContextManager`两种装配入口、`PostgresContextStore`与独立purpose状态机。
- Redis仅活动标记/短期事件，PG消息、协议和Checkpoint不以Redis TTL为寿命。
- 跨会话长期记忆、Query Understand、BM25、Query Expansion仍属后续必做阶段，本轮均NOT IMPLEMENTED；没有将它们取消或并入Redis。
- 下一动作仅为Owner复核本报告、增量diff、最终manifest与原始JUnit；本轮停止，不创建commit、不自行进入长期记忆阶段。
