# S8.6.3：引用身份修复与 ECS40 失败分层

日期：2026-10-22。状态：`OFFLINE_REPAIRED_REAL_NOT_RUN`。本报告为代理离线工程复核，不代表 Owner 验收。

## 1. 目标与实际交付

三条历史引用的正文/范围身份错误已完成离线复现和局部修复；18 个历史非 PASS 案例分别保留为 **10 FAIL、5 PARTIAL、3 UNVERIFIED**。按本轮可证实的主故障层统计：**C 7、D 4、F 3、A 3、A/B 未决 1**。不能将这 18 题全部称为错答，也不能据此声称新版本准确率提高。

本轮新增真实 Provider 尝试、结算、输入/输出 Token、UNKNOWN、数据库调用、ECS 操作均为 **0**。新版本真实模型问答、live HTTP/SSE/PG/history/source 回读验收及真实模型后台批次均为 `NOT_RUN`。历史 ECS40 的 **38 题正式 HTTP 完成、2 题受保护 NOT_RUN、158 次 Provider 全结算、364,593 Token** 是输入证据，保持原记录；该批由前台控制完成。后台独立运行已有零 Provider 探针，不能代替真实模型后台验证。

合法起点为 `d5b685046b7338250e011a518f924671439a0cd0`，沿 `codex/s8-6-adaptive-retrieval-20261021` 保留后续增量。引用主修复提交为 `922b5c3884693cb520378a2c18f90cb177916536`；本报告对应的最终源码提交为 **`c13eecda61ae6ac8c636e56b9a440898d934ab0e`**，包含缺失版本拒绝、原块类型/index、当前文档状态核验及相关回归。没有 Push、Tag、生产切换或重新导入/Embedding/建索引。历史报告记载的生产 API/Worker 仍为旧版本，本轮未访问远端核验其当前状态。

实际修改与证据入口：

| 文件 | 内容与作用 |
|---|---|
| `backend/weknora_port/retrieval/source_evidence.py` | `_project_selected()` 强制核对所有已入选原文；复用 `_materialize_selected()` 将复合正文还原为独立原块；`_validate_source()` 拒绝缺失版本；权威批量读回显式要求 `current=True` |
| `backend/weknora_port/storage/knowledge.py`、`backend/weknora_port/retrieval/search.py` | 既有 `get_chunks` 增加可选 `current` 参数并透传既有 `_read_chunks` 过滤，保留普通路径默认语义；不新增算法 |
| `tests/weknora_port/test_source_identity.py` | 三条引用、窗口外条目、Pieter、跨域/版本、预算、完整 Context、Registry/SSE/history/source reader 的离线回归 |
| `tests/weknora_port/test_knowledge_storage.py`、`test_source_evidence.py`、`test_source_reliability_wire.py`、`test_s86_pipeline_integration.py` | 当前文档状态回读、窗口外拒绝及既有离线repository适配 |
| `tests/fixtures/s8_6_3/ecs40_citation_identity.json`、`ecs40_complete_context.json`、`pieter_tribometer.json` | 冻结历史复现资料；不替换历史答案或 Gold |
| `scripts/s8_6_3_offline_evidence.py` | 从保存记录生成同名 results、历史来源轨迹和对照输入包 |
| `scripts/s8_6_3_fixed_evidence.py`、`tests/weknora_port/test_s863_fixed_evidence.py` | 固定请求校验与两种既有 Provider adapter 的纯离线渲染 |
| [机器结果](S8_6_3_SOURCE_IDENTITY_AND_FAILURE_LAYERS.results.json) | `citation_reproductions`、`failure_layers` 保存完整身份、QU、阶段、分数、历史评价及原始证据路径/哈希 |
| [分层标注](evidence/s8_6_3/failure_layers.annotations.json) | 版本化新诊断；旧评价原件不改写 |

读取了 `AGENTS.md`、`progress.md`、ADR 索引、ADR-004 和 `docs/design/S8_6_ADAPTIVE_RETRIEVAL_V1.md` 的相关合同。范围止于离线引用修复、证据分层及对照准备；没有改 P5、全局 Rerank 阈值、Prompt B 或规则族。

## 2. 来源、历史事实与证明边界

主要历史依据为 [S8.6.2-R3 ECS40 报告](S8_6_2_R3_ECS40_ACCEPTANCE.md)及同名 results、[独立首题真实 HTTP 报告](S8_6_2_R3_QU_HTTP_ACCEPTANCE.md)和[后台任务说明](../deployment/ECS_ACCEPTANCE_BACKGROUND_JOBS.md)。冻结本机快照：

```text
E:\RAG quention\var\reports\s8-6-2-r3\followup40\actual40-final-extracted.json
SHA256 79ecc4f19dd1d08b415270387be8df7003627d8005c14e906c21fc90665943a7
历史运行源码 5585f41dab1103738bd2aecd7161ded08598e198
历史报告 results SHA256 9c287498414ce79e832a2113148adf6e79fdfb94f5bcbf2470da37216f9ddd13
```

