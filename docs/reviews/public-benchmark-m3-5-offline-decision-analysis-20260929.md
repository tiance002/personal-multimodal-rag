# M3.5 公开数据集 A/B/C 离线决策分析

**日期：** 2026-09-29
**目的：** 基于已完成的冻结 Development 样本结果，判断是否值得进入小规模 Qwen 配对回答评测。
**分析范围：** 只读离线文件分析；本阶段未连接数据库、Docker、Ollama，也未调用检索、Embedding、生成或 Judge。

## 结论

- 建议在负责人批准后进行一次**限定在原 60 个冻结 Development QID、仅比较 A 与 C 的 Qwen 配对回答评测**。当前只完成检索证据分析；真实答案质量、引用正确性和 Token 消耗都没有测量，不能从 nDCG 或字符数替代这些结果。
- **暂不把 C 设为通用默认。** MIRACL 候选池上 C 相比 A 的 Recall@5 和 Context 来源召回平均下降 0.1667，30 题中 6 题下降、24 题持平；SciFact 结果混合，LongBench 来源召回已饱和。
- 如批准，建议复用这 60 个预先冻结的问题做 A/C 两组回答（共 120 次生成），预先固定答案完整度、证据支持和引用准确性判定；逐次记录模型实际输入/输出 Token。不要从本次结果中挑选有利题目，也不要使用 Locked Holdout。此建议不是执行授权。

## 1. 冻结数据与完整性

M3 是 `DEVELOPMENT-EXPLORATORY-RETRIEVAL-ONLY`，已完成 60 个 QID、180 条 A/B/C 结果；SciFact 20、MIRACL-ZH 30、LongBench-ZH 10。检索阶段耗时 77.769 秒，整次 M3 运行耗时 103.591 秒。M3 的真实查询 Embedding 为 60 次，缓存命中 120 次；语料 Embedding、新建索引、下载、生成、Judge、Locked 查询和数据库写入均为 0。

M3.5 只读取已经存在的 manifest、results、预注册/样本、G2/G4 证据、Development 案例文件与公开 qrels。分析器在原运行目录之外更新；它再次核验原 M3 运行产物哈希，并生成派生 JSON、本报告和机器可读附件，没有改写原始实验产物。Development 案例文件的 SHA 与预注册一致，全部冻结 QID 均属于相应 Development 案例集。

| 校验对象 | SHA-256 |
|---|---|
| 修订后预注册 | `d7116ec6aa0a6c993390d87119b43ec617ac34278ec65c430cca0caf8221ff60` |
| 原始预注册 | `53d2e48d9a9857cb27843b53f2c73bfe16fcf5aacbc410b3625585a0c0f12fc0` |
| 冻结 cases.jsonl | `07f6937c80acaed41fa388a47650c889ca3ff5e3281cd4522429ecdcc1233135` |
| QID 集合 canonical hash | `9962b1af390c5582b9a92a4a161f1f3402cce65925eca40da68a65dddd193f9a` |
| M3 manifest | `06a40ad0afef526e21f824f5f32e0d115204304522915a4ef402000aa8c0c8f4` |
| M3 results.jsonl | `eaf0bdeed2beb7346b3dbad56b2227baddfe44925124fea7331eb4365b83cb1c` |
| G2 preflight | `734d934aa992e97b8b52e8558992e63009f7d545c18099a27aaabc9a27689389` |
| G4 postflight | `9dd4ac71154fa899eeafc37d9f6090150d36c2849ae73f009ab61238368e5a26` |
| 规范化配置 | `ccbe558d77a02ac6a5e87dab876a7e6433e0a9632157517ecab70d50cfa3c39d` |

Development 输入文件哈希：
- `longbench-zh`：`29b9118617a2c38804223c777055ceefbc3296123834254cc7fc60379f8649d5`（320 个 Development 案例）
- `miracl-zh`：`41dc155ca1952e908da54021692358af777be965a08f5ac30fbb597c45619fce`（328 个 Development 案例）
- `scifact`：`d31d774ea5f836cbe41961b4d5762852ad7200caf9c5437d3762605e92a8a320`（809 个 Development 案例）

