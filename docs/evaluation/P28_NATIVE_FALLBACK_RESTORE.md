# P28 — 恢复原生 SearchNothing / Fallback

日期：2026-10-17。源码起点：`10002fe297d771f6beb1e615dcfea395e5cfa763`。
固定上游：`3e8b0bfc80b845b2d4b2ed683994748741450a97`。

## 实施范围

生产修改仅涉及 `backend/weknora_port/service.py::RuntimeService.prepare_turn`：

- 删除 P21 的 `kb_bounded` 强制 fixed 分支和专用中文常量。
- `SearchNothing` 现在尊重已有 `TurnConfiguration.fallback`；没有该配置时继续抛错，不制造新策略。
- 保留既有 model 提示词渲染、文档列表、历史、ChatOptions、普通生成与 SendGate 路径。
- 没有改模型、Provider、Prompt、阈值、路由、多模态入库、数据库结构、依赖或前端。

| 无命中时的有效配置 | P21 行为 | P28 行为 |
|---|---|---|
| 默认/model，非空 fallback_prompt | 被覆盖为固定中文拒答 | 使用既有模型 Fallback |
| 显式 fixed | 仍被覆盖为同一中文拒答 | 返回配置的 fallback_response |
| model，空 prompt | 被覆盖为同一中文拒答 | 保留原生固定回复处理 |
| 未提供 Fallback 配置的内部调用 | KB 有范围时临时制造 fixed | 抛出 SearchNothing，由调用者处理 |

普通产品默认仍由 `ConversationSettings` / 正常启动配置提供 `model`；显式启动或服务器配置的 `fixed` 得到尊重。现有配置不增加新的优先级或注册系统。`WEKNORA_PORT_FALLBACK_STRATEGY=fixed` 可选择原有固定路径；默认 `model`。没有改写已有持久化配置。

## 固定上游与 Python 对照

固定对象的七份文件共 3,508 行已实际读取，Git blob 与 SHA256 清单在本报告证据目录的 `upstream.json`。未使用最新分支。

| 环节 | 固定上游 | Python 对应及差异 |
|---|---|---|
| Search 无命中 | search.go 的最终 SearchResult 为空时返回 ErrSearchNothing | retrieval/search.py::search；范围/权限/取消错误单独抛出，检索整体失败为 SEARCH_FAILED |
| Rerank 全被过滤 | rerank.go 在最终结果为空时返回 ErrSearchNothing | retrieval/rerank.py::rerank，同样中断 |
| Pipeline 中断 | session_knowledge_qa.go::KnowledgeQAByEvent 捕获 ErrSearchNothing，执行 handleFallbackResponse 后返回 | RetrievalPipeline.run 异常返回，prepare_turn 执行已存在的 Fallback |
| Merge 的 Search fallback | merge.go 在真正执行到 Merge 时选 Rerank，否则 Search | retrieval/merge.py::merge 同样选择；不能挽救已经被 Rerank 异常中断的流程 |
| Top K | filter_top_k.go 排序并限制候选数 | retrieval/filter_top_k.py；不是将低 Rerank 分数重新放行的规则 |
| enable_rerank=false | YAML 有字段，但固定 Go 主链没有读取此开关；实际模型 ID 为空时才跳过重排 | Python 已有 resolve_saved_conversation 保留显式 false，runtime._configuration 将 rerank_model_id 置空；复用原生空 ID 跳过行为，Merge 使用 Search。这是已有移植接线，不能声称固定 Go 开关本身已生效 |
| 默认 Fallback | config/config.yaml 的 model + default_fallback_prompt | ConversationSettings 默认 model；runtime._fallback_prompt / _fallback_documents / prepare_turn 已有实现复用 |

**额外的来源安全适配仍保留：** `_source_merge_input` 排除模型生成 Summary，防止其冒充正文；`_project_source_evidence` 回取原文并验证 Tenant/KB/document/version/offset/type。无原件、身份或版本不匹配是明确错误，不会被 `except SearchNothing` 吞掉。图像派生证据继续标明 OCR/Caption 来源。

Rerank 原生细节（`rerank.go::OnEvent/p.rerank`）：分数按 `>= threshold` 保留；全空时仅最高响应分数达到 0.15 才保留 Top1；显式 recall-threshold override 时该 floor 为0。仅原阈值大于0.3且全空才以 `max(0.3, threshold*0.7)` 重试一次并恢复原阈值；当前0.3不会下探重试。Rerank API 调用错误走原候选降级并继续 Merge，而模型加载失败是硬错误。TopK 只有排序/截断，没有第二个分数门。Merge 还包括去重、相关历史注入、Parent 归并、相邻扩展、FAQ 和部分重叠去重；不能把这些与来源身份校验混为低分过滤。

