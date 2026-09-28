# RAG 评测平台最终独立验收审查

日期：2026-09-27。结论：**FAIL，暂不具备开展正式 RAG 参数优化实验的可信评测条件。** 真实本地 RAG 和 ECS 脱敏结果登记链路可运行；评测指标、Gold Evidence 与参数来源仍不足以支持可靠实验结论。本轮只建立隔离合成测试数据、上传两条脱敏实验并形成审查证据，未修改正常知识库或清空 ECS 数据。

## 1. 目标与改动 / 版本

- 审查基线：主工作区 `E:\RAG quention` 的 `main` HEAD 为 `a0a62e8ee78be4a176b81e3f61cf5b84dd6d364b`；另一个 `codex/eval-center` 工作树也在该 SHA，但评测中心代码、脚本、数据集改动**均未提交**。因此 ECS 运行内容不能仅凭该 Git SHA 复现。
- ECS 当前应用路径：`/srv/rag-eval/releases/ad2fc3ec7d66dc591a95c9da22170c2f1f326d6376cf7a482f90a212420c7142`，`contracts.py`/`store.py`/`server.py` SHA256 分别为 `8ee5afc3…7f97f6`、`32899cea…da12`、`ebe4a616…f1bd82e`，与未提交工作树文件逐一相同。发布目录名不是 Git commit。
- 实际修改：仅新增本报告；隔离审查脚本、原始结果与脱敏包位于被 Git 忽略的 `E:\RAG quention\var\audit\final_9c960258\`。在已有测试 Compose PostgreSQL 中新建数据库 `rag_eval_audit_9c960258`，其迁移到 `0013_message_run_link`，不连接正常知识库。
- 云端新增两条本轮合成实验：A `f61fb37a-fb21-4625-a61e-b126a62f4ddf`，B `a1d3c1ff-898b-49e0-b8d4-9a48781b6a89`。两条均标为 `partial`，不伪称生成质量已评估。

## 2. 已实现能力与真实链路

评测脚本复用 `HybridRetriever`，但当前正式脚本 `scripts/evaluate_retrieval.py:19-34` 和 `scripts/evaluate_rag_quality.py:26-42` 都用内存 fixture；前者没有 BGE embedding，后者 `answer_gateway=None`，没有真实 Qwen 回答。生产 API `backend/app/bootstrap.py:80-101` 会接入 PostgreSQL、BGE-M3 和 Ollama。评测模块与生产 API 在代码层隔离，Langfuse 不可用时固定 Quick 主链路仍可运行；云端是只读查询服务，导入使用独立 CLI/SQLite。

本轮另外编写**审查专用、非平台内置**驱动，在隔离库里通过真实 HTTP 上传 2 份合成 Markdown 到 A/B 两个 KB，真实 Worker 索引，PostgreSQL/pgvector 与 BGE-M3 混合检索（每条检索结果的 `sources=('keyword','vector')`），Quick 模式经 Qwen3.5:4b 返回“2024 预算 1000 credits [E1]”，A/B 的 `E1` 回读均为 HTTP 200。A 的预算/交付文档分别 3/3 块，B 为 4/3 块；相应 embedding 行数 3/3 与 4/3，证明切分参数真正进入了 Worker。两组源文档 SHA256 相同，分库索引；5 条可回答样例包括双证据跨文档样例，另有 1 条无答案样例。独立审查脚本按内容锚点取得 document/version/locator，再映射到各自的 chunk ID；这**不代表平台已实现 Gold mapper**。

| 独立审查指标，5 条可回答样例 | A: chunk 1200/overlap 120 | B: chunk 700/overlap 70 |
|---|---:|---:|
| Hit@5 / Recall@5 | 1 / 1 | 1 / 1 |
| Precision@5 / F1@5 | 0.24 / 0.380952 | 0.24 / 0.380952 |
| MRR@5 / MAP@5 | 0.7 / 0.716667 | 0.8 / 0.816667 |
| nDCG@5 | 0.791057 | 0.864871 |
| 检索平均墙钟延迟，含查询 embedding | 175.98 ms | 105.05 ms |
| Quick HTTP 端到端单次 | 1726.58 ms | 1393.9 ms |

上述差异来自实际排序，不能据此推断 B 具有稳定的性能优势：样本很小、运行顺序固定、模型缓存状态不同，也没有 p95/p99。生产 Quick 的 `top_k=8` 默认值与审查检索的显式 `top_k=5,candidate_k=32` 不同；云端包只代表这 5 条检索测量，不代表 Quick 的 top-k。无答案样例单独标为 `not_evaluated`，未掺入可回答样例均值。

实际版本探针在第三个隔离 KB 中上传同一文档的两个版本，原文 `1000 credits` 改为 `1100 credits`，两个 Worker 均成功；active version 切到新版本，旧证据仅在旧版本块中，新证据仅在新版本块中。记录见 `var/audit/final_9c960258/version-probe.json`。这证实存储侧版本隔离，**未解决平台 Gold 映射缺失**。

独立拦截同一 Quick Chain 对 Ollama 的原始响应可取到真实 `prompt_eval_count/eval_count`：A 答案调用 561/22 tokens，B 577/22 tokens；两次还各有 `/api/embed` 输入计数 12。该拦截是额外一次本地调用，不是上述 HTTP 回答的原始 usage，也未进入平台报告；A 的 12.37 s 包含模型冷启动，B 的 1.15 s 是暖机后的单次调用。详细原始响应元数据见 `var/audit/final_9c960258/instrumented-usage.json`。

## 3. 逐项指标与口径结论

| 目标指标 | 平台实际状态 / 结论 |
|---|---|
| Hit@K、Recall@K、MRR@K | **CONDITIONAL**：fixture 脚本只算固定 K=5，已用两条人工样例核对有答案时的基础公式；无答案 `expected=[]` 被计为 hit 0、recall 0、MRR 0，污染总体均值并使状态 FAIL。没有有效性分层。 |
| Precision@K、F1@K、MAP@K、nDCG@K | **NOT IMPLEMENTED**：schema 接受 `precision_at_5`、`ndcg_at_5` 外部数值，但正式评测脚本不计算；F1/MAP 甚至不在云端 allowlist。审查驱动的数值不是平台功能。 |
| Context Recall / Precision、Evidence Coverage | **NOT IMPLEMENTED**：无单独最终上下文分母、覆盖映射或与原始候选分层的统计。`target_coverage` 属固定 Quick fixture，不等于 Gold Evidence Coverage。 |
| Answer Accuracy / Relevance / Faithfulness / Completeness | **NOT IMPLEMENTED**：无真实答案 Gold/Judge 主路径。allowlist 的 `faithfulness`/`llm_judge_score` 只是接收数字。Judge 超时/无评分也没有可审查的分母与排除规则。 |
| Citation Accuracy / Refusal Accuracy / Hallucination Rate | **NOT IMPLEMENTED**：目前可回读引用 ID；未验证引用是否支持对应陈述，未计算拒答正确率与幻觉率。 |
| Exact/Near Duplicate Rate、Redundancy@K、Unique Evidence Coverage、Context Token Savings、False Merge Rate | **NOT IMPLEMENTED**：没有去重评测，年份、数值和版本的误合并率无法判断。当前 RRF 以 chunk ID 融合，不等于语义近重复测量。 |
| Token 统计 | **NOT IMPLEMENTED**：`OllamaGateway._usage_details` 能读原始 response 字段，但 `answer()` 只向调用方返回字符串；usage 仅可写到可选观测。缺 Query/Answer/Judge/Evidence/Context 分项、本地持久化与合计。字符数或 `estimate_tokens` 不可替代真实 Token。 |
| 时延 | **PARTIAL**：fixture 报告有单次 `latency_ms`，审查驱动测了检索和 HTTP Quick；平台缺上下文构造/生成/端到端分段与 p50/p95/p99，冷热启动和网络时间未分层。 |

人工 fixture：`multi` 查询命中 `a,b`，结果 `a,b,c`，脚本给 Hit=1、Recall=1、MRR=1；`no-answer` 的 Gold 与检索均空，脚本给 Hit=0、Recall=0、MRR=0，报告总体 0.5 且退出码 1。未把无答案正确拒答作为独立指标。结果见 `var/audit/final_9c960258/metric-report.json`。

## 4. ECS / Dashboard / 安全与同步

- ECS `rag-eval.service` enabled/active，`User=rag-eval`，systemd 单元仅监听 `127.0.0.1:8787`，本机探测公网 `120.55.115.162:8787` 失败。SSH 22 可重新连接；Dashboard 经 SSH 隧道实际浏览器打开，页面显示两条、dataset `audit-synthetic-9c960258-v1`、状态 `partial`、每条 6 样例、MRR 为 B=0.8/A=0.7。旧 study-plan 服务单元查询无匹配。
- 导入 A/B 均返回 `imported`；再次上传 A 返回 `unchanged`。同一 ID 修改 MRR 的包通过本地结构校验，但 ECS 导入返回退出 1、`experiment_content_conflict`，原记录未覆盖。100 字节中断上传暂存文件导入返回退出 1、`invalid_bundle`，正式实验数仍为 2；本轮冲突和中断暂存文件已定向清理。重启服务后短暂 `curl (7)`，重试健康检查通过，原两条实验仍在。
- 2026-09-27 15:39 UTC：ECS 2 CPU，RAM 3627 MiB、used 542 MiB、available 3084 MiB；根盘 40 GiB、used 3.1 GiB、available 35 GiB（9%）；服务 MemoryCurrent 12,632,064 bytes。SQLite 所在目录 0700、DB 0600，服务 `NoNewPrivileges`、`ProtectSystem=strict` 等限制有效。服务内只读 API 无远程写接口，上传需既有 SSH 权限。
- 包 A/B 分别为 2881/2873 字节，只包含脱敏的 opaque case ID、数字、状态、配置和哈希；扫描未见合成文档正文、问题、chunk ID、私钥格式或本机用户路径。评测中心代码/脚本的模式扫描也无私钥或常见 token 字面量命中。正常 KB 的 `cloud_allowed=false`、全局外发关闭；本次没有上传私人资料或调用第三方评估平台。Langfuse `trace()` 在全局禁用或任一 KB 不允许时返回空；真实网络外发包级抓取 **NOT RUN**。
- 从本地原始结果 → 脱敏包 → ECS 详情 API，逐字段核对两组各 5 条，共 10 条的 status、Hit、Recall、Precision、MRR、nDCG 与 retrieval latency，全部一致；Dashboard 页面的样本数/状态/MRR 亦与详情 API 相符。证据 `var/audit/final_9c960258/five-record-trace.json`。这证明**展示与上传值一致，不证明上传值真实**。云端没有重新计算原始 Gold/答案指标。

## 5. 问题分级、复现、最小修复

| 级别 | 发现与复现 | 根因 / 最小修复与回归 |
|---|---|---|
| **P0** | 可把 A 包的 `top_k=5` 改成 `9`、重算 `config_hash`，保留所有实测数值，本地 `eval_center.cli validate` 仍返回 `valid`。还可把聚合 MRR 0.7 改为 0.701 而通过结构校验。 | `scripts/package_eval_bundle.py:65-159` 与 `eval_center/contracts.py:136-161` 仅验证用户提供 JSON 的格式/哈希，不核对运行轨迹或按 case 重算指标；`backend/app/bootstrap.py:101` 生产 top-k=8。应由实际运行器生成不可任意声明的 effective config、模型/索引指纹与指标，入库前重算确定性汇总；回归用故意伪造 top-k、汇总值必须拒绝。 |
| **P0** | 换切分/版本后 fixture Gold 只保留 `expected_chunk_ids`；版本探针显示旧证据不在新 active version。 | `scripts/evaluate_retrieval.py:32` 直接以 chunk ID 为真值，缺 document/version/locator/span Gold mapper。以不可变原文锚点和版本策略建立 Gold，针对跨段落、多证据、跨文档、版本切换运行映射与人工裁定回归。 |
| **P1** | 人工无答案 fixture 的正确空检索被记为零分与 FAIL；大部分要求指标无计算器。 | `scripts/evaluate_retrieval.py:34-45` 用全部 case 作分母；`eval_center/contracts.py:30-38` 只有数值 allowlist。先冻结指标口径和样例类别，给无答案独立 refusal/false-positive 统计，补 Precision/F1/MAP/nDCG 与完整上下文/生成指标的最小真实计算器；用手算、空结果、多个正确证据、重复证据与 Judge 失败样例回归。 |
| **P1** | 真实 Ollama usage 只有额外拦截才能取得；Dashboard 无 tokens、阶段延迟和分位数。 | `backend/app/adapters/models/ollama.py:111-157` 将 usage 留在可选观测，不写本地评测结果。将各阶段 usage 与单调时钟时间作为本地结构化返回/事件；分别标注真实、估算、字符和冷启动；同一 HTTP run 对照原始 Ollama 响应回归。 |
| **P1** | ECS 内容来自未提交工作树，`git_sha` 只指共同基线；比较接口只校验 dataset/corpus/mode。 | 提交发布源码并记录 Git SHA、bundle/Gold hash、模型 digest、索引和 effective config；`eval_center/store.py:248-267` 增加 Gold/模型兼容性检查或显式“不具可比性”。用不同 Gold Set/模型的两包回归。 |
| **P2** | systemd restart 后 `is-active=active` 时即时首个 curl 返回 7；下一次成功。 | 发布验收加短暂 readiness 重试与明确超时，不以刚启动的 active 状态代替 HTTP 健康。 |

P0/P1 均与可信度相关，不能按低频缺陷延期。无 P2 延期批准；项目负责人尚未批准本次验收或任何延期。

## 6. 执行命令与结果（关键证据）

下列为本轮实际调用，SSH 私钥参数与本机绝对路径在仓库报告中省略；原始执行回执在本会话。**未实现的命令不得报告通过。**

| 命令或操作 | 退出码 / 关键输出 |
|---|---|
| `docker compose -p ragv1final0927 -f deploy/compose.yml exec -T db psql -U rag -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE rag_eval_audit_9c960258 OWNER rag"` | 0，`CREATE DATABASE` |
| `python -m alembic -c alembic.ini upgrade head`（进程环境指向隔离 DB） | 0，迁移至 `0013_message_run_link` |
| `python var/audit/final_9c960258/setup_audit.py` | 0，2 KB、4 文档上传 HTTP 201/202 |
| `python -m backend.app.workers.ingestion --once`（A 1200/120，共 2 次；B 700/70，共 2 次） | 初次 A 因 Ollama 未运行退出 1、`ProviderUnavailable`；按实际 `E:\llm_load` 模型目录恢复 Ollama 并 retry 后，四次均 0、`succeeded/ready` |
| `python var/audit/final_9c960258/run_real_eval.py` | 首次因审查脚本将字符串引用当对象退出 1；修正后退出 0，A/B 各 6 条真实检索及一次 Quick 回答，原始结果 `real-A.json`、`real-B.json` |
| `python var/audit/final_9c960258/instrument_usage.py` | 0，A 561/22、B 577/22 实际答案输入/输出 tokens；仅审查临时插桩 |
| `python var/audit/final_9c960258/version_probe.py` | 0，旧/新版本各 1 块，active version 正确切换 |
| `python -m scripts.package_eval_bundle --report … --manifest … --config … --output …`（各一次） | 0/0，A/B 脱敏包生成；直接执行脚本路径曾因模块路径错误退出 1，改用 `-m` 后成功 |
| `python -m eval_center.cli validate A-bundle.json`、`… B-bundle.json` | 0/0，digest `e6a8f39e…` / `86928859…`；伪造 top-k 包与篡改聚合 MRR 包也返回 0，说明结构校验不足 |
| `upload_eval_bundle.ps1 … -Apply`（A、B、A 重复） | 0/0/0，`imported/imported/unchanged`；冲突包退出 1，远端 CLI 明确 `experiment_content_conflict` |
| `python var/audit/final_9c960258/trace_records.py` | 0，10 条原始→bundle→ECS 字段比较通过，前 5 条见 `five-record-trace.json` |
| `python -m scripts.evaluate_retrieval --dataset metric-cases.jsonl --fixture metric-fixture.json --report metric-report.json --suite-version audit-artificial-v1` | 1，人工无答案样例被错误计零，报告 `FAIL`（预期暴露缺陷） |
| `python -m pytest -q eval_center/tests` | 0，41 passed；**仅单元/合成测试** |
| `python -m pytest -q backend/tests/test_egress_matrix.py backend/tests/test_citation_resolution.py` | 0，2 passed；另一组含 PostgreSQL scope 的尝试无及时进展，已中断，记 **NOT RUN**，不当作通过 |
| `systemctl restart rag-eval.service`，随后 `curl --retry 5 --retry-delay 1 --retry-connrefused -fsS http://127.0.0.1:8787/healthz` | 重启 0；即时 curl 7；重试 0，`{"status":"ok"}`，2 条实验仍在 |
| 本机 `Test-NetConnection -ComputerName 120.55.115.162 -Port 8787` | 命令 0、`TcpTestSucceeded=false`；远端 `ss -ltn` 显示仅 loopback 8787 |

完整发布回归、恢复演练、Judge/Ragas 联网评估、真实 Langfuse 外发抓包、跨语料/大样本统计与 p95/p99 压测均 **NOT RUN**；对应功能或环境前提尚未具备。本轮没有执行不存在的 `make verify-release` 等计划命令。

## 7. 验收状态与下一步

验收状态：**FAIL**。优先完成并提交真实 PostgreSQL/pgvector+BGE/Qwen 评测运行器，使 effective config、Gold 映射、可回答/无答案口径和确定性指标在同一 run 内可追溯；再接入真实 usage/阶段时延，拒绝伪造汇总与错误比较。以本轮 5 条人工 Gold、1 条无答案、版本探针和 A/B 两配置作为最小回归，再请项目负责人重新验收。未创建 Tag，未自称里程碑通过。
