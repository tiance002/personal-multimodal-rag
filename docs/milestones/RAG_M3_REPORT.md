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

## M3.1 来源边界与证据流转（2026-10-18，首次收口／授权前）

**状态：`M3_1_PARTIAL`；A 离线通过，C 保存回执回放通过，B1 `BLOCKED_AUTHORIZATION`，B2 `BLOCKED_INPUT_CONTRACT`。** 本轮基线为 `fee2229f97725df18ed2da4d4f94c87c130758c6`，生产仍用 A。历史 20 个请求、73 次 Provider、201,275 Token 与旧 UNKNOWN 保持原记录；本轮完整问答、Provider 请求、Token、新 UNKNOWN 均为 **0**。以下分数属于旧 M3 真实响应的新离线诊断，不是新增模型结果。机器结果见 `RAG_M3_1_DIAGNOSTICS.results.json`。本地提交的完整 SHA 可用 `git log -1 --format=%H -- docs/milestones/RAG_M3_REPORT.md` 查询。

### 来源边界与最小修复

- `query.py::resolve_intent` 仅在来源短语紧邻“不要／不用／无需／不必”等直接否定时，将该短语排除于意图护栏；不改原问题、Rewrite 或 Prompt。“不要根据课件回答，只解释一般原理”不再强制检索；句中另有正向搜索 KB／依据其他来源的要求仍检索。“不要只根据课件”也仍保留来源要求。这不是通用否定理解。
- 对明确“根据课件／讲义／教材评价上传的方案”的双角色表达，即使内部状态有附件、模型返回 `doc_only`，也执行检索。当前附件自身总结、纯图片分析、历史第二点展开和已有 KB 约束反例保持通过。这里只依据原请求中明确的来源／评价对象关系，不推断附件文件身份。
- `service.py::prepare_turn` 另修复了关联缺陷：SearchNothing 的 model fallback 原本丢弃当前附件。现在将其保留在最终 **user 数据内容**，不将附件放入 system 指令。正常有证据路径本已由 `context.py::into_chat_message` 保留附件。

实际入口核对发现：`http_api.py::MessageCreate`、`OrdinaryTurnRequest`、React `runPortChat` 和 runtime 状态装配均没有消息级 `images/attachments` 传输；额外字段被 `extra=forbid` 拒绝。页面上传是 KB 入库，不是当前消息附件。因此带 `attachment_prompt` 的 A2 反例属于已有内部组件合同，尚不能冒充真实浏览器附件验收。内部附件对象只有名称、类型和文本，没有稳定文档 ID；对含混的“当前附件是否就是所需来源”不猜测，沿用既有 `doc_only`。所选 KB 文档仍受原 `document_scope` 限制，不为参照另一份资料擅自扩大范围。

### B1／B2 的真实执行状态

| 场景 | 状态与理由 | 实际 Provider／Search／Context／答案 | 本轮用量 |
|---|---|---|---|
| B1 真实课件追问 | `BLOCKED_AUTHORIZATION`：已请求独立于旧 M3 的本轮有限授权，收口时尚未收到确认。 | 全部 `NOT RUN`，没有推断实际模型身份。 | 0 请求、0 Token、0 新 UNKNOWN |
| B2 浏览器图片＋课件 | `BLOCKED_INPUT_CONTRACT`，同时受授权限制：正常消息 API／UI 不接受图片附件。 | 浏览器请求、视觉 Provider、SSE／持久化比对均 `NOT RUN`。 | 0 请求、0 Token、0 新 UNKNOWN |

只读预检成功：PG `127.0.0.1:56423/weknora_python_port_20261013`、system identifier `7696088403504697382`；Redis `127.0.0.1:56424/15`、run ID `dcff9d65a25725574ba0cfbc743d409fac211ef0`、PING 正常。目标课件 KB `7f1d720d-9561-446f-a8cc-6a562c339d23` 的 `cloud_allowed=true`；现有 Cheap／Embedding／Rerank／Expensive 配置有凭据记录，但未据此声称已解密或真实调用成功，VLM 角色未配置。当前普通 `LeasedSendGate` 保留运行租约、模型／KB 权限、外发许可和持久化调用保护；P31 已撤销账户 Token Authority，本轮没有恢复。旧 M3 实验限制也未复用为本轮授权。未启动 API、Worker、前端或新容器，数据库操作均只读。

### Omni 精确 OCR 的首次丢失

目标 OCR `51316a20-67fd-4732-b520-c049999933d0` 与同页 Summary `4427641f-3190-598a-98a2-8f8d63d55d0e` 均属于 document `dfeb8157-6bf9-4fe2-927a-f38d40b342a0`，必须按精确 chunk ID 区分。

| OCR 环节 | A | B |
|---|---|---|
| Search | `PASS`，排名 6，score 0.014596036585365853 | `PASS`，排名 5，score 0.014427767354596622 |
| 实际 Rerank 输入 | `PASS`，index 5（从 0 计） | `PASS`，index 4（从 0 计） |
| `enriched_passage`／真实 wire | `PASS`，逐项一致；包含原有矩阵 OCR，文本及 SHA 在诊断 JSON | `PASS`，与 A 的目标输入文本一致 |
| 原始模型回执 | `PASS`，排名 2，0.1673908233642578 | `PASS`，排名 2，0.27084100246429443 |
| `filtered()` | **`DROPPED`，低于 0.3** | **`DROPPED`，低于 0.3** |
| accepted | `DROPPED`，上一步已排除 | `DROPPED`，上一步已排除 |
| MMR／rerank_top_k | `DROPPED`（未获输入），不是首次丢失点 | `DROPPED`（未获输入），不是首次丢失点 |
| Merge／Context | `DROPPED`（上游缺失） | `DROPPED`（上游缺失） |

