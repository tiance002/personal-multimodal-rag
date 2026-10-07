# P1 — 迁移前基础正确性与合同冻结实施报告

日期：2026-10-07（Asia/Shanghai）。任务来源：最新 P1 附件。本轮状态：**P1_PASS**，仅表示两个指定基础问题已实现并有下述离线测试证据；不是发布、真实数据库验收或负责人批准。P1 完成后停止，未进入 P2。

## 1. 初始基线、授权与保留内容

实际仓库：`C:\Users\22088\.codex\worktrees\rag-retrieval-opt-round1\RAG quention`。

开始时实际执行 `git status --short --untracked-files=all`、`git rev-parse HEAD`、`git branch --show-current`，退出 0。初始状态：

```text
?? docs/audits/p0-weknora-transition-current-state.md
HEAD=dd4ca9eec47f2d19ca57fcdab2f6780c80f819fb
branch=codex/local-first-rag-v1-20260930
```

HEAD 与附件要求 SAME；没有 checkout/reset/stash/fetch。上一轮 P0 报告完整保留，SHA-256 仍为 `2eb447a96ed258d92c9c504577caac66a49cd3680cba02ff7c38b0471e5defda`。读取了 AGENTS.md、progress.md、ADR 索引、相关版本/引用/提交设计和 P0 报告；progress 中的旧 main/历史通过记录没有当作当前证据。

本轮用户明确授权指定的源码、必要契约/测试、additive migration 和实施报告；此授权优先于 AGENTS 附录旧动态路由任务的写入禁令。未修改配置、全局设置或 AGENTS。执行者仅主线程，没有新派发 Worker。请求主协调 Sol high；上游实际模型/effort、配置与 app-server 独立证明 UNKNOWN，不把请求身份当实际模型证明。没有 Sol max 或 HARD 调用。任务身份 P1-ROOT/rev1/attempt1，唯一 writer 为主线程；测试迭代均属该实施任务。未证明所有协调约束由控制面硬执行，标记 **MANUALLY_SUPERVISED_TRIAL**。整体 token/费用/耗时 UNKNOWN，未读取总账或凭据。

## 2. 修改文件与范围

本轮 16 个已跟踪文件修改、5 个新增 Python 文件，加本报告，共 22 个交付文件。P0 报告是原有未提交内容，不计入 P1 修改。

| 文件 | 实际用途 |
|---|---|
| backend/app/domain/version_source.py（新增） | frozen VersionSource，区分新上传事实与 NULL legacy unknown |
| alembic/versions/0014_version_source_metadata.py（新增） | 三个 nullable 版本字段，forward-only additive upgrade |
| backend/app/ports/ingestion.py | VersionSource → 原 bytes/原文件名/原媒体类型的 parser 输入适配；不改 parser 实现 |
| backend/app/application/ingestion.py | 内存摄取路径同样按版本采集 metadata，激活时更新显示信息 |
| backend/app/adapters/postgres/knowledge_repository.py | 版本 INSERT、job 读、激活、source readback；提交锁内检查与 chunk/quote 可读性 |
| backend/app/application/final_answer_commit.py（新增） | 单一 FinalAnswerCommitCheck 确定性合同 |
| backend/app/domain/errors.py | FinalAnswerCommitError，表示成功事务写入前被回滚 |
| backend/app/application/knowledge_gateway.py | EvidenceService 持有同一检查，partial 路径与 AnswerResult 类型/finish_reason 契约 |
| backend/app/application/answer_hardening.py | fallback 重新检查；length 不通过摘录兜底变成成功 |
| backend/app/application/quick_chain.py | normal/fallback/source_excerpt 都执行检查，传递明确 answer_type |
| backend/app/application/answer_service.py | 所有产品成功路径汇合检查；同一回调进入原子提交；事务拒绝转 failed |
| backend/app/application/agent_ports.py | SmartAgentResult 携带可选 finish_reason |
| backend/app/application/langchain_agent.py | 同时识别 done_reason/finish_reason=length |
| backend/tests/test_version_source_contract.py（新增） | A1–A5、legacy、生产 SQL 参数/输入适配、历史引用 |
| backend/tests/test_final_answer_commit.py（新增） | B7–B13及空答案/快照/partial/excerpt/终态反例 |
| backend/tests/test_answer_hardening.py | 原 length 冒烟期望改为拒绝成功，仍检查审计与无额外调用 |
| backend/tests/test_answer_service.py | Smart 模拟夹具提供真实 [E1] 标记，保持事件顺序断言 |
| backend/tests/test_ingestion_state_machine.py | 失败夹具用明确不支持 MIME；不再把 .txt + octet-stream 误作不可解析输入 |
| backend/tests/test_message_run_link.py | 低层提交测试显式提供统一 policy 回调；数据库测试未运行 |
| backend/tests/test_postgres_run_terminal_states.py | 同上，保留取消/并发/原子性全部断言；数据库测试未运行 |
| scripts/smoke_v1_repair.py | 必要验收入口适配新增 commit_check 合同；脚本未运行 |
| docs/audits/p1-migration-foundation-report.md（新增） | 本报告 |

Parser/PDF/XLSX/Chunking/Hybrid/RRF/ContextBuilder/QueryRouter/RetrievalRouter/模型策略源码均未修改。没有新依赖、框架或模型，没有删除旧模块/字段/测试/migration。未接 Parent-Child、Rerank、SiliconFlow 或 Cheap/Expensive Router。

## 3. 问题 A：原调用链与修复

原 HEAD 的生产链（P0 与 Git 原始内容核对）：

```text
create_upload(existing) / create_version
  → UPDATE documents.media_type / file_name / original_size（候选尚未 ready）
process_job(job.version_id)
  → SELECT dv.*, d.file_name, d.media_type
get_document_source
  → active/latest dv.storage_key + d.file_name / d.media_type
```

新链位于 knowledge_repository.py 的 create_upload/create_version、process_job、get_document_source（当前约 83/107/374/979 行）：

```text
upload bytes + 上传 metadata
  → INSERT document_versions(file_name, media_type, original_size, sha256, storage_key)
  → queued job(version_id)
job.version_id
  → SELECT dv.*, d.knowledge_base_id
  → VersionSource.from_row
  → parse_version_source
  → version.storage_key bytes + version.file_name + version.media_type
ready + 原有 lease/claim 栅栏
  → 在同一事务中按 version_no 条件更新 active_version_id
  → 同时同步 Document 显示 metadata
source/preview
  → 选择同一个 active（无 active 时 latest candidate）Version
  → 该 Version 的 blob/name/type/version_id
```

