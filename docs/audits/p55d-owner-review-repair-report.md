# P5.5-D — Owner 限定修正后最终开发报告

状态：**CHECKS_PASSED / NEEDS_OWNER_REVIEW**。本轮目标范围已完成代码与授权验证，不授予 Owner ACCEPTED，不表示全项目发布验收。真实商业模型、业务库迁移、生产启用继续 REAL_BLOCKED。

旧 BLOCKED 报告 `p55d-retrieval-enhancement-report.md` 与全部失败/首轮 diff、manifest/JUnit 原样保留。本报告是随后 Owner 额外批准的三类回归限定修正结果，当前状态以本报告及 `owner-review-repair-r1/final-manifest.json` 为准。

## 1. 基线、修正授权和修改清单

HEAD 保持 `befd5478a680ffa779a1e8ede736260d9bac1060`；分支 `codex/local-first-rag-v1-20260930`。初始 32 dirty；前轮收口 48 dirty，续行核对全部原 16 任务文件无漂移。Owner 已明确批准 `owner-review-fix-scope-r1.md` 的一次限定修正与验证；此前初轮、普通修正与额外 fixture 修正计数未重置。

本次修正仅 6 个生产文件和 2 个新 D 测试文件：

- `domain/text_normalization.py`：混合脚本词元边界，原 q0/归一化原文保留。
- `domain/bm25.py`：明确 lexical version，不改变 k1=1.2/b=0.75。
- `adapters/postgres/knowledge_repository.py`：旧混合 `chunk_terms` 的查询时兼容投影，BM25 统计与候选严格绑定 Scope/版本/Child/index；补 Document 与 Child KB 一致性条件。
- `application/query_expansion.py`：区分 Keyword NOT_SELECTED 与 REQUESTED_EMPTY。
- `application/retrieval.py`：按实际已选择通道与显式 EvidenceTarget/合法 Tasks 缺口触发，保留强不足信号的向量恢复。
- `application/knowledge_gateway.py`：无安全变体且零扩展查询发送时保留旧显式 Local 路径；已执行或失败的扩展不再追加 Local 派发，模型补检关闭第二次确定性 Expansion。
- `tests/test_p55d_retrieval.py`、`tests/test_p55d_retrieval_postgres.py`：新增正反例、旧/新词元并存证明、保留原 TF 词元基线。

以上路径位于 `backend/app/`、`backend/tests/`。原 7 个失败测试的文件与断言没有修改。新增本报告；所有实际路径、byte-exact before/after、精确增量 diff 与最终 hash 在新 manifest。累计 D 差异相对于 Goal 开始快照单独给出，没有夹带 C/B2.1/P7 原有成果。

## 2. 固定上游与执行条件

WeKnora 固定 commit `3e8b0bfc80b845b2d4b2ed683994748741450a97`。已读取并校验 Git Blob 的 `query_expansion.go`、`read_document.go`、PG `repository.go` 保存于 D 盘；身份/位置/SHA-256 详见旧 `upstream-manifest.json`，本轮再次核对字节。未切版本或参数搜索。上游 Chunk offset 与本项目字符 offset 的区别保留；PG 全文排名未伪称上游 BM25。本地 E:/WeKnora 权限拒绝未绕过，使用已授权 GitHub 源码。

真实隔离目标现场核对：127.0.0.1:55729，`p55a_lifecycle_test`，OID 16384，system identifier 7694635764170571814，revision `0021_global_scoped_memory`。每个新 PG fixture 再次检查相同身份。本轮没有新 Migration，没有修改历史 Migration，没有重建索引/服务，没有触碰业务库或凭据。测试端口是已发现值，不是永久授权。

## 3. 最终 BM25 与旧索引兼容

```text
IDF = ln(1 + (N - DF + 0.5)/(DF + 0.5))
score = sum(IDF * TF * (k1+1)/(TF + k1*(1-b+b*DL/AVGDL)))
k1=1.2，b=0.75，同分按原 Child ID 稳定排序。
```

