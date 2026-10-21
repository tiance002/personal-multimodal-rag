# Spec — WeKnora Python Port：输出上界 Recipe 修复与关联链路审查（2026-10-15）

- 任务书（工作区外，已通读）：`...\long-text\6acf846f57e90e2d115ec65a\mv9o6tt8-zoxa\# WeKnora....txt`
- 工作树：`E:\RAG-Worktrees\weknora-python-port-20261013`；分支 `codex/weknora-python-port-integrate-image-20261014`；HEAD `04f7b0d78f0b539f0d70a2db79359487168e940d`
- 固定参考：`Tencent/WeKnora@3e8b0bfc` → `E:\RAG-reference\WeKnora-3e8b0bfc`
- 授权边界：**全程离线**（不发起真实模型请求）；不 Push/部署；不改旧库；不 flush Redis；不删历史；不解除 UNKNOWN/月阻断；不换账户标签；不放宽校验、不改旧账本；未运行不得写 PASS。

---

## 1. P0 根因（已证实，只读定位完成）

**结论**：真实冒烟 B 相（`deepseek/deepseek-flash`，`max_completion_tokens=64` → completion 177 > reserved_output 64）的根因是**计量 recipe 把一个“非供应商合同字段”的请求上限当成了输出预留上界**。

证据链：
1. 冒烟脚本 `E:\WK\run\real-egress-20261015\graded_real.py` L130-131 **手搓 `ModelConfig`**，绕过 `catalog.resolve_config()`：
   `ModelConfig(provider='deepseek', model='deepseek-flash', model_id='ds-chat', base_url='https://api.deepseek.com', source='remote')`
   → 使用**默认** `ChatSettings`：`max_tokens_field="max_completion_tokens"`、`thinking_format="none"`。
2. `models/request.py::build_chat_body` 按 `s.max_tokens_field` 写字段 → 最终 wire 携带 `max_completion_tokens:64`。
3. `catalog_resources/providers.generated.json` 中 `deepseek`（L336-379）声明
   `Compat.OpenAICompletions.MaxTokensField = "max_tokens"`、`ThinkingFormat="thinking-type"`、`SupportsReasoningEffort=true`。
   参考 `E:\RAG-reference\...\internal\models\providers\deepseek.go` 同款注释：output cap 是 `max_tokens`，**`max_completion_tokens` 不在参考中**；thinking 默认 enabled。
4. 供应商对合同外字段 `max_completion_tokens` 未施加 cap → 返回 completion=177；`storage/token_budget.py::finish` 按设计以 `state=unknown` + 月 `blocked_reason='USAGE_EXCEEDS_RESERVATION'` fail-closed。
5. 账本证据 `docs/weknora-port/evidence/REAL_EGRESS_20261015/real-ledger.json`：input 195/64→44、output 64/64→177、total 259→221。

**不可证明**（任务书要求标注）：响应体未保存 → 无法证明 113 差额即 reasoning token，也无法断言 DeepSeek 的 cap 天然不含 reasoning。
**因此不采用**“请求 cap + 固定 reasoning 余量”这类猜测性余量。

**受影响路径**：任何**未经 `resolve_config` 的 `ModelConfig`** 进入 approved 计量时，都可能让 wire 携带非合同 cap 字段，从而预留上界与供应商实际生成口径不一致。生产装配 `development.py` 走 `resolve_config`（字段正确），但**计量层本身没有校验 wire 字段**，这是系统性缺陷。

---

## 2. 修复设计（P1 核心）

### 2.1 新增不可变输出预算合同 `models/output_contract.py`（新文件）
一个 code-owned、不可变的结构，一次性分离：业务回答目标长度 / 最终 wire cap / cap 计量语义 / reasoning 是否含在 completion / 实际预留输出上界 / 上界来源与适用条件。

- `CAP_CONTRACTS: dict[(provider, api) -> (cap_field, semantics, reasoning_field)]`，当前仅声明**有明确合同**的供应商：
  - `('deepseek','openai-completions') -> ('max_tokens','full-completion/v1', None)`
  - `('siliconflow','openai-completions') -> ('max_tokens','full-completion/v1', None)`