机器结果逐题保留 `historical_core_review`、`secondary_review`、原答案及 answer SHA256、实际最终 request artifact、body/messages SHA256、Search/Rerank/Context 来源和 trace。`actual_generation.source_context.status=NOT_REVIEWED` 是保留的机械抽取字段；本轮代理语义复核位于 `final_input_sufficient`、`reason` 与版本化 annotation，两者不混为自动验证结果。

Search 中的原文只能证明该保存的授权检索范围曾见到它；独立的全量授权语料/解析文本未被本轮重新导出。历史串行观察器没有独立保存 source projection 之前的完整 Merge 正文；该段只能用真实代码、冻结输入和后续 trace 离线重建，不能伪称存在逐阶段实时快照。实际 Provider 消息是判断“模型见到了什么”的依据，审查者阅读 PDF 不等于模型收到图片。

SciFact/MIRACL 的冻结 Gold 是检索 qrels，不能直接作为问答真假标签；本报告使用项目来源复核。LongBench 使用冻结参考答案/原文，Hotpot 使用冻结答案并检查必要关系，MML 使用冻结 typed Gold 及独立 PDF 来源复核。各数据集原生指标不同；本轮没有官方跨数据集准确率、独立留出集或新模型效果评测。历史 20 PASS/5 PARTIAL/10 FAIL/3 UNVERIFIED 和 20/38 项目复核比例均未重算为新版本成绩。

## 3. 三条引用：精确复现、最早错误阶段与修复

三条保存记录均为 tenant `332`、owner `p8-real-3de2eca0bea340babcde75ce60c9a366`、请求 `document_ids=[]`，受各自 KB scope 约束。下表的 `document` 对应保存的 `knowledge_id`，范围使用 `[start,end)`；哈希均为完整正文 UTF-8 SHA256。

| 条目 | KB / document / source_version | Search、Rerank及历史 HTTP source 原块 | 错误 Context、Provider、注册后引用 |
|---|---|---|---|
| DSM-1：`1400dc3d-f641-4193-8218-0483ab6b528e` | KB `76770fba-17c3-4704-8663-10aa669476ce`；document `92fda6c7-bde7-4974-a637-b035be5db2a2`；version `f4a475b5-5737-4513-9135-8fed67e481ca` | `[5759,6238)`，479 字；`8cfaeb854d2480b8a2c8602f57f8fa037956bb098b41e31aca7f43c002b0fbc1` | 同 ID/版本/start，`end=6959`，1200 字；`7f2176a9ced22aa0cb3dd2525b8d1b9f884869681b5b9079ad3a1f11971a7797` |
| DSM-2：`072ced3a-cc3d-4260-ae9f-f73b49fd3a0b` | 同 KB；document `3db7e66a-71a1-4e98-af06-eff6eef8ad1e`；version `230d83b5-7a2f-41a5-8260-52a923ad9a12` | `[6162,6569)`，407 字；`ac79846f486af3de39d0c283a4ccbaa376c81022830553fb28882ac981e8e601` | 同 ID/版本/start，`end=6959`，797 字；`c60b3272254e8422118ca19d8a298404e9023b64226a19e494ca1217bfbfd8c2` |
| MML median：`834f3910-246d-4c81-8285-87afb311005b` | KB `fd444069-cc1a-42d0-9617-e95fa90b6941`；document `6c9e87eb-29c2-4df0-80a5-940a77a23791`；version `1c0ae50f-188f-4081-93b2-8c4a920cbccb` | `[41292,41788)`，496 字；`3bc994cbf928866480eb7b11f4182bfdc5c7165ce1c416e565bb60f4299dc800` | 同 ID/版本/start，`end=42602`，1310 字；`a4425d707bb6789f75d3330ae2f445467c8e4206d458cf9606d60e0cc9fb1d50` |

DSM 两条均来自 case `longbench-zh-multifieldqa_zh:1a6c19fed6361cbfbfd0df3fb7ca1e01f2a2ffa0da9903bc`；median 来自 `mmlongbench-doc-mmlongbench-doc:2ff6aa9237fc777b6627dc57a486e9225ac5fb86:row-0687`。DSM 主答案 “better than 5m”和 median 主答案 Clinton 历史均正确；这三条是独立的引用身份失败，不能反向标成核心错答。

冻结原块经 `merge.py::sequential(..., preserve_sources=True)` 的 **extend 分支**可逐字重建三条历史扩大正文和 end，仍沿用 anchor ID，其他 ID 进入 `sub_chunk_ids`。这些条目 `content_rewritten=false`、长度等于 `end-start`，因此单凭长度/版本自洽不足以证明按 ID 回读一致。最早正文扩大并借旧 ID 的可复现代码阶段是 Merge extend；已保存的最早实际扩大正文位于 source projection 后 Context，不能将离线重建说成独立历史 Merge 快照。

