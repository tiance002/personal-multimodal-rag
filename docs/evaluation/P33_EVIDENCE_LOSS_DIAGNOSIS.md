# P33：必要证据在 Rerank → Merge → Context 中的丢失定位

日期：2026-10-17
状态：P33_COMPLETE_DIAG_ONLY
代码基线：0a956fa6540177fd1d42904ae528d81dc3424739
固定上游：WeKnora v0.8.2，3e8b0bfc80b845b2d4b2ed683994748741450a97

## 结论

P32 的四个冻结样本表明，三道失败题的必要事实都在 Rerank 的选择结果中消失；已观察到的 Merge 和 Context 没有再丢弃已保留结果。row-0959 正例从 Search 经 Rerank、Merge 到实际 Cheap 请求，证据完整保留。

没有证据证明这是 Python 特有程序错误。P32 没有保存 Rerank 响应正文，因此 0434 的原始模型分数、返回 index 和触发的过滤分支都是 UNKNOWN。本轮不把空结果推断成“低于 0.3”或归因于某一个阈值。Hotpot 与 Omni 的正确候选确实发给了 Rerank，但其原始分数同样缺失；只能确认它们没有进入保留结果。

因此本轮不改生产代码，不调 Rerank 阈值、Prompt 或路由。阶段定位已完成，供应商分数级根因保留为 UNKNOWN。

## 核心阶段表

| 样本 | 必要事实是否入库 | Search | Rerank | Merge | TopK | Context | 最终丢失阶段 | 根因类型 |
|---|---|---|---|---|---|---|---|---|
| MML row-0434 | 是；目标文本在 Chunk 085643dc-5ebb-4b8a-b7f1-75427da40015 | 30条；目标 rank 15，Search 分数 0.0124252492 | 30条请求；目标位于 provider input index 14，466 字符中仍含 “Kaggle's Cat vs Dog dataset”；保留0。原始 response、目标分数和返回 index 未保存 | SearchNothing 结束该路径；Merge 未运行 | 未到达；独立快照未保存 | 0条；实际最终请求不含 Kaggle/Cat vs Dog | Rerank 输出边界；更细过滤分支 UNKNOWN | Rerank 选择结果为空。不能证实是模型低分、阈值分支或 index 异常，也无 Python 特有 Bug 证据 |
| Hotpot 5a7631cd5542994ccc91870c | 是；地点事实在 Search 命中的 The Frost Place Chunk 31417c7c-7abf-4723-956a-74bf7ee14683 | 30条；事实 Chunk rank 15、分数 0.0131263326 | 30条请求；事实 Chunk 在 provider input index 14，原文明确含 Franconia, New Hampshire；最终保留2条其他 Chunk，目标分数未保存 | 2→2，ID 与 Rerank 保留结果相同 | 独立 TopK 快照未保存；最终 Context 两个 ID 均与 Merge 输出一致 | 2条；Franconia / New Hampshire 未进入模型可见内容；回答错为 Portland, Maine | Rerank | 必要事实 Chunk 被 Rerank 排除。Merge 和后续 Context 未丢失保留项；原始评分和精确过滤条件 UNKNOWN |
| Omni page007 矩阵 | 是；同一 document/source_version 的 image_ocr Chunk 已启用、index_status=ready、embedding_enabled=true | 30条；OCR Chunk 51316a20-67fd-4732-b520-c049999933d0 rank 5、分数 0.0144277674 | 30条请求；OCR 文本在 provider input index 4，含矩阵系数及 0、8、-9；最终只保留 Summary。保留 Summary 的分数 0.7660 不是 OCR 分数；OCR 原始分数 UNKNOWN | 1→1，保留 Summary | 独立 TopK 快照未保存；Context ID 与 Merge 输出相同 | 1条 Summary；矩阵和目标数字不在实际 Cheap 请求中，回答无依据给出 6、10、12 | Rerank | OCR 已入索引、被 Search 召回并送入 Rerank，但未被保留。缺少原始分数，不能断言具体阈值原因；未见 Python 特有 Bug |
| MML row-0959 正例 | 是；目标 Chunk df6098cb-68e6-4546-b9e0-8682b4bf19e9 | 30条；目标 rank 2、分数 0.5968030684 | 30→2；目标在 provider input index 1 并保留，保留 model_score 0.5237 | 2→2 | 独立 TopK 快照未保存；最终 Context 与 Merge 输出 ID 一致 | 无；Context 和实际请求包含 ionosphere、microwaves、satellite | 未丢失 | 正例显示该次 Rerank→Merge→Context 路径可以保留有效文本证据；这是 P32 历史对照，本轮没有重问 |