关键上游定位：`search.go:150`（空结果），`rerank.go:237`（空重排），`session_knowledge_qa.go:795`（立即 Fallback 并返回），`merge.go:99`（执行到此处才可回退 Search），`filter_top_k.go:25`（排序截断），`session_knowledge_qa.go:951`（model/fixed 分支），`config/config.yaml:16`（默认 model）。固定配置未给 strategy 时 Go 代码另有空值默认 fixed；正式 YAML 明确选择 model，Python 产品默认与 YAML 对齐。Prompt/流错误的既有固定响应收口及预算/权限错误不降级保护继续保留。

**生成边界仍保留：** 现有默认 fallback_prompt 将文档列表视为 metadata，不支持未读正文的事实主张；一般知识回答须说明不是知识库结论。既有 Source data boundary 保留用户来源限制和提示注入防护。没有添加自动引用、自动选候选或另一套强制拒答。模型是否始终遵守这些要求，本轮没有真实云调用验证。

## 冻结样本与离线对照

本节只重放保存的真实候选与真实 Rerank 响应；不产生新的模型质量成绩。完整原始历史仍在 P25/P27 原目录，新回放与身份检查独立保存。关闭 Rerank 使用已有配置，仅作为受控对照，未更改全局默认。

| 样本 | 历史真实 Search → Rerank → Merge → Context | 定位 |
|---|---|---|
| P25 row-0434 | 30 → 0 → 未执行 → 0 | Kaggle/Cat vs Dog 候选 raw score 0.0005480324，最高 0.0021434166；未满足 0.3 或 Top1 0.15，原生 Rerank 即中断；P21 额外覆盖随后发生 |
| P27 row-0958 | 9 → 0 → 未执行 → 0 | 9 个 Search 中仅 4 个 enriched_passage 非空，恰与实际 Provider 请求对应；5 个纯图片链接被清理为空。4 个分数均低于 0.15/0.3；不是后续 Merge 再丢失 |
| P25 row-0959 | 30 → 2 → 2 → 2 | 原生保留正常证据，含 ionosphere / microwaves / satellite 事实；已保存真实 Context 1,720 字符 |
| P21 UTC | 真正空 KB → SearchNothing | 历史为 HTTP/PG/Redis＋模型传输替身；本轮同样通过正常 HTTP 分别验证 model 和 fixed，不称真实模型验收 |

0958 的准确派生输入为 OCR 0.0078387633、Summary 0.0008976934、Caption 0.0107271038、图像 Parent 0.0144267119。OCR 只有残缺的 `Reflecting surtace, eg shinyet`，Caption 概述反射/折射/衍射，均未恢复 `shiny object / mirror` 答案对。Search 命中与关闭 Rerank 后 Context 非空，都不能证明视觉事实已被正确提取或答案质量提高。

P25 原结果的公开引用/readback 文件为空，因此原始 trace 仅支持阶段事实，不能冒称当时已验证 source_version；本轮身份回放另列实际来源与限制。P27 有 PDF SHA、版本、物理页9、图像和派生 Chunk 的真实回读证据。

实际执行 `scripts/p28_native_fallback_replay.py`，退出码0，33个事实检查全部为真：

| 冻结候选离线重放 | Rerank 开启 | 已有 Python 配置关闭 Rerank |
|---|---|---|
| 0434 | 30 Search / 30 实际输入 → 0，Merge 未执行 | 28 个最终证据，Context 15,130字符；Kaggle 片段保留 |
| 0959 | 30 / 30 → 2 → 2；Context 1,720字符，与历史 hash 完全一致 | 28 个最终证据，Context 16,410字符 |
| 0958 | 9 / 4 → 0，Merge 未执行 | 8 个最终证据，Context 4,940字符；不据此判定答案正确 |

两种条件复用完全相同 Search 候选和已冻结 Rerank 输入/原始分数，只有是否设置 Rerank 模型 ID 不同。Query Rewrite、Search 在离线边界重放历史值，不重新执行 Embedding/检索；之后运行实际 Rerank、Merge、TopK、原文投影与 Context 代码。未手选正确候选、插入 Gold 或生成新分数。

