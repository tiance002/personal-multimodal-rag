# P5 RuleRouter V1 开发交付 — BLOCKED / NEEDS_OWNER_REVIEW

日期：2026-10-09。任务身份：`p5-rule-router-r1`。本轮实际真实模型请求 **0**，真实数据库访问 **0**；无 push、部署、Tag、迁移或业务索引重建。未实现的命令不得报告通过。

规则、生成接口、受控升级和离线回放已实施。最终新增 P5 测试 **104 PASS**；最终 23 文件关联回归 **335 PASS / 2 FAIL / 0 ERROR / 0 SKIP**，退出码 **1**。两项失败已在指定 P4 基线重现，不是新增回归，但属于既有 DeepSeek 收据/授权状态保护的未闭合合同。本报告不自行批准延期，不标 ACCEPTED 或 P5 完整通过。真实双模型接线保持 **BLOCKED_REAL**。没有形成新 commit，全部本轮修改保留待复核。

## 1. 基线、授权与执行范围

实际分支 `codex/local-first-rag-v1-20260930`，开始/结束 HEAD 均为 `e6cca0971d0f1875ca562a6b953fd5ec252b28e0`。开始工作树干净；读取实际 AGENTS.md、progress.md、ADR 索引、源码状态/已知问题和 P4 Hybrid 闭包报告。旧文档中的 paused/main 状态未代替现场分支和本轮 Owner 授权。

读取 Owner 的单独 Markdown 与 ZIP 中同名规范；二者字节一致，SHA-256 `18f813c136f57817b7de5f1e78a176d063d37c8657e1427fdcfa40560b7cada7`。ZIP 还包含启动指令。没有下载资料、模型或 tokenizer。最新 P5 授权允许本任务白名单代码/测试/文档开发，具体范围优先于 AGENTS 附录旧 dynamic-route-01 准备任务的路径限制；没有改 AGENTS 或全局配置。

开始快照 `var/reports/p5-rule-router-r1/baseline.json` 记录 **535** 个已追踪文件的字节哈希。末尾 manifest 验证除本轮七个既有文件外，其他原追踪文件保持一致。P3、P4 检索/融合/父块/Context、Embedding/Rerank、模型 registry、deploy 配置、Migration、冻结 PDF/gold、旧测试及冻结评测集均未修改。

采用 efficient-goal-execution 工作流；阶段源码快照、JUnit 和执行 JSON 是可恢复 checkpoint，汇总在 `var/codex-goals/p5-rule-router-r1.json`。主协调单写者，未派出 Worker；实际 serving model/effort 缺少控制面证明，记录 UNKNOWN。执行性质 MANUALLY_SUPERVISED_TRIAL，不宣称自动化控制面强制。没有读 `.env.local`、Key、全局 DeepSeek 账本或私有语料；真实账本未重新现场核验。

## 2. 实际修改文件和阶段状态

共 **18 个项目文件**，完整清单/哈希见 final-manifest.json：

| 文件 | 修改用途 | 状态 |
|---|---|---|
| backend/app/application/router_feature_extraction.py | 有界任务正则、条件/逻辑/依赖/负载/输出特征，未知状态 | P5.1 离线通过 |
| backend/app/application/rule_router_v1.py | Decimal 评分、Envelope、精确模型角色、B 策略、正常角色预算接口 | SIMULATED 通过；REAL 阻断 |
| backend/app/application/quick_chain.py | 原固定 Quick 内接线、冻结 prompt、两次上限、取消、防重复 | P5.2/3 模拟通过 |
| backend/app/application/answer_service.py | Router 请求幂等要求和取消回调，保留最终原子提交 | 模拟通过 |
| backend/app/application/answer_hardening.py | 只新增无副作用 check_candidate，不修改旧 finalize | 模拟通过 |
| backend/app/application/run_metrics.py | 独立 rule_router 指标字段 | 离线通过 |
| backend/app/bootstrap.py | Cheap Factory 与原 DeepSeekGateway 的闭锁角色绑定 | 真实仍 BLOCKED_REAL |
| backend/app/config.py、.env.example | 默认关闭的受信启动配置 | 离线通过 |
| backend/tests/test_p5_router_features.py | 评分正反例与边界 | 50 PASS |
| backend/tests/test_p5_router_generation.py | 模拟适配器/预算/升级/原证据映射 | 26 PASS |
| backend/tests/test_p5_router_boundaries.py | 容量、作用域、取消、并发、最终提交、持久化、用量 | 17 PASS |
| backend/tests/test_p5_router_replay.py | 缓存身份、四臂回放、未知成本和排他输出 | 11 PASS |
| scripts/replay_p5_router.py | 不构建 Provider/DB 的离线回放 CLI | 实际执行退出 0 |
| evaluations/router_dev/README.md、cases-v1.json | 36 项 SIMULATED 小型开发诊断 | 语义质量 NOT_REVIEWED |
| docs/design/rule-router-v1.md | 接口、策略、边界及回滚说明 | 已保存 |
| docs/audits/p5-implementation-report.md | 本报告 | 已保存 |

P5.0 已查清两条不同预算链。通用 CloudChat 的 DeepSeek 角色仅有 role usage guard，不能直接替代现有 DeepSeekGateway 的 SessionAttemptGate/持久收据。P5 没有改 cloud.py、factory.py、provider_usage.py、deepseek.py 或任何旧 Gate。

## 3. 评分案例与能力边界

`S=T+C+J+D+L+M` 使用整数百分位贡献和 Decimal，`>= .35` 为 Expensive，否则 Cheap，理论最大 .79。以下为规则测试预期，**不是真实答案质量或已校准最佳阈值**：

