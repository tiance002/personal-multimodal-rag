# S8.6 A/B 离线实施报告

日期：2026-10-21。状态：**A/B IMPLEMENTED + OFFLINE_PASS；真实质量与正式 API 验收 NOT_RUN**。

本报告中的 OFFLINE_PASS 指有界分支、来源安全、预算和实际生成消息投影合同通过离线验证。它不代表真实分类准确率、多跳问答准确率或生产可用性。**未实现的命令不得报告通过。**

## 1. 基线、工作树与提交

- 合法基线：`08ebd90b1c39965d1974de0717c5c6a66f36869a`，包含 S8.5.1；开始时核对本地和远端 S8.5 分支。
- 原 main：`0606063ad972c167de759f70137e9c1b07cd4b4f`，未用于开发；原 `.serena/project.yml` 未提交改动保留。
- 新独立工作树：`C:/Users/22088/.codex/worktrees/s8-6-adaptive-retrieval/RAG quention`。
- 分支：`codex/s8-6-adaptive-retrieval-20261021`。
- A 本地提交：`dc093c05b4b22b6c4ed92656f633cda20cd6fcd1`，`feat(s8.6-a): add opt-in query structure and serial source retrieval`。
- B 本地提交为包含本报告的提交；可执行 `git log -1 --format=%H -- docs/evaluation/S8_6_OFFLINE_IMPLEMENTATION.results.json` 解析。为避免提交自引用，机器结果冻结生产/测试文件哈希并记录该解析方法；最终交付另提供 B 完整 SHA。
- 本轮未 Push、部署、重建镜像、打 Tag、操作 ECS、执行真实数据库/索引操作或使用旧发送授权。

## 2. 实际改动与功能状态

| 范围 | 文件/主要函数 | 实际行为 |
|---|---|---|
| A 配置 | `conversation_configuration.py`、`http_api.py`、`runtime.py` | 白名单会话配置闭环；省略 off；显式 bounded 配对；keyword-only；非法值发送前拒绝 |
| A QU | `retrieval/query.py::understand_query` | 一次非思考 QU 四字段；先校验 rewrite；256 输出上限；off 原三字段/150/500 与 guard 保留 |
| A 计划 | `retrieval/strategy.py` | 闭合中英文比较两查询、原始 span；JOINT 保联合；复杂类型普通检索并记录未启用 |
| A 单轮 | `retrieval/pipeline.py::retrieve_once` | 无 QU/生成/持久化/递归；保留独立原始 Search/Rerank/source 结果 |
| A 串行 | `retrieval/search.py`、`storage/hybrid.py` | 候选 KB/模型/scope 分组、身份读取串行；内部 Expansion off；旧模式保持 |
| A 错误 | `retrieval/rerank.py`、`memory_affinity.py`、search | 候选权限/UNKNOWN/发送失败不被重试或 entity/affinity 降级吞掉；基础设施区别于空分支 |
| A 聚合 | `retrieval/strategy_executor.py` | 深度隔离子状态、最多三单元、轮转、安全去重、预算；不做最终 q0 硬阈值 |
| A 最终投影 | `generation/coordinator.py`、`generation/types.py`、`service.py` | 服务端绑定消息观察回调；实际 PreparedGeneration 后核验原文/句柄送达；P5/Prompt 不变 |
| B 第一跳 | `bridge_rescue.py::prepare_verified_bridge` | 复用 sentence-id/v2、原文回读、关系/身份验证；旧 rescue 为兼容包装 |
| B 决策 | 新 `bridge_strategy.py` | BridgePlan/Assessment；COMPLETE/RELATED_ONLY/NO_CANDIDATE/UNVERIFIED/CONFLICT；闭合直接关系判断 |
| B 新召回 | `pipeline.py` 追加分支 | DEPENDENT 已验证 Q1，最多一次原 scope Hybrid；不重入 QU 或完整 Pipeline |
| B Context | `retrieval/types.py`、`context.py`、`strategy_executor.py` | 原第一跳/姓名/普通证据保留；现有预算内原子补入；预览与真实消息送达分开 |
| 测试 | 五个 `test_s86_*.py` | QU/config、规则/执行器、实际 Pipeline、结构化桥接、B 全链路 Mock |