记录的来源/索引版本：

| 数据集 | Dataset version | Index version |
|---|---|---|
| scifact | `beir-scifact-5f7d1de60b170fc8027bb7898e2efca1` | `f6c8b191834638dfa8315adc158b1875ac69c0bef5ba2baac598734976aadb93` |
| miracl-zh | `MIRACL-ZH-CANDIDATE-POOL-SHARD0-SEED20260928-V1` | `629b25a5a14ea2ab960fa874c3b16e8cc77a61d0bb65e0e0847ce3c04d473c82` |
| longbench-zh | `LongBench-ZH-HF-5e628be450b7e67fb7ae6e201bd6d8f7056f7672-SEED20260928-V1` | `0d41031e54185d2ebc026cdeb62a099427f35a09b0b2d2034a5ce12e4dd3c179` |

源码来源：Git SHA `9303938c20a118cf022fd5934cbc8c4392452548`；Tree OID `b7cc742d06f14fa10d8bb2aab9189918b613d780`；Patch base `654012054bef5056663987bce683179c73735c70`；Patch SHA-256 `c5600bdc29b2e93fd784868e50ec05485e1dca34e09d69c8eefedce2cdd892ab`；M3 记录工作树干净 `true`。

## 2. A/B/C 各数据集检索指标

指标均为每题平均值。Context chars 是字符数，不是 Token。source/parent diversity 按原评测器定义；三个数据集分别解释，不合并排名。

### SciFact（Development，20 QID）

| 指标 | A 向量 | B 混合 RRF | C 向量优先 |
|---|---:|---:|---:|
| nDCG@10 | 0.6369 | 0.5418 | 0.6529 |
| Recall@5 | 0.7875 | 0.6750 | 0.7375 |
| Context source recall@5 | 0.7625 | 0.6750 | 0.7375 |
| MRR@5 | 0.5775 | 0.4667 | 0.5950 |
| Context chars | 4636.20 | 5276.90 | 4835.65 |
| Source diversity | 4.350 | 4.700 | 4.500 |
| Parent diversity | 4.350 | 4.700 | 4.500 |

### MIRACL-ZH 固定候选池（Development，30 QID）

| 指标 | A 向量 | B 混合 RRF | C 向量优先 |
|---|---:|---:|---:|
| nDCG@10 | 0.9488 | 0.6726 | 0.7957 |
| Recall@5 | 0.9889 | 0.7444 | 0.8222 |
| Context source recall@5 | 0.9889 | 0.7444 | 0.8222 |
| MRR@5 | 0.9611 | 0.6511 | 0.7411 |
| Context chars | 1009.60 | 1323.57 | 1216.17 |
| Source diversity | 5.000 | 5.000 | 5.000 |
| Parent diversity | 2.833 | 3.167 | 3.000 |

### LongBench-ZH 来源诊断（Development，10 QID）

| 指标 | A 向量 | B 混合 RRF | C 向量优先 |
|---|---:|---:|---:|
| nDCG@10 | 0.9431 | 0.9500 | 0.9631 |
| Recall@5 | 1.0000 | 1.0000 | 1.0000 |
| Context source recall@5 | 1.0000 | 1.0000 | 1.0000 |
| MRR@5 | 0.9250 | 0.9333 | 0.9500 |
| Context chars | 5180.50 | 5265.00 | 5151.00 |
| Source diversity | 2.600 | 2.700 | 2.600 |
| Parent diversity | 2.600 | 2.700 | 2.600 |

实验共用配置：candidate_k=32、top_k=5、chunk_size=1200、chunk_overlap=120、Context 上限 8000 chars；A 只启用 vector，B 启用 vector+keyword 并使用 RRF，C 启用两路并设置 vector=0.8、keyword=0.2。