历史完整路径核对结果：Search/Rerank 保留原 ID/正文/offset；Merge extend 离线重现扩大；旧 source projection 未逐条按 ID 核对所有已选条目，Context 留下复合正文；实际 Provider wire 包含复合正文；引用 Registry 的 cN 对应复合 ID；SSE、PG、history 中的 references 一致保存扩大正文/end；原 HTTP source 按该 ID 回读较短原块。历史独立 `GenerationRequest` 对象未保存，真实 `Evidence.from_result → prepare_generation → SourceRegistry → GenerationCoordinator._references` 用离线测试核对，不能补造历史对象。

修复后所有成为可引用证据的已选 `text/parent_text` 都在可选邻接扩展之外执行 `_project_selected()`。当前请求内 `get_source_unit` 的权威回读可复用；其余 ID 通过既有 scoped `get_chunks` 批量有限回读。`_materialize_selected()` 用原 ID、原版本、原文及 offset 还原各原块，验证 KB/document/version/enabled/type 与 locator；缺失版本拒绝，即使两侧都缺失也不通过。类型/index 与正文/offset 一同精确核对。没有建立持久化复合引用、修改数据库原块或创建向量。

后续审查确认普通 `get_chunks` 不过滤文档禁用/重解析状态，单靠chunk自身enabled和版本相等仍不足。新增三个窗口外反例先真实RED：文档disabled、parse_status processing/failed均原先 `DID NOT RAISE`。修复在有界身份投影中显式调用 `get_chunks(..., current=True)`，复用 `_read_chunks` 已有的文档enabled、parse completed、当前source_version及chunk enabled过滤；普通默认`current=False`保持原合同，`parent_text`仍可权威回读。该护栏最终GREEN结果见第6节，不能把前一测试检查点当作包含这项修复的证据。

修复后的独立身份如下；每组内 KB/document/version 承接上表，anchor 恢复原范围/哈希，而新增各原块绑定自己的 ID：

| 组 | 原始 chunk_id | 修复后 `[start,end)` | content_sha256 |
|---|---|---|---|
| DSM-1 | `1400dc3d-f641-4193-8218-0483ab6b528e` | `[5759,6238)` | `8cfaeb854d2480b8a2c8602f57f8fa037956bb098b41e31aca7f43c002b0fbc1` |
| DSM-1 | `ef2c43d4-0495-4eb1-8071-99accee1113c` | `[6162,6569)` | `ac79846f486af3de39d0c283a4ccbaa376c81022830553fb28882ac981e8e601` |
| DSM-1 | `b34c30a1-c2ba-48cd-93de-dd0c1a805c9e` | `[6569,6959)` | `8f1e1149a25627573a20701810d6a453b4d96b77ed4eae8f67518b6daf56bec5` |
| DSM-2 | `072ced3a-cc3d-4260-ae9f-f73b49fd3a0b` | `[6162,6569)` | `ac79846f486af3de39d0c283a4ccbaa376c81022830553fb28882ac981e8e601` |
| DSM-2 | `dc93c378-76df-4565-9d21-55e61c4f83d0` | `[6569,6959)` | `8f1e1149a25627573a20701810d6a453b4d96b77ed4eae8f67518b6daf56bec5` |
| median | `834f3910-246d-4c81-8285-87afb311005b` | `[41292,41788)` | `3bc994cbf928866480eb7b11f4182bfdc5c7165ce1c416e565bb60f4299dc800` |
| median | `f96f9f89-1b47-4a04-872a-53faa3a3e793` | `[41726,42164)` | `2d41f767c8a570d3a5d54c250bc781108271a8e95c7f9e0b9a50d4c3c6b0299d` |
| median | `c090bae1-733d-42a8-a110-9b37df7377cb` | `[42131,42602)` | `e3e7024ca6053d69683b27b767b79bc834bfafdd03abb4412dcf6b060a46c49c` |

三条结果均为 `FAIL_ID_BODY_RANGE_MISMATCH → OFFLINE_EXACT_ORIGINALS_PASS`。完整 Context 回放确认两题全部已选必要原块仍能进入现有预算内的最终实际消息准备结果，没有靠删除关键原文通过。既有上限不放宽；超出每组 6 块/2048 字的物化明确记录 `INSUFFICIENT_SOURCE / SOURCE_UNIT_BUDGET_EXCEEDED / omitted_ids`，其他有效来源保留；完整 Context 继续由既有编码器/预算检查。普通 `off` 路径保留兼容语义。

测试还经真实 Registry、GenerationCoordinator、SSE 事件序列、history 适配器和 source service reader 检查正文/offset/ID一致；模型、数据库连接和 source repository 均为离线替身。**这些是应用路径合同回归，不是新的 live HTTP/PG 验收。** 历史失败原件、历史引用和旧 38/41 结果均保留。

## 4. 18 题分层矩阵