主要实现细节、完整流程图及 C 的未来接法见设计文档。没有新增运行依赖、Schema、索引、复杂 Agent、Graph 或 HyDE。

## 3. 已定位问题、修复与 RED → GREEN

| 可复现问题 | 最小修复 | 真实离线证据 |
|---|---|---|
| 旧三字段 guard 与 v2 冲突；配置投影/静态 HTTP 错误未接齐 | 拆保护正文与合同；专用 v2 wrapper；白名单接线与安全错误码 | QU 初始 35 failed/6 passed；最终聚焦 44 passed，exit 0；中间失败保留 |
| 没有可独立调用的 retrieve_once/执行器 | 抽取原阶段顺序，隔离状态、串行、轮转聚合 | 初始缺模块/5 项集成失败；A 综合 285 passed，exit 0 |
| 第二次阈值重排或实体检索会吞掉候选 UNKNOWN/权限失败 | 只对候选路径保留严格异常传播、区分未发送基础设施错误 | 实际 Pipeline 集成测试覆盖第二次阈值重排、entity UNKNOWN 和基础设施 ERROR |
| 准确第一跳可被用于回答无关对象/人物出生地 | 在原文对象/关系和 q0 间增加闭合匹配 | B question-target RED 2 failed；后续 B 138 passed，exit 0 |
| 二次准入重复姓名锚点、不同阶段地点冲突未合并核对 | 复用已有原始锚点且重新验证前驱链；累计见过的真实来源检查冲突 | B review RED 3 failed；后续 B 138 passed，exit 0 |
| 辅助抽取/旧池 BGE 的明确未发送调用上限拒绝会丢掉已验证事实 | 仅捕获 `LOCAL_EXECUTION_LIMIT_DENIED(sent=False)`，保留已验证来源和不完整状态 | RED 2 failed/1 passed；GREEN 138 passed，exit 0；sent=True 保持硬停止 |
| 新聚合误把既有 TopK=0 当作零条 | 恢复 0 表示不增加该上限；仅调整两个条件 | RED 1 failed；受影响 GREEN 97 passed，exit 0 |
| 抽取 retrieve_once 时，旧 Graph 空原文投影多置一项 EMPTY | 恢复基线该分支的一行状态行为，候选路径不变 | 最后 Graph/Pipeline 定向 134 passed，exit 0；不是新增 Graph 功能 |

测试替身的 tuple/list scope 断言错误、默认解释器缺 python-multipart、审计脚本最初错误要求 EMPTY 分支必有 Rerank、PowerShell quoting/UTF-8 读取错误均保留；相应修复是测试/工具修正，不计为模型或生产质量问题。未安装新依赖。

## 4. 离线验收矩阵

下表均为实际源码路径 + Mock adapter/repository 的离线验证，非真实云端、PG/Redis或部署验收。表中节点均位于 `tests/weknora_port/`；参数化例包含正反例。