候选创建不再更新已存在 Document 的 file_name/media_type/original_size。processing/failed 不会污染 active。较旧 ready job 不能覆盖较新 active 的 metadata，仍受原有 version_no 条件控制。第一次上传没有 active 时，Document 保留初始上传的临时显示信息；有 active 后，其显示字段只随成功激活同步。最新失败 job 的现有列表字段保持独立，不抹去失败提示。

## 4. 新 Version contract 与 Parser 输入

`domain/version_source.py:10 VersionSource` 是 frozen dataclass，含：

```text
version_id, document_id, version_no,
source_sha256, storage_key,
file_name, media_type, original_size
```

file_name/media_type 是上传修订自己的解释事实。original_size 额外保存是为了让现有 Document.original_size 在激活后也与同一 source revision 一致；不是新增解析算法。

CAS 路径是 SHA 文件名，本身没有原扩展名。ports/ingestion.py:38 的适配使用私有 TemporaryDirectory，把指定 storage_key 的原 bytes 复制为 version.file_name，再调用**原封不动**的 ParserRegistry.parse(path, version.media_type, document_id, version_id)。这让 Parser 实际收到本版本的原文件名/后缀，不借用 Document；临时副本退出后清理，不覆盖原件。需要可写临时目录和一个源文件大小的临时空间。未使用硬链接，不把 CAS 原件暴露为可写输入。

保留原文件名意味着现有 ParserRegistry 的 .txt + generic octet-stream 后缀路由现在能正常工作；原失败夹具改为 application/x-unsupported，仍验证失败候选不激活，没有降低保护。

## 5. Migration、旧数据兼容与未执行项

仓库新 head：`0014_version_source_metadata`，down_revision=`0013_message_run_link`。upgrade 仅：

```sql
ALTER TABLE document_versions ADD COLUMN file_name TEXT;
ALTER TABLE document_versions ADD COLUMN media_type TEXT;
ALTER TABLE document_versions ADD COLUMN original_size BIGINT;
```

三个字段 nullable；不 DROP/RENAME/DELETE，不修改旧 migration，不进行不可靠回填。即使旧 active version，其 Document metadata 也可能已被失败候选污染，因此没有把当前 Document 值复制给任何历史 Version 并称作原始事实。

Legacy NULL 兼容路径：

- 已有 chunks/sections/answer_evidence 及 version/chunk 历史引用继续可读，不重建或删除。
- source/preview 仍读取选中旧 version 的真实 bytes；若 name/type 任一未知，返回生成的 `<version_id>.bin` 和 `application/octet-stream`，内部 metadata_status=`legacy_unknown`。不凭当前 Document 名称触发 Office/PDF 解释。
- legacy unknown 的排队/重试摄取明确失败 `LEGACY_VERSION_METADATA_UNKNOWN`；不会静默用错另一版类型。需要重新上传形成带完整事实的新 Version，或另行取得可靠历史证据后制定补录任务。
- downgrade 明确拒绝 destructive 回收新字段；保留新捕获事实。应用回滚仍会恢复旧应用行为，不能把回滚代码称作修复后的验收。
- **未应用 migration，真实 DB 已应用版本 UNKNOWN**。应用上线前必须在新的明确授权下验证隔离 DB 并先应用 additive migration；本轮没有连接真实数据库。

离线 Alembic `--sql` 退出 0，实际生成上述三条 ADD COLUMN 和 alembic_version 更新；打印的 BEGIN/COMMIT 是 SQL 文本，未执行数据库事务。

## 6. 问题 B：统一 FinalAnswerCommitCheck

`application/final_answer_commit.py:30 FinalAnswerCommitCheck.check` 是所有成功结果共用的 policy，不调用模型、检索、语义 Judge 或数据库。第一版合同：

| 规则 | 实现和边界 |
|---|---|
| B1 非空 | answer 必须是非空白字符串，否则 MODEL_EMPTY |
| B2 截断 | finish_reason=length → MODEL_OUTPUT_TRUNCATED；现有 provider 异常与 Smart done_reason/finish_reason 同样拒绝 |
| B3 身份 | [E数字] 标签必须在本轮快照集；E#/范围标记不冒充有效引用；提交 citations 与正文引用集合一致且无重复 |
| B4 可冻结 | 唯一 label、唯一 version/chunk 身份、非空 version_id/chunk_id/quote、quote SHA-256、可 JSON 序列化 locator；当前产品引用类型都基于 chunk，不发明无 chunk 的新引用类型 |
| B5 强结构化数值 | 复用现有 row_facts、claim_amount：同一主体/月/字段/值/单位与引用行、来源 hash/version；不跨头表拼接，不接受 formula/cache/merge/header 歧义 |
| B6 Caption | 复用已有 caption 数值独立断言保护；caption 不能充当 original numeric evidence |
| 持久化补充 | SQL 成功事务内再核对 chunk_id+version_id 的实际 content，quote 必须可回读；FK 存在本身不够 |

强数值检查仅围绕已有明确结构化行/月度目标及现有 caption 守卫，不声称覆盖任意事实。AnswerValidator 原有规则未重写或批量删除；关系、宽泛词面支持、topic coverage/self-contradiction 等旧 heuristic 仍属于上游流程，**不是所有最终答案均被严格事实验证的证据**。

明确 answer_type：generated / fallback / partial / source_excerpt。partial 允许明确未回答的目标，但已有数字断言仍要匹配其引用行；不能利用“partial 函数”绕过规则。source_excerpt 必须精确等于允许的证据前缀 + 已冻结 [E] 原文块，不接受附加生成断言。原样行展示不被误当作 subject/value 的生成结论；caption 数字即便在 evidence-only 摘录路径也不能升级成原始数值证据。

## 7. 所有最终 SUCCESS 路径

| 产品路径 | 上游处理 | 共同最终检查与提交 |
|---|---|---|
| Quick normal | 现有 AnswerValidator + Hardening | 同一 check → AnswerResult → AnswerService 同一 check → SQL 锁内同一 check |
| Quick extractive fallback | 审计成功后产生新 fallback candidate | Hardening 重新 check，失败保留错误/清空回答；不能直接 error=None |
| Quick partial | 现有缺项/部分回答构造 | EvidenceService.partial_answer 同一 check，显式 partial；随后同一 AnswerService |
| Quick evidence-only | 无生成，原证据摘录 | Quick 同一 check，显式 source_excerpt；随后同一 AnswerService |
| Smart normal/fallback | 现有 coverage、normalize、共享 EvidenceService/Hardening | 同一 check；随后同一 AnswerService，绕过或错误 Port 结果也不能直接提交 |
| Smart privacy evidence-only | 现有共享 Quick evidence-only | 同上，未改变 Cloud/Local 决策 |
| length | provider/Agent 检测 + 本地审计 | 保持 MODEL_OUTPUT_TRUNCATED，不再通过 fallback 清错成为 SUCCESS |
| clarification/insufficient/cancel/model failure | 原有失败/取消诊断 | 不属于 SUCCESS，不持久化正常 assistant/citations |

