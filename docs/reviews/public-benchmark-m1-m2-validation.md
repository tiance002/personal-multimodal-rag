# 公开数据集评测 M1/M2 融合排序与 Context 小规模验证

运行记录状态：**COMPLETED（development 诊断子集）**；这不是官方分数或产品验收。M1/M2 运行时间为 2026-09-29 08:10:06 至 08:14:01（UTC+08），总耗时 237.942 秒。清单记录的基线 Git SHA 为 `241d2c7ebdc4911e92a18bdc1e48307d85c87503`，但运行时实现处于未提交工作树，且清单未保存 patch/tree hash；因此结果可追溯到运行产物和基线提交，不能视为由不可变源码快照完全复现。证据文件：`D:\RAG-Public-Bench\runs\m1-m2\20260929T001003Z\manifest.json` 与同目录 `cases.jsonl`。

## 本次目标与身份核对

本报告仅覆盖公开数据 development 诊断子集：A vector-only、B 当前 Hybrid RRF、C 预先固定的 vector/keyword=0.8/0.2 加权 RRF（RRF k=60）。总量符合上限 60 qid / 180 次 retrieval / 20 分钟；**实际抽样为 SciFact 24、MIRACL-ZH 24、LongBench-ZH 12，与任务建议的 20/30/10 配额不同**，应按偏差样本解读，不外推为总体结论。qid 集合 SHA-256：`051e9b5027b288b5ff9c6b34bf26ab5322bb5ff26b666212f0b7057296cf109e`。

| 数据集 | 运行记录版本 | 数据/语料 hash | 索引 hash | 文档 / Chunk / Embedding |
|---|---|---|---|---:|
| SciFact | `beir-scifact-5f7d1de60b170fc8027bb7898e2efca1` | `8608aa67e02a307745506d92316ebd0d1b2079226a576446c2bb5943e064f59f` | `f6c8b191834638dfa8315adc158b1875ac69c0bef5ba2baac598734976aadb93` | 5183 / 9256 / 9256 |
| MIRACL-ZH | `MIRACL-ZH-CANDIDATE-POOL-SHARD0-SEED20260928-V1` | `105dde269ea99ddee8ad0ebf8649631e88c3710be9ad2b54b4ced59e4a17ad29`（固定 pool hash：`eb84e2b498eefb08c06c022616d6b63dcc64688d8e74aee0e6442b58770b5032`） | `629b25a5a14ea2ab960fa874c3b16e8cc77a61d0bb65e0e0847ce3c04d473c82` | 6000 / 6001 / 6001 |
| LongBench-ZH | `LongBench-ZH-HF-5e628be450b7e67fb7ae6e201bd6d8f7056f7672-SEED20260928-V1` | `d5f18bf83d652664d6778d1fab2eba0c1d6ef7781cc4c106e96217eca1cac0c1` | `0d41031e54185d2ebc026cdeb62a099427f35a09b0b2d2034a5ce12e4dd3c179` | 400 / 5003 / 5003 |

三数据集共用的 A/B/C 配置对象 SHA-256（按 manifest `configs` 做 UTF-8、key 排序、紧凑 JSON 编码）：`ccbe558d77a02ac6a5e87dab876a7e6433e0a9632157517ecab70d50cfa3c39d`。三库 schema 为 `0013_message_run_link`，chunk size/overlap 为 1200/120。运行记录中的 Embedding 模型为 BGE-M3，digest `7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab`；Chat 模型记录为 Qwen 3.5 4B，digest `2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd`。本轮没有调用 Qwen。

## 证据边界与当前索引状态

运行目录当前仅保存 manifest 与逐题 cases，没有 M1/M2 控制台日志或 PostgreSQL 前后快照。原草稿记录了 py_compile、定向 pytest 与只读 SQL 检查的退出码和摘要；本次发布审计没有重跑这些测试或评测，不能把草稿记录表述为本次重新验证的结果。逐题产物可独立核对完成数、配置、cache 计数、指标和 trace；数据库写探针结果及运行时索引复核只保留在原草稿的执行摘要中。

2026-09-29 15:44（UTC+08）的只读环境快照显示：Docker 服务已停止；本机正在运行的 PostgreSQL catalog 不含本次清单指定的三个 `rag_eval_trust_*` 数据库；`D:\RAG-Public-Bench\indexes` 为空。因此**当前无法重新附着或复核这三套索引**。历史 manifest 仍保留当时记录的 index hash、schema、模型 digest 与数量；这不等于这些数据库现在可用。本次没有启动 Docker、创建数据库、重建索引、重新下载或重跑 M2。全量网格保持停止。

