# S8.6.2-R 最小误拆修复与真实验收衔接

日期：2026-10-22。结论：**两类最小规则修复及离线预算通过；6 条真实 QU 完成；正式 HTTP 首题在发送前失败，后两题未运行。最新验收脚本身份修复仅离线验证，不宣称正式 API 验收通过或生产可用。**

## 1. 基线、权限与实际版本

合法工作树：`C:/Users/22088/.codex/worktrees/s8-6-adaptive-retrieval/RAG quention`，分支 `codex/s8-6-adaptive-retrieval-20261021`。起始 HEAD `71188d04e3f0ecd2ecbe761221f5bb127ac0bd04`，工作区干净；核对 AGENTS、progress、ADR、当前设计及 S8.6.1 收尾记录。没有回退分支、覆盖其他工作树或继续 C。

审查源码提交 `5c3f7d2db9fa832cc4c2ede070bb405a6ac96d4e` 的 Registry/strategy Blob 分别为 `274d000c280ed1258a447297b47812d86a238fe1` / `e800cfb7f2812c7f9ae940f69092f64334cde9b7`，实际相符。审查 ZIP SHA256 `2369b0f8bcb6460a585deb1b1207e6e9c42a076952434a83354c72964c250132`。附件按复现材料读取；本轮行为以 Owner 最新指令为准。

Owner 后续明确授权本 Goal 新发送总额 **500,000 Token，Provider 次数不限**。执行仍受固定 6 条 QU、3 条单次 HTTP 提交、每个既有账本最多 12 次/100,000 Token、当前请求树及停止条件限制。没有复用旧外发授权、旧 request_id 或 UNKNOWN。

| 用途 | Commit / 指纹 |
|---|---|
| 两类 Registry 修复 | `eaccaa0b6920635fa98b4f633e4c81cfd171c6ea` |
| 有界验收 profile、预算与 QU-only 入口 | `c3db8915ac379608c5e8409b615c1aa7946d8360` |
| 最新验收脚本身份修复 | `5f81e781e1648e8496ecd974b8cca234e3d8b739` |
| 实际真实运行源码 | `c3db8915ac379608c5e8409b615c1aa7946d8360` |
| 实际临时镜像 | `sha256:c36eeaef8789e2de941d8914fefa4017e389c1ea59dfcefa1134a1e12702f156` |
| 实际 profile | `s8.6.2-r-formal-api-prompt-b/v2`，SHA256 `491307defefcf7b3010058e27b1f98a4462706bbbeb6947a8d93513bdb33d8cc` |

最新源码没有重新构建镜像或重跑真实 HTTP。旧镜像/指纹不能证明最新身份修复。所有提交仅本地，不 Push、不 Tag。

## 2. 误拆 RED → GREEN

仅生产 `retrieval/rule_registry.py::_slot_rejection()` 改动：中文对象列表尾部明确 `之间` 返回 `RELATIONAL_OBJECT_LIST`；维度仅为 `区别` 或 `异同` 返回 `SHARED_DIMENSION_UNSPECIFIED`。两个既有规则族升级到 v2，QU 简短说明仍从同一 Registry 生成。

| 原问题（主动给定类型） | 修改前实际输出 | 修改后 plan_query 实际输出 |
|---|---|---|
| 分别介绍A和B之间的通信方式 | A / B之间；错误独立属性查询 | PLAIN，1 条原问题，RELATIONAL_OBJECT_LIST |
| 比较A和B之间的联系 | A / B之间；错误独立属性查询 | PLAIN，1 条原问题，RELATIONAL_OBJECT_LIST |
| 比较PostgreSQL和Redis的区别 | PostgreSQL的区别是什么？等 | PLAIN，1 条原问题，SHARED_DIMENSION_UNSPECIFIED |
| 比较A和B的异同 | A的异同是什么？等 | PLAIN，1 条原问题，SHARED_DIMENSION_UNSPECIFIED |

没有删掉“之间”再拆，也没有全局禁用“间”或“区别”。`比较A和B在安全性方面的区别` 仍得到 A/B、安全性；`空间引擎`/`中间件` 合法对象仍支持。`日志机制`、`误差`、`无锁机制` 的保守子串拒绝仍存在，正常回退普通检索，列为覆盖限制。