检查可以在不同层重复执行，但实现和成功不变量只有一个。仓库不另写一套数值 policy，而是要求应用传入同一检查回调，并在原子事务锁内重跑。必要 smoke/直接 repository 测试调用方已更新；没有回调的低层成功提交会报 FINAL_COMMIT_CHECK_REQUIRED。失败/取消不要求伪造成功检查。

## 8. 原子性、取消及失败终态

保留 knowledge_repository.py:1239 的 rag_runs FOR UPDATE 和 Agent terminal 锁、事件/快照/assistant message 同一事务。SQL source/quote 检查在任何成功 artifact INSERT 之前完成，不限制引用必须属于 active version，历史 revision 仍能读取。

AnswerService 在执行后与提交前沿用取消检查；锁内发现非 running/created 返回 False，迟到结果丢弃。FinalAnswerCommitError 先回滚成功事务，再仅提交 failed terminal；若此时取消已赢得终态锁，返回取消。不会因为 dangling quote 报错后仍写入成功 answer_evidence/assistant。

仅实际引用的 snapshots 被提交，assistant 消息继续记录 run_id。无历史源文件下载新 API。生产原子性依赖现有 PostgreSQL adapter；旧测试中无 atomic finalizer 的 recording stores 不是数据库原子性证明，本轮不把这些模拟事件当真实并发事务实测。

## 9. Version 与 Final Commit 验证覆盖

| 附件要求 | 离线证据 |
|---|---|
| A1 同格式版本 | test_a1_same_format_activation_keeps_both_revision_metadata |
| A2 PDF→DOCX candidate/activate | test_a2_cross_format_candidate_does_not_change_active_semantics |
| A3 candidate failure | test_a3_failed_candidate_preserves_active_blob_and_metadata；生产 failure SQL recording |
| A4 旧 job queued | test_a4_queued_old_job_gets_its_own_name_type_and_blob；真实 repository process_job + RecordingParser |
| A5 source/preview | test_a5_production_source_readback_and_http_response_match_selected_version，active/no-active 两分支 |
| 历史 citation | test_historical_citation_keeps_old_version_and_quote_after_document_changes + 原 citation/evidence tests |
| B7 normal PASS | test_b7_normal_exact_numeric_candidate_passes |
| B8 invalid E99 | test_b8_unknown_citation_fails + Quick/Smart service 注入反例 |
| B9 fallback invalid E99 | test_b9_b10_fallback_cannot_clear_error_without_final_check |
| B10 fallback wrong value | 同上 + entity/month/unit、foreign header/formula/cache/merge 反例 |
| B11 caption number | test_b11_caption_number_is_never_original_numeric_evidence + 原 caption_fact_guard |
| B12 truncation | test_b12_length_rejected_even_with_valid_answer_or_available_fallback、Smart finish_reason、原审计测试 |
| B13 cancellation | repository cancellation recording + 原 cancellation_boundaries；真实 DB race 未运行 |
| message-run link | SQL 成功提交记录与历史 readback、原 fixture 契约；test_message_run_link 的真实 DB 版本 NOT RUN |
| rollback failure | test_transactional_snapshot_failure_becomes_failed_terminal_without_answer |
| 类型边界 | exact source_excerpt、partial covered row、duplicate/invalid snapshot、missing policy callback |

新增测试文件明确标为 SIMULATED。实际运行的是 Python 测试代码、真实 application/repository 方法、标准库及本地文件操作；Parser/模型/SQL响应在指定测试中为模拟。未把模拟 SQL 记录、ScriptedChatModel 或 Mock 当真实 PostgreSQL/模型通过。

## 10. 测试命令、退出码与数量

命令完整原文见附录 A；JUnit 保留初次失败和修正后的结果，不覆盖失败证据。

| 执行 | exit | PASS | FAIL | ERROR | SKIP | 证据 |
|---|---:|---:|---:|---:|---:|---|
| targeted 初次 | 1 | 209 | 1 | 30 | 0 | var/reports/p1-targeted.xml |
| targeted 可写 temp 后 | 1 | 236 | 4 | 0 | 0 | p1-targeted-2.xml |
| targeted 第一轮完成 | 0 | 240 | 0 | 0 | 0 | p1-targeted-3.xml |
| 新增失败终态/partial 反例时 | 1 | 241 | 1 | 0 | 0 | p1-targeted-final.xml |
| **最终 targeted（16 文件）** | **0** | **245** | **0** | **0** | **0** | **p1-targeted-final2.xml** |
| regression 初次（96 文件） | 1 | 995 | 9 | 20 | 134 | p1-regression-1.xml |
| **最终 regression（96 文件）** | **1** | **1000** | **9** | **20** | **134** | **p1-regression-final.xml** |
| 原 HEAD 失败子集独立快照 | 1 | 1 | 9 | 20 | 0 | p1-preexisting-baseline.xml；另 7 deselected |
| Alembic 0013→0014 --sql | 0 | 非测试计数 | — | — | — | 实际三条 ADD COLUMN，未连 DB |
| 当前 contract_test | 1 | 不汇总为测试通过 | — | — | — | p1-contract.json |
| 原 HEAD contract_test | 1 | 同上 | — | — | — | p1-contract-baseline.json |
| XML 失败集合/契约比较 | 0 | 29 项完全同集合；3 import违规完全一致 | — | — | — | 附录命令 |
| 21 个改动 Python AST | 0 | 21 语法读取通过，不是执行验证 | — | — | — | p1-changed-source-manifest.json |
| git diff --check | 0 | 无空白错误；有已有 CRLF 提示 | — | — | — | 工具记录 |

245 项是广泛回归的子集，不能把两轮相加声称 1245 个唯一测试。最后 targeted 实际 stdout 245 passed；最后 regression 实际 stdout 1000 passed / 9 failed / 134 skipped / 20 errors。**整个离线 regression 与 contract_test 都不是全绿。**

## 11. 失败归因与基线对照

修正本任务中的问题，不将其冒称既有：

- 初次 30 ERROR：沙箱默认 Temp 无法创建 pytest numbered dir。后续明确使用当前工作区 var 下的可写专用 TEMP/TMP/basetemp，没有提权、关闭保护或读业务数据。
- 初次 1 FAIL：旧 Smart 模拟答案只有文字 E1 而 citations 声明 E1，没有 [E1]。夹具修正引用格式，未放宽合同。
- 第二轮 4 FAIL：新增 RecordingParser 夹具缺 NormalizedDocument 的必需 media_type/content_sha256；补齐真实契约字段。
- 新增事务失败测试的 1 FAIL：用真实检索去准备该特定反例得到 NO_CANDIDATES；改为显式注入合法 AnswerResult，只测试其声明的 commit rollback 边界，没有修改检索或阈值。
- 长度旧测试现在要求拒绝成功；新保护更严格。未删除旧测试。

