# P4-R1 — 固定源码补证与 RAG 主链执行报告

日期：2026-10-08（Asia/Shanghai）。任务 `P4-R1 / r1 / implementation-1`。

## 1. 当前结果与停止原因

**P4_BLOCKED**。固定 WeKnora 源码获取阻断已解除；生产接线和离线合同检查完成，真实 PostgreSQL 的合成数据测试通过。但是首次获准的真实 SiliconFlow Rerank 请求返回适配器错误 `ProviderUnavailable`，按“异常即停止”约定结束，没有重试或第二次请求。账户权限、真实 Rerank 成功响应及真实 Rerank 与 PostgreSQL 的联合端到端验收尚未通过。

代码检查结果记 `CHECKS_PASSED / READY_FOR_EXTERNAL_VALIDATION`，不能替代 `P4_PASS`，也不表示阶段验收或部署批准。无 commit、amend、push、merge、tag；不进入 P5。当前 HEAD 仍为 `4ffdaeafc4ffbdd6d506d46f17a5f90872e04d6e`。

执行方式 `MANUALLY_SUPERVISED_TRIAL`，本轮单协调者、单写入者，无新 Worker。实际服务模型和 effort 无控制面证据，记 `UNKNOWN`。未修改全局模型、权限、TLS、代理或 Git 配置。**未实现的命令不得报告通过。**

本轮开始读取过原 `05_P4_RAG.md` 和全局合同；交付整理时再次读取原 Temp 路径的 `01_GLOBAL_EXECUTION_CONTRACT.md` 返回文件不存在。该临时附件状态单独记录，未据此扩大范围，也未重新推定合同内容。此前已读取的执行约束继续遵守。

## 2. 固定上游源码门槛：PASS

固定提交 `Tencent/WeKnora@3e8b0bfc80b845b2d4b2ed683994748741450a97`，GitHub Git commit API 返回同一 SHA，tree 为 `9533ab2071e71f4bc2ebb09c85d3ac246841ff9a`。这是 Git 对象/内容身份验证，不宣称 GPG 签名验证。

本机 `git -C E:/WeKnora rev-parse --verify <commit>^{commit}` 退出 1，实际原因 `Permission denied`。没有修改原仓库权限或绕过保护。随后使用已授权 GitHub 连接，按固定 ref 取得原始字节；独立计算 `SHA1("blob " + byte_length + NUL + bytes)`，与完整 Blob SHA 及 Owner 给出的前缀逐项一致。下载全部保存在 D 盘，没有将上游源码下载到 C 盘。

| 固定源码路径 | 字节 | 验证后的完整 Git Blob SHA |
| --- | ---: | --- |
| internal/application/service/chat_pipeline/merge.go | 15380 | 21263c8a1ec4b0332ab95bc18c44b3c4abafc459 |
| internal/application/service/chat_pipeline/merge_expand.go | 7826 | e459e96c5874361a49f9081fb606810f2ead6741 |
| internal/application/service/chat_pipeline/rerank.go | 24080 | 659a0c6590b3d7bac0c71597a9ee4169b9b332df |
| internal/application/service/chat_pipeline/filter_top_k.go | 3259 | 6af579960922a81877f4114a234d3a7828f3741f |
| internal/searchutil/chunkmerge.go | 8475 | a8149577b7325a87fd0f558e4904a5eae71409c8 |

有界补读依赖 `internal/application/service/chat_pipeline/merge_overlap.go`，Blob `025e6c69292811dcda942c8fa75f9b3e032a37cc`，用于核对可信 offset 重叠处理。未审计上游全仓。

原始文件、SHA-256、提交/tree 和连接来源保存在 `D:/RAG-ModelAssets/weknora/3e8b0bfc80b845b2d4b2ed683994748741450a97/p4-r1/source-manifest.json`。五份必需源码均实际可读，最终重新计算字节哈希通过。连接曾有一次目录读取传输错误，随后同一固定来源读取成功；没有切换上游版本。

## 3. 保留、采用和必要适配

