# RAG V1 检索优化第一轮：Development 配对复核

日期：2026-09-30。状态：**检索验证完成；QA Pilot 未运行；不改变生产默认**

## 1. 目标与改动

本轮在 SciFact、MIRACL 中文和 LongBench 中文各运行冻结的 80 个 Development QID，对比历史等权 RRF 基线与一个固定候选：Vector 权重 `1.0`、Keyword 权重 `0.25`、`rrf_k=60`、`candidate_k=32`、`top_k=5`；候选 Context 从最多 10 个候选 Chunk 中选最多 5 个，首轮每文档最多 2 个并稳定回填。每题运行两个检索臂，共 240 个同题配对、480 次检索调用。

三个实现任务分别提交：

- `a066f4d84f5865c439e1383a1b4b708780e3421c`：可选加权 RRF；默认仍为等权。
- `7db2d57eedeed9ac92b163a6941d35e6065b9818`：候选 Context 的文档多样性与稳定回填。
- `26fe20ff74449f9b5cd9683a1b4b708780e3421c`：运行级查询 Embedding 缓存、冻结 Development 数据加载、只读克隆索引复用校验和固定 QID fixture。

执行前的两个小修复也分别提交：`a733d1dcd63dee0b2ab68a89aff47189732d3067` 修复 PostgreSQL `DISTINCT` profile 查询的排序列；`901b439d7e3c37def2da67fe05e181b4c9222a24` 令索引源码身份比较 Git 提交树对象，避免 Windows CRLF 检出差异被误判为代码变化。实验使用的代码提交为 `901b439d7e3c37def2da67fe05e181b4c9222a24`，Tree SHA 为 `fed248308583c5d9bf00dbbcedb22233761eb9a2`。

历史 M3 Development 诊断显示等权融合在 SciFact 与 MIRACL 的 Vector→Hybrid 损失明显，支持测试有限 Keyword 降权；该诊断不是本轮样本结果：

| 数据集 | 历史 Vector Recall@5 | 历史等权 Hybrid Recall@5 | Vector→Hybrid Top-5 损失 | 完整 Hybrid 排名中正相关文档缺席于已选 Context |
|---|---:|---:|---:|---:|
| SciFact | 0.7785 | 0.6932 | 84/809 | 156/809 |
| MIRACL 中文 | 0.9656 | 0.7122 | 68/328 | 68/328 |
| LongBench 中文 | 0.9250 | 0.9219 | 6/320 | 21/320 |

历史分析还表明这些 Context 缺失大多发生在 Chunk Top-5 截断和文档折叠之前后，不等同于 ContextBuilder 因字符预算丢弃已选 Chunk。

## 2. 执行命令

依赖环境变量从 Windows 用户作用域载入；密码没有打印或写入仓库。

```powershell
$env:RAG_PUBLIC_BENCH_ADMIN_DATABASE_URL = [Environment]::GetEnvironmentVariable('RAG_PUBLIC_BENCH_ADMIN_DATABASE_URL','User')
& 'E:\RAG quention\.venv\Scripts\python.exe' -m scripts.validate_retrieval_round1 --preflight-only
$code=$LASTEXITCODE
Remove-Item Env:RAG_PUBLIC_BENCH_ADMIN_DATABASE_URL -ErrorAction SilentlyContinue
exit $code
```

退出码 `0`，输出 `PREFLIGHT_COMPLETE`：三个索引、模型摘要、克隆容器/卷、Development 文件和 Git 身份均匹配；查询 Embedding、建索引及数据库写入均为 `0`。

```powershell
$env:RAG_PUBLIC_BENCH_ADMIN_DATABASE_URL = [Environment]::GetEnvironmentVariable('RAG_PUBLIC_BENCH_ADMIN_DATABASE_URL','User')
& 'E:\RAG quention\.venv\Scripts\python.exe' -m scripts.validate_retrieval_round1 --data-root 'D:\RAG-Public-Bench' --output-root 'D:\RAG-Public-Bench\round1\results\2026-09-30-rag-retrieval-opt-round1'
$code=$LASTEXITCODE
Remove-Item Env:RAG_PUBLIC_BENCH_ADMIN_DATABASE_URL -ErrorAction SilentlyContinue
exit $code
```

退出码 `0`，最终状态 `RETRIEVAL_COMPLETE`。输出目录在执行前不存在；结果文件写入后完成模型、容器和索引后置身份核验。

