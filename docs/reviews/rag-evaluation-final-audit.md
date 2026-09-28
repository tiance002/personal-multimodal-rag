# RAG 评测平台修复后验收复核

日期：2026-09-28。**结果：PASS（本 Goal 的核心评测可信度与真实云地闭环）；RAG 质量实验为 partial。** 本报告是实现方基于真实执行证据的复核，未冒称第三方独立复审或项目负责人里程碑批准。原独立审查 FAIL 全文保留于 `2026-09-27-rag-evaluation-audit-fail.md`；其发现促成本轮修复，不能从历史记录删除。

## 1. 目标与改动

当前工作树 `codex/eval-center`，真实评测及 ECS 运行提交 `f15aaeb374e32d7955429784b4d9dd50317608a5`。主要提交 c4ee364 实现协议/运行器，f15aaeb 修正真实运行暴露的明确文本拒答漏计；最后的报告提交只修改文档，不替换运行版本。未创建 Release Tag，未开展下一轮参数优化。

| 问题 | 修复前 → 修复后 / 文件 | 状态与证据 |
|---|---|---|
| P0-1 参数真实性 | 声明可独立改 top_k → 从实际 retriever/store/context 实例采样并对齐运行；生产切块与实际索引逐块复核，模型 digest、源码提交、原始来源哈希、索引/向量哈希、实际行数绑定。`runtime.py`、`isolated_index.py`、`runner.py`、`retrieval.py` | PASS；真实 A/B 切块不同，预检伪造 worker chunk_size 拒绝；top_k=9 即使重算 config_hash 仍被 ECS effective_config_mismatch 拒绝 |
| P0-2 汇总与同 ID | 接收声明值 → ECS 重建逐题和聚合/计数；保留原 SQLite 表，附加 v2 验证侧表，旧数据仅诊断。`contracts_v2.py`、`verification.py`、`store.py` | PASS；全部66题重算；summary/case/sample/config/manifest/deployment/private-statistics 及有效同ID冲突均拒绝；一致包 unchanged |
| P0-3 Gold 映射 | 易变 chunk ID → 固定 document/source_SHA/normalized-range/page/asset/sheet/cell/coordinate_space；源区间联合覆盖80%阈值。`gold.py`、`source_metrics.py`、`dataset.py` | PASS；拆分、多Gold、overlap、跨文档、PDF/OCR、sheet、版本、无答案、旧fixture转换定向单元；真实33题同Gold跨两索引 |
| P1 核心指标 | 缺失或污染无答案分母 → 四阶段完整rank指标与独立源覆盖，明确有效/不可用样本数。`metrics.py`、`source_metrics.py`、协议文档 | PASS；手算公式及空结果/重复/不足K/null回归；真实阶段统计由 ECS 重算 |
| P1 实际用量/延迟 | 适配器返回字符串时丢失统计 → ContextVar作用域记录 query/answer/embedding/error/retry usage，不变更业务返回类型；独立 estimated 上下文；阶段计时及type7分位数。`usage.py`、`ollama.py`、`telemetry.py` | PASS；真实Qwen/BGE预检 + 正式16次回答；本次q0无查询扩写、无实际重试，相关使用量 unavailable；错误/重试路径为确定性回归 |
| P1 生成/去重 | 仅外部数值 → 要点字面匹配、真实引用回读、业务门禁或首段明确文本拒答、同文档同版本精确重复和估算Token节省。`quality.py`、`runner.py` | PASS；年份/金额/版本误合并保护；首轮文本拒答漏计发现后修正、全量重跑。语义Judge始终 NOT_EVALUATED |
| P1 版本/部署/UI | 未提交代码与manifest不对应 → clean Git HEAD门禁，部署闭包、CODE_SHA/runtime.env，CLI导入及health绑定SHA，在线备份、重启/readiness、相对文件摘要。`deploy_eval_center.ps1`、`upload_eval_bundle.ps1`、install.sh/service、Dashboard | PASS；本地/ECS13个源码/UI文件逐一SHA256一致，重复部署同发布目录，实际浏览器数字/诊断禁选核对 |

## 2. 实际执行与真实 A/B