层级：A＝授权语料/解析或模态输入缺失；B＝已可检索原文未召回；C＝Search 后 Rerank/TopK/Merge/source projection/Context 丢失；D＝充分原文进入实际请求后生成错误；E＝无正文 fallback 的知识补充/拒答；F＝无可判定真值；G＝核心正确而引用/展示有误。主层以可验证的最早原因判断，E/D/C 可作为伴随层。18 题没有单独归入主 E/G；三条 G 性质引用缺陷位于两个历史 PASS 控制题中。

下列短名只用于表格：LB-11、LB-57、LB-67、LB-51、LB-bc 对应完整 case_id：

```text
LB-11 longbench-zh-dureader:11b3b7afb73f5bf65a51b3083e885a60825b17421c58a828
LB-57 longbench-zh-dureader:5726f5fc6feedda99cafb275a27a8fd67483185b6fb8efa7
LB-67 longbench-zh-dureader:6770736a958b78c4c1b1a0057b34a709bdd869f307f3dc40
LB-51 longbench-zh-multifieldqa_zh:51abd2060651563dfb6982a9e5380c6dfdf7c4917056feed
LB-bc longbench-zh-multifieldqa_zh:bcedff0a6829ca68f2c33247af3bea6f5fa81c8d2d940fa2
MML-row-* mmlongbench-doc-mmlongbench-doc:2ff6aa9237fc777b6627dc57a486e9225ac5fb86:row-*
```

### 4.1 历史 FAIL：10 题

| case / 数据集；原 q0 | 参考/主答案与独立次级评价 | 主层、最早失效与充分输入 | 最小下一步及缺失证明 |
|---|---|---|---|
| `scifact-1304` / SciFact；`Tirasemtiv targets cardiac muscle.` | 绑定原文为 selective fast-skeletal-troponin activation；回答 cardiac/inhibitor 错。补充品牌/适应症无依据；引用无回读，UNVERIFIED | **C**，伴 E；目标块 `d5d05a0b-eaa7-4368-8006-e8e5159bc039` Search 已有，BGE `0.000371902 < 0.3`，最终 metadata-only；充分输入 NO | 检查短断言与 Rerank 输入匹配；不改全局阈值。qrels 非真假标签，别名/机制不凭文件名外推；不是充分证据生成失败 |
| `miracl-zh-692002#0` / MIRACL；`美国有哪些经济学专家？` | 罗默原文明确美国经济学家；回答另把名字生成为 George Stiglitz。历史引用身份 PASS，国籍/补充事实未充分支持 | **C**，伴 D；罗默块 `4ddc4528-85d8-4aa9-936d-45edb5d1c41e` `0.1455925 < 0.3`；充分输入 NO | 先检查目标排序丢失，再独立检查姓名翻译；现有消息不能完整建立国籍，不硬凑 D 对照 |
| `miracl-zh-91294#0` / MIRACL；`人马座有哪些临近星座？` | 原文有必要名称/方位，回答将摩羯座改 Aquarius，混淆蛇夫座/巨蛇座；补充 FAIL，引用无有效回读 UNVERIFIED | **D**；`3f6a7679-4adf-4f93-9c2f-5d1bfd649478` 完整进入 wire；充分输入 YES | 固定消息对照名称和关系，不能只查子串；新模型成对验证 NOT_RUN |
| LB-57 / LongBench；`辐射避难所怎么刷箱子` | 原文任务循环/删除任务；最终回答清房间取战利品。补充 FAIL；无引用回读 | **C**，伴 E；`57d67ff3-557a-4377-90c9-c8d84ec77bac` Rerank `0.9996898` 后旧 source-unit 超预算，整轮无正文；充分输入 NO | 离线检查预算投影和玩法来源；具体超限锚点未独立保存，不能补造 |
| LB-51 / LongBench；`2005年全市农村劳动力技能培训了多少人？` | 参考 2005 年培训 **2.2万人**；回答 22,000 “training sessions”，单位错误。76,000 为转移就业人数，来源另立；引用与补充 PASS | **D**；`39aa8800-a6cd-48a6-8ec6-95ce8eb8a7d1` 的年份/条件/人数完整送达；充分输入 YES | 固定消息对照数字及单位，不能数字匹配即通过；新模型对照 NOT_RUN |
| LB-bc / LongBench；`这份技术规范的实施日期是什么？` | 冻结目标 HJ1134—2020 实施日 2020-08-27；消息为其他规范日期，未解决目标；引用 UNVERIFIED，其他规范日期补充单独 PASS | **C**；目标 `6d02e5b9-e416-42ec-b512-f9cd37b5c50b` `0.1046835 < 0.3`；充分输入 NO | 核对无历史“这份”指代与 document_scope；宽 KB且 q0 无规范名是伴随歧义，不拿另一规范日期判通过 |
| MML-row-0684 / MML；`Do more men or women (in %) think a female president will be elected in a lifetime?` | 冻结图表男81%/女78%；送达总体79%不足以比较性别，拒答未解题。已有引用/补充 PASS | **A**，文本模态输入不足；`126d0c0a-98a7-4934-9eef-766ef8df4dc7` 仅相关整体文本送达，非充分性别数据；充分输入 NO | 检查现有解析图表能否形成可用输入；无全量解析文本证明，不能断言整个索引缺81/78 |
| MML-row-0431 / MML；`How many layers does the neural network mentioned in the PPT for distinguishing faces have?` | 冻结参考5层，依赖图示结构；最终拒答，补充未捏造但核心未答；引用 UNVERIFIED | **A**，伴 C/E；最终无图且 metadata-only，现有候选全被 Rerank 淘汰；充分输入 NO | 先核对原图结构输入可用性；缺全量解析文本/图像覆盖证明，审查者看图不等于模型见图 |
| MML-row-0434 / MML；`Which dataset was introduced in the 'coding time' section of the PPT for training?` | PDF页为 Cat vs Dog；保存30候选与 wire 均未见目标文字，最终 Expensive 拒答；补充 PASS，引用 UNVERIFIED | **A/B 未决**，伴 C/E；最早可观察为 Search 目标缺失；充分输入 NO | 只读核对现有解析结果是否含该句，再区分 A 解析遗漏或 B 漏召回；不能因PDF存在就认定已可检索 |
| MML-row-0430 / MML；`How many times does the image of a tiger appear in the PPT?` | 冻结 typed Gold 为整数0；最终拒答不等于答0；补充 PASS，引用 UNVERIFIED | **A**，伴 C/E；全PPT图像计数、最终无图且 metadata-only；充分输入 NO | 先证明全页图像输入可用；未对68页独立逐图计数，0来自冻结Gold，不声称视觉重验 |