Summary 是两组 index 0／排名 1，原始得分 A `0.5140959024429321`、B `0.7705795764923096`，通过 0.3 并最终保留。**0.15 只在整批无通过者时检查 Top1**；它不保护已有其他通过者时的第二名。本例首次丢失明确在阈值过滤，未发现 Python 索引映射、accepted、MMR 或 Merge 独有 Bug。回放直接调用产品 `rerank.py`，所有实际候选文本均与产品 enrichment 相同；Omni／Hotpot 四组输出匹配，0434 两组按预期触发 SearchNothing。原 capture 未单独保存 threshold/top_k，`0.3/0.15/30` 的依据为当时报告和 runtime 默认，并明确以保存输出匹配交叉验证。

旧证据对照同样定位在过滤阶段：Hotpot 第二跳原文 `31417c7c-7abf-4723-956a-74bf7ee14683` 的 A/B 原始分数为 `0.002824527444317937`／`0.02049975097179413`；0434 的精确 Kaggle chunk `085643dc-5ebb-4b8a-b7f1-75427da40015` 位于 A/B 输入 index 10／9，得分 `0.001054094755090773`／`0.004162943456321955`，整批 Top1 也低于 0.15。它们与 Omni 的首次丢失阶段相同，分数和关联性表现差异很大，不能用统一降低阈值保证改善。Omni 可作为将来有限原生阈值对照的候选，必须同时保留噪声／成功样本；本轮默认配置不变，也不将 Summary 来源命中算作 OCR 数字证据命中。

### 本轮实际验证与遗留

使用本报告前述解释器和 `PYTHONPATH`，证据根 `E:\WK\run\rag-m31-20261018`：

| 实际命令 | 退出码与证据 |
|---|---|
| `python -B -m pytest tests/weknora_port/test_m31_source_constraints.py -q -p no:cacheprovider --basetemp E:\WK\run\rag-m31-20261018\pytest-before` | **1**，5 failed／5 passed；`pytest-before.log`，复现否定、双来源和 fallback 丢附件 |
| `python -B -m pytest tests/weknora_port/test_m31_source_constraints.py tests/weknora_port/test_p21_boundaries.py tests/weknora_port/test_pipeline.py tests/weknora_port/test_service.py -q -p no:cacheprovider --basetemp E:\WK\run\rag-m31-20261018\pytest-final-user-data` | **0，148 passed in 2.13s**；`pytest-final-user-data.log`，离线组件／服务合同 |
| `python -B E:\WK\run\rag-m31-20261018\replay_evidence.py` | **0**，6 组旧真实回执产品回放；`omni-evidence-flow.json`，未新增云请求 |
| `python -B E:\WK\run\rag-m31-20261018\preflight.py` | **0**，只读资源预检；`preflight.json`／`preflight.log`。初次诊断用错 Windows Proactor loop，改用 SelectorEventLoop，未改生产代码 |
| `git diff --check -- backend/weknora_port/retrieval/query.py backend/weknora_port/service.py tests/weknora_port/test_m31_source_constraints.py docs/milestones/RAG_M3_REPORT.md docs/milestones/RAG_M3_REPORT.results.json docs/milestones/RAG_M3_1_DIAGNOSTICS.results.json` | **0**；最终提交前执行 |

剩余阻断：B1 为待 Owner 确认的新外发授权；B2 为消息级图片／附件传输功能缺失，阻断该指定验收场景，但不是本轮引入的普通文本 RAG 故障。附件身份含混表达仍属未解决合同限制。Omni／Hotpot／0434 为已定位的证据选择质量问题，本轮没有授权更改默认策略。下一步最小建议是先取得本轮 B1 授权完成一次真实追问；B2 的消息附件入口另行确定范围。原 `.serena/project.yml`、`progress.md` 和六个未跟踪交接文件保持原状。本轮仅本地提交，停止于 M3.1。

## M3.1 授权后 B1 真实验证（2026-10-18）

Owner 随后明确确认“我确认授权进行真实问答”。本节记录新增真实验证，前节授权前的未运行状态作为历史保留。**B1 已通过真实链路验收；M3.1 仍为 `M3_1_PARTIAL`，仅 B2 的输入合同阻断尚在，生产默认仍为 A。** 本次实际源码为 `cc131bf4123da6b1d1616effc74298b468a1d23e`，未增加生产代码修改，也未重复执行已通过的离线测试。Owner 另明确授权完成后由 6luna 推送当前分支。

新建隔离会话 `6d3da3ce-e15d-4f0c-926f-3fae43cc2302`，通过正常 API 建立，再仅在此新会话复制原 B 的配置和两轮真实历史；没有复制或伪造 `port_turns`。原历史四条消息的内容哈希、顺序和权限范围均核对一致；实际 Query Understanding 请求中的两轮历史与原 M3 B 请求**完全相同**。原 session／messages、旧账本、文档／切块／索引均未改写。KB 仍为 `7f1d720d-9561-446f-a8cc-6a562c339d23`，`document_scope=[]`。

本次 run `c0696630-2e52-4c57-bcf4-e82b9231fc7e` 的真实 DeepSeek `deepseek-flash` 回执仍为 `intent=follow_up`，原始响应 SHA256、请求身份和回执路径在诊断 JSON 中。运行时来源护栏将其解析为有效 `kb_search`／`needs_retrieval=true`，未操纵模型输出。正常链路实际为 **Search 30 → Rerank 1 → Merge 1 → Context 1**；理解 0.746s、检索 2.127s、Rerank 0.700s、Merge 0.192s、Context 0.331s，端到端 **17.296s**。这是单次功能回归，不能作为新延迟 A/B 结论。

最终 Context 为 1,279 字符，实际 Cheap 请求含证据 chunk `df6098cb-68e6-4546-b9e0-8682b4bf19e9`，document `8fdbe2af-9cc0-4b4a-a554-352a61ca5252`，source_version `3ff26984-be2a-4c7e-8b8f-bb8140c156f8`，offset `[10575,11061)`。关键原文为 `Problems that can occur: -signal can spread out -waves can refract when passing through different layers of atmosphere -makes it difficult to send signals when you want to -drop in quality`。只读 DB 回读确认 chunk 正文、document／KB／version 与实际请求证据一致。