## 3. A/C 同题配对差异

下表为 C−A。区间是按 QID 配对重采样的 2,000 次 percentile bootstrap 95% 区间；“升/降/平”按单题差值及 `1e-12` 平值阈值计数。区间仅作小样本探索性不确定性描述。所有区间与 M3 manifest 的原始配对区间逐项一致。

### SciFact（Development，20 QID）

| 指标 | 平均 C−A | 95% 区间 | 升 / 降 / 平（题） |
|---|---:|---:|---:|
| nDCG@10 | 0.0160 | [-0.0435, +0.0704] | 3 / 2 / 15 |
| Recall@5 | -0.0500 | [-0.1500, +0.0000] | 0 / 1 / 19 |
| Context source recall@5 | -0.0250 | [-0.1500, +0.0750] | 1 / 1 / 18 |
| MRR@5 | 0.0175 | [-0.0650, +0.0976] | 3 / 2 / 15 |
| Context chars | 199.45 | [+55.74, +350.70] | 9 / 3 / 8 |
| Source diversity | 0.150 | [-0.050, +0.350] | 4 / 1 / 15 |
| Parent diversity | 0.150 | [-0.050, +0.350] | 4 / 1 / 15 |

### MIRACL-ZH 固定候选池（Development，30 QID）

| 指标 | 平均 C−A | 95% 区间 | 升 / 降 / 平（题） |
|---|---:|---:|---:|
| nDCG@10 | -0.1531 | [-0.2470, -0.0695] | 2 / 12 / 16 |
| Recall@5 | -0.1667 | [-0.3000, -0.0500] | 0 / 6 / 24 |
| Context source recall@5 | -0.1667 | [-0.3000, -0.0500] | 0 / 6 / 24 |
| MRR@5 | -0.2200 | [-0.3534, -0.1000] | 0 / 9 / 21 |
| Context chars | 206.57 | [+83.10, +352.75] | 16 / 3 / 11 |
| Source diversity | 0.000 | [+0.000, +0.000] | 0 / 0 / 30 |
| Parent diversity | 0.167 | [-0.067, +0.433] | 6 / 3 / 21 |

### LongBench-ZH 来源诊断（Development，10 QID）

| 指标 | 平均 C−A | 95% 区间 | 升 / 降 / 平（题） |
|---|---:|---:|---:|
| nDCG@10 | 0.0200 | [+0.0000, +0.0601] | 1 / 0 / 9 |
| Recall@5 | 0.0000 | [+0.0000, +0.0000] | 0 / 0 / 10 |
| Context source recall@5 | 0.0000 | [+0.0000, +0.0000] | 0 / 0 / 10 |
| MRR@5 | 0.0250 | [+0.0000, +0.0750] | 1 / 0 / 9 |
| Context chars | -29.50 | [-84.80, +9.00] | 1 / 2 / 7 |
| Source diversity | 0.000 | [+0.000, +0.000] | 0 / 0 / 10 |
| Parent diversity | 0.000 | [+0.000, +0.000] | 0 / 0 / 10 |

## 4. 正例证据进出 Context 与关键词候选

### 正例进入

SciFact QID `35` 的正例来源 `11705328`（Development qrels grade 1）在 A 的向量候选中排名 6/7，未进 Context（`TOP_K_LIMIT`）；B 将其中一个 chunk 排到 1 并选入 Context，C 排到 2 并选入。B/C 的选中 trace 均标记 `keyword+vector`。这是本次唯一观察到的正例来源由 A 未进 Context 转为混合模式进 Context 的案例；向量候选本身已存在于 A top32，因此 trace 支持“混合检索排序有帮助”，但不是隔离关键词单独因果效应的实验。

### 正例退出与向上竞争候选

SciFact QID `1375`：已判正例 `21993510` 从 A rank 5 到 C rank 6，退出 Context；候选 `168265642` 从 A rank 6 升至 C rank 1，trace 为 `keyword+vector`，该候选在对应 qrels 中未判定，不能称为错误证据。

