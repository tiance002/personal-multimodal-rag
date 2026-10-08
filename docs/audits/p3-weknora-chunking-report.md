# P3 复核修正：当前结论与证据（2026-10-08）

**CHECKS_PASSED / NEEDS_OWNER_REVIEW；Embedding 模型输入容量合同待决。**
本节是当前结论，下方原报告全文仅作为修正前历史记录保留。旧报告中的 512 字符输入护栏、错误码与旧测试计数不再描述当前源码；本轮没有批准 P3 验收、上线或进入 P4。

## 目标与实际改动

仅修正用户指定的长表短行保护与切块目标/Embedding 输入容量耦合。HEAD 仍为 `d0fce21b22b8ad3002027931507cd4f04d36626f`；本次入场已有 10 个未提交 P3 文件，全部先作原始字节快照。本轮仅修改 adaptive_chunking.py、test_p3_adaptive_chunking.py、本报告，并新增 test_p3_chunking_review_fixes.py。其余 7 个 P3 文件保持入场哈希；没有覆盖已有工作、提交、暂存或修改 P2 冻结资产。实际工作区为 `C:/Users/22088/.codex/worktrees/rag-retrieval-opt-round1/RAG quention`。

执行包 `P3-REVIEW-FIXES / rev1 / attempt1+one-test-setup-repair`；父协调者单写者，控制面仅父任务运行，没有派发 Worker。请求 GPT-6.1 Sol high；可观察的实际模型/effort、token、费用均 UNKNOWN；执行方式 MANUALLY_SUPERVISED_TRIAL。

| 修正 | 原始证据、实现依据 | 当前实现与验证 |
|---|---|---|
| 长表短行 | 固定 WeKnora `3e8b0bfc80b845b2d4b2ed683994748741450a97` 的 splitter.go:123 protectedPatterns 分别匹配表头＋分隔行、数据行；:169 protectedSpans 同起点长匹配优先，跳过重叠，不合并相邻行 | adaptive_chunking.py `PROTECTED`/`protected_spans` 同粒度；32/240 行 × auto/heading/heuristic/legacy，共 8 项；断言每行完整、所有字符覆盖、父/子边界不切行、offset/quote/SHA 精确 |
| 切块目标与模型容量 | ports/providers.py:24 EmbeddingResult、:104 EmbeddingProvider 只声明输入文本与输出形状；adapters/models/ollama.py:275 embed 只发送 model/input 并检查 1024 维，没有经过确认的输入上限、tokenizer 或非截断合同 | prepare_document 用 child_size 切 SourceContent，完整 ContextHeader 独立附加；原生表格/caption 继续旧 admission，允许超过 384/512 目标。没有复制上游 splitter.go:315 的 7500 常量，Chat num_ctx=8192 不能作为 Embedding 依据 |
| 原有 admission 保留 | domain/chunking.py:187 table_row_texts 和 TABLE_ROW_TOO_LARGE；adapters/parsers/xlsx.py:36/60 单字段 4096 字符门禁 | 整行 4096 成功、4097 明确失败；两个各 2500 字符单元格的整行拒绝仍成立；900 字符原生值在 general=128/child=64 和 512/384 下保留原 proof。以上均为字符 admission，不是 token 容量 |
| 身份隔离 | 配置 identity 原已消费于生产读写和 Embedding fingerprint | 新增保护粒度与输入语义版本键，当前 identity `p3:391208cb6fb98bbcd68552703cfa80d158973c46902eb9f321a1c1f02d88c041`，不会复用修正前 P3 identity；没有重建实际索引 |

普通子块的原文切片、SHA、locator.start/end/quote、完整内容覆盖和父子包含关系断言继续保留。旧 P3 的 embedding_content<=512 断言改为完整 header＋原文输入的精确等式；过长标题由错误拒绝改为完整保存；旧 900 字符原生行用例由错误拒绝改为逐项 content/SHA/SourceLocator/cells 对照，另补原有行 admission 的正负精确边界。没有修改或移除 P2 数值、表格、引用用例，没有绕开 proof/provenance 门。

## 实际命令、结果与失败证据

所有命令均为 `.venv/Scripts/python.exe -B -m pytest -q --tb=short -p no:cacheprovider` 加明确模块、独立 basetemp 和 JUnit；完整实际 argv、环境和退出码在本节末及同名 command/exit JSON。未实现的命令不得报告通过。

| 轮次 | PASS / FAIL / SKIP / ERROR | 退出码 | 证据位于 var/reports/p3/review-fixes/ |
|---|---|---:|---|
| 修复前反例 | 0 / 12 / 0 / 0 | 1 | counterexamples-red.xml / .log：8 行切断、2 原生证据错误拒绝、1 标题错误拒绝、1 样本先触发 XLSX_TEXT_LIMIT |
| 首次修正：反例＋P3＋XLSX admission | 69 / 0 / 0 / 0 | 0 | counterexamples-green.xml；这是增加精确边界用例前的中间源码，不能冒充最终证据 |
| 增加边界用例后的首次定向 | 385 / 2 / 0 / 0 | 1 | boundary-first-final-targeted.xml：新测试选中表头行，保留严格断言并修正为第 2 数据行 |
| 同轮广泛 | 1095 / 11 / 127 / 20 | 1 | boundary-first-final-regression.xml：既有 29 个 FAIL/ERROR ＋ 上述 2 个测试自身错误 |
| **最终定向** | **387 / 0 / 0 / 0** | **0** | **final-targeted.xml** |
| **最终广泛** | **1097 / 9 / 127 / 20** | **1** | **final-regression.xml** |

最终定向复用此前 353 项并加入 14 项新修正用例和 20 项已有 XLSX resource-limit 检查；最终广泛复用此前范围并加入 14 项。本轮新增 14 项全部 PASS。相对干净 d0 基线 1046 PASS/9 FAIL/127 SKIP/20 ERROR，当前 51 项新增均 PASS；29 项既有 FAIL/ERROR 的状态、message、traceback 在只忽略 Python 源码行号后相同。127 SKIP 仍是 SKIP；不宣称广泛测试全绿。与此前 final2 的 1083 PASS 相比增加 14 PASS，旧 P3 原生行测试改名和行为更正已逐项列在新 failure-baseline-comparison.json。

既有 29 项原因保持：1 项 layering adapters→application 既有导入；1 项 legacy DOC 缺 table.docx；1 项多模态旧 OCR_EMPTY 断言与 P2 OCR_UNAVAILABLE 行为不符；6 项 DOCX 声明 FAIL 和 20 项 fixture setup ERROR 均为 NATIVE_RUNTIME_UNAVAILABLE。原失败全文及逐项对照均保留。

最终源码快照 `review-fixes/tested-source-snapshot.json` 包含 261 个文件，并在两组最终测试完成后再核验；中间轮次快照与失败源码另存。测试时源码匹配交付源码；报告自身哈希见最终 manifest，不制造自引用哈希。冻结 PDF 目录的五份资产均与前轮字节相同，gold.json 仍为 18157 字节、d44ff365b77800fab83b4604b26a53f9152fa8d810851e52f8cf93aae8836f79。

## 风险、待决项与停止边界

**Embedding 输入上限 UNKNOWN，尚未完成模型容量门禁。** 现有 512/384/4096 是切块/原生行 admission 参数，不能推导 token 上限。实际模型 tag/digest、tokenizer、输入容量、超限拒绝与无静默截断策略需在获得真实模型验证授权后确认，再单独冻结 provider admission 合同及正负边界测试。当前离线用例仅证明文本、证据与引用完整，不证明真实 provider 能接收任意长输入；原 adapter 未显式设置 truncate=false，真实非截断行为未验证。这是上线前阻断/待决项，不在本轮虚构数值或扩大 provider 实现范围。

真实 DB 迁移/FK/pgvector、真实模型/OCR/VLM 验证仍为上线前阻断，均 NOT RUN；没有调用真实 DB/API/模型，没有启动服务、部署或重建业务索引。**依赖升级影响未独立验证**，限制不变。一般大代码/公式的既有递归行为不在本次范围，原生原子表格/caption 继续旧门禁，不截断原子 proof 换取通过。

Git 默认 diff --check=0，cached diff --check=0，暂存为空；新文件的 no-index --check 退出 1 来自内容差异且无空白诊断，不能把该 1 当作测试 PASS。完整 Git stdout/exit 与所有文件 SHA 见 review-fixes/git-checks.json、final-manifest.json。只读核验覆盖旧 486 文件基线和前轮所有证据哈希；其他已核对 P3 接线保持原字节。

