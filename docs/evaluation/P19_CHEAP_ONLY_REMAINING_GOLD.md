# P19 — Cheap Only 剩余有效 Gold 测评与失败归因

## 结论：P19_COMPLETE

P9 因本地预算预留不足而未运行的 **17/17 题完成真实 RAG 测评**，每题只执行一次。17 个 Cheap 回答均正常结束，`finish_reason=stop`。这是执行完成，不代表 17 题答案全部正确。

使用正常 FastAPI、实际 PostgreSQL/pgvector、Redis 和 SiliconFlow，未模拟业务 API 或模型。复用已有索引，没有重新上传、解析、切块或入库；因此本轮没有新建 Worker 解析任务，也没有重新验收上传流程。正式查询均使用对应 KB，`document_scope=[]`。

共 69 次实际模型调用、167,408 Token，全部正常结算；新增 UNKNOWN/悬空 reserved 均为 0。历史 632 条 SiliconFlow 账本记录逐条保持不变，包括 61 条 UNKNOWN。未修改生产 Prompt、模型、阈值、解析器或路由。

## 版本、冻结范围与分母

- 准备时基线：`92c90b626667e952c85f7b27cecb8f2ed443f367`。
- 真实执行 SHA：**`bec7c282be4c1cc4b49979e786e949082392d49b`**。
- 两个提交之间只有本轮实验脚本和定向测试，无 backend/frontend 变化。冻结计划的 `baseline_sha` 是准备基线；逐题结果的 `source_code_sha` 是实际执行版本。差异说明见 `source-provenance.json`，不能把两者当成同一个 SHA。
- 本轮评分与 P9 历史 16 题分别保存；不将不同版本拼成统一质量成绩。
- P9 冻结 40 QID、Gold 与 P18 历史结果未改。748 是来源单元数量，非问答题数。

| 数据集 | 本轮新执行 | 保留 P9 已执行 | 冻结 QID 覆盖 | 当前正式索引范围 |
|---|---:|---:|---:|---|
| SciFact | 4 | 4 | 8/8 | 11 篇来源文档的小子集，非官方 5,183 篇全语料 |
| MIRACL-ZH | 3 | 5 | 8/8 | 13 passages，非完整检索语料 |
| LongBench-ZH | 5 | 3 | 8/8 | 8 份冻结上下文；Dureader 与 MultifieldQA 分任务评价 |
| HotpotQA | 5 | 3 | 8/8 | 24 篇小子集；非官方全语料或完整 distractor 评测 |
| MMLongBench-Doc | 0 | 1 | 1/8 | 原 2 份 PDF；其余 7 题本轮 NOT_RUN |
| OmniDocBench | 0 | 不属于 40 QID | 本轮未重评 | 24 页解析 Gold；无官方 QA Gold |

累计问答覆盖 33/40，但仅本轮 17 题属于此执行版本的新成绩。MML row-0434/0959 在后续 MinerU 实验中恢复过文本，不将这两题永久归为能力不足；该实验使用不同 KB/解析条件，不能直接变成本轮分数。row-0430 的视觉计数限制等历史证据保留。详见 `mml-preserved.json`。

所有目标题的有效正例来源均已入当前 KB。MIRACL qrels 的 0 表示不相关，不能算作正例：首次预检误将其视为正例而退出，失败已保留；正式运行前的 `positive-coverage-correction.json` 对 17 题逐项验证 `relevance>0`。冻结 `target-plan.json` 中原始覆盖统计保留，最终覆盖解释以此修正记录为准。未修改 Gold、题目或评分器的 qrels。

## 可以相信的指标

以下 Recall 是 **Top-10 来源身份级 Recall 的逐题均值**；不是答案片段完整度，也不是官方全语料成绩。`context` 指实际交给模型的最终证据来源集合。