| # | 验收项 | 对应测试（主节点） | 结果与边界 |
|---|---|---|---|
| 1 | off 三字段、参数及原路径 | `test_s86_qu_configuration.py::test_off_preserves_configured_legacy_prompt_and_output_budget`；原 pipeline/bridge/Graph 回归 | OFFLINE_PASS；legacy guard 字节相同；旧 intent 断言失败另列 |
| 2 | 一次非思考 v2、无冲突 | `test_v2_one_non_thinking_call_has_no_legacy_three_field_contract` | OFFLINE_PASS；默认/自定义旧模板均覆盖；不证明真实类型准确 |
| 3 | 历史指代与数字/否定 | `test_literal_history_substitution_preserves_original_question_numbers_and_negation`；旧 rewrite 合同 | OFFLINE_PASS；沿用原 validator |
| 4 | it 歧义/连续格式处理 | `test_v2_type_cannot_bypass_comparison_ambiguity_or_transform_protection`；`test_s851_history_transform_ambiguity.py` | OFFLINE_PASS；不任意选择 Franklin bells |
| 5 | CN/EN 比较、完整共同维度 | `test_s86_strategy.py::test_closed_chinese_compare_preserves_full_dimension_and_spans`、英文节点 | OFFLINE_PASS；仅有限 grammar |
| 6 | 否定/时间/嵌套/联合 AND | `test_unsafe_or_unclosed_rule_is_rejected`；`test_type_advice_does_not_create_generic_planner` | OFFLINE_PASS；JOINT 保持单查询，不分散条件 |
| 7 | 无效类型/截断回退 | QU 配置测试的 invalid type / legacy-three-field / truncated 参数组 | OFFLINE_PASS；不新增分类调用 |
| 8 | 子查询不重复 QU/生成/持久化 | `test_s86_pipeline_integration.py::test_retrieve_once_has_no_understanding_context_or_persistence`、actual pipeline 节点 | OFFLINE_PASS；root q0 保留，独立 operation IDs |
| 9 | 空分支/权限/UNKNOWN | strategy empty/error/unsafe 节点；pipeline retry/entity UNKNOWN 节点 | OFFLINE_PASS；空与基础设施区别，unsafe 传播 |
| 10 | 不按 q0 再删必要分支 | `test_serial_substates_operation_ids_root_preservation_and_round_robin`；B actual pipeline | OFFLINE_PASS；每轮各自重排；没有最终 q0 Rerank |
| 11 | 同 ID 版本/来源与数字差异 | `test_same_id_conflicting_origin_or_raw_body_fails_closed`、`test_identical_source_merges_coverage_but_different_numbers_stay` | OFFLINE_PASS；生成/非当前来源拒绝 |
| 12 | 已完整不新增 Search | `test_s86_bridge_strategy.py::test_complete_normal_source_stops_before_pool_bge_or_new_search`、complete-old-pool 节点 | OFFLINE_PASS；一次抽取/最多一次旧池 BGE |
| 13 | 旧池没有的新 ID、最终消息 | `test_s86_b_pipeline.py::test_actual_dependent_pipeline_new_scoped_search_and_final_model_delivery`；冻结 B3/GB3 回放 | OFFLINE_PASS；合成案例证明新 ID 路径；冻结案例证明真实姓名原文送达，不能混称真实新召回 |
| 14 | 机构线索不等于活动场地 | bridge related/closed-event 节点；`test_related_only_empty_new_recall_keeps_evidence_without_claiming_complete` | OFFLINE_PASS；Frost 仍 EVENT_VENUE_UNVERIFIED |
| 15 | 重复 Q1、预算/Context | B duplicate/topK 节点；strategy max-three/budget/cancel；bridge budget/error 参数组 | OFFLINE_PASS；保留原证据，不扩大上限 |
| 16 | 会话保存/读取/defaultoff/早拒绝 | QU public-session/runtime/actual-HTTP 配置节点 | OFFLINE_PASS；实际 ASGI route + Mock SQL，不是实际 DB 持久化验收 |

## 5. 实际执行命令与结果

完整 argv、cwd、exit、stdout/stderr SHA 见机器结果 `test_runs` 与本地原始 evidence。以下批次有重叠，**不得相加成独立用例数量**。

| 批次 | 真实输出 | exit | 时序/说明 |
|---|---|---|---|
| QU 聚焦 | 44 passed | 0 | v2、保护、配置、实际 ASGI/Mock SQL |
| 规则/执行器聚焦 | 44 passed | 0 | CN/EN、scope、预算、送达 |
| A 综合 | 285 passed | 0 | A 提交前受影响回归 |
| B 最新辅助预算回归 | 138 passed | 0 | 47 个 B 参数化控制 + 91 旧 bridge 用例 |
| A+B 综合 | 374 passed | 0 | B 未提交工作文件；早于最后 TopK=0 两条件修复和 Graph 一行兼容修复 |
| 重复 Q1 控制 | 1 passed | 0 | 只新增测试，无生产行为修改 |
| TopK=0 修复后受影响 | 97 passed | 0 | 6 个 B pipeline + 44 规则 + 47 bridge，重叠 |
| Graph 兼容恢复后最终受影响 | 134 passed | 0 | Graph 两组、旧 pipeline、S86 pipeline 与 B pipeline，重叠 |
| 最新冻结 B3/GB3 原文 → Mock final model | OFFLINE_PASS | 0 | 实际 Coordinator 消息中核验原文，非真实模型发送 |
| 旧配置关联回归 | 190 passed / 2 failed | 1 | 两项在完整原始 08ebd90 工作树也 2 failed；保持可见 |