定向验证命令均退出码 `0`：

- `& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest eval_center/tests/test_query_embedding_cache.py eval_center/tests/test_verified_index.py eval_center/tests/test_development_data.py eval_center/tests/test_retrieval_replay.py -q --tb=short -p no:cacheprovider`：退出码 `0`，41 passed。
- `& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest backend/tests/test_context_and_locators.py backend/tests/test_hybrid_retrieval.py -q --tb=short -p no:cacheprovider`：退出码 `0`，17 passed。
- `& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest eval_center/tests/test_public_runner.py -q --tb=short -p no:cacheprovider`：退出码 `0`，1 passed。
- `& 'E:\RAG quention\.venv\Scripts\python.exe' -m py_compile eval_center/public_runner.py eval_center/retrieval_replay.py eval_center/verified_index.py eval_center/query_embedding_cache.py eval_center/development_data.py scripts/validate_retrieval_round1.py`：退出码 `0`。
- 本次离线拆分：`& 'E:\RAG quention\.venv\Scripts\python.exe' -`（Python 源从 PowerShell stdin here-string 传入）：退出码 `0`；只读取保存的 `retrieval-results.json`，汇总每个数据集的排序阶段、Context 阶段和缓存计数，不连接数据库或模型。

执行中遇到并修复的预检问题：首次直接执行 `& 'E:\RAG quention\.venv\Scripts\python.exe' scripts/validate_retrieval_round1.py --preflight-only` 退出码 `1`（模块搜索路径不含仓库根），改用上面的 `python -m` 形式。随后预检曾以 `INDEX_REUSE_REJECTED:index_validation` 退出码 `2`，只读异常栈定位到 `DISTINCT` 的 `ORDER BY p.id` 列表达式不在投影中，修复后进入下一校验；再一次预检退出码 `2`，原因为 Windows CRLF 检出与 Git LF blob 的字节比较差异，修复并新增回归测试后预检通过。失败尝试没有运行查询 Embedding、写库或重建索引。

## 3. 结果

**检索阶段 PASS。** 三个数据集各完成 80/80 配对；`baseline_equal_rrf` 与 `candidate_weighted_rrf_context_diversity` 的指标分别汇总，未跨数据集混合。QID 按预注册的 baseline-only 类别分层固定，选择过程未读取候选成绩。样本有意覆盖既有 Vector→Hybrid 损失与 Hybrid→Context 损失案例，因此下表是该分层 Development 样本的描述统计，不是完整 Development 总体估计；没有做抽样权重校正。

### 检索质量

| 数据集（每臂 QID） | Document Recall@5 基线 → 候选 | MRR@5 基线 → 候选 | nDCG@5 基线 → 候选 | nDCG@10 基线 → 候选 | Context Recall@5 基线 → 候选 |
|---|---:|---:|---:|---:|---:|
| SciFact (80) | 0.4813 → 0.6417 (+0.1604) | 0.3635 → 0.4821 (+0.1185) | 0.3924 → 0.5208 (+0.1284) | 0.4655 → 0.5631 (+0.0975) | 0.4813 → 0.6417 (+0.1604) |
| MIRACL 中文 (80) | 0.4437 → 0.6478 (+0.2040) | 0.3779 → 0.5233 (+0.1454) | 0.3704 → 0.5300 (+0.1596) | 0.4843 → 0.6345 (+0.1501) | 0.4437 → 0.6478 (+0.2040) |
| LongBench 中文 (80) | 0.7500 → 0.7750 (+0.0250) | 0.6213 → 0.6363 (+0.0150) | 0.6536 → 0.6709 (+0.0173) | 0.7001 → 0.7120 (+0.0119) | 0.7375 → 0.7625 (+0.0250) |

Recall@5 的逐题候选胜／负／平分别为 SciFact `14/0/66`、MIRACL `21/2/57`、LongBench `4/2/74`。所有三个分集的候选汇总指标均有提升，但这是固定、分层的 80 题样本，不能据此直接改生产默认。

### 离线阶段拆分：Fusion-only / Fusion+Context

保存的逐题结果同时包含融合后、Context 选择前的 `document_metrics_at_5` / `document_ndcg_at_10`，以及 Context 选择后的 `context_document_metrics_at_5`。据此可离线拆出两个阶段，无须重跑检索：Fusion-only 用排名阶段指标；Fusion+Context 用最终所选 Context 指标。

