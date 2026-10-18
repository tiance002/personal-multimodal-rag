# RAG-M3 — 延迟、查询理解模型 A/B 与质量回归

**结论：`M3_PARTIAL`，2026-10-18（Asia/Shanghai）。** 真实瓶颈已定位，10 组冻结问题的 20 次 A/B 均经正常 FastAPI、PG/pgvector、Redis 和真实 Provider 完成；DeepSeek 查询理解在本样本显著缩短等待，但出现明确的知识库追问绕检索回归。因此保留现有 Xing 查询理解默认值，不把 B 推广为产品开关，也不为凑里程碑修改检索或路由。原 A/B 批次未改生产路径；续作修复来源限定追问护栏，见补充；另交付可复用的有限验收执行器、离线评分器和定向测试。

## 版本、范围和数据流

实验源码 HEAD `862b0642c3136dcbe12ab1e2914389b67fe8d2de`；固定上游 WeKnora `3e8b0bfc80b845b2d4b2ed683994748741450a97`。最终本地提交用 `git log -1 --format=%H -- docs/milestones/RAG_M3_REPORT.md` 查询，交付消息给出完整 SHA。原有 `.serena/project.yml`、`progress.md` 和六个交接未跟踪文件均未纳入本次提交。

实际链路：React/HTTP → FastAPI 会话与历史 → Query Understanding（改写、意图、追问）→ Embedding / Hybrid Search / Weighted RRF → Rerank → Parent/Child Merge → Context → DYNAMIC 选型 → Cheap 最终生成 → SSE → PG 历史。M3 只在新建隔离会话中改变 `context_config.conversation.query_understand_model_id`：A 使用 SiliconFlow `XingChenAGI/Xing4.0-29B`，B 使用现有 DeepSeek `deepseek-flash`；两组最终回答均为原 Xing Cheap。原问题、KB、`document_scope=[]`、原件/索引、Prompt、Rerank、生成参数和 Gold 不变。A/B 顺序交错，B 未改变全局角色或用户默认值。实际 Provider 身份见每案 `provider-timing`、wire 观察和快照；不凭路由意图推断模型。

冻结计划为 10 个唯一问题、20 个正式请求：SciFact、MIRACL-ZH、LongBench-ZH、HotpotQA、MML-0959、同会话概括追问、同会话检索追问、Omni 第 7 页、MML-0434 和闲聊。计划 SHA256 `5bdc2932f1da071fc10079a508c6a683918844d7b677d10923634ab000624674`；事先冻结的离线评分标准 SHA256 `a4e3323cfcb62e524b11eaea109ec0fdb95c0f70343e6a139d6099b52055d8c2`。执行器从未读取 Gold 作为模型输入。六套仅为现有**子集**，不是官方全语料成绩；Omni 无官方 QA Gold。

## 性能：时间花在哪里

下表为真实客户端 POST 耗时及服务端阶段中位数，单位秒；每组仅 10 个样本。第一条可见进度的中位数 A/B 为 0.559/0.535 秒；第一条**最终答案**为 28.027/15.936 秒。DYNAMIC 会先缓冲 Cheap 候选答案，因此 Provider 的首个内容 delta 不等同于浏览器首个最终文字。

| 阶段或指标 | A：Xing 理解 | B：DeepSeek 理解 |
|---|---:|---:|
| 端到端中位数 | 28.114 | 15.956 |
| Query Understanding | 8.85 | 0.982 |
| Retrieval | 1.041 | 0.740 |
| Rerank | 0.735 | 0.672 |
| Merge / Context | 0.039 / 0.033 | 0.024 / 0.025 |
| 最终生成 | 12.776 | 12.418 |
| SSE `complete` | 28.089 | 15.937 |
| Query Provider 传输全程 | 8.84 | 0.925 |
| 最终 Cheap 传输全程 | 12.807 | 12.367 |
| 端到端 P90（仅描述样本尾部） | 83.670 | 35.919 |