| 请求 | 可观察贡献 | 分数/选择 |
|---|---|---|
| 列出甲方解除条款原文 | T=.02，其余未知/零贡献 | .02 / Cheap |
| 根据合同甲方能否解除 | T=.16，未推断隐藏条件 | .16 / Cheap（已知漏判风险） |
| 列出“能否解除协议”相关条款，不需要判断 | 引文和否定不构成 apply_rule | .02 / Cheap |
| 若金额≤5000，且负责人已批准，且安全审批通过，能否报销 | T=.16+C=.11+J=.03 | .30 / Cheap |
| 同三条件，除非紧急情况豁免上述条件 | T=.16+C=.11+J=.09，例外范围显式 | .36 / Expensive |
| 先计算两个增量，再用结果算占比 | T=.08+D=.09 | .17 / Cheap |
| (440-200)/(1300-1000) | 两层依赖，非三次操作即三层 | D=.09 |

仅分析 q0 或现有业务流程验证的 follow-up，绝不扫描 Context 条件。多个任务取最高 T 并保留标签/本地 anchor；对外指标只保留 anchor hash。条件计数 0–5+、同层 AND/OR、混合、明确例外、嵌套例外、两层计算、输出事项/内部步骤、阈值等号、负载边界均已执行。

当前正则是有界语法，不是完整语义解析：同义条件去重、隐含合同前提、多义请求、未识别逻辑和依赖继续 UNKNOWN_NOT_INFERRED。未知不伪装成已证明 0 条件/0 Tokens。

L 输入是实际完整生成 prompt。没有可靠默认 chat tokenizer，所以生产估算为 None/UNKNOWN；离线注入估算器覆盖 2000/6000/12000 边界及异常回退。SIMULATED 输入容量相等/超限/未知均测试，不能把测试容量 100 当模型容量。没有自动裁掉证据，也没有复制上游绝对 token 上限。现有 CloudAdapter 的 planned_tokens 并不证明完整输入容量或账单费用，真实能力合同仍待复核。

## 4. 接线、B 策略和原证据保护

OFF 保留原 LOCAL Ollama、显式 CLOUD DeepSeek、旧失败路径和 Smart；DYNAMIC 不走 LOCAL 名称伪装 Cheap。所有 KB 的 cloud_allowed 和全局出站配置仍须允许。上游无证据/缺项/partial/权限拒绝零生成。角色身份、预算、容量或授权拒绝时不静默换模型。

Cheap 精确为 SiliconFlow/XingChenAGI/Xing4.0-29B，正常 CloudChat.generate 自己使用唯一 role usage guard；Quick 不额外月度预占。Expensive 精确为 DeepSeek/deepseek-flash，只接原 answer_with_product_scope 和独立的一次月度角色 guard；闭合 Gate/收据要求未被替代。所有真实绑定在发送/预占前明确拒绝，当前只能使用带 SIMULATED 标签的内部离线注入。

GenerationEnvelope 固定 q0、可信解析、模板、完整 prompt、Context hash、E labels、Child/version/quote/locator hash、输出上限及超时。升级不附加 Cheap 草稿，不改 P4 候选/证据池/Scope，不增加检索或 rewrite 调用。正文、Child hash/version/locator、原引用标签实际由旧 Validator/CommitCheck 检查。

原候选先做同样的纯 marker normalization、Validator 和 Hardening.check_candidate，再决定 B；没有先执行 fallback 或把第一次失败答案落成最终业务答案。最多 Cheap 一次+Expensive 一次；直接 Expensive 不升级。质量失败、整题拒答、供应商失败分别记 QUALITY_ESCALATION/ABSTENTION_ESCALATION/PROVIDER_FALLBACK；后两项默认关闭，truncation 单独默认关闭。引文/部分拒答不匹配整题拒答。

最终仍走完整旧 finalize 和 FinalAnswerCommitCheck。请求/attempt 身份稳定；同 run 改阈值/角色、并发重复、跨新 chain 的 RunEventStore 重复 claim 均阻断。取消后没有第二家调用。两个生成候选只发生一次最终业务提交；原前端最终事件方式未改。

## 5. 命令、退出码和失败保存

完整 pytest argv、执行时长、Guard 计数和测试前后源码哈希分别在每个 attempt 的 execution.json/source-before.json/source-after.json；目录排他创建，旧失败没有被覆盖。所有执行用现有 Python/pytest；没有改 ACL、关闭保护、启动服务或临时下载依赖。首次 runner 的 socket import NameError 在 pytest 前发生，退出 1，NOT RUN；其后修复离线 runner，保留该已消费空目录和 early-runner-error.json。

| attempt | pytest/进程退出 | 实际结果 |
|---|---:|---|
| p5-1-checks-2 | 0 / 0 | 50 PASS |
| p5-23-checks-1 | 1 / 1 | 95 PASS、17 FAIL；CloudChat model 读取错误 |
| p5-23-checks-2 | 0 / 0 | 112 PASS；复用 spec.model_id，原断言未改弱 |
| p5-4-checks-1 | 0 / 0 | 87 PASS |
| p5-4-final-checks-1 | 1 / 1 | 332 PASS、2 FAIL |
| baseline-product-receipt | 1 / 1 | 指定基线 16 PASS、同样 2 FAIL |
| p5-4-final-checks-2 | 1 / 1 | 334 PASS、同样 2 FAIL |
| p5-metrics-final | 0 / 0 | 118 PASS；含最终 104 项 P5 |
| final-regression | 1 / 1 | 最终源码 335 PASS、同样 2 FAIL、0 ERROR、0 SKIP |
| replay-1 | 非 pytest / 0 | 36 样本四臂 SIMULATED 回放 |