Cheap 回答了信号扩散、大气层折射使信号难以准确发送、信号质量下降，核心事实受到该段支持。回答中的“覆盖范围扩大”和将质量下降归因为前述因素属于解释性推论，单独标记，不当作原文逐字事实或官方 Gold 得分。实际最终角色 Cheap、`finish_reason=stop`、无升级、非 fallback；SSE 与持久化答案一致。公开引用按既定产品配置关闭，未显示可点击引用属于预期行为，不计为本次失败。

| 本次实际调用 | 输入 Token | 输出 Token | 合计 Token |
|---|---:|---:|---:|
| DeepSeek `deepseek-flash` Query Understanding | 2,607 | 27 | 2,634 |
| SiliconFlow `BAAI/bge-m3` Embedding | 16 | 0 | 16 |
| SiliconFlow `BAAI/bge-reranker-v2-m3` Rerank | 4,408 | 0 | 4,408 |
| SiliconFlow `XingChenAGI/Xing4.0-29B` Cheap | 1,272 | 105 | 1,377 |
| **合计** | **8,303** | **132** | **8,435** |

只执行 **1 次完整问答、4 次 Provider 请求**，均 HTTP 200／usage REPORTED，新增 UNKNOWN 0、usage 缺失 0。新授权上界为最多 2 次完整问答、12 次 Provider、100,000 已报告 Token，本次未使用旧 M3 围栏；执行完成后停止自有 API，并关闭本次未用执行空间。旧账户账本各表哈希和既有文档／chunk 哈希均保持一致。B2 没有发送请求；授权已具备，但正常 API／React 的消息图片入口仍缺失，不能将内部状态测试或 B1 文本请求计为 B2 浏览器验收。

实际命令 `python -B E:\WK\run\rag-m31-20261018\run_b1.py prepare`、`start`、`query`、最终 `snapshot`、`stop` 均退出 **0**；`query` 只执行一次。首次收尾 `snapshot` 退出 **1**，原因是复用诊断工具需要 case 的 `tenant_id` 而包装器漏记该字段；仅给新实验状态补上已知 tenant 332 后完成只读快照，原始请求／回执未改、问答未重发。离线 `summarize_b1.py` 最终退出 **0**；初版分析器误把排名二元组当成对象的 TypeError 保留记录，修正为读取原有 typed stage 行，没有修改产品流程或新增调用。

新增证据根 `E:\WK\run\rag-m31-20261018\live-b1`：`owner-authorization.json`、`plan.json`、`history-seed.json`、`cases/b1-B/{request.json,answer.sse,result.json,messages.json}`、`evidence-332/{provider-requests,raw,stages,captures}`、`usage.json`、`summary.json`、`live-result.json`、`semantic-review.json`、`final-integrity.json` 及哈希清单。真实请求脚本和离线汇总脚本保存在同级任务证据目录中。下一步仅需单独确定 B2 正常消息附件接线范围；本次 B1 通过不能证明 B 适合全面推广，Omni 质量结论保持前节原状。


## M3.2：精确证据保留对照（2026-10-18）

**离线结论：采用已有会话级 `rerank_threshold=0.15` 作为有限验证候选；生产默认继续 0.3，不新增精确保留规则。** Omni A/B 的精确 OCR 均可通过原有 MMR、Merge、Top-K 到最终 Context；这项冻结输入收益尚不能证明全局降阈值安全，也不能直接证明新答案正确。本轮另获新授权并执行两次独立请求：Omni 因执行器错误未生成答案，0959 完成真实 Cheap 回答，整体 **`M3_2_PARTIAL`**；详见后面的真实验证。不复用 M3.1 授权，也不把部分链路成功计为完整回答通过。

### 源码与重放可信范围

开始时本地与 `git fetch origin` 核对的远端均为 `2d0f1c5c950c70a246039f8f1086cf6438aef08c`，分支 `codex/weknora-python-port-integrate-image-20261014`。固定上游不变。重放执行时生产源码为该 SHA；诊断脚本／测试随后独立本地提交 `cdf83444fce8d1cbf2ebad22a9da0ab122f08ec4`。本节最终交付提交以 `git log -1 --format=%H -- docs/milestones/RAG_M3_REPORT.md` 和交付消息中的完整 SHA 为准，避免文档自包含自身 Git 哈希。

新增脚本 `scripts/rag_m32_threshold_replay.py` 直接调用产品 `rerank()`（内含原生筛选和 MMR）、`merge()`、`filter_top_k()`、`into_chat_message()`。没有复制筛选算法，保存模型仅返回原始回执并严格核对 Query、完整候选文本和顺序；脚本拒绝 socket 连接。原始分数、wire、Gold 和历史输出均未改写。完整 15 组 Rerank 回执各重放 0.3／0.25／0.2／0.15／0，共 **75 次离线计算、0 次真实 Rerank 请求**；不把它们计为新模型调用。其余 5 组没有完整 Rerank 回执，逐案标明 `NOT_REPLAYABLE_NO_COMPLETE_RERANK_RECEIPT`，不编造分数。

Merge 使用显式 `BEGIN TRANSACTION READ ONLY` 导出的当前来源快照 **553 行**。读取前核对 PG `127.0.0.1:56423`、库 `weknora_python_port_20261013`、system identifier `7696088403504697382`；未写数据库或索引。该快照不是历史快照：历史候选与当前行的 `content/source_version/knowledge_base_id/knowledge_id/chunk_type` 五项逐一一致后才使用。0.3 下 **15 组 Rerank、13 组完整 Merge/Context、2 组正确 SearchNothing 停止**与保存结果一致，合计 **41 项基线比较全部匹配**。因此以下是产品函数的保存响应重放，不冒充新的真实 HTTP 问答。