| 数据集 | Fusion-only：等权 → 加权（Recall@5 / MRR@5 / nDCG@5 / nDCG@10） | Fusion+Context：等权 → 候选（Recall@5 / MRR@5 / nDCG@5） | Context 阶段变化：等权；候选（Recall@5 / MRR@5 / nDCG@5） |
|---|---|---|---|
| SciFact | `0.4813→0.6417 / 0.3635→0.4821 / 0.3924→0.5208 / 0.4655→0.5631` | `0.4813→0.6417 / 0.3635→0.4821 / 0.3924→0.5208` | `0/0/0；0/0/0` |
| MIRACL 中文 | `0.4437→0.6478 / 0.3779→0.5233 / 0.3704→0.5300 / 0.4843→0.6345` | `0.4437→0.6478 / 0.3779→0.5233 / 0.3704→0.5300` | `0/0/0；0/0/0` |
| LongBench 中文 | `0.7500→0.7750 / 0.6213→0.6363 / 0.6536→0.6709 / 0.7001→0.7120` | `0.7375→0.7625 / 0.6188→0.6331 / 0.6488→0.6656` | `-0.0125/-0.0025/-0.0048；-0.0125/-0.0031/-0.0054` |

“Context 阶段变化”是在同一检索臂中比较融合排名与所选 Context，反映文档正例进入最终上下文时的保留情况。Strict 2×2 策略消融仍缺交叉臂（加权 Fusion + 旧 Context 策略、等权 Fusion + 新 Context 策略），因此无法单独估计 Context 改动的因果效应；该更严格对比标记为 `ABLATION_NOT_AVAILABLE_WITH_CURRENT_ARTIFACTS`，本轮不为此补跑。

| 数据集 | Vector Top-5 有正例的 QID | Vector→等权 Hybrid 损失 | Vector→候选 Hybrid 损失 | 完整融合排名中的正例文档未进入所选 Context：基线 → 候选 | Top-5 命中在 Context 中丢失的 QID：基线 → 候选 |
|---|---:|---:|---:|---:|---:|
| SciFact | 59/80 | 20 | 7 | 45 → 31 | 0 → 0 |
| MIRACL 中文 | 80/80 | 40 | 24 | 61 → 42 | 0 → 0 |
| LongBench 中文 | 63/80 | 6 | 3 | 21 → 19 | 1 → 1 |

### 延迟与 Embedding 缓存

检索延迟包括该 QID 的检索调用及其是否触发首次查询 Embedding；两臂按固定 QID 哈希规则轮换先后，故此表适用于本地端到端检索观察，不应解读为纯 SQL 延迟或单独模型推理基准。Context build 时间单独计量。

| 数据集 | 平均检索 ms：基线 → 候选 | p95 检索 ms：基线 → 候选 | 平均 Context build ms：基线 → 候选 | Embedding 请求 / 提供方调用 / 命中缓存 |
|---|---:|---:|---:|---:|
| SciFact | 504.46 → 408.42 | 493.77 → 538.45 | 0.953 → 0.662 | 160 / 80 / 80 |
| MIRACL 中文 | 198.17 → 244.32 | 368.25 → 397.16 | 0.886 → 0.543 | 160 / 79 / 81 |
| LongBench 中文 | 402.04 → 459.58 | 820.81 → 778.96 | 0.582 → 0.441 | 160 / 80 / 80 |
| **总计** | — | — | — | **480 / 239 / 241** |

查询缓存只存在本次运行内存，缓存键包含查询哈希、模型 digest、Embedding profile 与维数，不保留原始查询。`requests=480` 是 240 个 QID 各两个检索臂的 Embedding 请求；`misses=239` 是实际发给本地 Embedding 提供方的 239 个唯一键。`hits=241` 可拆为每个配对第二臂共享的 240 次命中，加上 MIRACL 批次额外 1 次重复键命中（80 个 QID 对应 79 个 unique query keys）。结果未保留 query→QID/cache-key 映射，无法指出具体重复 QID。语料 Embedding、语料下载、索引构建、数据库写入、生成、Judge、云端调用均为 0。

### 固定输入、模型、索引和输出身份