| 范围 | 本轮行为及依据 |
| --- | --- |
| Hybrid 与 weighted RRF | 保留现有 vector 0.7、keyword 0.3、k=60；不把 RRF 分数解释为语义置信度；q0 退化路径保留。 |
| Keyword | 保留当前词项检索 backend；它不是 BM25，本轮不修改数据库来实现 BM25。 |
| Rerank | 接通既有独立 provider/model port；只调整已核验候选的排列，错误/空结果/注入/重复/缺失均回退 fusion ranking。 |
| Merge | 参考固定 merge/merge_overlap/chunkmerge 的同来源分组、顺序合并、包含和重叠处理，再按最优原排名全局排序去重。 |
| 文本匹配参数 | 使用固定 chunkmerge 的最小重叠 12、最大匹配窗口 400 字符；可信 offset 优先精确重叠，保留重复行和周期文本。未进行参数搜索。 |
| Parent | 固定上游会回填 Parent 并保留子块来源；本项目以独立 ContextPassage 表达辅助正文，保留原 Child 原文、ID、hash 和 locator。SQL 同时限制 KB/doc/version/index_identity/current。 |
| 邻接扩展 | 固定上游 min350/max850 字符，会递归取邻块并切到850。本项目复用已有至多10个 seed、每个±1邻块的有界读取，只接纳完整正文且合并后≤850，不截断原子证据。350/850 是字符边界，不是模型 token 容量。 |
| 授权差异 | 上游共享 KB 路径的 Parent 查询不直接套用到本项目；本项目所有 Parent/neighbor 仍经过服务端 Scope、当前版本和 index_identity。 |
| Top-K | 在合并后的上下文组筛 Top-K；组内保留独立原始 Child 证据，因此 evidence unit 数可大于 Top-K。 |
| Context | 复用既有 ContextBuilder 的选择与 hard limit。辅助正文不赋 E 标签，原始 Child 按原文冻结引用；Parent 太大时保留完整 Child，不截断 Child。 |
| 不引入的上游行为 | 不引入历史检索注入、FAQ/shared-tenant/image-grandparent 专用流程、阈值重试或 P5 Router。没有自动换模型/上云补救。 |

## 4. 完整生产调用链与合同

`bootstrap.build_container → HybridRetriever.retrieve → keyword/vector candidates → 原 weighted RRF → scoped current-child 再验证 → 可选 SiliconFlowRerank → scoped Parent/neighbor read → merge_passages → group Top-K → 既有 ContextBuilder.select/build → CitationService / EvidenceSnapshot → 原 AnswerValidator / FinalAnswerCommitCheck`。

生产接线是可配置实现，冻结配置没有被改成默认外发：`backend/app/bootstrap.py:159` 仅在原 rerank_enabled 打开时通过 ProviderFactory 建立 ranker；仍受全局云开关、独立 rerank 外发门、registry enabled、KB 外发许可、项目级 Key 与 BudgetUsageGuard 共同约束。MMR/rewrite 等原限制保留。

| 位置/符号 | 实际实现与边界 |
| --- | --- |
| backend/app/application/retrieval.py:200 HybridRetriever.retrieve | 保留检索/fusion；先批量核验 Scope/current/child，再让 reranker排序；返回必须是完整候选排列，不能加入候选或借返回 metadata 改写证据。失败保留原 fusion 结果。 |
| backend/app/adapters/models/cloud.py:274 SiliconFlowRerank | 既有独立配置/预算/授权/timeout；发送后的 provider 错误打开本实例 circuit，后续不自动探测；本地请求未发送的授权拒绝不污染其他许可 Scope。 |
| backend/app/adapters/models/cloud.py:48 _rerank_usage | 接受供应商标准 usage 或 meta.tokens.input/output；没有字段则 UNKNOWN，不按字符虚构 token。观测 token 与实际结算分开。 |
| backend/app/adapters/postgres/knowledge_repository.py:815 get_retrieval_chunks | 仅服务端 Scope 内 active/current/ready Child；保留现有 Profile/向量身份查询。 |
| 同文件:833 read_parent_contexts | 子块与父块均匹配 KB/doc/version/index_identity/role/current；Parent 只供上下文。 |
| 同文件:964 read_context_rows | 复用既有有界邻块 reader，补充当前 Child 与 index_identity 约束，不对父块建检索索引。 |
| backend/app/application/retrieval_merge.py:52 valid_context / :69 merge_passages | 校验 hash/Scope/version/index_identity；纯文本可信 offset 先精确合并，结构化行保持独立 proof；最终分组仍能回到全部原始 Child。旧无分块 metadata 的平面记录不被强行顺序合并。 |
| backend/app/application/context_builder.py:30 select / :85 build | 原 whole-chunk/per-doc/hard-limit 选择保留；辅助正文标“不是引用证据”，原 E 引用冻结原 Child。无法容纳整 Parent 时退回整 Child。 |
| backend/app/application/knowledge_gateway.py:38 EvidenceBundle / :164 with_context | 输出只读统计，已有质量/覆盖/验证流程继续工作，不产生动态模型选择。 |
| backend/app/domain/models.py ChunkRecord | 尾部追加可选 parent/role/index/type/index_identity 元数据以保持旧构造兼容；没有 Schema/Profile/Migration 变化。 |
| scripts/verify_p4_rag.py:22 AuthorizedTransport | 独立收据、发送前持久记录尝试、精确 URL/model/合成正文允许集、累计3次/20KB、无重试；不记录 Authorization 或原始 Key。 |

