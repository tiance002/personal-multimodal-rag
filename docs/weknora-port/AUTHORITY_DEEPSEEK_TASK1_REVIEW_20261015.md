# 任务一专项审查：标准 API 与独立 worker 共用 Token 授权装配

- 审查者：本轮执行者；审查对象为**未提交**实现（基线 HEAD `551c5f05`，149 个文件已在索引）。
- 结论：**PASS（离线／合成／closed MockTransport 范围）**；真实模型与浏览器验收 **NOT RUN**。
- 提交 SHA：见交付回复（本报告随该提交一并落盘）。

## 1. 独立复现的命令、退出码、证据

所有命令 cwd = 主工作树；`PYTHONPATH=<worktree>;E:\WK\deps\reader;<worktree>\var\weknora-port\runtime-deps`。

| # | 命令 | 退出码 | 结果 | 证据 |
|---|---|---|---|---|
| 1 | `pytest --noconftest tests/weknora_port/test_authority_usage.py tests/weknora_port/test_send_gate.py` | **0** | 40 passed | `evidence/AUTHORITY_DEEPSEEK_REVIEW_20261015/offline-unit.{exitcode.txt,stdout.txt,junit.xml}` |
| 2 | `pytest --noconftest tests/weknora_port/test_token_authority_pg.py`（隔离 PG 56423） | **0** | 7 passed | `…/token-authority-pg.{exitcode.txt,stdout.txt,junit.xml,pg.token-cases.jsonl}` |
| 3 | `pytest --noconftest tests/weknora_port/test_authority_cli_pg.py`（隔离 PG+Redis） | **0** | 1 passed (23.23s) | `…/authority-cli.{exitcode.txt,stdout.txt,junit.xml,pg.json}` |

隔离环境身份已重新核对：`eviroute-rag-postgres 127.0.0.1:56423`、`eviroute-rag-redis 127.0.0.1:56424`、Redis `run_id=de03f13f2ffba1146b01434f25514c4478e046fd`。

**调用约定（重要，否则假失败）**：实现要求 authority 配置路径为绝对 D/E 盘。pytest 的 `tmp_path` 默认在 C 盘会触发 `AUTHORITY_ABSOLUTE_DE_PATH_REQUIRED`。必须把 `TMP`/`TEMP` 与 `--basetemp` 路由到 E 盘（同 `run-check.py`）。未路由时本审查实测 `1 failed / 39 passed`（expects `DUPLICATE`，实为路径错误），属**调用方式**问题，非代码缺陷。

## 2. 七项不变量逐条

| # | 不变量 | 结论 | 证据（file:line / 测试） |
|---|---|---|---|
| 1 | 配置可信、不可被请求覆盖；来源/KB 范围/外发许可有效 | PASS | `authority.py` load/validate + `ServerPermissionResolver`；`send_gate._permission`；`test_send_gate.py::test_reserved_unknown_sum_carry_and_origin_scope_does_not_expand_retrieval` |
| 2 | 月限 1,000,000；input/output/cache/total 按供应商语义；reported/derived 分开；缺失 UNKNOWN 不补零、缓存不重复 | PASS | `authority.py::load_authority_settings`(limit_tokens)、`models/usage_facts.py` `accounting()`；`test_authority_usage.py::test_actual_missing_total_and_cache_not_reported_as_zero_or_inferred_actual`、`::test_reported_total_known_lower_and_cache_never_double_count` |
| 3 | 预留与实际分列；UNKNOWN 占用 ≥ max(预留,已知下界)；超界/占用/阻断同事务 | PASS | `token_budget.py:117-137`；`test_token_authority_pg.py::test_unknown_overrun_holds_known_lower_and_block_commits_atomically`、`test_send_gate.py::test_unknown_cannot_later_be_released_and_overrun_commits_unknown_then_blocks_month` |
| 4 | 跨单位/跨账本身份防重；统一锁序；发送前预留已持久提交 | PASS | `token_budget.py:45-86`（call锁→月advisory→row；`_deny_existing`；`# commit 后才返回`）`send_gate.py:90-125`；`test_token_authority_pg.py::test_global_claim_released_and_changed_model_role_cannot_resend_or_switch_unit`、`::test_cross_unit_concurrent_claim_has_only_one_winner` |
| 5 | 槽不随模型变化；恢复只做一致性核验 | PASS | `token_budget.py:77-79`（`_slot_id` 固定）、`fingerprint()`；`test_authority_usage.py::test_model_role_changes_do_not_create_new_recovery_slot` |
| 6 | 原始 usage 从解析到账本；流式快照不逐帧累加 | PASS | `models/transport.py:161-180`（保留 prior numeric raw / 观测下界）、`usage_facts.capture_usage`；`test_authority_usage.py::test_later_usage_snapshot_cannot_erase_earlier_known_lower_or_add_cache_twice` |
| 7 | 不清 UNKNOWN；不因表空填零 carry-in；条件不足保留阻断 | PASS | `token_budget.py:66-73`（carry 来自受信配置、`TOKEN_MONTH_BLOCKED`）、`authority.py` 拒绝 approved；`test_send_gate.py::test_default_closed_and_unverified_never_reserve`、`test_authority_usage.py::test_config_default_denied_and_approved_without_reliable_meter_cannot_enable` |