## 一次性交付证据及对应版本

`var/reports/p3-review-fixes-evidence.zip` 包含原 baseline.xml、failure-baseline-comparison.json、final2-tested-source-snapshot.json、final-manifest.json、git-checks.json 的原路径与原始字节；新版位于 `var/reports/p3/review-fixes/`，对应 failure-baseline-comparison.json、tested-source-snapshot.json、final-manifest.json、git-checks.json。同时含最终两份 JUnit/命令/日志、所有旧轮次/失败记录、原报告快照、修正前后源码、correction.diff 和相对 HEAD 的 p3-full-current.diff；bundle-manifest.json 对每一文件列长度和 SHA-256。

本次只交付待复核结果，不 commit、不进入 P4。是否批准独立提交由协调者/Owner 决定。

### 最终实际 argv

```json
{
  "command": [
    "C:\\Users\\22088\\.codex\\worktrees\\rag-retrieval-opt-round1\\RAG quention\\.venv\\Scripts\\python.exe",
    "-B",
    "-m",
    "pytest",
    "-q",
    "--tb=short",
    "-p",
    "no:cacheprovider",
    "backend/tests/test_p3_adaptive_chunking.py",
    "backend/tests/test_chunking.py",
    "backend/tests/test_chunk_strategy.py",
    "backend/tests/test_version_source_contract.py",
    "backend/tests/test_version_activation.py",
    "backend/tests/test_final_answer_commit.py",
    "backend/tests/test_structured_evidence.py",
    "backend/tests/test_citation_resolution.py",
    "backend/tests/test_pdf_table_evidence.py",
    "backend/tests/test_p2_pdf_offline_closure.py",
    "backend/tests/test_p2_document_processing.py",
    "backend/tests/test_caption_fact_guard.py",
    "backend/tests/test_local_caption_enricher.py",
    "backend/tests/test_context_neighbor_repository.py",
    "backend/tests/test_retrieval_routing.py",
    "backend/tests/test_p3_chunking_review_fixes.py",
    "backend/tests/test_xlsx_resource_limits.py",
    "--basetemp=var/p3-review-final2-targeted",
    "--junitxml=var/reports/p3/review-fixes/final-targeted.xml"
  ],
  "environment": {
    "TEMP": "C:\\Users\\22088\\.codex\\worktrees\\rag-retrieval-opt-round1\\RAG quention\\var\\p3-temp",
    "TMP": "C:\\Users\\22088\\.codex\\worktrees\\rag-retrieval-opt-round1\\RAG quention\\var\\p3-temp",
    "RAG_CLOUD_ENABLED": "false",
    "RAG_LANGFUSE_ENABLED": "false",
    "RAG_STORAGE_ROOT": "var/p3-test-storage",
    "RAG_NATIVE_TEST_PYTHON": "",
    "RAG_NATIVE_TEST_FIXTURES": "",
    "RAG_PDF_TEST_FIXTURES": ""
  },
  "source_snapshot": "tested-source-snapshot.json"
}
```

```json
{
  "command": [
    "C:\\Users\\22088\\.codex\\worktrees\\rag-retrieval-opt-round1\\RAG quention\\.venv\\Scripts\\python.exe",
    "-B",
    "-m",
    "pytest",
    "-q",
    "--tb=short",
    "-p",
    "no:cacheprovider",
    "backend/tests/test_agent_limits.py",
    "backend/tests/test_agent_tool_allowlist.py",
    "backend/tests/test_agent_trace_sse.py",
    "backend/tests/test_answer_hardening.py",
    "backend/tests/test_answer_service.py",
    "backend/tests/test_answer_validation.py",
    "backend/tests/test_backup_manifest.py",
    "backend/tests/test_bootstrap.py",
    "backend/tests/test_bounded_history.py",
    "backend/tests/test_budget_gate.py",
    "backend/tests/test_cancel_route.py",
    "backend/tests/test_cancellation_boundaries.py",
    "backend/tests/test_caption_fact_guard.py",
    "backend/tests/test_chunk_strategy.py",
    "backend/tests/test_chunking.py",
    "backend/tests/test_citation_group_span.py",
    "backend/tests/test_citation_resolution.py",
    "backend/tests/test_clarification_persistence.py",
    "backend/tests/test_config.py",
    "backend/tests/test_container_injection.py",
    "backend/tests/test_context_and_locators.py",
    "backend/tests/test_context_expansion.py",
    "backend/tests/test_context_neighbor_repository.py",
    "backend/tests/test_context_pool_policy.py",
    "backend/tests/test_conversation_scope.py",
    "backend/tests/test_egress_matrix.py",
    "backend/tests/test_embedding_profile_isolation.py",
    "backend/tests/test_evidence.py",
    "backend/tests/test_evidence_accumulator.py",
    "backend/tests/test_follow_up.py",
    "backend/tests/test_fusion.py",
    "backend/tests/test_graph_evidence.py",
    "backend/tests/test_graph_failure_isolation.py",
    "backend/tests/test_graph_scope_sql.py",
    "backend/tests/test_health.py",
    "backend/tests/test_hybrid_retrieval.py",
    "backend/tests/test_ingestion_claiming.py",
    "backend/tests/test_ingestion_lease_config.py",
    "backend/tests/test_ingestion_retry_route.py",
    "backend/tests/test_ingestion_state_machine.py",
    "backend/tests/test_inline_citation_group.py",
    "backend/tests/test_knowledge_scope_boundaries.py",
    "backend/tests/test_knowledge_tools.py",
    "backend/tests/test_langchain_agent.py",
    "backend/tests/test_langchain_quick_chain.py",
    "backend/tests/test_layering_preview.py",
    "backend/tests/test_legacy_doc.py",
    "backend/tests/test_llm_rerank.py",
    "backend/tests/test_local_caption_enricher.py",
    "backend/tests/test_model_policy.py",
    "backend/tests/test_multimodal_ingestion.py",
    "backend/tests/test_native_release.py",
    "backend/tests/test_native_release_review_regressions.py",
    "backend/tests/test_native_table_evidence.py",
    "backend/tests/test_original_header_boundary.py",
    "backend/tests/test_parsers.py",
    "backend/tests/test_pdf_ocr.py",
    "backend/tests/test_pdf_table_evidence.py",
    "backend/tests/test_preview_contract.py",
    "backend/tests/test_privacy_configuration_answer.py",
    "backend/tests/test_product_gap_contract.py",
    "backend/tests/test_quality_eval_schema.py",
    "backend/tests/test_quality_gate.py",
    "backend/tests/test_query_coverage.py",
    "backend/tests/test_question_checklist.py",
    "backend/tests/test_question_checklist_prompt_replay.py",
    "backend/tests/test_quick_answer_language_prompt.py",
    "backend/tests/test_quick_chain_budget.py",
    "backend/tests/test_quick_chain_quality.py",
    "backend/tests/test_quick_citation_prompt.py",
    "backend/tests/test_quick_evidence_flow.py",
    "backend/tests/test_reference_profile.py",
    "backend/tests/test_release_preflight.py",
    "backend/tests/test_restore_invariants.py",
    "backend/tests/test_retrieval_contract.py",
    "backend/tests/test_retrieval_execution_record.py",
    "backend/tests/test_retrieval_provenance.py",
    "backend/tests/test_retrieval_routing.py",
    "backend/tests/test_run_metrics.py",
    "backend/tests/test_schema_check.py",
    "backend/tests/test_scope.py",
    "backend/tests/test_scope_and_graph_routes.py",
    "backend/tests/test_smart_citation_replay.py",
    "backend/tests/test_smart_table_header_hint.py",
    "backend/tests/test_storage.py",
    "backend/tests/test_structured_answer_spacing.py",
    "backend/tests/test_structured_evidence.py",
    "backend/tests/test_table_header_hint.py",
    "backend/tests/test_targeted_merge_bound.py",
    "backend/tests/test_text_normalization.py",
    "backend/tests/test_version_activation.py",
    "backend/tests/test_xlsx_resource_limits.py",
    "backend/tests/test_xlsx_storage_paths.py",
    "backend/tests/test_xlsx_table_evidence.py",
    "backend/tests/test_version_source_contract.py",
    "backend/tests/test_final_answer_commit.py",
    "backend/tests/test_p2_document_processing.py",
    "backend/tests/test_p2_pdf_offline_closure.py",
    "backend/tests/test_p3_adaptive_chunking.py",
    "backend/tests/test_p3_chunking_review_fixes.py",
    "--basetemp=var/p3-review-final2-regression",
    "--junitxml=var/reports/p3/review-fixes/final-regression.xml"
  ],
  "environment": {
    "TEMP": "C:\\Users\\22088\\.codex\\worktrees\\rag-retrieval-opt-round1\\RAG quention\\var\\p3-temp",
    "TMP": "C:\\Users\\22088\\.codex\\worktrees\\rag-retrieval-opt-round1\\RAG quention\\var\\p3-temp",
    "RAG_CLOUD_ENABLED": "false",
    "RAG_LANGFUSE_ENABLED": "false",
    "RAG_STORAGE_ROOT": "var/p3-test-storage",
    "RAG_NATIVE_TEST_PYTHON": "",
    "RAG_NATIVE_TEST_FIXTURES": "",
    "RAG_PDF_TEST_FIXTURES": ""
  },
  "source_snapshot": "tested-source-snapshot.json"
}
```

