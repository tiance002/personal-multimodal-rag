# 公开 RAG 评测暂停后收口分析

生成时间：2026-09-28（Asia/Shanghai）。分析只读取本机已有 run 文件；未执行新检索、生成、Embedding、下载、建索引、ECS 同步或测试。

## 1. 执行状态与结果标签

| 数据集 / 阶段 | 实际完成内容 | 状态 |
|---|---|---|
| SciFact development | standard 809 qid；baseline、candidate-k-16、candidate-k-64、rrf-30、top-k-3/8/10 七变体 | `COMPLETED` |
| SciFact locked holdout | rrf-30，300 qid，一次 | `COMPLETED` |
| SciFact QA / Judge | 未生成答案；Judge 与 Citation Support 未评测 | `NOT_RUN` / `NOT_EVALUATED` |
| SciFact 400/40 development | 1,530 succeeded、3,652 queued、1 stale lease、8,256 chunks/embeddings；无完整结果文件或 index hash | `INTERRUPTED`，不是测试失败，不可聚合 |
| MIRACL-ZH 固定 6,000 passage pool development | 328 qid，七变体；排除了缺少完整已知正例的 query | `COMPLETED` |
| MIRACL-ZH locked holdout | candidate-k-16，99 qid，一次 | `COMPLETED` |
| MIRACL QA / Judge | 未生成答案；Judge 与 Citation Support 未评测 | `NOT_RUN` / `NOT_EVALUATED` |
| LongBench-ZH development | 400 文档，320 qid，七变体；qrels 是 paired-context 诊断 | `COMPLETED` |
| LongBench-ZH locked holdout | candidate-k-64，80 qid，一次；仅 paired-context 诊断 | `COMPLETED` |
| LongBench QA smoke | baseline candidate-k-32 与 candidate-k-64 各 4 次真实 Qwen 生成，4 个相同 qid | `COMPLETED`，仅 smoke |
| LongBench 完整 QA / Judge | 320 题生成未运行；Judge、Faithfulness、Citation Support 未评测 | `NOT_RUN` / `NOT_EVALUATED` |
| SciFact 700/70、900/90、1500/150；其他 chunk 档；context 4k/12k；合成重复诊断 | 无结果文件 | `NOT_RUN`；near-duplicate false-merge `NOT_AVAILABLE` |

用户决定停止后续 14–18 小时网格。暂停报告记载 Goal 用时 **6:25:48**；当前 interruption 单元运行 **20:05.4** 后被 Ctrl+C 正常中断。中断报告中 Docker/PostgreSQL 保持运行，partial DB 外部连接为 0。当前只读复查未发现 benchmark runner 进程，`ollama ps` 为空；没有对服务做停止或清理。

已完成标准与 QA smoke run 的 manifest 用时：

| Dataset / run | 完成样本 | 用时 |
|---|---:|---:|
| SciFact development optimize | 809 | 60.96 分钟 |
| SciFact locked holdout | 300 | 31.31 分钟 |
| MIRACL-ZH development optimize | 328 | 30.18 分钟 |
| MIRACL-ZH locked holdout | 99 | 29.85 分钟 |
| LongBench-ZH development optimize | 320 | 44.09 分钟 |
| LongBench-ZH locked holdout | 80 | 31.68 分钟 |
| LongBench-ZH baseline QA smoke | 4 生成 / 12 检索 | 3.51 分钟 |
| LongBench-ZH candidate-k-64 QA smoke | 4 生成 / 12 检索 | 3.45 分钟 |
| SciFact 400/40 partial unit | 1,530 个 ingestion job 成功 | 20.09 分钟后中断 |

单个完整 run 用时来自 manifest；Goal 总耗时为暂停时记录的 6:25:48。未执行网格还剩用户估算的约 14–18 小时，本次没有重估或启动。

## 2. 产物、哈希和完整性校验

### 工作树与共用身份

- 实际评测工作树：`C:\Users\22088\.codex\worktrees\eval-center\RAG quention`
- 分支：`codex/eval-center`；评测 Git SHA：`241d2c7ebdc4911e92a18bdc1e48307d85c87503`
- chat：`qwen3.5:4b` digest `2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd`
- embedding：`bge-m3:latest` digest `7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab`

### 三个正式数据集的身份