B 在 9/10 对中更快；**配对差值**中位数 8.055 秒。A 的 Hotpot 耗时 195.653 秒，其中理解 188.828 秒；B 对应 15.525/1.080 秒。M2 的 51–111 秒 Query Rewrite 长尾在新增样本中以更严重的同阶段长尾再次出现。当前 Query 请求含约 9,265 字符系统提示、约 2,546–2,565 输入 Token，`temperature=0.3`、`thinking=false`、`max_tokens=150`；这些参数本身不足以证明长尾由 Prompt 大小或思考模式导致。实际 Query Provider 用时几乎覆盖理解阶段，Embedding、Rerank 与本地 Merge/Context 的**时间**较小；但生成阶段在 B 中仍可能达到 31.388 秒（0434），是下一项交互延迟瓶颈。最终生成 A/B 同用 Xing，耗时快慢各占五组，不能把其差异归因于查询模型。

端到端耗时包含历史加载、网络、Provider、服务端和持久化。当前被动观察可取得 Provider HTTP 发送至头/正文、非流式响应完成时间、流式首个有效 delta、阶段与 SSE 时间；非流式 Query 的真实首 Token **不可观测**，报告只用首字节/完整正文时间。首进度在约半秒出现；由于回答缓冲，首个可见答案几乎贴近 complete。未发现必须改变 SSE 缓冲语义的前端等待。小样本和供方负载波动使 9/10 与 P90 都不能证明总体性能或纯模型因果效应。

### 配对实际耗时与 Token

| 冻结问题 | A/B 端到端秒 | A/B 理解秒 | A/B 已报告 Token | 主要质量观察 |
|---|---:|---:|---:|---|
| SciFact-339 | 19.420 / 14.447 | 2.288 / 1.078 | 10,789 / 10,645 | 来源命中相同；两组详细数字均错 |
| MIRACL-91294 | 7.229 / 14.684 | 2.398 / 1.012 | 7,136 / 7,053 | B 唯一更慢；两组最终仅 Summary |
| LongBench-51abd206 | 29.799 / 18.224 | 10.931 / 1.086 | 12,577 / 12,419 | 两组均含“2.2 万人” |
| Hotpot-5a7631 | 195.653 / 15.525 | 188.828 / 1.080 | 7,512 / 7,658 | 两组均失第二跳，答案错 |
| MML-0959 | 27.474 / 25.079 | 10.675 / 0.981 | 8,224 / 8,139 | 两组保留无线电波/微波证据 |
| 概括追问 | 13.972 / 10.135 | 4.352 / 1.076 | 3,749 / 3,590 | 真历史保留；B 两句，A 一句 |
| 检索追问 | 19.985 / 11.691 | 6.980 / 0.944 | 8,154 / 3,826 | **B 错判意图并跳过检索** |
| Omni 第7页 | 28.753 / 16.386 | 6.654 / 0.856 | 33,638 / 32,412 | OCR 仅在 Search，B 编造矩阵 |
| MML-0434 | 43.720 / 35.904 | 17.310 / 0.876 | 8,478 / 8,459 | 两组 Kaggle 片段均被 Rerank 丢弃 |
| 闲聊 | 71.227 / 36.056 | 30.401 / 1.002 | 3,506 / 3,311 | 两组均不检索；回答体验仍有限 |

## 质量回归与采用决策

