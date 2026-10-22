# S8.6.2-R3：精确 QU 恢复与真实 HTTP 验收

2026-10-22。**离线修复 PASS；ECS 最新镜像的首题正式 HTTP 工程链路 PASS；主答案正确 1/1，完整答案质量 PARTIAL。** 这不是生产质量验收通过。预授权状态原记录保存在 Git `755841b1`，原提案文件不改写成新授权。

## 1. 基线与最小修改

合法分支 `codex/s8-6-adaptive-retrieval-20261021`，起始 HEAD `22dcb59d78042fe8572027bfaecd18a7e300c75f`，包含身份修复 `5f81e781e1648e8496ecd974b8cca234e3d8b739`，起始工作区干净。AGENTS/override、progress、ADR 索引、S8.6 设计及上轮报告已核对。

仅生产 `retrieval/query.py` 新增 `_exact_terminal_period_recovery()`：仅在实际启用 QU v2 时，原始 JSON `rewrite_query` 必须恰为 q0 删除最后一个 ASCII `.`，且不是省略号；不做 trim、casefold 或其他标点处理。该例外要求明确 `stop`、无 tools/截断、完整无重复键的四字段字符串 JSON、合法 query_type。恢复服务器原始 q0 后继续执行既有数字、限制、歧义校验；只有通过才保留建议类型。

私有 Trace 记录 `EXACT_ASCII_TERMINAL_PERIOD_RESTORED`、原 `ChatResponse.content` UTF-8 SHA256 及 hash 范围；不把正文写入公开日志。QU v1、Prompt 字面量、输出上限和权限异常传播不变。

三个已结算英文输出直接离线回放：RED **3 failed / exit 1** → GREEN 后 **3/3 RULE_COMPARE**，子查询数 **2、3、2**；q0 逐字保留。受影响 QU/保护测试 **82 passed / exit 0**。初次测试 collection 因 Mock 的 MemoryScopeError 构造参数错误失败，原日志保留并单列，不算产品 RED。[回放与 Trace](s8_6_2_r3/replay.results.json)。

## 2. HTTP 身份与离线 ASGI

复用 `scripts/s8_6_1_api_acceptance.py`，客户端授权严格读取 `AcceptedTurn.client_request_id`，服务端 run UUID 与 Provider 子调用继续沿既有身份。新测试使用真正的 AcceptedTurn，两个 ID 明确不同。

FastAPI SessionCreate/Message POST、保存/读取模式、RuntimeService、实际 Pipeline、Context、GenerationCoordinator/P5、SSE 与回放均执行；Provider/PG/来源存储明确使用 Mock。覆盖原样 QU 与句号恢复两种响应，两条串行 Hybrid/Rerank 查询、原 Mock 片段送达最终消息、单次生成与回放不新增调用。仓库 Mock 只记录供 Embedding 使用的查询参数，**没有运行真实 Embedding Provider**。这不是实际 PG/模型/引用正确性的证明。

受影响 ASGI/预算测试 **103 passed / exit 0**，其中新 ASGI 模块 6 项已包含在103项内，不累加。没有重跑整仓、历史922项或全量评测。[原样命令与退出码](s8_6_2_r3/commands.json)。

## 3. 获批首题与实际运行版本

Owner 回复“批准”后，仅执行一个新 Session、一次 HTTP POST：

`Compare Leyden jar and Franklin bells in terms of operating principles and uses.`

| 身份 | 实际值 |
|---|---|
| QU 修复提交 | `9c2aa9c49768acbeae7aedb6996f8a40fdfcaf21` |
| 实际镜像源码 | `751c6b26d461e90e76d1c6c708977db9dd831eed` |
| ECS 临时镜像 | `sha256:9ef7691a9cb5667713d4e675e96055010165520984ef8ee1de6dd44532e5df50` |
| R3 profile SHA256 | `495447f94ab92f1e2ca770cc2cad851d4eaa40bad326e8b483d2c4d4279a8f2b` |
| 预算文件 SHA256 | `297b292e19fe67b58efc2e133a4c336d23096dd867f6d70e0caada84d34d3e8c` |
| 客户端 request ID | `s861-f644e1376f9f4ccfa0d65f19` |
| 服务端 run UUID | `98060179-90f4-42eb-863e-d2ed2d6e01b7` |
| Session | `76565813-b420-4a34-92ff-47dfe053b599` |

运行位置为 **ECS 独立临时 FastAPI**，复用正常 Session/Message、RuntimeService、Registry、Provider Adapter、SendGate、LocalExecution、PG/Redis 和 SSE。生产 API/Worker 未替换，仍为 revision `29ecf2f5d3037eed99a4de783546dee272a761bd`。候选已停止，无重试。获批上界 9 次 / 65,261 Token；旧 500,000 授权与旧四个 CLOSED 账本未复用。

## 4. 实际查询、证据与完成合同

真实 QU 输出 `SIMPLE_COMPARE`，原始 rewrite 恰好少最后一个 ASCII 句号，明确 `stop`；本轮精确恢复为逐字 q0，Trace 原因 `EXACT_ASCII_TERMINAL_PERIOD_RESTORED`。`COMPARE_SHARED/v2` 完整匹配，形成 `RULE_COMPARE`、两个检索单元。

实际 Embedding `input` 与 Rerank `query` 分别为：

1. `What is known about Leyden jar regarding operating principles and uses?`
2. `What is known about Franklin bells regarding operating principles and uses?`