| 数据集 | Search Recall@10 | Rerank 后来源 Recall@10 | 最终 Context 来源 Recall@10 | 有效答案指标 | 引用生成题数 / 回读合法条数 |
|---|---:|---:|---:|---|---|
| SciFact，4 题 | 1.000 | 1.000 | 1.000 | 无答案 Gold，仅报告检索指标；不报 QA 准确率 | 2/4；2/2 |
| MIRACL-ZH，3 题 | 1.000 | 1.000 | 1.000 | passage qrels；无答案 Gold，不报 QA 准确率 | 2/3；2/2 |
| LongBench-ZH，5 题 | 0.800 | 1.000，分母 4 个实际 Rerank；另 1 题未调用 | 0.800，分母 5 | Dureader 2 题 ROUGE-L **0.3370173565**；MultifieldQA-ZH 3 题 F1 **0.2142686182** | 2/5；2/2 |
| HotpotQA，5 题 | 1.000 | 0.900 | 0.900 | 完整答案 EM **0/5**，Token F1 **0.1471853081** | 5/5；7/7 |

LongBench 两种任务没有合并成一个准确率。Hotpot 没有真实句级 supporting-fact 映射，因此 supporting-fact/joint 指标 NOT RUN。所有现有引用均验证 KB、document、version、原文 hash、offset 和 quote 等回读绑定；13/13 合法不表示 13 条引用都在语义上支持答案。

来源级命中有局限：SciFact 247 的正确来源命中，但最终 Context 里的 TG2 结构摘要不足以证明问题中的化学损伤结论；Hotpot 地点题只保留 2 个正例来源中的 1 个。不能把这两种情况解释成完整证据召回。

逐题回答、Gold 指标、各阶段排名、原始引用校验、实际请求、Token、HTTP 延迟、finish_reason 和语义判断，见同名 `.results.json` 及原始证据。

## 失败归因

以下按题计数，**类别允许重叠**，不是相加得到的失败总数。语义复核只比较冻结 Gold、实际 Context 和完整回答；未额外调用模型评委，也未对所有世界事实做独立考证。

| 主要问题 | 题数 | 可复核依据 |
|---|---:|---|
| 完整回答缺引用 | **6/17** | SciFact 247/339、MIRACL 91294#0、两个 Dureader、MultifieldQA 741f…；保留无引用输出，没有自动补引 |
| 错误目标、无依据结论或矛盾推理 | **4/17** | SciFact 339 对剂量单调性过度概括；611 对反向因果回答 Yes；MIRACL 91294#0 增加“最密切”比较；Hotpot 地点答错 |
| Context 支持不足、来源冲突或答案遗漏 | **4/17** | SciFact 247；美国节日来源日期互相冲突且回答漏 Arbor Day；UTC 题无最终 Context；Hotpot 地点题缺支持来源 |

另有格式/指标诊断：Hotpot **4/5** 题的目标值与 Gold 一致，但完整回答扩展或表达不同导致 EM=0；这 4 题不能记成 4 个事实错误。MultifieldQA 的日期 `2020年8月27日` 与 Gold `2020-08-27` 也是同一日期的不同表达；保留原官方 F1，不改答案或评分。

确定的地点错误：Hotpot `5a7631cd5542994ccc91870c` 的 Gold 是 **Franconia, New Hampshire**，模型回答 **Hudson, New York**。实际 Context 是人物生平，未支持该地点；其引用身份合法，仍不能证明这个结论。

来源问题只作有依据的有限判断：Dureader 节日上下文出现 Halloween 与 Veteran’s Day 的内部冲突，回答采用其中一处，同时遗漏 Gold 所含 Arbor Day。这支持“来源冲突传播＋覆盖遗漏”，没有把整篇来源标成错误，也没有无证据地全部归咎模型。

SciFact/MIRACL 共 7 题没有答案 Gold；其中可由实际 Context 发现的无依据推断单独列明，不将人工归因转换成官方 QA 准确率。611 输出残留一个 `</ref>`，记为协议表现问题；未将其推断为 Decoder 丢失引用或扩大修复范围。

## 调用、Token、延迟与围栏

