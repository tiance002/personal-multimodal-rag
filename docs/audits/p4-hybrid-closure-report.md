# P4 最终 Hybrid 联合闭包

日期：2026-10-09；任务 `P4-HYBRID-CLOSURE/r1/attempt-1`。单协调者、单写入者，`MANUALLY_SUPERVISED_TRIAL`；实际服务模型与 effort 无控制面证明，`UNKNOWN`。本轮没有派发 Worker。

## 1. 结果与执行边界

**P4 限定阶段通过（CHECKS_PASSED，供 Owner 复核）**。同一次实际链路已通过：真实 PostgreSQL/pgvector Dense＋真实 Keyword → weighted RRF → 真实 SiliconFlow Rerank → 合并／Parent 回读／去重 → Top-K → 既有 Context Builder／原始 Child Citation。不是把不同测试拼成真实 Hybrid，也没有静默 Keyword 降级。

这不是全项目发布验收通过，不授予部署批准。历史广泛回归仍有 11 FAIL、20 ERROR、132 SKIP；4 项历史 Caption 仍 NOT RUN；依赖升级影响未独立验证。无 P5 实施、push、tag、部署、迁移、旧库访问或业务索引重建。

开始 HEAD `7dcf273e9dab66dc95632253245c2b5a56dfcb0f`，分支 `codex/local-first-rag-v1-20260930`。开始的 14 个 dirty 路径、原始字节拷贝及全部追踪文件哈希已保存于 `var/reports/p4-hybrid-closure-r1/start-snapshot.json` 和 `start-snapshot/`。开始内容与上一轮 echo final-manifest 一致。14 份已有工作没有丢弃；本轮在其中 `scripts/verify_p4_rerank_once.py` 增加显式 Hybrid 工厂选项，另两份文件仅删除末尾多余空行，Python AST 完全相同，其余 11 份原始内容不变。新增 Hybrid 入口、两份测试及本报告，共 18 个待提交 P4 路径。没有 reset/stash/clean 或删除文件。

条件提交按 Owner 本任务授权执行；最终 SHA、父提交、18 文件清单、暂存 blob/工作树字节核验和提交后状态见同目录 `commit-receipt.json`、`staged-checks.json`、`final-manifest.json`。本文所在提交就是本次交付版本，避免将提交 SHA 写入自身造成循环修改。

已读取 AGENTS、progress、ADR 索引和现存 P4 报告。原 Temp 的任务包当前不在，未臆造其内容；本轮以此前 P4 执行报告保存的验收条目、Owner 原始 P4 授权和本任务明确边界核对。**未实现的命令不得报告通过。**

## 2. 发送前一次性设计

有界检查已授权 P4 证据目录，没有发现同时具备可验证来源、相同输入文本哈希和完整 Profile 身份的可复用真实向量。此前 Preflight 语料不同，未保存向量，业务数据已回滚。检查范围与结果在 `cache-inspection.json`；未扫描私有语料或其他缓存。

因此复用现有正常 `SiliconFlowEmbedding.embed` 的列表入口，将 5 个 Child 的完整 `embedding_content`（ContextHeader＋正文）与问题 `Synthetic cost` 放在同一批次。没有另造直连发送器。固定合成语料、P3 prepare_document 参数、Child／Parent 结构均与此前 PG fixture 一致：