### 样本细节与来源身份

- **0434**：实际 Query Rewrite 响应文本没有保存；实际 Embedding/Rerank 输入为“PPT中'coding time'部分介绍的训练数据集是什么”，两者一致。Search 与 Rerank 请求的顺序对应已用 Search rank−1 映射核对，目标候选正文仍包含 Kaggle 事实。最终 SearchNothing/Fallback 不含目标事实。
- **Hotpot**：QID 为 5a7631cd5542994ccc91870c；问题询问 Jeanne Marie Beaumont 在 2007–2010 主持的 seminar 位于哪里，Gold 为 Franconia, New Hampshire。实际输入改写文本为“Jeanne Marie Beaumont 2007-2010 研讨会 举办地点”。已找到的确切地点 Chunk 进入了 Rerank 请求，但最终上下文只包含另外两条保留结果。Search 命中某文档不等于最终上下文含有需要的事实。
- **Omni**：只读 PG 核验的数据库 system identifier 为 7696088403504697382，KB 为 b66d8ae4-8cfe-4c9b-86d8-c28cb66ca3ef；文档为 PPT_lecture1_page_007.png，SHA-256 为 95a3151c2c9f5f3d9e36392065ecd1e7ab6cf49ae9c591b698cff7196ab78601，source_version 为 35ad1226-ec0e-4931-a87d-257e756f525c。该源是独立 PNG，因此 page_number 为 null；没有把它误当成多页 PDF。OCR 对布局/部分字形有噪声，但正确数字在已索引的 OCR 文本中。OCR Chunk 被送入 Rerank 时，其 OCR 文本由正文和 image_info 展开后重复出现；Python 与固定上游具有对应的 OCR enrichment 行为，现有证据不足以把重复文本认定为丢失原因。
- **0959**：P32 实际模型请求中的证据包含电离层折射及微波向卫星发送信号，答案与该证据一致。

## 查询、调用与证据缺口

P32 保存了四题最终 Embedding 输入与 Rerank query，且各题两者相同。Query Rewrite 的模型响应正文没有保存。本地 Query Expansion 的每个变体及其逐变体召回没有保存，均标记 UNKNOWN；没有从最终候选数量反推扩展行为。

保存的 Rerank wire 是请求体，不是响应体。目标候选在请求中的顺序及正文已核对，但对未保留候选，原始分数、模型返回 index、原始响应和阈值比较路径均 UNKNOWN。0434 评分 JSON 的 rerank_retained 保留为 null；P32 另外记录了空 Context/固定 Fallback 的有效链路结果为0，本报告不把 null 改成供应商分数证据。

TopK 没有独立阶段快照。对 Hotpot、Omni 和 0959，只能确认最终 Context 的 ID 与 Merge 输出 ID 相同；不能声称有独立保存的 TopK 响应。0434 在空 Rerank 后由 SearchNothing 收口，没有 Merge/Context 输出。

## 固定上游与 Python 路径对照

固定上游身份按指定提交处理，不改用新分支。固定源码由 E:\WK\go-linux\source-3e8b0bfc.tar 提供，归档 SHA-256 为 2dcc5f18f9442058c5e1a4f1f4c09c0b6cf795879f9fa83977fa74573008f21e。该松散 Go 源目录本身没有 Git 元数据；相关六个文件的归档内容哈希已记录在证据 JSON 中，因此报告不声称从该目录读取到 Git HEAD。

对照路径：

