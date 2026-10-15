# 输出上界 Recipe 修复与关联链路审查报告 — 2026-10-15

- 工作树：`E:\RAG-Worktrees\weknora-python-port-20261013`
- 分支：`codex/weknora-python-port-integrate-image-20261014`；审查基准 HEAD `04f7b0d78f0b539f0d70a2db79359487168e940d`；本轮提交 `561e3bc8a5a6d13a7199c48f35daf40be3cd04e1`
- 固定参考：`Tencent/WeKnora@3e8b0bfc`（`E:\RAG-reference\WeKnora-3e8b0bfc`）
- 执行边界：**全程离线**（未发起任何真实模型请求）；未 Push/部署；未改旧库；未 flush Redis；未删除历史；未解除 UNKNOWN/月阻断；未换账户标签；未放宽校验；未修改旧账本。真实密钥未读取、未回显、未入证据。
- 规划文件：`docs/weknora-port/spec.md`、`docs/weknora-port/tasks.md`

---

## 1. 根因分析

**已证实根因**：approved 计量 recipe 把**“非供应商合同字段”的请求上限**当成了输出预留上界。

链路与证据：

| 步骤 | 事实 | 证据位置 |
|---|---|---|
| 1 | 真实冒烟脚本手搓 `ModelConfig(provider='deepseek', model='deepseek-flash', model_id='ds-chat', base_url='https://api.deepseek.com', source='remote')`，**绕过 `catalog.resolve_config()`** | `E:\WK\run\real-egress-20261015\graded_real.py` L130-131 |
| 2 | 手搓配置使用默认 `ChatSettings`：`max_tokens_field="max_completion_tokens"`、`thinking_format="none"` | [types.py](file:///E:/RAG-Worktrees/weknora-python-port-20261013/backend/weknora_port/models/types.py#L130-L158) |
| 3 | `build_chat_body` 按 `s.max_tokens_field` 写字段 → 最终 wire 携带 `max_completion_tokens:64` | [request.py](file:///E:/RAG-Worktrees/weknora-python-port-20261013/backend/weknora_port/models/request.py#L158-L224) |
| 4 | 供应商合同声明 output cap 字段是 `max_tokens`（`MaxTokensField="max_tokens"`），**`max_completion_tokens` 不在合同中** | 注册表 `providers.generated.json` L336-379；参考 `deepseek.go` 头注释 |
| 5 | 供应商对合同外字段未施加 cap → 返回 completion=177（thinking 默认 enabled） | `real-ledger.json`：input 195/64→44、output 64/64→177、total 259→221 |
| 6 | `finish` 检出输出分项超界 → `state=unknown`、月 `blocked_reason='USAGE_EXCEEDS_RESERVATION'`（fail-closed，非崩溃） | [token_budget.py](file:///E:/RAG-Worktrees/weknora-python-port-20261013/backend/weknora_port/storage/token_budget.py#L126-L133) |

**系统性缺陷（本次修复对象）**：`bind_wire_facts` 直接按 `body.get('max_tokens', body.get('max_completion_tokens', 0))` 取预留上界，**不校验该字段是否为该供应商声明的 cap 字段**。因此任何绕过 `resolve_config` 的 `ModelConfig` 都能把“供应商不认的字段”当成预留上界，导致请求参数、供应商合同、预留、结算口径不一致。生产装配 `development.py` 走 `resolve_config`（字段正确），但计量层自身没有护栏。

**证据等级与不可证明项（按任务书要求标注）**：
- 已证实：`max_completion_tokens` 为合同外字段；实际返回 completion=177 > 预留 64。
- **不可证明**：响应体未保存 → 无法证明超出的 113 即 reasoning token；也无法断言 DeepSeek 的 cap 天然不含 reasoning（`UNVERIFIED`）。
- 因此**未采用**“请求 cap + 固定 reasoning 余量”这类猜测性上界。

---

## 2. WeKnora 固定上游对照

| 项 | 固定参考（Go） | Python 移植（本批后） | 结论 |
|---|---|---|---|
| `MaxTokensField` | `deepseek` → `max_tokens` | 注册表同款；新合同按注册表**重算并校验** | 一致 |
| `ThinkingFormat` | `thinking-type` | 注册表同款；合同只**记录** wire 实际模式，不臆测 | 一致 |
| `SupportsReasoningEffort` / `ThinkingLevels` | 支持；low/high/max | 注册表同款；`apply_thinking` 未改 | 一致 |
| `PromptCacheAccounting` | true | 未改 | 一致 |
| output cap 语义 | `max_tokens` 为输出上限；无 thinking 默认 8K / 有 thinking 64K / effort=max 128K；`max_completion_tokens` 不在参考 | 合同声明 `full-completion/v1`（cap 约束完整 completion，reasoning 含在内，不额外累加） | 方向一致；**供应商是否逐字履约 = 需真实冒烟核验（UNVERIFIED）** |
| usage 字段 | `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` + `prompt_tokens_details.cached_tokens` | 保留；新增 `completion_tokens_details.reasoning_tokens` 采集 | 一致（新增为可选分项，缺失≠0） |
| 非预期语义差异 | — | 无新增字段写入 wire；未改 thinking/采样/工具渲染 | 无 |

---

## 3. 修复设计

### 3.1 不可变输出预算合同（新增 `models/output_contract.py`）
一次性分离：业务回答目标长度 / 最终 wire cap / cap 计量语义 / reasoning 是否含在 completion / 实际预留输出上界 / 上界来源与适用条件。`OutputBudgetContract` 为 frozen dataclass。

`resolve_output_contract(config, body, *, capability)`：
- 非 `chat/vision`（embedding/rerank）→ `case='A'`、`cap_semantics='no-generation/v1'`、`output_upper=0`。
- `(provider, api)` 无合同条目 → **case D：`MODEL_OUTPUT_CONTRACT_UNKNOWN`**。
- **注册表漂移**（代码表字段 ≠ 固定注册表 vendor override）→ `MODEL_OUTPUT_CAP_FIELD_DRIFT`。
- wire `body[cap_field]` 非正整数 → `MODEL_OUTPUT_HARD_LIMIT_DENIED`。
- `full-completion/v1` → `case='A'`，`output_upper = cap_value`（**reasoning 不额外累加**）。
- `visible+reasoning/v1` → `case='B'`，`output_upper = cap_value + reasoning_upper`（两个可核验硬上限才成立）。
- 其他未声明语义 → **case D 拒绝**。

代码表 `CAP_CONTRACTS`：`('deepseek','openai-completions')`、`('siliconflow','openai-completions')` → `('max_tokens','full-completion/v1', None)`。**不含任何固定/经验/P99 reasoning 余量。**

### 3.2 recipe 成为可核验合同（`source_facts.py`）
- `METER_RECIPES = {'wire-bytes-upper-bound/v1','provider-completion-cap/v2'}`。
- `bind_wire_facts(config, context, body, wire, capability='chat', *, config_sha, recipe)`：
  - 保留 `SOURCE_FACTS_REQUIRED` / `WIRE_FACTS_MISMATCH`；新增 `TOKEN_BOUND_RECIPE_UNSUPPORTED`；
  - **两条 recipe 均走同一合同**：`output_upper = contract.output_upper`，`source = recipe`，附 `output_contract`。
  - v1 对**合规请求的预留数值保持不变**（= 声明 cap 值）；对合同外字段组合 **fail-closed**。这是**显式收紧适用域**（对合规请求的数值与结算语义不变），非静默改语义；历史已记账行保留原 recipe 与预留数值。
- `BoundCallFacts` 新增 `output_contract`（默认 `None`，兼容既有直接构造）。
- 输入 recipe `wire-bytes-upper-bound/v1` 语义未改（= wire 字节数）。

### 3.3 授权层解除等量耦合（`authority.py` / `storage/token_budget.py`）
- `ApprovedTokenAuthority.reserve_bound`：**删除** `bound.output_upper != (call.output_budget or 0)`；改为按**同一份合同**核验：合同存在、`output_upper` 一致、`cap_value == call.output_budget`、`provider` 一致、非生成能力 ⇒ `output_upper==0`、生成能力 ⇒ `>0`。
- 字段职责分离并落到校验：
  - `call.output_budget` = **wire 生成 cap（网络请求上限）**
  - `bound.output_upper` = **计量输出预留上界**（case B 时可 > cap）
  - `AuthoritySettings.output_limit` / `SendAuthorization.max_output_tokens` = **服务器允许的最大限制**（`output_upper ≤ output_limit` 保留）
- **计量层不扩大用户生成 cap**：合同只读 wire 既有字段，绝不新增/改写 wire。
- `ConfiguredTokenAuthority`（合成路径）保持原等量约束不变。

### 3.4 额度身份与配置版本分离（P3/T8）
- `ApprovedTokenAuthority.monthly`：`approval_source = s.approval_source`（稳定额度身份），**不再拼接 `config_sha`**。
- 新增 `same_month_authority(stored, expected)`：`stored == expected` 或 `stored.startswith(expected + ':')` —— **受控、只读的旧行兼容**（旧行格式 `<approval_source>:<config_sha>`），**不改写存储行、不重置 blocked 月、不新建额度池**。
- `config_sha` 仍用于**调用/recipe 版本**身份（`validate()`），两类概念分离。

### 3.5 reasoning 用量明细（P2/T6）
- `capture_usage` 增 `reasoning: completion_tokens_details.reasoning_tokens`；**缺失 → present=False（不视为 0）**。
- `accounting()` 校验 `0 ≤ reasoning ≤ completion`，越界 → `valid=False`（不静默结算）；**reasoning 不加入 input/completion/total**，不重复计入。

### 3.6 结算与关闭顺序（P2/T7）
- `AsyncHTTPTransport.open()` 的 `finally` 调整为：**先有界结算 `gate.finish`（`asyncio.wait_for`，`WEKNORA_LLM_SETTLEMENT_TIMEOUT_SECONDS` 默认 30s），后 `response.aclose()`**。
- `aclose` 失败被吞掉且**绝不跳过/掩盖结算**；结算异常 → `MODEL_USAGE_SETTLEMENT_FAILED(sent=sent)`；**不重试、不换模型**；`CancelledError` 原样传播；已发送不会被误记 `NOT_SENT`。
- `SIMULATED_USAGE.reset` 在“跨 Context 关闭”（清理任务驱动 `aclose`）时改为 best-effort（该上下文随任务销毁）。
- `provider.chat_stream`：**已上报 usage 时，结算与输出的 usage 为同一对象**（`assembler.usage = handle.usage`）；未上报时最终事件仍为 `usage=None`，而账本仍保留 UNKNOWN 占用。

### 3.7 输入上界适用域 fail-closed（P4/T10）
- `bind_wire_facts` 新增 `_unbounded_input_present(body)`：wire 含托管图片/视频/音频 part（`image_url`/`inlineData`/`fileData` 等）时 **`MODEL_INPUT_BOUND_NOT_APPLICABLE`**——因为供应商按像素/帧字节计 token，而 wire 只带 URL/base64。**未给 VLM 输入上界加任何常数**。纯文本路径不受影响。

### 3.8 rerank query 来源（P4/T11，最小修复）
- `build_call_sources` 的 rerank 分支新增 query 的 `question` 切片（`/query`），使 query 不再是“只随 wire 走、无来源记录”的元素。

---

## 4. 变更清单

| 文件 | 关键改动 | 兼容影响 |
|---|---|---|
| `backend/weknora_port/models/output_contract.py`（新增） | `CAP_CONTRACTS`、`registry_cap_field`、`OutputBudgetContract`、`resolve_output_contract` | 新增模块，无既有调用者 |
| `backend/weknora_port/source_facts.py` | `METER_RECIPES` 增 v2；`BoundCallFacts.output_contract`；`bind_wire_facts` 走合同 + capability + 媒体输入 fail-closed；rerank query 切片 | 合规请求 v1 数值不变；非合同组合改为拒绝；既有直接构造因默认值兼容 |
| `backend/weknora_port/authority.py` | `reserve_bound` 去等量耦合、按合同核验；`monthly` 身份去 `config_sha` | 合成路径不变；历史月行读取兼容 |
| `backend/weknora_port/storage/token_budget.py` | `same_month_authority`；月行 `approval_source` 兼容比较 | 只读兼容，不改行、不重置阻断 |
| `backend/weknora_port/models/usage_facts.py` | reasoning 分项采集 + 取值范围校验 | 无 reasoning 字段的旧供应商结算不变 |
| `backend/weknora_port/models/transport.py` | 结算先于关闭、有界、异常有界处理；ContextVar best-effort reset | 结算失败语义与 `sent` 事实保持 |
| `backend/weknora_port/models/provider.py` | stream：已上报时结算/输出同一 usage 对象 | 未上报时行为不变 |
| `backend/weknora_port/development.py` | approved binder 传 `capability` | embedding/rerank 走 no-generation，chat/vision 走 cap 合同 |
| `tests/weknora_port/test_output_budget_contract.py`（新增） | A/B/C/E 定向用例 18 例 | — |
| `docs/weknora-port/{spec.md,tasks.md}`（新增） | 本轮 spec 与任务 | — |

---

## 5. 关联风险审核

| # | 审核项 | 结论 | 处理 |
|---|---|---|---|
| 1 | 根因是否真正修复 vs 只延后超界 | **CONFIRMED** | 合同按注册表重算并校验 wire cap 字段；非合同字段组合**发送前**即 fail-closed（不再等结算期超界才发现） |
| 2 | 新旧参数解释是否不一致 | **CONFIRMED 无** | v1 保留原名与合规数值；v2 记录完整合同；两者共用同一 `resolve_output_contract` |
| 3 | 是否产生新权限绕过 | **CONFIRMED 无** | 合同只读 wire；`output_limit`/`max_output_tokens` 上界校验保留；`approved` 仍拒 `provider!='deepseek'` 与非真实身份 |
| 4 | 历史账本兼容 | **CONFIRMED** | 见第 7 节（UNKNOWN 行、blocked 月、原 recipe 数值全部保留） |
| 5 | Token 重复累计 / 漏计 / UNKNOWN 错误释放 | **CONFIRMED 无** | reasoning 为 completion 分项、不重复计入；越界 usage 不静默结算；`NOT_SENT` 仅在 `state=reserved` 且无下界时释放 |
| 6 | 新的并发竞争或取消问题 | **CONFIRMED 无** | 月锁/调用锁未改；跨 Context 关闭的 ContextVar 错误已修；结算在有界保护内 |
| 7 | 是否改了不该改的模块 | **CONFIRMED 无** | 未改路由评分、检索、Ingestion、Schema/迁移；`.serena` 与 6 个受保护文件未动 |
| 8 | 真实供应商合同风险（无法用测试覆盖） | **UNVERIFIED** | “DeepSeek 是否逐字以 `max_tokens` 约束完整 completion”需真实冒烟核验；已在第 8 节列为阻断 |
| 9 | 未经核验的输入上界是否被误称严格上界 | **CONFIRMED 处理** | 托管图片/视频/音频输入 → **拒绝**（不加上界常数）；文本/embedding/rerank 路径明确标为 wire-bytes 保守上界 |
| 10 | 与固定参考的非预期语义差异 | **CONFIRMED 无** | 未写入新 wire 字段；thinking/采样/工具渲染/缓存字段保持 |
| 11 | `wire-bytes-upper-bound/v1` 适用域（T10 逐项） | 纯文本 **CONFIRMED**；多消息 history **CONFIRMED**；system prompt **CONFIRMED**；JSON 模式 **CONFIRMED**；tool schema **CONFIRMED**；embedding 批量 **CONFIRMED**；rerank query/documents **CONFIRMED**；图片 URL **RISK→已拒绝**；base64 图像 **RISK→已拒绝**；文件引用 **RISK→已拒绝**；VLM **RISK→已拒绝**；供应商原生协议（文本部分）**CONFIRMED**（图像部分同前，拒绝）。依据：byte-level 分词下每 token ≥1 字节 → 字节数为严格上界；JSON 转义/协议键只会抬高字节数 |
| 12 | SourceFacts 原始来源证明（T11） | 发送内容哈希：**CONFIRMED**；真实原始来源与权限：**RISK** | `build_call_sources` 用**当前 Scope** 作为每个切片（含 history/assistant）的 `origin_kbs`；memory 无独立来源；system 模板按 KB 归属而非 `public_static`；image 无独立来源。**最小修复**：rerank query 现已单列。**架构级修复（history/memory/system/image 逐元素来源）按 spec 决策 4 隔离并单列，不在本批重构** |
| 13 | 路由与失败升级关联影响（T12） | **CONFIRMED** | `coordinator.py:380` 升级前提为 `error is None` → Token 上界/权限/预算异常**不会**被当成答案质量差触发升级；`storage/generation.py:101` 允许的 `upgrade_reason` 仅限超时/截断/空答/无效句柄；升级用 `ordinal=2` 独立调用身份但共用同一计费账户与月池（`resolver` 在锁内重解析）；`transport` 无通用重试（仅 `multimodal_unsupported` 镜像回退）；`run` 截止不触发升级 |

---

## 6. 测试报告

环境：Python `E:\RAG quention\.venv\Scripts\python.exe -B`；`PYTHONPATH` 含工作树/`E:\WK\deps\reader`/`var/weknora-port/runtime-deps`；临时目录 E 盘；隔离 PG `127.0.0.1:56423/weknora_python_port_20261013`；Redis `127.0.0.1:56424/db15`（`run_id=de03f13f2ffba1146b01434f25514c4478e046fd`，**未 flush**）。**未发起任何真实模型请求。**

| # | 命令 | 退出码 | 结果 |
|---|---|---|---|
| 1 | `python -B -m pytest tests/weknora_port/test_output_budget_contract.py -q` | 0 | **18 passed / 0 failed** |
| 2 | `python -B <evidence/AUTHORITY_APPROVED_20261015/run-accept.py> output-bound-repair-20261015 --pg-env test_authority_approved.py test_authority_approved_pg.py test_authority_carry_in_pg.py test_token_authority_pg.py test_authority_usage.py test_send_gate.py test_send_gate_pg.py test_output_budget_contract.py` | 0 | **85 passed / 0 failed / 0 skipped**（真实隔离 PG/Redis）；证据：`output-bound-repair-20261015.{junit.xml,exitcode.txt,stdout.txt,stderr.txt,redis-identity.json}` |
| 3 | `python -B -m pytest tests/weknora_port -q`（带 `WEKNORA_PORT_TEST_REDIS_RUN_ID`） | 1 | **1422 passed / 66 skipped / 1 failed** |

**必须区分模拟与真实**：以上全部为 **MockTransport + 隔离 PG/Redis 模拟**；`real_provider_calls=0`。

**唯一失败项**：`test_ocr_full_source_pg.py::test_fixed_go_full_ocr_same_fixture_and_native_image_entry` → `DocumentLifecycleError: OCR_HTML_SOURCE_CONVERTER_REQUIRED`。属**环境/工具缺失**（本机无该 OCR HTML 源转换器），与本次改动模块（output_contract/source_facts/authority/token_budget/usage_facts/transport/provider/development）**无交集**，判定为 **NOT RUN/BLOCKED（环境）**，非本次回归。

**本轮修复的 2 个既有失败（HEAD 已复现，非本批引入，已随后续 P2/T7 修复转绿）**：
- `test_provider_stream.py::test_stream_consumer_close_from_cleanup_task_has_no_context_token_error`（跨 Context 关闭导致 `SIMULATED_USAGE.reset` ValueError）→ 修复后通过。
- `test_provider_stream.py::test_final_usage_only_chunk_and_cache_precedence`（结算 usage 与输出 usage 的 `observed_lower_bound` 不一致）→ 修复后通过。
- 复核方式：对改动前 HEAD 用 `git stash` 单独运行该文件，确认两者在 HEAD 即失败（2 failed），随后恢复改动。

**必测场景覆盖（A–E）**：
- A：注册表声明 `max_tokens`；`max_completion_tokens` 组合被拒；thinking 开/关/默认记录；case A/B/D；注册表漂移拒绝；cap 非正整数拒绝；非生成能力 `output_upper=0`。
- B：复现旧记录 44/177/221 的输出分项超界仍被检出；reasoning 存在/缺失/越界（`>completion`）不静默结算且不重复计入；v1/v2 输入上界一致。
- C：授权层接受 case A（相等）与 case B（分离上界）、拒绝无合同/漂移/provider 不符；月身份跨 recipe 版本稳定；旧行前缀兼容；跨月 carry-in 只读保守（`test_authority_carry_in_pg.py` 通过）。
- D：隔离端到端兼容由 `test_authority_approved_pg.py` + `test_send_gate_pg.py` + `test_token_authority_pg.py` 覆盖（装配、预留、结算、replay、跨单位并发）；未重跑六套数据集评测。
- E：全目录回归 1422 passed。

---

## 7. 历史数据保护

**只读核验**（同一隔离 PG，未作任何写入；`deepseek-owner-main`）：

```
REAL_CALLS [
 {request_id:"real/embedding-0", provider:"siliconflow", capability:"embedding",
  state:"settled", outcome:"RESPONSE_RECEIVED", reserved_output:0, held_tokens:14,
  actual_total:14, bound_source:"wire-bytes-upper-bound/v1"},
 {request_id:"real/answer/1/cheap", provider:"deepseek", capability:"chat",
  state:"unknown", outcome:"RESPONSE_RECEIVED", reserved_output:64, held_tokens:259,
  actual_total:null, bound_source:"wire-bytes-upper-bound/v1"} ]
REAL_MONTHS [{account_id:"deepseek-owner-main", provider:"deepseek", month:"2026-10-01",
  carry_status:"KNOWN",
  approval_source:"OWNER:20261015-real-egress:0f16e911c36ee71d841c7c63403bc9ec803a196c2188c4a4e12d2351db866bad",
  blocked_reason:"USAGE_EXCEEDS_RESERVATION"}]
REAL_UNKNOWN_ROWS {n:1}
```

- UNKNOWN 行 **1 条保留**、`state=unknown`、`held_tokens=259`、`actual_total=null` **未变**。
- 月阻断 `USAGE_EXCEEDS_RESERVATION` **保留**；**未删除、未重置、未换账户标签**。
- 历史行 **原 recipe `wire-bytes-upper-bound/v1` 与原预留数值保留**（未被新 recipe 改写）。
- 旧格式 `approval_source:<config_sha>` 现在由 `same_month_authority` **前缀兼容读取** → 该账户本月后续调用仍会按 **`TOKEN_MONTH_BLOCKED`** 拒绝（不会因本轮 recipe 变更而改为 `TOKEN_MONTH_AUTHORITY_CONFLICT`，也不会新建额度池）。
- 证据文件 `docs/weknora-port/evidence/REAL_EGRESS_20261015/real-ledger.json` **未被修改**（`git status` 无该文件）。

---

## 8. 剩余阻断

**已解决**：
- 合同外 cap 字段被当作输出预留上界 → 发送前 fail-closed。
- `output_upper == call.output_budget` 等量耦合 → 解除（case B 可分离）。
- reason 用量缺失/越界/重复计入 → 已补采与校验。
- 结算先于关闭、跨 Context 关闭、结算/输出 usage 不一致 → 已修。
- 配置/recipe 变更引发月额度池冲突 → 已分离并兼容旧行。
- 托管图片/视频/音频的输入上界未证明 → 已 fail-closed。
- rerank query 无来源切片 → 已单列。

**未解决 / 需进一步核验**（不在本次授权范围内）：
1. **供应商合同履约核验（UNVERIFIED）**：DeepSeek 是否以 `max_tokens` 严格约束“完整 completion（含 reasoning）”，只能由**单独授权的真实冒烟**验证；离线无法证明。
2. **真实最小问答闭环未完成**：检索/回答仍依赖应用侧真实 `source_facts` 全链；本轮未发起真实调用。
3. **本月阻断前置条件**：`deepseek-owner-main` 2026-10 已被 `USAGE_EXCEEDS_RESERVATION` 阻断；任何真实 DeepSeek 复测在当前月都会被 `TOKEN_MONTH_BLOCKED` 拒绝。**是否解除/迁移该月须由项目负责人决定，本会话不得自行解除**。
4. **SourceFacts 架构级来源证明（RISK）**：history/memory/system/image 的**逐元素真实原始来源与权限**仍以当前 Scope 归并表示；完整修复需记忆/检索架构改造，**已单列为后续任务**。
5. **非流式 chat 的 usage 一致性**：已修流式；非流式 `response.usage` 与账本 `handle.usage` 仍非同一对象（潜在同类不一致，**RISK，单列**）。
6. **环境阻塞**：`OCR_HTML_SOURCE_CONVERTER_REQUIRED`（本机缺 OCR HTML 源转换器）与 66 项需显式 DSN 的 skip，均为环境限制，未伪造通过。

---

## 9. 最终结论

**`READY_FOR_AUTHORIZED_REAL_SMOKE`**

依据：全部离线定向用例（18）与隔离 PG/Redis 验收（85）通过；全目录回归 1422 passed（1 项环境阻塞、66 项缺 DSN skip），**模拟测试与真实服务分开标注**；历史 UNKNOWN、blocked 月与账本数值**未被删除或重置**；未 Push/部署、未发真实请求。具备进入**单独授权真实冒烟**的条件。

进入真实冒烟前必须由项目负责人确认的前置项：
- 使用 **recipe `provider-completion-cap/v2`**（或显式收紧后的 v1）重建 approved 授权，且 wire 使用**注册表声明的 cap 字段**（DeepSeek = `max_tokens`）。
- 明确处理 2026-10 的既有阻断（解除/迁移策略由负责人决定，本会话不擅自更改）。
- 真实冒烟须同时核验第 8 节第 1 项供应商合同履约。

**未使用 `REAL_QA_PASS` 或 `PRODUCTION_READY`**（真实完整验收尚未执行）。

---

### 附：完成后回执格式

1. **根因**：approved 计量 recipe 把**供应商合同外的字段**（DeepSeek 的 `max_completion_tokens`）当作输出预留上界；真实冒烟手搓 `ModelConfig` 绕过 `resolve_config`，供应商未施加 cap，返回 completion 177 > 预留 64。
2. **修复文件/函数**：新增 `models/output_contract.py`（`CAP_CONTRACTS`/`registry_cap_field`/`OutputBudgetContract`/`resolve_output_contract`）；`source_facts.py`（`METER_RECIPES`、`BoundCallFacts.output_contract`、`bind_wire_facts`、`_unbounded_input_present`、rerank query 切片）；`authority.py`（`reserve_bound`、`monthly`）；`storage/token_budget.py`（`same_month_authority`、月行比较）；`models/usage_facts.py`（reasoning 采集/校验）；`models/transport.py`（`finally` 结算/关闭顺序、ContextVar reset）；`models/provider.py`（流式 usage 同一对象）；`development.py`（传 capability）。
3. **额外问题与处理**：解除等量耦合；reasoning 缺失≠0/不重复计入/越界不结算；跨 Context 关闭导致的 `SIMULATED_USAGE.reset` ValueError（既有失败）已修；流式结算/输出 usage `observed_lower_bound` 不一致（既有失败）已修；托管图片输入上界未证明 → fail-closed；rerank query 无来源 → 单列；历史月身份与配置哈希耦合 → 分离并前缀兼容。
4. **测试**：通过 **18 + 85 + 1422**；失败 **1**（`OCR_HTML_SOURCE_CONVERTER_REQUIRED`，环境）；未运行/阻塞：66 项缺 DSN skip、真实冒烟（无授权）、供应商合同履约核验。
5. **历史账本与预算保护**：**保持不变**（UNKNOWN 1 条、blocked 月、原 recipe 预留数值、`real-ledger.json` 均未改；见第 7 节）。
6. **剩余阻断**：供应商合同履约（UNVERIFIED）、真实问答闭环、2026-10 月阻断前置、SourceFacts 架构级来源、非流式 usage 一致性、环境 OCR。
7. **Git**：分支 `codex/weknora-python-port-integrate-image-20261014`；审查基准 HEAD `04f7b0d…`；本轮改动提交 **`561e3bc8a5a6d13a7199c48f35daf40be3cd04e1`**（20 files changed, 1057 insertions(+), 20 deletions(-)；仅提交已审查文件，逐文件 `git add`）；**未 Push**、未部署。工作树仅剩受保护项：`.serena/project.yml` 与 6 个受保护未跟踪文件（未被暂存/修改）。
8. **最终状态与报告**：`READY_FOR_AUTHORIZED_REAL_SMOKE`；报告 `docs/weknora-port/OUTPUT_BOUND_RECIPE_REPAIR_20261015.md`。