两项 PREEXISTING_FAILURE：

- `test_conversation_configuration.py::test_dedicated_model_failure_falls_back_once_and_same_id_is_not_retried`。
- `test_conversation_configuration.py::test_exact_go_configuration_function_rows`。

旧 Go-oracle 的 greeting/LOCAL 预期与 S8.5 已有 q0 意图保护冲突。已在**完整原始 S8.5 工作树**执行同节点，exit 1、2 failed；并非只替换单个模块后推测。未改旧测试期望、未称全仓全绿，也未自行批准延期。证据：`root/full-baseline-preexisting.*` 和 `qu-config/preexisting-baseline.*`。

### 精确命令记录

**a-integration** — cwd: `C:\Users\22088\.codex\worktrees\s8-6-adaptive-retrieval\RAG quention`；exit `0`。

```powershell
& "E:/RAG quention/.venv/Scripts/python.exe" -B -m pytest -q -p no:reporter -p no:cacheprovider tests/weknora_port/test_s86_pipeline_integration.py tests/weknora_port/test_s86_strategy.py tests/weknora_port/test_s86_qu_configuration.py tests/weknora_port/test_pipeline.py tests/weknora_port/test_hybrid_storage.py tests/weknora_port/test_generation.py tests/weknora_port/test_service.py tests/weknora_port/test_affinity_pipeline.py
```

**final-ab** — cwd: `C:\Users\22088\.codex\worktrees\s8-6-adaptive-retrieval\RAG quention`；exit `0`。

```powershell
& "E:/RAG quention/.venv/Scripts/python.exe" -B -m pytest -q -p no:reporter -p no:cacheprovider tests/weknora_port/test_s86_qu_configuration.py tests/weknora_port/test_s86_strategy.py tests/weknora_port/test_s86_pipeline_integration.py tests/weknora_port/test_s86_bridge_strategy.py tests/weknora_port/test_s86_b_pipeline.py tests/weknora_port/test_bridge_rescue.py tests/weknora_port/test_bridge_pipeline.py tests/weknora_port/test_source_evidence.py tests/weknora_port/test_s851_bridge_identity_context.py tests/weknora_port/test_s85_bridge_identity.py tests/weknora_port/test_s85_ambiguity_clarification.py tests/weknora_port/test_s851_history_transform_ambiguity.py tests/weknora_port/test_generation_wire.py::test_reported_jean_without_finish_reason_is_not_a_completed_answer
```

**b-duplicate-q1** — cwd: `C:\Users\22088\.codex\worktrees\s8-6-adaptive-retrieval\RAG quention`；exit `0`。

```powershell
& "E:/RAG quention/.venv/Scripts/python.exe" -B -m pytest -q -p no:reporter -p no:cacheprovider tests/weknora_port/test_s86_b_pipeline.py::test_verified_q1_duplicate_of_original_query_stops_before_new_hybrid
```

**zero-topk-red** — cwd: `C:\Users\22088\.codex\worktrees\s8-6-adaptive-retrieval\RAG quention`；exit `1`。

```powershell
& "E:/RAG quention/.venv/Scripts/python.exe" -B -m pytest -q -p no:reporter -p no:cacheprovider tests/weknora_port/test_s86_b_pipeline.py::test_existing_zero_topk_means_no_additional_global_cap
```

**zero-topk-green** — cwd: `C:\Users\22088\.codex\worktrees\s8-6-adaptive-retrieval\RAG quention`；exit `0`。

```powershell
& "E:/RAG quention/.venv/Scripts/python.exe" -B -m pytest -q -p no:reporter -p no:cacheprovider tests/weknora_port/test_s86_b_pipeline.py tests/weknora_port/test_s86_strategy.py tests/weknora_port/test_s86_bridge_strategy.py
```