---

# 以下为修正前原报告全文（历史证据，不是当前结论）

# P3 — Adaptive Chunking / ContextHeader / Parent-Child 离线实施报告

本次范围内执行结果：**P3_PASS（代码与离线检查） / READY_FOR_EXTERNAL_VALIDATION**。
这是执行者的 CHECKS_PASSED / NEEDS_OWNER_REVIEW 建议，不是协调者验收、上线批准或真实服务验证通过。
广泛离线回归仍为退出码 1；29 项既有 FAIL/ERROR 与本轮修改前基线逐项同因，没有新增失败或旧用例结果变化。SKIP 不计 PASS。

## 1. 目标与改动

### 授权、基线与证据身份

- 入场 HEAD：`d0fce21b22b8ad3002027931507cd4f04d36626f`，分支 `codex/local-first-rag-v1-20260930`，入场工作树干净。结束 HEAD 不变，未 stage、commit、amend、push 或 tag，未进入 P4。
- 实际工作区：`C:/Users/22088/.codex/worktrees/rag-retrieval-opt-round1/RAG quention`。
- 读取项目 AGENTS.md、progress.md、ADR 索引及 ADR-004。progress.md 开头仍包含旧 main/M4.5 信息；本轮阶段以用户明确指定的 P3 和实际 Git 为准，没有改写旧进度或旧报告。
- 任务原件 `04_P3_CHUNKING.md` SHA-256：`238da55825efa0f1267746e852cae4a29257af08f742fb4b2eb3aec75252facf`。
- Temp 中的全局合同文件不存在；从用户原任务包 `C:/Users/22088/Downloads/rag_weknora_dynamic_execution_plan_20261007.zip` 读取原件。ZIP 内 P3 与当前附件字节一致。`01_GLOBAL_EXECUTION_CONTRACT.md` SHA-256：`9c25c2c282ca3c1527a82435c6285aa673bab9b9c90dc2ec37e924d59d427cae`。两份只读副本保存在 `var/reports/p3/`。
- 唯一上游基线：Tencent/WeKnora v0.8.2，`3e8b0bfc80b845b2d4b2ed683994748741450a97`。没有改用其他版本，没有对本地 WeKnora 部署作版本等价推断。
- 执行包：`P3-CHUNKING / rev1 / implementation+offline`，父协调者单写者。没有派发 Worker；控制面查询仅见父任务运行。请求路由继承用户的 GPT-6.1 Sol high；本次工具没有提供 app-server 或上游实际模型/effort 证据，实际身份、token 和成本均 **UNKNOWN**。执行方式为 **MANUALLY_SUPERVISED_TRIAL**，不宣称全自动控制已被强制实施。
- 允许路径是下表 10 个项目文件；其他写入仅为忽略目录 `var/reports/p3/` 和离线 pytest 临时文件。基线哈希见 `baseline-manifest.json`，测试时源码哈希见 `final2-tested-source-snapshot.json`，最终检查见 `final-manifest.json`。

### 最小实现范围

| 文件 | 实际变更与理由 | 实现/验证状态 |
|---|---|---|
| `backend/app/domain/adaptive_chunking.py` | 唯一新准备入口、profile/tier/validator/fallback、protected spans、breadcrumb、parent-child、配置身份 | 生产接通；离线执行 |
| `backend/app/domain/models.py:162` `ChunkDraft` | 增加独立 context_header、逻辑 parent_index、embedding_content；原 content/locator 保持源语义 | 离线执行 |
| `backend/app/adapters/postgres/knowledge_repository.py:378` `process_job` | 共用准备入口、保存 parent/header/identity，只为 child 生成 embedding 与 terms；拒绝原地重写 ready version | 真实生产方法 + SIMULATED SQL/Embedding 执行；真实 DB 未运行 |
| 同文件 `keyword_candidates` / `vector_candidates` / `list_active_chunks` | 对 child、新配置 identity 和精确 profile fingerprint 过滤，保留服务端 scope/active-version 条件 | SQL 条件与替身测试；真实 pgvector 未运行 |
| 同文件 `list_chunks` / `get_document_content` / `get_chunk` | 文档列表/内容排除重复 parent；按 ID 历史 readback 保留，无新 identity 限制 | 离线检查；没有新增 Parent 自动回捞 |
| `backend/app/adapters/postgres/graph_repository.py:15` | 现有图谱构建源读取仅接收 child，避免存储 parent 经旧消费者间接成为图谱召回资料；不改图谱算法 | SQL 读取口离线检查；真实 DB 未运行 |
| `backend/app/application/ingestion.py` | 内存 ingestion 共用准备函数；分别保存 children、parents、identity | 真实解析器 + 内存执行 |
| `backend/app/config.py:61` | general 默认从 1200/120 收敛到固定 512/80；已有显式环境配置仍可覆盖并进入 identity | 默认值测试 |
| `alembic/versions/0016_parent_child_chunks.py` | 增量列、同 version parent FK、角色约束、parent 索引；不改旧 migration | PostgreSQL 方言离线 DDL 编译；未应用 |
| `backend/tests/test_p3_adaptive_chunking.py` | 新增 37 项，包括真实冻结 PDF 解码与引用链、生产接线、身份隔离、空白片段修复 | 全部 PASS |
| `backend/tests/test_postgres_chunk_strategy.py` | 仅更新旧 live-DB 测试的 chunker/实际 tier 预期；两标题不足固定 auto 阈值，实际为 legacy | AST 对照确认其余逻辑/断言不变；此真实 DB 用例 NOT RUN |
| `docs/audits/p3-weknora-chunking-report.md` | 本报告 | 待协调者复核 |

表中同一 repository 文件合计只算一个文件，总计 10 个。未修改 P2 PDF 样本、生成器、旧 PDF 测试、旧 P2 报告、依赖或锁文件。旧 `domain/chunking.py` API 保留，既有直接调用方继续使用其原语义；新 ingestion 使用新入口。旧表格与 caption admission 门作为源码依赖复用，未删除或绕开其数值/定位/provenance 保护。

### 固定上游核对

源码均从固定 commit 获取，原件及 SHA-256 列表见 `var/reports/p3/upstream-source-manifest.json`。以下是实现证据，不是 README 推断。