### 五档对照

下表为 `MMR 选择数量 → Merge/Top-K 后 Context 块数`；每格按 A/B 分开。精确目标是否保留另行检查，不以同页 Summary 或来源 ID 命中代替。

| 样本 | 0.3 | 0.25 | 0.2 | 0.15 | 0（仅诊断） |
|---|---|---|---|---|---|
| Omni A | 1→1 | 1→1 | 1→1 | 2→2 | 30→29 |
| Omni B | 1→1 | 2→2 | 2→2 | 2→2 | 30→29 |
| Hotpot A | 2→2 | 2→2 | 3→3 | 3→3 | 30→22 |
| Hotpot B | 3→3 | 3→3 | 3→3 | 3→3 | 30→22 |
| 0434 A | 0→0 | 0→0 | 0→0 | 0→0 | 30→14 |
| 0434 B | 0→0 | 0→0 | 0→0 | 0→0 | 30→17 |
| 0959 A | 2→2 | 2→2 | 2→2 | 2→2 | 30→13 |
| 0959 B | 2→2 | 2→2 | 2→2 | 2→2 | 30→15 |

LongBench、MIRACL、SciFact 及原 A 检索追问的现有完整回执也纳入这 15 组，只做离线干扰检查，没有重新运行六套数据集。逐案候选 ID、真实原分数、threshold/MMR/Merge/Top-K/Context 路径和字符数见 `RAG_M3_2_RERANK_RETENTION.results.json`。短标题偶然出现在其他段落不等同于该 Chunk 被保留；机器摘要分别保存文字出现、原候选 ID 的合并身份和精确目标文本。

| 精确目标及结果 | 0.3 | 0.25 | 0.2 | 0.15 | 0 |
|---|---|---|---|---|---|
| Omni A OCR `51316a20…`，原分数 0.1673908234 | 阈值丢弃 | 丢弃 | 丢弃 | 全阶段保留 | 保留但噪声严重 |
| Omni B 同一 OCR，原分数 0.2708410025 | 阈值丢弃 | 全阶段保留 | 全阶段保留 | 全阶段保留 | 保留但噪声严重 |
| Hotpot A/B 第二跳 text `31417c7c…` | 丢弃 | 丢弃 | 丢弃 | 丢弃 | 精确文本到 Context |
| 0434 A/B Kaggle text `085643dc…` | 丢弃 | 丢弃 | 丢弃 | 丢弃 | 精确文本到 Context |

Omni Summary `4427641f…` 与 OCR 是两个独立 Chunk，0.15 下两者共存；Summary 仍只有主题说明，不能回答数字。原冻结图片 `E:\WK\run\p9-20261016\s\95a3151c2c9f5f3d9e36\PPT_lecture1_page_007.png` 已实际查看，系数矩阵为 `[1,-2,1; 0,2,-8; -4,5,9]`，增广末列为 `0,8,-9`，与有噪声的 OCR 对应。这是事后来源核对，未写入模型输入、索引或 Gold，未重跑 OCR。

### 收益、干扰与工程决定

- **0.25／0.2：**只能恢复 Omni B，不能恢复 A。0.2 开始让 Hotpot A 新增一段 Beaumont 诗集／人物简介，虽有人名匹配但不证明研讨会地点；Context 从 2,632 增至 3,387 字符（+755），目标第二跳仍未进入。
- **0.15：**Omni A/B 的原文 OCR 均进入最终 Context，原问题所需数字证据由 0 个增至 1 个；每组 Context 从 **295→623 字符（+328）**，新增明确无关 OCR 为 0。0959 A/B 的 **整个 Context 均与 0.3 完全相同**，均为 2 块／1,720 字符，原无线电波、微波证据保留。15 组中仅 4 组 Context 有变化：Omni A/B、Hotpot A，以及原 M3 的 A 检索追问；后者补入地球曲率阻挡信号的原课件段落（0.1967539），Context 1,279→1,720 字符，属于相关补充。这里只重放旧 M3 回执，没有重新执行已通过的 B1。不能据此推断全量语料无副作用。
- **0：**Omni 每组通过 30 个候选，其中内容核对为 1 个含答案的带噪 OCR、3 个仅主题相关、2 个不确定、**24 个明确无关**。Merge 后 29 个独立块中仍保留 **23 个明确无关原候选身份**；第 24 个候选文本也可能经其他块出现，另行记录，不能算成新的相关证据。Context 膨胀至 A **35,354**／B **34,251** 字符。即使 Hotpot／0434 目标也进入，仍不建议该诊断上界用于上线。
- **负例覆盖限制：**候选相关性来自对原始文本的 agent-assisted engineering review，是可复查的工程标注，不是新增人工 Gold 或官方 precision。未知候选保持 UNKNOWN。已有 chitchat 无检索，不算拒答负例；P29 `utc-empty` 没有候选，也不能证明低阈值对有干扰候选安全。未因缺少负例额外调用模型。

**选择 A 作为隔离验证候选，生产执行 C。** 现有 `context_config.conversation.rerank_threshold` 已可注入 0.15，故不必新增运行代码。拒绝新增“数字问题＋同页 Summary 就补 OCR”的规则：同源不证明相关，当前又没有经过足够负例验证的通用精确字段匹配条件；新增词表／状态机没有本轮必要证据。保留生产 0.3、0.15 top-1 floor、模型、Top-K、MMR、Merge、Prompt、查询理解 A 及动态路由不变。不实施无条件 OCR 提权。

### 实际命令与验证

工作目录 `E:\RAG-Worktrees\weknora-python-port-20261013`，解释器为 `E:\RAG quention\.venv\Scripts\python.exe`，下表 `python -B` 指该实际解释器。