数据集 `trust-docs-v1`：两份预存项目设计/README源文档的冻结快照，33题（30可回答、3无答案）；问题及来源锚点在实验前固定，Agent来源核查/引用定位通过，human_review NOT RUN。该数据集支持当前项目资料评估，不声称覆盖所有领域或经过人工盲审。旧6题 smoke 保留。

两组独立 PostgreSQL 数据库 `rag_eval_trust_0ccc68d6f380_a` / `_b`，真实 Alembic 至0013，pgvector0.8.6，真实 Worker重新切分和 BGE生成1024维Embedding；不连接日常KB。A1200/120：2文档、33块、33向量；B700/70：2文档、53块、53向量。共同 top_k5、candidate_k32、RRF60、context8000字符、q0规则查询。本次各33次检索、8次真实Quick/Qwen回答、25题仅准备上下文；无真正检索/模型重试。

- A：`58e770ff-44d1-45b9-b5a3-61934f0f720a`，index `f99f6dd82123b5ee1d3a5dffde5df69ecd0d8feb8ac260076567b6803bb68f4d`。
- B：`e8485bb5-27d5-48ae-9182-a67bcbe0cb67`，index `176e6dbd1da229dafd702624e367dfba05896e97d76d85b0d5002e08d7cd75d4`。
- Chat `qwen3.5:4b` digest `2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd`；Embedding `bge-m3:latest` digest `7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab`。

| 指标 | A | B |
|---|---:|---:|
| 融合 Recall@5 | 0.700000 | 0.616667 |
| 融合 Precision@5 | 0.153333 | 0.146667 |
| 融合 F1@5 | 0.249206 | 0.233333 |
| 融合 Hit@5 | 0.733333 | 0.700000 |
| 融合 MRR@5 | 0.392778 | 0.359444 |
| 融合 MAP@5 | 0.378889 | 0.313056 |
| 融合 nDCG@5 | 0.461267 | 0.398689 |
| 最终上下文来源召回 | 0.716667 | 0.650000 |
| 最终证据区间覆盖 | 0.715574 | 0.668096 |
| 答案要点覆盖 | 0.733333 | 0.700000 |
| 引用回读率 | 1.000000 | 1.000000 |
| 明确拒答率 | 1.000000 | 1.000000 |
| 实际回答输入 Token / 题均值 | 1407.625000 | 886.375000 |
| 实际回答输出 Token / 题均值 | 168.250000 | 128.250000 |
| 实际业务 Token / 已生成题均值 | 1575.875000 | 1014.625000 |
| 检索 p50(ms) | 96.028200 | 54.967800 |
| 检索 p95(ms) | 121.484880 | 66.214620 |
| 检索 p99(ms) | 136.219720 | 68.299188 |
| 生成 p50(ms) | 3428.150000 | 1950.400000 |
| 生成 p95(ms) | 6772.220000 | 3713.970000 |
| 生成 p99(ms) | 7259.644000 | 3803.514000 |
| 端到端 p95(ms) | 5057.044660 | 3563.375040 |
| 估算精确重复 Token 节省比例 | 0.000000 | 0.000000 |

检索/源覆盖质量分母30，拒答分母3，答案要点分母5，引用/回答Token分母8。回答实际总Token A12607 / B8117，表中业务Token为8题宏平均，不是累计。端到端分位数含33题、其中25题无生成；生成分位数仅8题。不能把二者混为纯生成时延。暖机和摄取usage单独保存，不纳入逐题业务均值。Context/Evidence用 cl100k_base 估算，明确 approximate for Qwen，绝不是 prompt_eval_count。Query/ Judge使用量 unavailable；Judge未调用。实际8个sent上下文、25个prepared上下文逐题分开标记，表中最终源召回包括prepared；不能称33题都送入模型。

结果包含真实召回失败、跨文档缺半证据以及答案要点缺失，所以 status=partial 是诚实质量结果。没有改Gold或下降阈值。当前单次顺序A→B，模型输出及并列排序存在运行差异；不宣称B显著更快或当前参数已优化。首轮c4ee364的两条数据因拒答协议漏计已备份并标 `invalid_refusal_protocol`，只在诊断视图，原payload/digest不变。

## 3. 公式、真实与确定性证据