| 数据集或场景 | Search → Rerank → Context / Gold 与回答 |
|---|---|
| SciFact | 冻结来源 qrels 的 Search/Rerank/Context Recall@10 两组均 1，**没有答案 Gold**。原文的低剂量 PONV/出血分组数值在 A、B 答案均有误，不把来源命中当答案正确。 |
| MIRACL-ZH | 冻结 passage 来源级 Recall@10 两组均 1；邻近星座核心事实有人审原文支持，但最终仅生成 Summary，未直接保留原文 text chunk。没有生成 Gold。 |
| LongBench-ZH | 两组 Context 均含真实“培训 2.2 万人”，核心答案正确；`multifieldqa_zh` 对**完整应用答复**的单题 F1 为 0.083333/0.142857，B 更短不代表新事实收益。 |
| HotpotQA | Franconia, New Hampshire 的第二跳原文已在 Search（A 排 15，B 排 6），但两组 Rerank/Context 均无该来源；A 答纽约、B 答新英格兰，EM 均 0，答案 token F1 为 0.044444/0.05。无真实句级映射，不报官方支持句或 joint 指标。 |
| MMLLongBench-Doc | 0959 两组 Context 保留电离层折射无线电波、微波向卫星发信号，事实覆盖通过，字符串 EM 均 0。0434 Kaggle Cat vs Dog 片段在 Search（A 排 11、B 排 10），两组 Rerank/Context 均 0 且未答 Gold；检到片段不能证明“Coding time”跨页章节归属。 |
| OmniDocBench | 第7页原始数字 OCR chunk `51316a20-67fd-4732-b520-c049999933d0` 在 Search A 第6/B 第5，**均不在 Rerank 或最终 Context**；保留的是同页生成 Summary。A 未给出诊断数字，B 无依据写出错误矩阵及 10/15/20。冻结解析 Gold 的对应矩阵/常数为 `[1,-2,1; 0,2,-8; -4,5,9]` / `0,8,-9`；仅作已有解析标注与额外 RAG 诊断，**不报官方 QA**。 |
| 多轮与闲聊 | 概括追问两组都读到各自真实历史；B 指代解析与两句格式较好，附加泛化无课件支持。**检索追问**明示“根据课件”，A 判 `kb_search`、Search30→Rerank1→Context1；B 判 `follow_up`、Search/Rerank/Context 全 0，并补充信号增强、多路径、纠错编码等无来源内容。闲聊两组均无 KB 检索、无跨库来源；意图标签 A=`chitchat`、B=`greeting`。 |

20/20 正式请求的 SSE 与持久化历史一致，最终回答角色均为 Cheap，无升级；各案 Search/Rerank/Context 的来源身份均落在冻结 KB 范围。由于本轮未改最终模型和路由，M2 的 Expensive 与受控升级真实验收、P32 的 Edge 上传/会话恢复作为既有基线；本轮**没有重新运行浏览器或升级场景**，不把历史结果冒充 M3 新验证。图片/附件理解保留既有离线合同；未重新 OCR、入库或跑视觉 Provider。

**决策：保留生产 A。** B 的 Query 阶段速度优势真实且显著，但显式知识库追问绕检索是本轮范围内的高价值回归反例；Omni 的无数字证据回答进一步显示生成质量不能用速度代替。当前 `query_understand_model_id` 仅有内部会话配置路径，尚无用户可用的正常 API 开关；直接上线 B 或按简单关键词自动选模型均无充分依据。没有改变默认 Cheap/Expensive、Prompt、Rerank 0.3/0.15、路由评分或原生检索主链。若未来要复测 B，应先解决意图和证据回归，用新的独立冻结样本验证；本轮不为得到理想结果重复调用。

## 调用、异常与证据

20 次完成请求 + 1 次**发送前失败**，合计 21/24 次端到端尝试。完成请求共 73/120 次真实 Provider 调用，输入 197,201、输出 4,074、合计 **201,275/450,000 Token**；UNKNOWN 0，usage 缺失 0。A 为 37 调用、103,763 Token；B 为 36 调用、97,512 Token。按供应商：SiliconFlow 63 调用、176,912 Token，DeepSeek 10 调用、24,363 Token。按能力：chat 40 调用、76,957 Token；Embedding 18 调用、295 Token；Rerank 15 调用、124,023 Token。**Rerank 占 Token 多但不是主要延迟阶段**；不估算现金价格或推断 DeepSeek 更便宜。快照 `provider_calls=217` 含旧任务历史，本轮只计已过滤的 `p32_provider_calls=73`；不清除历史 UNKNOWN。

