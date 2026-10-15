# 真实外发授权记录（Owner，2026-10-15）

本文件只记录授权范围与来源，**不含任何密钥值**。

## 授权（Owner 原文口径）

| 项 | 内容 |
|---|---|
| 模型密钥位置 | `E:\RAG quention\.env.local`（变量名：`SILICONFLOW_API_KEY`、`DEEPSEEK_API_KEY`）。**只读变量名，值不回显、不入证据、不入 commit。** |
| Langfuse 密钥位置 | `E:\RAG quention\.env`（`LANGFUSE_BASE_URL`、`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`、`LANGFUSE_AUTHORIZATION`、`RAG_LANGFUSE_*`） |
| DeepSeek | **不限次数**，但**累计消耗 100 万 Token**（含 vision，因 vision = `deepseek`/`deepseek-flash`，与 expensive 同池） |
| SiliconFlow（bge-m3 / bge-reranker-v2-m3 / Xing4.0-29B） | 不限 |
| 其它模型 | 随便用 |
| 效力 | “之后的真实测试授权按以上来” |

## 正式五角色（与 `deploy/service-policy.example.json`、`docs/model-provider-registry.md` 一致）

embedding=siliconflow `BAAI/bge-m3`；rerank=siliconflow `BAAI/bge-reranker-v2-m3`；cheap=siliconflow `XingChenAGI/Xing4.0-29B`；expensive=deepseek `deepseek-flash`；vision=deepseek `deepseek-flash`。

## 执行纪律（不变）

1. 密钥只经**加密模型配置存储**（`enc:v1:`）注入；approved authority 配置文件内**不含密钥**；任何 evidence/`command.json`/日志不得出现密钥值。
2. DeepSeek 预算门按 Token：`limit_tokens=1,000,000`，approved authority 必须提供**经账本核实的 carry-in**（`LEDGER:` 来源），不得填 0、不得从金额反推。
3. `wire-bytes-upper-bound/v1` 是**保守上界**（可能明显大于实际），预留偏大；若因保守预留在月中触发 `MONTHLY_TOKEN_LIMIT_EXCEEDED`，如实上报，**不得静默放宽**；必要时另建更紧、经核验 tokenizer 资产的 recipe。
4. 真实调用**梯度进行**：先 1 次 siliconflow embedding，再 1 次最小 deepseek chat，核对 `port_token_calls` 真实 usage facts 结算与预算扣减后再扩大。
5. 三模式对照前先算 DeepSeek 预算：Expensive Only≈40 次答案调用、Dynamic 为其子集、Cheap Only=0；账本监控贯穿，逼近 100 万即停。
6. 仍不：Push/部署、改旧业务库、reset/clean、释放 UNKNOWN、删除历史。
7. Langfuse 仅在端口已有接线时使用；否则单列为**待确认**，不新建外发通道。

## 前置阻断（本授权不自动解除）

标准 API 真实问答在 `bind_wire_facts` 处仍拒绝，直到：
- **服务端按能力组装真实 `CallContext.source_facts`**（question/history/evidence/memory/document/image/system）；
- **从 `port_token_calls` 核对 carry-in/UNKNOWN 上界**的函数接入并写入 approved 配置。