精确附件测试先 **4 failed, 2 passed，exit 1**，修复后受影响 **273 passed，exit 0**；覆盖实际 Pipeline 一次 QU/一次普通检索/Rerank及原问题保留。最初在 E 盘外部测试根导入错误 backend 的 collection failure 已保留，随后将附件测试原字节放入合法 C 工作树运行，未冒充产品错误。

历史 66 题语义 Gold 未改：24/24 正例完整对象/维度/span，0/31 反例误拆，11/11 类型门控。此为相同开发集回归，由代理按问题和合同复核；不是 Owner 逐题批准、未见样本或生产准确率。[修改前后完整计划](s8_6_2_r/counterexamples.json)、[开发集分母](s8_6_2_r/development_coverage_summary.json)。

## 3. 当前请求体、输入边界与预算

复用 `scripts/s8_6_1_api_acceptance.py` 的正式 HTTP、ApplicationRuntime/RuntimeService、Provider Adapter、Registry、SendGate、LocalExecution 与 SSE。沿用 Prompt B、citations、`bounded_source_v1`、`query_structure_v1`，内部 Expansion 关闭。没有新的生产 QU 模型、生成 Prompt、P5、Schema、索引或全局阈值修改。

验收专用有效查询边界是 **256 Unicode 字符、1024 UTF-8 字节**。9 个固定输入最大 80 字符/80 字节；在 understand/completed 的实际有效查询观察点检查，早于规划/Embedding/Search/Rerank。超界保存原始 QU 并报 `PROFILE_INPUT_BOUND_EXCEEDED`，不截断、不替换 q0、不补发。检查位于实例级 progress 包装中，避免普通进度回调吞掉异常。

同一常量与真实 renderer 推导子查询、Q1、JSON 转义、嵌套抽取 JSON 和请求体上界。子查询最多 288 字符/1056 字节，Q1 最多 192 字符/768 字节；覆盖 Unicode、反斜线/引号/控制符、边界与超界。QU 的四字段实际输出上限仍为 256；生成上限仍为 2048。当前语料 11 个可检索 Chunk、1 个物理 Embedding 分组、每个非空池单 Rerank 批次经当前库存核验。阈值 .3 不触发自动降阈值重排；Memory 仍被已有 origin-KB 护栏排除。

| 批次 | 完整请求树尝试上界 | 预留 Token 上界 | 实际尝试 / 实际 Token |
|---|---:|---:|---:|
| 6 条 QU-only | 6 | 41,712 | 6 / 7,268 |
| 英文比较 HTTP | 9 | 65,261 | 0 / 0（HTTP 已提交失败） |
| 中文并列 HTTP | 9 | 65,210 | 0 / 0，NOT RUN |
| Frost HTTP | 9 | 65,240 | 0 / 0，NOT RUN |
| 合计 | 33 | 237,423 | 6 / 7,268 |

预算包括 QU、初始及子查询 Embedding/Rerank、第一跳 Cheap 抽取、旧池 BGE、新 Hybrid 召回、正常/无来源 Fallback 最终生成和既有 P5 可能升级；按最多 3 个单元，不误将检索单元当 Provider 调用。237,423 是完整树保守预留，不是消耗。没有降低生产模型输出或放松账本上限。

发送前 bounded-profile 受影响测试 **103 passed**。身份修复后用最新源码重新实际 dry-run：**33 次 / 237,423 Token，exit 0**；请求体 hash 与先前相同，implementation hash 已更新为 `ca99eaea03f792cc7f3b2c26a3ec5416d42c533f6bba42f94f669150eb4db07a`。旧 grant 已停止且账本已关闭，不能拿请求体 hash 相同当作继续发送依据。[预算摘要](s8_6_2_r/budget_summary.json)。

## 4. 真实 QU：分类与实际策略分别计量

6 个新 Session，正常非思考 `understand_query()`，没有注入 query_type，没有检索、桥接或最终生成。实际模型 `siliconflow / Qwen/Qwen3.5-4B`，6 条响应均 `finish_reason=stop`。