### 4.2 历史 PARTIAL：5 题

| case / 数据集；原 q0 | 参考/主答案与独立次级评价 | 主层、最早失效与充分输入 | 最小下一步及缺失证明 |
|---|---|---|---|
| `miracl-zh-5721594#0` / MIRACL；`日本有哪些动画制作公司？` | 原文中文清单完整，主清单部分正确；英文别名未绑定，如 `Studio Deen (新銳動畫)`，430/600冲突未说明。引用身份 PASS，补充 PARTIAL | **D**；`276bb106-7489-48b2-bc35-449b6b1d4630` 完整送达；充分输入 YES | 固定消息检查实体翻译和冲突表达；只做来源复核，不借外部公司知识补 Gold |
| `miracl-zh-704170#0` / MIRACL；`醇类化合物有哪些特征？` | 回答部分特征正确，原文双键改为 triple bond、醇盐混作 alcohols；引用 PASS，补充 FAIL。原文 sp 表述自身不规范，保留来源限制 | **D**；`31ef880a-0230-4788-89b4-933049c5c660` 完整送达；充分输入 YES | 固定消息核对术语；不将审查者纠正改写成原始知识来源 |
| LB-11 / LongBench；`谷丙转氨酶和谷草转氨酶偏高的原因` | 参考病因清单多项缺失，fallback只给部分病因；引用/补充 UNVERIFIED | **C**，伴 E；`be66f052-9317-4030-b2e3-6a16ecd2c281` Rerank `0.9977775` 后旧 source-unit 超预算整轮清空；充分输入 NO | 离线检查分块预算遗漏；独立 Merge 快照/触发锚点缺失，不声称本轮答案已改善 |
| LB-67 / LongBench；`美国的节日` | 冻结参考清单已检索，fallback只列部分节日；引用/补充 UNVERIFIED | **C**，伴 E；`6f0e75a5-b74d-4c0a-ab0b-3c0505c6b386` Rerank `0.9992894` 后 source-unit 超预算；充分输入 NO | 核对遗漏源单位，常识补充单独标记；具体超限锚点缺保存快照 |
| `hotpotqa-5a729fe55542991f9a20c53b` / Hotpot；`What man is credited eith inventing both the Leyden jar and the Tribometer?` | 冻结答案 Pieter van Musschenbroek；仅正确人名出现，回答仍不能确认 BOTH 共同发明关系，故 PARTIAL。引用身份 PASS，已述补充 SUPPORTED | **C**；Tribometer `41295870-5f65-458c-b045-85280c1184a3` Search/Rerank已过，`0.6711833 ≥ 0.3`，首次排除在 source projection；充分输入 NO | 本轮强制身份投影离线证明原文送达；新答案未测。`eith→with` 被字面改写保护拒绝是事实，不据此预设阈值/桥接/P5修复 |

Pieter 的具体分支与三条引用的 extend 分支不同：短 Tribometer 原文经 `expand_neighbors` 即使无邻居仍带 rewritten 标志；它是第四文档，未进入旧可选扩展组，随后出现 `UNMATERIALIZED_SELECTED_PROJECTION`。因此原块通过 Rerank但未进入最终 Provider 消息。新增冻结回归证明窗口外条目仍经强制投影恢复原文，不能把“已修投影”写成“完整 BOTH 答案已在真实模型通过”。

### 4.3 历史 UNVERIFIED：3 题