六次物理发送严格串行：QU → E1 → R1 → E2 → R2 → Cheap 最终生成。每次 Rerank 11 个候选；没有内部 Expansion、桥接、Fallback、Expensive 或升级。Embedding/Rerank 由原始请求体与结算收据共同证明；Hybrid/关键词参数由实际 Pipeline 状态及已核验代码路径证明，**没有捕获 SQL wire**。

当前原始 Chunk 两个完整片段都进入最终模型 user message，随后引用回读 2/2：Leyden `e8a751ff-032c-4c8c-bb31-4eddc73b3a86`，429字符；Franklin `0f650d87-44cc-4fe5-8a91-30844d2578e0`，500字符。文档、KB、版本、偏移、原文 SHA 与上下文一致。完整可审计原始改写、原文位置、实际模型输入/答案、每次 wire SHA 和收据见[实际审查记录](s8_6_2_r3/actual_run_audit.json)。

生成包含明确 `finish_reason=stop`、最终 usage 和 `[DONE]`，非仅凭 DONE 判成功。SSE 30帧与回放30帧相同，答案/引用与PG/public history一致，回放新增调用0，generation attempt `COMPLETED`、port_turn `completed`。原始 messages.turn_status仍为既有合同的pending，正常完成由is_completed=true和公开history归一化表示；没有重新标记历史FAILED。原始HTTP capture的complete=false表示解析器在合法DONE后未耗尽迭代器，不能误报为缺结束帧。cost_status=UNKNOWN是未核算金额，**不是发送UNKNOWN**；不估算费用。

实际最终渲染 System Prompt B SHA `cabb2818fdfed59572a8dff282378c8373d2129aced63d1caec953e768b5cc68` 与冻结 renderer 字节一致；配置组合前的 profile Prompt SHA `fbea5b6a…8faf8e` 是不同阶段，不能混为一个 hash。

## 5. 实际答案质量与限制

| 指标 | 本题结果 |
|---|---|
| 主指标 final_answer_correct | PASS，1/1；代理依据原文独立审查，非 Owner 新 Gold |
| 必要原文送达 / 引用回读归属 | PASS，2/2 |
| 全文 evidence_support | PARTIAL |
| 补充事实准确性 | ISSUES_FOUND |
| 引用位置 / 呈现一致性 | FAIL / ISSUES_FOUND |
| 全面生产可用性 | NOT VERIFIED |

主结论正确比较了 Leyden jar 的电容储电作用与 Franklin bells 的电荷定性指示、摆锤运动演示。保留以下问题：`completing the circuit` 超出原文只证明端子接触内箔的范围；`they do not store energy` 是没有原文支持的绝对表述；总结中 charge→motion 的措辞也不够精确。两个合法ref集中在答案末尾，未遵守靠近论断的引用位置要求。没有为了通过而改 Prompt、评分、P5 或后处理，也没有补发本题。

## 6. Provider、Token 与保护

| 能力 | 调用数 | 输入 Token | 输出 Token | 合计 |
|---|---:|---:|---:|---:|
| Chat（QU+最终生成） | 2 | 2,455 | 336 | 2,791 |
| Embedding | 2 | 34 | 0 | 34 |
| Rerank | 2 | 2,616 | 0 | 2,616 |
| **总计** | **6** | **5,105** | **336** | **5,441** |

PG 与 Local 两侧 `(request_id,capability)` 均唯一、键集合完全相等6/6；模型、wire hash、完整 usage 与Session绑定一致。6次全部结算，新UNKNOWN0，未结held0；实际发生调用的原预留合计29,467，完整授权树上界65,261，均不当作实际消耗。新账本仅在完全对账后改为CLOSED，条目不变，闭账SHA `e530f8989c68c886b7531099f0a6f841259fa7db0122ed6c3627279f5354b3c4`。

全局 PG 从13047增加为13053，正好新增6条；旧UNKNOWN3指纹、原来源、87索引、生产服务/配置、旧四账本保持。215个基线保护文件不改，唯一生产源码变更仍是query.py。没有Push/Tag、生产部署或重建索引。

## 7. 执行证据与最新后续范围

正式候选启动、Ready、模块身份、runtime check、collector和停止的保存命令均exit0；只读取回证据exit0；PG/Local对账并闭账exit0。命令记录及文件SHA见[实际审查记录的 provenance](s8_6_2_r3/actual_run_audit.json)，完整原命令/原字节留在 `E:/RAG quention/var/reports/s8-6-2-r3`。快照归档SHA `55f8ddd6564013d4952c8542c7622a97a9b4d337cf0c99f11225b4894a81af91`。**未实现的命令不得报告通过。**

Owner 最后澄清：**不再做本地真实问答，不再补另两道小样题，直接准备 ECS40题**。此前已完成本地零Provider API启动/鉴权200、缺/错认证401、两原文回读及正常退出0；这不等价本地模型问答。本地旧704调用、8UNKNOWN和1未结束行全部不动；未结束行为tenant1旧取消轮次，tenant332未结束0。[本地零调用摘要](s8_6_2_r3/local_zero_ready_summary.json)。

原冻结40题的来源/Gold校验exit0，五数据集各8题；Omni24页仅解析核验，不计问答。准备保留原40分母，其中scifact-339和hotpotqa-5ae3992d5542994393b9e722继续NOT_RUN_LEGACY_UNKNOWN_PROTECTED，不偷偷换题。**ECS40当前请求树预算、完整语料库存和独立新授权尚在准备，真实调用0，生产增量更新NOT RUN**。[后续状态](s8_6_2_r3/followup40_preflight.json)。
