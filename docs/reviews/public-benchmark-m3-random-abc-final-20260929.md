# M3 Development 随机样本 A/B/C 检索复核报告

日期：2026-09-29
状态：**PASS（本次 Development 检索复核）**

## 目标与范围

在已完成的 M1/M2 公共索引上，用事先冻结的 Development 样本比较三个检索配置。仅运行检索；不生成答案、不调用 Judge、不访问 Locked Holdout。评测结果属于小样本探索性证据，不代表发布验收或全量基准结论。

样本固定为 SciFact 20 题、MIRACL-ZH 30 题、LongBench-ZH 10 题，seed 为 20260929。选择规则为按 SHA256(dataset|qid|seed) 升序取样。冻结样本共 60 个 QID，A/B/C 每题各运行一次。

原冻结记录保留未修改。代码身份修复提交后，创建了新的预注册目录；新旧 QID 集、cases.jsonl 字节、配置、运行上限、数据版本和历史 M1/M2 manifest 均逐项核对相同。此次修订只更正源码 provenance。

## 配置

| 模式 | 检索源 | 权重 |
|---|---|---|
| A-vector-only | Vector | 默认 |
| B-hybrid-rrf | Keyword + Vector，RRF | 默认 |
| C-vector-priority-0.8-0.2 | Keyword + Vector，RRF | Keyword 0.2 / Vector 0.8 |

三种配置都使用 top_k=5、candidate_k=32、rrf_k=60、chunk_size=1200、chunk_overlap=120、context budget=8000 字符。每题模式顺序按 ABC、BCA、CAB 轮换。

## 结果

各数据集单独报告，不计算跨数据集总分。

| 数据集 | 模式 | n | nDCG@10 | Recall@5 | MRR@5 | Context source Recall@5 |
|---|---|---:|---:|---:|---:|---:|
| SciFact | A-vector-only | 20 | 0.6369 | 0.7875 | 0.5775 | 0.7625 |
| SciFact | B-hybrid-rrf | 20 | 0.5418 | 0.6750 | 0.4667 | 0.6750 |
| SciFact | C-vector-priority-0.8-0.2 | 20 | 0.6529 | 0.7375 | 0.5950 | 0.7375 |
| MIRACL-ZH | A-vector-only | 30 | 0.9488 | 0.9889 | 0.9611 | 0.9889 |
| MIRACL-ZH | B-hybrid-rrf | 30 | 0.6726 | 0.7444 | 0.6511 | 0.7444 |
| MIRACL-ZH | C-vector-priority-0.8-0.2 | 30 | 0.7957 | 0.8222 | 0.7411 | 0.8222 |
| LongBench-ZH | A-vector-only | 10 | 0.9431 | 1.0000 | 0.9250 | 1.0000 |
| LongBench-ZH | B-hybrid-rrf | 10 | 0.9500 | 1.0000 | 0.9333 | 1.0000 |
| LongBench-ZH | C-vector-priority-0.8-0.2 | 10 | 0.9631 | 1.0000 | 0.9500 | 1.0000 |

B→C 的配对平均 nDCG@10 差值：SciFact +0.1111（bootstrap 95% 区间 [0.0399, 0.1820]），MIRACL-ZH +0.1231（[0.0721, 0.1793]），LongBench-ZH +0.0131（[0.0000, 0.0393]）。MIRACL-ZH 上 A 的结果仍高于 C；SciFact 上 A 与 C 的差异较小。LongBench-ZH 的 Recall@5 已达到 1.0，存在饱和现象。

这些区间是固定小样本上的探索性描述，不用于宣称普遍统计显著性。结果显示数据集之间表现不同，不支持用一个合并分数选出通用最优配置。

## G2 / G4 索引与数据只读核验

运行前 G2 与运行后 G4 均通过。三个数据库的 schema_revision 都是 0013_message_run_link；运行前后 index_version、索引计数、BGE/Qwen 模型 digest、来源版本绑定数和 HNSW 定义 hash 完全相同。事务处于只读模式，HNSW 索引 valid/ready。