| 能力 | 固定上游源码/符号 | 本地对应 |
|---|---|---|
| 512/80、EmbeddingContent、protected patterns、semantic overlap | [splitter.go](https://github.com/Tencent/WeKnora/blob/3e8b0bfc80b845b2d4b2ed683994748741450a97/internal/infrastructure/chunker/splitter.go) `EmbeddingContent`（43）、默认值（103） | `ChunkDraft.embedding_content`，`_recursive`、`protected_spans` |
| 自动选 tier 与阈值 | [profiler.go](https://github.com/Tencent/WeKnora/blob/3e8b0bfc80b845b2d4b2ed683994748741450a97/internal/infrastructure/chunker/profiler.go) `DominantHeadingLevel`、`SelectStrategy`（220） | `profile_text`、`select_tiers` |
| 每 tier 验证、最终 legacy 质量失败仍有输出 | [strategy.go](https://github.com/Tencent/WeKnora/blob/3e8b0bfc80b845b2d4b2ed683994748741450a97/internal/infrastructure/chunker/strategy.go) `SplitWithDiagnostics`、`splitParentChild` | `split_text`、`validate_ranges`、`prepare_document` |
| parent 4096 / child 384 / child overlap 384/5=76 | 同 `strategy.go:219` `DeriveParentChildConfigs` | `ChunkingConfig`、child_size // 5；不是 80，也没有参数搜索 |
| 等价唯一 child 时省略 parent；合并 breadcrumb | 同 `strategy.go` `splitParentChild`、`mergeBreadcrumbs` | parent_index 省略规则；全局标题栈提供父子共同上下文并去除重复层级 |
| 标题层次、相邻短节只在共享标题下合并 | [heading_splitter.go](https://github.com/Tencent/WeKnora/blob/3e8b0bfc80b845b2d4b2ed683994748741450a97/internal/infrastructure/chunker/heading_splitter.go) `coalesceTinyChunks`（137） | heading tier、共同 breadcrumb；保留源标题行 |
| OCR/章节/编号/换页、贪心打包、对齐 overlap、跳过纯空白块 | [heuristic_splitter.go](https://github.com/Tencent/WeKnora/blob/3e8b0bfc80b845b2d4b2ed683994748741450a97/internal/infrastructure/chunker/heuristic_splitter.go) `splitByHeuristicsImpl`、`applyOverlapAligned`、`appendChunk` | `_bin_pack`、过滤纯空白 children |
| 中文章节和中英文句末标点 | [patterns.go](https://github.com/Tencent/WeKnora/blob/3e8b0bfc80b845b2d4b2ed683994748741450a97/internal/infrastructure/chunker/patterns.go) `ChineseChapterPattern`、`NumberedSectionPattern`、`SentenceSeparators` | 同源正则/边界语义；无新增中文分词器 |
| tiny/single/underfilled/oversized 的质量判定 | [validator.go](https://github.com/Tencent/WeKnora/blob/3e8b0bfc80b845b2d4b2ed683994748741450a97/internal/infrastructure/chunker/validator.go) `ValidateChunks` | `validate_ranges`；另外强制精确源范围、覆盖及本地大小护栏 |
| 真实生产 parent 持久化、child-only index、ContextHeader 输入 | [knowledge_process.go](https://github.com/Tencent/WeKnora/blob/3e8b0bfc80b845b2d4b2ed683994748741450a97/internal/application/service/knowledge_process.go) parent 构造（511）、child 引用（573）、排除 parent 的索引循环（655）、EmbeddingContent（660） | 本地 `process_job`；上游删除旧 knowledge 索引的行为未迁入本项目版本链 |
| preview 与 ingestion 同一配置派生 | [chunker_debug.go](https://github.com/Tencent/WeKnora/blob/3e8b0bfc80b845b2d4b2ed683994748741450a97/internal/handler/chunker_debug.go) `DeriveParentChildConfigs`（174） | 本项目不存在 chunk preview HTTP 功能；新增离线 helper 仅委托同一 prepare_document，未把文档/PDF preview 冒称 chunk preview |

本轮也读取 header_tracker.go、heading_hierarchy.go、types、process_config、index_content 与 tokens.go，用于核对消费与模型限制。没有审计 WeKnora 全仓或本地部署。固定源码的生产接线在 knowledge_process.go，而不能只凭较早报告中的 knowledge.go 文件名判断。

### 参数、tier 与 validator 的具体合同

默认 general=512 字符/overlap=80；parent=4096/overlap=80；child 目标=384/overlap=76。Python 字符 offset 对应 Unicode code point，不改 P2 NormalizedDocument 的文本以重新制造 offset。父块不送入 Embedding。

`auto` 先取结构 profile：Markdown 标题至少 3 个、标题行占比 >0.005、存在 dominant level 时尝试 heading；编号/章节/大写短行/分隔线/换页总信号至少 5，或存在换页，或任意中英德章节信号时尝试 heuristic；最后 legacy。dominant 优先最浅的至少出现 3 次的层，否则最深的存在层。未闭合代码 fence 内的假标题也排除。这里保存的是参与实际路由的 profile 子集，未复制上游未消费的统计展示字段。

显式 heading/heuristic 各带 legacy 后备；recursive 是 legacy 别名。上游空 Strategy 为 legacy，本任务明确要求自动策略，所以本地默认显式 auto，不以空字符串替代。版本的 chunk_strategy 保存实际选择的 tier 集；配置 auto 和全部父/子诊断保存在 manifest，不能把配置名称当实际执行结果。

每个 tier 均验证：非空、合法精确源范围、顺序覆盖、长度上限，以及上游单块/过多 tiny（尾块之外 <50 字符，数量 >总数/4 且 >2）/整体过短等质量信号。失败记录 tier/reason 并尝试后备。最终 legacy 的质量信号可作为诊断保留，但不能豁免空结果、无效 source coverage 或硬大小护栏。整文纯空白直接 EMPTY_TEXT；生成过程的纯空白 children 不进入模型，统计 skipped_whitespace，所有正文引用仍用原 offset。

Markdown table、fenced/inline code、块公式、有限长度 link/image token 尽量在边缘切；超过子块预算的文本保护区有递归/硬切兜底，仍保持精确 SourceContent。heading 短块合并要求共享标题，最终 ContextHeader 取公共 breadcrumb，不能把一个兄弟标题冠到另一个兄弟内容上。

**模型长度边界的适配与限制：** 固定上游 TokenLimit 来自模型元数据，tokens.go 的语言比例也是估算。本项目当前 Ollama adapter 不提供经确认的输入 token 上限，本轮未查询真实模型。本地使用已有 general_size=512 作为含 ContextHeader 的保守字符输入护栏；普通 child 在必要时预留标题空间，未截断标题或污染 quote。512 字符不是“供应商实际 token 上限已验证”的声明，也不是新一轮成本调参。真实 tokenizer/模型容量、无静默截断验证仍为上线前阻断项。

P2 原生表格行/caption 继续通过旧 admission/provenance 门，作为原子 child 保持整行 cells/bbox/quote，不硬切成失去证据意义的半行；它们可以超过 384 目标，但不得超过含标题的 512 本地护栏。超大原子证据明确 `ATOMIC_EVIDENCE_EMBEDDING_LIMIT`，过长 breadcrumb 明确 `CONTEXT_HEADER_EMBEDDING_LIMIT`；不以删除 cells、删 quote 或静默截断换取成功。这是需要协调者关注的兼容性边界：超长单元格/描述可能比旧 1200 字符配置更早失败，真实资料分布未验证，不能声称所有旧资料都可无失败重建。

### Source / Context / Parent 的持久化与 API 消费

`content`、content_sha256、start/end 和 locator.quote 一直来自 NormalizedDocument 同一精确切片。`context_header` 独立存于 chunks；EmbeddingContent 为 header + 两换行 + content.strip()。trim 只影响模型输入，不影响 quote、原文哈希、offset 或数值证明。

`PreparedChunks.parents` 与 `children` 是定义者。child.parent_index 是准备阶段逻辑引用；repository 分配 parent UUID 并写 child.parent_id。父、子都保存同 version、source span、locator 和独立 header。父块先写入；child 的 chunk_index 保持 0..N-1，parent 的存储 index 使用 N..N+P-1，避免破坏现有 child 相邻序列。复合 FK `(version_id,parent_id) -> chunks(version_id,id)` 防止跨版本引用；parent_role 约束使 parent 不再引用另一个 parent。真实 DB 约束与并发效果未验证。

只有 child 调用 Embedding，并写 chunk_terms/chunk_embeddings；parent 只保存。来源 SHA、最小 DocumentVersion、独立 table proofs、原始资料存储、历史 evidence/quote readback 保留。`get_chunk(id)` 不加新 pipeline/active-version 条件，因此历史引用没有因新 identity 被主动屏蔽；没有缩短版本链或重写旧 EvidenceSnapshot。

文档 chunks 列表/拼接内容仅排除 parent，不按新 identity 隐藏历史 child；避免 Parent 重复显示或重复拼接。已有内容接口仍采用旧的 child 拼接语义，重叠正文的既有展示问题不在本轮重构。RAG 的关键词、向量及 active-chunk 候选则同时过滤 child 与当前配置 identity。现有 GraphService.build 通过 graph_repository.list_version_chunks 读取源块，本轮只为该 SQL 加 child 过滤，避免间接将 Parent 作为图谱证据源。API DTO、前端、Hybrid/RRF、rerank、路由、图谱算法、neighbor 自动扩展、Parent 回捞均未改动。

### 新旧索引身份与 reindex 策略

新的 chunker=`weknora-adaptive-parent-child/v1`，schema=`chunk-source-context/v2`。`ChunkingConfig.identity` 是包含固定上游 SHA、全部有效尺寸/overlap/strategy、header 与原子证据语义版本的 SHA-256，前缀 p3:。Embedding fingerprint 再包含 provider、model、dimension 和该 identity；profile 的 model_revision 带 pipeline identity，避免撞旧的 provider/model/local/dimension 唯一约束。

写入和 get_embedding_profile_id 均按精确 fingerprint；vector_candidates 额外核对传入 profile 的 fingerprint，不能只接收任意旧 profile_id。keyword/vector/list_active 同时核对 chunk 与 version 的 identity；旧行迁移默认 legacy/v1，不批量伪装成 P3。未删除旧 profiles、vectors、chunks 或 source。ready version 的重复 job 只记 job 失败 `REINDEX_REQUIRES_NEW_VERSION`，不改该 version 的 ready 状态或历史 chunks。

**部署/reindex 前提：** 当前新检索入口会排除旧 identity 的活跃索引，所以不能直接部署后宣称旧资料已迁移。后续必须单独批准：备份与核对旧版本/旧 profile → 在隔离 DB 验证 0016 → 用不可变原始资料创建新的候选 DocumentVersion → 使用同一已冻结配置建立新 children/profile → 检查数值、引用、范围和检索后再按原版本激活规则切换。失败保留旧 active version；已保存的历史引用仍指向原 IDs。本轮没有执行迁移、业务 reindex、活跃版本切换脚本或自动回滚，没有提供可误触业务索引的批处理命令。

## 2. 执行命令与实际结果

**未实现的命令不得报告通过。** 未启动服务，未执行 make verify、真实 DB/OCR/VLM/API 或任何外部模型命令。

所有测试实际使用工作区 `.venv/Scripts/python.exe -B -m pytest -q --tb=short -p no:cacheprovider`，明确列举离线模块、指定独立 basetemp 和 JUnit。精确 argv、有效隔离变量在每轮 `*-command.json`；完整输出和真实退出码各在同名 `.log`、`*-exit.json`。下方追加完整最终命令，而非以“理论通过”替代执行。

| 执行 | 实际结果 | 退出码 | 证据 |
|---|---|---|---|
| P3 修改前广泛离线基线 | 1046 PASS / 127 SKIP / 9 FAIL / 20 ERROR | 1 | `var/reports/p3/baseline.xml`、baseline.log |
| 首轮 P3 新用例 | 29 PASS | 0 | targeted-1.xml、targeted-1.log |
| 首轮广泛回归 | 1075 PASS / 127 SKIP / 9 FAIL / 20 ERROR | 1 | regression-1.xml、regression-1.log |
| 后续切分/接线边界核对 | 348 PASS，随后 351 PASS；两轮均 0 FAIL/0 SKIP | 0 | targeted-final.xml、verification-targeted.xml（这些名称是当时轮次，不冒称最终源码证据） |
| 修复纯空白片段前广泛回归 | 1081 PASS / 127 SKIP / 9 FAIL / 20 ERROR | 1 | verification-regression.xml |
| 空白前缀的最小复现 | 3 children，发现 2 个空 Embedding 输入 | 1 | whitespace-reproduction.json；失败证据保留 |
| 纯空白修复后、图谱读取口核对前 | 352 PASS；广泛 1082 PASS / 127 SKIP / 9 FAIL / 20 ERROR | 0 / 1 | final-targeted.xml、final-regression.xml（保留此前轮次） |
| **最终定向离线回归** | **353 PASS / 0 SKIP / 0 FAIL / 0 ERROR** | **0** | **final2-targeted.xml / .log / -command.json / -exit.json** |
| **最终广泛离线回归** | **1083 PASS / 127 SKIP / 9 FAIL / 20 ERROR** | **1** | **final2-regression.xml / .log / -command.json / -exit.json** |
| JUnit 节点/原因对照、AST 与源码哈希检查（内联 Python） | 29 项旧失败/错误同因；旧用例结果变化 0；新增 37 项全部 PASS；260 个被测源码/测试/迁移文件哈希一致 | 0 | failure-baseline-comparison.json、ast-and-scope-check.json |

最终定向包含 P3、旧 chunking、version source/activation、FinalAnswerCommitCheck、structured evidence、citation、PDF 原 10 项与 P2 closure 6 项、P2 processing、caption、context-neighbor repository 与 retrieval-routing 等相关离线模块。未添加真实 PostgreSQL 测试到离线命令。旧 live-DB 元数据测试仅更新合理的新协议预期，保留待外部验证。

最终 JUnit 与最终源码通过 `final2-tested-source-snapshot.json` 绑定；结束再次比对这些文件，报告整理没有改变被测实现。纯空白修复按固定上游 appendChunk 的拒绝纯空白语义进行，保留该次失败和修复前全部 XML；未通过删断言、增加 skip 或改 frozen PDF 掩盖问题。

## 3. 结果与基线失败因果对照

结果解释：本阶段代码/离线合同可供下一步审查，广泛套件不是全绿。最终 1202 项旧用例的 PASS/FAIL/ERROR/SKIP 状态与入场基线一致，新增 37 项全通过；29 项旧失败/错误的 message 与 traceback 在仅正规化 Python 源码行号后相同。完整原始前后文本保存在 failure-baseline-comparison.json，不把原因不同但数量相同当作等价。

| 旧问题组 | 数量 | 原因与源码边界 | 本轮处理 |
|---|---:|---|---|
| `test_layering_preview::test_declared_layering_has_no_violations` | 1 FAIL | 修改前已有 3 条 adapters→application 依赖违规。repository 新 import 使原有 import 行号移动，但违规对象/类型未增加 | 保留失败；不扩张做跨层重构 |
| `test_legacy_doc::test_simulated_doc_conversion_reuses_real_table_citation_chain` | 1 FAIL | 显式 native fixture 未配置，直接读取 `table.docx` 不存在 | 不替造真实 fixture，不降级断言 |
| `test_multimodal_ingestion::test_pdf_and_image_keep_source_locators_and_image_failure_is_recoverable` | 1 FAIL | 当前离线环境返回 OCR_UNAVAILABLE，旧测试仍期待 OCR_EMPTY；P2 已有此差异 | 保留旧测试和既有失败，没有恢复废弃空白页行为 |
| product_gap 的 6 个 ambiguous DOCX 参数 | 6 FAIL | no-declaration / false-declaration / non-leading / multiple-header / duplicate-label / merged-header 均在 NATIVE_RUNTIME_UNAVAILABLE 失败 | 未运行到下游数值/引用断言，不能说这些合同已验证 |
| product_gap 的 declared 正例、provenance/unit 负例及错误答案参数 | 20 ERROR | 同一 declared fixture setup 报 NATIVE_RUNTIME_UNAVAILABLE；具体节点见下方逐项表及 JSON | 不算 PASS，不以新增 P3 用例替代全部 native-runtime 验证 |

127 项 SKIP 的节点与状态保持不变，主要是未配置的显式 native runtime/fixture、OCR 语言数据及显式历史 renderer。没有将 skip 转为 pass；本轮冻结 PDF 离线合同本身已真实执行，不能把广泛套件的其他 skip 说成 PDF 16 项又被跳过。仓库真实 migration 应用版本为 **UNKNOWN**，源码 migration head 为 0016_parent_child_chunks，二者未推定一致。

本轮读取和调用的现有 P1/P2 数值、引用、caption 与 FinalAnswerCommitCheck 断言未改动。唯一旧测试变更是 live-DB 元数据预期，AST 对照在恢复两项旧字符串后完全一致，原断言数量不变。该证据支持“未为 P3 弱化旧数值/引用保护”，不支持“所有真实格式与模型已验证”。

## 4. 风险、未知项与上线前阻断项

| 项目 | 当前证据 | 最小后续验证 / 影响 |
|---|---|---|
| 真实 DB 与 migration 0016 | SIMULATED SQL 路径、PostgreSQL 方言 DDL 编译；应用版本 UNKNOWN | 隔离真实 DB 应用增量迁移；验证旧 history、FK/角色、并发 profile 创建、事务激活和 child-only pgvector/关键词查询。上线前阻断 |
| OCR / VLM | 本轮不调用；真实 PDF 解码、无 OCR fixture、既有明确替身验证分别记录 | 经 Owner 授权的真实环境验证；不能以 mock 代替。上线前阻断 |
| Embedding 真模型 | SIMULATED 1024 向量，输入、数量和 parent 排除可验证；容量/截断/模型 digest UNKNOWN | 确认实际输入窗口和 tokenizer/服务截断行为，检验长标题/原子行策略与固定配置。上线前阻断 |
| 新旧索引切换 | identity/fingerprint 条件已接通；不改旧索引 | 部署前须批准新版本 reindex 与检索验证；当前新入口不会使用 legacy/v1 活跃索引。上线前阻断 |
| 超长原子表格行/caption | 明确失败且保留全部证明，离线 real-XLSX 负例验证；未切坏 cell proof | 真资料分布/最大输入合同未验证；可能比旧 1200 配置更早拒绝。需协调者确认该边界，不能称全面兼容 |
| 依赖版本 | 本轮未改依赖、锁文件，也未安装/升级 | **依赖升级影响未独立验证**，原限制不变 |
| 既有 29 项 FAIL/ERROR 与 127 SKIP | 入场与最终同因/同状态 | 独立范围修复或补环境，不由本次执行者批准延期，不冒称这些合同通过 |
| 真实性能/检索质量 | 本轮未做业务 corpus 重建、参数搜索或 P4 回捞评测 | 本报告不能证明 parent-child 带来实际质量或成本收益 |

发现/限制记录：寻找丢失的全局附件时，对部分临时目录的只读枚举出现权限拒绝；没有重试受拒目录、换保护设置或通过它们读取内容，转而使用用户原任务 ZIP。固定 SHA 的一次 HTTPS 读取出现 SSL EOF，按同一 URL 重试成功，未禁用 TLS。当前 Python launcher 会打印 `Failed to find real location of D:\Drivers\python\python.exe`，实际 pytest 可运行；PyMuPDF/SWIG 有既有弃用告警。没有修改环境修补这些提示。结束未发现白名单之外的项目字节变化。

## 5. 版本与下一步

HEAD 保持 `d0fce21b22b8ad3002027931507cd4f04d36626f`。工作树有本轮 10 个文件变更，暂存区为空；这是按用户要求保留的待审工作树，不宣称结束工作树干净。

完整 diff（含新增未跟踪文件）为 `var/reports/p3/p3-full.diff`，Git 状态/检查原始输出为 `var/reports/p3/git-checks.json`；工作树原始字节 SHA-256、fixture 长度/哈希、测试/上游证据哈希及白名单为 `var/reports/p3/final-manifest.json`。该 manifest 在本报告完成后生成，避免报告自引用哈希循环。

默认 `git diff --check` 及暂存区检查退出 0；新增未跟踪文件另用 `git diff --no-index --check -- /dev/null <path>` 检查，其退出 1 来自 --no-index 隐含的差异退出语义，stdout 无空白问题。首轮证据收集器误将该退出码断言为 0，收集器本身退出 1；原始失败保留在 collector-first-failure.json，随后仅修正证据收集器的预期，没有改生产代码或重跑测试。LF/CRLF 提示保留，未改 Git 配置或冻结文件。

当前停止在协调者审核点。下一最小动作是复核本报告、完整 diff、JUnit 和兼容性边界；经明确批准后才可形成 P3 独立 commit。本轮不提交，不进入 P4，不自行执行真实 DB/API/部署或 reindex。

以下附录记录最终命令、37 项新增用例清单、旧 29 项失败逐项对照及文件哈希。

## 附录：最终执行证据

### 精确实际 argv

`baseline`，实际退出码 `1`。以下是 subprocess 实际执行的 argv，环境见同名 command.json。

```json
[
  "C:\\Users\\22088\\.codex\\worktrees\\rag-retrieval-opt-round1\\RAG quention\\.venv\\Scripts\\python.exe",
  "-B",
  "-m",
  "pytest",
  "-q",
  "--tb=short",
  "-p",
  "no:cacheprovider",
  "backend/tests/test_agent_limits.py",
  "backend/tests/test_agent_tool_allowlist.py",
  "backend/tests/test_agent_trace_sse.py",
  "backend/tests/test_answer_hardening.py",
  "backend/tests/test_answer_service.py",
  "backend/tests/test_answer_validation.py",
  "backend/tests/test_backup_manifest.py",
  "backend/tests/test_bootstrap.py",
  "backend/tests/test_bounded_history.py",
  "backend/tests/test_budget_gate.py",
  "backend/tests/test_cancel_route.py",
  "backend/tests/test_cancellation_boundaries.py",
  "backend/tests/test_caption_fact_guard.py",
  "backend/tests/test_chunk_strategy.py",
  "backend/tests/test_chunking.py",
  "backend/tests/test_citation_group_span.py",
  "backend/tests/test_citation_resolution.py",
  "backend/tests/test_clarification_persistence.py",
  "backend/tests/test_config.py",
  "backend/tests/test_container_injection.py",
  "backend/tests/test_context_and_locators.py",
  "backend/tests/test_context_expansion.py",
  "backend/tests/test_context_neighbor_repository.py",
  "backend/tests/test_context_pool_policy.py",
  "backend/tests/test_conversation_scope.py",
  "backend/tests/test_egress_matrix.py",
  "backend/tests/test_embedding_profile_isolation.py",
  "backend/tests/test_evidence.py",
  "backend/tests/test_evidence_accumulator.py",
  "backend/tests/test_follow_up.py",
  "backend/tests/test_fusion.py",
  "backend/tests/test_graph_evidence.py",
  "backend/tests/test_graph_failure_isolation.py",
  "backend/tests/test_graph_scope_sql.py",
  "backend/tests/test_health.py",
  "backend/tests/test_hybrid_retrieval.py",
  "backend/tests/test_ingestion_claiming.py",
  "backend/tests/test_ingestion_lease_config.py",
  "backend/tests/test_ingestion_retry_route.py",
  "backend/tests/test_ingestion_state_machine.py",
  "backend/tests/test_inline_citation_group.py",
  "backend/tests/test_knowledge_scope_boundaries.py",
  "backend/tests/test_knowledge_tools.py",
  "backend/tests/test_langchain_agent.py",
  "backend/tests/test_langchain_quick_chain.py",
  "backend/tests/test_layering_preview.py",
  "backend/tests/test_legacy_doc.py",
  "backend/tests/test_llm_rerank.py",
  "backend/tests/test_local_caption_enricher.py",
  "backend/tests/test_model_policy.py",
  "backend/tests/test_multimodal_ingestion.py",
  "backend/tests/test_native_release.py",
  "backend/tests/test_native_release_review_regressions.py",
  "backend/tests/test_native_table_evidence.py",
  "backend/tests/test_original_header_boundary.py",
  "backend/tests/test_parsers.py",
  "backend/tests/test_pdf_ocr.py",
  "backend/tests/test_pdf_table_evidence.py",
  "backend/tests/test_preview_contract.py",
  "backend/tests/test_privacy_configuration_answer.py",
  "backend/tests/test_product_gap_contract.py",
  "backend/tests/test_quality_eval_schema.py",
  "backend/tests/test_quality_gate.py",
  "backend/tests/test_query_coverage.py",
  "backend/tests/test_question_checklist.py",
  "backend/tests/test_question_checklist_prompt_replay.py",
  "backend/tests/test_quick_answer_language_prompt.py",
  "backend/tests/test_quick_chain_budget.py",
  "backend/tests/test_quick_chain_quality.py",
  "backend/tests/test_quick_citation_prompt.py",
  "backend/tests/test_quick_evidence_flow.py",
  "backend/tests/test_reference_profile.py",
  "backend/tests/test_release_preflight.py",
  "backend/tests/test_restore_invariants.py",
  "backend/tests/test_retrieval_contract.py",
  "backend/tests/test_retrieval_execution_record.py",
  "backend/tests/test_retrieval_provenance.py",
  "backend/tests/test_retrieval_routing.py",
  "backend/tests/test_run_metrics.py",
  "backend/tests/test_schema_check.py",
  "backend/tests/test_scope.py",
  "backend/tests/test_scope_and_graph_routes.py",
  "backend/tests/test_smart_citation_replay.py",
  "backend/tests/test_smart_table_header_hint.py",
  "backend/tests/test_storage.py",
  "backend/tests/test_structured_answer_spacing.py",
  "backend/tests/test_structured_evidence.py",
  "backend/tests/test_table_header_hint.py",
  "backend/tests/test_targeted_merge_bound.py",
  "backend/tests/test_text_normalization.py",
  "backend/tests/test_version_activation.py",
  "backend/tests/test_xlsx_resource_limits.py",
  "backend/tests/test_xlsx_storage_paths.py",
  "backend/tests/test_xlsx_table_evidence.py",
  "backend/tests/test_version_source_contract.py",
  "backend/tests/test_final_answer_commit.py",
  "backend/tests/test_p2_document_processing.py",
  "backend/tests/test_p2_pdf_offline_closure.py",
  "--basetemp=var/p3-pytest-baseline",
  "--junitxml=var/reports/p3/baseline.xml"
]
```

`final2-targeted`，实际退出码 `0`。以下是 subprocess 实际执行的 argv，环境见同名 command.json。

```json
[
  "C:\\Users\\22088\\.codex\\worktrees\\rag-retrieval-opt-round1\\RAG quention\\.venv\\Scripts\\python.exe",
  "-B",
  "-m",
  "pytest",
  "-q",
  "--tb=short",
  "-p",
  "no:cacheprovider",
  "backend/tests/test_p3_adaptive_chunking.py",
  "backend/tests/test_chunking.py",
  "backend/tests/test_chunk_strategy.py",
  "backend/tests/test_version_source_contract.py",
  "backend/tests/test_version_activation.py",
  "backend/tests/test_final_answer_commit.py",
  "backend/tests/test_structured_evidence.py",
  "backend/tests/test_citation_resolution.py",
  "backend/tests/test_pdf_table_evidence.py",
  "backend/tests/test_p2_pdf_offline_closure.py",
  "backend/tests/test_p2_document_processing.py",
  "backend/tests/test_caption_fact_guard.py",
  "backend/tests/test_local_caption_enricher.py",
  "backend/tests/test_context_neighbor_repository.py",
  "backend/tests/test_retrieval_routing.py",
  "--basetemp=var/p3-pytest-final2-targeted",
  "--junitxml=var/reports/p3/final2-targeted.xml"
]
```

`final2-regression`，实际退出码 `1`。以下是 subprocess 实际执行的 argv，环境见同名 command.json。

```json
[
  "C:\\Users\\22088\\.codex\\worktrees\\rag-retrieval-opt-round1\\RAG quention\\.venv\\Scripts\\python.exe",
  "-B",
  "-m",
  "pytest",
  "-q",
  "--tb=short",
  "-p",
  "no:cacheprovider",
  "backend/tests/test_agent_limits.py",
  "backend/tests/test_agent_tool_allowlist.py",
  "backend/tests/test_agent_trace_sse.py",
  "backend/tests/test_answer_hardening.py",
  "backend/tests/test_answer_service.py",
  "backend/tests/test_answer_validation.py",
  "backend/tests/test_backup_manifest.py",
  "backend/tests/test_bootstrap.py",
  "backend/tests/test_bounded_history.py",
  "backend/tests/test_budget_gate.py",
  "backend/tests/test_cancel_route.py",
  "backend/tests/test_cancellation_boundaries.py",
  "backend/tests/test_caption_fact_guard.py",
  "backend/tests/test_chunk_strategy.py",
  "backend/tests/test_chunking.py",
  "backend/tests/test_citation_group_span.py",
  "backend/tests/test_citation_resolution.py",
  "backend/tests/test_clarification_persistence.py",
  "backend/tests/test_config.py",
  "backend/tests/test_container_injection.py",
  "backend/tests/test_context_and_locators.py",
  "backend/tests/test_context_expansion.py",
  "backend/tests/test_context_neighbor_repository.py",
  "backend/tests/test_context_pool_policy.py",
  "backend/tests/test_conversation_scope.py",
  "backend/tests/test_egress_matrix.py",
  "backend/tests/test_embedding_profile_isolation.py",
  "backend/tests/test_evidence.py",
  "backend/tests/test_evidence_accumulator.py",
  "backend/tests/test_follow_up.py",
  "backend/tests/test_fusion.py",
  "backend/tests/test_graph_evidence.py",
  "backend/tests/test_graph_failure_isolation.py",
  "backend/tests/test_graph_scope_sql.py",
  "backend/tests/test_health.py",
  "backend/tests/test_hybrid_retrieval.py",
  "backend/tests/test_ingestion_claiming.py",
  "backend/tests/test_ingestion_lease_config.py",
  "backend/tests/test_ingestion_retry_route.py",
  "backend/tests/test_ingestion_state_machine.py",
  "backend/tests/test_inline_citation_group.py",
  "backend/tests/test_knowledge_scope_boundaries.py",
  "backend/tests/test_knowledge_tools.py",
  "backend/tests/test_langchain_agent.py",
  "backend/tests/test_langchain_quick_chain.py",
  "backend/tests/test_layering_preview.py",
  "backend/tests/test_legacy_doc.py",
  "backend/tests/test_llm_rerank.py",
  "backend/tests/test_local_caption_enricher.py",
  "backend/tests/test_model_policy.py",
  "backend/tests/test_multimodal_ingestion.py",
  "backend/tests/test_native_release.py",
  "backend/tests/test_native_release_review_regressions.py",
  "backend/tests/test_native_table_evidence.py",
  "backend/tests/test_original_header_boundary.py",
  "backend/tests/test_parsers.py",
  "backend/tests/test_pdf_ocr.py",
  "backend/tests/test_pdf_table_evidence.py",
  "backend/tests/test_preview_contract.py",
  "backend/tests/test_privacy_configuration_answer.py",
  "backend/tests/test_product_gap_contract.py",
  "backend/tests/test_quality_eval_schema.py",
  "backend/tests/test_quality_gate.py",
  "backend/tests/test_query_coverage.py",
  "backend/tests/test_question_checklist.py",
  "backend/tests/test_question_checklist_prompt_replay.py",
  "backend/tests/test_quick_answer_language_prompt.py",
  "backend/tests/test_quick_chain_budget.py",
  "backend/tests/test_quick_chain_quality.py",
  "backend/tests/test_quick_citation_prompt.py",
  "backend/tests/test_quick_evidence_flow.py",
  "backend/tests/test_reference_profile.py",
  "backend/tests/test_release_preflight.py",
  "backend/tests/test_restore_invariants.py",
  "backend/tests/test_retrieval_contract.py",
  "backend/tests/test_retrieval_execution_record.py",
  "backend/tests/test_retrieval_provenance.py",
  "backend/tests/test_retrieval_routing.py",
  "backend/tests/test_run_metrics.py",
  "backend/tests/test_schema_check.py",
  "backend/tests/test_scope.py",
  "backend/tests/test_scope_and_graph_routes.py",
  "backend/tests/test_smart_citation_replay.py",
  "backend/tests/test_smart_table_header_hint.py",
  "backend/tests/test_storage.py",
  "backend/tests/test_structured_answer_spacing.py",
  "backend/tests/test_structured_evidence.py",
  "backend/tests/test_table_header_hint.py",
  "backend/tests/test_targeted_merge_bound.py",
  "backend/tests/test_text_normalization.py",
  "backend/tests/test_version_activation.py",
  "backend/tests/test_xlsx_resource_limits.py",
  "backend/tests/test_xlsx_storage_paths.py",
  "backend/tests/test_xlsx_table_evidence.py",
  "backend/tests/test_version_source_contract.py",
  "backend/tests/test_final_answer_commit.py",
  "backend/tests/test_p2_document_processing.py",
  "backend/tests/test_p2_pdf_offline_closure.py",
  "backend/tests/test_p3_adaptive_chunking.py",
  "--basetemp=var/p3-pytest-final2-regression",
  "--junitxml=var/reports/p3/final2-regression.xml"
]
```

最终证据整理命令：`.venv/Scripts/python.exe -B var/reports/p3/collect_final_evidence.py`；断言失败即非零退出，实际结果见 git-checks.json / final-manifest.json 与工具回执。

### 37 项新增用例

- `backend.tests.test_p3_adaptive_chunking::test_fixed_defaults_and_effective_child_overlap`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_adaptive_profiles_exact_source_and_fallback[# Heading 0\nSource statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. \n# Heading 1\nSource statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. \n# Heading 2\nSource statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. \n# Heading 3\nSource statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. Source statement. \n-heading]`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_adaptive_profiles_exact_source_and_fallback[\u7b2c\u4e00\u7ae0 \u6570\u636e\n\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\u8d44\u6599\u6570\u503c\u4e3a -7.25\u3002\n\u7b2c\u4e8c\u8282 \u8bc1\u636e\n\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002\u5e94\u6838\u5bf9\u539f\u6587\u3002-heuristic]`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_adaptive_profiles_exact_source_and_fallback[OCR line one.\nOCR line two.\n\x0cPage 2\nScanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. Scanned content. -heuristic]`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_adaptive_profiles_exact_source_and_fallback[\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002\u8fde\u7eed\u4e2d\u6587\u6587\u672c\u5e94\u4fdd\u7559\u53e5\u672b\u8fb9\u754c\u3002-legacy]`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_adaptive_profiles_exact_source_and_fallback[UNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKENUNBROKEN-legacy]`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_marker_thresholds_and_fenced_false_headings`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_unterminated_ocr_fence_and_cross_line_hash_are_not_headings`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_heading_coalescing_uses_common_breadcrumb_not_a_sibling_title`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_very_long_heading_is_bounded_without_fabricating_or_truncating_quote`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_rejected_tier_records_reason_and_real_recursive_fallback`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_empty_final_output_is_an_explicit_failure`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_blank_page_separators_do_not_generate_empty_embedding_inputs`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_small_protected_span_remains_whole[| A | B |\n| --- | --- |\n| -7.25 | 123 |\n]`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_small_protected_span_remains_whole[```python\n# hidden heading\nprint(-7.25)\n```]`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_small_protected_span_remains_whole[$$x = -7.25 + y$$]`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_small_protected_span_remains_whole[[\u539f\u6587\u94fe\u63a5](https://example.test/source)]`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_small_protected_span_remains_whole[![\u56fe\u50cf](https://example.test/image.png)]`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_small_protected_span_remains_whole[`inline(-7.25)`]`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_oversized_protection_falls_back_to_bounded_source_spans[```\ncodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecodecode\n```]`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_oversized_protection_falls_back_to_bounded_source_spans[$$\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570\u6570$$]`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_breadcrumb_embedding_and_citation_are_separate`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_parent_children_and_short_equivalent_parent_omitted`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_preview_and_memory_ingestion_use_same_parser_and_configuration`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_profile_identity_includes_input_semantics[change0]`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_profile_identity_includes_input_semantics[change1]`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_profile_identity_includes_input_semantics[change2]`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_profile_identity_includes_input_semantics[change3]`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_schema_and_chunker_versions_are_part_of_profile_identity`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_oversized_native_row_fails_without_splitting_cell_evidence`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_real_frozen_pdf_rows_keep_geometry_numbers_and_citations`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_production_persists_source_context_and_child_only_indexes`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_indexed_version_reprocessing_cannot_overwrite_history`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_production_read_filters_new_identity_and_legacy_profile`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_document_views_exclude_duplicate_parents_but_history_readback_stays_open`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_graph_source_reader_cannot_turn_stored_parents_into_graph_recall`：PASS。

- `backend.tests.test_p3_adaptive_chunking::test_additive_migration_compiles_without_db_and_keeps_same_version_fk`：PASS。

### 29 项既有失败逐项对照

| 节点 | 基线 → 最终 | 同因核对 |
|---|---|---|
| `backend.tests.test_layering_preview::test_declared_layering_has_no_violations` | failed → failed | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_legacy_doc::test_simulated_doc_conversion_reuses_real_table_citation_chain` | failed → failed | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_multimodal_ingestion::test_pdf_and_image_keep_source_locators_and_image_failure_is_recoverable` | failed → failed | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_source_declared_docx_header_qualifies_complete_and_preserves_provenance` | error → error | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_declared_docx_complete_pipeline_accepts_correct_row_unit_and_original_citation` | error → error | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[header-role]` | error → error | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[header-origin]` | error → error | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[row-origin]` | error → error | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[foreign-table]` | error → error | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[missing-unit]` | error → error | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[wrong-column]` | error → error | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[wrong-policy]` | error → error | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[partial]` | error → error | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[conflict]` | error → error | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[header-source]` | error → error | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[header-version]` | error → error | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[header-document]` | error → error | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[chunk-document]` | error → error | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[chunk-version]` | error → error | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_declared_docx_validator_rejects_wrong_generated_claim[\u7d2b\u6e7e\u95e8\u5e97 2027-04 \u7684\u8425\u4e1a\u989d\u4e3a 741 \u4e07\u5143 [E1]\u3002]` | error → error | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_declared_docx_validator_rejects_wrong_generated_claim[\u5176\u4ed6\u95e8\u5e97 2027-04 \u7684\u8425\u4e1a\u989d\u4e3a 741 \u5343\u5143 [E1]\u3002]` | error → error | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_declared_docx_validator_rejects_wrong_generated_claim[\u7d2b\u6e7e\u95e8\u5e97 2027-05 \u7684\u8425\u4e1a\u989d\u4e3a 741 \u5343\u5143 [E1]\u3002]` | error → error | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_declared_docx_validator_rejects_wrong_generated_claim[\u7d2b\u6e7e\u95e8\u5e97 2027-04 \u7684\u8425\u4e1a\u989d\u4e3a 742 \u5343\u5143 [E1]\u3002]` | error → error | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_docx_ambiguous_source_declarations_keep_unknown_and_partial[no-declaration]` | failed → failed | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_docx_ambiguous_source_declarations_keep_unknown_and_partial[false-declaration]` | failed → failed | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_docx_ambiguous_source_declarations_keep_unknown_and_partial[non-leading]` | failed → failed | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_docx_ambiguous_source_declarations_keep_unknown_and_partial[multiple-header]` | failed → failed | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_docx_ambiguous_source_declarations_keep_unknown_and_partial[duplicate-label]` | failed → failed | message/traceback 仅忽略源码行号后相同 |
| `backend.tests.test_product_gap_contract::test_docx_ambiguous_source_declarations_keep_unknown_and_partial[merged-header]` | failed → failed | message/traceback 仅忽略源码行号后相同 |

### 被测项目文件原始字节 SHA-256

报告自身最终哈希见 final-manifest.json，以下不制造自引用哈希。

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| `backend/app/adapters/postgres/knowledge_repository.py` | 90017 | `16339fd8f164114a5ab4e7657bb3ea31e07f82e1ca1fea6750d71d7a92c2a35f` |
| `backend/app/adapters/postgres/graph_repository.py` | 7196 | `b18c1943ee1ab90a13d65a0b2e6f0f7c3a30bc92d2fed4691b001fd4c4103d4b` |
| `backend/app/application/ingestion.py` | 8707 | `a1a6fae1e2cda9ea25b99f8f458106bddebf3225a8b7428f25874c958cf7b89e` |
| `backend/app/config.py` | 6606 | `c674d96f9f9f1d7606fcbe3dc28e186450fc50175e139bdd7d5bb81f84ab6fe4` |
| `backend/app/domain/models.py` | 7986 | `63048a395d1eae122e23573e2c361f59d9dd9ca55cb4214c7d4c3d9bbd53e882` |
| `backend/app/domain/adaptive_chunking.py` | 16705 | `f030ae2dc4d068d04ba99082e037106993874e22c188236e8cdd3fac80681702` |
| `alembic/versions/0016_parent_child_chunks.py` | 1399 | `2ef6e07b51677e137350b40f0ce3ac6eb924299aa5c34d3549154ff3af1fa2da` |
| `backend/tests/test_p3_adaptive_chunking.py` | 20801 | `8c1487175d4de99d4bc557433e41a15d99426da39aa4c944c2eb4e0e3afd6d5e` |
| `backend/tests/test_postgres_chunk_strategy.py` | 1812 | `2a597543d49ec79b2305e5deb7fcea42e32e456a170bd68c07f70f85793caa8b` |

### 冻结 PDF 资产原始字节

| 文件 | 字节数 | SHA-256 |
|---|---:|---|
| `gold.json` | 18157 | `d44ff365b77800fab83b4604b26a53f9152fa8d810851e52f8cf93aae8836f79` |
| `provenance.json` | 1395 | `f31a0e78dd006ebd95ea4e5a1e5aeb613aaaa36e2dafcb76761be436b5506c65` |
| `README.md` | 2139 | `e091c5f5ad31b7cf28ed33e2740c1ca972982a2165badb258a58ee5c3b6ff5a0` |
| `scan_table.png` | 5348 | `1a02f380dd87cd422e8ed0f58773b2d3578a8ee30165b19784416824a3ed67f2` |
| `tables.pdf` | 273256 | `327630979b4097b3f78732d46aea86888c78ffae4298fdad3c6519e86afad08f` |