引用映射：`ContextPassage.unit_ids → 原 RetrievalItem.chunk → CitationService.freeze → EvidenceSnapshot`。合并正文可包含 Parent/neighbor；可引用的 E 单元始终保留 Child 原始正文、SourceLocator、source offset、SHA 和版本。结构化表格 proof 不被合并正文替换。辅助上下文去重与原始证据展示分开，原始引用区可能再次包含相同文字，这是稳定引用的投影，不伪造新原文。

只读 stats 包括候选数、合并/最终上下文组数、最终证据数、doc/parent数、跨文档、table/caption/OCR标志、coverage、已有冲突结果及实际context字符数。未形成确定性冲突时不凭空推定；answer correctness 为 UNKNOWN。检索不足仍是 retrieval/evidence 问题，不触发贵模型。

## 5. 实际测试、首轮失败与基线对照

所有历史轮次 XML/log/source snapshot 均保留在 `var/reports/p4-r1/`，未重新写成全绿。离线执行去掉 API Key，关闭云端/追踪，将普通 DB 指向 loopback port1；真实 PG 专项另用已有身份核验后的 rollback fixture。

| 轮次/证据 | PASS | FAIL | ERROR | SKIP | 退出码 / 含义 |
| --- | ---: | ---: | ---: | ---: | --- |
| baseline.xml | 1234 | 25 | 20 | 127 | 1；P4 修改前的当前环境基线 |
| targeted-first.xml | 172 | 32 | 0 | 0 | 1；首轮失败保留 |
| targeted-final.xml | 396 | 0 | 0 | 0 | 0 |
| targeted-final2.xml | 399 | 0 | 0 | 0 | 0 |
| targeted-final3.xml | 400 | 0 | 0 | 0 | 0；最终定向 |
| postgres-first.xml / postgres-final.xml | 各5 | 0 | 0 | 0 | 各0；真实PostgreSQL，模型HTTP为SIMULATED |
| broad-final.xml | 1267 | 25 | 20 | 132 | 1；中间广泛回归 |
| broad-final2.xml | 1268 | 25 | 20 | 132 | 1；最终源码广泛回归 |

首轮32项失败：2项揭示旧平面记录缺少 chunk index/parent metadata 时不应默认合并，已限制该路径；其余30项为既有 SQL capture 测试用 object.__new__ 构造 repository 未提供当前实际需要的 ChunkingConfig，补最小 fixture 配置。旧注入测试由“抛异常”更新为任务要求的“拒绝注入并退回原候选”，没有删除外库/原候选断言。数值、引用和原生表格 proof 的相关定向回归通过。

`failure-baseline-comparison.json` 对照当前基线：45个失败/错误ID完全相同，`new_failures=[]`、`resolved_failures=[]`。原历史29个失败全部仍在当前最终结果中，并保留逐项原始 JUnit 错误与原因对照。另16个当前环境基线失败/错误已在本次代码修改前出现，涉及现有环境/来源访问等限制；不冒充已修复。广泛回归绝不是全绿。

新增5个 PG 用例在离线 broad 中 SKIP，另行真实 PostgreSQL 执行全部5 PASS；不把 SKIP 算PASS。原4个因历史目录缺失而不能执行的 Caption 测试保持 **NOT RUN**。原PDF/历史fixture及P3分块文件不修改。保留限制：**依赖升级影响未独立验证**。

新增合同覆盖：vector/keyword/hybrid；rerank顺序/注入/重复/缺失/空/异常回退；Scope再验证；Parent重复去重与跨库/文档/版本/index拒绝；原引用hash/offset/quote；可信周期文本重叠；有界整邻块；超大Parent整Child回退；native proof；不足证据；预算/外发门；circuit与实际usage；真实入口请求次数/字节硬界。