- `_registry_cap_field(provider, api)`：从**固定注册表**（`catalog.provider_definition(provider)['Compat'][协议]`）取声明字段；表与注册表不一致 → `MODEL_OUTPUT_CAP_FIELD_DRIFT`（防止代码表与参考漂移）。
- `OutputBudgetContract`（frozen dataclass）：`provider, api, capability, cap_field, cap_value, cap_semantics, reasoning_field, reasoning_upper, thinking_mode, reasoning_effort, reasoning_included, output_upper, upper_source, case`。
- `resolve_output_contract(config, body, *, capability='chat')`：
  - `capability ∉ {chat, vision}` → `case='A'`、`cap_semantics='no-generation/v1'`、`output_upper=0`（embedding/rerank 无生成）。
  - `(provider, api)` 无合同条目 → **case D：`MODEL_OUTPUT_CONTRACT_UNKNOWN`**（拒绝作为 approved 严格预算）。
  - wire `body[cap_field]` 非正整数 → `MODEL_OUTPUT_HARD_LIMIT_DENIED`。
  - `full-completion/v1` → `case='A'`、`output_upper=cap_value`（reasoning 已含在 completion，不额外累加）。
  - `visible+reasoning/v1` → `case='B'`、`output_upper=cap_value+reasoning_upper`（需两个可核验硬上限；当前无供应商声明，代码路径保留并由单测覆盖）。
  - 其他未知语义 → case D 拒绝。
  - `thinking_mode` 取自 wire（`thinking.type` / `enable_thinking` / `reasoning_effort`），只**记录**不臆测。
  - **不引入任何固定/经验/P99 reasoning 余量。**

### 2.2 `source_facts.py` — recipe 成为可核验合同
- `METER_RECIPES = frozenset({'wire-bytes-upper-bound/v1','provider-completion-cap/v2'})`。
- `BoundCallFacts` 增字段 `output_contract: Any = None`（默认 None，兼容既有直接构造）。
- `bind_wire_facts(config, context, body, wire, capability='chat', *, config_sha, recipe)`：
  - 保留原有 `SOURCE_FACTS_REQUIRED` / `WIRE_FACTS_MISMATCH` 前置校验；
  - **两条 recipe 都通过 `resolve_output_contract` 计算上界**：`output_upper = contract.output_upper`，`source = recipe`，并附 `output_contract=contract`；
  - 「v1 的预留数值对合规请求保持不变（= 声明 cap 值）」；对**非合同字段组合 fail-closed**。这是**显式收紧适用域**（对合规请求的数值与结算语义不变），不是静默改语义；历史已记账行的 recipe/预留数值不改。
  - `recipe ∉ METER_RECIPES` → `TOKEN_BOUND_RECIPE_UNSUPPORTED`。
- `bind_synthetic`、`measure_final_input`、`build_call_sources` 保持不变（合成路径与输入 recipe 不动）。

### 2.3 `authority.py` + `storage/token_budget.py` — 解除等量耦合
- `ApprovedTokenAuthority.reserve_bound`：**删除** `bound.output_upper != (call.output_budget or 0)`；改为按**同一份合同**核验：
  - 合同存在且 `output_upper == bound.output_upper`；`cap_value == (call.output_budget or 0)`（wire cap 与调用 cap 一致，**不再要求上界等于 cap**，故 case B 可分离）；`provider == call.provider`；`upper_source == bound.source`；非生成能力 ⇒ `output_upper == 0`；否则 `TOKEN_BOUND_UNSUPPORTED`。
- `ConfiguredTokenAuthority`（合成）保持原等量约束不动。
- 字段职责澄清（文档 + 校验落实）：`call.output_budget` = **wire 上位字段值（网络请求 cap）**；`bound.output_upper` = **计量输出预留上界**；`AuthoritySettings.output_limit`/`SendAuthorization.max_output_tokens` = **服务器允许的最大限制**（上界校验 `output_upper ≤ output_limit` 保持）。
- **计量层不得偷偷扩大用户生成的 cap**：合同只读取 wire 既有字段，不新增/改写 wire。

### 2.4 `storage/token_budget.py` — 额度身份与配置版本分离（P3/T8）
- `ApprovedTokenAuthority.monthly`：`approval_source = s.approval_source`（**额度授权身份**，稳定），**不再拼接 `config_sha`**。
- 新增 `same_month_authority(stored, expected)`：`stored == expected` 或 `stored.startswith(expected + ':')`（**受控、可核验的旧行兼容**：旧行格式为 `<approval_source>:<config_sha>`）；只在读取比较处使用，**不改写存储行、不重置 blocked 月**。
- `config_sha` 仍用于**调用/recipe 版本**校验（`validate()` 的 in-flight 身份），两类概念分离。

### 2.5 `models/usage_facts.py` — reasoning 用量明细（P2/T6）
- `capture_usage`（openai 语义）新增路径 `'reasoning': ('completion_tokens_details.reasoning_tokens',)`；缺失 → `present=False, value=None`（**不视为 0**）。
- `accounting()`：`reasoning` 存在时校验 `0 <= reasoning <= output`，违反则 `valid=False`（不静默结算）；**reasoning 不加入 input/completion/total，不重复计入**；`lower` 计算不变。
- 旧供应商无该字段时 `not present` → 不影响既有结算。

