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

## M3.1 来源边界与证据流转（2026-10-18）

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