| 数据集 | dataset hash | 标准索引 hash | locked effective_config hash | 索引规模（doc / chunk / embedding） |
|---|---|---|---|---:|
| SciFact | `8608aa67e02a307745506d92316ebd0d1b2079226a576446c2bb5943e064f59f` | `f6c8b191834638dfa8315adc158b1875ac69c0bef5ba2baac598734976aadb93` | `33f92e99cb52c9d8f2cecec9a4aa647aacf497a8911a7e92a4597be0e8a4d60d` | 5,183 / 9,256 / 9,256 |
| MIRACL-ZH | `105dde269ea99ddee8ad0ebf8649631e88c3710be9ad2b54b4ced59e4a17ad29` | `629b25a5a14ea2ab960fa874c3b16e8cc77a61d0bb65e0e0847ce3c04d473c82` | `886d82e90f67bdffafa99785955151fa3eec7030ec93dcdd5bb951950dde0ff6` | 6,000 / 6,001 / 6,001 |
| LongBench-ZH | `d5f18bf83d652664d6778d1fab2eba0c1d6ef7781cc4c106e96217eca1cac0c1` | `0d41031e54185d2ebc026cdeb62a099427f35a09b0b2d2034a5ce12e4dd3c179` | `c98b34c11068d8bd6ddeac3396044dd6670a19bfdb5763bc27206b7980578268` | 400 / 5,003 / 5,003 |

锁定配置分别为 SciFact rrf-30、MIRACL candidate-k-16、LongBench candidate-k-64；全部 chunk size/overlap 为 1200/120，top-k=5，context budget=8,000 characters，RRF K=30 或 60 如配置所示。每个 run 的 manifest 另记录同一模型 digest、对应 index hash 和 Git SHA。

### 校验结论

身份文件 `D:\RAG-Public-Bench\reports\completed-experiment-identities-20260928.json` 中 10 个 completed run 全部核对通过：cases 行数、manifest 样本数与唯一 qid 数一致；manifest/config/dataset/index/model/Git 身份相符；manifest、cases、metrics、report 文件齐全。总共 12 个 manifest source file 的本地 SHA-256 均匹配。

三个 one-time lock 文件各自的 `run_manifest_sha256`、query-set hash、Git SHA、locked split 标记与 candidate config 均匹配实际 run manifest；没有重复使用冻结集或缺失指标证据。所有六个正式 standard run 路径为：

- SciFact development：`D:\RAG-Public-Bench\runs\scifact\development\20260928T060742Z-6f0c61007b`（809/809）
- SciFact locked：`D:\RAG-Public-Bench\runs\scifact\locked_holdout\20260928T083231Z-efc760238e`（300/300）
- MIRACL development：`D:\RAG-Public-Bench\runs\miracl-zh\development\20260928T071014Z-cffac0cd6a`（328/328）
- MIRACL locked：`D:\RAG-Public-Bench\runs\miracl-zh\locked_holdout\20260928T090914Z-7586bc13ce`（99/99）
- LongBench development：`D:\RAG-Public-Bench\runs\longbench-zh\development\20260928T074111Z-442ee43dd3`（320/320）
- LongBench locked：`D:\RAG-Public-Bench\runs\longbench-zh\locked_holdout\20260928T093926Z-3cda58550a`（80/80）

两次 LongBench QA run：

- baseline candidate-k-32：`D:\RAG-Public-Bench\runs\longbench-zh\development\20260928T060258Z-8721f3bc72`
- candidate-k-64：`D:\RAG-Public-Bench\runs\longbench-zh\development\20260928T082622Z-f83949b20b`

中断 run：`D:\RAG-Public-Bench\runs\scifact\development\20260928T102515Z-8505c397eb`；interruption metadata 明确 `aggregation_eligible=false`、`index_hash=NOT_AVAILABLE_PARTIAL_INDEX_NOT_SNAPSHOTTED`，没有完整 manifest/metrics/cases。

### 实际运行时间

| Run | manifest 开始至结束 |
|---|---:|
| SciFact dev 809 | 60.96 min |
| SciFact locked 300 | 31.31 min |
| MIRACL dev 328 | 30.18 min |
| MIRACL locked 99 | 29.85 min |
| LongBench dev 320 | 44.09 min |
| LongBench locked 80 | 31.68 min |
| LongBench QA baseline / K64 | 3.51 / 3.45 min（各 12 条 retrieval case、4 次 generation） |

这些计时是 manifest wall time；Goal 从启动到用户暂停的 wall time 以 interruption report 记载的 6:25:48 为准。

