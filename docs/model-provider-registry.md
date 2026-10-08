# P4_PRE-R3 模型注册与调用边界

本配置只登记模型，不授予外发许可。默认五个角色如下；模型别名不自动替换为 Pro 或其他型号。

| 角色 | Provider | Model ID | 当前接线 |
|---|---|---|---|
| chat_cheap | siliconflow | XingChenAGI/Xing4.0-29B | Factory / 离线合同；未替换 Quick/Smart 回答链 |
| chat_expensive | deepseek | deepseek-flash | Factory / 离线合同；原 DeepSeek 产品入口仍使用原 AttemptGate |
| embedding | siliconflow | BAAI/bge-m3 | 摄取与查询共享实例，默认拒绝外发 |
| rerank | siliconflow | BAAI/bge-reranker-v2-m3 | CandidateRanker 适配器；未接入生产召回、RRF、Context |
| vision | deepseek | deepseek-flash | CaptionProvider 合同；显式注入 CaptionEnricher，默认关闭 |

静态示例为 `deploy/clean-slate/models.json`。`Settings.model_registry_json` / `RAG_MODEL_REGISTRY_JSON` 接收同一 JSON 结构。一个角色可以登记多个不同 model_key，defaults 指定该角色默认项；同一模型可用不同 model_key 登记不同角色。每次 Factory.build 返回独立适配器、请求计数、统计身份与职责 Guard。启动前拒绝不支持的角色、未登记模型能力、错误维度、无效 defaults 或非白名单 URL。新增实际模型能力需先核实厂商合同并登记，不通过猜测或动态发现开放能力。

Embedding 默认后端为 siliconflow；`RAG_EMBEDDING_BACKEND=ollama` 显式选择原本地 Provider。保留 Ollama 的 truncate=false、完整输入与向量响应校验。生产装配不再强制 Embedding 与本地 Chat / Query Expansion 共用 Gateway。现有 model= 测试注入继续兼容，另有独立 embedding_provider= 注入。

## 外发与费用

四类职责开关分别为 RAG_EMBEDDING_EGRESS_ENABLED、RAG_CHAT_EGRESS_ENABLED、RAG_RERANK_EGRESS_ENABLED、RAG_VISION_EGRESS_ENABLED，全部默认 false。Chat/Rerank/Vision 的 Factory 许可还要求 RAG_CLOUD_ENABLED；原 DeepSeek 组合根现在同时要求 Chat 职责开关，并保留原 AttemptGate。CloudChat 适配器没有接入原产品回答入口，不授予新的 DeepSeek 会话额度。

Embedding 的独立开关是显式职责许可，供经过批准的隔离验证使用，不会打开其他云功能。它仍要求 KB.cloud_allowed=true；摄取查真实 KB，查询检查整个服务端 Scope 的 KB。空范围、缺失 KB 或任一拒绝 KB 都不授权。Clean-slate helper 仍保持全局 cloud=false，不修改原配置；本轮所有职责开关均关闭。

调用还要求运行时对应 Key、可信职责上下文和独立 ModelUsageGuard。Key 只在调用前从环境读取，不进入注册表、日志或报告。固定端点仅允许 https://api.siliconflow.cn/v1 和 https://api.deepseek.com；无重定向、系统代理、模型 fallback、HTTP 重试或输入缩短。超时上限 120 秒，响应/请求体有 2 MB 本地保护；每实例最多 10 次请求、已知计划输入最多 20000 tokens。Guard 负责跨实例的持久预算，构造新实例不能重置其数据库账本。

BudgetUsageGuard 复用原 BudgetGate，按角色分别预留；配置必须提供正的保守预留值，月预算不足则拒绝。发送后，即使供应商返回 token，也保留 unknown 占用，因为 token 报告不是实际结算凭证。不把免费档、缺 usage、请求失败或未知供应商消耗当作 0 成本。免费价格核实不等于取得 Key、账户权限、用户外发批准或输入安全合同。Adapter receipts / UsageCapture 保存职责、Provider、model_key、模型、耗时、状态、供应商 token 与 UNKNOWN 结算，不保留正文/Key。Rerank 的 meta.tokens 尚未映射为统一 usage，缺少标准 usage 时明确 UNKNOWN，不能据此计算完整费用。