最终实际命令（已运行，重复需新 attempt，不复用已消费目录）：

```powershell
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py final-regression backend/tests/test_p5_router_features.py backend/tests/test_p5_router_generation.py backend/tests/test_p5_router_boundaries.py backend/tests/test_p5_router_replay.py backend/tests/test_langchain_quick_chain.py backend/tests/test_quick_chain_budget.py backend/tests/test_answer_service.py backend/tests/test_answer_hardening.py backend/tests/test_bootstrap.py backend/tests/test_run_metrics.py backend/tests/test_final_answer_commit.py backend/tests/test_cancellation_boundaries.py backend/tests/test_product_receipt_repair.py backend/tests/test_deepseek_policy_boundary.py backend/tests/test_egress_matrix.py backend/tests/test_model_provider_contracts.py backend/tests/test_p4_rag_pipeline.py backend/tests/test_quick_evidence_flow.py backend/tests/test_quick_chain_quality.py backend/tests/test_quick_citation_prompt.py backend/tests/test_quick_answer_language_prompt.py backend/tests/test_privacy_configuration_answer.py backend/tests/test_answer_validation.py
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py baseline-product-receipt backend/tests/test_product_receipt_repair.py
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/run_replay_guarded.py replay-1
git diff --check
```

Guard 禁止真实网络、native PostgreSQL、子进程、Secret 文件与 canonical DeepSeek 账本；最终各 Guard 触发数均 0，source-before/after 相等。Python launcher 仍有 local Python executable path location 告警；真实退出码/JUnit 按命令结果记录，未把告警掩盖成测试失败或成功。

两项失败均为原 `test_product_receipt_repair.py::test_persistence_failure_preserves_unknown_budget_and_closes_scope[replace/readback]` 第 100 行。初次收据失败后占用 UNKNOWN、scope 关闭正常；fixture 明确重新 enable，再次同身份调用被拒且发送数仍 1，但 calls_allowed_in_this_task 保持 true，断言要求 false。基线模块通过 git show 的六份原源码进行只读 import overlay；其余依赖及旧测试字节与 baseline 相同。没有 checkout/reset/stash 或替换工作树文件。实际模块 SHA、失败 node ID/断言对照见 failure-baseline-comparison.json。未据此宣称旧 Gate 已安全闭合或自动批准延期。

## 6. 用量、费用和离线回放

每个 attempt 留角色/provider/model、身份、同 Envelope hash、状态、固定错误类别、finish、耗时、可得 Token、UNKNOWN 结算/费用和来源；不保存 Provider 任意错误字符串、Key、请求头或新增 prompt/Context 正文。供应商格式的模拟 Token 11/7 与 12/8 在双次生成中累计 23/15，已专门断言；这不是实际供应商用量。实际墙钟/Router CPU 时间记录在执行/回放 JSON。没有把未知成本写 0。

36 项仅为开发诊断；缓存 identity 绑定模型、模板、prompt/Context、参数及现有后处理源码哈希，任一改变拒绝复用。规则改分可复用同 Context，生成身份变化不可复用旧回复。

| SIMULATED arm | 直接 Expensive | B 升级 | 确定性校验 PASS | scenario p50/p95 ms |
|---|---:|---:|---:|---:|
| CHEAP_ONLY | 0 | 0 | 28/36 | 12/12 |
| DYNAMIC | 3 | 8 | 36/36 | 12/44 |
| EXPENSIVE_ONLY | 36 | 0 | 36/36 | 32/32 |
| ALL_CHEAP_SAME_B | 0 | 8 | 36/36 | 12/44 |

这些延迟是显式构造的场景值，不是模型实测；校验 PASS 仅指现有确定性规则，语义质量 NOT_REVIEWED。四臂 actual cost UNKNOWN、Net Saving NOT_AVAILABLE，不能用 Cheap 占比代替降本。纯净成本公式测试也只是算术反例。没有证明≥10%实际降本。

保留明确 UNDETECTED 反例：模型自信说“月亮是奶酪做的 [E1]”，引用原文“月亮是石头做的”，现有确定性校验仍可能通过而不升级。本轮没有虚构一般事实验证能力。

## 7. 继承限制、Owner 待审批和最小补证

1. **真实接线 BLOCKED_REAL**：P5 双角色报价/账户许可、输入容量和准确 P5 attempt/prompt/output cap 授权未核实。旧 DeepSeek 产品 Gate 绑定已有请求身份/内容，不能将它当新 P5 的开放额度。需要单独审阅注册 Gate+durable receipt 与月度预算路径，再有界授权真实生成；本轮不能读取/重置旧账本或复用 P4 模型验证额度。生产闭锁需要后续受审代码变更，不靠环境布尔值绕过。
2. **两项旧收据状态失败仍 FAIL**：Owner 决定是否授权最小修复现有 ReviewedProductRequestGate.execution_scope/validate_request、ProductReceiptFileSink.prepare 或对应不可变 scope-close 边界，并保留旧断言。当前发送拒绝有效，但开关状态未满足合同，不能自动延期或弱化测试。
3. P4 全后端历史结果 1285 PASS/11 FAIL/20 ERROR/132 SKIP 是其既有版本证据；本轮未重跑完整全项目套件，不写成当前通过。历史 29 项失败基线记录不改；4 项 Caption 因历史目录缺失仍 NOT RUN。native-runtime/fixture、OCR 原因变化、layering 等未在 P5 扩修。**依赖升级影响未独立验证**限制不变。
4. 本轮没有真实 PG+生成、真实模型容量/外发/结算、商业账号或真实业务质量验证。临时 PG 集成 NOT RUN；内存合成预算/Provider 和现有离线 SQL mocks 不能冒充真实 PG。P4 数据库/账本现场身份和余额只引用旧报告，不在本轮重新核验或推定仍一致。
5. draft-v0.4 的隐含任务/条件漏判、假拒答、静默错误和无效升级需要独立人审；P7 才能做正式同 Context 对照。未授权 LLM 分类、Router 权重搜索或 P6/P7 实验。