统计“文档”为有效 Child。N、DF、DL、AVGDL 均基于当前 Scope、未删除 KB/Document、活动 ready Version、Child-only、当前 Chunk/Version index identity；Document 与 Child KB 必须一致。RRF 只融合排名，不能代替 BM25。

新词元规则把 `A方案成本1000元` 分成 Latin、CJK、数字脚本 run；CJK run 保留原 full-run+bigram，`API_v2` 等标识符不拆散。PG 在有效语料 CTE 内，仅将旧混合词元按同一规则展开，按频次汇总；纯 CJK 原来已保存的 bigram 不重复展开。因此 TF/DF/DL/AVGDL 对旧混合行与新行一致。

实际 PG 反例逐项与 Python BM25 分数一致，且兼容查询前后 `chunk_terms` 原行逐项相同。没有 UPDATE/DELETE 索引行、伪造 revision/fingerprint、修改 Embedding Profile、增加表或生产重建。只读 PG/Python regex 7 个边界一致性诊断在 `regex-read-only.json`。lexical version：`mixed-script-compatible/v2`，不是新的 P3 chunking/Embedding 身份。

旧版本、删除文档、跨 KB/Document、Parent、错误 Chunk/Version index identity、删除 KB、未 ready、Document/Child KB 不一致均被排除于统计和候选。

## 4. Query Expansion 的最终边界

默认一次确定性阶段、最多 2 个投影；固定前缀移除、原文引用短语、已验证 Tasks 源 span 和原文分隔片段。q0/完整约束继续交给原 Rerank、EvidencePlan、生成/Validator，不覆盖用户问题，不从记忆/历史编造实体或事实。编号、数字、日期和显式 Latin 实体保持于各投影，Scope 由服务端固定。

代理信号包含空候选、实际请求 Keyword 后缺失、关键术语未覆盖、EvidenceTarget/合法 Tasks 不足、证据过少；不是在线真实 Recall。adaptive 纯向量路由中，未选择 Keyword 记为 NOT_SELECTED；普通词法不一致不能自动否定语义释义。空候选/明确不支持的目标或合法 Tasks 仍可触发向量的有界词法恢复，并非整体禁用 Expansion。

每个投影记录来源、query/term hash、独立候选 ID/分数/排名、状态；按原 Child ID 去重并进行词法通道排名融合，再进入原 weighted RRF、有效 Scope/Version 回读与一次全局 Rerank。扩展查询不增加 Embedding；失败保留初次结果、固定诊断，不重试。

原显式 Local Query Gateway 只在没有候选、显式开启且确定性阶段实际零查询时接续。已执行/失败的扩展均不会再触发 Local；Local 补检关闭第二次确定性扩展，默认依然零模型。新增禁用/无安全变体/已执行/失败反例通过。未开发新的 LLM Expansion 派发或 purpose；额外确定性模型费 0，依据实际无模型调用，而非公开免费价格。

额外真实 PG 行为反例：初次关键词候选非空，但问题包含未被支持的条款/金额词元，执行有界扩展、保留 300 和 q0、单次模拟 Rerank、原始 Child Quote/Version。`expansion-pg-trace.json` 记录实际 SQL 候选和额外毫秒；该补充没有相关性标签，只能作为行为诊断，不能伪造 Recall 提升。

## 5. Smart 阅读与最终生产接线

最终链：Quick 或 Smart `KnowledgeToolGateway.search_knowledge → KnowledgeGateway/合法 Query Understand → HybridRetriever → PG Keyword(BM25) + Dense → weighted RRF → Rerank → Parent/Neighbor Merge → Top-K → ContextBuilder → 原 Child EvidenceSnapshot/Citation`。默认 top_k=8、candidate_k=max(4*top_k,32)、RRF k=60；原配置权重未改。

Smart `read_document(offset,length)` 复用原工具，PG 用 Scope/活动 Version/Child/index 绑定的 Child 顺序阅读投影。严格位置、实际 start/end、has_more/next_offset，服务端固定 Version，不允许模型指定权限。单 Run 默认 8 次/24000 字符，每页 8000；模型绑定计数器额度默认 8000，UNKNOWN 不冒充字符 Token。实际模型窗口/计数器未准入时真实路径仍关闭。

