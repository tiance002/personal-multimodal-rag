# P5-R2 — DeepSeek 旧 Gate 收据状态合同最小修复

## 1. 目标、基线与范围

任务身份 P5-R2 / rev1 / initial + 一次基于证据的修正；状态为限定修复 CHECKS_PASSED / NEEDS_OWNER_REVIEW，关联回归整体仍 FAIL。不是 P5 验收，不启用真实路由。

HEAD `e6cca0971d0f1875ca562a6b953fd5ec252b28e0`，分支 `codex/local-first-rag-v1-20260930`。开始已有 18 个 P5 未提交文件，全部最终字节/长度/SHA-256 与 start-snapshot.json 和 R1.1 manifest 一致；它们本轮未修改。原始 Gate、父 Gate、DeepSeek adapter 和收据测试源码与指定 P4 HEAD 内容一致（换行归一比较），当前其他 P5 dirty 内容不是 P4 checkout。

仅新增本报告，修改 `backend/app/application/reviewed_product_request.py` 和 `backend/tests/test_product_receipt_repair.py`。不修改 RuleRouter 特征、贡献分、0.35 阈值、P4 检索/引用、Grant60、DeepSeek adapter、账本实现或旧测试断言。原 6,512 字节测试源码是最终测试文件的精确字节前缀。

读取适用 AGENTS.md、progress.md、ADR 索引和 P5 设计；旧里程碑文字与当前任务不一致时，以 Owner 当前限定授权为准。使用现有受保护离线入口。无新增依赖。单执行者；控制面未证明全自动，记 MANUALLY_SUPERVISED_TRIAL。实际服务模型/effort 无可观察证明，UNKNOWN；未换模型或分派 Worker。

## 2. 根因与最小修复

`reviewed_product_request.py:168 execution_scope` 的旧无 Grant60 分支委托 `ReviewedImmutableBatchGate.execution_scope`。父类 `reviewed_immutable_batch.py:302` 在账本锁内检查不确定尝试/已消费请求，随后才安装 execution_owner；进入失败时调用 `_close_execution`。父类 `:275` 只关闭匹配 owner，未取得 owner 的重复进入因此不改变重新启用的 `calls_allowed_in_this_task=true`。真实发送仍被拒绝，问题是闭锁状态合同没有完成。

子类 `reviewed_product_request.py:210 _close_execution` 继续先执行已有所有权清理；只在本 scope 的精确 Request ID 已存在、global enable=true、reserved_attempts=0 且 `_has_owner(data)=false` 时关闭 global enable。使用已有 `reviewed_grant60.py:140 _has_owner` 检查 reviewed/static/Grant60 owner 和启用状态。检查和持久化均在原有规范账本锁内完成。

不关闭合法并发 owner，不修改任何 attempts、历史费用/Token、预占、UNKNOWN、收据或额度。无新授权、无 retry、无额外 reserve。未消费请求的预算拒绝仍保持原先账本字节不变。正常所有权退出仍使用父类逻辑；Grant60 分支不变。

关闭持久化失败仍按既有 `session_attempts.py:22 _locked` 的固定错误码 `SESSION_LEDGER_UNAVAILABLE` 拒绝，不回显异常正文。写入失败不能声称磁盘开关已关闭；测试确认旧 UNKNOWN/收据/占用保留且无再次发送，不自动清理/重试。

## 3. 实际执行命令与结果

环境 Python 3.13.0 / pytest 9.1.1；所有本轮执行为 SIMULATED/OFFLINE，真实 API=0、真实 DB=0。**未实现的命令不得报告通过。**

```powershell
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py p5-r2-before backend/tests/test_product_receipt_repair.py -k test_persistence_failure_preserves_unknown_budget_and_closes_scope
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py p5-r2-direct-baseline backend/tests/test_product_receipt_repair.py backend/tests/test_reviewed_product_request.py backend/tests/test_product_wire_anchor.py backend/tests/test_product_gateway.py backend/tests/test_product_gap_contract.py backend/tests/test_reviewed_immutable_batch.py backend/tests/test_grant60.py backend/tests/test_deepseek_safety.py backend/tests/test_deepseek_policy_boundary.py backend/tests/test_pretransport_budget.py backend/tests/test_validation_usd_budget.py
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py p5-r2-counterexamples-before backend/tests/test_product_receipt_repair.py -k test_r2
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py p5-r2-target-after backend/tests/test_product_receipt_repair.py -k test_persistence_failure_preserves_unknown_budget_and_closes_scope
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py p5-r2-final-core backend/tests/test_product_receipt_repair.py backend/tests/test_reviewed_product_request.py backend/tests/test_product_wire_anchor.py backend/tests/test_reviewed_immutable_batch.py backend/tests/test_grant60.py backend/tests/test_deepseek_policy_boundary.py backend/tests/test_pretransport_budget.py backend/tests/test_validation_usd_budget.py
```

| 证据轮次 | 收集 | PASS / FAIL / ERROR / SKIP | 进程及 pytest 退出码 |
| --- | ---: | --- | ---: |
| baseline-product-receipt | 18 | 16 / 2 / 0 / 0 | 1 |
| p5-r2-before | 2 | 0 / 2 / 0 / 0 | 1 |
| p5-r2-direct-baseline | 297 | 240 / 37 / 20 / 0 | 1 |
| p5-r2-counterexamples-before | 11 | 5 / 6 / 0 / 0 | 1 |
| p5-r2-target-after | 2 | 2 / 0 / 0 / 0 | 0 |
| p5-r2-final-core | 233 | 232 / 1 / 0 / 0 | 1 |