## 输入容量与索引身份

官方 BGE-M3 公布 1024 维、8192 tokens，SiliconFlow Classic Embeddings 文档也列出 8192；这不是 Child 384 字符的含义。Batch 32 是本地防护策略，供应商真实批量上限本轮未确认。适配器不向 SiliconFlow 发送 Ollama truncate=false 或 BGE 不支持的 dimensions 参数。

本机未安装 BGE tokenizer，供应商默认不截断合同未独立确认。因此默认 EmbeddingAdmission 对所有非空输入都返回 EMBEDDING_CAPACITY_GUARANTEE_UNKNOWN，连短文本也不例外；Key 和开关不足以解除此拒绝。接入方必须提供经审核的模型对应 tokenizer counter（含特殊 token、禁用 tokenizer 截断、记录 tokenizer 身份），以及供应商不截断依据。离线 counter 明确为 SIMULATED，不是实际模型容量证明。任何一条超过 8192 tokens、空文本、批量/计划额度超限均发送前拒绝，不截断 Header 或原子证据。

有效向量身份包含 provider、model_id、resolved_revision_or_unknown、dimension、distance_metric、chunking_index_identity、embedding_input_semantics_version。完整七项生成 SHA-256 fingerprint；Profile provider/model/revision 写入既有字段，完整语义身份在指纹中，未新增或改动 Migration。供应商未提供可验证 revision 时写 UNKNOWN，不再把 local/P3 identity 冒充模型版本。

这会隔离旧四项指纹的本地索引，包括维度相同的历史 Profile；不会自动迁移或重建。需要新索引时仍遵守 P3 新版本路径，禁止覆盖历史版本。UNKNOWN 不能检测同一 model_id 的供应商静默漂移；后续实测漂移基线、经核实 revision 或经批准的 input semantics 版本变更需要建立新身份。

## Vision 证据

只接收经实际解码校验的 PNG/JPEG 原始字节，使用 user 图文消息与内联 data URL，不接受调用方公网图片 URL。CaptionEnricher 继续调用 CaptionUsageGuard 的允许、预留与结算，并由职责上下文约束 Provider。来源 Asset/Version、图片 SHA、Prompt 版本、原字节处理策略、UNKNOWN 模型 revision、token 与失败状态保留在现有派生资产链。

Caption 继续标为 model_generated_caption / UNVERIFIED，不升级为 OCR 或单元格原文证明。拒绝、截断、错误模型、错误向量映射及不可靠响应均不能伪装成功。Rerank 返回原 RankedHit 对象并保留原 rank/score/sources；独立 RerankResult 保存重排分数、顺序、usage 和耗时，禁止用供应商返回正文改写原 Chunk。

当前总体状态 P4_PRE_BLOCKED。现有 embedding_profiles_identity_ux 没有包含 fingerprint，同模型/UNKNOWN revision 的不同输入语义或 chunking identity 不能建立并存 Profile；真实回滚事务反例已失败。须先由 Owner 决定新增 Migration 的最小方案，见 docs/audits/p4-pre-r3-schema-blocker.md；本轮未创建或应用 Migration。

真实云 Embedding 与真实模型索引验收 NOT RUN；Schema 修订获准并完成后，仍需要 Key、明确的本轮有限合成数据外发批准、账户/免费档核实、输入合同补证与 Guard 配置，才可运行不超过 10 请求 / 20000 计划 tokens 的独立验证。不得据此进入 P4 或执行付费 Chat/Vision。


## P4_PRE-R3-FIX 当前状态

Owner 授权的 0017_embedding_profile_identity 已应用于固定 Clean-slate；六列唯一身份含 fingerprint，Repository 原子 get-or-create 与真实独立连接并发检查通过。当前代码状态 P4_PRE_R3_CODE_PASS，整体 P4_PRE_BLOCKED；上文 Schema 阻断状态为历史记录。模型名单、容量/价格/预算/外发合同没有改变。真实 SiliconFlow、输入完整性保障和模型 pgvector 验证仍 NOT RUN；依赖升级影响未独立验证。详见 p4-preflight-embedding-and-index-report.md 的 FIX 节及本轮证据。