## 3. 开发集检索：模式/阶段对照

下表来自同一配置（baseline top-k=5 / candidate-k=32 / RRF=60）的逐题 `keyword`、`vector`、`hybrid` 排名。runner 明确说明：keyword/vector 是一次生产 hybrid 查询捕获的实际候选排名，hybrid 使用生产融合排名；ContextBuilder 分别处理这些排名。因此这是“阶段排名对照”，不是关闭某个检索组件后分别跑出的独立消融实验。

| 数据集 / qrels 单位 | 排名阶段 | nDCG@10 | Recall@5 | MRR@5 | Context recall@5 |
|---|---|---:|---:|---:|---:|
| SciFact 文档 qrels | keyword | 0.214520 | 0.243304 | 0.179522 | 0.239596 |
| SciFact 文档 qrels | vector | 0.708971 | 0.778513 | 0.673218 | 0.762031 |
| SciFact 文档 qrels | hybrid | 0.579188 | 0.693222 | 0.507808 | 0.687660 |
| MIRACL passage qrels，固定 6k 池 | keyword | 0.330483 | 0.364322 | 0.280793 | 0.364322 |
| MIRACL passage qrels，固定 6k 池 | vector | 0.909327 | 0.965563 | 0.899695 | 0.965563 |
| MIRACL passage qrels，固定 6k 池 | hybrid | 0.644756 | 0.712239 | 0.582215 | 0.712239 |
| LongBench paired-context 诊断 | keyword | 0.598282 | 0.665625 | 0.545677 | 0.640625 |
| LongBench paired-context 诊断 | vector | 0.857019 | 0.925000 | 0.824479 | 0.921875 |
| LongBench paired-context 诊断 | hybrid | 0.847535 | 0.921875 | 0.807031 | 0.918750 |

这些数字不应跨 qrels 口径解读。SciFact 是官方文档 qrels；MIRACL 是固定 pool 内 passage qrels；LongBench 的匹配来源只用于 paired-context 诊断，不能叫作官方检索 benchmark 得分。

## 4. 开发集参数矩阵与 paired 差值

每项是该变体在同一开发集逐题记录上的 macro mean。Latency 是逐题生产检索路径中记录的平均 `retrieval_ms`。top-k 只改变最终 context 选取，因此其文档排名分数相同、Context recall 不同。

| 数据集 | 变体 | nDCG@10 | Recall@5 | MRR@5 | Context recall@5 | 平均 retrieval ms |
|---|---|---:|---:|---:|---:|---:|
| SciFact | baseline t5/c32/r60 | 0.579188 | 0.693222 | 0.507808 | 0.687660 | 378.54 |
| SciFact | candidate-k-16 | 0.575297 | 0.699918 | 0.507973 | 0.695591 | 377.07 |
| SciFact | candidate-k-64 | 0.564267 | 0.663762 | 0.489143 | 0.660260 | 379.15 |
| SciFact | rrf-30 | 0.579893 | 0.692604 | 0.508488 | 0.687042 | 379.37 |
| SciFact | top-k-3 | 0.579188 | 0.693222 | 0.507808 | 0.608344 | 376.66 |
| SciFact | top-k-8 | 0.579188 | 0.693222 | 0.507808 | 0.693222 | 382.96 |
| SciFact | top-k-10 | 0.579188 | 0.693222 | 0.507808 | 0.695694 | 388.05 |
| MIRACL | baseline t5/c32/r60 | 0.644756 | 0.712239 | 0.582215 | 0.712239 | 186.63 |
| MIRACL | candidate-k-16 | 0.690373 | 0.767269 | 0.606301 | 0.767269 | 187.18 |
| MIRACL | candidate-k-64 | 0.631409 | 0.708174 | 0.584299 | 0.708174 | 189.27 |
| MIRACL | rrf-30 | 0.648034 | 0.722401 | 0.587297 | 0.722401 | 188.41 |
| MIRACL | top-k-3 | 0.644756 | 0.712239 | 0.582215 | 0.574935 | 184.25 |
| MIRACL | top-k-8 | 0.644756 | 0.712239 | 0.582215 | 0.712239 | 192.12 |
| MIRACL | top-k-10 | 0.644756 | 0.712239 | 0.582215 | 0.712239 | 196.96 |
| LongBench | baseline t5/c32/r60 | 0.847535 | 0.921875 | 0.807031 | 0.918750 | 356.13 |
| LongBench | candidate-k-16 | 0.843239 | 0.921875 | 0.803490 | 0.918750 | 358.36 |
| LongBench | candidate-k-64 | 0.850180 | 0.928125 | 0.806979 | 0.925000 | 357.77 |
| LongBench | rrf-30 | 0.847126 | 0.921875 | 0.806510 | 0.918750 | 356.98 |
| LongBench | top-k-3 | 0.847535 | 0.921875 | 0.807031 | 0.871875 | 353.57 |
| LongBench | top-k-8 | 0.847535 | 0.921875 | 0.807031 | 0.918750 | 365.52 |
| LongBench | top-k-10 | 0.847535 | 0.921875 | 0.807031 | 0.918750 | 369.86 |