| 实际阶段 | 请求数 | 实际 Token | 实际模型 |
|---|---:|---:|---|
| Query Rewrite 等原有查询辅助 | 17 | 43,549 | XingChenAGI/Xing4.0-29B |
| Query Embedding | 19 | 285 | BAAI/bge-m3 |
| Rerank | 16 | 86,227 | BAAI/bge-reranker-v2-m3 |
| 最终 Cheap 回答 | 17 | 37,347 | XingChenAGI/Xing4.0-29B |
| 合计 | **69** | **167,408** | 均为 SiliconFlow |

19 次 Embedding 是原有查询流程的实际调用数；没有为了评分重跑问题。1 题无检索候选，未发 Rerank，仍保留实际 Cheap 输出并纳入答案分母。17 次问答 HTTP 等待合计 144.35 秒，均值 8.49 秒，范围 4.44–15.60 秒；此值不含逐题预检、会话创建、回读和离线评分，不是供应商纯推理延迟。

按现有可信增额流程，本轮建立 1,500,000 Token 的有限本地实验增量：同一账户/月账本从 1,474,244 增至 2,974,244。不是修改 Owner 的无限 SiliconFlow 授权，也不是另建账户获取额度。预留、外发权限、P1SendGate、可信结算均正常执行。

结束后围栏收缩至实际占用 **1,641,652**（含既有 carry-in 14），headroom=0，撤回未使用增量 **1,332,592**。历史 UNKNOWN 不结算、不释放；历史 632 行逐条不变。新增 69 行全部 settled，UNKNOWN=0、reserved=0。DeepSeek 账本及配置不变、调用 0。

16 个真实 Rerank 响应的 `meta.tokens.input_tokens + output_tokens` 均与新增结算一致，image_tokens=0；计量字段未使用 `billed_units`。原始响应与比对见 `rerank-meter-verification.json`。没有为计量单独增加调用。

## 实际修改与执行证据

只新增实验范围脚本/测试：

- `scripts/weknora_port_p19_fence.py`、`tests/weknora_port/test_p19_fence.py`：可信有界增量、锁/CAS、历史保留、关闭回执。
- `scripts/weknora_port_p19_observed_api.py`：复用 P9/P17 的被动观察器；限制最多 17 次最终 Cheap 请求；不读取 Gold。
- `scripts/weknora_port_p19_real.py`：正常 HTTP、KB 空 document scope、逐题预检、请求前持久化 intent、不重发不确定请求、断点记录、只停止自有进程。
- `scripts/weknora_port_p19_score.py`、`tests/weknora_port/test_p19_score.py`：复用固定 Gold、来源/引用校验、固定版本 LongBench 指标；未把 EM=0 直接解释为事实错误。

没有确认需修复的生产 RAG 阻断。实验准备中修正了把 qrels=0 当正例的预检；清理时修正了 venv 子进程退出导致父进程同步退出的停止竞争。首次失败记录保留，未因此重新运行测评。共享生产文件、迁移、依赖变更均为空。

所有 Python 命令均用 `E:/RAG quention/.venv/Scripts/python.exe -B`，在本工作树执行。PYTHONPATH 为工作树、`E:/WK/deps/reader` 和工作树 `var/weknora-port/runtime-deps`；缓存/日志在 E 盘，Tokenizer 在 D 盘。

