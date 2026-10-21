# P20 — 答案、证据与引用一致性优化验证

## 结论：P20_COMPLETE，暂不启用自动语义放行

已完成失败追踪、结构化证据绑定原型、离线反例测试及最多 4 次 Cheap 审计调用。**来源身份合法、原文引句真实和答案语义得到支持是三个不同条件。** 本轮精确绑定检查有实用价值，但同一 Cheap 模型的语义审计仍会误判，不建议自动放行答案，也不建议自动补引。

没有修改生产 Prompt、检索阈值、模型、路由或解析器；没有改写或重新生成原答案。新增内容均在实验脚本、测试和报告内。未发现需修复的生产 RAG 阻断。

基线：`d61a26316f6b7cfff4b2e279ffa46fafdcb6d190`。真实审计执行 SHA：`4c9854c75651221372cd5fe3b80d58be3d096601`。原答案取自 P19 执行 SHA `bec7c282be4c1cc4b49979e786e949082392d49b`，原始模型输出/请求/Context 均冻结绑定；不是不同版本分数的混合重评。调用结算时间为 2026-10-16 15:57:18–15:57:43 UTC，本报告在续做会话中收口，证据目录沿用 `p20-20261016`。

## 1. 逐项追踪与主要共性根因

P19 三组问题分别为 6 题漏引、4 题错误/无依据推理、4 题证据不足/冲突/遗漏，去重后为 **8 题**，不是 14 题。交集：漏引∩推理 2，漏引∩证据 3，推理∩证据 1，三重交集 0。完整逐题映射位于 `failure-map.json`，包含问题、主张、原始回答、实际 Context、chunk/source/document/version、原文快照、引用及文件哈希。

| 冻结样本 | 漏引 | 推理问题 | 证据缺口/冲突/遗漏 |
|---|---|---|---|
| SciFact 247 | 是 | 未据此判事实错误 | TG2 来源命中，但片段不足以支持化学损伤结论 |
| SciFact 339 | 是 | 把剂量数据概括为单调上升 | — |
| SciFact 611 | — | 对反向因果回答 Yes，而解释为相反方向 | — |
| MIRACL 91294#0 | 是 | 添加来源未给出的“最密切”比较 | — |
| Dureader 6770736… | 是 | — | 节日来源日期冲突，回答遗漏 Arbor Day |
| Dureader 9938c97… | 是 | — | 最终 Context 为空，回答给出 UTC 公式 |
| MultifieldQA 741f377… | 是 | — | — |
| Hotpot 5a7631… | — | 地点答案与 Gold 不同 | 缺少地点支持来源，仅保留人物生平 |

**漏引根因有直接证据：** 6 个 P19 漏引题的供应商原始 SSE delta 拼接结果与落库回答逐字一致，原始输出均无引用标记。支持“模型未生成引用”，未见 Decoder 丢引；不能以修改 Decoder 或自动添引解决。

P9 的旧 4 次漏引用也逐项复用，见 `p9-reused.json`。P9 缺供应商解码前正文，P16 对其原因仍未决；P17 的新调用不能替代这些旧记录。没有用 P19 的可定位结论倒填 P9 历史根因。

共性问题是：现有引用协议能登记与回读资源，但自由生成的主张并未与确切证据片段绑定；来源相关也未必支持剂量趋势、因果方向、比较程度或地点。已有 Context 缺口与来源冲突又不能靠附一个合法引用补足。

## 2. 开源参考与最小方案