Paired candidate-minus-baseline nDCG@10 differences were independently recomputed from cases using the runner's `public_selection._paired_bootstrap` seed and percentile method; all three matched their saved candidate JSON exactly (2,000 resamples).

| Dataset / selected dev variant | n | Mean Δ nDCG@10 | 95% paired bootstrap CI | Per-qid improve / worsen / tie |
|---|---:|---:|---:|---:|
| SciFact / rrf-30 | 809 | +0.0007047 | [-0.0000049, +0.0018911] | 6 / 2 / 801 |
| MIRACL / candidate-k-16 | 328 | +0.0456175 | [+0.0327808, +0.0591417] | 90 / 34 / 204 |
| LongBench / candidate-k-64 | 320 | +0.0026450 | [-0.0073670, +0.0141491] | 14 / 16 / 290 |

### 已经执行过一次的 locked 结果

| Dataset / 已冻结配置 | qid 数 | nDCG@10 | Recall@5 | MRR@5 | Context recall@5 |
|---|---:|---:|---:|---:|---:|
| SciFact / rrf-30 | 300 | 0.572968 | 0.671889 | 0.512667 | 0.664389 |
| MIRACL / candidate-k-16 | 99 | 0.676084 | 0.756253 | 0.578283 | 0.756253 |
| LongBench / candidate-k-64 | 80 | 0.797858 | 0.887500 | 0.757083 | 0.875000 |

冻结集没有跑 baseline 对照，也不能再用于选择参数。结果只验证各数据集当前已冻结配置的一次表现；MIRACL 只代表指定 6,000 passage pool；LongBench 只代表 paired-context 诊断信号。

## 5. 逐题阶段丢失与来源冗余

“keyword/vector 未召回”表示记录的两个 candidate ranking 均不含任何正 qrel；“融合丢失”表示至少一个候选排名含正 qrel、hybrid document ranking 完全不含；“context 丢失”表示 hybrid ranking 含正例、最终 hybrid context 不含；“partial positive sources”表示一题有多个正 qrel，但 context 未包含全部。后两类可以重叠。该统计检查的是是否命中，不衡量融合后的排序位置下降。

| 数据集 / selected dev 变体 | keyword+vector 都未召回 | 正例被融合排名完全丢弃 | Hybrid 命中但最终 context 丢失 | 多正例 context 未覆盖全部 |
|---|---:|---:|---:|---:|
| SciFact / rrf-30 | 76 / 809 | 0 / 809 | 157 / 809 | 60 queries |
| MIRACL / candidate-k-16 | 0 / 328 | 0 / 328 | 44 / 328 | 71 queries |
| LongBench / candidate-k-64 | 2 / 320 | 0 / 320 | 22 / 320 | 0；每题一个 paired source |

示例里的 `case_id = SHA256(dataset + "|" + qid)[:12]`，保留原 qid 与 gold/hit ID 便于核查，不包含题目或答案文本。