| 固定样例 | 模型原始类型 | 有效类型 / 实际程序计划 | 真实 Token |
|---|---|---|---:|
| 两对象英文比较 | SIMPLE_COMPARE | 删除末尾句号，保护回退 UNKNOWN / PLAIN | 1,215 |
| 三对象英文比较 | SIMPLE_COMPARE | 同上 | 1,213 |
| 中文并列原理与用途 | SIMPLE_PARALLEL | RULE_PARALLEL，2 条计划查询 | 1,213 |
| Qwen3.5-4B 数字型号比较 | SIMPLE_COMPARE | 删除末尾句号，保护回退 UNKNOWN / PLAIN | 1,229 |
| A和B之间的通信方式 | UNKNOWN | PLAIN，原问题 | 1,200 |
| PostgreSQL和Redis的区别 | SIMPLE_COMPARE | PLAIN，SHARED_DIMENSION_UNSPECIFIED | 1,198 |

代理按既有合同复核：4 个受支持正例原始类型对齐 4/4；两个负例规则资格分类对齐 1/2，合计资格对齐 5/6。此分母只是固定开发样例，不是六分类生产准确率。保护后正例规则计划触发 **1/4**；两个负例有效计划安全回退 **2/2**。

英文三题的 `REWRITE_NOT_LITERAL_REFERENCE_SUBSTITUTION` 是实际覆盖限制，没有放松 q0/歧义保护。本轮真实维度负例确实触发新 guard；关系负例被 QU 分为 UNKNOWN，所以真实调用未执行新 `RELATIONAL_OBJECT_LIST` 检查，后者只通过离线验证。中文两条查询是 `Leyden jar的工作原理与用途是什么？` 与 `Franklin bells的工作原理与用途是什么？`；仅证明计划形成，不能说检索分支已真实执行。[每条原始 QU、q0、计划、收据身份](s8_6_2_r/real_qu_samples.json)。

## 5. 正式 HTTP 失败及最小验收脚本修复

新镜像通过实际构建、14 个导入模块 hash 核验；HTTP 临时 API 启动、认证 readyz、运行配置和模块身份检查通过。复用现有 PG/Redis、模型 Registry 和正常端点。正式 API/Worker 保持旧 image `80e2f2d…931a36` / revision `29ecf2f5d3037eed99a4de783546dee272a761bd`。

英文比较题只有一次正常 HTTP 提交。实际客户端 ID `s861-19eca8492fac40be92eab02d`；服务端 run `4ec70dbd-beb9-4998-8a13-7b3f28056081`。SSE 为 agent_query → error `ORDINARY_CHAIN_FAILED` → complete `status=failed`。PG port_turns.status=error；助手 turn_status=error、is_completed=false、正文空、usage=NULL；Provider 账本 0 条。没有成功答案、没有 final-answer/citation 可评分。

确定性源码复核发现测试 profile 错用了 `turn.request.request_id` 匹配客户端授权。真实 `storage/turns.py::_accepted()` 将该字段设为服务端 run UUID，原客户端 ID 单独存入 `AcceptedTurn.client_request_id`。因此配置准入检查必然失败；通用服务以安全错误码输出，原始 SSE 未包含 Python traceback。此根因是依据实际 PG 两种 ID、当前源码及 RED 复现确定，不能声称从原始日志直接读到了具体内部异常。

修复仅位于测试脚本：新增 `_accepted_client_request_id()` 严格读取可信原客户端字段，配置授权和 QU 预检 hash 查找共用它；不回退到 run ID、不绕过正常 Adapter/账本、不改生产身份绑定。真实 AcceptedTurn/OrdinaryTurnRequest 的客户端/服务端 ID 不同的新测试先 **6 failed，exit 1**，后 **96 passed，exit 0**；另 **13 API 配置测试 passed，exit 0**。测试 fixture 只纠正身份语义，旧数值 Gold 不改。

出现明确失败后保留停止标记和原失败记录，未重试、未运行中文/Frost 两题；临时 API 已停止。4 个本轮账本在确认全部已结算/无在途后关闭，仅改变这些新账本 OPEN→CLOSED，所有 entries 原样保留。最新修复没有新镜像与真实 HTTP 结果，状态 **NOT_RUN_AFTER_OFFLINE_IDENTITY_FIX**。[HTTP/PG/SSE 原失败摘要](s8_6_2_r/http_failure.json)。