baseline-product-receipt 是此前真实执行的历史证据，本轮只核对源码/manifest，不重跑；使用已固定六份 P4 模块 overlay，不能描述为当前整个工作树通过 P4。各轮 argv、退出码、耗时、护栏计数、JUnit 和前后源码哈希保留于 `var/reports/p5-rule-router-r1/<轮次>/`。

先复现指定 replace/readback 两项（0 PASS/2 FAIL），再做修复前关联基线。初次关联范围选得过宽：297 项 240 PASS/37 FAIL/20 ERROR，包含 network、native 解析、模拟 .env 和子进程入口。护栏拒绝网络 22 次、Secret 文件 2 次、子进程 1 次，DB 0，真实外发 0；没有绕过拒绝、修改 ACL、换路径重试或修环境。失败/错误全部保留。

修复后收窄到八个直接模块：收据、Product Gate、wire anchor、immutable batch、Grant60、DeepSeek policy、pretransport budget、USD budget。与上述基线共同的 222 项从 214 PASS/8 FAIL 到 221 PASS/1 FAIL；同根因 7 项恢复（指定 2 项加已有 timeout/truncated/invalid_usage/overrun/sink_failure 5 项）。新增 11 项全部 PASS，最终共 232 PASS/1 FAIL/0 ERROR/0 SKIP，退出 1，无新增失败。

修复后指定两项另行先跑，2 PASS/27 deselected，退出 0。最终收据文件全组 29 PASS，含原 18 项及新增 11 项。

新增首轮 5 PASS/6 FAIL：四项暴露闲置已消费请求闭锁问题；另外两项的初版断言错误期待裸 OSError，现有锁本来会转换为固定 AttemptDenied。仅修正本轮新增断言为精确 `SESSION_LEDGER_UNAVAILABLE`；旧断言不变。初版完整源码按首轮快照哈希重建并验证，存于 `counterexamples-initial-test_product_receipt_repair.py`，首轮失败和 JUnit 保留。

## 4. 新增反例与合同证据

`test_product_receipt_repair.py:140–277` 共 11 个参数展开用例：

- 成功/UNKNOWN/截断后的已消费 ID 再启用：仅闭 global enable，旧 23 条历史、计数、Token hold 和收据字节均不变，模拟发送仍仅一次（3）。
- 成功/UNKNOWN 时另一激活 owner 下 4 线程/8 次重复进入：账本字节和 owner 不变（2）。
- 独立合法 scope 持有 owner 和预占时，旧已消费 scope 的并发拒绝不影响该作用域（1）。
- 闲置重复 ID 的并发进入全部拒绝，无额外 reserve/send（1）。
- 激活写失败，已获取的 owner 被关闭，无消费/预占增长（1）。
- body RuntimeError/KeyboardInterrupt 前无 reserve，退出闭锁不增加用量（2）。
- 重复进入的闭锁写失败：固定拒绝，旧 UNKNOWN 和收据不变，无再发送（1）。

其余原测试继续检查收据 replace/readback、外来绑定拒绝、输入/Scope 绑定、计费、授权、预算拒绝、独立 owner、Grant60 及无自动重试。全部被测 Python 源码运行前后一致，最终再次逐文件核对 final source snapshot。

## 5. 遗留与阻断

最终唯一 FAIL：`test_reviewed_product_request.py:163::test_ledger_manifest_mutation_cannot_authorize`。修复前同样 FAIL；它在未消费请求的 registry 篡改拒绝后仍观察到 global enable=true，属于另一进入前闭锁边界。该场景确实拒绝授权/发送，但测试要求的闭锁未满足；不能标 PASS 或自行豁免。按“仅处理两项历史失败”限定范围本轮未修改它，建议 Owner 独立授权针对性修复/复核。

宽基线的 `test_product_gateway.py` 网络护栏、`test_product_gap_contract.py` native 配置/fixture、`test_deepseek_safety.py` 模拟 Secret/子进程路径修复后 NOT RUN；不是 PASS、SKIP 或被认定已修复。详细失败名、原退出码、前后源码哈希保留，不把环境/护栏失败冒充产品回归通过。

R3 的可信持久化防重与可持续内存生命周期仍是生产启用阻断；未删除 `_router_runs/_router_attempts`、提高 4096/8192 容量或修改现有幂等方案。P5 的最终验收、真实启用和范围外修复须 Owner 审批。真实 API/DB、push、部署、Tag、提交均 NOT RUN。本次没有读取、重置或清理真实旧账本。

## 6. 哈希、Diff 与版本

| 文件 | 修复前 SHA-256 | 最终 SHA-256 |
| --- | --- | --- |
| reviewed_product_request.py | `b9855054bad2b9f0610c49b01c57857e248d2fd20b40a86c9031b7e9e24b5ed2` | `0fd28ba749df1e6e8f5725af92431d07814daca20d771638d0efb5bf983d5940` |
| test_product_receipt_repair.py | `59d91f316c1c8009fd6662adda714757d68d9cc6eda0ffaaf94167c286c6536b` | `22807d1a9a4265b0c611f05d4c2a73bc9a57b7240e9888eb8311ec30826b30d4` |

完整最终哈希/长度、18 文件保全、原 535 tracked map 的范围核对、旧证据哈希、最终 Git 状态见 `final-manifest.json`。精确本轮 diff 为 `r2.diff`，包含两份源码和本报告，不混入原有 P5 dirty；默认 `git diff --check` 实际结果及 stderr 见 `git-checks.json`。其他 tracked 文件应与原 map 一致，若存在漂移，closeout 会失败。

最终 HEAD 保持 `e6cca0971d0f1875ca562a6b953fd5ec252b28e0`，无新 commit、暂存为空，dirty 为原 18 文件加本轮 3 文件。不进入后续阶段，停止供 Owner 复核。回滚仅逆向应用本轮两份源码 diff 和另行处理新增报告，不能 reset 全树或触碰旧 ledger/receipt。