| 命令（Python 前缀同上） | 退出码 / 结果 |
|---|---|
| `-m pytest -q tests/weknora_port/test_p19_fence.py tests/weknora_port/test_p19_score.py -p no:cacheprovider --basetemp E:/WK/run/p19-20261016/unit-combined` | 0；36 passed，3.20s；离线测试 |
| `E:/WK/run/p19-20261016/prepare.py` | 1；首次 qrels 预检失败保留 |
| `E:/WK/run/p19-20261016/prepare-v2.py` + 正例覆盖修正检查 | 0；17 题有效正例在库，原件 hash/version 一致 |
| `-m scripts.weknora_port_p19_fence open --approval-file E:/WK/run/p19-20261016/owner-approval.txt` | 0；同账户月账本开额回执 |
| `-m scripts.weknora_port_p19_real start` | 0；真实 FastAPI，readyz 正常配置 |
| `-m scripts.weknora_port_p19_real batch --limit 4` | 0；4 题完整链路，检查点通过 |
| `-m scripts.weknora_port_p19_real batch --limit 13` | 0；剩余 13 题完成 |
| `-m scripts.weknora_port_p19_real stop` | 首次 1，清理竞争；修正后 0，56429 关闭 |
| `-m scripts.weknora_port_p19_real start` 然后 `stop`（清理脚本定向复验，无问答） | 0；命令身份核验后停止自有父/子 2 进程；账本仍为新增 69 行，无额外模型调用 |
| `-m scripts.weknora_port_p19_fence close` | 0；headroom=0，撤回剩余额度 |
| `-m scripts.weknora_port_p19_real snapshot` | 0；最终 PG 快照 |
| `-m scripts.weknora_port_p19_score --run-dir E:/WK/run/p19-20261016 --output E:/WK/run/p19-20261016/scoring/results.json` | 0；17/17 COMPLETED，独立质量指标 |
| `E:/WK/run/p19-20261016/audit.py` | 0；生产、历史账本、原文/Chunk/完整向量不变 |
| `git diff --check -- scripts/weknora_port_p19_real.py` | 0 |

**未实现的命令不得报告通过。** 本轮没有运行全项目回归、官方全语料评测、浏览器验收、上传/Worker 重建、真实额外模型评委或 MML/Omni 新调用，均 NOT RUN。

原始证据根目录：`E:/WK/run/p19-20261016`。

- `cases/<identity>/`：冻结请求、intent、完整应用 SSE、消息、逐条原文回读、HTTP 延迟。
- `captures/`、`stages/`、`rerank/`：Search/Rerank/Context 与排名、实际证据。
- `provider-requests/`、`raw/`、`diagnostics/`：过正常发送门后的实际请求、供应商原始回答、Decoder 观察；未保存 headers/密钥。
- `scoring/results.json`、`semantic-review.json`：确定性评分与有证据的人工归因分开。
- `fence-1500000/`：开关回执、授权原文摘要、历史行 hash 及独立只读审计。
- `audit-final.json`：567 个生产文件不变；1,333 个历史证据文件不变；KB 原文/Chunk/完整向量不变；旧 UNKNOWN 和 DeepSeek 不变。
- `process.json`、`process-stop-first.json`：测评 API 子进程 25972/父启动器 42892 的身份和首次清理记录。`process-cleanup-regression.json`、`process-stop-cleanup-regression.json`：清理脚本定向复验的自有进程 43368/43500，未发问答。仅清理本次进程，未停止共享依赖。
- `delivery-validation.json`、`evidence-manifest.json`：17 个请求 Scope、17 个 stop、69 次结算、关闭端口及 477 个证据文件 SHA256。

PG：127.0.0.1:56423，`weknora_python_port_20261013`，system identifier `7696088403504697382`。Redis：127.0.0.1:56424/15，run_id `87b4c2a9f76f9f94b34b5f21fe31a967926f343a`；使用唯一 P19 前缀，未 flush。API 为 56429，Origin 为 56430；未覆盖既有端口服务。

## 下一轮建议与停止

1. 优先针对“回答中的事实/推断—实际证据—引用”做离线反例验证：本轮 6 个缺引用、4 个无依据或矛盾推断；P18 仅加强协议未证明有效，不直接上线其 Prompt，也不自动补引。
2. 用缺支持的 4 题单独验证答案片段覆盖、多跳支持完整性与来源冲突处理；文档级 Recall 已很高，不能据此降低阈值或扩大 Context 后宣布改善。
3. 把完整回答 EM/F1 与目标值/语义诊断并列展示，避免 4 个 Hotpot 正确目标因长句输出被误读成事实错误；本轮保留冻结评分，不更改 Gold。

继续搁置全量语料、模型切换、全局阈值/路由调整、自动解析器路由及 GAG。没有发现本轮必须修复的生产代码阻断；质量和指标局限如上保留。P19 范围已完成，停止执行；未 Push、部署、合并或打 Tag。