连续首/中/尾不重读、不跳读；版本/Scope/删除/同版本内容变化拒绝。Table/完整行、Caption/OCR 单元不被截碎，超大原子单元明确拒绝。阅读内容标为 READING_CONTEXT_ONLY，不自动增加 EvidenceSnapshot；最终引用需原 Child、Quote、Version、SourceLocator，阅读投影字符 offset 不是原始 locator。内部仍先构造文档 Child 投影再有界返回，并非 SQL 流式分页，大文档资源性能为生产前待测项。

新的完整路径记录实际真实 PG/pgvector 运算与 **SIMULATED** Embedding/Rerank：Keyword 5、Vector 5，权重 keyword=.25/vector=1，RRF k=60，top_k=1 个 Parent 上下文分组；保留 5 个原始 Child 引用单元，不能把这解释为 top_k 返回 5 个独立上下文。所有快照 Quote 与原 Child 相同。

该记录各阶段毫秒：Keyword 4.167、Vector 2.511、模拟 Embedding 12.268、Fusion 0.038、模拟 Ranking 1.244、Merge 2.722。是单次测试观测，不是实际供应商延迟或生产 p95。完整每阶段身份/排序/父子/quote hash 在 `hybrid-context-citation-trace.json`。

旧 Stage 0 模块时序保留为修正前诊断，不充当本轮最终时序。当前最终逐查询阶段耗时来自新的排名报告和上述完整路径记录。

## 6. 同冻结语料的最终质量对照

20 Child / 10 标注查询，fixture SHA-256 `dc61ab5c276785c10893ae12942d7d91e926315396d01100e3ebc737cf18ade0`，未改正文、标签、参数或查询集合。覆盖编号、术语、中文、长短/重复、表格行、Caption/OCR 与跨文档字段；不是真实 OCR/表格解析验证。

旧 TF SQL 使用冻结的旧词元规则与同一份旧物理索引；新 BM25 对相同物理行进行兼容查询。原 TF 与 BM25 的完整原始分数/排名、Recall/MRR、Scope、数据版本、扩展触发及延迟均已保存。

| 最终真实 PG 方案 | Recall@1 | MRR@1 | Recall@3 | MRR@3 |
|---|---:|---:|---:|---:|
| 原 TF | 0.000 | 0.000 | 1.000 | 0.467 |
| BM25 | 0.500 | 0.500 | 1.000 | 0.750 |
| BM25 + Expansion | 0.500 | 0.500 | 1.000 | 0.750 |

混合词元兼容后 Recall@1 从上一轮 .6 变为 .5，MRR@3 从 .8 变为 .75；结果如实保留，没有为恢复上一轮数字调参。这仍优于原 TF 的该项合成对照，但不能推断实际用户语料改善。Expansion 在主语料的触发率仍为 0，增量收益未证实；补充 PG/模拟反例只证明不足信号和恢复行为。未把初始 k1/b 称为最佳参数。

## 7. 历史、记忆、费用和禁止事项

本轮集中关联包含 Context Window/Compaction、Global/Scoped Memory、Query Understand、历史引用、P5 Router/生成/回放、P4 Rerank/Parent/Context、Quote 和成本/防重；412 PASS，其中原 P5 四个文件当前共 167 个用例，不冒称旧 104 计数。

Memory、Checkpoint、合法历史与 Query Understand 的 12 个存储/核心/相关测试文件哈希与原 47 PASS 的真实 PG 证据一致，按未变存储接口复用；`storage-evidence-reuse.json` 给出具体 hashes、原命令/JUnit/退出码。复用不代表新完整应用 E2E 已在 PG 重跑。当前变动接线通过本轮真实 PG 检索与模拟生成的定向/关联证明。

旧 Memory 集成 fixture 会提交并保留合成记录，本轮没有采用，不伪造其重跑。新增 14 PG＋1 路径记录＋1 扩展 PG 的所有合成业务数据在外层事务回滚，16 份 public 全表前后计数一致；没有清理、退款、修改任何真实/旧 UNKNOWN。