首个 `scifact-339-A` 请求虽收到 HTTP 200，但 SSE 明确返回 `RUNTIME_DURABLE_GATE_REQUIRED`，DB turn 为 error，0 Provider 尝试、0 Token、0 新 UNKNOWN。原因是 M3 被动观察器替换了发送 gate 对象，违反生产 `model.transport.gate is gate` 身份合同。仅修执行器：保留原 gate，包装其 `authorize` 方法；旧失败与操作日志完整保留，证明确无外发后使用**新会话、新 request_id、新证据目录**执行 `attempt2`，原失败计入 21 次，未重发不确定请求。真实运行使用的执行器 SHA256 `2928e60637506a16db06939c1b93080c46dff75afe355e6896f199284e5f5cbd`。收尾又补了离线终态检查：取消/失败 complete 或历史 turn 不能计作完成，提交版 SHA256 `cd13519f392916b150dc10dfd0978a47d8a24487662d3db2c40789cd8d2a07ef`；**该收尾修复没有重新发模型请求**。评分器保留三版只读诊断；最终 v3 把同页 source 与精确 OCR chunk 分开，防止 Summary 来源身份掩盖 OCR 丢失。

原始证据根 `E:/WK/run/rag-m3-20261017`：`plan.json`、`scoring-rubric.json`、`identity-before.json`、`operations.json`、`state.json`、`snapshot.json`、`summary.json`、`scored-final-v3.json`、`cases/*/request.json`、`result.json`、`answer.sse`、`messages.json`、`client-timing.json`、`usage-after.json`，以及 `evidence-332/{stages,captures,configuration,provider-timing,provider-requests}`。最终评分 v3 SHA256 `f9344068b50692bf054a8f2c0e0f3f7ec9d19b6c0a2c881c4ad0dd7eaabc43ca`；早期评分版本仍保留，不能用旧 Omni 诊断字段覆盖原始阶段记录。结果摘要另见同名 `.results.json`，不在 Git 提交大量原始请求或密钥。

## 实际命令、验证与收口

使用 `E:\RAG quention\.venv\Scripts\python.exe`，在本工作树设置 `PYTHONPATH=E:\RAG-Worktrees\weknora-python-port-20261013;E:\WK\deps\reader;E:\RAG-Worktrees\weknora-python-port-20261013\var\weknora-port\runtime-deps`。下列均为本轮实际执行；完整 stdout/原始结果在上述证据根。

| 命令（`python -B` 为上述解释器） | 退出码与结果 |
|---|---|
| `scripts/rag_m3_acceptance.py prepare --plan E:\WK\run\rag-m3-20261017\plan.json` / `start` | 各 0；隔离资源、模型及来源范围预检，API 127.0.0.1:56425 ready |
| 首次 `batch --count 4` | **1**；首案 gate 身份错误，0 外发，完整失败保留 |
| `resume-observer-fix` / 再次 `start` | 各 0；先验证零发送和原始状态，再开新尝试 |
| `batch --count 1`, `1`, `2`, `4`, `4`, `4`, `4` | 各 0；完成冻结 20 个正式请求，分批且无选优重试 |
| `snapshot` / `summarize` / `rag_m3_score.py --output E:\WK\run\rag-m3-20261017\scored-final-v3.json` | 各 0；最终作用域 73 调用、201,275 Token，20 行完成 |
| `stop`，随后检查本轮 API 端口 56425 | 0；本轮 API 已停止，PG/Redis 原实例保留 |
| `python -B -m pytest tests/weknora_port/test_rag_m3_acceptance.py tests/weknora_port/test_rag_m3_score_omni_source_identity.py tests/weknora_port/test_conversation_configuration.py tests/weknora_port/test_kb_model_selection.py -q -p no:cacheprovider --basetemp E:\WK\run\rag-m3-20261017\pytest-final-targeted` | **0，50 passed in 2.76s**；日志 `pytest-final-targeted.log`，离线合同，不冒充真实问答 |

