# P13 — Rerank 低分原因分析与最小对照实验

**状态：P13_COMPLETE。** 冻结的六次真实 Rerank 已全部完成并收口，没有追加请求。完成的是诊断与限定实验，不代表0434完整问答已通过。

## 1. 结论

当前最有依据的线索是：**原0434 Chunk缺少与问题相连的章节/训练上下文；真实上下文包对中英文两种问法都有大幅增益。** 英文原问优于既有中文Rewrite，但单独改回英文仍远低于现有保底线。0959中文反而更高，不能据0434认定中文普遍失效。

六次原始响应共186个分数与适配结果逐项相等，索引、排序和当前过滤规则核对通过；未发现分数提取、缩放、映射或过滤代码Bug。**不建议降低全局阈值。**

上下文包同时改变真实标题、邻近正文、长度和重复，尚不能独立认定是哪一项导致提升，也未验证自动生产实现。所有优化建议在本轮停止处保持未实施。

## 2. 基线与既有真实证据

- 任务基线 `cbac422d98e56a2b7aec8511762f7dd7a0aa9e5c`；实际六次执行源码 `a92da46337cb5fd62f431af50182de654541e278`。初始实验支持提交 `d959e0a4a5b409bf0a6c2dd188edaaa714d31c06`，随后仅修实验器预检。567个backend/frontend文件哈希不变。
- 固定上游仍为 `3e8b0bfc80b845b2d4b2ed683994748741450a97`。P11/P12原始结果、原账本和失败保留，没有合并成统一版本成绩。
- P11/B0434正式向量run的正确Kaggle Cat vs Dog片段为index14、chunk085643dc-5ebb-4b8a-b7f1-75427da40015、doc4019d2aa-c21d-42c2-8e8f-4d5aee4039ce。实际中文Query已保留PPT/coding time/训练数据集含义。输入完整，分数.0007800887，最高.0020336467；现有.3阈值、.15 Top1 floor使其空结果。P11/B0959正确无线通信片段原分数.3873645067，作为正例锚点；旧英文0434诊断也低分，详见P12根因报告。
- 原slot14 text-bearing physical pages为[24,25,26,27,28,30,33,34,35]，派生Unicode[7276,7744)：跨多张低文字密度幻灯片，包含Audio/NLP等先前内容和Kaggle名称，却没有Coding time标题。原PDF第33页图像确有该标题，原图和绑定crop均可见；middle_json对应image_body没有标题文字。这是现有输入的可复核缺口，不能自动等同于切块代码Bug。

## 3. 预先冻结的六条件

全体使用同一31槽候选池：完整保留P11/B0434原30条文本与顺序，追加同KB的0959正例chunk df6098cb（完整ID/版本/定位在JSON）为index30。模型标签与协议固定 SiliconFlow `BAAI/bge-reranker-v2-m3` / cohere-rerank / probability；模型注册、权限、来源与正常P1SendGate沿用现有实现。

0434采用2×2：原英文问题与既有中文Rewrite × 原slot14与章节上下文包。只在slot14改变文本，其它30槽、顺序与协议字段相同。另以0959原英文问题/忠实人工中文译文作控制；中文译文明确不是历史模型Rewrite，不含答案词。五条配对边各只改变一个因素。全部候选、问题、标签、阈值网格与body在调用前冻结。

章节变体为“完整原slot14 → 第33页逐字标题 → 第34..38页45个真实locator文本”。标题`Coding time!`、`our ingredients...`是明确标记的人工转写，crop SHA3a48bfaa8438f3b680ab11200f15b027a430552fcf9d96136d4d258c61cc17a0；不是MinerU自动输出。原件SHA40a74d3fc5e73c81465206a4043c19b850daf66fc5617756f5894d25ad054b4b，physical p33/idx32，绑定中间JSON及image_body路径均保留。45/45机器文本逐项匹配middle指针。没有补写“这就是训练数据集”等Gold解释，没有回写索引。Kaggle句在原slot与真实窗口各出现一次，长度/重叠是解释限制。

proposal-v1保留；冻结前调整拼接顺序，避免标题插到旧Audio/NLP内容前误示章节归属。最终proposal-v2 SHA1195adad87091056940826364b67c2c7fbd414e92ac7b8fda5f36052190d8950。预检补正文档版本后frozen-v2 SHA3dcda911393b73b8cb177967250f2c767727a78b765e4eedef9054e7b5706ef4；六个body/wire hash、候选和标签完全没变。

## 4. 实测分数与当前过滤

以下是六次真实Rerank和保存结果作用于当前raw filter的离线重放；不是新的完整RAG问答。阈值.3、Top1 floor .15均未改。