MIRACL 共 6 个正例来源退出 Context；均从 A top5 降到 C top6–10。所有记录在其上方的候选都同时标为 `keyword+vector`，并非 keyword-only 新文档。qrels 对 34 次候选出现的标注为：10 次 grade 0、1 次 grade 1（QID `3078476#0` 的另一个正例 `76178#0`）、23 次未判；因此不能将全部 rank 上升候选视为负例。

| QID | 正例来源 | A rank → C rank | C rank 高于正例的候选 | qrels 计数（grade 0 / grade 1 / 未判） |
|---|---|---:|---:|---:|
| `1079064#0` | `90596#12` | 3 → 10 | 7 | 0 / 0 / 7 |
| `1281019#0` | `66183#17` | 1 → 7 | 6 | 2 / 0 / 4 |
| `3078476#0` | `76178#4` | 1 → 6 | 5 | 1 / 1 / 3 |
| `4123730#0` | `7105#15` | 3 → 7 | 4 | 2 / 0 / 2 |
| `545699#0` | `17466#65` | 1 → 7 | 6 | 2 / 0 / 4 |
| `6012746#0` | `20338#49` | 1 → 7 | 6 | 3 / 0 / 3 |

<details>
<summary>逐 QID 的关键词贡献候选、A→C 排名和 qrels 标签</summary>

- **`1079064#0`** 正例 `90596#12`：A rank 3 → C rank 10；`164846#20` (8→3, qrel UNJUDGED); `467#47` (9→4, qrel UNJUDGED); `235#39` (11→5, qrel UNJUDGED); `467#49` (12→6, qrel UNJUDGED); `4090#60` (13→7, qrel UNJUDGED); `36342#6` (18→8, qrel UNJUDGED); `97342#11` (17→9, qrel UNJUDGED)。
- **`1281019#0`** 正例 `66183#17`：A rank 1 → C rank 7；`3298#36` (2→1, qrel 0); `3298#38` (4→2, qrel 0); `1715#32` (6→3, qrel UNJUDGED); `64872#0` (11→4, qrel UNJUDGED); `9268#33` (7→5, qrel UNJUDGED); `12428#0` (12→6, qrel UNJUDGED)。
- **`3078476#0`** 正例 `76178#4`：A rank 1 → C rank 6；`76178#0` (2→1, qrel 1); `481#123` (6→2, qrel UNJUDGED); `76178#6` (4→3, qrel 0); `2259#0` (10→4, qrel UNJUDGED); `26262#7` (11→5, qrel UNJUDGED)。
- **`4123730#0`** 正例 `7105#15`：A rank 3 → C rank 7；`117876#4` (8→2, qrel 0); `509#16` (7→3, qrel 0); `7105#10` (6→4, qrel UNJUDGED); `687#25` (18→6, qrel UNJUDGED)。
- **`545699#0`** 正例 `17466#65`：A rank 1 → C rank 7；`412#53` (2→1, qrel 0); `7129#0` (3→2, qrel 0); `16500#3` (8→3, qrel UNJUDGED); `23326#0` (10→4, qrel UNJUDGED); `7290#56` (12→5, qrel UNJUDGED); `23326#37` (15→6, qrel UNJUDGED)。
- **`6012746#0`** 正例 `20338#49`：A rank 1 → C rank 7；`20338#43` (4→1, qrel 0); `20338#46` (3→2, qrel 0); `15528#1` (8→3, qrel UNJUDGED); `39793#75` (7→4, qrel UNJUDGED); `20338#42` (6→5, qrel 0); `52016#0` (14→6, qrel UNJUDGED)。

</details>

MIRACL 六个正例的 qrels 均为 grade 1。上述 rank 流向与 trace 表明 keyword 是候选来源之一；由于每个抬升候选也来自 vector 且 C 同时改变了融合权重，本实验无法把降位完全归因于 keyword。