真实 PG 用例使用 unchanged P3 `prepare_document` 生成合成资料，写真实 PostgreSQL/pgvector，向量与模型HTTP显式 **SIMULATED**；所有合成业务数据在外层事务结束回滚。它验证 Repository→检索→Parent→Context→引用的 SQL/合同，不代表真实上传worker或真实Embedding验收。本轮没有调用真实Embedding/Chat/Vision/OCR。

实际命令与退出码：

```text
.venv/Scripts/python.exe -B var/reports/p4-r1/run_checks.py targeted-final3  # exit 0
.venv/Scripts/python.exe -B var/reports/p4-r1/run_checks.py postgres-final   # exit 0
.venv/Scripts/python.exe -B var/reports/p4-r1/run_checks.py broad-final2    # exit 1
.venv/Scripts/python.exe -B scripts/verify_p4_rag.py --execute-owner-authorized  # exit 1
git diff --check  # exit 0（最终以 git-checks.json 为准）
git diff --cached --check  # exit 0，暂存区为空
```

前三项实际 pytest argv、文件范围与环境策略在相应 `*-command.json`；实际日志和JUnit同名前缀。基线实际argv/退出码在 baseline-command.json / baseline-exit.json。首轮直接调用 pytest 的5文件为 test_p4_rag_pipeline.py、test_retrieval_routing.py、test_model_provider_contracts.py、test_context_pool_policy.py、test_context_neighbor_repository.py；JUnit保留。命令输出存在 `Failed to find real location of D:\Drivers\python\python.exe` 警告，未修环境，实际Python/pytest退出码独立记录。

## 6. 获准真实调用：首次失败即停止