| 条件 | 正确证据原始分数 | 排名 | 现有raw过滤结果 |
|---|---:|---:|---|
| R1-0434-EN-original | 0.004106618 | 4 | 空 |
| R2-0434-ZH-original | 0.000774569 | 6 | 空 |
| R3-0434-EN-context | 0.340859950 | 1 | 保留正确证据（达到.3） |
| R4-0434-ZH-context | 0.243264154 | 1 | 保留正确证据（Top1保底） |
| R5-0959-EN-control | 0.471884936 | 1 | 保留正确证据（达到.3） |
| R6-0959-ZH-control | 0.581011653 | 1 | 保留正确证据（达到.3） |

0434上下文包的增益：英文+.336753331，约83倍；中文+.242489585，约314倍。两者均由rank4/6升至rank1。原英文比中文约5.3倍，但原英文最高候选仍低于.15，不能单独解决。0959中文.5810高于英文.4719，两个表达都保留正例；不支持统一中文降权或简单全局分数尺度错误。

**R4 .243264虽低于.3，仍被现有Top1 fallback保留。** .15只在没有候选达到阈值时保护最高项，不是所有候选的第二条准入线；在更大pool若其它候选超过.3，R4这种较低项不保证保留。只读复核通过AST提取当前真实filtered()函数，冻结重放exit0：R1/R2[]、R3/R4[14]、R5/R6[30]。

与P11不同，本轮保留供应商完整原始response.body。六个请求均HTTP200、各31条、索引0..30双射、排序递减；raw relevance_score与adapter score **186/186完全相等**，不存在本次可见的sigmoid重复、除100或映射错序。当前分数位于0..1不意味着已校准为答案正确概率；六个条件不足以判断全局校准。供应商模型标签固定，但没有可固定的远端权重版本。

## 5. 降低阈值的负例风险

调用前定义的标签包括：index14为0434答案正例；index30为0959正例、0434跨主题负例；index6的pylearn2 import代码为0434无答案困难负例；index24 Caffe项目活动、index28 NLP工具列表为两题明确负例。其余候选保持UNJUDGED，不擅自算成负例或官方precision。

| 原片段条件 | 反事实阈值 | 保留数 | 正确证据 | 已预标负例 |
|---|---:|---:|---|---|
| R1-0434-EN-original | 0.01 | 1 | 丢失 | [6] |
| R1-0434-EN-original | 0.002 | 8 | 保留 | [6] |
| R2-0434-ZH-original | 0.002 | 1 | 丢失 | [6] |
| R2-0434-ZH-original | 0.00078 | 5 | 丢失 | [6] |

原片段的index6分数为英文.027744217、中文.002003263，均高于正确证据。因此**任何单调raw分数阈值若放入这两次正确证据，也会放入该代码负例**。中文阈值.00078甚至略高于当前正例.000774569，放入5项却仍漏正例；说明按旧单点精调阈值也脆弱。阈值0收入所有31条，包括明确负例与生成摘要。

网格在调用前冻结；这些仅为raw filter反事实，未模拟composite/MMR/merge、生成或引用，不能当成完整问答准确率。现有.3/.15在本次有上下文与0959条件已能保留目标，缺乏降低全局阈值依据。

## 6. 实际修复、计量与保护

生产Rerank未发现需修Bug，Prompt、路由、MinerU及其它RAG模块未改。只增加P13专用sender/fence及定向测试，复用正常Runtime/Registry/Authority/P1发送门，没有直读输出模型密钥。

首个sender脚本进程exit1发生在外发前：既有生成摘要候选index8缺chunk级source_version。原预检把其与真实文档版本直接比较而拒绝，**0个trial intent、0个物理模型请求**。最小修复是以冻结的权威document_versions绑定该summary；非summary无版本、删除/文档版本漂移仍拒绝。补6项安全反例后49项定向测试exit0。保留原失败日志与frozen-v1；没有对已发送请求重试。该修复是实验器预检Bug，不是0434低分的生产原因。

本轮以既有可信流程打开120000 Token有限围栏，原账户siliconflow-owner-main、2026-10月、carry14与approval均保持。月上限1237596→1357596，六个请求正常预留总计90212；只允许上述Rerank模型，没有Embedding、生成、DeepSeek、新上传/解析/索引或Worker/API服务启动。

**Token证据必须分开：**

- 原始响应meta.tokens与meta.billed_units分别报告input[5343,5157,5788,5602,5188,5188]，合计 **32266 Token**，output/image为0。这是供应商响应报告，不是独立账单验证。
- 当前适配器只接受它识别的usage结构，未将meta.tokens归一化；账本 **0条新settled、6条UNKNOWN，保守占用90212**。不能称实际Token为0或90212，也不能把32266与90212相加。
- 首版closeout-audit中UNKNOWN_NO_PROVIDER_USAGE措辞过宽；原文件不覆盖，`usage-field-correction.json`及本机器结果更正为“有meta.tokens，但当前未被适配为可结算usage”。没有清除或手动释放UNKNOWN，没有改变历史结算。
- 关闭围栏后月limit **1327808** 等于全账户占用+carry，headroom0；未用 **29788** 已撤回。原608条记录逐行不变，原51条UNKNOWN不变，DeepSeek calls/months不变；无reserved遗留。