复用并核对 P16 保存的 RAGFlow v0.27.2 [`Dealer.insert_citations`](https://github.com/infiniflow/ragflow/blob/v0.27.2/rag/nlp/search.py#L316) 源码，SHA256：`e11c2aa749db1a44d0698f3cdda9b85c0519a5919e05d173fec793dfca3b9387`。它提供生成后切分回答、映射来源候选的参考；其相似度插引和逐步降低阈值没有证明主张蕴含关系，本轮未采用。保存副本和取舍在 `references/`。

沿用可追溯的[失败分析方法](https://langfuse.com/guides/cookbook/error-analysis-llm-applications)，在本地冻结已有记录；未新增 Langfuse 平台、外发 trace 或云端评测服务。

最小实验分两层：

1. **确定性结构检查。** 保留原回答；服务器划分有上限的段落单元并记录原始 span/hash。识别是否缺引用，不创建引用。P19 17 个回答检出原有 6 个漏引；P9 旧 4 个仅作历史输出复核。
2. **结构化生成后审计。** 同一 Cheap 对每个固定单元返回 verdict、来源 handle、逐字 quote 和理由。服务器要求单元全覆盖且 ID 不重复，quote 必须在声明的来源中唯一、连续、逐字存在，再计算字符 offset、chunk/document/KB/version/hash。缺引、未知或不支持、截断、无效绑定均 hold。原答案和原引用不变。

段落单元不是原子事实。混合单元不能因其中一个事实有依据就整体判 SUPPORTED。审计提供的引文是检查证据，**不是新答案引用**。

所有结果均保留 `ready_for_automatic_publish=false`。即使模型判 SUPPORTED 且 quote 存在，也只能记为模型判断和来源绑定；“错误主张＋真实但不相关的 quote”有可执行安全反例，不能自动认证语义。

## 3. 离线验证与真实实验设计

离线原型初版 17 项测试通过；观察真实响应后仅增加单一完整 JSON 代码块的兼容解析及反例，最终 **22 项通过**。覆盖重复 ID/键、漏单元、未知 handle、虚构/过短/歧义引句、截断、多 JSON 块、额外散文、真实引句绑错另一个真实 chunk，以及无依据主张配真实 quote 等情况。围栏另有 **29 项离线测试通过**。

真实实验是**冻结原回答的生成后审计 A/B**：

- A：精确保留已有 P19 原回答、原 Context、原引用，新增调用 0；历史生成费用仍保留。
- B：只增加一次结构化 Cheap 审计，原回答不重生成，不重新检索、入库或解析。
- 冻结 3 个支持缺陷反例和 1 个正确且带引用的对照；每题审计一次，共 4 次，无追加或挑选最好结果。
- 原问题、Context、原生成请求/参数均保留；审计自身统一温度 0.3、输出上限 4096、thinking off。同一模型 `SiliconFlow XingChenAGI/Xing4.0-29B`，沿用 Registry/Authority/P1SendGate。
- 模型只接收问题、原答案单元和当前证据；不接收 Gold、既有失败标签或人工判定。外发检查仍按原 KB 权限和实际版本进行。

这不是重新跑 4 次完整 RAG，也不是 4 对新生成答案。唯一比较因素是已有答案后是否增加结构化审计。

## 4. 四次真实结果

| 样本 | 原始审计输出及问题 | 严格校验 | 单 JSON 围栏兼容后的离线回放 | Token |
|---|---|---|---|---:|
| SciFact 339 | 两个含剂量趋势问题的单元均被判 SUPPORTED；引句属于正文 c2，却绑定标题 c1 | 拒收：quote 不在声明来源 | 仍拒收；不自动改绑到 c2 | 1,533 |
| SciFact 611 | 反向因果的 Yes 被判 SUPPORTED；一个后续单元被判 UNSUPPORTED；输出包在 JSON 围栏中 | 拒收：非裸 JSON | 解析后仍因引句/来源不匹配拒收 | 1,872 |
| Hotpot 地点题 | 识别到地点缺支持，但把一个固定单元拆成两个同名 u1，一条 SUPPORTED、一条 UNSUPPORTED | 拒收：单元覆盖/重复 ID | 仍拒收；不挑选有利判定 | 802 |
| MultifieldQA 51abd… | 正确的 2.2 万人及 7.6 万人回答，给出确切相关引句；包在 JSON 围栏中 | 拒收：非裸 JSON | 通过结构校验，保留实验候选；仍不自动发布 | 727 |

4 个原始响应均 `finish_reason=stop`，没有传输失败。初始严格解析 **4/4 拒收**。真实调用已停止后，只兼容“恰好一个完整 json 围栏”，离线回放 **1/4 保留、3/4 拒收**；原始失败结果不覆盖，这不是新的模型调用结果。

同一 Cheap 原始语义判断中，至少有 3 个错误或过宽的 SUPPORTED 条目：339 的两个单元、611 对反向因果的肯定。不能把这 3 个反例被结构性拒收写成“语义检查器识别正确”。它可能在另一次输出中给出真实但不相关的 quote 而绕过来源检查，这正是暂不自动接入的原因。

## 5. 质量、覆盖与可用性代价

这 4 题是定向校准样本，非随机基线，不能外推整体准确率。人工复核只用冻结原文及已有 Gold，不构造新官方 Gold。8 个服务器段落单元中，3 个全支持、3 个含冲突/无依据断言、2 个混合，严格全单元支持率 **3/8**。

| 指标 | A 原回答 | B 兼容解析后实验候选 | 解释 |
|---|---|---|---|
| 原答案是否被修正 | 否 | 否 | 0 次改写；拒收错误答案不算纠正答案 |
| 有答案 Gold 的 2 题目标值 | 1 正确、1 错误 | 保留正确 1，错误 1 被拒收 | 原始正确题数仍是 1/2；不把拒收计为正确 |
| 全段落证据支持 | 3/8 全支持，另有混合/冲突 | 保留的 1/1 单元支持；总体仅留 1/8 | 条件支持率提高伴随大量弃答，不能只报 100% |
| 回答级引用出现率 | 3/4 | 保留回答 1/1；相对原分母为 1/4 | 没有补引；覆盖分母明确不同 |
| 引用身份合法性 | 3/3 | 保留的 1/1 | 身份合法不是蕴含关系 |
| 带引但不足以支持主回答的答案 | 2/3 个带引回答（611、地点题） | 保留的 0/1 | 答案级语义支持诊断，不冒充逐原子事实的错误引用率 |
| 回答候选可用性 | 4/4 原回答可见 | 1/4；3/4 整答 hold | 有用且正确的部分也被一起拒收，原始严格解析甚至 0/4 |
| 新增模型 Token | 0，重用历史回答 | 4,934 | 不是说 A 历史生成免费 |

SciFact 两题没有答案 Gold，不报告世界事实 QA 准确率；其推断错误依据是所给来源与因果/剂量断言不一致。Hotpot 地点 Gold 和 MultifieldQA Gold 均沿用原值。已有所有 Gold/指标与失败未修改。

自动主张级引用覆盖率、逐原子事实错误引用率尚无可靠全量标注/对齐，记为 **NOT IMPLEMENTED**，不以段落级或回答级数字替代。当前结果支持结构检查能拦截不合格审计输出，不证明问题已经被回答正确，也不证明 Cheap 可以担任可信语义裁判。

## 6. 计量、隔离与历史保留

4 次均为真实 Cheap chat 审计，合计 **4,934 Token（输入 3,667，输出 1,267）**；新增 settled=4，UNKNOWN=0，reserved=0。没有新增 Embedding、Rerank、原答案生成或 DeepSeek 请求。审计调用耗时分别 6.26、16.15、5.11、2.67 秒。

原账户/月账本执行围栏从 1,641,652 增加 100,000 至 1,741,652，结束后收缩为实际占用 **1,646,586**，headroom=0，撤回未使用增量 **95,066**。原 701 条账本逐条不变，历史 UNKNOWN 61→61，不结算或释放；4 条新行的 request_id、实际 wire hash、模型、KB scope 均与冻结计划一致。

PG 127.0.0.1:56423，`weknora_python_port_20261013`，system identifier `7696088403504697382`；Redis 56424/15，run_id `87b4c2a9f76f9f94b34b5f21fe31a967926f343a`，独立 P20 前缀。未 flush、未启动容器或后台服务。567 个生产文件、1,812 个历史证据文件、KB/原文/Chunk/完整向量、DeepSeek 均保持不变。

## 7. 改动与可复核命令

实际文件：

- `scripts/weknora_port_p20_evidence.py`：固定单元、精确 quote 绑定与只读审计结果；单完整 JSON 围栏兼容。
- `scripts/weknora_port_p20_audit_ab.py`：四次上限、冻结原答后审计、权限/版本/来源核对、真实 raw/adapter 记录、请求前持久化 intent、不重发不确定请求。
- `scripts/weknora_port_p20_fence.py`：复用 P19 同账户/月锁、CAS、不可覆盖回执及历史保留机制。
- 对应 `test_p20_evidence.py`、`test_p20_fence.py`；本报告、同名结果和 progress。

生产共享文件、迁移及依赖变更均为空。单 JSON 围栏兼容只用于此实验，不改生产 Decoder 或自由生成协议；完整来源、单元与安全反例仍保持严格校验。

命令在本工作树执行，Python 为 `E:/RAG quention/.venv/Scripts/python.exe`。PYTHONPATH 为本工作树、`E:/WK/deps/reader` 和工作树 `var/weknora-port/runtime-deps`。日志、原始输出、缓存和证据放 E 盘，Tokenizer 复用 D 盘。

| 实际命令（Python 路径同上） | 退出码与证据 |
|---|---|
| `-m pytest tests/weknora_port/test_p20_fence.py -q --tb=short -p no:cacheprovider` | 0，29 passed；`E:/WK/tmp/unit/p20-20261016/`；离线模拟账本 |
| `-B -m pytest -q tests/weknora_port/test_p20_evidence.py -p no:cacheprovider --basetemp E:/WK/run/p20-20261016/unit-evidence-final` | 0，22 passed，0.14s；`unit-evidence-final.log` |
| `-B E:/WK/run/p20-20261016/prepare.py` | 1；只读准备误用 embeddings.tenant_id，无外发；保留日志 |
| `-B E:/WK/run/p20-20261016/prepare-v2.py` | 0；准备快照与冻结计划 |
| `-B -m scripts.weknora_port_p20_fence open --approval-file E:/WK/run/p20-20261016/owner-approval.txt` | 0；正常可信有限开额 |
| `-B -m scripts.weknora_port_p20_audit_ab --frozen E:/WK/run/p20-20261016/frozen.json` | 1；发送前 body 比较失败，无 intent/调用。零温度字段会被现有 renderer 省略；保留原 0.3 后冻结 v2，无生产修改 |
| `-B -m scripts.weknora_port_p20_audit_ab --frozen E:/WK/run/p20-20261016/frozen-v2.json --validate-only` | 0 |
| `-B -m scripts.weknora_port_p20_audit_ab --frozen E:/WK/run/p20-20261016/frozen-v2.json` | 0；恰好 4 个真实审计响应，质量失败保留；`real-v2.log` |
| `-B -m scripts.weknora_port_p20_fence close` | 0；关闭/撤回围栏回执 |
| `-B E:/WK/run/p20-20261016/audit.py` | 0；历史、身份、真实计量与隔离核对；`audit-final.json` |

原始根目录：`E:/WK/run/p20-20261016`。重点：`failure-map.json`、`p9-reused.json`、`offline-screen.json`、`frozen.json`/`frozen-v2.json`、`trials/`、`raw/`、`diagnostics/`、`offline-replay-v2.json`、`audit-review.json`、`fence-100000/`、`new-ledger.json`、`audit-final.json`。完整机器结果随同名 `.results.json` 提供。

**未实现的命令不得报告通过。** 本轮未运行新完整 RAG 问答、全项目回归、上传/Worker/解析重建、官方全语料评测或其它模型实验；均 NOT RUN。

## 8. 接入判断与停止

建议保留并继续限定验证的最小部分：请求内来源身份＋确切引句＋版本/offset 绑定、原回答完整保留、结构不合格时明确标记。它确实检出了把正文引句绑到标题 chunk 的错误，且没有给错误答案自动修好一个看似合法的引用。

**不建议正式接入自动发布闸门：** 同 Cheap 审计的错误 SUPPORTED、段落混合事实、未校准的语义判定及 75% 整答 hold 代价均未解决。更不建议根据相似度自动补引或自动改绑到另一条正确来源。真实但不相关的 quote 仍可通过纯来源检查，已保留反例。

后续最小方向是先用本轮 8 题和正确对照验证更细的主张覆盖及语义检查器可靠性，分别量化误放行与误拦截；这是建议，未继续执行。不扩大模型、检索、Prompt 或架构。4 次调用上限已到，停止；未 Push、部署、合并或打 Tag。