| 数据集 | 文档 / Chunk / Embedding | 来源绑定 | index_version |
|---|---:|---:|---|
| SciFact | 5183 / 9256 / 9256 | 5183 | f6c8b191834638dfa8315adc158b1875ac69c0bef5ba2baac598734976aadb93 |
| MIRACL-ZH | 6000 / 6001 / 6001 | 6000 | 629b25a5a14ea2ab960fa874c3b16e8cc77a61d0bb65e0e0847ce3c04d473c82 |
| LongBench-ZH | 400 / 5003 / 5003 | 400 | 0d41031e54185d2ebc026cdeb62a099427f35a09b0b2d2034a5ce12e4dd3c179 |

本次只连接本机克隆库 127.0.0.1:25437。原数据库容器保持停止，原数据卷未挂载、未读取。评测结束后克隆容器也已正常停止。

## 执行与用量

| 阶段 | 结果 / 耗时 |
|---|---|
| 冻结修订样本 | PASS；QID、cases、配置和限制与原冻结记录相同 |
| G2 运行前只读预检 | PASS；126.426 秒 |
| G3 A/B/C 检索 | COMPLETED；退出码 0；103.591 秒，其中检索阶段 77.769 秒 |
| G4 运行后只读复核 | PASS；27.183 秒 |
| 检索尝试 / 完成模式结果 / 完成 QID | 180 / 180 / 60 |
| 成功查询 Embedding | 60（每个 QID 一次，A/B/C 间复用） |
| 语料 Embedding / 新索引 / 下载 | 0 / 0 / 0 |
| Qwen 生成 / Judge / Locked 查询 / 数据库写入 | 0 / 0 / 0 / 0 |
| Token 用量 | NOT_MEASURED |

第一次 G2 辅助脚本调用因外部脚本未把源码工作树加入 Python 模块路径而以退出码 1 结束，错误发生在导入项目模块时，未连接数据库。修正模块路径后，同一只读预检通过；该调用未触发模型推理或检索。

配置通过用户级环境变量提供。凭据未写入仓库、报告或命令行参数，也未回显。

## 源码、数据、配置与结果身份

- M3 源码提交：9303938c20a118cf022fd5934cbc8c4392452548
- Git Tree：b7cc742d06f14fa10d8bb2aab9189918b613d780
- Commit Patch SHA256：c5600bdc29b2e93fd784868e50ec05485e1dca34e09d69c8eefedce2cdd892ab
- 工作树：clean
- 配置 SHA256（canonical JSON）：ccbe558d77a02ac6a5e87dab876a7e6433e0a9632157517ecab70d50cfa3c39d
- BGE-M3 digest：7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab
- Qwen 3.5 4B digest：2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd（本次生成调用为 0）
- HNSW 定义 SHA256：24649c5333c4a0d55854016faf971dffe2cd8b1e585d8f6f243a356bd5f58444

| 数据集 | dataset_version | Development cases SHA256 |
|---|---|---|
| SciFact | beir-scifact-5f7d1de60b170fc8027bb7898e2efca1 | d31d774ea5f836cbe41961b4d5762852ad7200caf9c5437d3762605e92a8a320 |
| MIRACL-ZH | MIRACL-ZH-CANDIDATE-POOL-SHARD0-SEED20260928-V1 | 41dc155ca1952e908da54021692358af777be965a08f5ac30fbb597c45619fce |
| LongBench-ZH | LongBench-ZH-HF-5e628be450b7e67fb7ae6e201bd6d8f7056f7672-SEED20260928-V1 | 29b9118617a2c38804223c777055ceefbc3296123834254cc7fc60379f8649d5 |

- 修订预注册 SHA256：d7116ec6aa0a6c993390d87119b43ec617ac34278ec65c430cca0caf8221ff60
- 原预注册 SHA256：53d2e48d9a9857cb27843b53f2c73bfe16fcf5aacbc410b3625585a0c0f12fc0
- cases.jsonl SHA256：07f6937c80acaed41fa388a47650c889ca3ff5e3281cd4522429ecdcc1233135
- QID set SHA256：9962b1af390c5582b9a92a4a161f1f3402cce65925eca40da68a65dddd193f9a
- 历史 M1/M2 manifest SHA256：78847a429048e412f018089bf76ced269705d901ee79edcdfb675a81cf701fa3
- G2 evidence SHA256：734d934aa992e97b8b52e8558992e63009f7d545c18099a27aaabc9a27689389
- Run manifest SHA256：06a40ad0afef526e21f824f5f32e0d115204304522915a4ef402000aa8c0c8f4
- results.jsonl SHA256：eaf0bdeed2beb7346b3dbad56b2227baddfe44925124fea7331eb4365b83cb1c
- Runner report SHA256：154a7b54d4cc34827e2ebd0b3225b577c5b64270de525fcfd30bbacace7499c4
- G4 evidence SHA256：9dd4ac71154fa899eeafc37d9f6090150d36c2849ae73f009ab61238368e5a26