广泛回归的所有 29 个 FAIL/ERROR 用例在原 HEAD 的独立 tracked-source 快照重现，最终失败集合比较为 True：

| 类别 | 数量 | 因果证据 / 分级 |
|---|---:|---|
| layering | 1 FAIL | 原 HEAD 同样 3 条跨层 import：knowledge_repository→application.context_expansion；local_caption→adapters.models.ollama；ports.retrieval→application.context_expansion。P1 未新增此类违规 |
| legacy DOC fixture | 1 FAIL | 原 HEAD 同样缺显式 native fixture table.docx；ENVIRONMENT / OUT_OF_SCOPE |
| native product-gap DOCX | 6 FAIL + 20 ERROR | 原 HEAD 同样 NATIVE_RUNTIME_UNAVAILABLE；ENVIRONMENT / OUT_OF_SCOPE |
| multimodal OCR | 1 FAIL | 原 HEAD 同样期待 OCR_EMPTY 而本环境返回 OCR_UNAVAILABLE；PREEXISTING 环境相关断言；未改 Parser/OCR |

复现快照在 ignored `var/p1-baseline-source`，由 `git archive HEAD` 的明确 backend/app、backend/tests、contract 脚本和契约文件生成，共 232 个 tracked 文件；没有 checkout/reset 或读取业务数据。该快照测试只运行失败子集，不冒充原 HEAD 全量回归。

当前与 baseline contract 均为 FAIL，但 actual_paths=23、missing/unexpected=[]、openapi_snapshot_drift=false、sse_sample_errors=[]；两者 3 条 import违规完全一致。未为全绿修改无关分层或 native/OCR 代码。此为因果分类，不是负责人批准延期；若要发布或下一阶段依赖这些能力，仍需负责人安排环境/修复与验收。

## 12. 未运行、跳过和环境边界

- **NOT RUN / 未提供明确隔离实例**：document_version_boundaries、document_version_route、ingestion_recovery、message_run_link、全部 test_postgres*.py 的真实数据库测试。没有尝试生产/默认数据库连接或用 skip 掩盖连接。
- **NOT RUN**：真实 DB migration current/upgrade/downgrade、服务启动、真实模型、DeepSeek/SiliconFlow/Langfuse、ollama probe、verify-mX/release、浏览器 E2E、smoke_v1_repair.py。
- 广泛回归排除会启动本地 HTTP 伪服务的 test_ollama_usage，未运行 eval_center 的 HTTP server 测试；未运行无关 provider/ledger 测试以避免触及外部调用/账本范围。未读取全局 DeepSeek ledger，保留禁用状态和既有记录。
- 134 SKIP 来自显式 native runtime/fixture/测试临时根或本地 OCR 数据缺失；SKIP 不是 PASS。相关 native fixture 的 20 ERROR 没有伪改为 skip。
- .venv Python3.13.0 / pytest9.1.1 实际可运行；每次入口有 `Failed to find real location of D:\Drivers\python\python.exe` 提示，未隐瞒，未修环境/安装新依赖。
- 测试生成的 var 下数据、JUnit、源码基线快照和临时副本均为本轮测试证据/夹具；未写任何真实业务 DB/原件/index。全局 auth/provider/隐私/审批/沙箱设置未改。TEMP/RAG 测试开关仅对应命令的进程环境。

## 13. 未解决风险与后续前置

1. 真正 PostgreSQL DDL、约束和 cancel/commit 并发行为仅保留原 SQL 实现并做离线调用/SQL生成验证；真实隔离 DB 验收 NOT RUN。未来部署前须另行授权该环境，不冒充已上线。
2. 旧版本精确 name/type 已丢失的事实无法靠代码恢复。现在明确 unknown、原字节可回读、旧引用保留、重摄取 fail-closed；可靠补录或重传属于后续明确任务。
3. 私有命名副本增加每次 ingestion 的一个源文件大小临时空间和复制 IO；临时目录不可用时任务明确失败。没有改 Parser 算法，也没有引入长期重复原件。
4. Final check 是轻量确定性边界，不保证任意语义 entailment/事实正确性。原 heuristic 未扩充或用作全局质量声明。
5. 引用 locator 仍为原 Snapshot JSON 结构，不新增深不可变/locator 签名；P0 的 PDF 原始证据持久化和用量缺口仍未解决，均在 P1 禁止范围内。
6. 广泛回归/contract 既有失败未修、未获延期批准；P1_PASS 不代表发布门禁通过或批准下一阶段。
7. 新 repository 成功入口必须带统一检查回调。已检索 backend/scripts/eval_center 的直接调用点并适配必要 tests/smoke；任何未来调用方都须遵守，不能用无条件成功回调冒充业务检查。

## 14. Git diff summary 与证据身份

最终 HEAD/branch 未改变。已跟踪 diff：16 files，159 insertions、42 deletions（文本行替换，不是删模块）；另 5 新 Python 文件与本报告未包含在 git diff --stat 的 tracked 统计中。21 个改动 Python 文件的 SHA-256 见附录 B。

对 P0 的 397 个源文件清单重新计算：16 个变化均为本轮明确修改文件，381 个保持相同，无无法解释的变化。P0 报告自身 hash 不变。没有用户既有文件被覆盖/清理。最终 git status 只包括上述 P1 交付文件和原有 P0 报告；测试产物在 ignored var 下。

## 15. 最终状态与停止

**P1_PASS**。两个指定问题均完成：新 Version 自身解释事实被冻结、候选/旧 job/source 一致；所有产品最终成功回答统一经过 FinalAnswerCommitCheck，fallback/partial/evidence-only/Smart 不再以路径区别绕过强边界，length/invalid citation/可确定的 structured mismatch/cancel 不产生成功答案。

该结论依据最终 245 项定向测试及广泛回归的基线因果证据。没有宣称整个仓库测试全绿，没有真实 DB/模型/部署验收，没有 commit/Tag，没有批准删除、替换、延期或里程碑。完成本报告后立即停止，等待 P2 新指令。


## 附录 A. 实际执行命令

除明确标注 baseline cwd 外，工作目录均为本报告第 1 节的实际仓库。下列命令是实际工具调用原文；不包含凭据。初次失败产物保留用于解释迭代，不作为最终通过证据。

### 定向测试初次 — exit 1