| 数据集 / case_id | gold positive IDs | 命中与 context IDs | 判定 |
|---|---|---|---|
| SciFact `3ddf433b5af3`（qid `0`） | `31715818` | keyword/vector/hybrid/context 均无；context top5=`20101846, 17482507, 5185871, 44674301, 1974176` | 两个召回候选均未命中；需要难例 query / 来源语义分析，不能用一次错误扩大到全检索重构 |
| SciFact `a455148c571f`（qid `1040`） | `16626264, 25254425` | keyword=`25254425`；vector/hybrid 都命中两例；最终 context 一个都没有；context top5=`9451052, 5966635, 2817000, 17518195, 18038955` | 最终 context 选择丢失；多正例缺失；适合单独核查 ContextBuilder 的 budget/排序约束 |
| MIRACL `9b888acb2b90`（qid `855039#0`） | `3448#20` | vector/hybrid 命中；context=`39129#0, 39129#26, 3448#78, 3448#43, 3448#53` | candidate 存在而 context 丢失；同 parent ID 的其他 passage 进入 context |
| MIRACL `53f4239c71ae`（qid `1050376#0`） | `92973#10, 92973#8, 92973#9` | context 五个 passage 均以 `92973#` 开头，只命中 `92973#9` | 同一 parent 下多个 passage 占 5 个 context slot，另两个已标注正例未进入；这是可核查的来源集中现象，尚不能称为近重复文本或 false merge |
| LongBench `671b6178b1e6` | paired source `multifieldqa_zh:198b2a1122828dd5539b9c9baf7b36849ecdeff4f804b2e6` | keyword/vector/hybrid 命中；最终 context 缺失 | ContextBuilder / context 排序后丢失，仍属 paired-source 诊断 |
| LongBench `9d1144c47731` | paired source `multifieldqa_zh:9b68370fb6e7556f1c145af721e968061b39aeb5527880ef` | keyword/vector/hybrid/context 均无 | 记录候选池上双路未召回 |

MIRACL `context.selected_document_ids` 映射的是最终 context 选中的 indexed passage。按 passage ID 的 parent 前缀分组，candidate-k-16 开发集有 **247/328 query** 的最终五个 passage slot 中至少两个来自同一 parent，合计 527 个“超过每 parent 一个”的 slot，平均只有 3.39 个不同 parent / 5 个 passage slot。例 `1050376#0` 的五个 slot 全属 parent `92973`，三个正 qrel 中只带入 `92973#9`。

这支持“passage 来源集中 / evidence 多样性可能不足”这一局部调查方向；它不证明文本近重复、错误去重或用户答案被同一段重复淹没。除最终 context 以外的完整 chunk candidate list 未保存逐 chunk → source 映射，因此更早阶段精确的重复 chunk 占位数量是 `UNKNOWN`。SciFact 和 LongBench 也没有可据此完成的 near-duplicate false-merge 评测。

## 6. LongBench 已有 8 次真实生成

两次 QA run 各包含 12 retrieval cases，但只给相同的 4 个 qid 调用 Qwen；其余 8 条每个 run 都没有答案生成。

| case_id | qid | baseline EM / char-F1 | candidate K64 EM / char-F1 | paired source 在两个 context 中 |
|---|---|---:|---:|---|
| `dcd276eef04b` | `dureader:00f3d80feed0400a66f531091cb6b1650fb32cc0198387f2` | 0 / 0.0431 | 0 / 0.0431 | 是 / 是 |
| `170b30b764b7` | `dureader:0364c6be6b2228f29703557e96c0d1ff0655d1dcc02b10e9` | 0 / 0.2930 | 0 / 0.2930 | 是 / 是 |
| `fb0007ca0e75` | `dureader:052020668a5c87feabb54bcd7bf9d1341cf74bcaf78be89f` | 0 / 0.0000 (`UNSUPPORTED_ANSWER`) | 0 / 0.1058 | 是 / 是 |
| `4a7906707d2a` | `dureader:08b23b8d4989f0f8a23f727c39c0ae8b72b480d951c8befc` | 0 / 0.0695 | 0 / 0.0447 | 是 / 是 |

全部 8 个 normalized EM 都为 0；合并 char-F1 为 0.111509。Baseline 的 4 样本均值为 0.101386，K64 的 4 样本均值为 0.121633；n=4/组、仅 4 对，不能声称生成质量改善。两配置 Qwen `answer` usage 总计 8 calls、27,219 input tokens、3,486 output tokens；BGE 查询 embedding usage 另记 8 calls / 66 input tokens。平均生成延迟 8.19 秒/answer。存在引用的 7 行中 citation readback 为 7/7；Citation Support 和 Judge 全部 `NOT_EVALUATED`。

这 8 个生成结果使用 50 文档 smoke index `f2a5a0fb11bdf9a91cdbfda07bc5f82c5f8d5c01bc8a62b016a9eab85aa695ea`，不是 400 文档开发/locked index `0d41031e...`。paired source 命中只确认来源文档进入 context，不确认答案 span 或语义证据覆盖。

