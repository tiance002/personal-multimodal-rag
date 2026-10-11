# P5.5-D — 检索增强开发报告（BLOCKED）

本轮 BM25、确定性 Query Expansion、Smart 分段阅读已实现，新增定向离线 31 PASS、真实隔离 PG 12 PASS。但关联回归 226 PASS、7 FAIL；这 7 项在开始源码快照下全部 PASS。**本轮不能报告 P5.5-D 通过，不能生产启用。** 失败证据与代码保留，等待 Owner 对后续限定修正作决定。

## 1. 基线、权限与保护

- HEAD：`befd5478a680ffa779a1e8ede736260d9bac1060`；分支：`codex/local-first-rag-v1-20260930`，未 commit/push/tag/部署。
- 开始 32 个 dirty 路径，C/B2.1 与前轮 manifest 匹配；完整开始快照：`var/reports/p55d-retrieval-r1/start-snapshot.json`，原字节在 `before/`。最终保护结果与精确新增/修改清单见同目录 `final-manifest.json`。
- 仅新增本任务代码、测试、冻结合成语料与本报告；未修改 Migration、P3 参数、P5 分值/0.35 阈值、模型名单、价格、Gate 或原失败断言。保留 P7/Langfuse 和原有成果。
- 未读取凭据、业务库；真实商业/本地模型请求 0；未重建业务索引。新测试的合成业务数据在事务结束回滚，12 份 `rollback-counts-*.json` 显示 public 全表计数前后一致。
- 实际模型/effort 由工具不能确认：UNKNOWN；未调度代理；执行为 MANUALLY_SUPERVISED_TRIAL。

## 2. 固定上游与方法边界

WeKnora 固定 commit：`3e8b0bfc80b845b2d4b2ed683994748741450a97`。三份源码经 GitHub 固定 tree/file 读取、完整 Git Blob SHA 与 SHA-256 核验，下载只保存在 `D:/RAG-task-downloads/p55d-weknora-3e8b0bf/`；完整路径与哈希在 `upstream-manifest.json`。