rank单位为chunk，Recall=独立相关命中/整个范围索引相关chunk数；Precision=命中/K；F1=2PR/(P+R)；Hit=是否命中；MRR=首命中倒数；AP@K=sum(各命中处Precision)/min(K,相关总数)，MAP取题均值；nDCG用(2^grade-1)/log2(rank+1)归一化。keyword/vector使用candidate_k32，fused/context排名使用top_k5。无答案rank=null独立统计；少于K仍固定K；重复ID占位置无额外gain。

手算用 `[x,a,y,b]`、a等级2/b等级1、K5：Recall1、Precision0.4、F1=4/7、Hit1、MRR0.5、AP0.5，nDCG=(3/log2(3)+1/log2(5))/(3+1/log2(3))。type7时延[10,30]：p50=20/p95=29/p99=29.8。对应定向断言实际运行通过。跨切分chunk Recall分母会改变，核心来源Recall采用稳定Gold每项联合区间一次计分；一个Gold分多块可联合达到80%，同一overlap不重复计来源证据。完整实际上下文覆盖与rank@K指标分别保存。

云端只见匿名ID、相关等级、相对区间、rank、数值计数与受限元数据。其独立重算证明统计一致性；没有原文时无法独立证明人工标注及提交的匿名轨迹是否真实，不能把哈希称为远程执行证明。真实执行绑定证据在本地保留。

## 4. 执行命令 / 退出码

命令中的 Python 指向本机既有 `.venv`，连接文件和 SSH 参数仅从仓库外运行时读取；仓库报告不保存密钥路径/内容、URL或密码。**未实现的命令不得报告通过。**

```powershell
python -m pytest -q eval_center/tests backend/tests/test_ollama_usage.py backend/tests/test_retrieval_execution_record.py backend/tests/test_quality_eval_schema.py backend/tests/test_hybrid_retrieval.py backend/tests/test_retrieval_contract.py backend/tests/test_langchain_quick_chain.py backend/tests/test_quick_evidence_flow.py backend/tests/test_quick_chain_quality.py backend/tests/test_quick_chain_budget.py backend/tests/test_context_and_locators.py backend/tests/test_citation_resolution.py backend/tests/test_embedding_profile_isolation.py backend/tests/test_egress_matrix.py -p no:cacheprovider --tb=short
python -m eval_center.runner --connection-file $evalConnectionFile
./scripts/deploy_eval_center.ps1 -HostName $approvedHost -User $sshUser -IdentityFile $identityPath -PythonExecutable $pythonPath -Apply
./scripts/upload_eval_bundle.ps1 -Bundle var/trust-acceptance/0ccc68d6f380/A/bundle.json -HostName $approvedHost -User $sshUser -IdentityFile $identityPath -PythonExecutable $pythonPath -Apply
./scripts/upload_eval_bundle.ps1 -Bundle var/trust-acceptance/0ccc68d6f380/B/bundle.json -HostName $approvedHost -User $sshUser -IdentityFile $identityPath -PythonExecutable $pythonPath -Apply
```

| 实际命令/操作 | 退出码 / 证据 |
|---|---|
| 上述完整评测模块+关联业务回归 | 0；157passed，6既有弃用警告；此前154passed +部署/外发定向18passed均0 |
| PowerShell deploy/upload Parser.ParseFile；runner --help；git diff --check | 0；语法/入口/差异检查 |
| 两次正式 runner（修复前/后） | 0/0；最终 `REAL_AB_EXECUTED`，最终目录 `var/trust-acceptance/0ccc68d6f380`；不是fixture模拟 |
| clean源码 Git commit | 0；c4ee364、f15aaeb；真实run启动/结束均检查HEAD与干净状态 |
| 部署（c4ee364、f15aaeb、f15aaeb重复） | 0/0/0；相对摘要相同f15重复release不变，health实际SHA匹配，active，仅loopback8787 |
| 最终两组 upload helper | 0/0；imported/imported，仅脱敏统计 |
| 远端Python online SQLite backup/integrity及恢复副本检查 | 0；部署前2/4行备份均ok，当前恢复副本6行，全部ID/digest相同；这是实际数据恢复副本，未冒称全实例恢复演练 |
| 云端 `python3 -` 执行 `var/trust-acceptance/cloud_probe.py` | 首次1：审查脚本重新排序cases使严格内容digest冲突；用原始匿名包修正后0；66题全部重算，summary/case/sample/config/manifest/deployment/private_statistics拒绝，同ID有效异内容冲突，同内容unchanged，原6行摘要不变 |
| 本地Python逐字段追踪API | 0；`cloud-record-trace.json`，66题metrics/status/statistics及runtime/config/errors/counts/聚合全相同 |
| 远端源码摘要 + 本地 Get-FileHash | 初次1：探针自产pycache不属于源码闭包，按源码/UI13文件比较后0；源码字节完全一致，未把pycache当源码 |
| 实际 Cua 浏览器 | 真实UI，非pytest/mock；两条verified各33题、MRR .359444/.392778，比较召回 .65/.716667、refusal1/1、业务1014.625/1575.875、query unavailable有效0；诊断6条且4条异常/旧记录选择框disabled；鼠标自动化未改变checkbox，键盘Space/Enter交互成功 |