## 8. 最终版本、证据和停止

当前 HEAD `e6cca0971d0f1875ca562a6b953fd5ec252b28e0`；没有新增 commit，index 未变，18 个项目文件保留 dirty。状态 **BLOCKED / NEEDS_OWNER_REVIEW**，仅新增离线代码检查可记 CHECKS_PASSED。没有自授 ACCEPTED、P5_PASS、全项目验收、延期批准或真实可用状态。

证据目录 `var/reports/p5-rule-router-r1/`：baseline.json、各轮 JUnit/命令/被测源码快照、failure-baseline-comparison.json、final-tested-source-snapshot.json、final.diff、git-checks.json、final-manifest.json、replay-1/replay.json。结束再次检查 HEAD、工作树白名单、旧追踪文件哈希、最终被测源码和 whitespace。manifest 列出 18 文件及全部当前证据 SHA-256；报告自己的哈希由 manifest 外置，避免自引用。

回滚开关 `RAG_RULE_ROUTER_ENABLED=false`；回滚代码须复核这 18 个文件并保护后续 dirty，不执行全仓 reset/clean。无 DB/Storage 回滚操作，模型 UNKNOWN/历史收据不可删。完成本轮交付后停止，等待 Owner 对安全缺口、独立修复和提交的复核；不进入后续阶段。


# P5 Owner Review Fix R1 — CHECKS_PASSED / NEEDS_OWNER_REVIEW

本轮仅完成 Owner 指定的六项最小修正及 SIMULATED/离线检查，不批准 P5 验收、提交或真实启用。HEAD `e6cca0971d0f1875ca562a6b953fd5ec252b28e0`，分支 `codex/local-first-rag-v1-20260930`。开始时上轮 18 文件字节与 final-manifest 全部一致，未发现并行漂移；开始副本、哈希和 Git 状态在 start-snapshot.json/before/。没有 reset/stash/clean；原历史报告、JUnit、失败记录及账本未覆盖。

## 1. 目标与实际修改

本轮改变 8 个原 P5 已交付文件：

- backend/app/application/router_feature_extraction.py：逗号/分号不再决定例外边界；保留已识别条件，未识别余项明确标 OBSERVED_LOWER_BOUND_UNKNOWN_REMAINDER / LOWER_BOUND。无条件范围的例外保持 UNKNOWN，不推断 J=0.09；不完整条件不伪装完整计数。
- backend/app/application/rule_router_v1.py：仅补 Expensive guard 结算失败的固定异常信息，保留结算前 COMPLETED/NOT_SENT/TRUNCATED/PROVIDER_FAILURE；预算 reserve/settle 次数和 sent 合同不变，不记录原异常消息。
- backend/app/application/quick_chain.py：请求幂等集合有界，生成、候选校验、发送事实和结算操作分别记录。
- backend/tests/test_p5_router_features.py、test_p5_router_generation.py、test_p5_router_boundaries.py：新增 47 个参数展开用例，原 104 个 node ID 全数保留且通过，未改弱原断言。
- docs/design/rule-router-v1.md、docs/audits/p5-implementation-report.md：追加本轮说明，原报告字节前缀保留。

numeric identity 在 AST 算术前屏蔽：YYYY-MM-DD、带标签版本/编号/型号/ID、v 版本。没有计算指令的裸连字符编号不算减法；真正公式仍按 AST 层数计分，日期前缀不遮蔽后续合法公式。最小新增规则包含“能不能”和明确数量/列举式实体 × 指标；“多个/若干”等不明数量仍 UNKNOWN。引用或否定指令中的词不抬分。

T/C/J/D/L/M 贡献表及 `0.35` 未改变，regression-comparison.json 有 AST/常量对照。L 默认仍 UNKNOWN/None，长达 20,000 字符的 prompt 不伪装 Chat Token。原 Validator、FinalAnswerCommitCheck、P4 检索/Rerank/Parent/Context/Citation 源码字节未改变。Cloud/DeepSeek 适配器、旧收据测试及账本实现字节未改变。

## 2. 生命周期与记录准确性

`_router_runs` 在锁内保存固定长度 digest tombstone；默认最多 4096 个，构造时仅允许整数 1–4096，不能通过配置开大。重复检查先于容量检查，故容量满时旧身份仍返回 DUPLICATE_REQUEST。新身份满额时返回 ROUTER_IDEMPOTENCY_CAPACITY_EXCEEDED，零 reserve、零 send。`_router_attempts` 每个已认领 run 至多两条稳定身份，因此最多 8192 条。两集合都不会因 TTL/LRU/完成/失败而删除，避免重发；实例释放时正常随对象释放。容量、并发与重复实测使用缩小的合成容量 2，不是内存性能压测。

**代价需 Owner 复核：** 这是本阶段安全、有界的 fail-closed 方案；长寿命实例达到 4096 个路由认领后会拒绝新路由，需要后续批准的实例生命周期方案或可信持久 claim 复用设计。没有自动轮换实例、清账本或新增 DB 表。跨 chain/重启的防重复仍由现有 RunEventStore.create_run_once、Provider gate 与持久收据承担，不把内存 tombstone 说成持久化保障。现有跨 chain 原测试继续 PASS。