- [query_expansion.go](https://github.com/Tencent/WeKnora/blob/3e8b0bfc80b845b2d4b2ed683994748741450a97/internal/application/service/chat_pipeline/query_expansion.go)：参考低召回门、原问题投影及独立候选记录；本项目采用最多 2 个词法投影，一次扩展阶段、一次全局 Rerank，不复制上游多目标并发或更大的默认池。
- [read_document.go](https://github.com/Tencent/WeKnora/blob/3e8b0bfc80b845b2d4b2ed683994748741450a97/internal/agent/tools/read_document.go)：参考位置继续阅读和有界返回。上游 offset 为 Chunk 位置，默认 20/最大 100 个 Chunk；本任务要求合法字符 offset，本项目明确使用 Child 顺序阅读投影的字符位置，并保持原始 SourceLocator 独立。没有静默更换上游版本或搜索参数。
- [PostgreSQL repository.go](https://github.com/Tencent/WeKnora/blob/3e8b0bfc80b845b2d4b2ed683994748741450a97/internal/application/repository/retriever/postgres/repository.go)：检查关键词接线，不将其全文排名称为 BM25。BM25 为本项目的独立公式实现。[Stanford IR-book BM25 说明](https://nlp.stanford.edu/IR-book/html/htmledition/okapi-bm25-a-non-binary-model-1.html)提供 TF 饱和与长度归一化依据。本项目使用正值 log1p IDF 变体。

本机 `E:/WeKnora` Git 读取被拒绝后未重试、改 ACL 或禁用 TLS；使用独立已授权 GitHub 读取。上游身份与本机部署身份没有混同。

## 3. Stage 0：实际检索链与固定基线

`Quick / KnowledgeToolGateway.search_knowledge → KnowledgeGateway → HybridRetriever → Scope/活动版本/Child/index 绑定的 Keyword 与 Dense → weighted RRF → Rerank → Parent/Neighbor Merge → Top-K → ContextBuilder → 原始 Child EvidenceSnapshot/Citation`。

默认 `top_k=8`、`candidate_k=max(4*top_k,32)`、RRF k=60；保留实际 source_weights、Rerank 和 Context Pool 的既有配置，没有改成一套新的固定权重。模式为 keyword/vector/hybrid/adaptive 的原有机制，向量缺失/失败仍有明确 Keyword 降级。Parent/Neighbor 只扩展上下文，引用仍冻结原始 Child。

原 PG 关键词分数为匹配 `chunk_terms` TF 求和加完整查询子串奖励 2。原内存 double 则使用正文子串计数，两者已存在词元边界差异。RRF 融合排名，不能替代 BM25 相关性；非空候选不能代表 EvidenceTarget 或 Tasks 被支持。

原/新同冻结语料的模块实际延迟在 `var/reports/p5-rule-router-r1/p55d-start-baseline-1/stage0-final-timing-comparison.json`，包含每阶段毫秒、配置、降级与排序；原模块来自 start-snapshot 并校验哈希。这是内存模块时序诊断，不是完整历史应用、真实 Dense 或模型延迟。真实 PG 新方案每阶段延迟和原 TF SQL 延迟在 `pg-ranking-comparison.json`，不是计划值。

## 4. Stage 1：真实 BM25

`backend/app/domain/bm25.py::bm25_score/score_bm25` 与 `PostgresKnowledgeRepository.keyword_candidates` 使用：

```text
IDF = ln(1 + (N - DF + 0.5)/(DF + 0.5))
score = sum(IDF * TF * (k1+1)/(TF + k1*(1-b+b*DL/AVGDL)))
k1=1.2, b=0.75；同分按 Child ID 稳定排序。
```

DL 是现有 `term_frequencies` 索引词元频次总和，包括中文完整 run/重叠 bigram；不是 Chat Token。N/DF/AVGDL 的 SQL CTE 均先限制有效语料：服务端 KB/Document Scope、未删除 KB/Document、活动且 ready Version、Child-only、Chunk 与 Version 均匹配当前 index identity。零索引长度的有效 Child 也计入 N/平均长度。只查询有效语料统计，不安装数据库扩展、不增加 Migration。

真实 PG 的所有 10 个冻结查询分数与 Python 公式逐项一致；旧版、删除、跨 KB/Document、Parent、错误 Chunk/Version index identity、删除 KB、未 ready 版本均不改变原有效候选和分数。真实 PG/pgvector 候选配合 SIMULATED Embedding/Rerank 走过 weighted RRF、Parent、Context 和原始 Citation；该向量是明确标记的测试向量，不是一次真实模型质量验证。

## 5. 冻结语料、排名与质量限制

20 个自包含合成 Child、10 个有明确相关性标签的查询，涵盖编号、专业术语、普通中文、长短 Child、重复关键词、表格行、Caption/OCR 文本及跨文档字段。SHA-256：`dc61ab5c276785c10893ae12942d7d91e926315396d01100e3ebc737cf18ade0`。语料在首轮测试前冻结，未为全绿改语料、标签或调参。Caption/OCR 行是合成候选排名样本，不证明真实 OCR/表格解析正确性。

下表采用同一次真实 PG、相同语料/Scope 的原 TF SQL 与 BM25 SQL：

| 方案 | Recall@1 | MRR@1 | Recall@3 | MRR@3 |
|---|---:|---:|---:|---:|
| 原 TF | 0.000 | 0.000 | 1.000 | 0.467 |
| BM25 | 0.600 | 0.600 | 1.000 | 0.800 |
| BM25 + Expansion | 0.600 | 0.600 | 1.000 | 0.800 |

该语料 Expansion 触发率 0，增量指标与 BM25 相同，**没有证明 Expansion 提高召回**。独立反例证明“非空但不相关 Dense + Keyword 缺失”能够有界恢复关键词候选、降低术语未覆盖信号、一次全局 Rerank；这只是行为与代理信号证据，不能冒充标注 Recall。

内存旧 TF Recall@3=0.9/MRR@3≈0.4167，真实旧 TF SQL 为 1.0/≈0.4667，原因是既存的子串 vs 索引词元差异。两份报告分别保留，不将它们混成同一基线。新 BM25 两份结果一致。该小型合成语料的改善不能代表实际用户语料，也未校准为最优参数。

## 6. Stage 2：一次有界低召回扩展

`query_expansion.risk_signals/variants → HybridRetriever.retrieve`：空候选、关键词缺失、关键词未覆盖、EvidenceTarget 不足、经来源验证的显式 Tasks 不足、证据单元过少。它们是风险代理，线上真实 Recall UNKNOWN。

默认零模型确定性投影：固定问题前缀移除、原文引用短语、合法 Tasks 原文 span、原文分隔片段；保留原 q0/normalized/硬性 Latin 编号及数字日期词元。最多 2 个查询，记录来源、查询/词元哈希、各自候选 ID/分数/排名、前后信号、耗时、固定失败原因。历史与记忆不生成新事实实体、Scope 不改变。

原关键词与投影按 Child ID 去重并在词法通道做有界排名融合，再保留既有全局 weighted RRF、版本/Scope 回读和单次 Rerank。Dense 只对原合法表达调用一次，不对每个投影额外 Embedding。追加读取/检索失败保留首次候选，不重试，失败详情不外泄。定向 supplementary retrieval 关闭第二次 Expansion，避免同一检索计划循环恢复。

新增阶段 model_calls=0、模型费用=0，依据 `DETERMINISTIC_NO_MODEL`；无需新的预算 purpose。额外模型 Expansion 没有实现/启用，真实派发关闭；原 Query Understand 和正文生成费用/Attempt 未复用。

**尚未解决**：保守信号与既有 adaptive 纯向量路由的关系，以及旧显式 Local Query Gateway 在 `NO_SAFE_VARIANT` 下的接续行为，见第 9 节；不能因实现存在就报告兼容。

## 7. Stage 3：Smart 分段阅读

`LangChainAgentAdapter read_document(offset,length) → KnowledgeToolGateway._read_page → PostgresKnowledgeRepository.read_document_view(Scope,document,version)`。

- 保持已有只读工具集合，不新增 Job/Agent 框架；offset/length 严格整数，bool/负数/越界拒绝。模型不能指定 KB/Scope/Version。
- PG 一次读取快照按有效 Child 顺序构建阅读投影；返回明确 Document/Version、start/end、has_more/next_offset。各页绑定同一 Run、Scope、活动版本，前后复查身份，版本/Scope/删除或同版本内容变化拒绝。内部目前读取整个 Child 投影后截取有界页，不是数据库流式分页，超大文档内部资源性能仍待评估。
- 默认每页 8000 字符、单 Run 最多 8 次/24000 字符/8000 个由模型绑定计数器计算的额度；完整工具结果协议参与计数。真实窗口或 tokenizer 未准入时继续阻断，不用字符数冒充准确 Chat Token。兼容旧无计数器入口只允许前部单页、显示 UNKNOWN；继续阅读必须有计数器，不能据此宣称真实容量已验证。
- 范围重叠拒绝；从 next_offset 连续读取首/中/尾，不重复或跳读。表格/完整行与 Caption/OCR locator 单元标为 atomic，超大单元拒绝，切入单元拒绝，不截碎来获取通过。
- 页内容明确 `READING_CONTEXT_ONLY_SEARCH_CHILD_FOR_CITATION`，不加入 EvidenceAccumulator。最终引用仍经过原始 Child/Quote/Version/SourceLocator；原引用未改成阅读投影 offset。
- SIMULATED 模型通过真实 LangChain 工具与 ContextManager 分三次阅读后检索尾段并返回验证的 E1；这不是实际商业模型调用。关联历史、Checkpoint、Global/Scoped Memory 与引用用例多数通过，但第 9 节的窗口反例回归尚未解决，不能宣称整个上下文链已通过。

## 8. 隔离身份与实际命令

连接前只读核验：127.0.0.1:55729，`p55a_lifecycle_test`，OID 16384，system identifier 7694635764170571814，revision `0021_global_scoped_memory`。发现值不是永久端口授权。新 PG 用例每个连接再次检查身份。`target-identity.json` 与 PG 12 份回滚计数为实际证据。没有业务 Migration、服务重启或全量索引重建；Redis 没有在本轮重启/修改，未新增 Redis 恢复验收。

静态/身份准备命令（均实际退出 0）：

```text
.venv/Scripts/python.exe -B -X utf8 var/reports/p55d-retrieval-r1/prepare.py
.venv/Scripts/python.exe -B -X utf8 var/reports/p55d-retrieval-r1/freeze_corpus.py
.venv/Scripts/python.exe -B -X utf8 var/reports/p55d-retrieval-r1/verify_target.py
.venv/Scripts/python.exe -B -X utf8 -m alembic heads
```

上游导出、静态语法检查、Git/证据 hash 详情见 `upstream-manifest.json`、`git-checks.json`、`final-manifest.json`。不存在的 `ADR-004.md` 与 `0001_initial.py` 检索已如实报错；后续读取实际 ADR-004 文件/真实 schema，不据此臆造表结构。首次 PG 错误为测试写了不存在的 `evidence_snapshots`，没有形成业务写入。

两次 `diagnose_initial.py` 为零外发诊断，进程退出 0 仅表示成功记录 `DOCUMENT_RUN_TOKEN_LIMIT`，不计测试 PASS。旧失败、日志与 JUnit 均保留。

正式尝试原样命令与结果：

```text
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py p55d-targeted-1 backend/tests/test_p55d_retrieval.py backend/tests/test_p55d_smart_reading.py
```
进程 / pytest 退出码：1 / 1；{'PASS': 27, 'FAIL': 1, 'ERROR': 0, 'SKIP': 0}；JUnit：`var/reports/p5-rule-router-r1/p55d-targeted-1/junit.xml`。

```text
.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55b2_memory_isolated.py p55d-pg-1 backend/tests/test_p55d_retrieval_postgres.py
```
进程 / pytest 退出码：1 / 1；{'PASS': 0, 'FAIL': 0, 'ERROR': 12, 'SKIP': 0}；JUnit：`var/reports/p55b-memory-r1/p55d-pg-1/junit.xml`。

```text
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py p55d-targeted-repair-1 backend/tests/test_p55d_retrieval.py backend/tests/test_p55d_smart_reading.py
```
进程 / pytest 退出码：0 / 0；{'PASS': 31, 'FAIL': 0, 'ERROR': 0, 'SKIP': 0}；JUnit：`var/reports/p5-rule-router-r1/p55d-targeted-repair-1/junit.xml`。

```text
.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55b2_memory_isolated.py p55d-pg-repair-1 backend/tests/test_p55d_retrieval_postgres.py
```
进程 / pytest 退出码：1 / 1；{'PASS': 2, 'FAIL': 10, 'ERROR': 0, 'SKIP': 0}；JUnit：`var/reports/p55b-memory-r1/p55d-pg-repair-1/junit.xml`。

```text
.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55b2_memory_isolated.py p55d-pg-owner-repair-1 backend/tests/test_p55d_retrieval_postgres.py
```
进程 / pytest 退出码：0 / 0；{'PASS': 12, 'FAIL': 0, 'ERROR': 0, 'SKIP': 0}；JUnit：`var/reports/p55b-memory-r1/p55d-pg-owner-repair-1/junit.xml`。

```text
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py p55d-associated-1 backend/tests/test_hybrid_retrieval.py backend/tests/test_retrieval_routing.py backend/tests/test_retrieval_provenance.py backend/tests/test_retrieval_execution_record.py backend/tests/test_query_coverage.py backend/tests/test_langchain_agent.py backend/tests/test_langchain_quick_chain.py backend/tests/test_p4_rag_pipeline.py backend/tests/test_context_expansion.py backend/tests/test_p55b_context_window.py backend/tests/test_p55b2_memory.py backend/tests/test_p55b21_global_scoped.py backend/tests/test_p55c_query_understand.py backend/tests/test_quick_citation_prompt.py
```
进程 / pytest 退出码：1 / 1；{'PASS': 226, 'FAIL': 7, 'ERROR': 0, 'SKIP': 0}；JUnit：`var/reports/p5-rule-router-r1/p55d-associated-1/junit.xml`。

```text
.venv/Scripts/python.exe -B -X utf8 var/reports/p55d-retrieval-r1/verify_start_baseline.py p55d-start-baseline-1 backend/tests/test_retrieval_routing.py::test_vector_route_avoids_keyword_rank_interference backend/tests/test_retrieval_provenance.py::test_gateway_records_merge_and_links_actual_calls_after_an_earlier_empty_pass backend/tests/test_langchain_quick_chain.py backend/tests/test_p55b2_memory.py::test_old_cloud_or_fallback_cannot_use_local_window
```
进程 / pytest 退出码：0 / 0；{'PASS': 7, 'FAIL': 0, 'ERROR': 0, 'SKIP': 0}；JUnit：`var/reports/p5-rule-router-r1/p55d-start-baseline-1/junit.xml`。

每轮都有源码 before/after 哈希、真实 argv、进程与 pytest 退出码；保护 guard 计数均为 0。新增/最终必要集合无 SKIP，没有把旧 SKIP/FAIL 变成 PASS。过程警告包含 Python 路径提示/Swig deprecation，测试仍实际执行；未修复环境或绕过权限。

## 9. 回归、停止边界及最小待修项

以下全部是本轮新增回归，原断言没有修改：

- `test_vector_route_avoids_keyword_rank_interference`：当前 FAIL；开始源码 PASS；NEW_REGRESSION。
- `test_gateway_records_merge_and_links_actual_calls_after_an_earlier_empty_pass`：当前 FAIL；开始源码 PASS；NEW_REGRESSION。
- `test_quick_chain_uses_langchain_runnable_and_shared_gateway`：当前 FAIL；开始源码 PASS；NEW_REGRESSION。
- `test_quick_chain_accepts_an_independent_query_gateway`：当前 FAIL；开始源码 PASS；NEW_REGRESSION。
- `test_quick_chain_reuses_core_for_targeted_retrieval_and_partial_answer`：当前 FAIL；开始源码 PASS；NEW_REGRESSION。
- `test_old_cloud_or_fallback_cannot_use_local_window[False]`：当前 FAIL；开始源码 PASS；NEW_REGRESSION。
- `test_old_cloud_or_fallback_cannot_use_local_window[True]`：当前 FAIL；开始源码 PASS；NEW_REGRESSION。

根因归类：

1. `test_vector_route_avoids_keyword_rank_interference`：adaptive 有意不请求关键词通道，当前风险逻辑仍视“关键词缺失”为不足，扩展将 vector 转 hybrid，破坏原渠道合同。需要明确哪些真实不足信号能覆盖 adaptive 决策，不能仅改旧断言来放行。
2. Quick 正常/partial 两项、provenance 一项、Cloud/Fallback 窗口两项：旧内存 scorer 用子串命中 `A方案成本1000元`，新 BM25 沿用整词 `term_frequencies` 得到一个混合词元，查询 `a`/中文字段不能匹配，候选丢失；窗口测试提前 NO_CANDIDATES，没有验证应到达的容量拒绝。因此不能称窗口保护已回归通过。后续需审定一致的混合文本词元/索引兼容方法，不能添加假 BM25 分数、伪造索引身份或削弱窗口保护。
3. 独立 Local Query Gateway：当确定性阶段已触发但无安全变体时，当前互斥条件阻断旧显式授权的 Local 路径。需要限定接续合同，同时保证一阶段有界、防重复、Scope 与正常预算入口。

普通初轮+一次有依据修正额度已消耗：初轮 27 PASS/1 FAIL、PG 12 ERROR；修正后 31 PASS、PG 2 PASS/10 FAIL。Owner 另授权一次仅文件名 fixture 修正，PG 12 PASS。未将此授权扩大为处理上面的生产接线回归。依 [AGENTS.md](../../AGENTS.md) 原句 “Initial attempt plus at most1 evidence-driven repair; thereafter hand off or BLOCKED” 停止继续修补，保留证据交 Owner。没有自行批准延期。

## 10. 状态、交接与未运行项目

| 能力 | 状态 | 实际边界 |
|---|---|---|
| BM25 Python + PG | IMPLEMENTED / REAL_ISOLATED_PG_VERIFIED | 公式/统计隔离通过，混合文本兼容回归未关闭 |
| 有界确定性 Expansion | IMPLEMENTED / SIMULATED_VERIFIED | 新反例通过；adaptive/旧 Local 接续阻断 |
| Smart 分段阅读 | IMPLEMENTED / SIMULATED_VERIFIED / REAL_ISOLATED_PG_VERIFIED | 页/Version/Quote保护通过；实际模型窗口准入 REAL_BLOCKED |
| P5.5-D 整体 | BLOCKED / NEEDS_OWNER_REVIEW | 7 项新增回归不可当作历史缺口 |
| 商业模型、业务 Migration、生产启用 | NOT RUN / REAL_BLOCKED | 本轮未授权，请求数 0 |
| P5.5-E、P6、向量记忆索引 | NOT IMPLEMENTED IN THIS GOAL | 未进入 |

原始 q0、合法 Query Understand Schema、Tasks/Intent、MemoryRetriever、ContextCheckpoint、模型费用 UNKNOWN 与正文角色次数合同保留。真实模型效果、真实容量、生产性能/中文 tokenizer 的一致索引方法尚未验收。完整 P5 正文 104 项及浏览器全量未重跑：NOT RUN，未以新增 31 项代替全项目验收。

下一步只建议 Owner 审批一个针对上述三类回归的限定修正合同，复用当前 PG/冻结语料/安全 Runner，不重新审计或调参。当前没有新 commit SHA；最终 HEAD/dirty/hash/diff 保存在 final-manifest，全部未提交成果和旧证据保留。回滚本任务应由 Owner 根据 task-only.diff 与 byte-exact before/after 选择性处理，禁止 reset/stash/clean 整个 dirty 工作树。