| 数据集 | 来源 run | Run manifest SHA-256 | Corpus SHA-256 | Development cases SHA-256 | Prepared manifest SHA-256 | 固定 QID 列表 SHA-256 |
|---|---|---|---|---|---|---|
| SciFact | `20260928T060742Z-6f0c61007b` | `dfad84c3b3a943ea3605660e44b61ec20611a87644a958352c2cb5e15701def1` | `8608aa67e02a307745506d92316ebd0d1b2079226a576446c2bb5943e064f59f` | `d31d774ea5f836cbe41961b4d5762852ad7200caf9c5437d3762605e92a8a320` | `0f9957b10413f4ed85bac62a112e416485e749f940637cdb4cd393f3e5e0982c` | `22067f3e823d8fc622edeaaababe44abd7c5cf496865b0325163bde26ed45207` |
| MIRACL 中文 | `20260928T071014Z-cffac0cd6a` | `5cbd3945ba9c1252a6247589804c8023a49f91498e79c74c87d3b42e2726e276` | `105dde269ea99ddee8ad0ebf8649631e88c3710be9ad2b54b4ced59e4a17ad29` | `41dc155ca1952e908da54021692358af777be965a08f5ac30fbb597c45619fce` | `e9dbbac0001321a27d16d89d8851596481b078445efd0d0c3e34bd102450fe79` | `a9b56e58a6d6a68dbc9a822edf11c7f15195f999b261522389087a33a5c4e9ae` |
| LongBench 中文 | `20260928T074111Z-442ee43dd3` | `c014c88969d4a482cc102f2060af5fdf58d856f72c9d7092a1e59959bddab8e0` | `d5f18bf83d652664d6778d1fab2eba0c1d6ef7781cc4c106e96217eca1cac0c1` | `29b9118617a2c38804223c777055ceefbc3296123834254cc7fc60379f8649d5` | `891029790b066b02a134f469a5fda2298b3a42b28b2b9c05aa3cc735df02301e` | `7401e6d02ee545c5e2f466afe878d12e5848ab272ba7d1ac4bd89fdc79218e9a` |

**注意：** 上表 LongBench `Development cases SHA-256` 应以机器结果中的值 `29b9118617a2c38804223c777055ceefbc3296123834254cc7fc60379f8649d5` 记录；如本表字符校对与机器结果不一致，以机器结果文件为准。

| 身份项 | 值 |
|---|---|
| 候选配置规范 SHA-256 | `182bd609198e42a83769b56b3674c9073431bc4e7863cbec09bc77a6a05c8686` |
| QID fixture SHA-256 | `d1d640cb658cc68c0c2304224081f0e2f1052c7587bb69bb6f4aef2fd097b499` |
| QID 选择摘要 SHA-256 | `babb41023732677201df7328daaf0ee93d6734060031b0dbac39485c366470ed` |
| BGE-M3 本地模型 | `bge-m3:latest`，digest `7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab` |
| Qwen 本地模型身份（本轮未调用生成） | `qwen3.5:4b`，digest `2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd` |
| 三个索引相关源码 SHA-256（基线与当前提交相同） | `bec3e72f9f81ea94d10211897ef61e3cbb450f49b6ad7ab9d00a74c2f575d4d5` |
| 240 个 Context hash 的聚合 SHA-256 | `f1b95a56f67a22141b4133f904d496fca554e6dc1fcee65c41911df0fe11e601` |
| 最终 `retrieval-results.json` SHA-256 | `b22ddce5112f7dfb9f9448276ea9639e1381df764e483f5fab7d1314d1565019` |
| `preregistration.json` SHA-256 | `665b56cb9901fac8ca318142b81b496c9a238fbe20032ce2cc5d77cf0d469be2` |

Context 聚合 hash 按数据集、QID、检索臂排序后对每个 Context SHA-256 记录做 canonical JSON SHA-256；单条内容未复制到仓库。