**graph-compat-final** — cwd: `C:\Users\22088\.codex\worktrees\s8-6-adaptive-retrieval\RAG quention`；exit `0`。

```powershell
& "E:/RAG quention/.venv/Scripts/python.exe" -B -m pytest -q -p no:reporter -p no:cacheprovider tests/weknora_port/test_graph_s7_retrieval.py tests/weknora_port/test_graph_retrieval.py tests/weknora_port/test_pipeline.py tests/weknora_port/test_s86_pipeline_integration.py tests/weknora_port/test_s86_b_pipeline.py
```

**冻结最终回放** — 运行 `E:/RAG quention/var/reports/s8-6/root/frozen-final/replay_final.py`，`run.exit.txt=0`；原始 stdout 与输入/消息 SHA 位于同目录和机器结果。

**完整旧基线复现** — cwd 为原 S8.5 工作树，节点为上列两个 `test_conversation_configuration.py` 用例；保留 stdout 与 exit=1。该批次完整 shell argv 未单独落盘，标为 `EXACT_SHELL_ARGV_NOT_CAPTURED`，不补造命令；另有 QU 模块基线复现的精确 argv 记录。


## 6. 可审计执行轨迹与冻结回放

机器结果 `execution_traces` 记录三个真正运行了当前 Pipeline、Coordinator 和 Mock final-model 接口的案例，保留 q0、有效改写、类型、原始 span、实际 Embedding/关键词/Rerank 问题、request IDs、各轮候选、原文版本/正文/locator、句柄注册、实际消息哈希与覆盖状态。完整本地原始消息留在 evidence 中。

1. 简单比较：`Compare PostgreSQL and Redis in terms of concurrency.` → 两条规则查询 `What are PostgreSQL concurrency?`、`What are Redis concurrency?`；各自 Embedding/关键词/Rerank 一致；最终问题仍是 q0。Mock 计数为 QU 1、Embedding 2、round Rerank 2、final answer 1；无中间答案。两条来源 DELIVERED，语义为 NOT_CHECKED。英文模板语法较机械，真实检索质量未测。
2. 合成依赖：`Where is the seminar Alice Bennett directed in 2011–2014 located?` → 原文完整对象 `Arbor Poetry Seminar` → Q1 `Where is Arbor Poetry Seminar located?`；新 `new-place-evidence` 原本不在旧池，由 Mock 仓库新召回，经 Q1 Rerank 和原文验证进入实际 Mock 生成消息。有限直接证据链为 COMPLETE；这不表示真实模型答案评分 PASS。
3. 机构线索：同问题只有较短机构名称的地址；旧池一次 BGE 后 RELATED_ONLY，追加一次 EMPTY Mock 召回，仍 RELATED_ONLY、`final_answer_supported=false`。

冻结 Frost B3/GB3 使用四份未修改输入，SHA 见机器结果。sentence-id/v2 的原抽取输入/输出和原 BGE 分数精确回放；四项旧原文回读正文一致；最多三个真实前驱 Chunk 的姓名身份链沿用既有验证。第一次任职原文为：

> She served as the Director of The Frost Place Advanced Seminar from 2007–2010, and serves on the faculty for the Stonecoast MFA Program in Creative Writing.

新 Q1：`Where is The Frost Place Advanced Seminar located?`。本次新召回是 EMPTY Mock，**不是新数据库检索或新来源发现**。

| 所需原文 | Chunk ID | 回放结果 |
|---|---|---|
| 任职第一跳 | `ec07a1fd-8a8e-4f23-a7d6-65d385d2343b` | 原文与身份链验证，进入最终消息 |
| 全名锚点 | `abc3a6aa-5453-4f0a-b7b6-5270cc96067f` | 完整原文补入，进入最终消息 |
| 姓氏身份链 | `86c40ec8-2147-431b-a1d4-3bfbc08e1232` | 原文保留，进入最终消息 |
| The Frost Place 机构地址 | `31417c7c-7abf-4723-956a-74bf7ee14683` | RELATED_ONLY，进入最终消息 |

