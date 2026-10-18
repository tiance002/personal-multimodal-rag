# Phase 01：原文证据回取实现与阶段审查

## 结论

状态：`BLOCKED_SAFETY_OR_SCOPE`。已完成限定实现、离线回归和一个真实正例；未达到阶段全部验收条件。E2 的 Xing 查询理解发生新的 UNKNOWN 后，Owner 明确决定“保留本轮成果，停止外发并交审查”。未运行的案例不再发送，不进入下一阶段。

实际使用的是更正后的 `RAG_Phase_01_Execution_Pack.zip`，包 SHA256 见 [RESULTS.json](RESULTS.json)。源码起点 `0fc3a0c9ccaa7679cfd4b032e134362f751ae95b`；实现提交 `87c12e53fb5ae91439f2ee199f58abfd87c1fb9f`；真实执行源码 `6f0b97bb69702fcc4f2a993a2b0d053245d345de`。本报告不把旧实验当作新增真实验证。

## 实现与边界

- `retrieval/source_evidence.py`：通过现有源加载器回取同版本、当前有效的原文。完整短文档最多 2,048 字符 / 6 块；文本窗口最多回取两块具有双向连续链接的前文。最多三个文档版本域、新增 4,096 原文字符、最终 Context 16,000 字符。字符上限不是 Token 上限。
- `storage/knowledge.py`：校验租户、KB、版本、启用状态、完整短文档成员和 offset。Summary 的文档级 parent 不冒充章节关系；缺失或过期来源拒绝扩展。
- Pipeline 在原生 Merge/Top-K 后、Context 前运行可选扩展；新回取原文不伪造 Rerank 得分，不驱逐既有结果。生成输入区分原文、派生 OCR、Caption 和 Summary。
- `merge.py::expand_neighbors` 修正已确认的截断问题：不再用邻居拼接后截取 850 字符的方式截掉已选择的正文，邻居按完整块纳入。
- `execution.py` 被动保存真实候选/分数/阶段快照与传输时间，移除逐 delta 的同步落盘。持久化发送门禁与账本写入不变。

新证据扩展默认 `off`，可通过现有服务/会话设置启用 `bounded_source_v1`。保持原生模型、阈值 0.3、DYNAMIC 和 MinerU；无 Schema、重新入库或索引修改。**关闭新模式只撤回证据扩展；独立的邻居正文保全修复在关闭时仍有效，完整恢复旧行为需要回退实现提交。**

参考了 LlamaIndex RecursiveRetriever、Haystack sentence window 和 RAGFlow 的官方实现/文档，来源及固定文件摘要见 [DESIGN.md](DESIGN.md)。未引入这些框架或复制新的检索系统。

## 离线结果

复用保存的完整请求、原始回执及当前只读来源，经产品真实筛选/MMR/Merge/Top-K/Context 函数回放；不调用 Provider。结果中的“完整原文”要求实际正文，不能用同源 Summary 或 ancestry ID 代替。

| 案例 | 完整必要原文：原策略 → 新模式 | Context 字符：原 → 新 | 证据性质 |
| --- | --- | --- | --- |
| E1 矩阵组合题 | 有 → 有 | 794 → 794 | 新真实保存回执；原文已通过阈值 |
| E2 系数矩阵 | 无 → 有 | 333 → 794 | 明确构造的 Summary-only 边界，不伪造历史分数 |
| E2 增广末列 | 无 → 有 | 333 → 794 | 同上 |
| E3 LongBench 年份＋数量 | 无 → 有 | 767 → 1,529 | 保存的真实候选/回执 |
| E4 MML-0959 | 有 → 有 | 1,720 → 2,147 | 保存的成功对照 |
| E6 Hotpot 地点第二跳 | 无 → 无 | 2,632 → 3,925 | 新窗口不能补出另一文档的必要事实 |
| E6 Hotpot 导演 | 有 → 有 | 2,226 → 2,226 | 原证据保留 |
| E6 MML-0434 | 无 → 无 | SearchNothing | 章节来源关联仍未证明 |

五个非空历史基线的最终 ID 与保存结果一致。完整比较、输入/回执 SHA 和来源映射见结果 JSON。窗口的连续来源身份不等于相关性或章节语义证明，字符增长也不等于质量提升。

修正一个历史归因：LongBench 的真实最终消息中，2005 只在问题中，原文年份块在 Search rank14、Rerank 约 0.003274 后丢失；原文标题是 opaque 标识。不能沿用旧报告“年份已送达，只是模型漏答”的归因。旧报告/结果未修改。

## 新增真实执行

### E1：完成的正常 FastAPI 正例

使用日常 tenant1、原 M3.5 KB、新独立会话，`document_scope=[]`，原组合矩阵问题。API 使用正式装配、真实 PG/Redis/Provider，无理想证据注入。