| 状态 | generation_status | finish_reason | result_validation | 结算 |
|---|---|---|---|---|
| 正常返回且候选通过 | COMPLETED | stop | PASS | UNKNOWN 费用；操作 COMPLETED |
| 正常返回但候选拒绝 | COMPLETED | stop | 原拒绝码 | UNKNOWN 费用；操作 COMPLETED |
| 截断 | TRUNCATED | length | NOT_RUN | UNKNOWN；无默认第二次调用 |
| 明确未发送 | NOT_SENT | UNKNOWN | NOT_RUN | NOT_SENT，操作 NOT_REQUIRED_OR_RELEASED |
| 请求失败/超时 | FAILED | UNKNOWN | NOT_RUN | UNKNOWN；未推断发送成功或退款 |
| 返回后本地校验异常 | COMPLETED | stop | VALIDATION_ERROR | UNKNOWN；不误归 Provider 故障 |
| 结算操作失败 | FAILED | UNKNOWN | NOT_RUN | UNKNOWN，操作 FAILED；能证明未发时 send_status=NOT_SENT 但占用仍 UNKNOWN |

`send_status=RESPONSE_RECEIVED` 只在完整/截断返回可确认时使用，无法证明则 UNKNOWN。所有 actual_cost 保持 UNKNOWN，没有凭模拟 Token 做 ACTUAL 费用。保留首个结算前结果的固定枚举，不记录任意错误字符串、正文、Header 或 Key。Cheap adapter 无法提供原始阶段的情况不猜测，保留 UNKNOWN。正常 guard 结算完成也不等于供应商实际结算。

## 3. 原句、修复前后实际评分

前 22 个评分反例在修复前 pytest 实际执行并保存失败；后补 6 项也实际调用开始快照中哈希匹配的原 extract_features/decide，与最终实现逐项对照。所有 28 项最终值与最终 JUnit 轮次 scores.json 一致。这里没有把原副本计算说成旧全套 pytest 通过。完整特征/状态/角色在 score-comparison.json。

| 合成反例原句 | 修复前 | 修复后 |
|---|---:|---:|
| 若金额≤5000，且负责人已批准，且安全审批通过，除非紧急情况豁免上述条件，能否报销 | 0.16 | 0.36 |
| 若金额≤5000；且负责人已批准；且安全审批通过；除非紧急情况豁免上述条件；能否报销 | 0.36 | 0.36 |
| 若金额≤5000,且负责人已批准,且安全审批通过,除非紧急情况豁免上述条件,能否报销 | 0.16 | 0.36 |
| 若金额≤5000;且负责人已批准;且安全审批通过;除非紧急情况豁免上述条件;能否报销 | 0.36 | 0.36 |
| 若金额≤5000，且负责人已批准，且安全审批通过，除非紧急情况，能否报销 | 0.16 | 0.3 |
| 若金额≤5000，且情况待核实，且负责人已批准，能否报销 | 0.16 | 0.26 |
| 比较2026-10-09与2026-10-10 | 0.09 | 0.09 |
| 比较版本1.2-3与版本1.2-4 | 0.12 | 0.09 |
| 摘录编号123-456 | 0.05 | 0.02 |
| 摘录ID:123-456-789 | 0.11 | 0.02 |
| 比较v1.2-3与v1.2-4 | 0.12 | 0.09 |
| 计算2026-10-09的(440-200)/(1300-1000) | 0.08 | 0.17 |
| 计算123-45 | 0.11 | 0.11 |
| 根据合同能不能解除 | 0.1 | 0.16 |
| 列出‘能不能解除’的原文，不需要判断 | 0.02 | 0.02 |
| 摘录条款，不要判断能不能解除 | 0.02 | 0.02 |
| 分别列出三个城市两个指标 | 0.02 | 0.06 |
| 分别列出三个实体的两个指标 | 0.02 | 0.06 |
| 分别列出北京、上海、广州的成本和收入 | 0.02 | 0.06 |
| 分别列出多个实体与指标 | 0.02 | 0.02 |
| 列出‘分别列出三个城市两个指标’这句话 | 0.02 | 0.02 |
| 不要分别列出三个城市两个指标，摘录原文 | 0.02 | 0.02 |
| 比较2026-10-10与2026-10-11 | 0.18 | 0.09 |
| 计算版本号1.2-3的成本 | 0.11 | 0.08 |
| 计算编号为123-456对应的费用 | 0.11 | 0.08 |
| 计算ID:123-456的(8-2)/3 | 0.11 | 0.17 |
| 计算版本v1.2.3对应的成本 | 0.08 | 0.08 |
| 摘录123-456 | 0.05 | 0.02 |

无明确例外作用域不会凭空获得 scoped exception 分；未知条件余项只保留已识别下界。规则仍是有限语法观察，不是语义解析，也不是已校准最佳 Router。

## 4. 实际命令、退出码、JUnit

受保护的现有 offline_runner 未修改；其 SHA 在 final-manifest。解释器为当前 .venv Python 3.13.0，pytest 9.1.1。离线 guard 禁止网络、native psycopg、子进程、secret 与 canonical 账本读取。各轮所有 guard 触发数为 0，真实 API/真实 DB 调用为 0，测试前后源码哈希均相等。输出目录均为独立新 attempt；没有复用旧已消费目录或提权/改 ACL。

