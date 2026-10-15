# Tasks — 输出上界 Recipe 修复（对应 spec.md）— 全部完成

约定：工作树 `E:\RAG-Worktrees\weknora-python-port-20261013`；`git` 用绝对路径 `C:\Program Files\Git\cmd\git.exe`；Python `E:\RAG quention\.venv\Scripts\python.exe`（`-B`）；`PYTHONPATH=<worktree>;E:\WK\deps\reader;<worktree>\var\weknora-port\runtime-deps`；`TMP`/`TEMP`/`--basetemp` 路由到 E 盘；approved PG 测试需 `WEKNORA_PORT_TEST_REDIS_RUN_ID`。

## [x] T0 基线（已完成）
- P0 只读根因定位（根因=合同外 cap 字段 `max_completion_tokens` 被当作输出预留上界）。
- 记录 `git status`/分支/HEAD，保护 `.serena/project.yml` 与 6 个受保护未跟踪文件。

## [x] T1 新增 `models/output_contract.py`（P1/T3）
- `CAP_CONTRACTS`（deepseek/siliconflow → `max_tokens`,`full-completion/v1`）、`registry_cap_field`、`OutputBudgetContract`、`resolve_output_contract`。
- 覆盖 case A/B/D + 拒绝码：`MODEL_OUTPUT_CONTRACT_INVALID`、`MODEL_OUTPUT_CONTRACT_UNKNOWN`、`MODEL_OUTPUT_CAP_FIELD_DRIFT`、`MODEL_OUTPUT_HARD_LIMIT_DENIED`。

## [x] T2 `source_facts.py`（P1/T4）
- `METER_RECIPES` 增 `provider-completion-cap/v2`；`BoundCallFacts` 增 `output_contract`（默认 None）。
- `bind_wire_facts` 增 `capability` 并走合同；保留来源/wire 校验；保留 v1 合规数值。

## [x] T3 `authority.py` + `storage/token_budget.py`（P1/T5, P3/T8）
- `ApprovedTokenAuthority.reserve_bound` 去等量耦合，改按合同核验；`monthly` 去 `config_sha`；`same_month_authority` 前缀兼容（只读）。

## [x] T4 `models/usage_facts.py`（P2/T6）
- 增 `reasoning` 采集路径；`accounting` 校验 `0 ≤ reasoning ≤ completion`；不重复计入。

## [x] T5 `models/transport.py`（P2/T7）
- `finally`：先有界 `gate.finish`，后 `aclose`；吞掉 aclose 失败；保持 `sent` 事实与 CancelledError；ContextVar best-effort reset；`provider.chat_stream` 已上报时结算/输出同一 usage 对象。

## [x] T6 `development.py`（装配）
- approved `bind_facts` lambda 传 `capability=capability`。

## [x] T7 P4 审核（T10/T11/T12）
- T10：输入上界适用域逐项核验；托管图片/视频/音频 → `MODEL_INPUT_BOUND_NOT_APPLICABLE` fail-closed（无 VLM 常数）。
- T11：来源证明/Scope 区分；rerank query 最小修复（新增 `question` 切片）；架构级来源缺陷单列。
- T12：升级前提 `error is None`；允许的 `upgrade_reason` 有界；无通用重试；升级独立身份共用账户（审计无代码改动）。

## [x] T8 测试（P5 A–E）
- 新 `tests/weknora_port/test_output_budget_contract.py`：18 passed。
- 隔离 PG/Redis runner `output-bound-repair-20261015`：85 passed（junit 0 failures）。
- 全目录回归：1422 passed / 66 skipped / 1 failed（环境 `OCR_HTML_SOURCE_CONVERTER_REQUIRED`）。
- 复核 2 个既有失败（HEAD 复现）已随 P2/T7 修复转绿。

## [x] T9 报告（P6）
- `docs/weknora-port/OUTPUT_BOUND_RECIPE_REPAIR_20261015.md`（9 节 + 最终结论 `READY_FOR_AUTHORIZED_REAL_SMOKE`）。
- 独立自审 10 条清单见报告第 5 节。

## [x] T10 交付
- 仅提交本项已审查文件（`git add` 逐文件，禁 `-A`）；保护 `.serena/project.yml` 与 6 个受保护未跟踪文件；不 Push。

## 验收标准
- [x] 新合同对 `max_completion_tokens`（deepseek）组合 fail-closed；合规 `max_tokens` 数值不变。
- [x] 授权层接受 case B 分离上界，拒绝漂移/未知合同。
- [x] reasoning 采集正确且不重复计入；结算与关闭顺序不再互相跳过。
- [x] 旧月阻断与 UNKNOWN 未被删除/重置；未发真实模型请求；报告结论与实际一致。