| 固定 Go 源码 | Python 对应实现 | 核对结果 |
|---|---|---|
| chat_pipeline/rerank.go | retrieval/rerank.py::rerank | 阈值筛选、Top1 保底、候选 index 对应及重排结果都在 Rerank 边界；没有发现能解释本次已记录输出的 Python-only 偏差。缺少被过滤候选的响应，无法判断实际分数分支 |
| chat_pipeline/merge.go 与 merge_expand.go | retrieval/merge.py::merge、expand_neighbors | 合并、去重、父块/相邻块处理在 Rerank 之后执行；本次 Hotpot/Omni 的 Merge 输出和输入保留结果一致 |
| chat_pipeline/filter_top_k.go | retrieval/filter_top_k.py::filter_top_k | 在 Merge 之后应用 TopK；独立 TopK 记录缺失，因此不基于 ID 相同以外的假设归因 |
| chat_pipeline/into_chat_message.go | retrieval/context.py::into_chat_message | Context 从最终 merge_results 构建；三个候选保留案例中，最终实际请求与已捕获 Context 内容核对一致 |
| knowledgebase_search_results.go | Python Search 结果进入 retrieval pipeline 的适配路径 | 本轮样本的 Search/Rerank 候选顺序和 provider 输入 index 可映射；没有发现 ID 与 index 错配证据 |

Python 阶段顺序为 Search → Rerank → memory affinity → Merge → TopK → Context，见 retrieval/pipeline.py::run。当前默认 Rerank 阈值 0.3、Top1 fallback floor 0.15；代码存在对应上游的筛选与空结果行为。不能因为 0434 的最终候选数为0，就断言 provider score 低于0.3或低于0.15。

## Bug、修复与回归

- 确认的 Python 程序 Bug：无。
- 生产代码修改：无。
- 调整阈值、Prompt、模型、路由或索引：无。
- 对照回归：0959 使用 P32 的既有真实结果；Rerank 保留有效 Chunk，Merge 与 Context 保留该 Chunk，实际 Cheap 请求包含必要证据，答案正确。没有为了 P33 重复发送问题。

## 实际验证

| 命令/检查 | 结果 |
|---|---|
| P33 冻结记录一致性检查：E:\RAG quention\.venv\Scripts\python.exe -B docs/evaluation/evidence/p33-evidence-loss-20261017/check_frozen_records.py | PASS，退出码0；26项断言，0 provider call、0 DB write。结果见 frozen-record-consistency-check.json |
| Omni PostgreSQL 只读身份与 Chunk 查询 | PASS，退出码0；通过 scripts.weknora_port_mvp_real.connection() 校验隔离数据库身份，事务只读并回滚；查询结果见 omni-db-readonly.json。原始 inline invocation 未单独持久化 |
| 固定上游归档与六个目标 Go 源文件核验 | PASS，退出码0；归档 SHA 与文件哈希见 frozen-run-trace-summary.json |
| pytest | NOT RUN；无生产代码改动 |
| 新 API、模型调用、重入库/OCR/Embedding | NOT RUN；本轮云/供应商调用为0，新增 Token 为0 |

注：第一次一致性辅助断言使用了不存在的顶层 source_version 字段，退出码1；确认实际字段位于 chunk.metadata 后修正并重跑，最终 26 项检查退出0。该错误仅在只读辅助断言中，无应用或数据库写入。

## 结论、风险与可选下一步

本轮三道失败题的必要事实均在 Rerank 结果处丢失；Hotpot 与 Omni 的 Merge/Context 阶段没有继续丢失已保留结果。row-0959 正例未退化。没有足够证据认定 Python 迁移错误，也没有可安全实施的最小生产修复。

主要证据缺口是历史 Rerank 响应正文及逐候选 score/index、独立 TopK 快照、Query Rewrite 响应和 Expansion 逐变体召回。可选的下一步是在另行授权的诊断中，仅捕获去敏后的候选 index/score/阈值结论并保留请求与样本身份；风险是诊断会新增模型调用和结果信息，需要复用真实调用授权。无需为补齐历史证据而重跑或重新计算样本。本轮不自动实施该选项。

有限样本支持“Rerank 是当前三题的实际丢失边界”，不支持下调全局阈值、关闭默认 Rerank 或推出整体策略优劣。