- 语料 SHA-256：`7e2773faf0ef01fafefb488d37a7cfffce35ca66dbd8aae9b45db348753ba330`，1380 字节。
- Embedding 请求：6 项、1689 UTF-8 字节，SHA-256 `b109830bb6402f7999e8eda1f30e688a4accbeff36ccc6cb34690ffa00086b6d`。
- 固定本地 tokenizer：`BAAI/bge-m3@5617a9f61b028005a4858fdac845db406aefb181`，tokenizer.json SHA-256 `21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08`，tokenizers/0.23.2；本批计数 526，全部输入通过现有逐项容量与批量上限检查。
- 继续使用获准的 7680 client 输入边界和既有 8192 文档容量依据；供应商内部是否截断仍 UNKNOWN，不扩大为全模型输入完整性保证。
- 只用精确非 Pro `BAAI/bge-m3` 与 `BAAI/bge-reranker-v2-m3`。本轮直接打开[官方价格页](https://siliconflow.cn/pricing)，两者均标免费，Pro 单独收费。`price-evidence.json` 记录实际核价时间和来源；公开价格不冒充账户结算凭证。
- 唯一新身份 `p4-hybrid-joint-r1`，排他目录 `var/reports/p4-hybrid-closure-r1/live/`。最多 Embedding 1 次、Rerank 1 次，均 30 秒、无重试，无 probe／最终回答模型。

预算发送前 cap10、used8、remaining2 microunits，每个正常请求仍预占1，恰好足够；没有调整 cap、释放旧 UNKNOWN 或把免费价格当作绕过预算的理由。账户初态记录 UNKNOWN，由本次获准请求观测可用性。`authorization.json` 精确绑定请求 hash、字节、模型、次数、0 CNY 费用上限、Owner 当前任务来源和核价证据。

新批次返回的查询向量仅在同一进程复用一次。`VerifiedQueryCache` 校验 query hash、完整模型／维度／fingerprint、Profile、有限非零向量和外发 Scope；缓存不命中即拒绝，不发第二次 Embedding。真实完整向量保存在 `live/real-vectors.json`，SHA-256 `268805dbe7ee828edcb74f98ecb0a0b04185dea61db91301e4fa66b3e1f51d5f`；它是本次真实响应证据，不能直接当作已持久化业务索引。Profile ID 所属事务已经回滚，未来复用须重新验证输入、身份和持久 Profile 映射。

## 3. 实际改动与正常入口

| 文件／符号 | 本轮变化与保护 |
| --- | --- |
| scripts/verify_p4_rerank_once.py / live_container | 默认 Keyword 路径保留；增加显式 Hybrid＋已核验 admission，正常 ProviderFactory 构造 Embedding，两个角色共享实际 PostgresBudgetGate 账本，cap10、每请求1均不变。 |
| scripts/verify_p4_hybrid_once.py | 新独立验证入口；默认仅输出 NOT_RUN。精确授权、排他收据、固定批次、正常工厂／预算／AuthorizedTransport；观察真实 SQL 候选和 actual RRF，不替换检索；业务 SQL 最终回滚。REAL 与 SIMULATED 工厂分离，真实入口不能换目录／factory 复用身份。 |
| backend/tests/test_p4_hybrid_once.py | 41 项 SIMULATED 用例：缺授权／免费证据零发送；正常精确批次最多一次；正文／URL／超时不符零发送；缓存身份与 Scope；双路 RRF 贡献；拒绝静默降级。 |
| backend/tests/test_p4_hybrid_postgres.py | 7 项真实 PG/pgvector＋明确 SIMULATED 模型 HTTP／预算演练：成功、Embedding401、Rerank403、null 正文、Dense 空、Keyword 空、预算不足；同一模拟账本保留8条旧 UNKNOWN，业务回滚，重复目录零重发。凭据加载器在读取前被测试 fixture 替代。 |
| docs/audits/p4-hybrid-closure-report.md | 本轮报告。 |

另 13 份已交接 P4 dirty 文件随独立提交收口，其修复范围、旧失败和断言依据见保留的 `p4-rerank-echo-compatibility-report.md`、`p4-rerank-offline-diagnostics.md`。暂存审查发现 `backend/tests/test_p4_rerank_integrated.py`、`scripts/verify_p4_integrated_offline.py` 各有末尾多余空行，默认cached检查退出2；失败保存在 `staged-whitespace-failure.json`，仅清理这两处空行，AST前后完全一致，字节/hash差异见 `staged-whitespace-repair.json`。其中生产 cloud.py 的结构诊断与严格 true 正文回显字节本轮完全未变；没有修改已经通过的接受规则。

正常链：`live_container → ProviderFactory / BudgetUsageGuard / PostgresBudgetGate → SiliconFlowEmbedding → 原 Repository profile/child vectors → HybridRetriever.retrieve → keyword_candidates + vector_candidates → rrf_fuse → scoped current-child 再验证 → SiliconFlowRerank + OnceTransport/AuthorizedTransport → read_parent_contexts / merge_passages → group Top-K → EvidenceService / ContextBuilder → CitationService.freeze / EvidenceSnapshot`。

没有新模型名单、P3 参数、Embedding Profile 合同、Migration、路由策略、代理、ACL 或凭据配置变化。scope 是当前项目服务端 KB／可选 document 范围；本项目没有多租户表，不将单用户项目验收夸大为多租户验证。

## 4. 同次真实链路证据

`live-command.json`：实际进程退出 0；`live/live-receipt.json`：`HYBRID_JOINT_PASS`、`evidence_kind=REAL`。

| 阶段 | 实际结果 |
| --- | --- |
| Embedding | 正常入口发送1次；HTTP200，130836响应字节；6个合法1024维向量；本地 planned526，供应商 ACTUAL usage prompt526/completion0/total526。 |
| SQL Dense | 真实 pgvector 检索5个 Child，使用本次真实向量＋完整 Profile；保存的向量独立复算 cosine，与PG分数在1e-6内一致。 |
| SQL Keyword | 真实词项检索5个 Child，分数16/16/16/16/4；不是 BM25，未改算法。 |
| Fusion | 原真实配置 vector0.7/keyword0.3、k60；每个候选的两个贡献及求和均核对，双路排名不同。 |
| Rerank | 正常入口发送1次；true，top_n5；1734字节，SHA-256 `5a4be07cd4d89690afefe0ccd1e99ce043a5cc55cf8ee05450e8863bf29b02e8`；HTTP200，2193响应字节；5/5，document对象，每项原文与合法 index 精确匹配。 |
| 诊断 | complete/NONE；顶层object，results存在且array，model/error/data缺失，first_failure_row为空。没有虚构缺失 model 回显；没有保存响应原文、请求头或任意错误字符串。 |
| Parent／Top-K | 5个 Child → 1个去重 Parent 上下文组；最终context组1、Parent1、证据5、文档1；mode hybrid，sources keyword/vector，degradation_flags空。 |
| Context／Citation | 复用既有 Builder，context2994字符，E1–E5；Parent完整正文在辅助上下文出现1次，五份原 Child 的 quote／offset／hash／version／locator保持准确。answer_correctness UNKNOWN，未调用回答模型。 |

以下 C0–C4 是 `pipeline.children` 的原始顺序别名；完整 UUID、版本、父块映射与 locator 保存在收据及 `independent-evidence-review.json`。

| Child | 原文 [start,end) | Dense rank | Keyword rank | RRF rank | Rerank rank |
| --- | --- | ---: | ---: | ---: | ---: |
| C0 | [0,368) | 5 | 4 | 5 | 2 |
| C1 | [322,690) | 1 | 3 | 1 | 5 |
| C2 | [644,1012) | 2 | 2 | 2 | 4 |
| C3 | [966,1334) | 4 | 1 | 3 | 3 |
| C4 | [1288,1380) | 3 | 5 | 4 | 1 |

例如 C1：Dense贡献 `0.7/(60+1)=0.011475409836065573`，Keyword贡献 `0.3/(60+3)=0.0047619047619047615`，合计 `0.016237314597970336`。RRF不是语义置信度。真实 Rerank 顺序 C4→C0→C3→C2→C1，评分依次 `[0.9855782389640808, 0.9723092913627625, 0.9521055817604065, 0.9510282278060913, 0.9482484459877014]`；供应商 ACTUAL usage550/0/550。

完整身份：KB `9076a20e-d0cd-4b6f-9e96-5d9ff47eedf9`，document `36f45779-7098-4fab-9ba0-d02ecd6f8834`，version `4a9c61d3-79a1-480b-941a-46baf6903c9a`，Parent `0b7d928b-a4b5-4064-92a9-c59a15c0180b`。Profile `81458725-a09b-41c6-9997-6a4da355c5fd`，fingerprint `6c451334d5651e277d8d4dc2eacbef3f5b77bfd53b57cb162b9150e937f07d21`；provider siliconflow/model BAAI/bge-m3/revision UNKNOWN/dim1024/cosine/semantics context-header-child/v1；index `p3:391208cb6fb98bbcd68552703cfa80d158973c46902eb9f321a1c1f02d88c041`。Parent未进向量索引；错误Scope/Profile查询为空。完整KB/doc/version/index边界另由保留的PG及离线反例验证。

## 5. 测试、基线及原 P4 验收清单

| 证据 | PASS | FAIL | ERROR | SKIP | 进程／pytest退出码 |
| --- | ---: | ---: | ---: | ---: | --- |
| 本轮 checks-1，SIMULATED HTTP/DB | 363 | 0 | 0 | 0 | 0/0 |
| 本轮 checks-2，末尾空行修正后最终源码 | 363 | 0 | 0 | 0 | 0/0 |
| 本轮 pg-rehearsal-1，REAL PG、SIMULATED模型与预算 | 7 | 0 | 0 | 0 | 0/0 |
| 复用 targeted-echo-final | 403 | 0 | 0 | 0 | 0/0 |
| 复用 postgres-echo-final，REAL PG、SIMULATED模型 | 5 | 0 | 0 | 0 | 0/0 |
| 复用 broad-echo-final，非全绿 | 1285 | 11 | 20 | 132 | 1/1 |

不能将重叠测试数量相加当唯一通过项数。checks-1／checks-2各覆盖已有322项加新增41项，网络／secret_file／subprocess／database拦截计数均0；没有真实模型调用。各轮测试前后源码哈希一致；最终以checks-2为准。pg-rehearsal-1是7个独立case，不冒充7次真实模型验证；其相关入口与测试文件最终字节未变。真实调用之后仅修改上述两个非业务文件的末尾空行，没有重发请求。

原 broad／targeted／PG 被测源码哈希与最终源码逐项一致；本轮只新增验证入口／测试及调整未在broad清单中的验证工厂，均由本轮363＋7定向验证覆盖。独立核验 `broad_source_drift=[]`，因此按Owner“避免无变化反复跑整套”要求，本轮广泛回归 **REUSED_WITH_HASH_VERIFICATION，NOT RERUN**，不将旧结果伪装为新执行。

原 P4 必需门禁逐项闭合：固定上游源码身份、保留Hybrid/RRF、真实精确模型Rerank、完整候选排列与异常退化、合并／整邻块／Parent边界、组Top-K、Context与原始引用、Scope/current/version/index/Profile隔离、外发／预算／重试／持久收据保护。本轮再次读取本地固定上游6文件并核对Git Blob SHA及SHA-256，全部一致；没有切换上游或参数搜索。原生产实现的证据位置见 `p4-weknora-rag-report.md`，最新缺口由本轮同次真实Hybrid补齐。

`regression-classification.json`逐项保存31个FAIL/ERROR及132个SKIP，不把SKIP算PASS：

- product_gap_contract：20 ERROR＋6 FAIL缺 native runtime，是此前 DOCX声明／解析至QuickChain的非P4缺口；相关断言没有执行，仍未完成。不能以其他格式通过冒充这些DOCX用例通过。
- legacy_doc：2 FAIL缺 native runtime、1 FAIL缺转换fixture，旧DOC转换能力仍未完成，未扩修。
- layering：1 FAIL，原三项违规及源码字节一致，非本轮新增；没有为清绿做跨模块重构。
- multimodal_ingestion：1 FAIL。基线PermissionError变为OCR_UNAVAILABLE，而断言期待OCR_EMPTY；原因变化单列，仍FAIL，真实OCR可用性不宣称已验证。
- 132 SKIP原样保留。其中原5个PG用例已有独立真实PG通过证据；其余受开关／运行环境等限制的用例未执行，逐项理由见JSON/JUnit。
- 原4项历史Caption缺目录仍NOT RUN，不冒充其解析／真实VLM通过。

P4必须保留的证据消费保护有实际通过的覆盖：37项p4_rag_pipeline（含native proof原对象不改写、Scope、Parent、整块、原引用）；52项structured_evidence（数值、单位、实体、来源／版本、错误原引用拒绝）；原10项PDF表格合同＋6项PDF闭包走真实解码、geometry/bbox/页码／引用；90项caption_fact_guard防止Caption替代精确原始数值证据；33项FinalAnswerCommit保护。合计228项可定位的执行证据列于JSON，这些证明相应P4消费不变量，不代替上述缺失的DOCX/OCR/VLM运行环境验收。

沿用原始 `baseline.xml`（1234 PASS/25 FAIL/20 ERROR/127 SKIP）及逐ID因果对照：无新增失败ID；原29历史失败仍失败，14项因获准执行环境变化现通过，不能记成本轮业务修复。30项持续失败细节完全相同，OCR1项原因改变。历史所有失败、旧140 PASS／70 setup ERROR及后续静态记录全部保留，不作为当前源码通过证据。**依赖升级影响未独立验证**。

## 6. 实际命令与证据

以下相对项目根目录，实际argv、退出码、JUnit及前后源码hash见同名记录：

```text
.venv/Scripts/python.exe -B var/reports/p4-hybrid-closure-r1/preflight_readonly.py       # 0，只读初核
.venv/Scripts/python.exe -B var/reports/p4-hybrid-closure-r1/offline_checks.py checks-1 # 0；363 PASS
.venv/Scripts/python.exe -B var/reports/p4-hybrid-closure-r1/offline_checks.py checks-2 # 0；最终363 PASS，唯一修复轮
.venv/Scripts/python.exe -B var/reports/p4-hybrid-closure-r1/run_pg.py                  # 0；7 PASS
.venv/Scripts/python.exe -B var/reports/p4-hybrid-closure-r1/final_preflight_readonly.py # 0；最终发送前
.venv/Scripts/python.exe -B var/reports/p4-hybrid-closure-r1/run_live.py                # 0；只启动下述正常入口一次
.venv/Scripts/python.exe -B scripts/verify_p4_hybrid_once.py --execute-owner-authorized-once --authorization-file "C:\Users\22088\.codex\worktrees\rag-retrieval-opt-round1\RAG quention\var\reports\p4-hybrid-closure-r1\authorization.json" # 0
.venv/Scripts/python.exe -B var/reports/p4-hybrid-closure-r1/postflight_readonly.py       # 0
git diff --check            # 0，最终记录见git-checks.json
git diff --cached --check   # 必须0，暂存后实际记录见staged-checks.json
```

pg-rehearsal实际子命令为 `.venv/Scripts/python.exe -B -m pytest backend/tests/test_p4_hybrid_postgres.py -q --tb=short -p no:cacheprovider --basetemp=<本轮pg-rehearsal-1/tmp> --junitxml=<本轮pg-rehearsal-1/junit.xml>`，唯一DB opt-in明确开启，模型传输和账本用SIMULATED fixture。完整绝对argv在 `pg-rehearsal-1/command.json`。checks-1完整8测试文件argv在 `checks-1/report.json`；pytest与进程退出分别留证。

正常提权遵循Owner此前明确授权，通过工具批准流程执行必要测试／DB／真实有界调用。未关闭沙盒、修改ACL、扫描全机进程、换凭据或配置代理。既有Python launcher location警告保留，命令实际退出码不依赖警告推断。

## 7. 数据、预算和历史保全

唯一获准DB现场身份：127.0.0.1:25438 / `rag_clean_dev_20261008t072656z_352f705b` / OID21278 / system identifier7691227493754040358 / migration0017_embedding_profile_identity。Storage基线 `D:\RAG-CleanSlate\cs0_20261008t072656z_352f705b\storage` 不变。验证fixture的合成文件位于本轮独立证据runtime目录；没有全量摄取或业务Storage清理。

全部23张业务表前后0；Embedding Profile、chunks、chunk_embeddings、chunk_terms及KB/document/version均随事务回滚。正常model_calls独立持久化8→10，旧8行全部逐列一致。`postflight.json`保存新2行ID及保守占用。cap10、used10、remaining0 microunits。此后没有发任何模型请求。

本轮实际发送Embedding1＋Rerank1，合计3423字节；无probe、重试或回答模型。供应商观测usage合计1076 tokens（ACTUAL），本地预占合计2 microunits（ESTIMATED保守占位），实际费用／结算继续UNKNOWN，不能写成已结算0元。

旧五次Rerank收据hash及UNKNOWN账本保持不变。加本轮Rerank后累计6次／4375字节，其中旧3次失败、旧2次成功、本轮1次成功；旧三次失败681字节没有归零、退款或改成功。全账本10行UNKNOWN包含历史Embedding；未虚构其不明usage或总外发字节。

开始全部已追踪文件本轮字节不变；27个既有受保护Migration／P3／模型配置／冻结PDF资产hash再次通过。gold.json仍为历史18157字节及原SHA。未读取旧业务库，不将“未访问”写成旧库计数实测；删除数量0。没有读取或输出真实Key，只有正常项目加载器在获准真实入口内存使用已有凭据。

## 8. P5可用接口与真实特征字段

这里只交接已存在的接口和字段，不冻结任何新路由权重、阈值或cheap升级策略：

- `KnowledgeGateway.retrieve(scope, QueryPlan)`、`retrieve_query(scope, question, query_plan=...)`；底层 `HybridRetriever.retrieve(scope, question, query_plan=None)`。服务端Scope仍是授权边界，客户端不能扩大。
- `RetrievalResult`：items、candidate_rankings、fused_ranking、sources、reason_codes、effective_config、retrieval_mode、route_reason、degradation_flags、stage_latency_ms、latency_ms、embedding_cache_hit、context_items、merge_provenance、retrieval_stats。
- `RankedHit`：chunk_id/rank/raw_score/fused_score/sources。raw_score和RRF不同尺度，RRF不是置信度；真实Rerank分数在 `RerankResult.relevance_scores`，不要读取不存在的scores字段，也不要用它覆盖原hit来源metadata。
- `RetrievalItem`：原ChunkRecord＋原hit＋可选ContextPassage。ContextPassage的unit_ids／parent_ids／neighbor_ids用于回溯辅助正文；只有原Child可冻结为引用证据。
- `EvidenceService.plan → bundle → with_context` 返回 `EvidenceBundle`：plan/retrieval/selected/decision/context/labels/snapshots/quality/evidence_stats。`EvidenceSnapshot`保留chunk/document/version/quote/quote_sha256/locator；E标签映射原证据，Parent上下文不获新证据权限。
- retrieval_stats：candidate_count/merged_count/final_context_count/final_evidence_count/document_count/parent_count/cross_document/contains_table/contains_image_caption_or_ocr/answer_correctness。evidence_stats：final_evidence_count/context_chars/document_count/parent_count/coverage_status/conflict/answer_correctness。
- 既有 `EvidenceQualitySignals`：retrieval_scores、top1_topk_gap、vector_keyword_agreement、number_of_supporting_chunks、number_of_source_documents、context_diversity、conflicting_evidence、retrieval_confidence。agreement是候选重叠、gap仅原向量尺度；未校准的confidence和未评估的conflict保持None，quality不等于回答正确。
- 预算：正常ProviderFactory与每role外发开关、KB许可、BudgetUsageGuard／PostgresBudgetGate保留；token观测与费用结算分开。当前有界验收账本余额0，P5不能自动复用本批授权或清理UNKNOWN来继续外发。

对应源码：`backend/app/application/retrieval.py`、`knowledge_gateway.py`、`evidence_quality.py`、`retrieval_merge.py`，`backend/app/domain/models.py`、`backend/app/ports/providers.py`、`backend/app/application/provider_usage.py`。不实施P5。

## 9. 回滚与停止

业务测试数据已经回滚，正常模型账本与收据须永久保留，不能退款或删除UNKNOWN。代码回滚建议：先保护当前证据和任何后续dirty，再单独逆向审阅本次独立commit；此前P4实现已在父提交，不能用reset误删。未实际执行回滚。

本轮代码及必要离线门禁、真实同次Hybrid闭包检查完成，按授权形成独立本地提交后停止。历史环境缺口、真实模型内部截断UNKNOWN、供应商结算UNKNOWN、无最终回答模型质量验收继续保留；Owner复核与后续P5方案另行处理。