结果：`delivered=true`，`source_chain_supported=false`，`final_answer_supported=false`，`EVENT_VENUE_UNVERIFIED`。旧 GB3 PARTIAL 和 B2 UNKNOWN 不变。历史 GB3 rewrite 将任职年份强化为活动年份的问题没有被当作合法新 QU；它只用于固定历史输入的回放，Q1 由 q0 与原文对象约束，不携带虚构活动年份。

## 7. 不变量、账本及验证等级

最终逐文件核对：原基线 37 个受保护 Prompt/P5/RRF/冻结样例文件 LF-SHA 全部一致；另外 54 个 Provider/流、history/identity、迁移及依赖清单文件 LF-SHA 一致。旧 S8.5 受保护 476 个文件原始字节 SHA 全部一致；原工作树仍在 08ebd90 且干净。B2 的 1 笔 UNKNOWN、6365 held Tokens 未改动。本轮未读写真实 PG/Redis/索引，不能把静态保护核对写成实时库状态验收。详见机器结果 protection、source manifest 与本地 final-protection.json。

| 等级/能力 | 状态 |
|---|---|
| SOURCE_READ / A+B IMPLEMENTED | PASS |
| 受影响离线合同及冻结记录重放 | OFFLINE_PASS，旧失败独立保留 |
| 隔离真实 PG/Redis 集成 | NOT_RUN |
| 新真实 QU 分类 / 检索 / 生成 | NOT_RUN |
| 新 Provider 尝试 / 结算 / UNKNOWN | 0 / 0 / 0 |
| 新实际输入 / 输出 / 总 Token | 0 / 0 / 0，不估算费用 |
| 正式 API 真实验收 / CUTOVER | NOT_RUN / NOT_RUN |
| PARALLEL/MIXED 复杂规划 / HyDE / 通用语义覆盖 | NOT_IMPLEMENTED |

## 8. 限制与下一步最小任务

- 规则拆分仅覆盖闭合 CN/EN 比较；额外条件、数字及日期等可能保守退回普通检索，不证明任意比较等价。
- B 问题与第一跳绑定支持窄英文模式；直接位置/活动场地验证只是有限 grammar，复杂句保留 UNVERIFIED。
- 比较分支的命中不等于语义支持；没有新增模型 Coverage 调用或通用事实证明算法。
- 原始子块仅以父块合并内容呈现时，当前 required-source 送达检查可能保守给出未送达，不伪造 PASS。
- 最多三单元不保证在现有 12-call 账本中必可完成；跨 KB 分组/重排分批可能提前耗尽预算，保留不完整状态。
- Frost 活动场地在当前来源下仍未证实；不能因机构地址或姓名锚点送达把 PARTIAL 改成 PASS。
- 既有两项 intent 基线断言失败保留供独立后续处理。本轮没有改 Go-oracle 或扩大意图算法范围。

下一步真实验证只形成计划，不执行，也不视为已取得授权：

| 独立实验 | 固定输入与控制 | 准备保存/评价 |
|---|---|---|
| QU 分类 | 相同 q0/有限历史/权限，对比 legacy 与 v2；单独比较 150/256 profile | 原始 QU 字节、finish/usage、rewrite 校验、类型与独立语义审查；无 Gold 注入 |
| 检索策略 | 固定有效 QU、第一跳输出；候选 A/B 均 Expansion off，legacy 单独列 | 每轮 request、原文版本/locator、Rerank、缺口、实际 Context、Token/延迟；质量与成本分开 |
| 正式 API | 准确最终源码、可追溯隔离服务；真实会话/PG/Redis/SSE/Registry/SendGate | 正常终止、历史/状态一致、引用可回读、断线不重发、合法第二用户隔离、完整账本 |

真实前置：完成 S8.5.1 正式 profile、引用配置、单账本 12-call 与聚合上界协调、合法第二用户 HTTP、预算 dry-run 和安全隔离。将调用次数/Token/停止条件作为新的独立 Owner 外发授权申请；旧授权、旧 UNKNOWN 或 MML 提示词实验均不能替代。未安全隔离时保持阻断，不更新现有 API/Worker。

本轮完成 A/B 本地离线交付后停止，不自动开展 C、真实调用、Push 或部署。