```powershell
$env:RAG_CLOUD_ENABLED='false'; $env:RAG_LANGFUSE_ENABLED='false'; $env:RAG_STORAGE_ROOT='var/p1-test-storage'; & .\.venv\Scripts\python.exe -B -m pytest -q --tb=short -p no:cacheprovider backend/tests/test_version_source_contract.py backend/tests/test_final_answer_commit.py backend/tests/test_version_activation.py backend/tests/test_ingestion_state_machine.py backend/tests/test_evidence.py backend/tests/test_evidence_accumulator.py backend/tests/test_citation_resolution.py backend/tests/test_answer_validation.py backend/tests/test_answer_hardening.py backend/tests/test_structured_evidence.py backend/tests/test_caption_fact_guard.py backend/tests/test_cancellation_boundaries.py backend/tests/test_answer_service.py backend/tests/test_quick_evidence_flow.py backend/tests/test_langchain_agent.py backend/tests/test_preview_contract.py --junitxml=var/reports/p1-targeted.xml
```

### 定向测试第二次 — exit 1

```powershell
$env:TEMP=(Join-Path (Get-Location) 'var/p1-tmp-runtime'); $env:TMP=$env:TEMP; New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null; $env:RAG_CLOUD_ENABLED='false'; $env:RAG_LANGFUSE_ENABLED='false'; $env:RAG_STORAGE_ROOT='var/p1-test-storage'; & .\.venv\Scripts\python.exe -B -m pytest -q --tb=short -p no:cacheprovider backend/tests/test_version_source_contract.py backend/tests/test_final_answer_commit.py backend/tests/test_version_activation.py backend/tests/test_ingestion_state_machine.py backend/tests/test_evidence.py backend/tests/test_evidence_accumulator.py backend/tests/test_citation_resolution.py backend/tests/test_answer_validation.py backend/tests/test_answer_hardening.py backend/tests/test_structured_evidence.py backend/tests/test_caption_fact_guard.py backend/tests/test_cancellation_boundaries.py backend/tests/test_answer_service.py backend/tests/test_quick_evidence_flow.py backend/tests/test_langchain_agent.py backend/tests/test_preview_contract.py --basetemp=var/p1-pytest-targeted-2 --junitxml=var/reports/p1-targeted-2.xml
```

### 定向测试第一轮完成 — exit 0

```powershell
$env:TEMP=(Join-Path (Get-Location) 'var/p1-tmp-runtime'); $env:TMP=$env:TEMP; New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null; $env:RAG_CLOUD_ENABLED='false'; $env:RAG_LANGFUSE_ENABLED='false'; $env:RAG_STORAGE_ROOT='var/p1-test-storage'; & .\.venv\Scripts\python.exe -B -m pytest -q --tb=short -p no:cacheprovider backend/tests/test_version_source_contract.py backend/tests/test_final_answer_commit.py backend/tests/test_version_activation.py backend/tests/test_ingestion_state_machine.py backend/tests/test_evidence.py backend/tests/test_evidence_accumulator.py backend/tests/test_citation_resolution.py backend/tests/test_answer_validation.py backend/tests/test_answer_hardening.py backend/tests/test_structured_evidence.py backend/tests/test_caption_fact_guard.py backend/tests/test_cancellation_boundaries.py backend/tests/test_answer_service.py backend/tests/test_quick_evidence_flow.py backend/tests/test_langchain_agent.py backend/tests/test_preview_contract.py --basetemp=var/p1-pytest-targeted-3 --junitxml=var/reports/p1-targeted-3.xml
```

### 补充反例首次 — exit 1

```powershell
$env:TEMP=(Join-Path (Get-Location) 'var/p1-tmp-runtime'); $env:TMP=$env:TEMP; New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null; $env:RAG_CLOUD_ENABLED='false'; $env:RAG_LANGFUSE_ENABLED='false'; $env:RAG_STORAGE_ROOT='var/p1-test-storage'; & .\.venv\Scripts\python.exe -B -m pytest -q --tb=short -p no:cacheprovider backend/tests/test_version_source_contract.py backend/tests/test_final_answer_commit.py backend/tests/test_version_activation.py backend/tests/test_ingestion_state_machine.py backend/tests/test_evidence.py backend/tests/test_evidence_accumulator.py backend/tests/test_citation_resolution.py backend/tests/test_answer_validation.py backend/tests/test_answer_hardening.py backend/tests/test_structured_evidence.py backend/tests/test_caption_fact_guard.py backend/tests/test_cancellation_boundaries.py backend/tests/test_answer_service.py backend/tests/test_quick_evidence_flow.py backend/tests/test_langchain_agent.py backend/tests/test_preview_contract.py --basetemp=var/p1-pytest-targeted-final --junitxml=var/reports/p1-targeted-final.xml
```

### 最终定向测试 — exit 0

```powershell
$env:TEMP=(Join-Path (Get-Location) 'var/p1-tmp-runtime'); $env:TMP=$env:TEMP; New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null; $env:RAG_CLOUD_ENABLED='false'; $env:RAG_LANGFUSE_ENABLED='false'; $env:RAG_STORAGE_ROOT='var/p1-test-storage'; & .\.venv\Scripts\python.exe -B -m pytest -q --tb=short -p no:cacheprovider backend/tests/test_version_source_contract.py backend/tests/test_final_answer_commit.py backend/tests/test_version_activation.py backend/tests/test_ingestion_state_machine.py backend/tests/test_evidence.py backend/tests/test_evidence_accumulator.py backend/tests/test_citation_resolution.py backend/tests/test_answer_validation.py backend/tests/test_answer_hardening.py backend/tests/test_structured_evidence.py backend/tests/test_caption_fact_guard.py backend/tests/test_cancellation_boundaries.py backend/tests/test_answer_service.py backend/tests/test_quick_evidence_flow.py backend/tests/test_langchain_agent.py backend/tests/test_preview_contract.py --basetemp=var/p1-pytest-targeted-final2 --junitxml=var/reports/p1-targeted-final2.xml
```

### 广泛回归初次 — exit 1