| attempt | 实际结果 | pytest / 进程退出 | 用途 |
|---|---|---:|---|
| owner-review-r1-before | 10 PASS / 22 FAIL / 0 ERROR / 0 SKIP，93 deselected | 1 / 1 | 初始 32 项反例；尚未修改生产代码 |
| owner-review-r1-after | 145 PASS / 0 FAIL / 0 ERROR / 0 SKIP | 0 / 0 | 原 104 + 首批 41；之后补了身份标记边界，非最终源码证明 |
| owner-review-r1-final-regression | 382 PASS / 2 FAIL / 0 ERROR / 0 SKIP | 1 / 1 | 最终源码，含 P5 全部 151 PASS（104 原 +47 新） |

最后轮 execution.json 的保护入口总耗时 32.899322 秒；pytest 输出执行时间 23.50 秒。这是本机测试时间，不是实际云模型延迟。Windows Python launcher 的 D:/Drivers/python/python.exe 位置告警及 SWIG deprecation warning 保留，不改解释器或造通过。

```powershell
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py owner-review-r1-before backend/tests/test_p5_router_features.py backend/tests/test_p5_router_generation.py backend/tests/test_p5_router_boundaries.py -k owner_r1
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py owner-review-r1-after backend/tests/test_p5_router_features.py backend/tests/test_p5_router_generation.py backend/tests/test_p5_router_boundaries.py backend/tests/test_p5_router_replay.py
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py owner-review-r1-final-regression backend/tests/test_p5_router_features.py backend/tests/test_p5_router_generation.py backend/tests/test_p5_router_boundaries.py backend/tests/test_p5_router_replay.py backend/tests/test_langchain_quick_chain.py backend/tests/test_quick_chain_budget.py backend/tests/test_answer_service.py backend/tests/test_answer_hardening.py backend/tests/test_bootstrap.py backend/tests/test_run_metrics.py backend/tests/test_final_answer_commit.py backend/tests/test_cancellation_boundaries.py backend/tests/test_product_receipt_repair.py backend/tests/test_deepseek_policy_boundary.py backend/tests/test_egress_matrix.py backend/tests/test_model_provider_contracts.py backend/tests/test_p4_rag_pipeline.py backend/tests/test_quick_evidence_flow.py backend/tests/test_quick_chain_quality.py backend/tests/test_quick_citation_prompt.py backend/tests/test_quick_answer_language_prompt.py backend/tests/test_privacy_configuration_answer.py backend/tests/test_answer_validation.py
git diff --check
```

最后命令默认 diff --check 退出 0；各 R1 改动的 no-index 检查（包含未追踪文件）和完整输出单独保存 git-checks.json。完整 pytest argv（含仓库 -ra 和 basetemp/JUnit 绝对路径）在各 execution.json；实际进程退出回执在 process-exits.json。未实现的命令不得报告通过。真实 API/DB/完整全项目回归均 NOT RUN。

两个旧失败仍为 `backend/tests/test_product_receipt_repair.py::test_persistence_failure_preserves_unknown_budget_and_closes_scope[replace]` 与 `[readback]`，同第 100 行 `calls_allowed_in_this_task is False`，实际 True；拒绝重发仍只有一次模拟发送。失败名字及 JUnit message 与上轮完全相同。测试、Cloud/DeepSeek adapter、旧 gate 实现原字节保持，无删断言、skip 或 xfail。详见 regression-comparison.json。原失败虽已基线证实，仍记 FAIL；本轮不处理，也不自动批准延期。

## 5. 精确 diff、完整哈希、版本与待审批

本轮只比较开始副本的 `r1.diff`；相对 HEAD 的完整 18 文件 P5 改动另列 `full-p5.diff`，包括 git diff 默认漏掉的未追踪文件。final-manifest.json 保存本轮 8 文件前后字节/哈希、全部 18 交付文件最终哈希、完整 535 追踪文件最终哈希、JUnit/报告/diff/runner 证据哈希。此前 18 文件有 10 份未变化，原 535 追踪文件中不属 P5 的 528 份仍与 P4 基线一致；未暂存任何文件，HEAD/branch/dirty 白名单在 git-checks.json。最后被测源码对照在 final-tested-source-snapshot.json，任何不一致将使本报告无效。

Owner 待审批：本轮语义修正和有界满额拒绝策略；旧两项 DeepSeek 收据失败的独立任务；未来真实路由的 grant/容量/价格/账户/预算许可及有界实测。阈值/贡献仍初始规则，不声称实际降本达到 10%。**依赖升级影响未独立验证**；原 native-runtime/fixture/OCR/layering、4 Caption NOT RUN 等非本轮缺口保持原状态，不宣称全项目通过。

新增 commit：无。HEAD 保持基线，原 18 文件 dirty 保留，index 空；没有提交、push、部署、Tag、真实路由、账本清理或索引重建。立即行为回滚仍为 RAG_RULE_ROUTER_ENABLED=false；如 Owner 需要撤销本轮代码，可逐文件对照 before/ 和 r1.diff，不能全仓 reset 或覆盖之后的改动。

执行标记 MANUALLY_SUPERVISED_TRIAL；请求/实际服务模型身份无法从当前工具证明，记 UNKNOWN，没有以角色或模型自述充当路由证据。没有派 Worker。仅 R1 离线检查 CHECKS_PASSED；P5 整体仍 BLOCKED / NEEDS_OWNER_REVIEW。完成后停止，等待 Owner。


