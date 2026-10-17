# P30：Query Rewrite / Search 稳定性诊断

## 结论

**P30_COMPLETE_DIAG_ONLY**。没有复现需要立即修复的程序缺陷，生产代码未改。发现的是 Rewrite 模型输出波动、已有语言约束未被模型遵守，以及改变查询文本后检索分数的实际变化。不能把 P29 候选变化全部归因于 Rerank，也不能证明每一处变化的历史因果关系。

- 源码：`a974736d6a26e07969a110975224e490d9ca461e`。
- 固定上游：`3e8b0bfc80b845b2d4b2ed683994748741450a97`。
- 本轮新增云模型请求 **0**、Token **0**；未开启围栏，未操作历史 UNKNOWN、授权或账本。未启动 API/Worker，未入库、写库或创建索引。
- P29 报告、机器结果、原始日志及索引快照保持原样。新诊断工具和证据单独保存。

## 三组实际差异

on/off 是 P29 的 Rerank 配置标识，两侧都启用了 Rewrite，**不是 Rewrite 开关**。

| 样本 | on 实际初始 Embedding 输入 | off 实际初始 Embedding 输入 | P29 Search | 本轮核实与分类 |
|---|---|---|---|---|
| 0434 | PPT中coding time部分引入的用于训练的数据集是什么 | PPT中coding time部分介绍的训练数据集是什么 | 各30，交集27；Kaggle 片段仅 off，排29 | 措辞波动、核心问题基本保留；两侧转中文违反现有保留语言指令。当前关键词结果两侧88条的 ID、分数、顺序完全相同；目标均排62。不能证明措辞变化单独导致历史 Top30 差异。 |
| 0959 | What are used for long-distance communication as introduced in this slide? | What are used for long-distance communication as introduced in this slide | 各30，交集27 | 标点波动。实际 Embedding 请求字符串不同，但旧向量不可得。当前同快照关键词实测13条分数及顺序变化，每个固定查询均稳定；这是已验证的标点检索影响，不自动构成 Bug。 |
| 0958 | 什么是反射面 两个反射面的例子 | What are two examples of reflecting surfaces? | 各9，交集9，顺序不同 | on 违反已有语言要求，并扩展出“什么是”概念问题；属于模型协议遵循及语义泛化问题。候选集合未改善，排序有变化；没有足够记录分离语言、向量、关键词和扩展的贡献。 |

实际 Rewrite 请求体在每对内部一致：SiliconFlow Xing，`temperature=0.3`、`max_tokens=150`、`enable_thinking=false`。Prompt 已要求保留原问题语言，不需要新增语言规则。相同请求产生不同输出符合非确定性生成特征，尚未发现参数传递错误。

**证据边界：**表中改写取自真实后续 Embedding 请求，不是伪造 Rewrite 回执。P29 没有保存完整原始 Rewrite JSON、历史查询向量或融合前 vector/keyword 排名。不能补造这些输入，也不能将本轮当前文档向量代理称为历史查询向量。

### 0434：定位到 Search / 融合 Top30 边界，未证明完整因果

目标 Chunk：`085643dc-5ebb-4b8a-b7f1-75427da40015`，document `4019d2aa-c21d-42c2-8e8f-4d5aee4039ce`，chunk_index=17。P29 off 最终 Search 排29，融合分数 `0.01069431051108968`；on 的最终30条不含它。只能确认差异已经发生在 Rerank 前。

当前只读关键词查询中，两种改写均返回88条，目标均排62，BM25=`0.873098`，无同分目标。单 KB 主召回 SQL 池为150，向量内层上限200；现有范围88/9条均小于池上限，**未发生此处 LIMIT 截断**。随后 `hybrid_search()` 对融合结果执行 `[:match_count]`（本例30），确有更早的候选边界；后面的 FilterTopK 无法救回此前未返回的候选。

因此可以解释它为何对融合排名敏感，但不能声称 on 时恰好排31，或“引入/介绍”必然改变了向量并造成掉出。缺失历史分支排名与查询向量，向量分支/融合贡献的精确原因仍未证明。

### 0959：问号影响已在关键词边界复现

生产 `PostgresRetriever.keywords()` 原样绑定查询到 `content ||| %s`，按 `paradedb.score(id)` 降序取150。固定 Go 同样使用 `content ||| ?` 原样传查询，不是 Python 特有的去标点/编码错误。

同一个只读快照中，两条查询各运行三次，各自有序 ID+score 指纹完全一致；跨查询13条分数和顺序不同，成员集合均为88条。没有保存 parser tree、分析后 term 或 scorer explanation，**不推断问号是 wildcard 或某个特定操作符**。同样不能据此证明它独立造成了 P29 的三个候选替换，或推断历史向量差值。

### 0958：扩展偏宽，但没有已证实的处理错误

主召回不足30才执行扩展；0434、0959 两侧主召回已满30，没有执行扩展。0958 两侧实际各有初始查询加四条扩展，英文侧包含独词 `examples`、`reflecting`、`surfaces` 及 `two examples reflecting surfaces`；中文侧包含概念、例子及分词组合。完整实际输入见 `p29-input-audit.json` 和 `offline-determinism.json`。

现有 `expand_queries()` 从分词、关键词组合、引号片段、空白/标点分段和中文问句前缀生成，执行长度限制、去重和数量上限。通用英文词来自该既有规则，不能仅凭它们宽泛就判为代码 Bug。冻结改写重算的扩展集合与实际请求集合相同；供应商调用完成顺序不同不等于应用合并顺序不同。