### 2.6 `models/transport.py` — 结算与关闭顺序（P2/T7）
`AsyncHTTPTransport.open()` 的 `finally` 调整为：**先有界结算 `gate.finish`，再清理 `response.aclose()`**。
- 结算用 `asyncio.wait_for(..., timeout=SETTLE_BOUND_SECONDS)` 有界保护；结算异常 → `MODEL_USAGE_SETTLEMENT_FAILED(sent=sent)`（**不重试、不换模型**）。
- `aclose()` 失败被吞掉且**不得跳过/掩盖结算**；网络清理失败不改变已发送事实。
- `asyncio.CancelledError` 仍按原样传播（取消不阻止占用保留）。
- 已发送 `sent=True` 不会被误记为 `NOT_SENT`（`outcome` 分派逻辑不变）；重复 finish 幂等由 `token_budget.finish`/`send_gate.finish` 的终态分支保证。

### 2.7 `development.py` — 传递能力给绑定器
approved 分支 `transport.bind_facts` lambda 改为把 `capability=capability` 传入 `bind_wire_facts`（使 embedding/rerank 走 `no-generation/v1`、chat/vision 走 cap 合同）。

### 2.8 P4 审核（任务 10/11/12）
- **T10**：逐项核验 `wire-bytes-upper-bound/v1` 适用域（纯文本/history/system/JSON/tool schema/embedding 批量/rerank/图片 URL/base64/VLM/原生协议）；缺证场景**继续 fail-closed**；**不给 VLM 加常数**。输出：报告第 5 节逐项 `CONFIRMED/RISK/NOT_APPLICABLE/UNVERIFIED`。
- **T11**：审核 `build_call_sources` 的**原始来源证明**与 **Scope 区分**；最小可修缺陷（如 rerank query 来源）在本批修复并测试；架构级改造**只隔离不安全路径 + 单列**，不全面重构。
- **T12**：审核路由/升级：上界异常不得被当作答案质量差触发升级；权限/预算失败不得换模型绕过；UNKNOWN 不得触发无约束重试；一次升级用独立调用身份但共用正确计费账户。**不改路由评分规则**。

### 2.9 额度归属（P3/T9）
- 文档 + 最小校验：**额度归属于真实计费账户/供应商**，不归属于 cheap/expensive/vision 角色；`allowed_models` 已按 `(provider, model)` 约束；`ApprovedTokenAuthority` 以 `(account_id, provider, month)` 为月池键，角色别名/模型别名不能铸造新额度池。SiliconFlow 不得计入 DeepSeek 额度（`provider != 'deepseek'` 在 `_load_approved` 拒绝）。**不改路由评分规则**。

---

## 3. 测试（P5 必测 A–E）

全部离线 / MockTransport / 隔离 PG（`127.0.0.1:56423/weknora_python_port_20261013`）/ Redis db15（**禁 flush**）；临时目录路由到 E 盘。

- **新增单测** `tests/weknora_port/test_output_budget_contract.py`：
  - A：deepseek 配置解析出 `max_tokens`；`max_completion_tokens` 组合被拒（复现旧故障组合）；A/B/D 三态；注册表漂移拒绝；非生成能力 `output_upper=0`。
  - B：复现旧记录 195/64→44/177/221 仍触发输出分项超界并月阻断；v2 cap 边界值/超界；reasoning 存在/缺失/非法（`>completion`）不静默结算。
  - C：授权层接受合同（case A 相等、case B 分离上界）；旧月 `approval_source:<config_sha>` 兼容；跨月 carry-in 语义不变。
- **回归**：`test_authority_approved.py`、`test_authority_approved_pg.py`、`test_authority_carry_in_pg.py`、`test_authority_usage.py`、`test_send_gate.py`、`test_token_authority_pg.py`、`test_provider_usage.py`、`test_transport_occupancy.py`、`test_provider_transport.py`、`test_native_protocols.py`、`test_model_configuration.py`、`test_catalog.py`。
- **受影响最小 RAG 回归**：`test_generation_wire.py`、`test_pipeline_tokenizer.py`（如受影响）。

记录实际命令、退出码、通过/失败数；未运行写 `NOT RUN`。

---

## 4. 交付物
- 报告 `docs/weknora-port/OUTPUT_BOUND_RECIPE_REPAIR_20261015.md`（9 节：根因/WeKnora 对照/修复设计/变更清单/关联风险审核/测试报告/历史数据保护/剩余阻断/最终结论∈{READY_FOR_AUTHORIZED_REAL_SMOKE, PARTIAL, BLOCKED}）。
- 仅提交本项已审查文件；**保护** `.serena/project.yml` 与 6 个受保护未跟踪文件；不 Push。

## 5. 需要确认的决策
1. **两 recipe 均合同化（v1 显式收紧适用域）** vs 仅 v2 严格、v1 原样保留——本 spec 采用前者（对合规请求数值/结算不变，非合同组合 fail-closed）。
2. `BoundCallFacts.output_contract` 为新增可选字段（默认 None）以兼容既有直接构造。
3. 旧月 `approval_source` 采用**前缀兼容读取**（不改写行、不重置阻断），而非数据迁移。
4. P4 架构级来源缺陷只隔离 + 单列，不在本批全面重构。