## 7. 重复工作和缓存/复用矩阵

| 项目 | 复用身份 / 可复用边界 | 现场发现与建议（本轮不实施） |
|---|---|---|
| 下载原始公开数据 | 上游 revision / URL、原始文件 SHA-256、许可说明；相同即复用 | 所需本地文件在；12 个 manifest source 文件 SHA 全匹配。停止后未下载任何数据。 |
| Prepared adapter / splits | dataset version、source manifest hash、candidate pool id/hash、split seed、qrels scope | 三套 adapter 与分割齐备；MIRACL pool 固定为 SHARD0 / 6,000；LongBench 为内部 hash split。仅有内容或定义改变时重做。 |
| Corpus chunk / BGE embeddings / index | corpus hash、parser/chunker 版本、chunk size/overlap、embedding model digest + dimension、index schema / index fingerprint | 一个 optimize run 的七个 retrieval 变体共用一次建好的 index；变 top-k / candidate-k / RRF / context budget 本身不改变文档向量。改变 corpus/chunker/embed model 才需要重建。 |
| 跨 run corpus index reuse | 上述相同 index 身份 + 可校验 ready state + 隔离数据库身份 | 当前 `run_public_retrieval` 明确为 fresh local DB，数据库名每次随机生成。SciFact dev/locked、MIRACL dev/locked、LongBench dev/locked 分别具有相同 index hash，却各自重做 9,256、6,001、5,003 corpus embeddings；两组 50-doc LongBench QA smoke 也分别写 464 embeddings。相同配置跨 split 可避免重切分/重 embedding，但需要未来设计只读、强校验的索引复用；本轮不接管或修改数据库。 |
| Query embedding across variants | normalized query hash + embedding model digest + embedding profile/dim；仅内存缓存可避免落盘 query 文本 | usage 实际记录每个 dev query 每个七变体各一次 BGE embedding：SciFact 5,663，MIRACL 2,296，LongBench 2,240，共 10,199 次；相对每 qid 一次多 8,742 次。top-k/candidate-k/RRF 对 query vector 无影响。未来在单次 run 内按 query/model 缓存；不保存用户文本。 |
| QA query embedding across paired configs | qid/query hash + same query embed digest；context 与 retrieval 配置变化不改变 query vector | 4 个相同 qid 在两组 smoke 中生成，query embedding 合计 8 次，即每 qid 两次。候选比较有意重复 QA，但 query vector 可跨配置复用。 |
| Per-query ranking / context results | dataset/query hash + qrels kind + index hash + Git/model digests + effective config + rank unit | 三个 dev cases 文件已保留每题 7 变体、Keyword/Vector/Hybrid 文档排名、context ranking 和指标；现有产物可以直接做本报告逐题分析。不同 qrels 语义不得合并。 |
| candidate_k 与 RRF 的离线指标 K | 若保存的 keyword/vector 候选排名足够深，可对已有排名截断/重算指标 | 这只改变离线截断/指标 K；不代表运行时的 candidate_k 真正改过并重跑。若需要运行时行为、截断前 candidate pool 不足，仍要重新检索；不需要重算 corpus embedding。 |
| top_k 与 context budget | 已保存的排序和最终选择规则/证据 | 已完成 variants 可直接比较真实 top-k context recall。要重算未保存预算下的 context，必须保存足够的 context candidates 和长度/去重元数据；它仍不需要重新建 corpus index，但需要重跑 context-building 逻辑。 |
| Qwen answer / citation readback | qid + context chunk ids/order + prompt version + Qwen digest + generation config + verifier version | 同一上述身份才可复用 QA 行。当前 smoke 是 50-doc index；不能当成 400-doc run 的 QA 结果。未启用 Judge 的结果不能补写成 Judge。 |
| 报告 / summary metrics | 相同 manifest、cases、candidate selection JSON 与 report code SHA | 当前 `--phase report` 只读这些文件重建摘要，不触发运行。结果报告可直接复用，不需要索引或模型。 |

### SciFact 400/40 中断库：只给恢复设计，不执行

受保护的目录/数据库：`D:\RAG-Public-Bench\runs\scifact\development\20260928T102515Z-8505c397eb` 及其 `storage`；数据库 `rag_eval_trust_8505c397eb_scifact`。目前保留 5,183 documents/versions，1,530 succeeded、3,652 queued、1 stale expired lease，8,256 chunks/embeddings，停止后 0 个 benchmark worker / 0 个外部数据库连接。它没有完整 run manifest、metrics、cases 或稳定 index hash。