## 目标与改动

- M1 Context Trace：`backend/app/application/context_builder.py`、`eval_center/public_trace.py`、`eval_center/public_runner.py` 记录逐候选排序、来源/版本/parent/locator、字符占用、选入状态和真实拒绝原因。旧结果缺字段标记 `INSUFFICIENT_TRACE`，不回填推断原因。本次真实 run 仅出现 `TOP_K_LIMIT`；没有伪造预算或去重拒绝。M0 旧案例的 rank≤K 且未入选原因仍不可从旧记录重现，未来 run 可直接检查 trace。
- M1 查询缓存：`eval_center/query_cache.py` 在单次运行内按归一化查询哈希、预处理版本、模型 digest、profile、维度和 KB 范围复用成功向量；失败不缓存。真实调用 60，命中 120，失败 0。
- M1 只读索引：`eval_center/readonly_public_index.py` 将附着限制在三个白名单 development 库，并检查数据/语料 hash、schema、chunker、模型 digest、来源版本、ready、数量和索引 fingerprint。原报告记录 transaction read-only 与写探针拒绝；对应 PostgreSQL 原始日志未保留，当前数据库不可见，故本次发布审计无法重验该连接结果。未调用迁移、ingestion 或索引构建。
- M2：`backend/app/domain/fusion.py`、`backend/app/application/retrieval.py` 提供 opt-in 真正 vector-only 与固定权重 RRF；默认生产配置保持原样。`eval_center/public_m1_m2.py` 固定抽样、限定配额、逐题落盘并计时。C 在运行前固定 vector/keyword=0.8/0.2，RRF k=60；A/B/C 均 top_k=5、单支路候选深度=32、context 预算=8000 字符。

## 执行命令与证据

| 命令 | 退出码与关键结果 |
|---|---|
| `python -B -m py_compile eval_center/public_m1_m2.py eval_center/readonly_public_index.py eval_center/public_trace.py eval_center/query_cache.py` | 0 |
| `python -B -m pytest -q eval_center/tests/test_public_trace.py eval_center/tests/test_query_cache.py eval_center/tests/test_readonly_public_index.py backend/tests/test_opt_in_retrieval_modes.py` | 0；11 passed，6 条依赖弃用 warning |
| `run_m1_m2(data_root=..., output_root=..., database_urls=...)`（原报告保留的函数调用摘要；没有保存 shell 命令/控制台日志） | manifest/cases 显示 COMPLETED：60 qid、180 retrieval、237.942 秒；本次未重跑 |
| PostgreSQL 只读连接查询 `documents/chunks/chunk_embeddings` | 原报告记录 exit 0 且计数一致；原始 SQL 输出/前后快照未持久化，当前环境无法重连这三库 |

运行的固定子集：SciFact 24、MIRACL-ZH 24、LongBench-ZH 12；qid、抽样层和 hash 在 manifest。该数据集分配偏离任务建议的 SciFact 20、MIRACL-ZH 30、LongBench-ZH 10，但总 qid 与 retrieval 配额未超限。M0 报告列出的 13 个案例全部包含在本次子集中，三方案的 trace 均为 `COMPLETE`；这验证的是新运行记录，不是为旧记录补造 trace。没有执行 locked split、QA/Qwen/Judge/Ragas、下载、ECS 同步、全量网格。query embedding 60、corpus embedding **0**、新索引 **0**、Qwen **0**、Judge **0**。

| 运行记录索引 | 文档 / Chunk / Embedding（manifest 记录） | index hash |
|---|---:|---|
| SciFact | 5183 / 9256 / 9256 | `f6c8b191834638dfa8315adc158b1875ac69c0bef5ba2baac598734976aadb93` |
| MIRACL-ZH | 6000 / 6001 / 6001 | `629b25a5a14ea2ab960fa874c3b16e8cc77a61d0bb65e0e0847ce3c04d473c82` |
| LongBench-ZH | 400 / 5003 / 5003 | `0d41031e54185d2ebc026cdeb62a099427f35a09b0b2d2034a5ce12e4dd3c179` |

manifest 记录三个库 schema 为 `0013_message_run_link`，以及当时索引数量和模型 digest。原报告另记录文档 ready、来源版本校验、`CREATE TEMP TABLE` 写探针被拒绝和前后计数相等；这些检查的原始数据库输出未持久化。鉴于当前评测数据库不可见，以上连接期检查仅能作为历史执行摘要，不能在本次发布审计中独立复核；单凭计数也不能证明字节级未变。