证据整理回执：首轮 Python stdin 清单收集器退出 1，条件表达式优先级错误把 simulated-storage 目录当文件读取，得到 PermissionError；这是报告工具错误，不是新 pytest/DACL 故障。错误记录在 evidence-builder-first-failure.json。随后仅收集明确命名的证据文件，未重读该目录、提权、改权限或重跑测试。no-index whitespace 检查退出 1 是 --no-index 隐含 --exit-code 的“文件有差异”标志，stdout 空表示无空白错误；默认 git diff --check 仍退出 0。


# P5 Owner Review R1.1 — CHECKS_PASSED / NEEDS_OWNER_REVIEW

## 1. 目标与改动

基线 HEAD `e6cca0971d0f1875ca562a6b953fd5ec252b28e0`、分支 `codex/local-first-rag-v1-20260930` 均正确。开始时 R1 的 18 交付文件和 27 项证据哈希全部匹配，原 dirty 保留。start-snapshot.json/before/ 保存 R1.1 开始字节，不 reset/stash/clean；新 attempt 独立创建，不覆盖 R1、旧失败或旧收据。

本轮只改变六份 P5 已交付文件：

1. backend/app/application/router_feature_extraction.py：YYYY/MM/DD（含分隔空格）与 YYYY/YYYY 年度身份在算术识别前屏蔽。年份对默认为时间身份；明确计算/公式/表达式/括号的局部算术框架且没有年度标记时仍可计算真实除法。既有减法日期、版本和编号规则保留。数字本身不改变 T/C/J/D/L/M 贡献；L 仍 UNKNOWN。
2. backend/app/application/quick_chain.py：模型成功返回后的本地候选校验内部异常改为固定 `CANDIDATE_VALIDATION_INTERNAL_ERROR`。generation_status=COMPLETED、finish_reason=stop、result_validation=VALIDATION_ERROR、provider_failure_reason=None；不输出异常消息。该分支直接返回空答案终态，不触发 Provider Fallback/拒答升级/质量升级，也不进入旧 finalize 将错误覆盖为引用错误或成功回退。正常 Validator、FinalAnswerCommitCheck、Provider 失败和降级分支均保留。
3. backend/tests/test_p5_router_features.py：评分采集使用可选 XML 路径；没有 XML 时使用 pytest tmp_path，不强制 JUnit。open('x') 排他写文件，已有历史 scores.json 明确拒绝覆盖。新增日期/年度等义与真实除法反例及输出路径测试。
4. backend/tests/test_p5_router_generation.py：新增 fallback 开关正反例、异常正文不泄露、UNKNOWN 保留、已有本地 audit 会保存时也不得 finalize 成功回退的反例。
5. docs/design/rule-router-v1.md 与 docs/audits/p5-implementation-report.md：追加本轮证据与生产阻断说明；历史内容字节前缀保留。

没有修改贡献值、0.35 阈值、model registry、P4 检索/Rerank/Parent/Context/Citation、Guard/Gate、原收据测试、数据库或账本。rule_router_v1.py 在本轮字节未变；数值贡献与策略常量在最终清单中核对。

## 2. 日期、范围与真实除法的实际对照

下表修复前取自开始快照中哈希匹配的原 extract_features，修复后由最终实现计算；新增参数化测试逐项断言 score/depth。词法规则仍有语义歧义边界：未显式说明的四位年份对不猜成除法；确需除法用明确计算或算术括号。日历形的 YYYY/MM/DD 作为时间身份，反向算术 `(2026/10)/10` 仍计层数。这不是通用语义分类器。

| 合成问题原句 | 修复前 | 修复后 |
|---|---:|---:|
| 比较2026/10/10与2026/10/11 | 0.18 | 0.09 |
| 比较2026-10-10与2026-10-11 | 0.09 | 0.09 |
| 比较2026 / 10 / 10与2026 / 10 / 11 | 0.18 | 0.09 |
| 比较2025/2026年度与2026/2027年度 | 0.12 | 0.09 |
| 比较年度范围2025/2026与2026/2027 | 0.12 | 0.09 |
| 计算2025/2026年度对应的成本 | 0.11 | 0.08 |
| 计算年度范围（2025/2026）的成本 | 0.11 | 0.08 |
| 计算2026/10/10当天的(440-200)/(1300-1000) | 0.17 | 0.17 |
| 计算12/3 | 0.11 | 0.11 |
| 计算(2026/10)/10 | 0.17 | 0.17 |
| 计算2025/2026 | 0.11 | 0.11 |
| 计算(2025 / 2026) | 0.11 | 0.11 |
| 计算(440-200)/(1300-1000) | 0.17 | 0.17 |

## 3. 命令与实际结果

所有测试使用现有 .venv Python 3.13.0、pytest 9.1.1。现有专用 offline_runner 原字节未修改；ordinary_pytest_guarded.py 复用其相同 offline 护栏并调用标准 pytest `__main__`（runpy），没有 --junitxml。明确区分：未执行无护栏的直接 python -m pytest；实际验证的是标准 pytest 命令行在相同 offline 护栏下、无 XML 参数运行。普通入口被测 helper/feature/rule 模块的字节至最终保持相同；后来只修了 Quick 的终态分支，因此此小验证按相关输入复用，没有冒称其整个源码快照就是最终版本。

| attempt | 实际结果 | pytest/进程退出码 |
|---|---|---:|
| owner-review-r11-before | 7 PASS、10 FAIL、0 ERROR、0 SKIP，117 deselected | 1 / 1 |
| ordinary-after（无 JUnit） | 1 PASS、0 FAIL | 0 / 0 |
| owner-review-r11-after | 168 PASS、0 FAIL | 0 / 0 |
| owner-review-r11-finalize-before | 0 PASS、2 FAIL；39 deselected | 1 / 1 |
| owner-review-r11-final-regression | **401 PASS、2 FAIL、0 ERROR、0 SKIP** | 1 / 1 |

