# 生产接线与计量（approved 模式）实现专项审查 — 2026-10-15

- 范围：补齐原任务缺失的 approved（生产）Token 接线与计量代码，**fail-closed**，**零真实请求**。
- 基线 HEAD：`5720ca0`。
- 结论：**契约层实现 PASS（隔离 PG/Redis + 闭合传输，`real_provider_calls=0`）**。approved 现在**不再是无条件 `APPROVED_METER_UNIMPLEMENTED`**，而是按已验证输入逐项校验；缺任一项即拒绝。**真实供应商调用仍 NOT RUN**（未配置真实凭据/额度，且本交接不发起真实请求）。

## 1. 改动文件与符号

| 文件 | 新增/修改 | 作用 |
|---|---|---|
| `backend/weknora_port/source_facts.py` | `METER_RECIPES`；`measure_final_input(recipe, wire)`；`bind_wire_facts(config, context, body, wire, *, config_sha, recipe)` | 真实计量 recipe（`wire-bytes-upper-bound/v1`，字节级 BPE 的严格上界，**不得**当作供应商 actual）；approved 事实必须来自服务器解析的真实 `CallContext.source_facts`，缺失/不匹配拒绝 |
| `backend/weknora_port/authority.py` | `MeterAsset`；`AuthoritySettings.provider/meter`；`_load_approved`；`ApprovedTokenAuthority`；`ServerPermissionResolver(tokens=)` | approved 配置解析（真实身份/已核验计量资产/LEDGER carry-in/已知 provider 上限）；approved 月度映射与上界校验 |
| `backend/weknora_port/development.py` | `assemble` approved 分支 | 构造 `ApprovedTokenAuthority` + `ServerPermissionResolver`，并给出**真实** transport（真实 endpoint + 交付密钥）+ `bind_wire_facts`；模型须在 `allowed_models` 内 |
| `backend/weknora_port/entrypoint.py` | `/readyz` 识别 `ApprovedTokenAuthority` | approved 有效时方可 configured，否则 503 |
| `tests/weknora_port/test_authority_approved.py` | 新增 15 例 | 配置 fail-closed、计量上界、来源事实、approved 权威 |
| `tests/weknora_port/test_authority_approved_pg.py` | 新增 1 例 | approved 装配接线（隔离 PG/Redis，闭合传输） |

## 2. 命令、退出码与证据

cwd=工作树；`PYTHONPATH=<worktree>;E:\WK\deps\reader;runtime-deps`；`TMP/TEMP` 路由到 E 盘；证据根 `docs/weknora-port/evidence/AUTHORITY_APPROVED_20261015/`。

| # | 命令（label） | 退出码 | 结果 | 证据 |
|---|---|---|---|---|
| 1 | `run-accept.py task5-approved-unit2 --pg-env tests/weknora_port/test_authority_approved.py` | **0** | 15 passed | `task5-approved-unit2.{command.json,exitcode.txt,stdout.txt,stderr.txt,junit.xml}` |
| 2 | `run-accept.py task5-approved-wiring --pg-env tests/weknora_port/test_authority_approved_pg.py` | **0** | 1 passed | `task5-approved-wiring.*` |
| 3 | `run-accept.py task5-regression --pg-env tests/weknora_port/test_authority_usage.py tests/weknora_port/test_send_gate.py tests/weknora_port/test_token_authority_pg.py tests/weknora_port/test_authority_cli_pg.py` | **0** | 48 passed | `task5-regression.*`（synthetic 路径未回归） |

首次 `task5-approved-unit` 为 RED（2 个测试自身缺陷：helper 参数名冲突、`bind_wire_facts` 传入 `None` 配置），已修复；原始 RED 证据保留。

## 3. approved 现在校验什么（fail-closed）

`_load_approved` 逐项拒绝并报明确错误码：
- 合成身份（`synthetic-` 前缀）→ `APPROVED_SYNTHETIC_IDENTITY_REJECTED`；合成 approval_source → `APPROVED_APPROVAL_SOURCE_REJECTED`；
- carry-in 非 LEDGER 来源 → `APPROVED_CARRY_SOURCE_UNTRUSTED`；`carry_status=UNKNOWN` 且 `carry_in_tokens=0` → `APPROVED_CARRY_BOUND_REQUIRED`；
- provider 非 `deepseek` 或 `limit_tokens≠1,000,000` → `APPROVED_PROVIDER_LIMIT_UNKNOWN`；
- meter recipe 不在已知集 → `APPROVED_METER_RECIPE_UNSUPPORTED`；资产不存在/非 D/E 盘/哈希不符 → `APPROVED_METER_ASSET_UNVERIFIED`；
- 过期 → `AUTHORITY_EXPIRED`。

`ApprovedTokenAuthority.validate` 另有：`facts.simulated=True`（合成事实）→ `MODEL_BOUND_FACTS_REQUIRED`；模型不在 `allowed_models` → 同码；非本人 scope → 同码。`reserve_bound` 要求上界来自 config 的 `meter.recipe`。

`bind_wire_facts`：无真实 `source_facts` → `SOURCE_FACTS_REQUIRED`；wire 哈希不符 → `WIRE_FACTS_MISMATCH`。

## 4. 仍未完成（明确列出，不得当作已启用）

1. **应用侧逐来源接线**：`CallContext.source_facts` 目前仍由测试/调用方提供；服务端在 question/history/evidence/memory/document/image/system 各能力处**尚未组装真实 `SourceFacts`**。因此经标准 API 的真实问答仍会在 `bind_wire_facts` 处 **fail-closed 拒绝**（这正是期望的当前状态）。
2. **真实 carry-in/UNKNOWN 上界**：`carry_source` 要求 `LEDGER:` 前缀，但**从既有账本实际取数并核对**的代码未接；真实启用仍需 Owner 核实。
3. **真实凭据/额度**：本实现不创建任何真实 key 或授权；配置未提供。
4. **真实供应商调用**：NOT RUN（闭合传输；`real_provider_calls=0`）。
5. `wire-bytes-upper-bound/v1` 是**保守上界**（可能偏松、导致按月额更早拒绝），不是供应商实际计数；不得据此报告实际 Token。

## 5. 边界

- 默认仍拒绝：无 authority 配置 → `P1SendGate` 拒绝所有发送、`/readyz` 503。
- synthetic 模式既有断言**未放松**（回归 48 passed）。
- 未改旧金额账本、旧 service_* 历史、未释放 UNKNOWN、未 Push/部署。
- 上游对照 `3e8b0bfc80b845b2d4b2ed683994748741450a97` 未改；路由阈值/升级规则未改。

## 6. 下一步

1. 在服务层按能力组装真实 `SourceFacts`（从 messages scope/版本/任务派生），使标准 API 的 approved 发送可达。
2. 实现基于 `port_token_calls` 的 carry-in/UNKNOWN 保守上界核对函数（PG，只读）。
3. 待 Owner 提供真实 provider 凭据环境变量名与月度/单次上限后，才做真实最小问答闭环（本轮不含）。
