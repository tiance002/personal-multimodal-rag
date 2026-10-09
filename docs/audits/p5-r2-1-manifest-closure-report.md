# P5-R2.1 — Manifest 篡改拒绝后的安全闭锁修复

## 1. 目标与改动

任务身份 P5-R2.1 / rev1，执行结果 CHECKS_PASSED / NEEDS_OWNER_REVIEW。仅当前限定修复离线检查通过，不代表 P5 已验收、真实路由可启用或发布通过。**未实现的命令不得报告通过。**

HEAD `e6cca0971d0f1875ca562a6b953fd5ec252b28e0`、分支 `codex/local-first-rag-v1-20260930` 与交接一致。开始 21 个未提交文件已逐字节与 R2 final-manifest 核对，43 个 R2 证据哈希一致。适用 AGENTS.md、progress.md、ADR 索引/ADR-002 和当前 P5 设计已读取；旧 progress 的 main/M4.5 描述不是当前任务基线。

本轮明确写范围：`backend/app/application/reviewed_product_request.py`、追加 `backend/tests/test_reviewed_product_request.py`、新增 `docs/audits/p5-r2-1-manifest-closure-report.md`、证据 `var/reports/p5-r2-1-manifest-closure/`，现有离线 Runner 仅创建四个全新 p5-r21-* 目录。只读复用 Gate 父类、Session/Validation/Grant60、指定直接测试、P5 测试、既有版本证据与列举的 tracked 哈希 map。不读取真实 canonical ledger、Key、.env.local 或真实业务数据。

原 21 文件除获准继续修复的 Gate 外，其余 20 文件字节和哈希不变。Gate 的 R2 `_close_execution`、execution_scope、授权判断、用量计算和 grant 逻辑 AST 均不变。原 reviewed_product_request 测试完整字节作为最终文件前缀，所有旧断言保留，R2 原收据测试文件和旧报告不变。

## 2. 原失败路径与修复依据

实际复现 `test_reviewed_product_request.py:163::test_ledger_manifest_mutation_cannot_authorize`：0 PASS/1 FAIL，退出 1。模拟规范账本仍有 23 条历史、当前 Request ID 未消费；只改变持久 manifest 的 prompt_sha256 后重新启用全局授权。

旧路径：`ReviewedProductRequestGate.execution_scope` 无 Grant60 分支 → 父 scope 在规范锁内 `_read`/授权/`_used` → `_task_entry` 检查受信 policy、digest、batch hash → `_validate_manifest` 比对源配置派生的 manifest，抛 REVIEWED_MANIFEST_CHANGED → `_task_entry` 转为 REVIEWED_REGISTRY_CHANGED → owner 尚未安装 → 失败清理无匹配 owner，R2 已消费请求条件也不成立 → 请求拒绝但 global enable 仍 true，原第 167 行断言失败。

新增实际反例记录 manifest 校验发生时 enabled=false/owner=null，且 body 未进入；原失败先通过捕获阶段错误再在闭锁断言失败。静态全路径见 execution-path.json，实际观测见 observed-execution-path.json（SIMULATED）。

子类新增 `_task_entry` 只在该注册清单校验路径的精确 REVIEWED_REGISTRY_CHANGED 上调用独立 `_close_idle_manifest_rejection`。不会把所有 AttemptDenied 统一闭锁，也不删除或放宽 R2 consumed-ID 条件。

闭锁前重新确认：规范路径和源内不可变配置 (`_frozen`)、v1 provider/schema/计数/历史完整性、现有授权上限合同、validation policy/历史费用和 Token 合同；scope/policy/digest/完整 batch 集合及 sha 必须与受信配置一致。仅持久 manifest 不匹配（含缺失/null）且当前 Request ID 未消费、global enable=true、reserved_attempts=0 时继续。其他 reviewed/static scope 的 owner 状态必须可解析并无 owner/启用，Grant60 账本不进入该 legacy 分支。未知 owner 结构、外来路径/策略/provider/binding 都拒绝，且不更新账本。

校验和闭锁均在既有调用方规范账本锁内完成，不另启锁外清理，不信任/修补被篡改 manifest，只持久化 global enable=false；其他授权、attempts、UNKNOWN、Token hold、累计限额和收据不变。

闭锁写入/replace 失败返回固定 `PRODUCT_MANIFEST_CLOSURE_UNAVAILABLE`，不泄露异常正文，不声称磁盘开关已关闭，不重试或退款。现有 fsync/atomic replace 写入机制继续复用，未新增授予权限的入口。

## 3. 执行命令及实际结果

Python 3.13.0 / pytest 9.1.1。全部 SIMULATED/OFFLINE，正常受保护 Runner，真实 API=0、真实 DB=0，四轮网络/Secret/子进程/DB 护栏触发均为 0。未提权、关闭保护、改变 ACL 或绕过拒绝。

```powershell
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py p5-r21-before backend/tests/test_reviewed_product_request.py::test_ledger_manifest_mutation_cannot_authorize
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py p5-r21-counterexamples-before backend/tests/test_reviewed_product_request.py -k 'test_r21'
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py p5-r21-target-after backend/tests/test_reviewed_product_request.py::test_ledger_manifest_mutation_cannot_authorize backend/tests/test_reviewed_product_request.py -k 'test_r21 or test_ledger_manifest_mutation_cannot_authorize'
.venv/Scripts/python.exe -B -X utf8 var/reports/p5-rule-router-r1/offline_runner.py p5-r21-final-regression backend/tests/test_product_receipt_repair.py backend/tests/test_reviewed_product_request.py backend/tests/test_product_wire_anchor.py backend/tests/test_reviewed_immutable_batch.py backend/tests/test_grant60.py backend/tests/test_deepseek_policy_boundary.py backend/tests/test_pretransport_budget.py backend/tests/test_validation_usd_budget.py backend/tests/test_p5_router_features.py backend/tests/test_p5_router_generation.py backend/tests/test_p5_router_boundaries.py backend/tests/test_p5_router_replay.py
```