真实服务证据：`var/trust-acceptance/cloud-probe.json`、`cloud-record-trace.json`、`deployed-files.json`；两组各manifest/config/metrics/report/cases/errors/bundle/summary及本地完整资料。原预检仍在 `var/reports/trust-*-preflight.json`，明确非正式实验。

## 5. ECS、备份与风险遗留

ECS code `f15aaeb374e32d7955429784b4d9dd50317608a5`；release `/srv/rag-eval/releases/673449235bed8e693550b5774b71b2b004e5dd75d40902679e18cbda8a0a6a99`。主数据库v2，6行：2正式 +2首轮协议失效诊断 +2原smoke未验证。部署备份位于 `/srv/rag-eval/backups/deploy-20260928T033151Z-623693/`、`deploy-20260928T033528Z-624513/`、重复部署 `deploy-20260928T034152Z-625341/`；另有拒答quarantine备份和恢复校验副本。旧学习规划备份/清理已由此前记录复核，旧DB/证书保留；本轮不重复删除旧资源。无公网RAG产品部署、无安全组修改、无凭据入库、无私人正文外传；Langfuse与云调用关闭。

剩余P2/可选技术债（本Goal授权记录后停止扩展，未自称负责人批准发布/延期）：

- 本地数据域较小、单次顺序运行；统计显著性/随机化重复、其他真实PDF/OCR/表格语料属于后续评估。当前定位映射这些类别已有单元覆盖，不冒称真实PDF/OCR摄取A/B已运行。
- 首段字面拒答/答案要点匹配不是语义正确性；引用回读率也不是引用支持率。Faithfulness/Relevance/Factual Correctness、语义近重复/已标注误合并率、LLM Judge/Ragas **NOT_EVALUATED**，不能外推其评分。
- 正式基线q0，因此query扩写与真实错误/重试场景 NOT RUN；使用量采集相关路径通过确定性测试和独立实际query预检（46/12tokens），不能伪称正式A/B发生重试。
- 备份仅同盘；完整业务发布回归、全实例灾难恢复、网络抓包 NOT RUN；当前完成评测库在线备份与恢复副本验证。
- 重启readiness首次可能产生空JSON解析噪声，15次重试内成功且检查真实SHA；P2日志体验，不影响最终健康/数据完整性。

## 6. 版本与下一步

**本Goal验收核心条件满足，可开展有边界的固定语料RAG参数优化比较。** 当前真实质量未达到全题成功，结果如实partial；评测平台PASS不代表RAG产品质量或M4.5发布通过。保留隔离数据库/本地报告/模型以供重现；SSH Dashboard隧道仅本机。下一任务由项目负责人选择，本Goal不自动优化参数或打Tag。

### 最后连接复查

最终文档提交d1cb6c8后，本机旧SSH隧道遭Connection reset，末次本地HTTP复查退出1；直接新SSH访问ECS health退出0，实际SHA仍f15aaeb。重新以隐藏后台进程建立同一本机18788隧道，添加ServerAliveInterval30/ServerAliveCountMax3，重新请求health/official/diagnostic退出0：FINAL_LIVE_PASS，2正式/6诊断。此次是本机隧道断连，不是服务数据或部署故障；不隐去失败尝试。隧道无需公网端口或防火墙变更。