| 实际命令 | 退出码／证据 |
|---|---|
| `git fetch origin`；`git rev-parse HEAD`；`git rev-parse origin/codex/weknora-python-port-integrate-image-20261014` | 0；起始本地／远端均 `2d0f1c5…` |
| `python -B E:\WK\run\rag-m32-20261018\replay_snapshot.py` | 最终 0，`SOURCE_ROWS 553`；首次因 Windows ProactorEventLoop 不兼容退出 1，改该只读工具为 SelectorEventLoop 后完成，无历史数据修改 |
| `python -B scripts/rag_m32_threshold_replay.py --snapshot E:\WK\run\rag-m32-20261018\replay-source-snapshot.json --output E:\WK\run\rag-m32-20261018\replay-results.json` | 0；41 项全部一致。初始版本另存 `replay-results-initial.json`，随后增加输入身份与原响应格式校验后离线重跑 |
| `python -B -m pytest tests/weknora_port/test_rag_m32_replay.py tests/weknora_port/test_pipeline.py tests/weknora_port/test_filter_top_k.py tests/weknora_port/test_conversation_configuration.py -q -p no:cacheprovider --basetemp E:\WK\run\rag-m32-20261018\pytest-final` | **0，100 passed in 1.71s**；`pytest-final.log` |
| `python -B E:\WK\run\rag-m32-20261018\summarize_offline.py` | 0；生成本节配套机器摘要，未调用模型 |
| `git diff --check`／`git diff --cached --check` | 0；提交前执行 |
| `python -B E:\WK\run\rag-m32-20261018\run_live.py prepare`／首次 `start` | 各 0；新授权、模型、Scope、资源预检通过 |
| 同上 `query omni-page007` | **1**；202.571 秒后显式失败 SSE，3 份已报告用量，未发送最终答案，不重发 |
| `python -B -m pytest E:\WK\run\rag-m32-20261018\test_live_send_gate.py -q -p no:cacheprovider --basetemp E:\WK\run\rag-m32-20261018\pytest-live-tool` | **0，2 passed**；实际发送限制保留 durable gate 身份并拒绝非 SiliconFlow |
| `run_live.py confirm-tool-failure`／修正后 `start`／`query mml-0959` | 各 0；首案明确失败并保留计数后，只运行另一个未执行样本 |
| `run_live.py snapshot`／`stop`／`python -B E:\WK\run\rag-m32-20261018\summarize_live.py` | 最终各 0；快照、完整回执和持久化核对完成，自有 API 停止 |

100 项为离线定向验证，覆盖正常 Rerank／MMR、Merge／Context、Top-K、配置优先级及 Scope。新增 16 项专门防止诊断工具把缺失行、跨 KB／文档身份、非法／重复 raw index、缺失 score 或网络连接误判为有效重放；过期版本通过当前快照与历史版本严格比对拒绝，不声称离线工具代替真实生产权限验收。未改生产引用系统；空证据不会生成新的真实引用记录。

### 交付和剩余问题

本轮相关文件仅：`scripts/rag_m32_threshold_replay.py`、`tests/weknora_port/test_rag_m32_replay.py`、本报告增补、`docs/milestones/RAG_M3_2_RERANK_RETENTION.results.json`。详细证据在 `E:\WK\run\rag-m32-20261018`，包含原回执哈希清单、当前只读快照、完整 75 次重放、逐候选内容标注、测试日志和源图哈希；大型原始内容不加入 Git。生产模块、依赖、迁移、索引、历史账本、UNKNOWN 均未修改，用户原有 8 个未提交路径保留。离线阶段云调用为 0；新增真实消耗和失败均在下一节单列。

尚未证明：0.15 对新问题和真实生成的收益、广泛无答案场景的误召回风险；Hotpot 第二跳和 0434 在 0.15 仍丢失。它们属于后续相关性／输入证据问题，不能用继续降低全局阈值一起解决。两次真实请求额度已使用，Omni 完整生成仍未验收，不能用0959通过替代。若 Owner 后续另批一次 Omni 验证，应复用已修复的执行器；本轮不补发。不重复 B1，不开发 B2，不自动扩大样本或推广默认值。本轮只本地提交，不 Push、部署、合并或打 Tag。


### 新授权后的真实验证：2 次尝试，1 次完整回答

Owner 对本轮明确确认：仅现有 SiliconFlow，最多 2 次完整问答、12 次 Provider、100,000 已报告 Token，UNKNOWN 停止；后续 UI 确认按同一授权记录，未刷新额度。保持 DYNAMIC、查询理解 A、原 Cheap、Embedding、Rerank、原问题、既有 KB 与 `document_scope=[]`。只在新会话注入阈值 0.15，运行时额外保存实际 `rerank_threshold=0.15`、Top-K=30。所有新 Provider 均为 SiliconFlow；未发送 DeepSeek、VLM 或新入库请求。

| 样本 | 真实阶段与生成 | 质量及状态 | 总 Token／端到端 |
|---|---|---|---|
| Omni page007 | Search30→Rerank2→Merge2→Context2；目标 OCR 排名2，原分数 **0.2711016238**，623字符 Context 含完整 OCR；最终答案调用 **0** | **FAIL_NO_ANSWER_GENERATED**；执行器在生成准备阶段阻断，没有矩阵答案可评分 | 31,253／202.571秒 |
| 0959 | Search30→Rerank2→Merge2→Context2；两段来源实际进入 Cheap 请求；`finish_reason=stop`，未升级 | **PASS_SUPPORTED_WITH_SOURCE_TERMINOLOGY_CAVEAT**；无线电波／电离层、微波卫星和附加传播问题均有课件支持；SSE=持久化 | 8,210／435.698秒 |