真实模型请求 0、实际本轮商业支出 0；确定性 Expansion 额外模型费 0。完整路径有 1 次模拟 Embedding、1 次模拟 Rerank：输入 token=3 为模拟返回值，其他未提供数据/结算 UNKNOWN。当前通用 usage.summary 未包含 Rerank 聚合，不能据它声称所有模型均被覆盖或 Rerank 零调用；Rerank 次数由模拟传输断言证明。保留原预算、外发、Attempt、UNKNOWN 与正文角色限制，未增加云端授权。

## 8. 实际命令、原始证据和回归修复

以下均通过项目已保护入口执行，不关闭沙盒/ACL，不读取真实 Key；process 与 pytest 均实际退出 0，各轮 source-before/source-after 一致。诊断捕获入口复用原 Provider/检索与预算代码，只采集既有测试元数据，不增加真实网络请求。

```text
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py p55d-owner-targeted-1 backend/tests/test_p55d_retrieval.py backend/tests/test_p55d_smart_reading.py backend/tests/test_text_normalization.py backend/tests/test_retrieval_routing.py::test_vector_route_avoids_keyword_rank_interference backend/tests/test_retrieval_provenance.py::test_gateway_records_merge_and_links_actual_calls_after_an_earlier_empty_pass backend/tests/test_langchain_quick_chain.py backend/tests/test_p55b2_memory.py::test_old_cloud_or_fallback_cannot_use_local_window
```
进程/pytest 退出码 0/0；{'PASS': 49, 'FAIL': 0, 'ERROR': 0, 'SKIP': 0}；JUnit：`var/reports/p5-rule-router-r1/p55d-owner-targeted-1/junit.xml`。

```text
.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55b2_memory_isolated.py p55d-owner-pg-1 backend/tests/test_p55d_retrieval_postgres.py
```
进程/pytest 退出码 0/0；{'PASS': 14, 'FAIL': 0, 'ERROR': 0, 'SKIP': 0}；JUnit：`var/reports/p55b-memory-r1/p55d-owner-pg-1/junit.xml`。

```text
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py p55d-owner-associated-1 backend/tests/test_hybrid_retrieval.py backend/tests/test_retrieval_routing.py backend/tests/test_retrieval_provenance.py backend/tests/test_retrieval_execution_record.py backend/tests/test_query_coverage.py backend/tests/test_langchain_agent.py backend/tests/test_langchain_quick_chain.py backend/tests/test_p4_rag_pipeline.py backend/tests/test_context_expansion.py backend/tests/test_p55b_context_window.py backend/tests/test_p55b2_memory.py backend/tests/test_p55b21_global_scoped.py backend/tests/test_p55c_query_understand.py backend/tests/test_quick_citation_prompt.py backend/tests/test_retrieval_contract.py backend/tests/test_knowledge_tools.py backend/tests/test_agent_tool_allowlist.py backend/tests/test_p5_router_features.py backend/tests/test_p5_router_boundaries.py backend/tests/test_p5_router_generation.py backend/tests/test_p5_router_replay.py
```
进程/pytest 退出码 0/0；{'PASS': 412, 'FAIL': 0, 'ERROR': 0, 'SKIP': 0}；JUnit：`var/reports/p5-rule-router-r1/p55d-owner-associated-1/junit.xml`。

```text
.venv/Scripts/python.exe -B -X utf8 var/reports/p55d-retrieval-r1/capture_pg_trace.py p55d-owner-trace-1 backend/tests/test_p55d_retrieval_postgres.py::test_pg_hybrid_rrf_rerank_parent_context_original_citation
```
进程/pytest 退出码 0/0；{'PASS': 1, 'FAIL': 0, 'ERROR': 0, 'SKIP': 0}；JUnit：`var/reports/p55b-memory-r1/p55d-owner-trace-1/junit.xml`。

```text
.venv/Scripts/python.exe -B -X utf8 scripts/verify_p55b2_memory_isolated.py p55d-owner-expansion-pg-1 var/reports/p55d-retrieval-r1/owner-review-repair-r1/test_pg_expansion_evidence.py
```
进程/pytest 退出码 0/0；{'PASS': 1, 'FAIL': 0, 'ERROR': 0, 'SKIP': 0}；JUnit：`var/reports/p55b-memory-r1/p55d-owner-expansion-pg-1/junit.xml`。