| case / 数据集；原 q0 | 真值与当前答案/次级评价 | 主层及可观察伴随故障 | 最小下一步及缺失证明 |
|---|---|---|---|
| `scifact-1351`；`Upregulation of dynein in Drosophila neurons increases the number of microtubule plus-ends growing toward the cell body of each neuron.` | 已送原文是正常/缺失dynein，不证明上调实验；拒答保留。引用无回读 UNVERIFIED，补充 PARTIAL | **F**；`242ea540-5ec0-40af-982b-f002180ce6d3` 已送达，但回答目标所需充分输入 NO | 先补上调条件的可判定来源；无官方QA真值，不能标Cheap失败 |
| `scifact-247`；`Chemical injury represses transglutaminase 2 activity.` | 已送TG2结构/胰岛素原文无化学损伤作用方向；拒答保留，引用 UNVERIFIED，补充 PASS | **F**；`fa490391-ac34-4c22-952e-e528d8d5bda2` 已送达，充分输入 NO | 先核对目标方向性实验来源；qrels仅相关性，不能决定断言真假 |
| `scifact-621`；`Individuals with Alzheimers who participate in six months of physical activity improve cognitive function for up to 18 months.` | 原文志愿者“不满足痴呆标准”，不能据此判Alzheimers人群；拒答未解决但不记模型失败。引用/补充 UNVERIFIED | **F**，伴 C/E；`9c56759d-e0a2-41f3-8d7b-745c4aa52b26` 2395字、Rerank `0.9987992`，source-unit超过2048后遗漏；充分输入 NO | 分别检查预算可见性和人群真值；无Alzheimers条件可用真值，保留UNVERIFIED |

### 4.4 QU、阶段送达及最终模型对照索引

下表 `S/R/P/C/W` 分别指目标原文在 Search、Rerank、source projection 后正文、Context、实际最终 Provider wire 的观察；`✓/—`为有/无，`相关✓`表示仅相关文本已到但不含解题必要数据。全部案例 source projection 前独立 Merge 正文均为 `NOT_INDEPENDENTLY_SAVED`。各阶段完整 ID、分数、scope、版本、hash 与证据文件见 results 的对应 `failure_layers`，不凭候选数推定语义充分。

所有有效 query 均精确等于本节表中的 q0；除了 Pieter，无 QU 回退，decision 均为 ACCEPTED，reason为空。全部最终检索计划为 PLAIN/单条主查询，UNKNOWN不等于错答。

| case | QU原始建议 → 最终类型 | S/R/P/C/W | 实际最终请求模型 |
|---|---|---|---|
| scifact-1304 | DIRECT → DIRECT | ✓/—/—/—/— | Qwen/Qwen3.5-4B |
| scifact-1351 | UNKNOWN → UNKNOWN | ✓/✓/✓/✓/✓ | Qwen/Qwen3.5-4B |
| scifact-247 | UNKNOWN → UNKNOWN | ✓/✓/✓/✓/✓ | Qwen/Qwen3.5-4B |
| scifact-621 | UNKNOWN → UNKNOWN | ✓/✓/—/—/— | Qwen/Qwen3.5-4B |
| miracl-zh-5721594#0 | DIRECT → DIRECT | ✓/✓/✓/✓/✓ | Qwen/Qwen3.5-4B |
| miracl-zh-692002#0 | DIRECT → DIRECT | ✓/—/—/—/— | Qwen/Qwen3.5-4B |
| miracl-zh-704170#0 | DIRECT → DIRECT | ✓/✓/✓/✓/✓ | Qwen/Qwen3.5-4B |
| miracl-zh-91294#0 | DIRECT → DIRECT | ✓/✓/✓/✓/✓ | Qwen/Qwen3.5-4B |
| LB-11 | DEPENDENT → DEPENDENT | ✓/✓/—/—/— | Qwen/Qwen3.5-4B |
| LB-57 | 非法值 `kb_search` → UNKNOWN | ✓/✓/—/—/— | Qwen/Qwen3.5-4B |
| LB-67 | UNKNOWN → UNKNOWN | ✓/✓/—/—/— | Qwen/Qwen3.5-4B |
| LB-51 | UNKNOWN → UNKNOWN | ✓/✓/✓/✓/✓ | Qwen/Qwen3.5-4B |
| LB-bc | UNKNOWN → UNKNOWN | ✓/—/—/—/— | Qwen/Qwen3.5-4B |
| Pieter | DEPENDENT → UNKNOWN；FALLBACK `REWRITE_NOT_LITERAL_REFERENCE_SUBSTITUTION` | ✓/✓/—/—/— | Qwen/Qwen3.5-4B |
| MML-row-0684 | UNKNOWN → UNKNOWN | 相关✓/相关✓/相关✓/相关✓/相关✓ | Qwen/Qwen3.5-4B |
| MML-row-0431 | DEPENDENT → DEPENDENT | 目标未见/—/—/—/— | Qwen/Qwen3.5-4B |
| MML-row-0434 | UNKNOWN → UNKNOWN | 目标未见/—/—/—/— | deepseek-flash |
| MML-row-0430 | UNKNOWN → UNKNOWN | 目标未见/—/—/—/— | Qwen/Qwen3.5-4B |