**首案失败是本轮执行器错误，非 Rerank／Merge 缺陷。** 本轮包装器最初在 `model_factory` 构造阶段拒绝非 SiliconFlow。产品 `runtime.py::_generation()` 在 DYNAMIC 决策前会预加载 Cheap 和 Expensive，因此本地构造 DeepSeek 就触发普通 RuntimeError，被服务映射为 `ORDINARY_CHAIN_FAILED`。没有答案 HTTP 请求或不确定外发。SSE 失败与 PG `port_turns.status=error`、空错误答案已对应保存；原 wire、回执、消费和失败计数完整保留。

仅修正工作树外的执行工具：供应商限制移到实际发送 gate，允许正常的角色本地预加载，仍在任何非 SiliconFlow HTTP 发送前拒绝。保持 durable gate 对象身份、原 SendGate 权限和路由不变。2 项离线工具反例通过后，已确认失败且 UNKNOWN 为0，才执行另一个样本0959；**没有重发 Omni，也没有新增第3次问答**。修正前脚本／状态、失败终态回读和恢复决定均在 `live/` 留存。此修复不涉及生产文件。

Omni 真实目标仍是 `51316a20-67fd-4732-b520-c049999933d0`，document `dfeb8157-6bf9-4fe2-927a-f38d40b342a0`，version `35ad1226-ec0e-4931-a87d-257e756f525c`。原文 OCR 的定位沿用图片来源合同；它的 legacy文本offset为0/0，不能伪称拥有普通文本span定位。精确内容进入 finalized Context 已确认；**没有实际 Cheap 请求，不能声称已送达生成模型或被正确使用**。这是一次新的 Rerank 回执，不能当作恢复 M3 历史响应。

0959 实际答案为“无线电波（电离层折射）和微波（卫星通信）”，附加信号扩散、大气折射和质量下降均来自 `df6098cb…` 原文。该段与 `1a4c8c0f…` 同属 document `8fdbe2af-9cc0-4b4a-a554-352a61ca5252`、version `3ff26984-be2a-4c7e-8b8f-bb8140c156f8`，真实区间分别 `[10575,11061)`／`[11437,11851)`，完整原文均在实际生成请求中。原课件相邻段对无线电波分别用 refraction/reflection；答案沿用目标段的 refraction，没有为“纠正物理知识”改写原文。这是来源支持的语义核对，不是新官方 EM/F1。公开引用继续关闭，不要求可点引用。

| 实际能力 | 调用数 | 输入 Token | 输出 Token | 合计 |
|---|---:|---:|---:|---:|
| Xing Query Understanding | 2 | 5,067 | 65 | 5,132 |
| BGE-M3 Embedding | 2 | 44 | 0 | 44 |
| BGE Rerank | 2 | 32,992 | 0 | 32,992 |
| Xing Cheap 最终生成 | 1 | 1,168 | 127 | 1,295 |
| **合计（含失败消耗）** | **7** | **39,271** | **192** | **39,463** |

7 份实际调用均已报告 usage，新增 UNKNOWN0、缺失用量0。历史账本哈希和来源文档／chunk哈希前后保持一致；原有8个用户工作树路径和159份历史输入文件哈希一致。本任务两个 API 进程已停止，56425不再监听；PG／Redis 保留原状态。授权执行空间已关闭，未用5次 Provider／60,537已报告Token空间不结转，也不是账户额度或历史UNKNOWN的释放。

生产 A 本次 Query 等待分别约199.015／227.613秒，0959 Cheap传输也较慢；保留真实延迟，不以更换模型或延长超时修饰结果。模型响应与候选池均是新执行结果，不能把该两次运行当成纯阈值因果A/B；阈值因果对照来自前面的固定输入离线实验。

**最终决策不变：**0.15 是 Omni精确证据保留的可复现隔离候选；0959真实回归通过，但 Omni最终回答未验证，且有主题干扰／硬负例覆盖不足，暂不修改生产全局默认。Hotpot与0434低分证据仍作为后续质量问题保留，本轮不为它们增加规则。达到本轮次数上限后停止，不 Push、部署或启动下一轮。


### 单次 Omni 补验：完整回答完成，精确证据与答案均失败（2026-10-18）

Owner 另行授权补做一次 Omni page007 完整问答。本次独立执行空间为 **1 次完整请求、最多6次 SiliconFlow Provider、100,000已报告 Token**，未复用已关闭额度。实际源码 `96d8820b1e883e69039185146e9e642de63d1984`，复用修正后的发送门禁执行器与既有 PG/Redis/索引；仅新隔离会话阈值0.15，生产查询理解A、全局阈值0.3、DYNAMIC及所有模型不变。没有重新解析、入库、Embedding文档或重建索引。前文两次尝试与首案失败原样保留；本节为单独的新回执。

**执行链路 PASS，精确证据保留 FAIL，答案质量 FAIL。** 唯一请求 `f62ae25f-a80d-4840-909c-c9f35e2e4daf` 完成真实 FastAPI → PG/Redis → Provider → SSE → 持久化，`finish_reason=stop`、实际角色Cheap、未升级、SSE与持久化答案一致。Search30→Rerank1→Merge1→Context1，最终仅295字符的同页Summary。OCR目标 `51316a20-67fd-4732-b520-c049999933d0` 在Search排名6、实际Rerank输入index5、原始模型排名2，但新得分只有 **0.09591700881719589**。它低于隔离阈值0.15，第一次丢失发生在Rerank阈值筛选，未进入MMR、Merge、Context或最终生成请求；不是后续合并丢失，也不能将Summary的来源命中视为OCR数字命中。

Cheap实际收到Summary后生成了矩阵 `[1,2,3; 4,5,6; 7,8,9]` 和末列 **4、10、13**，并称按常见教学示例补充。冻结原图片／OCR对应矩阵为 `[1,-2,1; 0,2,-8; -4,5,9]`、末列 **0、8、-9**。这些值仅用于事后核对，没有写入模型输入或索引。原始供应商SSE本身已经包含错误数字及补充说明，因此不是Decoder制造错误。分别记录“精确证据未送达”和“模型无依据生成”；不能说模型收到OCR后仍忽略，也不能因为附注承认举例就把答案算正确。