## M2 真实检索结果

下表为各 qid 宏平均。SciFact 使用原 document qrels；MIRACL 使用 6k pool 内 passage qrels；LongBench 仅为 paired-source 诊断，不能与官方检索 qrels 混称。

| 数据集 | 方案 | nDCG@10 | Recall@5 | MRR@5 | Context source recall | 平均检索 ms | 平均 Context 字符 |
|---|---|---:|---:|---:|---:|---:|---:|
| SciFact | A vector-only | .6319 | .6424 | .5972 | .6424 | 340 | 4768 |
| SciFact | B hybrid RRF | .5202 | .6528 | .4694 | .6111 | 377 | 5308 |
| SciFact | C vector-priority | .5612 | .6632 | .4875 | .6632 | 313 | 4962 |
| MIRACL-ZH | A vector-only | .8600 | .9599 | .8243 | .9599 | 168 | 1053 |
| MIRACL-ZH | B hybrid RRF | .6203 | .6798 | .6278 | .6798 | 199 | 1453 |
| MIRACL-ZH | C vector-priority | .7792 | .8127 | .7743 | .8127 | 147 | 1216 |
| LongBench-ZH | A vector-only | .6656 | .6667 | .6250 | .6667 | 164 | 4998 |
| LongBench-ZH | B hybrid RRF | .6226 | .6667 | .4958 | .5833 | 606 | 5434 |
| LongBench-ZH | C vector-priority | .6579 | .6667 | .5486 | .6667 | 304 | 5299 |

相对 B 的逐题 nDCG@10 改善/退步/持平：SciFact A 10/2/12、C 10/1/13；MIRACL A 15/2/7、C 15/1/8；LongBench A 4/3/5、C 4/3/5。MIRACL top-10 中未标注比例：A 114/240=47.5%，B 144/240=60.0%，C 121/240=50.4%；因此数值是固定 pool 诊断，不外推全语料性能。平均选入来源数 SciFact A/B/C=4.50/4.50/4.33，MIRACL 都为 5.00，LongBench=2.83/3.00/3.00；平均不同 parent 数分别为 SciFact 4.50/4.50/4.33、MIRACL 3.04/3.71/3.46、LongBench 2.83/3.00/3.00。字符数是最终渲染长度，未将字符数冒充精确 Token；估算 Token 可按约 4 字符/Token 粗略观察，但中文误差可能较大。

按每题是否有相关来源进入最终 Context 归因：SciFact A/B/C 入选 16/16/17，相关来源在候选内但被 top-K 截断 8/8/7；MIRACL 入选 24/19/22，top-K 截断 0/5/2；LongBench 入选 8/7/8，top-K 截断 3/5/4，A 另有 1 题候选缺失。对照 A 与 B，MIRACL 的 `1022676#0` 是向量候选有正例而当前融合使其落到 top-K 外的实例。未观察到相关来源被字符预算拒绝、无效来源映射或独立去重分支；同 parent 多 Chunk 的占位通过候选及选入 parent 列表可审计。完整逐题归因请以 `cases.jsonl` 的真实 trace 为准。

## 结果、风险与下一步

M1：运行产物中的 A/B/C 共 180 条逐题 trace 均为 COMPLETE，缓存计数为 60 次实际 embedding / 120 次命中 / 0 次失败；原草稿报告定向测试 11/11 通过，但控制台日志未保存，本次没有重跑。M2：历史运行 manifest 标记 COMPLETED，包含 60/60 qid、180/180 retrieval，耗时低于 20 分钟；当前索引重连状态为 **BLOCKED_INDEX_REUSE**，所以不能在当前主机复现数据库只读检查或继续跑新的检索。这支持“当前 RRF 在该子集部分题上使相关来源降位”的诊断，不支持直接修改生产默认或宣称总体优劣。样本有意包含旧疑难案例，且 MIRACL 未标注率高；需要独立预注册样本验证才能推广结论。LongBench 仍仅是 paired-source 诊断；当前没有运行小规模 LongBench QA 的必要，除非先在 development 上确定一个检索候选并单独批准生成评估。

未执行：locked_holdout、全量网格、SciFact 400/40 恢复、LongBench QA、生产部署、Release Tag。本次发布仅提交诊断报告，不提交未提交的 M1/M2 实现代码、逐题结果文件或任何凭据。没有运行新的评测任务。当前下一步是由负责人审阅已保存结果，并在数据库索引可用性恢复后决定是否另行批准独立验证；不得自动恢复全量网格。