1530个P11/P12/报告文件与继承378个保护文件哈希不变，567个生产文件哈希不变；目标KB、documents、chunks、完整embeddings、image tasks前后全等。PG127.0.0.1:56423，dbweknora_python_port_20261013，system id7696088403504697382；Redis127.0.0.1:56424 DB15，run_id87b4c2a9f76f9f94b34b5f21fe31a967926f343a，新独立前缀。复用P11资产根确保来源可回查，未消费旧队列/flush/启动容器。sender进程已退出，无常驻P13服务，共享PG/Redis仍healthy。

## 7. 实际命令与退出码

cwd为工作树；Python `E:/RAG quention/.venv/Scripts/python.exe -B`，PYTHONPATH工作树、E:/WK/deps/reader、工作树var/weknora-port/runtime-deps。日志/缓存/临时文件均E/D。下表python指上述可执行文件；完整参数同时写入results.json.commands。

| 实际命令 | exit | 证据 |
|---|---:|---|
| `python E:/WK/run/p13-20261016/inventory.py` | 0 | inventory.log，PG/Redis身份、全账户/数据/生产及历史文件快照 |
| `python E:/WK/run/p13-20261016/freeze.py` | 0 | freeze.log，proposal源哈希、原始冻结；v2仅补文档版本的完整命令在工作记录 |
| `python -m scripts.weknora_port_p13_rerank --frozen E:/WK/run/p13-20261016/frozen.json --validate-only` | 0 | validate.log：VALIDATED6，wire_bound90212 |
| `python -m pytest tests/weknora_port/test_p13_rerank.py tests/weknora_port/test_p13_fence.py -q --tb=short -p no:cacheprovider --basetemp E:/WK/run/p13-20261016/unit-final` | 0 | unit-final.log：43 passed（初版离线） |
| 同上，basetemp为`E:/WK/run/p13-20261016/unit-preflight-v2` | 0 | unit-preflight-v2.log：49 passed（预检修复后） |
| `python -m scripts.weknora_port_p13_fence open --approval-file E:/WK/run/p13-20261016/owner-approval.txt` | 0 | fence-open.log及不可覆盖真实PG收据 |
| `python -m scripts.weknora_port_p13_rerank --frozen E:/WK/run/p13-20261016/frozen.json` | 1 | real-rerank.log：P13_STALE_SOURCE，外发前失败 |
| `python -m scripts.weknora_port_p13_rerank --frozen E:/WK/run/p13-20261016/frozen-v2.json` | 0 | real-rerank-v2.log，六条RECORDED；trials内intent/raw/meta/adapter/result |
| `python -m scripts.weknora_port_p13_fence close` | 0 | fence-close.log，headroom0 |
| `python E:/WK/run/p13-20261016/closeout.py`；`analyze.py` | 各0 | closeout-audit、usage更正、controlled-analysis；186项raw/adapter相等 |
| 专属离线源码/locator核对及真实filtered() AST重放 | 0 | offline-analysis.json、causal-review.json保存完整命令/代码/stdout，未发模型请求 |

未实现的命令不得报告通过。没有整项目回归、完整RAG问答、回答质量/引用闭环或生产优化部署；这些明确NOT RUN。localized skill旧init误把Help当项目名，只创建了自己的root findings模板，已移到E盘留存；原计划和progress未覆盖，后用当前脚本建立P13独立计划。

## 8. 推荐与剩余不确定性

1. **保持现有全局阈值，下一轮优先验证可回查的章节上下文增强。** 最小有意义的验证是固定中文query与同pool，对比原slot和纯机器章节窗口，明确去重、长度边界及负例；再决定是否需要标题提取。不要据此直接把人工标题方案部署为自动能力。
2. **另做usage字段离线契约修复候选。** 用已保存raw fixture验证meta.tokens/billed_units一致性、缺失/冲突字段处理，若后续实现只影响新调用；历史UNKNOWN保持。当前未修改计量逻辑。

原标题、附近正文、长度、重复各自贡献尚不能拆开；两种表达差异不能纯归因语言，单次条件不能估计波动。Rerank模型标签相同不等于远端权重已固定。全局校准、完整语料负例分布和QA/引用效果尚未验证，不把本试验转成官方成绩或普遍策略保证。

已达到6次上限并完成所有冻结条件；只有一次外发前预检修复，无请求重发。停止追加实验与优化，不Push、不部署、不合并、不打Tag。原.serena修改和六个历史未跟踪文件保留。原始证据 `E:/WK/run/p13-20261016`，哈希索引 `delivery-evidence-manifest.json`。