| 本轮执行 | 收集/执行 | PASS / FAIL / ERROR / SKIP | 进程及 pytest 退出码 |
| --- | ---: | --- | ---: |
| p5-r21-before | 1 | 0 / 1 / 0 / 0 | 1 |
| p5-r21-counterexamples-before | 19 | 12 / 7 / 0 / 0 | 1 |
| p5-r21-target-after | 20 | 20 / 0 / 0 / 0 | 0 |
| p5-r21-final-regression | 422 | 422 / 0 / 0 / 0 | 0 |

前后源码哈希每轮完全一致；argv、elapsed_seconds、JUnit 和守卫计数均由入口保存于 `var/reports/p5-rule-router-r1/<轮次>/`。初版新增测试 12 PASS/7 FAIL 保留，未修改断言以消除失败。修复后原用例加新增 19 项共 20 PASS。

最终唯一一轮综合回归 422 PASS/0 FAIL/0 ERROR/0 SKIP，退出 0：Gate/R2/相关预算 252 项（含本轮 19 项），P5 四模块 170 项。与 R2 最终真实执行的共同 233 项逐项比较，原 232 PASS 均保持 PASS，唯一 registry-mutation FAIL 恢复 PASS。R2 replace/readback 两项及原收据全组 29 项保持 PASS。

P4 原始基线仅复用此前固定六模块 overlay 的 16 PASS/2 FAIL 历史证据，不把当前 dirty 源码称为 P4 checkout 或重跑完整 P4。本轮没有重跑 R2 那次过宽关联基线的网络/native/Secret/子进程受阻路径，标 NOT RUN，原 FAIL/ERROR 和报告保留，未冒充全项目通过。

## 4. 新增 19 项反例

- 精确观测 manifest 校验在 owner 获取之前，拒绝后仅 global enable 变化（1）。
- 未消费 prompt/hash 篡改、manifest 缺失/null/外来 scope；旧 UNKNOWN 历史和占用完整保留、模拟发送 0（4）。
- Gate write 与真实原子 replace 的模拟写失败：固定错误码，原文件字节不变、无残留自有临时文件（2）。
- 合法其他 scope 无预占/有预占持有 owner，4 线程/8 次篡改拒绝不改其账本/owner，正常退出后闭锁（2）。
- static owner、尚存 owner token、没有 owner 但仍有未结预占，不执行闲置闭锁（3）。
- policy digest、batch sha、ledger policy、provider、外来 ledger 路径及 owner 结构未知，不进行本修复的写入（6）。
- 其他 scope 的坏 manifest 拒绝，不能关闭当前合法 owner（1）。

R2 已消费 Request ID 的重复进入、并发所有权、UNKNOWN 保留、收据失败和激活失败反例全组继续实际执行通过；无增加预占/外发/计费。未重建 ledger 或把未知费用归零。

## 5. Diff、哈希、版本与遗留

| 文件 | 本轮开始 SHA-256 | 最终 SHA-256 |
| --- | --- | --- |
| reviewed_product_request.py | `0fd28ba749df1e6e8f5725af92431d07814daca20d771638d0efb5bf983d5940` | `9e26cb2f1b33b8223babeb7d4c955c35a7bcd8ef5942a25152902994f66b77db` |
| test_reviewed_product_request.py | `e7e565316af9641b220a5295bcbf3623ac4d31c3027a1b4efc878426ffa9c66e` | `6e91b196c070c62faaa6dfcc51c54ac3e8ce478e037dd1f28bbdb081508ab3ce` |

`r21.diff` 相对本轮 start-snapshot（已经包含 R2 修复），仅本轮两份代码/测试及新增报告；`source-only.diff` 为两份源码差异。不会把已有 R2/P5 改动误算成本次新增。完整 23 dirty 文件/长度/哈希、测试证据/旧 manifest 哈希、原 tracked map 对照见 final-manifest.json；Git 命令/退出码见 git-checks.json。

HEAD 不变、暂存为空，无新 commit。当前 dirty 为原 21 文件加本轮原先干净的 reviewed_product_request 测试文件和新增本报告，共 23 路径。默认 `git diff --check` 退出 0，新增报告无行尾空白。

R3 可信持久化幂等和长期内存生命周期保持独立生产阻断，未删除 `_router_runs/_router_attempts`、改容量或改路由分数/0.35 阈值。P5 真实角色授权/容量保护仍 BLOCKED_REAL；本轮 PASS 只证明限定离线闭锁合同，不授权真实路由。真实 API/DB、提交/push/部署/Tag 均 NOT RUN。旧报告、首次失败、R2 UNKNOWN 模拟证据保留；真实 canonical ledger 完全未触碰。

单执行者，无 Worker；实际服务模型/effort 无可观察证明，UNKNOWN。控制面未证明完整自动化，记 MANUALLY_SUPERVISED_TRIAL。本轮初版失败后一次实现修复；没有重置修复轮数。

停止供 Owner 复核。回滚只逆向应用 r21.diff 中的本轮两份源码改动并单独处理本轮报告；不得 reset/stash/clean 全树、清理旧证据或真实账本。