模型列为保存的实际最终请求/adapter绑定，响应reported model的原始证据仍留在历史results；请求路由名不能独立证明底层模型身份。Rerank主阈值0.3、空集最佳候选floor0.15均未改动；floor只适用于最佳候选，不能因某目标接近0.15就认定必应保留。

## 5. 固定证据 Cheap/Expensive 对照包

[manifest](evidence/s8_6_3/fixed_evidence/manifest.json) 保存 **2个 D 类 FAIL + 2个已正确控制题**，没有拿缺证据题充当模型能力题：

| 请求文件 | case | 用途 | 冻结 body SHA256 |
|---|---|---|---|
| `requests/01.json` | miracl-zh-91294#0 | D：星座名称/关系失败 | `0e916200fbad365a0f2a3b4287a55ae03a723a85056983515c17f111c734d272` |
| `requests/02.json` | LB-51 培训人数 | D：单位失败 | `c4d76f228f54ac558df9a7037caf2e7f69e5c88659e1787c063869466f6a541b` |
| `requests/03.json` | DSM题 | 核心答案正确控制 | `f8715782588531ea5b8bda82ca543db9e1e9ce106eab5f76fece6f54e5de4598` |
| `requests/04.json` | MML-row-0687 | 核心答案正确/配置敏感控制 | `a3c1e04519ab63a45a96753b09d09b418843dddc2057f6f403186eac5a3b557a` |

四份请求均逐字保留历史实际 System/User/上下文和参数；manifest分别记录 body/messages/system/user SHA256、参数、原请求artifact与哈希。Gold、历史回答和评分在独立 `scoring_only.json`，`load_inputs()` 不打开评分文件；它只允许 `requests/` 下的文件、检查精确hash和参数白名单。离线测试还覆盖评分文件写入无效JSON/marker仍不被加载、正文篡改拒绝、评分路径冒充请求拒绝。Gold隔离依据为精确冻结请求相等及加载边界，不要求答案词在原上下文中绝不存在。

`render_pair()` 用既有 ProviderModel 的请求renderer，对 Cheap `Qwen/Qwen3.5-4B` 与 Expensive `deepseek-flash` 渲染相同消息，均 `max_tokens=2048`、thinking关闭、temperature0.7；DeepSeek适配器使用 `thinking={"type":"disabled"}`，Cheap使用 `enable_thinking=false`。不要求两个模型Token数相等。当前四例均 `OFFLINE_RENDER_PASS`，只是准备可复用的输入，没有transport、授权对象、账本或任何真实发送。

两个正确控制刻意保留历史 wire，包括旧复合引用身份；它们用于固定同证据模型比较，**不能作为修复版引用HTTP证明**。MML0687不再固定标为Cheap必败。历史仅有2次Expensive生成，不能说成38题的同证据Cheap/Expensive比较，不能据此制定升级收益或新P5阈值。后续真实比较必须有覆盖新版/样本的新授权，并复用既有Registry/SendGate/LocalExecution与后台入口；本包不复用旧request_id或CLOSED授权。

## 6. 命令、退出与验证状态