LongBench-ZH 没有正例 Context 进出变化；其 Context source recall@5 在 A/B/C 都为 1.0。

## 5. 分阶段延迟：冷 Embedding 与缓存命中分开

各单元格为 `n；p50/p90 ms`。每种模式、每种 cache state 的样本数可能不同；A 的 `keyword_retrieval_ms` 显示“未执行”。冷 Embedding 样本的 retrieval 耗时包含当次冷 Embedding 的影响，不能与 warm 行直接作算法速度比较。

### SciFact（Development，20 QID）

| 阶段 | Cache state | A 向量：n；p50/p90 ms | B 混合：n；p50/p90 ms | C 向量优先：n；p50/p90 ms |
|---|---|---:|---:|---:|
| Embedding | 冷 Embedding | n=7；135.7/3362.1 | n=7；163.6/213.5 | n=6；127.4/175.9 |
| Embedding | 缓存命中 | n=13；0.0/0.1 | n=13；0.1/0.1 | n=14；0.1/0.1 |
| Vector retrieval | 冷 Embedding | n=7；74.4/88.1 | n=7；81.1/97.9 | n=6；79.9/82.8 |
| Vector retrieval | 缓存命中 | n=13；64.7/72.9 | n=13；70.8/75.5 | n=14；72.1/81.3 |
| Keyword retrieval | 冷 Embedding | —（未执行） | n=7；202.3/230.6 | n=6；208.6/216.6 |
| Keyword retrieval | 缓存命中 | —（未执行） | n=13；208.7/225.2 | n=14；202.6/224.6 |
| Fusion | 冷 Embedding | n=7；0.1/0.1 | n=7；0.2/0.2 | n=6；0.2/0.2 |
| Fusion | 缓存命中 | n=13；0.1/0.1 | n=13；0.2/0.2 | n=14；0.2/0.2 |
| Context build | 冷 Embedding | n=7；1.0/1.5 | n=7；1.2/1.5 | n=6；1.1/1.7 |
| Context build | 缓存命中 | n=13；1.0/1.5 | n=13；1.2/1.6 | n=14；1.1/1.4 |
| Retrieval total | 冷 Embedding | n=7；252.2/3492.6 | n=7；468.5/503.3 | n=6；430.8/487.8 |
| Retrieval total | 缓存命中 | n=13；80.4/91.3 | n=13；302.8/317.1 | n=14；300.6/332.3 |

### MIRACL-ZH 固定候选池（Development，30 QID）

| 阶段 | Cache state | A 向量：n；p50/p90 ms | B 混合：n；p50/p90 ms | C 向量优先：n；p50/p90 ms |
|---|---|---:|---:|---:|
| Embedding | 冷 Embedding | n=10；128.0/177.8 | n=10；133.1/183.8 | n=10；124.0/141.6 |
| Embedding | 缓存命中 | n=20；0.0/0.0 | n=20；0.1/0.1 | n=20；0.1/0.1 |
| Vector retrieval | 冷 Embedding | n=10；55.7/67.2 | n=10；52.4/70.0 | n=10；52.2/61.7 |
| Vector retrieval | 缓存命中 | n=20；45.1/55.5 | n=20；42.3/52.3 | n=20；43.0/51.8 |
| Keyword retrieval | 冷 Embedding | —（未执行） | n=10；17.4/112.9 | n=10；59.9/114.4 |
| Keyword retrieval | 缓存命中 | —（未执行） | n=20；13.2/113.6 | n=20；12.4/111.1 |
| Fusion | 冷 Embedding | n=10；0.1/0.1 | n=10；0.2/0.2 | n=10；0.2/0.2 |
| Fusion | 缓存命中 | n=20；0.1/0.1 | n=20；0.2/0.2 | n=20；0.2/0.2 |
| Context build | 冷 Embedding | n=10；0.9/1.0 | n=10；1.0/1.4 | n=10；0.8/1.1 |
| Context build | 缓存命中 | n=20；0.7/1.3 | n=20；0.8/1.2 | n=20；0.9/1.3 |
| Retrieval total | 冷 Embedding | n=10；201.5/256.7 | n=10；276.6/355.3 | n=10；250.7/339.5 |
| Retrieval total | 缓存命中 | n=20；64.2/82.6 | n=20；83.3/184.5 | n=20；92.0/174.7 |