原 7 项失败修复对照（断言未改）：

- `test_vector_route_avoids_keyword_rank_interference`：开始源码 PASS → 初次 D FAIL → 本轮 PASS。
- `test_gateway_records_merge_and_links_actual_calls_after_an_earlier_empty_pass`：开始源码 PASS → 初次 D FAIL → 本轮 PASS。
- `test_quick_chain_uses_langchain_runnable_and_shared_gateway`：开始源码 PASS → 初次 D FAIL → 本轮 PASS。
- `test_quick_chain_accepts_an_independent_query_gateway`：开始源码 PASS → 初次 D FAIL → 本轮 PASS。
- `test_quick_chain_reuses_core_for_targeted_retrieval_and_partial_answer`：开始源码 PASS → 初次 D FAIL → 本轮 PASS。
- `test_old_cloud_or_fallback_cannot_use_local_window[False]`：开始源码 PASS → 初次 D FAIL → 本轮 PASS。
- `test_old_cloud_or_fallback_cannot_use_local_window[True]`：开始源码 PASS → 初次 D FAIL → 本轮 PASS。

额外准备命令 `prepare_owner_repair.py`、`inspect_mixed_regex.py` 均实际退出 0；旧代码/报告原字节保存于新 before/。AST 和默认 git diff --check 实际结果、warn/退出码原样保存在 git-checks.json；Git 的 LF→CRLF 提示没有改变冻结 fixture。新增文件的 no-index --check 可返回 1（存在差异），无空白诊断不能记成测试失败或伪造退出 0。

## 9. 最终状态与 Owner 待审

| 能力 | 状态与边界 |
|---|---|
| 真 BM25/旧索引兼容 | IMPLEMENTED / REAL_ISOLATED_PG_VERIFIED；无 Migration/索引改写 |
| 有界低召回 Expansion | IMPLEMENTED / SIMULATED_VERIFIED / REAL_PG_LEXICAL_VERIFIED；主语料增益未证实 |
| Smart 分段阅读 | IMPLEMENTED / SIMULATED_VERIFIED / REAL_ISOLATED_PG_VERIFIED；实际模型 Token/性能准入 REAL_BLOCKED |
| 当前 Goal 开发/授权测试 | CHECKS_PASSED / NEEDS_OWNER_REVIEW；不等于 Owner 验收或全项目发布 |
| 商业 API、业务 Migration、生产启用 | NOT RUN / REAL_BLOCKED |
| BM25 参数校准、用户语料评测 | NOT RUN；需后续有界质量任务，不能由合成测试代替 |
| P5.5-E / P6、记忆向量索引 | 未进入 |

Owner 待审批：本轮限定验收、真实模型/生产性能与效果验证的后续预算/数据范围。当前不会继续调参或执行任何真实派发。没有 commit/push/tag/部署，不重做 P0～P3，不进入下一阶段。

原报告/失败保留；最终 SHA、Git dirty 状态、全部文件 hash、精确任务差异与要求逐项 completion-audit 见新 manifest。若需回退，由 Owner 按 byte-exact snapshots 与增量 diff 选择性处理，不对共享 dirty 工作树执行 reset/stash/clean。


## D delivery artifact correction (r2)

The workspace dirty count is 48 (the full status list is recorded in `final-manifest.json`). `start.json` contains 47 hash rows; it does not substantiate the earlier claim that 386 files were unchanged. The manifest now records that claim as unverified. Exact `before/` and `after/` snapshots matched the recorded pre-owner/final hashes for 17 task paths; the owner-review report is a new file. `task-only.diff` was rebuilt from those exact bytes and the added report.

The prior `cumulative-task-only.diff`, manifest, completion audit, task-only diff, and this report were copied byte-for-byte to `evidence-preserved-r2/` before editing. The cumulative start-to-final diff is not reconstructed because the required byte-exact start snapshots are unavailable. Its preserved original remains malformed/unverified and must not be used as a complete patch. The historical test records are preserved as evidence, not rerun during this correction.
