# S8.6.2-R3：精确 QU 恢复与正式 HTTP 验收准备

2026-10-22。状态：**READY_FOR_FRESH_OWNER_AUTHORIZATION**。离线修复与隔离零调用启动通过；本轮真实 HTTP 问答 **NOT RUN**，尚未声称最新 RAG 问答通过。

## 1. 基线与最小修改

合法分支 `codex/s8-6-adaptive-retrieval-20261021`，起始 HEAD `22dcb59d78042fe8572027bfaecd18a7e300c75f`，包含身份修复 `5f81e781e1648e8496ecd974b8cca234e3d8b739`，起始工作区干净。AGENTS/override、progress、ADR 索引、S8.6 设计及上轮报告已核对。

仅生产 `retrieval/query.py` 新增 `_exact_terminal_period_recovery()`：仅在实际启用 QU v2 时，原始 JSON `rewrite_query` 必须恰为 q0 删除最后一个 ASCII `.`，且不是省略号；不做 trim、casefold 或其他标点处理。该例外要求明确 `stop`、无 tools/截断、完整无重复键的四字段字符串 JSON、合法 query_type。恢复服务器原始 q0 后继续执行既有数字、限制、歧义校验；只有通过才保留建议类型。

私有 Trace 记录 `EXACT_ASCII_TERMINAL_PERIOD_RESTORED`、原 `ChatResponse.content` UTF-8 SHA256 及 hash 范围；不把正文写入公开日志。QU v1、Prompt 字面量、输出上限和权限异常传播不变。

三个已结算英文输出直接离线回放：RED **3 failed / exit 1** → GREEN 后 **3/3 RULE_COMPARE**，子查询数 **2、3、2**；q0 逐字保留。受影响 QU/保护测试 **82 passed / exit 0**。初次测试 collection 因 Mock 的 MemoryScopeError 构造参数错误失败，原日志保留并单列，不算产品 RED。[回放与 Trace](s8_6_2_r3/replay.results.json)。

## 2. HTTP 身份与离线 ASGI

复用 `scripts/s8_6_1_api_acceptance.py`，客户端授权严格读取 `AcceptedTurn.client_request_id`，服务端 run UUID 与 Provider 子调用继续沿既有身份。新测试使用真正的 AcceptedTurn，两个 ID 明确不同。

FastAPI SessionCreate/Message POST、保存/读取模式、RuntimeService、实际 Pipeline、Context、GenerationCoordinator/P5、SSE 与回放均执行；Provider/PG/来源存储明确使用 Mock。覆盖原样 QU 与句号恢复两种响应，两条串行 Hybrid/Rerank 查询、原 Mock 片段送达最终消息、单次生成与回放不新增调用。仓库 Mock 只记录供 Embedding 使用的查询参数，**没有运行真实 Embedding Provider**。这不是实际 PG/模型/引用正确性的证明。

受影响 ASGI/预算测试 **103 passed / exit 0**，其中新 ASGI 模块 6 项已包含在103项内，不累加。没有重跑整仓、历史922项或全量评测。[原样命令与退出码](s8_6_2_r3/commands.json)。

## 3. 新版本与零 Provider 实际启动

| 项目 | 已冻结身份 |
|---|---|
| QU 修复提交 | `9c2aa9c49768acbeae7aedb6996f8a40fdfcaf21` |
| 最新源码提交 | `751c6b26d461e90e76d1c6c708977db9dd831eed` |
| 临时 Image ID | `sha256:9ef7691a9cb5667713d4e675e96055010165520984ef8ee1de6dd44532e5df50` |
| R3 profile | `s8.6.2-r3-formal-api-prompt-b/v3` |
| profile SHA256 | `495447f94ab92f1e2ca770cc2cad851d4eaa40bad326e8b483d2c4d4279a8f2b` |
| 预算文件 SHA256 | `297b292e19fe67b58efc2e133a4c336d23096dd867f6d70e0caada84d34d3e8c` |
| QU 实际空历史 System SHA256 | `a3d246f8766829274ca758359f9c11232bc18f1f7eefaf0f41f7cc3ec49a6213` |
| 最终生成 System Prompt B SHA256 | `fbea5b6a2fd2147bd67e853ee38c25913d04f3e690f6dc914edccfe8968faf8e` |