最终含 **170 个 P5 用例全部 PASS**：原 R1 的 151 +本轮新增 19。所有原 node ID 保留；原 104 项未删改断言。首轮 17 新反例中 10 个失败已保存；新增 audit/finalize 的两项在 repair 前真实复现内部错误被清成 None 的成功 fallback，证据未覆盖。普通入口无 JUnit 真实通过；专用入口带 XML 和历史文件拒绝覆盖反例真实通过。

```powershell
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py owner-review-r11-before backend/tests/test_p5_router_features.py backend/tests/test_p5_router_generation.py -k owner_r11
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-owner-review-r1-1/ordinary_pytest_guarded.py ordinary-after backend/tests/test_p5_router_features.py::test_owner_r1_save_actual_scores
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py owner-review-r11-after backend/tests/test_p5_router_features.py backend/tests/test_p5_router_generation.py backend/tests/test_p5_router_boundaries.py backend/tests/test_p5_router_replay.py
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py owner-review-r11-finalize-before backend/tests/test_p5_router_generation.py -k owner_r11_validator_internal_error_cannot
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py owner-review-r11-final-regression backend/tests/test_p5_router_features.py backend/tests/test_p5_router_generation.py backend/tests/test_p5_router_boundaries.py backend/tests/test_p5_router_replay.py backend/tests/test_langchain_quick_chain.py backend/tests/test_quick_chain_budget.py backend/tests/test_answer_service.py backend/tests/test_answer_hardening.py backend/tests/test_bootstrap.py backend/tests/test_run_metrics.py backend/tests/test_final_answer_commit.py backend/tests/test_cancellation_boundaries.py backend/tests/test_product_receipt_repair.py backend/tests/test_deepseek_policy_boundary.py backend/tests/test_egress_matrix.py backend/tests/test_model_provider_contracts.py backend/tests/test_p4_rag_pipeline.py backend/tests/test_quick_evidence_flow.py backend/tests/test_quick_chain_quality.py backend/tests/test_quick_citation_prompt.py backend/tests/test_quick_answer_language_prompt.py backend/tests/test_privacy_configuration_answer.py backend/tests/test_answer_validation.py
git diff --check
```

pytest 完整 argv、basetemp/JUnit 绝对路径、耗时、guard、测试前后源码哈希在每轮 execution.json/source-before.json/source-after.json。实际进程回执在 process-exits.json。最后轮 30.562325 秒（入口总时长），pytest 输出 23.52 秒；无真实模型延迟。所有 guard 计数零、测试时源码前后相等；未提权、改 ACL、关闭保护或下载依赖。原 launcher 路径告警/SWIG deprecation 保留，不当作 PASS/FAIL 依据。**未实现的命令不得报告通过。**

两项 FAIL 仍为 test_product_receipt_repair.py::test_persistence_failure_preserves_unknown_budget_and_closes_scope[replace/readback] 第 100 行。同名、同一 JUnit failure message、同一 True is False 断言，拒绝重复后仍只模拟发送一次。regression-comparison.json 比对确认；原 Gate、测试和真实账本不修改，未 xfail/skip，不自动批准延期。本轮不将有关套件写成全绿。

## 4. 生产阻断与剩余问题

**PRODUCTION_ENABLE_BLOCKED：正式启用前，必须具备并核验可信持久化防重和可持续的内存生命周期方案，两者缺一不可。** 现有 RunEventStore/provider gate/持久收据是需要保留的基础；当前 SIMULATED 的跨 chain 证据不能代替完整生产接线核验。4096 run /8192 attempt 的有界、不淘汰方案继续保留；满额永久拒绝仅是 fail-closed 保护，不是可持续生产生命周期。本轮不重构、不自动清理/轮换，也不声称上述阻断已经关闭。

独立旧 DeepSeek Gate 两项失败仍待另行授权。真实路由仍 OFF/BLOCKED_REAL；还需单独批准精确 grant、输入容量、价格/账户/预算与有界真实验证。真实 API、真实 DB、全项目发布回归、业务索引重建均 NOT RUN。旧 4 Caption NOT RUN、native-runtime/fixture/OCR/layering 等遗留未改变；**依赖升级影响未独立验证**。规则未校准，不声称达到 10% 实际降本。

## 5. 版本、diff、哈希与停止

本轮六文件的 R1.1 精确 diff 在 r11.diff；final-manifest.json 列全部 18 交付文件及本轮前后字节/哈希、535 追踪文件哈希和关键证据哈希。最终被测源码匹配见 final-tested-source-snapshot.json；default git diff --check 退出 0，未追踪改动另做 no-index whitespace 检查（退出 1 为内容有差异，stdout 空表示没有空白错误）。R1 27 证据文件和旧轮次证据在结束时复核不变。

HEAD 仍为 `e6cca0971d0f1875ca562a6b953fd5ec252b28e0`，原 18 dirty、index 空，新增 commit 无。本轮状态仅 CHECKS_PASSED / NEEDS_OWNER_REVIEW；P5 未授 ACCEPTED/验收。没有真实 API/DB、提交、push、部署或 Tag。

回滚即时行为开关仍 RAG_RULE_ROUTER_ENABLED=false；撤销本轮必须逐文件对照 before/ 与 r11.diff，不覆盖 R1 或后续工作。不改账本、数据库或索引。执行为 MANUALLY_SUPERVISED_TRIAL；实际服务模型/effort 证明 UNKNOWN，不以模型自述当证据。完成后停止，待 Owner 复核。