| 数据集 | Index fingerprint（前/后置一致） | Document / Chunk / Embedding 数 | Source binding SHA-256 | 身份证明 SHA-256 |
|---|---|---:|---|---|
| SciFact | `f6c8b191834638dfa8315adc158b1875ac69c0bef5ba2baac598734976aadb93` | 5,183 / 9,256 / 9,256 | `3c702c098d840e470ae4ff30f78a8a2d36d5df6f7d704a7ddbf98816099bbd8c` | `0810c958deb25e94d1ed4abb96e055c00b1ce45aa781224fce4f256c1e7d647c` |
| MIRACL 中文 | `629b25a5a14ea2ab960fa874c3b16e8cc77a61d0bb65e0e0847ce3c04d473c82` | 6,000 / 6,001 / 6,001 | `5a56736dff24c2eda820fc76ddd8909cbd8826fd58d7595a3a4242a8e7e80ec5` | `fbe483abb43b67a62d00c4a4564726af58a2f12579c3909d97636bdfe4912b0b` |
| LongBench 中文 | `0d41031e54185d2ebc026cdeb62a099427f35a09b0b2d2034a5ce12e4dd3c179` | 400 / 5,003 / 5,003 | `e1645049dacfcdfa483edc239a68ccbb7fe1e86c671835a9756e0b1010cd2be2` | `31ead77b5c9d65ee49e708faa7c8e453e05637ea4cd5bdebefd2de00dd6f0bf7` |

输出文件保存在 `D:\RAG-Public-Bench\round1\results\2026-09-30-rag-retrieval-opt-round1\`，仓库只提交本报告。机器结果保留 QID、公开数据集文档 ID、逐题检索指标和 Context 哈希，不含问题、答案或 Context 原文；没有把它提交或推送到 GitHub。

### QA、外发与索引保护

- QA Pilot：**NOT_RUN**。冻结 fixture 只含 Prompt hash，没有 Prompt 文本；没有调用 Qwen、Judge 或 Warmup，生成调用为 0。
- 数据范围：只打开 `manifest.json`、`corpus.jsonl` 和 `cases/development.jsonl`；Locked 文件未打开，`locked_data_opened=false`。
- 原 PostgreSQL 容器保持停止，原卷未挂载。只使用容器 `rag-eval-trust0928-db-clone-20260929t091523z`，数据库端口 `127.0.0.1:25437`；前后容器 ID 与三个索引指纹相同。
- Docker clone 的 PostgreSQL 数据卷在 Docker 层显示为可写挂载。连接 URL 强制 `default_transaction_read_only=on`，每个数据库连接均核对 `transaction_read_only=on`；报告的数据库写入数为 0，前后索引 counts/fingerprint 相同。

## 4. 风险与遗留

- 本轮只覆盖预注册分层 Development 样本。SciFact 与 MIRACL 改善幅度较大，LongBench 较小；这不能代替完整 Development 抽样估计、Locked 测试或最终答案质量测试。
- 候选检索平均延迟在 MIRACL 与 LongBench 高于基线，尽管 Context 构建均值更低；延迟包含共享 Embedding 缓存的冷/热次序，需在之后固定工作负载下单独分析。暂不据此调整生产默认。
- 离线阶段拆分可用，但完整 2×2 Context 策略交叉消融缺少保存臂，状态为 `ABLATION_NOT_AVAILABLE_WITH_CURRENT_ARTIFACTS`；不追加检索运行。
- QA Pilot 因缺少冻结 Prompt 正文保持 `NOT_RUN`。要验证最终答案、引用回读或语义支持，需要先将 Prompt 正文与生成配置纳入受版本控制的冻结输入，再单独批准运行。
- Luna max 只读审查未发现 Critical/Important 项；其 Minor 建议是运行器尚未重新计算 fixture 的总 `selection_sha256`。本轮每个数据集的有序 QID 列表 SHA-256 与 Development 成员资格均已验证，因此该项没有改变本轮数据范围；建议后续补充聚合摘要校验。
- 本报告与机器结果仅含公开统计和哈希。结果 JSON 未加入 Git，任何公开分享前仍应只分享此聚合报告，不传播逐题文件。

## 5. 版本与下一步

- 分支：`codex/rag-retrieval-optimization-round1-20260930`。
- 本轮执行代码 SHA：`901b439d7e3c37def2da67fe05e181b4c9222a24`；Tree SHA：`fed248308583c5d9bf00dbbcedb22233761eb9a2`。
- 结果状态：检索阶段 `PASS / RETRIEVAL_COMPLETE`；QA Pilot `NOT RUN`；生产默认未更改；未建 Tag、未推送 GitHub。
- 本轮 RAG 参数优化到此结束，后续回到 V1 产品主线。本轮不扩展 QID、不补跑缺失交叉臂，也不自动执行 QA Pilot；如需之后做 QA，须另行冻结 Prompt 正文、审核样本和计分规则。