新实际Rerank Query为“课件中线性方程组的系数矩阵是什么，以及增广矩阵最后一列的三个数分别是什么”。它与历史M3 A/B的Query、完整候选文本集合和顺序均不同。前文冻结输入在0.15下的收益仍成立，但这次新请求表明该配置不能稳定保证精确证据保留。不能单独归因于Query措辞，也不把新旧结果当作纯阈值因果A/B。未追加调用或继续下调阈值。

| 本次实际调用（均SiliconFlow） | 次数 | 输入Token | 输出Token | 合计 |
|---|---:|---:|---:|---:|
| Xing4.0-29B 查询理解 | 1 | 2,536 | 37 | 2,573 |
| BGE-M3 查询Embedding | 1 | 30 | 0 | 30 |
| bge-reranker-v2-m3 | 1 | 28,793 | 0 | 28,793 |
| Xing4.0-29B Cheap最终回答 | 1 | 965 | 273 | 1,238 |
| **新增合计** | **4** | **32,324** | **310** | **32,634** |

本次端到端 **69.875秒**，新增UNKNOWN0、缺失usage0，未调用DeepSeek/VLM。前两次39,463 Token另行保留；M3.2累计为3次尝试、2次完成回答、11次Provider、72,097 Token（输入71,595／输出502），不混为统一版本质量成绩。自有API已停止、56425关闭；本次授权标为CLOSED，未用2次Provider／67,366已报告Token执行空间不结转，不代表账户余额或历史UNKNOWN释放。来源资源与旧账本哈希一致，原有8个用户脏路径保留。

实际解释器仍为 `E:\RAG quention\.venv\Scripts\python.exe`。以下命令均实际执行、退出 **0**：`-B E:\WK\run\rag-m32-20261018\run_omni_once.py prepare`、`start`、`query`（仅一次）、`snapshot`、`stop`，以及 `-B E:\WK\run\rag-m32-20261018\summarize_omni_once.py`。执行器绑定／冻结计划检查和保存输入的离线比较亦退出0。命令成功仅证明执行完成，不代表答案质量通过。本次没有生产代码修改，前述100项定向测试和2项工具反例为前次已执行证据，本次没有重复运行，也没有新增浏览器验收。最终JSON一致性检查及 `git diff --check`／`git diff --cached --check` 在本次提交前单列留证。

新增证据根 `E:\WK\run\rag-m32-20261018\omni-supplement` 包含完整请求／原始SSE／阶段与Context／持久化答案／实际用量，及 `live-result.json`、`semantic-review.json`、`query-input-comparison.json`、`owner-authorization.json`、`final-integrity.json`。包装器和只读汇总器位于其父目录，哈希清单保留在证据根。机器结果追加 `omni_supplement`，此前所有字段不改写；本报告此前内容逐字节保留为前缀。

**收口：M3_2_PARTIAL，有限补验已完成，优化尚不具备推广依据。** 生产默认继续0.3，不新增特殊OCR提权、不降低默认阈值。0959此前真实回归通过仍有效；Omni新请求的检索证据与答案失败均保留，Hotpot第二跳／0434问题不在本次扩展处理。下一步最小建议是用这批冻结正负候选比较现有Rerank模型或输入表达，先取得可比较证据；本次不自动执行。Owner随后明确要求由6LUNA推送，本次仅授权正常推送当前任务分支，不合并、部署、打Tag或启动下一阶段。


## M3.3：固定正确证据生成诊断（2026-10-18）

**结论：`M3_3_COMPLETE_DIAGNOSIS_GENERATION_FAILURE`。固定原始OCR确实送达，但当前Cheap仍未正确提取；按Owner指定分支停止重排候选扩展和完整问答。** 这表示本轮诊断分支已完成，不表示M3_COMPLETE或自动检索闭环通过。起始HEAD与审查基线均为 `b97bfd00b40b51143803704f3e549533e997abd5`，原有8个脏路径保留。生产A、Rerank0.3、原Cheap/Expensive及DYNAMIC均未修改。

### 局部工具修复与验证

仅修改 `scripts/rag_m32_threshold_replay.py::replay_verdict` 及调用处：必须存在实际REPLAYED case/threshold runs且有效基线检查数大于0，才允许有效重放PASS。无case、全无回执、零基线返回 `PARTIAL_NOT_REPLAYABLE`／退出2；真实执行且有匹配基线的 `SEARCH_NOTHING` 仍可PASS。对应定向测试 **19 passed in 0.36s，退出0**。没有扫描五档阈值、重跑75次历史重放或改变旧41项基线结论。生产检索函数没有改动。

### 固定生成：实际输入与错误位置

复用 `E:\WK\run\rag-m32-20261018\omni-supplement` 最新Summary-only失败请求的原问题、Xing模型、实际Prompt、历史（空）、输出参数和渲染日期。通过现有 `prepare_generation()` 精确重建旧prepared messages／prompt hash，再核对Provider构造出的旧wire body完全相同；新请求仅把证据改为原Summary＋目标真实完整OCR。OCR `51316a20-67fd-4732-b520-c049999933d0`，document `dfeb8157-6bf9-4fe2-927a-f38d40b342a0`，version `35ad1226-ec0e-4931-a87d-257e756f525c`；只读数据库回查内容和版本一致。237字符OCR保持原噪声、换行和符号，SHA256 `088a90867822c715162b2400097a75958a8b11f4f532a6dbc222cd13f4d3c144`，在实际user消息中以原有HTML转义完整出现。图片来源offset保留0/0，不伪装普通文本span；没有注入整理好的矩阵或Gold。