```powershell
$env:TEMP=(Join-Path (Get-Location) 'var/p1-tmp-runtime'); $env:TMP=$env:TEMP; $env:RAG_CLOUD_ENABLED='false'; $env:RAG_LANGFUSE_ENABLED='false'; $env:RAG_STORAGE_ROOT='var/p1-test-storage'; $env:RAG_NATIVE_TEST_PYTHON=''; $env:RAG_NATIVE_TEST_FIXTURES=''; $env:RAG_NATIVE_TEST_TMP=''; $env:RAG_OFFLINE_HINT_ARTIFACTS=''; $env:RAG_OFFLINE_SMART_HINT_ARTIFACTS=''; & .\.venv\Scripts\python.exe -B -m pytest -q --tb=short -p no:cacheprovider backend/tests/test_agent_limits.py backend/tests/test_agent_tool_allowlist.py backend/tests/test_agent_trace_sse.py backend/tests/test_answer_hardening.py backend/tests/test_answer_service.py backend/tests/test_answer_validation.py backend/tests/test_backup_manifest.py backend/tests/test_bootstrap.py backend/tests/test_bounded_history.py backend/tests/test_budget_gate.py backend/tests/test_cancel_route.py backend/tests/test_cancellation_boundaries.py backend/tests/test_caption_fact_guard.py backend/tests/test_chunk_strategy.py backend/tests/test_chunking.py backend/tests/test_citation_group_span.py backend/tests/test_citation_resolution.py backend/tests/test_clarification_persistence.py backend/tests/test_config.py backend/tests/test_container_injection.py backend/tests/test_context_and_locators.py backend/tests/test_context_expansion.py backend/tests/test_context_neighbor_repository.py backend/tests/test_context_pool_policy.py backend/tests/test_conversation_scope.py backend/tests/test_egress_matrix.py backend/tests/test_embedding_profile_isolation.py backend/tests/test_evidence.py backend/tests/test_evidence_accumulator.py backend/tests/test_follow_up.py backend/tests/test_fusion.py backend/tests/test_graph_evidence.py backend/tests/test_graph_failure_isolation.py backend/tests/test_graph_scope_sql.py backend/tests/test_health.py backend/tests/test_hybrid_retrieval.py backend/tests/test_ingestion_claiming.py backend/tests/test_ingestion_lease_config.py backend/tests/test_ingestion_retry_route.py backend/tests/test_ingestion_state_machine.py backend/tests/test_inline_citation_group.py backend/tests/test_knowledge_scope_boundaries.py backend/tests/test_knowledge_tools.py backend/tests/test_langchain_agent.py backend/tests/test_langchain_quick_chain.py backend/tests/test_layering_preview.py backend/tests/test_legacy_doc.py backend/tests/test_llm_rerank.py backend/tests/test_local_caption_enricher.py backend/tests/test_model_policy.py backend/tests/test_multimodal_ingestion.py backend/tests/test_native_release.py backend/tests/test_native_release_review_regressions.py backend/tests/test_native_table_evidence.py backend/tests/test_original_header_boundary.py backend/tests/test_parsers.py backend/tests/test_pdf_ocr.py backend/tests/test_pdf_table_evidence.py backend/tests/test_preview_contract.py backend/tests/test_privacy_configuration_answer.py backend/tests/test_product_gap_contract.py backend/tests/test_quality_eval_schema.py backend/tests/test_quality_gate.py backend/tests/test_query_coverage.py backend/tests/test_question_checklist.py backend/tests/test_question_checklist_prompt_replay.py backend/tests/test_quick_answer_language_prompt.py backend/tests/test_quick_chain_budget.py backend/tests/test_quick_chain_quality.py backend/tests/test_quick_citation_prompt.py backend/tests/test_quick_evidence_flow.py backend/tests/test_reference_profile.py backend/tests/test_release_preflight.py backend/tests/test_restore_invariants.py backend/tests/test_retrieval_contract.py backend/tests/test_retrieval_execution_record.py backend/tests/test_retrieval_provenance.py backend/tests/test_retrieval_routing.py backend/tests/test_run_metrics.py backend/tests/test_schema_check.py backend/tests/test_scope.py backend/tests/test_scope_and_graph_routes.py backend/tests/test_smart_citation_replay.py backend/tests/test_smart_table_header_hint.py backend/tests/test_storage.py backend/tests/test_structured_answer_spacing.py backend/tests/test_structured_evidence.py backend/tests/test_table_header_hint.py backend/tests/test_targeted_merge_bound.py backend/tests/test_text_normalization.py backend/tests/test_version_activation.py backend/tests/test_xlsx_resource_limits.py backend/tests/test_xlsx_storage_paths.py backend/tests/test_xlsx_table_evidence.py backend/tests/test_version_source_contract.py backend/tests/test_final_answer_commit.py --basetemp=var/p1-pytest-regression-1 --junitxml=var/reports/p1-regression-1.xml
```

### 最终广泛回归 — exit 1

```powershell
$env:TEMP=(Join-Path (Get-Location) 'var/p1-tmp-runtime'); $env:TMP=$env:TEMP; $env:RAG_CLOUD_ENABLED='false'; $env:RAG_LANGFUSE_ENABLED='false'; $env:RAG_STORAGE_ROOT='var/p1-test-storage'; $env:RAG_NATIVE_TEST_PYTHON=''; $env:RAG_NATIVE_TEST_FIXTURES=''; $env:RAG_NATIVE_TEST_TMP=''; $env:RAG_OFFLINE_HINT_ARTIFACTS=''; $env:RAG_OFFLINE_SMART_HINT_ARTIFACTS=''; & .\.venv\Scripts\python.exe -B -m pytest -q --tb=short -p no:cacheprovider backend/tests/test_agent_limits.py backend/tests/test_agent_tool_allowlist.py backend/tests/test_agent_trace_sse.py backend/tests/test_answer_hardening.py backend/tests/test_answer_service.py backend/tests/test_answer_validation.py backend/tests/test_backup_manifest.py backend/tests/test_bootstrap.py backend/tests/test_bounded_history.py backend/tests/test_budget_gate.py backend/tests/test_cancel_route.py backend/tests/test_cancellation_boundaries.py backend/tests/test_caption_fact_guard.py backend/tests/test_chunk_strategy.py backend/tests/test_chunking.py backend/tests/test_citation_group_span.py backend/tests/test_citation_resolution.py backend/tests/test_clarification_persistence.py backend/tests/test_config.py backend/tests/test_container_injection.py backend/tests/test_context_and_locators.py backend/tests/test_context_expansion.py backend/tests/test_context_neighbor_repository.py backend/tests/test_context_pool_policy.py backend/tests/test_conversation_scope.py backend/tests/test_egress_matrix.py backend/tests/test_embedding_profile_isolation.py backend/tests/test_evidence.py backend/tests/test_evidence_accumulator.py backend/tests/test_follow_up.py backend/tests/test_fusion.py backend/tests/test_graph_evidence.py backend/tests/test_graph_failure_isolation.py backend/tests/test_graph_scope_sql.py backend/tests/test_health.py backend/tests/test_hybrid_retrieval.py backend/tests/test_ingestion_claiming.py backend/tests/test_ingestion_lease_config.py backend/tests/test_ingestion_retry_route.py backend/tests/test_ingestion_state_machine.py backend/tests/test_inline_citation_group.py backend/tests/test_knowledge_scope_boundaries.py backend/tests/test_knowledge_tools.py backend/tests/test_langchain_agent.py backend/tests/test_langchain_quick_chain.py backend/tests/test_layering_preview.py backend/tests/test_legacy_doc.py backend/tests/test_llm_rerank.py backend/tests/test_local_caption_enricher.py backend/tests/test_model_policy.py backend/tests/test_multimodal_ingestion.py backend/tests/test_native_release.py backend/tests/test_native_release_review_regressions.py backend/tests/test_native_table_evidence.py backend/tests/test_original_header_boundary.py backend/tests/test_parsers.py backend/tests/test_pdf_ocr.py backend/tests/test_pdf_table_evidence.py backend/tests/test_preview_contract.py backend/tests/test_privacy_configuration_answer.py backend/tests/test_product_gap_contract.py backend/tests/test_quality_eval_schema.py backend/tests/test_quality_gate.py backend/tests/test_query_coverage.py backend/tests/test_question_checklist.py backend/tests/test_question_checklist_prompt_replay.py backend/tests/test_quick_answer_language_prompt.py backend/tests/test_quick_chain_budget.py backend/tests/test_quick_chain_quality.py backend/tests/test_quick_citation_prompt.py backend/tests/test_quick_evidence_flow.py backend/tests/test_reference_profile.py backend/tests/test_release_preflight.py backend/tests/test_restore_invariants.py backend/tests/test_retrieval_contract.py backend/tests/test_retrieval_execution_record.py backend/tests/test_retrieval_provenance.py backend/tests/test_retrieval_routing.py backend/tests/test_run_metrics.py backend/tests/test_schema_check.py backend/tests/test_scope.py backend/tests/test_scope_and_graph_routes.py backend/tests/test_smart_citation_replay.py backend/tests/test_smart_table_header_hint.py backend/tests/test_storage.py backend/tests/test_structured_answer_spacing.py backend/tests/test_structured_evidence.py backend/tests/test_table_header_hint.py backend/tests/test_targeted_merge_bound.py backend/tests/test_text_normalization.py backend/tests/test_version_activation.py backend/tests/test_xlsx_resource_limits.py backend/tests/test_xlsx_storage_paths.py backend/tests/test_xlsx_table_evidence.py backend/tests/test_version_source_contract.py backend/tests/test_final_answer_commit.py --basetemp=var/p1-pytest-regression-final --junitxml=var/reports/p1-regression-final.xml
```