- Search2 → Rerank2 → Merge2 → source_unit2 → Context2。Summary 分数约 0.977440，原文约 0.359142，阈值仍 0.3。
- **原文原本已通过 Rerank，新阶段添加 0 块。** 本次证明正常输入送达及 Qwen 正确生成，不能冒充低分原文补回机制的真实因果验证。
- 完整 MinerU 原文及准确版本进入实际 Qwen 请求；真实回执 `Qwen/Qwen3.5-4B`、`finish_reason=stop`。系数矩阵为 `[[1,-2,1],[0,2,-8],[-4,5,9]]`，末列 `[0,8,-9]`，逐位置/符号与来源一致。无 Expensive 升级或 fallback。
- 7 次真实 Provider 调用，4,546 Token（输入 4,225、输出 321）；其中 Xing 查询理解 2,572、四次 Embedding 合计 108、Rerank352、Qwen1,514。实际 `enable_thinking=false`，Qwen 回报 reasoning Token0。
- 整次 HTTP 100.388 秒；Xing 传输开始至完整响应 85.921 秒；Qwen 响应头约 0.663 秒、完整读取 10.199 秒。传输开始包括准入，不能拆成 DNS/TCP/TLS/模型计算。DYNAMIC 缓冲导致页面首段答案接近整体结束，不能把它视作模型首 Token 时间。
- SSE、权威 turn completed 与保存答案一致；刷新 GET 的 data 完全相同，未重复生成。旧 `messages.turn_status` 仍为 pending，与 `is_completed=true` 存在历史投影不一致，未改库。

### E2：查询理解 UNKNOWN，未进入 Qwen

问题：“增广矩阵最后一列的三个数分别是什么？”

Xing 查询理解使用原 300 秒 timeout，未获得可信响应头、正文或 usage，约 300.005 秒后记为 UNKNOWN；整次 HTTP301.463秒，以 `LOCAL_EXECUTION_UNKNOWN_STOP` 收口。不是人工中止，也不是可证实的模型计算超时。保守占用 12,314 Token 保留，usage 为 null。

随后 Embedding 虽生成了待发送记录，但实际 Gate STOP 在发送前拒绝，**没有第二次 Provider 发送**。Qwen/Expensive 均未发送。未自动重试，未释放 UNKNOWN。

E3、E4、E5、E6 及系数矩阵单独表达的新增真实验证均 `NOT RUN`。离线结果不能替代它们的必需真实验收。

### 总用量与授权收口

新增共 8 次实际 Provider 尝试：7 个可信响应、4,546 已报告 Token，另 1 个 UNKNOWN、12,314 Token 保守占用；已知新增保守合计 16,860。未知实际用量不填 0。两次完整问题提交，直接诊断0。父授权 STOP，E1 子授权 CLOSED，未打开的后续执行空间已撤回；E2 未决占用保持不动。

历史 M4 的 9,468 Token UNKNOWN 占用和旧 Qwen 未报告用量的 UNKNOWN 均原样保留，不混入新增消耗。仅停止本任务 API，PG/Redis 保留；没有在途请求。日常配置在阶段执行结束检查时仍关闭云调用。

## 实际命令与证据

工作目录：`E:\RAG-Worktrees\weknora-python-port-20261013`。以下命令均实际执行：

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest --noconftest -q -p no:cacheprovider --tb=short tests/weknora_port/test_source_evidence.py tests/weknora_port/test_knowledge_storage.py tests/weknora_port/test_conversation_configuration.py tests/weknora_port/test_pipeline.py tests/weknora_port/test_generation_wire.py tests/weknora_port/test_filter_top_k.py tests/weknora_port/test_development_entry.py tests/weknora_port/test_execution_fence.py
# exit0，211 passed
& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest --noconftest -q -p no:cacheprovider --tb=short tests/weknora_port/test_execution_fence.py
# exit0，61 passed；计时记录修改后重测的子集，不能加成272项
& 'E:\RAG quention\.venv\Scripts\python.exe' -B scripts/rag_phase01_replay.py --plan docs/phases/phase01/cases.json --config E:\WK\run\p81-retirement-20261018\local.json --output E:\WK\run\phase01-evidence-20261018\replay-window-bounded.json
# exit0，只读数据库＋离线回执回放
& 'E:\RAG quention\.venv\Scripts\python.exe' -B E:\WK\run\phase01-evidence-20261018\run_case.py E1
& 'E:\RAG quention\.venv\Scripts\python.exe' -B E:\WK\run\phase01-evidence-20261018\run_case.py E2
# 均exit0表示记录器保存终态；E2不是验收PASS，不再运行
& 'E:\RAG quention\.venv\Scripts\python.exe' -B E:\WK\run\phase01-evidence-20261018\check_final_readonly.py
# exit0，阶段关闭时10个原有文件、71个来源块、2条旧UNKNOWN、角色版本和日常principal/config未变
```

原始首次失败日志、真实消息、原始响应、逐阶段快照、账本及哈希索引保存在 `E:\WK\run\phase01-evidence-20261018`，不将敏感配置放入仓库。最终 `git diff --check` 结果见 RESULTS 的 closeout 字段。

## 审查保留项

- 阶段必需真实覆盖未完成，不能标阶段 PASS；新回取机制目前只有离线因果证据。
- Hotpot 另一文档事实和0434章节关联保持独立质量/来源问题，不增加规则补全。
- E1 输出含 d1/c2 内部来源句柄的自然语言残留，另有 legacy message 状态投影不一致，均未在本阶段扩大修改。
- 否定/缺答案真实反例未执行，不能推广新模式或宣称通用性能收益。
- Owner 已要求停止外发、提交审查。本交付不包含新的发送授权，不 Push、部署或进入 S2。后续 Owner 单独要求的查询理解模型切换作为新的配置改动记录，不倒写本阶段实际模型身份。