源码修复提交 9303938 修正 expected index identity 的比较契约，并加入匹配/不匹配回归用例。该提交是本次运行绑定的实际源码版本；未覆盖历史实验提交。

## 命令记录

连接串仅由用户级环境变量 RAG_PUBLIC_BENCH_ADMIN_DATABASE_URL 注入进程，未放入命令行、仓库或报告。

| 命令 | 结果 |
|---|---|
| E:\RAG quention\.venv\Scripts\python.exe -m eval_center.public_m3_abc freeze --data-root D:\RAG-Public-Bench --m1-m2-manifest D:\RAG-Public-Bench\runs\m1-m2\20260929T001003Z\manifest.json --output-dir D:\RAG-Public-Bench\runs\m3-random-abc\prereg-20260929-seed20260929-amend-9303938 | PASS，退出码 0 |
| E:\RAG quention\.venv\Scripts\python.exe D:\RAG-Public-Bench\runs\m3-random-abc\prereg-20260929-seed20260929-amend-9303938\g2_preflight.py（首次缺少源码模块路径） | FAIL，退出码 1；ModuleNotFoundError，发生在导入阶段，未连接数据库 |
| 修正模块路径后，以同一 Python 和 g2_preflight.py 重跑 | PASS，退出码 0；126.426 秒 |
| E:\RAG quention\.venv\Scripts\python.exe -m eval_center.public_m3_abc run --data-root D:\RAG-Public-Bench --m1-m2-manifest D:\RAG-Public-Bench\runs\m1-m2\20260929T001003Z\manifest.json --preregistration D:\RAG-Public-Bench\runs\m3-random-abc\prereg-20260929-seed20260929-amend-9303938\preregistration.json --cases D:\RAG-Public-Bench\runs\m3-random-abc\prereg-20260929-seed20260929-amend-9303938\cases.jsonl --expected-preregistration-sha256 d7116ec6aa0a6c993390d87119b43ec617ac34278ec65c430cca0caf8221ff60 --output-dir D:\RAG-Public-Bench\runs\m3-random-abc\run-20260929-amend-9303938 | PASS，退出码 0，状态 COMPLETED；103.591 秒 |
| E:\RAG quention\.venv\Scripts\python.exe D:\RAG-Public-Bench\runs\m3-random-abc\run-20260929-amend-9303938\g4_postflight.py | PASS，退出码 0；27.183 秒 |
| docker start rag-eval-trust0928-db-clone-20260929t091523z；docker exec rag-eval-trust0928-db-clone-20260929t091523z pg_isready -U rag_eval -d postgres | PASS；克隆 PostgreSQL 接受连接 |
| docker stop rag-eval-trust0928-db-clone-20260929t091523z | PASS；原库与克隆库最终均为 Exited |
## 解释限制与产物

MIRACL-ZH 的 judged/unjudged 率来自冻结的 6,000 文档候选池，不能外推至完整 MIRACL。LongBench-ZH 是 task-context 诊断，不是官方检索 qrels 或完整语料评测。该复核不评估生成答案、引用语义、Judge 质量或 Locked Holdout。

完整运行产物保存在：

D:\RAG-Public-Bench\runs\m3-random-abc\run-20260929-amend-9303938

冻结样本、修订说明、G2 证据保存在：

D:\RAG-Public-Bench\runs\m3-random-abc\prereg-20260929-seed20260929-amend-9303938

仓库仅提交本脱敏 Markdown 报告，不提交逐题 results.jsonl、冻结 cases、凭据或本机数据库数据。