## 3. 设计评审 3 项“必须修正”的落地核对

1. **占用公式**：`token_budget.py:117-133` `lower=max(lower, known_lower_bound)`；`held=max(reserved, lower)`；超界时 `state=unknown, held=max(reserved,lower)` 并同事务写 `blocked_reason='USAGE_EXCEEDS_RESERVATION'`，异常在事务**提交后**抛（L136-137）。满足"reserved=100/actual=150 → 断言 held=150"的要求。
2. **跨表全局 claim + 锁序**：`_deny_existing` 在月锁**前**执行，覆盖含 `released` 在内的历史状态；锁序 `call → canonical(account,provider,month) advisory → row` 在 reserve 与 finish **一致**；月度 SUM 在月锁**之后**、`READ COMMITTED` 下取锁后新快照（L46/54/56/74）。预算单位由 `port_call_slots.unit='tokens'` 固定。
3. **logical slot / 模型快照**：槽键为持久 logical slot，`provider/model/wire` 为首次 claim 固定后仅比较的指纹；`test_model_role_changes_do_not_create_new_recovery_slot` 覆盖"模型 A 的 UNKNOWN 不得变成模型 B 的新请求"。

## 4. 六类定向反例覆盖

**均已存在**：并发（`test_two_connections_cannot_oversubscribe_month_and_outer_transaction_denies`、`test_cross_unit_concurrent_claim_has_only_one_winner`）、取消（`test_cancel_consent_and_missing_usage_preserve_admission_boundaries`、`test_consent_revocation_while_waiting_for_month_lock_is_rechecked`）、截止时间（`test_component_bound_breach_and_expired_deadline_remain_conservative`）、重复请求（`test_global_claim_released_and_changed_model_role_cannot_resend_or_switch_unit`、`test_send_gate.py::test_call_id_reuse_never_reopens_ledger_and_upgrade_rechecks_permissions`、`::test_finish_identity_and_terminal_replay_are_immutable`）、恢复（`test_model_role_changes_do_not_create_new_recovery_slot`）、UNKNOWN（`test_unknown_overrun_holds_known_lower_and_block_commits_atomically`、`test_send_gate.py::test_unknown_cannot_later_be_released…`）。

## 5. “正常入口非测试专用装配”证据

`test_authority_cli_pg.py` 用 `subprocess.Popen` 起**两个真实进程**：
`python -B -m backend.weknora_port.entrypoint --port … --authority-config …` 与 `python -B -m backend.weknora_port.worker --once --authority-config …`。
合成配置 `mode=synthetic`、closed MockTransport、`real_provider_calls=0`；断言重放不新增 claim、重启后 claim 数不变、默认 disabled 时 ready 503。满足"标准 API 接受上传/问答、独立 worker 消费任务并更新状态"。

## 6. 未完成 / 阻断（**不得当作已实现**）

- `approved` 模式仍抛 `APPROVED_METER_UNIMPLEMENTED`；真实生产计量 recipe、逐来源 SourceFacts 未实现。
- 真实 carry-in / 旧 UNKNOWN 可靠上界 / 同账户其他消费覆盖**未核实** → 真实启用保持拒绝。
- 真实供应商调用 **NOT RUN**；浏览器验收本轮 **NOT RUN**；`token_migrate` 旧金额表迁移未跑。
- 本审查全部为 synthetic/MockTransport，**不代表真实模型通过**。

## 7. 被测源码哈希

26 个 backend/tests 文件的 SHA256 与 git blob 哈希见 `evidence/AUTHORITY_DEEPSEEK_REVIEW_20261015/source-sha256.txt`。
