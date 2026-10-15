# 真实外发梯度冒烟报告（approved 路径）— 2026-10-15

- 授权：Owner 2026-10-15（见 `REAL_EGRESS_AUTHORIZATION_20261015.md`）；deepseek 累计 100 万 Token、siliconflow 不限。
- 结论：**A 相真实 SiliconFlow embedding PASS（真实结算 14 Token）；B 相真实 DeepSeek chat 已实际发出并记录真实用量，但因供应商未遵守所请求的输出上限而触发超界 → 按设计标记 UNKNOWN 并阻断该月，未静默放行。真实最小问答闭环尚未达成。**
- 密钥：仅从 `E:\RAG quention\.env.local` 进程内读取，**未回显、未写入任何证据/命令文件**。全部证据不含密钥。

## 1. 命令与结果

脚本：`E:\WK\run\real-egress-20261015\graded_real.py`（隔离 PG `127.0.0.1:56423/weknora_python_port_20261013`；真实 TLS；approved authority）。

| 相 | 目标 | 结果 | 证据（`evidence/REAL_EGRESS_20261015/real-ledger.json`） |
|---|---|---|---|
| A | 1 次 siliconflow `BAAI/bge-m3` embedding | **真实 200，`state=settled`**；`reserved_input=85`，`actual_total=14`（input 14 / output 0 / total 14） | ledger 行 `real/embedding-0` |
| B | 1 次 deepseek `deepseek-flash` chat（`max_completion_tokens=64`） | **真实发出并返回**（`outcome=RESPONSE_RECEIVED`，真实用量 prompt 44 / completion **177** / total 221、cache_miss 44）；**`state=unknown`**，月被 `USAGE_EXCEEDS_RESERVATION` 阻断 | ledger 行 `real/answer/1/cheap` + `port_token_budget_months` |

脚本退出码 **1**（B 相 `MODEL_USAGE_SETTLEMENT_FAILED`，其内部真实原因是 `TOKEN_USAGE_EXCEEDS_RESERVATION`）；这是**期望的 fail-closed 行为**，不是崩溃。

## 2. 失败层次定位（先定位再改）

1. 硬件/网络层：两主机可达（`api.deepseek.com` 401、`api.siliconflow.cn` 404，均无凭据）。**PASS**。
2. 授权/装配层：approved 配置解析、`ApprovedTokenAuthority`、`ServerPermissionResolver`、`bind_wire_facts`/`build_call_sources`、`P1SendGate` Token 预留均真实执行并写入账本。**PASS**。
3. 发送层：真实 HTTPS 调用发出并收到响应；真实 usage 被解析并写入 `usage_facts`（`simulated=false`）。**PASS**。
4. 结算层：**FAIL（缺陷就在此）**。请求 `max_completion_tokens=64`，供应商实际返回 completion_tokens=**177** → 超过 `reserved_output=64`。
5. 结论：**approved 计量 recipe 对“思考型模型”的输出上界不足**——供应商不保证可见输出上限覆盖 reasoning/thinking token。按设计“供应商不保证实际输出受所选 cap 约束时需可靠保守总上界，否则该真实能力应拒绝”，当前 recipe（`output = 请求上限`）不足。

## 3. 系统做得对的部分

- 真实用量被**如实记录**（不是 0、不是估算）：`usage_facts` 含原始字段与存在性。
- 超界后 `held=max(reserved, lower)`，`state=unknown`，并**同事务**写 `blocked_reason='USAGE_EXCEEDS_RESERVATION'`；异常在提交后抛出。
- **未静默放行、未清 UNKNOWN、未重置账本**；该账户本月后续 deepseek 调用将被 `TOKEN_MONTH_BLOCKED` 拒绝（保持阻断）。

## 4. 待修复（有界，需 Owner 认可 recipe 形状）

1. **输出上界 recipe**：为思考型模型提供**保守输出上界**（例如“请求 cap + 固定 reasoning 允许量”或“按模型声明的最大思考预算”），并将其作为 `meter.recipe` 的可核验成分；在此之前，超出请求 cap 的真实回答会持续触发超界与月阻断。
2. **更紧的输入上界**（可选）：`wire-bytes-upper-bound/v1` 偏松（embedding 预留 85 vs 实际 14）。可另建经核验 tokenizer 资产的更紧 recipe；**不得**因偏松就放宽校验。
3. **本月的阻断保留**：`deepseek-owner-main` 在 2026-10 已被阻断；不得删除或换账户标签规避。

## 5. 边界与未完成

- 真实最小问答闭环（上传→真实检索→真实回答→引用）**未完成**：检索/回答仍依赖应用侧真实 `source_facts` 全链与上述输出上界修复。
- 三模式对照（Cheap/Dynamic/Expensive）与分 Gold 指标 **NOT RUN**（B 相已阻断该月；且需先修输出上界）。
- 未 Push/部署/改旧库；未删除历史；未释放 UNKNOWN。
- Langfuse：端口是否已有接线未核实，单列**待确认**，未新建外发通道。