### 原 HEAD 源码快照（仅首次创建，232 files） — exit 0

```powershell
@'
import io, subprocess, tarfile
from pathlib import Path
root=Path('var/p1-baseline-source').resolve()
root.mkdir(parents=True,exist_ok=False)
data=subprocess.check_output(['git','archive','HEAD','backend/app','backend/tests','scripts/contract_test.py','pyproject.toml','contracts'])
count=0
with tarfile.open(fileobj=io.BytesIO(data)) as archive:
 for member in archive.getmembers():
  target=(root/member.name).resolve()
  if not target.is_relative_to(root): raise ValueError('unsafe archive path')
  if member.isdir(): target.mkdir(parents=True,exist_ok=True)
  elif member.isfile():
   target.parent.mkdir(parents=True,exist_ok=True)
   target.write_bytes(archive.extractfile(member).read()); count+=1
  else: raise ValueError('unexpected archive member')
print('DISPOSABLE_TRACKED_SOURCE_SNAPSHOT', count, 'HEAD=dd4ca9eec47f2d19ca57fcdab2f6780c80f819fb')
'@ | & .\.venv\Scripts\python.exe -B -
```

### 原 HEAD 失败子集（cwd=var/p1-baseline-source） — exit 1

```powershell
$env:TEMP=(Join-Path (Get-Location) '../../var/p1-tmp-runtime'); $env:TMP=$env:TEMP; $env:RAG_CLOUD_ENABLED='false'; $env:RAG_LANGFUSE_ENABLED='false'; $env:RAG_STORAGE_ROOT='var/p1-test-storage'; $env:RAG_NATIVE_TEST_PYTHON=''; $env:RAG_NATIVE_TEST_FIXTURES=''; & 'C:\Users\22088\.codex\worktrees\rag-retrieval-opt-round1\RAG quention\.venv\Scripts\python.exe' -B -m pytest -q --tb=short -p no:cacheprovider backend/tests/test_layering_preview.py::test_declared_layering_has_no_violations backend/tests/test_legacy_doc.py::test_simulated_doc_conversion_reuses_real_table_citation_chain backend/tests/test_multimodal_ingestion.py::test_pdf_and_image_keep_source_locators_and_image_failure_is_recoverable backend/tests/test_product_gap_contract.py -k 'not default_runtime and not ambiguous_unbounded and not original_question and not resolver' --basetemp=var/p1-pytest-baseline --junitxml=../reports/p1-preexisting-baseline.xml
```

### 当前 contract — exit 1

```powershell
$env:RAG_CLOUD_ENABLED='false'; $env:RAG_LANGFUSE_ENABLED='false'; $env:RAG_STORAGE_ROOT='var/p1-test-storage'; & .\.venv\Scripts\python.exe -B scripts/contract_test.py --report var/reports/p1-contract.json
```

### 原 HEAD contract（cwd=var/p1-baseline-source） — exit 1

```powershell
$env:RAG_CLOUD_ENABLED='false'; $env:RAG_LANGFUSE_ENABLED='false'; $env:RAG_STORAGE_ROOT='var/p1-test-storage'; & 'C:\Users\22088\.codex\worktrees\rag-retrieval-opt-round1\RAG quention\.venv\Scripts\python.exe' -B scripts/contract_test.py --report ../reports/p1-contract-baseline.json
```

### 最终源哈希/AST及既有失败集合检查 — exit 0

```powershell
@'
import ast, hashlib, json, xml.etree.ElementTree as ET
from pathlib import Path
manifest=json.loads(Path("var/reports/p1-changed-source-manifest.json").read_text(encoding="utf-8"))
for row in manifest:
 p=Path(row["path"])
 assert hashlib.sha256(p.read_bytes()).hexdigest()==row["sha256"], str(p)
 ast.parse(p.read_text(encoding="utf-8-sig"), filename=str(p))
print("CHANGED_SOURCE_HASHES_AND_AST",len(manifest),"MATCH")
p0=Path("docs/audits/p0-weknora-transition-current-state.md")
assert hashlib.sha256(p0.read_bytes()).hexdigest()=="2eb447a96ed258d92c9c504577caac66a49cd3680cba02ff7c38b0471e5defda"
print("P0_REPORT_HASH MATCH")
def failures(name):
 root=ET.parse(Path("var/reports")/name).getroot()
 return {(c.attrib.get("classname"),c.attrib.get("name"),e.tag) for c in root.iter("testcase") for e in c if e.tag in ("failure","error")}
a=failures("p1-regression-final.xml"); b=failures("p1-preexisting-baseline.xml")
assert a==b
print("FINAL_FAILURE_SET_EQUALS_BASELINE",a==b,"COUNT",len(a))
a=json.loads(Path("var/reports/p1-contract.json").read_text(encoding="utf-8-sig"))
b=json.loads(Path("var/reports/p1-contract-baseline.json").read_text(encoding="utf-8-sig"))
assert a["layering_violations"]==b["layering_violations"]
print("LAYERING_IMPORTS_EQUALS_BASELINE",True,"COUNT",len(a["layering_violations"]))
'@ | & .\.venv\Scripts\python.exe -B -
```