本节记录本轮已执行的本地离线命令。原始stdout位于 `E:\RAG quention\var\reports\s8-6-3\`；可随仓库核查的副本、哈希与退出来源见 [validation.json](evidence/s8_6_3/validation.json)、[test_runs](evidence/s8_6_3/test_runs/)及[artifact_runs.json](evidence/s8_6_3/artifact_runs.json)。**未实现的命令不得报告通过。**

| 实际命令/阶段 | 退出码 | 结果与证据 |
|---|---:|---|
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -X utf8 -B -m pytest tests/weknora_port/test_source_identity.py -q` | 1（主代理原执行回执） | `red-source-identity.txt`：**9 failed, 2 passed**。旧stdout本身未内嵌退出码，保留错误原件；argv见validation |
| 首次GREEN尝试（原始完整argv未独立保存） | 失败；原stdout无内嵌退出码 | `green-source-identity-attempt1.txt`：**3 failed, 42 passed**；测试工具误用了不存在的 `SourceRegistry.render_citations`，原始AttributeError保留，改为真实 `expand_text()` 后继续验证，不把本次失败抹去 |
| 文档状态护栏前受影响测试检查点，精确argv见 `targeted-final-v3.run.json` | 0 | `targeted-final-v3.txt`：**219 passed in 10.66s**，日志SHA256 `a4f2722e775257832dd7b37a314a86a0b1feb64af7a28e3f36a4252aa0dd7d14`；旧v2的218项stdout也保留，不叠加计数 |
| 文档状态护栏RED（原stdout无完整argv） | 1（主代理原执行回执） | `document-current-red.txt`：**3 failed, 34 deselected**，三项 `DID NOT RAISE` 原件保留；最终回归覆盖这三个反例 |
| `& 'E:/RAG quention/.venv/Scripts/python.exe' -m pytest -q --tb=short tests/weknora_port/test_knowledge_storage.py` | RED 1 → GREEN 0 | `storage-current-red.log`：**11 failed, 9 passed**；`storage-current-green.log`：**20 passed**；分别有 `.command.txt/.exit.txt` |
| 第一次包含文档状态护栏的12模块测试（v4；argv见run metadata） | 1 | `targeted-final-v4.txt`：**1 failed, 269 passed**；旧窗口外测试stub缺`get_chunks`，并按旧语义丢弃整个raw3。补齐权威原块回读，改验精确原文保留且未核验suffix排除；未改历史Gold |
| 包含current-document护栏的最终12模块测试（原样命令见下方） | **0** | **PASS，270 passed in 8.78s**；`targeted-final-v5.run.json/.txt`，日志SHA256 `907a38a44c60149059a16d1dcfaaefbf24aa30fd5e7fe754772f8dae79445384`；218/219为此前检查点，不叠加 |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -X utf8 -B -m scripts.s8_6_3_offline_evidence --snapshot 'E:/RAG quention/var/reports/s8-6-2-r3/followup40/actual40-final-extracted.json' --output docs/evaluation/S8_6_3_SOURCE_IDENTITY_AND_FAILURE_LAYERS.results.json` | **0** | PASS，18分层、3引用复现、4固定请求；`offline-evidence.txt` SHA256 `4353e9b5bf7ef1ef0581ba7e7006fb0033c744aef929790aa9b99dacfb5d8c02`；不含真实模型调用 |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -X utf8 -B -m scripts.s8_6_3_fixed_evidence docs/evaluation/evidence/s8_6_3/fixed_evidence` | **0** | PASS，`fixed-evidence-render.txt`：4例 OFFLINE_RENDER_PASS，provider_calls=0；SHA256 `2c3731ec32faade6dfd06b2064d13012d8dc1c7432bf1da68ee3ef42a0e3b7fa` |
| 新模型成对生成 / 新真实HTTP / 真实模型后台批次 | — | `NOT_RUN`；无新外发授权，不进行发送 |
| 全仓回归 / 官方数据集指标 / 新生产验收 | — | `NOT_RUN`；本轮不是里程碑/生产发布 |

最终测试命令工作目录为本工作树，原始argv与子进程退出码见 `test_runs/targeted-final-v5.run.json`；Windows PowerShell等价原样调用如下：

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -X utf8 -B -m pytest tests/weknora_port/test_source_identity.py tests/weknora_port/test_source_evidence.py tests/weknora_port/test_merge_source_retention.py tests/weknora_port/test_bounded_source_dedup.py tests/weknora_port/test_s86_strategy.py tests/weknora_port/test_s86_bridge_strategy.py tests/weknora_port/test_s86_pipeline_integration.py tests/weknora_port/test_generation_wire.py tests/weknora_port/test_history_storage.py tests/weknora_port/test_s863_fixed_evidence.py tests/weknora_port/test_knowledge_storage.py tests/weknora_port/test_source_reliability_wire.py -q
```

必要测试覆盖：复合正文长度/end自洽但ID读回不同；可选窗口之外的复合条目；三条真实引用与两个场景完整Context；Pieter短块标志；跨KB/document/version及禁用来源；缺失版本；单块精确保持和请求内读回复用；预算不足明确遗漏且保留其他有效来源；off兼容；Registry/生成/SSE/history/source reader离线路径；固定证据和Gold隔离。不重新跑40题。

## 7. 风险、遗留与下一步

已修引用缺陷属于P1证据身份问题；本轮离线合同覆盖，修复版真实端到端source回读仍 `NOT_RUN`，不能宣称生产已修复。C类预算丢失、Rerank目标排除和D类错误仍是独立问题；本次没有扩大到算法/阈值/Prompt修复，也没有把它们自行批准延期。

尚缺证据集中于：source projection前独立Merge快照、MML完整解析文本/图表输入覆盖、row0434的A/B区分、row0430独立全页视觉计数、SciFact三个条件真值，以及新模型同证据结果。A层标注仅表示保存材料中的可用输入缺口，不证明整个持久索引绝无目标内容。F层继续UNVERIFIED；旧UNKNOWN及账本不清理。

本轮到此停止：离线引用合同通过、18题原因与不确定性已记录、固定证据输入包可复用。后续最小验证是Owner批准新范围后，选择最少必要引用/送达案例并复用既有后台HTTP入口；第一次真实模型后台批次还须验证SSH返回后继续、检查点/最终结果落盘、新连接只读回执，状态未知不得重新启动。同证据模型比较尚未执行。ECS40未覆盖COMPARE/PARALLEL，独立R3首题已证明英文比较；后续若获授权只补缺失真实路径，不重跑40题。

当前未获Owner最终验收，未打Tag；没有官方准确率或“整个项目生产可用”的声明。