P29 两侧最终候选同为9条，未证明召回集合质量改善。没有原始各扩展分支分数，不能量化某个独词对排序的贡献；本轮不新增黑名单、不调整扩展策略。

## 实际链路与确定性

| 函数/边界 | 核查结果 |
|---|---|
| `query.py::understand_query / parse_understanding` | q0 独立保留；默认先用 q0；关闭、缺模型、调用失败及异常/空改写按已有合同保留 q0；取消和超时传播。成功改写用于主检索，并不保证额外检索 q0。 |
| `query.py::expand_queries` | 基于成功改写生成，q0用于去重；重算的实际执行扩展集合相符。 |
| `search.py::search / deduplicate` | Scope 校验保留；主查询后追加扩展；`asyncio.gather` 按输入顺序返回，20种完成时序未改变合并顺序；按 ID/内容先到去重。 |
| `fusion.py::fuse_or_deduplicate` | Weighted RRF 固定有序输入重复100次一致；默认k=60，vector=0.7、keyword=0.3。合成夹具验证首排名及重复向量元数据选择，不能冒充历史完整重放。 |
| `storage/hybrid.py::hybrid_search` | vector/keyword 查询顺序和融合池/Top30 路径明确；没有证明本例 SQL 池提前裁掉目标。 |
| `filter_top_k.py::_compare / filter_top_k` | 已有 score、knowledge ID、type、chunk_index、ID 次级比较；六组最终 Search 快照各重复100次稳定，同分正反输入结果一致。 |
| `storage/postgres.py` | SQL 同分缺 ID 次级排序，与固定 Go 边界相同；有理论上的跨计划同分顺序风险，但本轮各固定查询三次均未复现，不能认定为 P29 根因。 |

实际 SQL 在 PostgreSQL `127.0.0.1:56423`、库 `weknora_python_port_20261013`，system identifier `7696088403504697382` 执行。事务 `REPEATABLE READ / READ ONLY`，快照 `230956:230956:`。当前三表共3文档、97 Chunk、97 embedding 的全行摘要与 P29 index-after 一致。

另以索引中真实1024维文档向量（index ID100310）作为固定代理，原生向量 SQL 重复三次结果相同。**这是当前文档向量代理实验，不是历史查询重放**。未发现相同输入的 Search 漂移；也不能据有限重复证明所有并发、查询计划及索引环境永远确定。

## 执行命令与证据

以下命令在仓库根目录执行，Python UTF-8，PYTHONPATH 含仓库、`E:\WK\deps\reader` 及 `var\weknora-port\runtime-deps`。

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -B scripts/p30_search_stability.py
```

退出码0：六组快照共600次；合成 RRF100次；真实 `search()` 配合合成仓库的完成时序20组，全通过。此项是离线测试，非云端/历史向量验收。工具首次执行因诊断脚本误把 embedding 记录字典当字符串而退出1，已只修诊断工具并保留 `offline-first.stdout.txt`，不算生产缺陷。

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest tests/weknora_port/test_fusion.py tests/weknora_port/test_hybrid_storage.py tests/weknora_port/test_pipeline.py -k 'not faq and not images and not image and not rerank and not merge and not neighbor and not render and not web_composite and not stored_history and not html_data and not affinity' -q -p no:cacheprovider --basetemp=E:\WK\run\p30-20261017\pytest
```

退出码0：**41 passed, 30 deselected**。覆盖相关混合检索、Scope、查询解析/降级及合并行为，未运行全项目回归。SQL 调用成功，四个关键词条件各三次及一个文档向量代理三次；实际 SQL、绑定参数、会话设置、全部有序结果和身份保存于 `sql-diagnostics.json`，微量源码补充在 `sql-code-notes.json`。没有新增云调用。

新证据目录：`docs/evaluation/evidence/p30-query-search-stability-20261017/`；运行原件：`E:\WK\run\p30-20261017`。机器结果同名 `.results.json`。来源 SHA、输入文件哈希、P29 原始 ZIP 哈希见 `source-binding.json`；新证据哈希见 `manifest.json`。Search 摘要中未捕获的 source_version 保留 null，不能据此伪造版本认证；来源、版本、权限路径未修改，相关 Scope 回归通过。

## 遗留与下一步建议

1. **最值得后续验证的是现有 Rewrite/Expansion 的语言遵循、语义保真和扩展区分度**：已有语言指令仍被模型忽略，且0958扩展含通用词。应先冻结更多正负例做限定对照；本轮不修改 Prompt、模型或规则，不以三例宣布整体优化收益。
2. 0434 完整历史因果、0959 问号的引擎内部解释、0958 单个扩展贡献仍缺证据。若后续确需做因果对照，应保存原查询向量和融合前分支结果，再固定输入；新的云请求不能重建“完全相同”的旧向量。
3. SQL 同分次序属于未复现风险；当前未发现1024维 HNSW索引属于配置/性能观察，不能将其归为本轮不稳定根因。本轮不改排序规则、不建索引、不扩大开发。

没有改变 Rerank 开关/阈值、Top1、Fallback、路由、模型、Caption、Gold、Chunk或索引。继承的 `.serena/project.yml`、`progress.md` 及既有未跟踪文件保留，不纳入本任务提交。完成后停止。