## 6. 收据、Token 与保护证据

PG 与 LocalExecution 两侧分别验证 `(request_id, capability)` 唯一且键集合完全相等。6/6 实际请求体 canonical SHA、预检 hash、Local/PG wire_sha256 一致；Provider/model/model_id、完整归一化 usage、Session/范围对应；PG finished_at 完整，Local 全 SETTLED。原始 response.body 的模型与结束标记独立核对。

- 实际 Provider：**6 尝试、6 结算、0 新 UNKNOWN**；输入 **7,074**、输出 **194**、合计 **7,268 Token**。
- 原始 reservation 之和 **41,712**；未结 held **0**。SETTLED 行 held=已结实际 Token，不当作未结占用。
- PG 总调用 13,041→13,047，增量恰为 6；无额外或未完成 Provider。
- 历史 PG UNKNOWN 3 条及指纹保持不变；11 个授权 Chunk、4 个文档、87 个索引定义、PG/Redis身份、正式容器 ID/镜像/restart/config hash 保持不变。115 个基线保护文件原样；唯一授权变化的第116项为验收脚本。旧 Gold、冻结结果、旧 UNKNOWN 不改。
- 原始诊断 reconciliation 中 `provider_sends=0` 表示该只读对账操作自身没有发送；实验实际 6 次由 PG/Local/原始响应共同证明。保留原字段，机器摘要另用 derived_actual_provider_attempts=6，避免混淆。

原始私有证据在 `E:/RAG quention/var/reports/s8-6-2-r`，新远端目录 `/opt/rag-s8/diagnostics/s862r-20261022t010207z-cc548180`；全部保留。仅拉取新 records、4 个新账本目录及对账 JSON，不包含凭据、正式 runtime.env、存储或 rollback 文件。快照 109 个文件，archive SHA256 `5b94a6c0b1f95472c7bbc3e90bb6918c9b066716ee629a4d2eed11715e888a2a`。[收据摘要](s8_6_2_r/receipt_summary.json)、[命令与退出码](s8_6_2_r/command_results.json)、[交付文件 hash](s8_6_2_r/artifact_manifest.json)。

## 7. 最终状态、遗留与下一步

| 项目 | 状态 / 实际含义 |
|---|---|
| 两类规则最小误拆修复 | PASS（离线真实程序路径） |
| 输入边界与当前请求预算 | PASS（离线 renderer/Mock/实际 dry-run） |
| 真实 QU 小样本 | 6/6 正常结束；分类/改写/激活限制如上，不能总称质量 PASS |
| 首题正式 HTTP | FAIL（发送前 profile 身份字段错误） |
| 最新身份修复 | 离线 PASS；真实 HTTP NOT RUN |
| 中文并列 / Frost HTTP | NOT RUN（首题失败后停止） |
| final_answer_correct | NOT EVALUABLE，未生成答案 |
| 来源支持、引用归属、呈现一致性 | NOT RUN |
| SSE 回放无重复 Provider / 异用户读取 | NOT RUN，本次未到该验收步骤；无第二合法 principal |
| Frost 研讨会直接场地关系 | 未新增验证；不改变既有 UNVERIFIED，不硬写 Franconia/Gold |

下一步最小工作是依据最新 `5f81e78` 另建可追溯临时镜像，重新绑定当前 profile/预算/新 ID，明确恢复真实验收后运行固定 HTTP 小样本；本轮停止，不自动补发。此前已用授权的实际消耗仅 7,268，不将剩余额度视为可以绕过停止条件的理由。先解决正常正式 API 验收；不进入 Cheap/Expensive 同证据对照或 P5 校准。

不扩展第三规则族、复杂规划、HyDE、Graph、记忆，不导入语料、不重算 Embedding、不重建索引、不部署生产。**未实现的命令不得报告通过。** 历史922项和六套全量评测 NOT RUN；两项既有旧基线配置失败未改期望，本轮不重复运行。上述测试组前后版本有重叠，不累计成一个新总测试数。