### LongBench-ZH 来源诊断（Development，10 QID）

| 阶段 | Cache state | A 向量：n；p50/p90 ms | B 混合：n；p50/p90 ms | C 向量优先：n；p50/p90 ms |
|---|---|---:|---:|---:|
| Embedding | 冷 Embedding | n=4；144.1/147.8 | n=3；149.4/153.3 | n=3；146.5/179.2 |
| Embedding | 缓存命中 | n=6；0.0/0.0 | n=7；0.1/0.1 | n=7；0.1/0.1 |
| Vector retrieval | 冷 Embedding | n=4；47.0/64.3 | n=3；46.5/48.0 | n=3；46.1/50.8 |
| Vector retrieval | 缓存命中 | n=6；45.5/48.8 | n=7；38.8/40.7 | n=7；38.5/44.0 |
| Keyword retrieval | 冷 Embedding | —（未执行） | n=3；305.5/471.0 | n=3；274.2/648.7 |
| Keyword retrieval | 缓存命中 | —（未执行） | n=7；227.1/1483.1 | n=7；120.4/287.4 |
| Fusion | 冷 Embedding | n=4；0.1/0.2 | n=3；0.4/0.4 | n=3；0.2/0.2 |
| Fusion | 缓存命中 | n=6；0.1/0.1 | n=7；0.2/0.3 | n=7；0.2/0.2 |
| Context build | 冷 Embedding | n=4；0.7/0.8 | n=3；0.9/1.3 | n=3；1.2/1.3 |
| Context build | 缓存命中 | n=6；0.6/1.0 | n=7；0.7/1.0 | n=7；0.8/0.9 |
| Retrieval total | 冷 Embedding | n=4；214.3/226.5 | n=3；538.2/698.9 | n=3；477.8/907.3 |
| Retrieval total | 缓存命中 | n=6；67.8/80.3 | n=7；295.4/1539.0 | n=7；168.3/350.0 |

A/C 同题且两边均为 warm cache 的 retrieval total 差异：

| 数据集 | 同为 warm 的 QID 数 | C−A 平均 ms | 95% 配对区间 ms | 配对差值 p50/p90 ms |
|---|---:|---:|---:|---:|
| scifact | 7 | +226.0 | [+195.7, +252.3] | +232.2/+264.5 |
| miracl-zh | 10 | +51.6 | [+19.7, +83.5] | +36.3/+110.6 |
| longbench-zh | 3 | +150.8 | [-22.0, +278.0] | +196.3/+261.6 |

这些 matched warm 子集只有 3/10/7 题，作为小样本延迟观察。M3 查询 Embedding 总量为 60 次，缓存命中 120 次。

## 6. Token、模型与索引

180 条逐题结果的 `tokens` 都是 `NOT_MEASURED`；因此本报告将 Token 成本标为 `NOT_MEASURED`。Context chars 只代表字符数，不按固定字符比例估算 Token，也不推测 Qwen 输入 Token。

M3 manifest 记录的模型身份与索引版本：

| 数据集 | Embedding 模型 digest | Chat 模型 digest（身份记录；本次未调用） | Index version |
|---|---|---|---|
| scifact | `bge-m3:latest` `7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab` | `qwen3.5:4b` `2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd` | `f6c8b191834638dfa8315adc158b1875ac69c0bef5ba2baac598734976aadb93` |
| miracl-zh | `bge-m3:latest` `7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab` | `qwen3.5:4b` `2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd` | `629b25a5a14ea2ab960fa874c3b16e8cc77a61d0bb65e0e0847ce3c04d473c82` |
| longbench-zh | `bge-m3:latest` `7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab` | `qwen3.5:4b` `2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd` | `0d41031e54185d2ebc026cdeb62a099427f35a09b0b2d2034a5ce12e4dd3c179` |