P25 在只读事务核对实际 PG 身份、tenant332、候选 KB/document/content 和文档版本；两题共51个不同候选原文与历史一致，56个逐题原始文本/图片候选版本匹配。P27 使用已保存 tenant344 的174个 Chunk 快照，8个原始候选版本匹配，派生图像两项另与实际 API 回读一致。模型 Summary 缺少原文版本是已知身份，仍被实际投影排除，未伪造版本。

离线 `TrustedScope` 和 source-reader 仅是绑定这些已核实来源的重放器，不是线上授权证明；线上权限、范围、原文回读与失败边界由独立正常 HTTP/PG 测试验证。回放全文和来源快照见证据目录 `replay.json` / `source-snapshot.json`。

## 验证与运行状态

本轮真实云模型调用 / 实际云 Token / 新增真实 UNKNOWN 均为 **0**。P27 围栏保持关闭，未开启新围栏。全部模型替身的 usage 和 UNKNOWN 都属于独立测试账户，不能计入真实消耗或覆盖历史账本。

测试命令、退出码、逐项结果、资源身份和离线回放见同名 `.results.json` 与证据目录。当前已执行：

- 定向离线首轮：157 passed，退出码0。
- 原生 Pipeline 与 P28 补充：73 passed，退出码0；其中 P28 用例与首轮有重叠，不能直接相加为独立总数。
- 正常 loopback HTTP＋真实隔离 PG/Redis＋MockTransport：5 passed，退出码0。覆盖正常检索、model/fixed、空 prompt、原生未知策略处理、历史回读、重放、取消、权限/范围/预算门、公开引用关闭与标签清理。
- `git diff --check`：退出码0；LF/CRLF 提示不影响结果。

两批离线 pytest 合计221个不同用例（9个重复执行），HTTP/PG另5个用例；回放的33个事实断言单独计数，不当成模型回答成绩。一次身份预检命令因内联 Python 拼写错误退出1，未连接服务；修正后身份核对退出0，原错误记录保留在 `preflight-failure.txt`。

原 P21 “无证据必须固定拒答”断言根据 Owner 本轮明确指令改为尊重 model/fixed。保留来源、Scope、私有库、预算、取消、UNKNOWN 和重放安全反例。没有修改 P21/P25/P27 历史报告或 Gold。

隔离资源：PG `127.0.0.1:56423/weknora_python_port_20261013`，system identifier `7696088403504697382`；Redis `127.0.0.1:56424` DB15，run_id `dcff9d65a25725574ba0cfbc743d409fac211ef0`。测试创建独立合成 Tenant、KB、会话、Redis 前缀和 E 盘资产；真实账户历史账本摘要前后相同。测试启动的临时 HTTP 服务均由测试关闭；既有依赖保持运行。

真实 Edge UI / 真实云模型质量复验：**NOT RUN**。本轮通过离线与真实 HTTP 契约验证历史“刷新”读取；不将其声称为浏览器实际操作。没有新的真实调用授权及围栏，按目标文件完成离线验收后停止。

## 遗留与边界

1. 原生 Rerank 对相关片段仍可能给低分并清空候选；本轮未降低 0.3/0.15、未关闭全局 Rerank、未绕过过滤。可供后续选择的是既有 enable_rerank 配置，不代表默认调整已获批准。
2. 0958 的 OCR/Caption 内容缺失正确视觉事实，属于上游证据提取质量问题；本轮未改 OCR/VLM 或补写答案。
3. 模型 Fallback 恢复后会按正常授权产生生成调用；不能将一般知识回答当成知识库证据命中。真实协议遵循与回答质量本轮未测。
4. 保留原有 `.serena/project.yml`、`progress.md` 修改及六个历史未跟踪文件。仅显式暂存本任务文件；不 Push、部署、合并或打 Tag。

## 验收结论

**P28_COMPLETE_OFFLINE**：强制 fixed 覆盖已撤销；model/fixed 接线、正常 RAG、来源安全和必要定向验收通过；固定上游过滤机制与移植适配已区分。本轮按授权边界完成离线/HTTP 模型替身验收，真实模型质量和真实浏览器验收为 NOT RUN。该结论不宣称0434/0958答对，也不变更原 MVP/P9–P27 历史成绩。报告与全部本轮文件在同一本地提交中，完整 SHA 由交付时的 `git rev-parse HEAD` 提供。