预检时旧配置/查询合同另有 127 项通过、模型身份 9 项通过；收尾执行器终态合同 27 项通过，日志分别为 `query-config-offline.log`、`query-model-identity-offline.log`、`executor-terminal-fix-offline.log`。新 scorer OCR 身份反例 1 项通过。定向测试覆盖配置优先级、模型身份、恢复零发送、SSE 错误/取消与 OCR chunk/source 分离；真实 RAG 由 20 个正式调用证明。未跑全量六套、完整前端构建或新 Edge 流程：**NOT RUN**，因为无前端/生产路径变更。`git diff --check` 在暂存前另行执行。

隔离 PG 为 127.0.0.1:56423、`weknora_python_port_20261013`、system identifier `7696088403504697382`；Redis 为 127.0.0.1:56424/DB15、run ID `dcff9d65a25725574ba0cfbc743d409fac211ef0`。本轮不清库、不重建索引、不修改 Gold/旧账本/UNKNOWN，不 Push、部署、合并或打 Tag。

## 风险与下一步候选

`M3_PARTIAL` 的剩余条件是**没有可安全采用的提速配置**，并非测试没跑通。B 的意图错误会直接损害 KB 限定问答；A 的长尾仍影响交互。P2/P3 体验问题及是否延期由 Owner 决定。最值得下一里程碑先处理的是：①用受控反例校正已有 Query Understanding 对显式 KB 追问的意图合同，再评估 DeepSeek 是否可选；②检查 Rerank 对 Hotpot 第二跳、Omni 精确 OCR 和 0434 目标片段的共性证据损失；③在保留 DYNAMIC 缓冲语义下研究 Cheap 生成尾延迟。三项均**未在本轮自动实施**，也不改变当前默认策略。

## 后续补充（2026-10-18）

在基线 HEAD `cf646e46cd42ef909acc51ed8214305fdf69c16b` 上，修正了 `query.py` 的来源限定请求意图校验：显式“根据/依据/基于/按照 + 文档来源名词”的请求，在没有当前实际文档时优先走 `kb_search`，避免图片/历史绕过路径覆盖检索意图。当前附件 `doc_only`、实际 image-only 与仅追问历史细节仍保留原行为；混合图片与课件请求受保护。Prompt、模型、阈值和路由配置未改。

原始真实保存的 B wire `5ae38cd2...` 中 JSON 意图为 `follow_up`，解析器忠实保留了该值；但原问题“请根据课件回答”未被既有 KB guard 识别，导致带历史时绕过检索。A/B 历史内容不同，不能据此因果归因于模型身份。使用原始 wire 和两轮真实历史的确定性离线重放显示：修复前 `follow_up`/不检索，修复后 `kb_search`/检索，改写文本保持不变。重放证据：`E:\WK\run\rag-m3-followup-20261018\saved-wire-replay.json`。

定向离线测试命令 `E:\RAG quention\.venv\Scripts\python.exe -B -m pytest tests/weknora_port/test_p21_boundaries.py tests/weknora_port/test_pipeline.py -q -p no:cacheprovider --basetemp E:\WK\run\rag-m3-followup-20261018\pytest-resumed` 实际退出码 0，114 passed in 1.52s；日志 `pytest-final.log`。一次早期重定向尝试因证据目录尚未创建而未启动 Python，记为 **NOT RUN**（`pretest-path-setup.txt`）。本补充没有新增云端请求或 Token；累计仍为 73 次 Provider 调用、201,275 Token、UNKNOWN 0。真实修复后 API/浏览器验证 **NOT RUN**。状态仍为 `M3_PARTIAL`，生产默认仍为 A；本修复不构成 B 安全推广证据，Omni 的证据/答案限制仍未解决。