当前 runner 没有 partial DB resume 路径。之前启动的入口命令仅作识别，不能当作断点续跑命令；它会重新开始 ingestion/chunking/embedding，此处不执行：

`scripts/run_public_benchmark.ps1 -Phase baseline -Dataset scifact -Profile standard -Split development -ChunkSize 400 -ChunkOverlap 40 -TopK 5 -CandidateK 32 -RrfK 30 -ContextBudgetChars 8000`

恢复若未来获批，先在不碰原 DB 的前提下做 DB snapshot/clone；确认 Git/schema、dataset/config/model digest、job lease 和每个 document version 的 chunk/embedding 一致性；只在副本上处理过期 lease 和幂等 job，再逐文档核对 source/chunk/embed fingerprint；最后生成新的、完整验证的 index hash 和全量 cases/metrics/manifest。原库不能被强行标为成功索引。当前无可靠全量耗时预测，因此具体恢复工时为 `NOT_ESTIMATED`；已知原 run 在 20:05 内未到可报告状态。恢复不是这次工作，也不会自动启动。

## 8. 最多两个下一轮 opt-in 配置

1. **MIRACL-ZH candidate-k-16**：只适用于固定 6,000 文档/passage pool。Dev n=328，ΔnDCG@10 +0.0456175，95% paired CI [+0.0327808,+0.0591417]；candidate-k-16 已在原 locked n=99 运行一次，locked nDCG@10=0.676084。不能外推到完整 MIRACL 中文语料，也无 locked baseline 配对。
2. **LongBench-ZH candidate-k-64**：仅作 400 文档 paired-context 候选；dev n=320，Δ+0.0026450，95% CI [-0.0073670,+0.0141491] 跨 0，locked n=80 一次 nDCG=0.797858。没有官方检索 qrels，且 QA 质量不支持优越性结论。可用于下一轮 retrieval+QA 的有界候选比较，不改变默认。

SciFact rrf-30 的开发差值 +0.0007047，CI 跨 0，locked 已使用一次；不列入下一轮候选，不改默认参数。

## 9. 下一轮最小可选 QA pilot（现在不运行）

仅在你单独同意后，目标是在 LongBench-ZH 标准开发 index `0d41031e54185d2ebc026cdeb62a099427f35a09b0b2d2034a5ce12e4dd3c179` 上，用同一模型 `qwen3.5:4b` 配对比较 baseline candidate-k-32 与 candidate-k-64。下表给出 12 个未生成答案的固定开发 qid，各配置一次，最多 24 个评分用回答。当前 runner 每个 run 都创建 fresh DB，且 QA CLI 把 generation limit 固定为最多 4 并且没有 qid-file/offset 选择；因此现在没有能安全完成这 12×2 QA 且复用现有 index 的现成命令。获批后才可实现一个窄范围 QA 路径：按完整 index hash 只读附着到现有 400-doc index，一次 run 处理两个配置和指定 qid，禁用虚拟 warmup（首个计分答案记录冷启动），并在模型、索引或 qid hash 不一致时生成前停止。无法只读复用时，立即停止，不回退重建 index。

计算预算 10–15 分钟，硬停止上限 20 分钟；24 个计分 Qwen answer calls 是总模型调用上限（不再额外发 warmup）；query embedding 上限 24 次且仅走本地 BGE；0 个新索引、0 个 corpus embeddings、0 个 Judge、0 外发。模型实际 usage 预计按既有均值约 81,657 input / 10,458 output tokens，结果以之后模型返回记录为准。遇到 index/model digest 不匹配、任何 locked split 请求、超过 5 分钟无进度或需重试单题即停止，保留部分 QA 结果；不自动扩题、不重试已失败 qid。估时不含获批后增加只读 index attach / qid-list 能力的工程时间。预算是建议上限，不是执行授权。