### 离线 migration SQL — exit 0

```powershell
$env:RAG_DATABASE_URL='postgresql+psycopg://offline:offline@127.0.0.1:1/offline'; & .\.venv\Scripts\python.exe -B -m alembic upgrade 0013_message_run_link:0014_version_source_metadata --sql
```

这是显式虚拟占位 URL，只用于离线 SQL 生成，未连接数据库。

### 最终 Git 命令 — 分别 exit 0

```powershell
git status --short --untracked-files=all
git rev-parse HEAD
git branch --show-current
git diff --check
```

语法读取和 XML 比较不计作产品测试。最终 targeted 用时 4.72s；regression 用时 65.14s，均为 pytest stdout 报告时间，不代表整个任务耗时。

探索性只读定位曾遇到不存在路径（evidence_service.py、adapters/postgres/models.py、adapters/langchain_agent.py、test_document_source_route.py、scripts/schema_check.py）以及一次 PowerShell 的 rg glob 错误 exit 123；之后采用真实文件/符号定位，不把失败读取作为实现或验证证据。没有审批拒绝或通过换路径绕过权限。

## 附录 B. 改动源码 SHA-256

最终写报告前重新读取 21 个 Python 文件并与以下清单逐项比较，全部 MATCH，AST 解析 21 个通过。报告新增后再检查 Git 状态。报告自身不纳入递归 hash。

| 文件 | SHA-256 |
|---|---|
| alembic/versions/0014_version_source_metadata.py | `82d2f269a865f5ab500f7bbcd6ea8d090b4c225c5845f9f73082b4ceb1777f00` |
| backend/app/adapters/postgres/knowledge_repository.py | `4433008309cdfc9ebc33216be99e683479544e0c509b5aca949cf7d7de0c2eb0` |
| backend/app/application/agent_ports.py | `cd57e7e0398b2e9499f6e3e9f0e47ae00822e68a168f70c7d71deb22d69da97d` |
| backend/app/application/answer_hardening.py | `51a9fa85c75f0b2c870277adcd870445abaefd72b1899fa60063067ad2e4b157` |
| backend/app/application/answer_service.py | `3beef12f8d12503a16ab4d9ca61b4309a2cf7d867eae7363432237338b343324` |
| backend/app/application/final_answer_commit.py | `84004784ec5347513d18722cab202e0ddca97c3d03bd0b61cae0ed772c45e44f` |
| backend/app/application/ingestion.py | `51253a46028ef098d38f13c20308ceb6fac800677c0ca624736c96418b1db50d` |
| backend/app/application/knowledge_gateway.py | `7867728274733bca3b0aaeccb3903031d9e7ee29ea04db7a6a486746dab7107c` |
| backend/app/application/langchain_agent.py | `c7189c741bb1b50fe33b72640fc2b96b6f64c5f9fa5d7b56840a3daad6cfc3fd` |
| backend/app/application/quick_chain.py | `0511fcf3dff4ac3b17cc7ce0f11adee5d9b3d09d6e790ce49e4e2183f0f32c39` |
| backend/app/domain/errors.py | `2fab0b45aa0c8a405a3254888bc10f7465f483fd8758e2370ed36aa5e4a9bb5f` |
| backend/app/domain/version_source.py | `274d19c881800937c8c430474224e870924bc623ead9767ec4577547cd091b65` |
| backend/app/ports/ingestion.py | `34b9cb21fae51f673b685b4e3077a7e5ebbd972db211c56c821520c0fe139967` |
| backend/tests/test_answer_hardening.py | `5c04b22a4547fd87135d8918dc7e4a62fe6e587efc055b0452ced51b29df3e46` |
| backend/tests/test_answer_service.py | `3e19934a88b96b3eaf8dcf4cb52fadf3c30b27f839cf2984dafdd4772098ed1c` |
| backend/tests/test_final_answer_commit.py | `8ea9e59df834503658811eff32a2c718c7961cc642fe13d4145034ce15f5ce57` |
| backend/tests/test_ingestion_state_machine.py | `716e8a474ed22fc0ae715dd72dea3a82412ee35968909bb2a47544a6cdaf4a58` |
| backend/tests/test_message_run_link.py | `44974e96dcea4a83200d04fbe42b72bf3406e19448df68eceab7cfdbaec17c79` |
| backend/tests/test_postgres_run_terminal_states.py | `e6b31bf74fb3c3226af248819e35180785f8d913d11b66cb76eb08e92a482a32` |
| backend/tests/test_version_source_contract.py | `1d1d0652a4c1b9ed01dab532514b6775016bfca0c3958b1ccc53573e3fbda8cc` |
| scripts/smoke_v1_repair.py | `a8fe66ba3ae0ec59f0af738def8af51a82e8a2f0ecb24bf3d8fd974cf3974003` |

### JUnit 证据 SHA-256

以下产物在 ignored var/reports，随本地证据保留，未提交 Git。

| 文件 | SHA-256 |
|---|---|
| var/reports/p1-preexisting-baseline.xml | `2055e589279fefcd453580559869cabf4094de74c0726d1d162576b871ee9c37` |
| var/reports/p1-regression-1.xml | `5895a170b0a3f7dc1f8c33761c24c752019924f9050e0f05e0e86301d4a8250a` |
| var/reports/p1-regression-final.xml | `1596f3f5800448932cf107edfe74e98bdc5f2d6764f8e9f76bbc4ee4ad6ea8ec` |
| var/reports/p1-targeted-2.xml | `2890a1cbbe985584ca60bce1760bacdb3dac924026ae26becae1b2571f50812d` |
| var/reports/p1-targeted-3.xml | `6f78a35b392a6280c14de1e2d4db9e0975f52883adb26cbe56055d34206e136c` |
| var/reports/p1-targeted-final.xml | `d3ef7bef456e1a56ea9956148a5d74d9f40dc6c7a72c8497d4c544ecb699349e` |
| var/reports/p1-targeted-final2.xml | `74bbf076adf74f55d97bbfce0ff4d9c0929dfb8bc0fb35bb28b7f436e2235c6a` |
| var/reports/p1-targeted.xml | `40ee10b356db9e8e31aef8d9ebf9eaaff5755cb04d5abd29ffe7e8664e327df1` |