M3 的 generation_calls 与 judge_calls 均为 0；M3.5 也没有任何模型调用。

## 7. 决策解释与建议

- **SciFact：** C 的平均 nDCG@10 比 A 高 0.0160，但 Recall@5 低 0.0500；配对区间均跨 0 或触及 0。关键词与向量共同把一条 grade-1 正例带入 Context，也有一条正例退出。
- **MIRACL 固定候选池：** A 的检索正例覆盖最好。C 的 nDCG@10 平均低 0.1531、Recall@5/Context source recall@5 平均低 0.1667；Context 平均多 206.6 chars，source diversity 不变。不能据此断言答案质量同比下降，但说明回答评测应关注这些证据丢失题。
- **LongBench-ZH：** source recall 饱和，C 的 nDCG@10 平均增 0.0200；10 题不足以支持通用结论，应作为来源诊断。
- **建议：** 若负责人批准，继续一次仅 A/C 的小规模 Qwen 配对回答实验，复用当前冻结的全部 60 个 Development QID，不再按结果挑题；记录每次实际 Token、答案正确性、证据支持和引用有效性。当前没有足够证据改生产默认，也不执行 Qwen、Judge 或扩大网格。

## 8. 可复核来源与 qrels

| qrels 文件 | SHA-256 |
|---|---|---|
| SciFact qrels/train.tsv | `a53f2114831916c096b6c37d9e54da68cef4efdcdbd5ed46533601af972acf1d` |
| SciFact qrels/test.tsv | `0864bb985e0ca2367ba217977e72004d549054b2b06666ed9d4825ac7c21284c` |
| MIRACL raw qrels/train.tsv | `0ad7a35722803798058be9e4a8690b74b255591539f51827d49a1849a8585ee9` |

SciFact 与 MIRACL qrels 用于核对本报告列出的 Development 正例和向上候选；未使用 Locked Holdout 样本。LongBench 作为来源诊断单独报告。

## 9. 命令与验证状态

| 命令 / 检查 | 退出码 / 状态 | 结果 |
|---|---:|---|
| `py -3 "D:\RAG-Public-Bench\reports\m3_5_offline_analysis.py"` | `0` | `PASS`；完整性哈希、180 行状态、Development 文件哈希与 QID 归属、聚合值及 A/C bootstrap 区间与 manifest 一致；写入派生 JSON。 |
| DB / Ollama / 检索 / Embedding / Qwen / Judge 操作 | `NOT RUN` | 本分析不进行这些操作。 |
| 自动化测试 | `NOT RUN` | 本任务只分析已完成实验并新增报告，不改应用代码。 |

M3 原始运行状态由已校验的 manifest/G2/G4 证据给出：`COMPLETED` / G2 `PASS` / G4 `PASS`；这不是 M3.5 新运行。

## 10. 交付与下一步

- M3.5 分析器：`D:\RAG-Public-Bench\reports\m3_5_offline_analysis.py`；SHA-256 `841c3880f2ce2932dcd8be1a6343e3829fc95b623953532a3aa10753f234c7db`。
- 机器可读分析：`docs/reviews/artifacts/public-benchmark-m3-5-offline-analysis-20260929.json`；SHA-256 `ae53e1a4cac2e33abbd649ab64c0edb05466e6131ae17e7b38890714593a1590`。
- 原始 M3 manifest/results、冻结样本、索引及默认配置均未改。
- 下一步：等待负责人批准是否按上述范围运行 Qwen A/C 配对回答评测；在批准前不启动任何耗时评测。