先核 [SiliconFlow 官方定价](https://siliconflow.cn/pricing) 的基础模型 `BAAI/bge-reranker-v2-m3` 免费公开标记；这不证明当前账户权限或实际账单为0。[Rerank API 文档](https://docs.siliconflow.cn/docs/api/rerank-post) 的 max_chunks_per_doc 是块数，不能作为模型token容量；meta.tokens为观测用量，不是结算凭证。

实际 `.venv/Scripts/python.exe -B scripts/verify_p4_rag.py --execute-owner-authorized` 退出 **1**。不可变一次性收据 `var/reports/p4-r1/live/live-receipt.json`：

- 已消耗 **1次尝试 / 227 UTF-8请求正文字节**；正文SHA-256 `5b3c083dc324871e201ff270013015a99a3f08974ad43a68aeba9f0caf8fdbd0`。发送前计数，不因未知响应重置额度。
- Provider `siliconflow`，model `BAAI/bge-reranker-v2-m3`，role `rerank`，status `error`，错误类型 `ProviderUnavailable`。
- 适配器观测延迟约627.57ms；未取得成功响应或实际token，planned_tokens/usage_actual为空，settlement **UNKNOWN**。不推断免费结算。
- 当前脱敏证据不能区分底层传输、响应解析或返回校验原因，底层原因 **UNKNOWN**。不能将其写成已证实DNS/TLS/代理问题。
- 首次账户权限探测未通过；之后计划的真实Rerank+PG合成链路 **NOT RUN**。无重试、第二次请求或其他模型请求。

## 7. Clean-slate / 旧资产保护

唯一使用数据库：`rag_clean_dev_20261008t072656z_352f705b`，OID `21278`，cluster system identifier `7691227493754040358`，端口 `25438`，migration `0017_embedding_profile_identity`。Storage `D:/RAG-CleanSlate/cs0_20261008t072656z_352f705b/storage`。原身份/OID/cluster/Migration检查保留；没有应用迁移、改Schema、重建业务索引或触碰旧业务库。

live收据的23张业务表前后均0。model_calls从3变4，新增本轮rerank审计行 `b70fa255-f62a-4fab-a133-b589c9d218b6`，reservation_state `unknown`，保守占用1 microunit；它是本地未知费用占位，不是供应商实际结算。最终只读查询将原3条Preflight审计的ID/provider/model/purpose/state/reservation逐项与历史收据核对，完全一致。证据 `database-final-read-only.json`。

原517份追踪文件中仅授权10份被修改，其余507份SHA-256一致；P3 Chunking、冻结PDF、model/profile配置和Migration均包含在不变集合。此结论限于已哈希的追踪资产；未读取旧业务库，不把它表述为旧库行数实测。旧公开资产不改写，不删除任何文件或数据库。

## 8. 修改清单、源码身份与交付

修改10份追踪文件：

1. backend/app/adapters/models/cloud.py
2. backend/app/adapters/postgres/knowledge_repository.py
3. backend/app/application/context_builder.py
4. backend/app/application/knowledge_gateway.py
5. backend/app/application/retrieval.py
6. backend/app/bootstrap.py
7. backend/app/domain/models.py
8. backend/app/ports/retrieval.py
9. backend/tests/test_context_neighbor_repository.py
10. backend/tests/test_retrieval_routing.py

新增5份项目文件：retrieval_merge.py、test_p4_rag_pipeline.py、test_p4_rag_postgres.py、scripts/verify_p4_rag.py及本报告，完整路径见final-manifest.json。没有暂存其他改动；没有commit。

最终 source snapshot 与 broad-final2-tested-source-snapshot.json 所记录的全部源码/测试/入口字节一致；报告是测试后整理的交付文档，不声称其经过pytest。targeted-final3覆盖最终业务代码，最终广泛回归同时覆盖最后入口断言。全部15份项目文件的长度/SHA-256、证据文件哈希、白名单及默认diff检查结果在 `final-manifest.json` / `git-checks.json`。完整实际diff含未追踪新增文件，保存为 `actual.diff`，不是仅展示git tracked diff。

历史准备阻断报告原件保存在 `var/reports/p4-r1/prior-blocked-report.md`，并完整附在本文下方。早期失败、中间轮次及旧Preflight/P2/P3证据没有删除或改成PASS。

## 9. 遗留与最小下一步

Owner/协调者复核实现、失败基线和真实请求收据；补证底层请求失败原因及账户可用性后，另行决定如何恢复有界真实验证。当前异常停止规则生效，不能因尚有2次名义配额而自动继续或重置收据。真实Rerank质量、真实Rerank+PG端到端与实际结算证据缺失，本轮不授予P4_PASS。

P3真实模型容量等历史限制按Preflight已有边界保留，本轮未扩大为全模型保证；依赖升级影响未独立验证；4 Caption NOT RUN；广泛基线45项失败/错误均保留。无自动延期批准，无P5开发。本轮完成报告和证据后停止。

---

# 历史附件：原准备阻断报告（保留原文，以下不是当前状态）

# P4 — WeKnora-style RAG 主链执行记录

## 本轮结果

**P4_BLOCKED**。任务 id/revision/attempt：`P4 / r1 / preparation-1`。固定上游 P4 源码无法读取，无法满足先核对固定实现再迁移合并语义的要求。本轮仅作准备核对，没有修改生产实现、测试、模型配置、P3 参数、冻结 PDF、Migration 或业务数据，没有形成 P4 commit。

执行模式：`MANUALLY_SUPERVISED_TRIAL`；本轮单协调者、无新 Worker。实际服务模型/effort 无控制面证明，记 `UNKNOWN`。历史 P0 动态路由要求不视作实际模型身份。未改任何全局模型、权限或网络保护配置。

**未实现的命令不得报告通过。** 本文的静态发现、真实 PostgreSQL 只读核对与未运行测试分别记录。

## 授权与基线

- 已读取 `C:/Users/22088/AppData/Local/Temp/01_GLOBAL_EXECUTION_CONTRACT.md` 与 `05_P4_RAG.md`，全局合同最初缺失的问题已解决。
- 本轮开始及停止前 HEAD：`4ffdaeafc4ffbdd6d506d46f17a5f90872e04d6e`。开始时 `git status --short` 无输出，工作树干净。
- 用户另行批准 SiliconFlow Rerank：最多 3 次 HTTP 请求、无重试、仅合成问题/候选、累计请求正文 UTF-8 不超过 20,000 字节，先核免费状态，首次小请求核账户权限，异常停止。不复用 Preflight 额度。
- 本轮实际模型请求 **0 次**，请求正文累计 **0 字节**，账户权限 `NOT RUN`；未使用或输出真实 Key。GitHub 文档请求不计模型请求，也不证明模型账户权限。
- 下载目标限定 D 盘。本轮固定源码下载在首个请求即失败，未成功保存任何上游源码；没有下载到 C 盘。

## Clean-slate 身份核对

实际执行 `.venv/Scripts/python.exe -B scripts/with_clean_slate.py --mode check`，退出码 **0**，输出 `PASS / REAL_POSTGRESQL_READ_ONLY`：

| 项目 | 实际结果 |
| --- | --- |
| database | `rag_clean_dev_20261008t072656z_352f705b` |
| OID | `21278` |
| system identifier | `7691227493754040358` |
| port | `25438` |
| Alembic revision | `0017_embedding_profile_identity` |
| Storage | `D:\RAG-CleanSlate\cs0_20261008t072656z_352f705b\storage` |

该检查输出的 `model_calls: 0` 表示检查本身没有调用模型，不能解释为数据库没有历史调用记录。Preflight 的历史用量与失败证据未清理、未重置。本轮未执行迁移、写入合成业务数据或重建索引，也未读取旧业务库。

Python 启动同时输出 `Failed to find real location of D:\Drivers\python\python.exe`，但上述进程返回 0 并完成真实数据库核对。保留该环境告警，不将其描述为已修复。

## 固定上游阻断证据

唯一参考仍为 **Tencent/WeKnora v0.8.2 / `3e8b0bfc80b845b2d4b2ed683994748741450a97`**，没有切换版本。

所需文件：

1. `internal/application/service/chat_pipeline/merge.go`
2. `internal/application/service/chat_pipeline/merge_expand.go`
3. `internal/application/service/chat_pipeline/rerank.go`
4. `internal/application/service/chat_pipeline/filter_top_k.go`
5. `internal/searchutil/chunkmerge.go`

证据与局限：

- 本机 `E:/WeKnora` 的固定提交 Git 对象读取此前返回 `Permission denied`；未重试该拒绝路径、未修改权限。
- 网页提取工具读取固定 GitHub/blob 与 raw 页面返回 `DisabledError`，不是成功读取源码。
- 本轮读取已有 `var/reports/p3/upstream-source-manifest.json`、`upstream-relevant-paths.json` 并枚举其 `.go` 缓存。缓存是 P3 chunker/ingestion 源码，不含上述五个 P4 文件；路径索引不是源码内容证据。
- 官方固定 raw URL 只读下载脚本，首个 `merge.go` 请求失败，进程退出 **1**，没有成功下载。目标目录为 `D:/RAG-ModelAssets/weknora/3e8b0bfc80b845b2d4b2ed683994748741450a97/p4`。
- 同一 URL 的只读错误诊断返回 `URLError`，底层 `SSLEOFError`、errno `8`、`[SSL: UNEXPECTED_EOF_WHILE_READING]`。未禁用 TLS 校验、未换上游版本。诊断 Python 返回 1；该复合 shell 后续执行 Git 检查，使 shell 总退出码为 0，不能把总退出码误记为下载成功。
- 旧 P0 报告记录了固定源码符号和链接，但不是本轮完整源码缓存，不能据此虚构重叠合并细节、参数或生产行为。

最小补证：恢复对上述固定 GitHub 文件的正常 TLS 读取，或提供这五份固定提交源码及可核对来源/哈希的本地包。不需要重新审计 WeKnora 全仓，不需要放开旧业务目录或模型调用权限。

## 当前项目事实与未实施范围

以下为当前基线静态源码证据，**不是本轮功能测试 PASS**：

| 能力 | 当前证据 | 状态 / P4 待办 |
| --- | --- | --- |
| Hybrid / weighted RRF | `backend/app/application/retrieval.py:252`，`rrf_fuse` | 保留现有实现，不重写，不视为语义置信度 |
| Keyword | `PostgresKnowledgeRepository.keyword_candidates` | 保留当前 keyword backend，不称 BM25 |
| Rerank Adapter | `backend/app/adapters/models/cloud.py:260`，`SiliconFlowRerank.rank` | Adapter 已存在；本轮未接生产链 |
| 组合根 | `backend/app/bootstrap.py:85`、`:156` | `rerank_enabled` 仍被 unavailable 检查拒绝；构造 HybridRetriever 未传 ranker |
| 实验 ranker | `backend/app/application/retrieval.py:256`–`:272` | 存在可选 ranker 分支；完整的外发门、非法结果 fallback 与生产接线需本阶段验证 |
| Top-K | `backend/app/application/retrieval.py:274` | 当前直接截取 full_fused，未接 P4 parent/merge |
| Parent resolve | `backend/app/ports/retrieval.py`、`PostgresKnowledgeRepository` | 生产检索至 ContextBuilder 的 Parent 链路未完成；本轮未新增实现 |
| 历史 readback | `backend/app/adapters/postgres/knowledge_repository.py:1054`，`get_chunk` | 保留历史回读语义；未来 scoped Parent 读取不能简单改变历史接口 |
| Context Builder | `backend/app/application/context_builder.py:13`、`:70`，`select/build` | 保留 whole-chunk 与原始引用；尚未接 Parent context |
| Stats | `RetrievalResult` / `EvidenceBundle` | 本轮未新增 P4 只读特征，也未实施 P5 Router |

当前没有完成的 P4 生产调用链、Merge/Parent citation mapping 或新增 stats 合同。下一轮取得固定源码后再实现及验证，不能用本文准备记录替代交付。

## 免费状态与实际外部验证

本轮官方 [SiliconFlow 价格页](https://siliconflow.cn/pricing) 明确将 `BAAI/bge-reranker-v2-m3` 标为免费，将 `Pro/BAAI/bge-reranker-v2-m3` 单独列为收费模型。该证据仅是公开价格，不等于账户权限、实际结算或所有调用占用为零。

已读取官方 [Rerank API 文档](https://docs.siliconflow.cn/docs/api/rerank-post)，但未执行账户请求；输入上限/截断参数尚未形成经过源码与测试确认的 P4 合同。没有把 Embedding tokenizer 容量推断成 Rerank 模型容量，没有修改冻结模型注册信息。

## 命令与测试状态

| 实际操作 | 退出码 / 结果 |
| --- | --- |
| `git rev-parse HEAD` | 0；指定基线一致 |
| `git status --short` / `git status --porcelain=v1 --untracked-files=all`（报告生成前） | 0；无输出 |
| `Get-Content -LiteralPath .../01_GLOBAL_EXECUTION_CONTRACT.md` / `05_P4_RAG.md` | 0；两份文件已读取 |
| `Get-Content -LiteralPath var/reports/p3/upstream-source-manifest.json` / `upstream-relevant-paths.json` | 0；已有缓存清点 |
| `rg --files var/reports/p3 -g '*.go'` | 0；无 P4 merge/rerank 文件 |
| 固定 raw URL 下载 Python stdin 脚本 | 1；URLError，首文件失败 |
| 同一 raw URL 错误诊断 Python stdin 脚本 | Python 1；后续 Git 检查导致外层 shell 0，底层 TLS EOF |
| `.venv/Scripts/python.exe -B scripts/with_clean_slate.py --mode check` | 0；真实 PG 只读身份 PASS |
| `git diff --check`（报告生成前） | 0 |
| P4 新增 / 受影响定向 / 广泛离线回归 | **NOT RUN**；尚未实施代码，不创建虚假 JUnit |
| 真实 Rerank / 合成 PG 端到端 / 账户权限 | **NOT RUN** |
| P4 提交 / Push / Tag / P5 | **NOT RUN** |

另有一次 PowerShell 中向 `rg` 传入 `docs/audits/p3*`、`p4*` 字面 glob，返回 os error 123、退出 1；随后改用目录及 `-g 'p3*.md' -g 'p4*.md'` 完成文档检索。该检索错误不是测试失败，不改写旧失败基线。

本轮未重跑历史测试，未把此前 29 项失败、4 项 Caption NOT RUN 或旧 PASS 转成当前结果。保留限制：**依赖升级影响未独立验证**。

## 最终证据与停止条件

机器证据保存在忽略目录 `var/reports/p4/preparation-blocked.json`，包括当前 HEAD、最终 Git 状态、关键文件 SHA-256、合同 SHA-256、实际命令/结果和阻断原因；其哈希不自引用。仅本报告为预期新增追踪候选，不暂存、不提交。

最终 `git diff --check` 和 `git diff --cached --check` 均退出 **0**；暂存区为空。对未追踪报告另执行 `git diff --no-index --check -- NUL docs/audits/p4-weknora-rag-report.md`，退出 **1**，没有空白错误输出，只有 LF 将按已有 Git 属性转换为 CRLF 的告警；该返回值不伪称为 0。最终 Git 状态仅 `?? docs/audits/p4-weknora-rag-report.md`，已有追踪文件没有改动。

本轮结论是证据前置阻断，不是 RAG 行为验收失败，也不是负责人批准延期。取得固定源码后从现有基线继续 P4，不重做 P2/P3，不扩大真实 API 额度，不自动进入 P5。