从干净 Git 源码归档521文件，以已核验依赖底座 `80e2f2d…931a36` 离线构建；14模块导入、query/recovery、profile、QU/B Prompt、资产/依赖 hash 实际核验。没有替换正式 API/Worker。

新临时 API **cloud=false、Graph off、DYNAMIC**；实际 API 进程移除所有 grant/execution/audit/evidence 环境键，再进入原入口。认证 Ready200、无认证401、正常 runtime check exit0、PG/Redis 与实际 query 模块身份通过。候选已停止，exit143、无OOM/重启。没有 SessionCreate、问答或 Provider。[零调用准备摘要](s8_6_2_r3/zero_ready_summary.json)。

## 4. 预算与保护

现有实际请求 renderer、同一256字符/1024 UTF-8字节有效查询边界及当前库存做 dry-run：首题 **最多9次 Provider 尝试、65,261 Token 保守预留**。包含最多3检索单元、QU、Embedding/Rerank、可能桥接、正常/Fallback最终生成和既有 P5 升级；不把单元数当调用数，不降低生产输出上限。QU256、最终每角色2048不变。该预留不是实际消耗或费用估算。[预算与 Prompt 指纹](s8_6_2_r3/preflight_summary.json)。

本轮实际 **Provider0 / 结算0 / 新UNKNOWN0 / 输入0 / 输出0 / 合计0 Token / 未结held0**。旧PG调用数13047和UNKNOWN3指纹不变；87索引、4文档/11授权Chunk、正式容器及配置保持。基线保护的215个已跟踪文件未改，唯一生产变化为query.py；历史Gold/原始收据不改。旧四账本仍CLOSED且原字节SHA一致，不重开。[保护证据](s8_6_2_r3/protection.json)。

## 5. 待 Owner 新授权：仅首题

本申请只覆盖一个新 Session、一次 HTTP 提交：

`Compare Leyden jar and Franklin bells in terms of operating principles and uses.`

固定 `query_structure_v1`、`bounded_source_v1`、Prompt B、引用开启、原有P5 DYNAMIC，沿现有一KB/四文档范围；必要问题、验证后的检索查询及原文可发往 SiliconFlow（Qwen/Qwen3.5-4B、BAAI/bge-m3、BAAI/bge-reranker-v2-m3）与现有P5 DeepSeek `deepseek-flash`。严格 **9次 / 65,261 Token** 上界；不自动重试。发生失败、UNKNOWN、权限/发送不明、不完整输出或合同越界立即停止并保留账本。

新客户端request ID：`s861-f644e1376f9f4ccfa0d65f19`。服务端run UUID由正式接口独立分配。新账本路径在[未激活授权提案](s8_6_2_r3/authorization_request.json)冻结，**尚未创建、更没有OPEN发送权限**。旧500000Token授权不复用。中文并列/Frost不在本申请内。

获新授权后才执行并核验：QU类型与RULE_COMPARE、两条实际Embedding/Hybrid/Rerank、当前原文到最终输入、正常生成结束、SSE/PG/history一致、最终答案正确、引用回读、全部Provider收据与Token结算。任一首题验收失败停止；当前这些真实指标一律NOT RUN，不由离线通过替代。

未改P5/规则族/模型绑定/Schema/索引，未Push或部署生产。新远端诊断目录 `/opt/rag-s8/diagnostics/s862r3-20261022t015644z-e3f8d7c3`；私有完整命令/日志在 `E:/RAG quention/var/reports/s8-6-2-r3`。**未实现的命令不得报告通过。**