| case_id | 固定 LongBench development qid |
|---|---|
| `2824bc78bcbb` | `dureader:0a36d30499531eae58f5f4a8cfd437f6c28a3c7b4c419750` |
| `bd4874271c72` | `dureader:0a53324a2f45b8a2e5016b20928f0192cee16edf9339d94b` |
| `a6009a800bb3` | `dureader:0a5c0aa841998077b125e266b1f27d8abeb5d2592da47615` |
| `114c2c10b7c3` | `dureader:0b704eaa04aa78dd365d7f50ea3e9edad9bd6a5dc3149f42` |
| `434246350a51` | `dureader:0e23c7165ff3bdb9596ece3b1743d279ebf96f4e8037c127` |
| `96ecd855c6e7` | `dureader:0e96a2a3062e231d40d378f44ffe8785ce34958449ff673c` |
| `2ea27c364fc2` | `dureader:0ff2c8f7b634c54a72e282eaa78568acd5c385e66c93550e` |
| `090abd17f735` | `dureader:109f983d7e88b42c229f88324276ce66d6e8e03464b102bd` |
| `1a621771c972` | `dureader:10f9fe7d94d5d760bb815ab218d98d0f17c0865470df3127` |
| `3d123e50af88` | `dureader:11b3b7afb73f5bf65a51b3083e885a60825b17421c58a828` |
| `55924a0fa49f` | `dureader:11d99241110c220af6d63a9497c3febad0a2a8e8a33ebdc8` |
| `ede1a46e92a4` | `dureader:11f6f41bae56eefd2f45ed3c6f58186059a01abf37b9953f` |

Judge 如需纳入，须另给预算与边界；本方案默认不开启。当前 Ollama 未加载模型；若以后授权，先核对本地安装的模型 digest，不自动下载。

## 10. ECS / 命令记录 / 结论边界

- ECS 不同步。服务端部署 SHA `f15aaeb374e32d7955429784b4d9dd50317608a5` 与本地评测 SHA `241d2c7ebdc4911e92a18bdc1e48307d85c87503` 不同；本次没有远程 SSH、bundle 上传或服务修改。SciFact sanitized bundle 即使本地存在，也未传输。
- 总报告由真实汇总器生成：`& 'E:\RAG quention\.venv\Scripts\python.exe' -m eval_center.public_benchmark_cli --phase report --data-root 'D:\RAG-Public-Bench'`，exit 0，写入 `D:\RAG-Public-Bench\reports\public-benchmark-optimization-report.md` 与 `docs/reviews/public-benchmark-optimization-report.md`。
- 只读运行状态命令：PowerShell process command-line filter 未发现 `eval_center/public_runner/run_public_benchmark` 进程（exit 0）；`docker ps --format '{{.Names}}`t{{.Image}}`t{{.Status}}'` exit 0，评测 DB 容器仍运行；`ollama ps` exit 0、无已加载模型。未停 Docker/PostgreSQL/Ollama。
- 当前 partial QA CLI 没有可直接执行的命令；当前 400/40 的 interruption command 会从头重建索引，因此不作为恢复命令。
- 完整的逐题分析读取 `completed-experiment-identities-20260928.json`、六个 formal standard run 的 manifest/metrics/report/cases、两组 LongBench QA 的 manifest/metrics/report/cases、三份 `runs/locks/*-locked-holdout.json`、对应 candidates JSON、interruption report 与 partial `interrupted.json`。所有 official run paths 均在本报告第 2 节。
- 输入 manifest 中 12 个 source path 以 SHA-256 流式核对：12/12 匹配。Run cases 逐行核对：10/10 identity run row count = manifest sample_count = unique qid count；无 duplicate qid。Locked 三锁 6 项比较均通过。候选 paired bootstrap 用保存的 dataset-version/corpus-hash seed、2,000 次重采样独立复算，三组 CI 与候选 JSON 完全匹配。
- 两个辅助检查的首次尝试分别因临时脚本错误处理 null token 数、以及 worktree 没有本地 `.venv` 返回非零；改用 None-safe 汇总和共享 `E:\RAG quention\.venv` 后的只读检查均 exit 0。它们没有改变数据。artifact audit 没有缺失文件、hash mismatch 或 query 重复项。

## 11. 结论

已完成文件支持：三个数据集各自冻结配置的一次 retrieval 验证、开发集内的候选相对效果、context/drop 阶段诊断及极小规模 LongBench answer smoke 的记录。它们不支持：跨数据集通用最优配置、MIRACL 全语料表现、LongBench 官方 retrieval 分数、语义支持/忠实性判定、长答案质量结论、near-duplicate false-merge 率或生产默认变更。下一步若获批，最多是同一现有 400-doc index 上 12×2=24 次本地 Qwen calls，0 新建 corpus index，约 10–15 分钟，20 分钟硬停止。