本次独立run `8e53d038-ce27-4ffb-9b94-e24903825bf6`，实际Provider request为其 `/answer/1/cheap`，仅调用现有SiliconFlow Adapter的Xing流式生成。`temperature=0.3`、`max_tokens=1024`、`enable_thinking=false` 等输出参数不变；保留原渲染日期 `2026-10-18 Sunday`。它是**人工选择证据的生成诊断**，不经过查询理解、Embedding、Search或Rerank，不是正常FastAPI／浏览器验收，不声称有会话答案持久化或DYNAMIC选择验收。

| 核对项 | 实际结果 |
|---|---|
| 完整原OCR进入实际生成请求 | **PASS**；独立语义核对确认wire与preflight一致 |
| 系数矩阵第一、二行 | 正确：`[1,-2,1]`、`[0,2,-8]` |
| 第三行 | **FAIL**；原图／OCR是`[-4,5,9]`，回答复制为`[1,-2,1]` |
| 增广矩阵末列 | **FAIL**；应为`0,8,-9`，回答为`0,8,9` |
| 无依据补充／格式问题 | 补写了原文不支持的第三个方程；不是单纯排版错误，也没有诚实说明无法提取 |
| Decoder | 原始SSE、224个decoded events及最终349字符答案一致；有`[DONE]`和`stop`，未发现解码器篡改 |

矩阵原图的行列清楚，但OCR确有扁平化和符号／行边界噪声，不能假称其表达完美。本轮只能把问题收敛到生成／OCR表达一侧，不能凭一次运行确定两者各自贡献；所需`-4/5/9/-9`已在实际输入中，亦不能把错误数值视作有来源支持。未修改OCR、重新解析或增加第二次生成。

### 调用、未运行分支及收口

Owner另行明确授权M3.3：仅SiliconFlow，固定生成1次、直接Rerank最多4次、前置条件通过后完整问答最多1次，合计最多10次Provider／150,000Token；旧M3.2授权未复用。新发送前在**同一个durable gate对象**的authorize前增加本轮任务执行预留：UTF-8 wire字节＋原1024输出上限＋framing余量，共预留 **7,183 Token**；这是工作树外诊断工具的执行围栏，没有恢复账户/月度Token Authority。原Gate继续核对模型、Scope、外发权限和幂等身份；实际usage按原路径保存。真实请求wire SHA256 `627dd662371d0884e6de2901bfa994e2ea1ff3ace8a4b2fbbee71b359fff190f`，HTTP200，原始响应SHA256 `d3d32d769951c7912ebd609803eef6773187cda2589753b6b2eec4c1bb4dda39`。

实际 **1次Provider，输入1,116＋输出223＝1,339 Token，29.080秒，新增UNKNOWN0／缺失usage0**。只读PG回查与本地预留结算一致；历史账本、原件／Chunk资源哈希及Redis实例身份前后相同。没有启动API／Worker／浏览器进程，没有任何DeepSeek调用。授权与本轮预留日志已CLOSED，剩余9次Provider／148,661Token执行空间不结转，不释放历史UNKNOWN或账户额度。

Qwen3-4B仅完成官方合同和冻结输入的只读准备，没有账户／模型探针；静态目录缺4B条目，但真实可用性**未核验**，不能据此说可用或不可用。A是最新Omni的30条完整有序候选，B是成功0959的30条；C准备为原Summary加已确认不支持答案的真实片段，UNKNOWN排除，只作构造诊断。**Qwen A/B/C、BGE C四次Rerank均NOT_RUN_PRECONDITION_FIXED_GENERATION_FAILED；完整Omni问答亦NOT_RUN。** 没有候选新分数，不能作采用／拒绝该模型的质量判断。官方合同准备资料链接为 `https://docs.siliconflow.cn/docs/api/rerank-post`，不构成本轮实测。

本轮没有再发Summary-only负基线；历史无依据数字回答原样保留，问题仍未修复。当前DYNAMIC只检查截断、空答案和无效引用，不能描述为已验证事实正确性。未恢复在线正则Validator、固定拒答或语义裁判。下一步最小建议是先离线检查已有OCR布局与文本表达、保留机器来源映射，再决定是否需要新的生成验证；本轮不执行。Hotpot、0434、聊天附件、全局模型推广与长尾延迟保持独立。

### 实际命令与交付

工作目录为 `E:\RAG-Worktrees\weknora-python-port-20261013`，以下`python`指 `E:\RAG quention\.venv\Scripts\python.exe`：

| 实际命令 | 退出码／结果 |
|---|---|
| `python -B -m pytest -q --tb=short -p no:cacheprovider --basetemp E:\WK\run\rag-m33-20261018\pytest-replay\basetemp tests/weknora_port/test_rag_m32_replay.py` | **0，19 passed** |
| `python -B E:\WK\run\rag-m33-20261018\run_m33.py prepare` | 最终0，原Prompt/wire精确复现；最初两次本地装配退出1（Decimal类型、tuple/list JSON比较），日志保留，均无模型发送 |
| 同脚本 `generate` | **0，仅一次发送**；完成传输不代表答案正确 |
| `python -B E:\WK\run\rag-m33-20261018\snapshot_m33.py` | **0**；只读身份／用量回查并关闭授权 |
| 原OCR版本回查、完整OCR实际wire包含、raw SSE／解码逐项一致性断言 | **0**；原件／原文未改写 |

新机器结果为 `docs/milestones/RAG_M3_3_FIXED_EVIDENCE.results.json`；完整输入、原始SSE、decoded events、源图核对、预留／结算、官方合同准备、未运行分支、命令日志和哈希清单位于 `E:\WK\run\rag-m33-20261018`。本次仅提交局部工具、必要测试、本报告增补和新结果JSON；此前M3报告逐字节保留为前缀，M3.2结果不改写。提交前实际执行JSON一致性及 `git diff --check`／`git diff --cached --check`，结果保留于证据根。最终完整commit SHA以本次交付及该节对应 `git log` 为准，避免自包含提交哈希。本轮不Push、合并、部署、打Tag或继续扩展。
