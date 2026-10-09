# P4-DIAG-R1 — SiliconFlow Rerank 离线诊断

日期：2026-10-08。结果：**P4_DIAG_OFFLINE_PASS**。

本轮真实 SiliconFlow 请求 **0**，其他真实 HTTP / 模型请求 **0**。
本报告的反例和回归均为 **SIMULATED**，不构成供应商真实 Rerank 验收。
历史 **P4_BLOCKED** 保留；首次请求的用量继续是 **UNKNOWN**。

## 1. 目标与改动

开始时工作树干净；本机 HEAD、缓存的 upstream SHA 与用户提供的 GitHub P4
SHA 均为 `7dcf273e9dab66dc95632253245c2b5a56dfcb0f`。
本轮没有联网查询 GitHub，因此不声称独立核验了远端当前状态。
未 reset、stash、提交、推送或打 Tag。

实际变更范围：

- `backend/app/adapters/models/cloud.py`：增加安全诊断字段；修复畸形返回的校验路径、
  结算失败状态，以及可信 Transport 明确拒绝发送时的用量释放。
- `backend/tests/test_p4_rerank_diagnostics.py`：44 个离线用例，其中 34 个故障场景同时检查
  生产检索的 Fusion 回退、Circuit、单次调用、占用及日志隔离。
- `scripts/verify_p4_diag_offline.py`：独立离线包装器，阻断 socket 网络、子进程和密钥文件读取；
  使用模拟密钥、内存数据库设置和独立临时目录，保留各次运行证据。
- 本报告；新增证据仅在 `var/reports/p4-diag-r1/`。

已审查但未修改：`scripts/verify_p4_rag.py`、项目密钥加载源码、模型注册白名单、
`BudgetUsageGuard`、用量记录接口及原 P4 回归。现有真实验证脚本已经保存
`rerank.receipts`，未来由负责人另行授权的运行会自然获得新增字段；本轮没有运行其真实入口。

## 2. 已复现的代码缺陷

以下是本地 P4 代码快照的离线重放结果，**不能据此归因首次真实请求**。

| 缺陷 | 原代码实际表现 | 最小修复及反例 |
| --- | --- | --- |
| 返回行不是对象，或 `document` 为 null/标量 | `.get()` 引发非预期异常，外部只得到 `MODEL_REQUEST_FAILED` | 先校验对象结构；返回 `ProviderUnavailable(RERANK_RESPONSE_INVALID)`，安全码 `RERANK_STRUCTURE_INVALID` |
| 评分为超大整数 | `math.isfinite()` 溢出，外部只得到 `MODEL_REQUEST_FAILED` | 捕获数值转换边界；按评分非法拒绝，安全码 `RERANK_SCORE_INVALID` |
| 合法响应后用量结算抛错 | 调用失败，但已写收据的 `status=ok`；模拟 ledger 仍为 RESERVED | 先尝试结算，再写最终失败状态；收据及 usage capture 都记录 error，保留已观察 token 和 UNKNOWN |
| Transport 明确抛出 `ProviderRequestNotSent` | Circuit 正确保持关闭，但结算仍传 `sent=True`，占用成为 UNKNOWN | 仅对明确未发送传 `sent=False`；模拟 ledger RELEASED，收据 NOT_SENT；其他失败不释放 |

在 `baseline-replay/junit.xml` 中，结算反例的属性明确记录：
`receipt_status=ok`、`error_code=ABSENT`、`ledger_state=RESERVED`、`transport_calls=1`。
畸形行/文档和超大评分的失败断言也保留了原通用错误与期望固定错误的差异。
历史首次 UNKNOWN 占用没有套用任何新规则回溯释放。

## 3. 历史证据证明的范围

首次原始脱敏收据 `var/reports/p4-r1/live/live-receipt.json` 记录：

- `attempts` 恰好 1 次；在进入下层 Transport 前已经消耗授权尝试并保存请求体长度及哈希。
- 统一异常类型为 `ProviderUnavailable`；adapter 收据 `status=error`。
- `usage_actual=null`、`settlement=UNKNOWN`；model_calls 计数从 3 到 4。
- 本机准入链已走到 Transport：非空凭据存在检查、模型/URL 配置、合成输入白名单与本次预算预留
  没有在该路径提前拒绝。这不证明凭据有效，也不证明服务器收到请求。

原代码遇到正常 `HTTPError(401/403/404/429/5xx)` 会记录 `http_N`。
旧收据只有 `error`，因而**没有证据支持把首次失败断定为其中某个 HTTP 状态**。
这也不能排除响应处理中的其他错误，或主请求错误与结算错误同时发生。

原代码中，单独发生在合法响应之后的结算错误会留下 `ok`；旧收据为 `error`，
因此“仅结算失败”与现有记录不符。复合故障仍无法排除。
约 628 ms 的耗时不足以证明 DNS、TLS、连接或解析阶段中的任意一个。

**仍无法确定的真实请求原因：** DNS/连接、TLS、超时、JSON 解码、返回结构、
候选映射/评分、响应模型身份、用量解析或复合结算故障，旧记录不能进一步区分。
原异常细节没有保存，无法离线恢复；没有把模拟反例写成历史根因。

## 4. 凭据、URL、代理及 TLS 审查

- 只阅读加载实现，没有打开实际 `.env`、`.env.local`、auth/token 文件或读取 API Key 值。
  `with_clean_slate.py` 的项目级加载器仅取声明的供应商键；项目 `.env.local` 可覆盖继承值，
  不输出键值。`backend/app/env_loader.py` 的普通配置加载白名单与此路径不同，
  不能假定仅经普通加载器就获得 SiliconFlow 凭据。
- `ModelSpec` 固定 provider / model / URL / key-env 配对；SiliconFlow 基址为
  `https://api.siliconflow.cn/v1`，Rerank 模型为 `BAAI/bge-reranker-v2-m3`。
  真实脚本另有 `/rerank`、模型与合成 query/document 的精确准入。
- `_send()` 继续使用 `_NoRedirect()` 和 `ProxyHandler({})`；不启用代理或自动重定向。
- 使用 urllib 默认 HTTPS 校验，没有自定义不安全 context。
  本机标准 TLS context 检查输出为 `check_hostname=True`、`CERT_REQUIRED`；
  模拟 opener 用例同时检查默认 HTTPS handler、空代理和拒绝重定向。
- 保留请求/响应大小上限、完整候选一一映射、索引唯一/范围、文档一致性、有限数值评分、
  原始 hit 身份和服务端 KB 范围校验。没有重试、换模型或关闭校验。

## 5. 安全收据协议

新增 `diagnostic_schema=provider-diagnostic/v1`、`diagnostic_stage`、`error_code`。
不增加异常消息、URL、header、请求体、原文或响应体字段。
外部仍是既有 `ProviderUnavailable` / `ProviderRequestNotSent` 合同；
内部诊断异常在边界转换，不向外暴露新异常类型。

| 阶段 | 稳定错误码 | 离线场景 |
| --- | --- | --- |
| transport | DNS_FAILED | 原始及 URLError 包装的 gaierror |
| transport | NETWORK_CONNECTION_FAILED | 连接拒绝、包装 OSError |
| transport | TLS_CERTIFICATE_FAILED / TLS_HANDSHAKE_FAILED | 证书错误、握手错误及包装错误 |
| transport | REQUEST_TIMEOUT | 原始及包装超时 |
| http_response | HTTP_401 / HTTP_403 / HTTP_404 / HTTP_429 / HTTP_5XX | 401/403/404/429/500/503/599 |
| response_decode | JSON_RESPONSE_INVALID / RESPONSE_TOO_LARGE | `_send()` 的模拟原始字节：非法 JSON、非法编码、超限 |
| response_validation | RESPONSE_STRUCTURE_INVALID / MODEL_IDENTITY_MISMATCH | 非对象顶层、模型身份不符 |
| rerank_validation | RERANK_STRUCTURE_INVALID | results 非列表、行非对象、document 非对象 |
| rerank_validation | RERANK_MAPPING_INVALID | 重复/越界 index、文档不一致 |
| rerank_validation | RERANK_SCORE_INVALID | NaN、Inf、bool、文本、溢出整数 |
| usage_parse | USAGE_PARSE_FAILED | 模拟用量解析器抛错 |
| usage_settlement | USAGE_SETTLEMENT_FAILED | 合法响应结算失败及与超时复合故障 |
| admission | REQUEST_NOT_SENT | 可信 Transport 在 I/O 前明确拒绝 |
| complete | NONE | 成功；缺失用量仍 UNKNOWN |

未识别异常只输出阶段内固定兜底码（TRANSPORT_FAILED、PROVIDER_UNAVAILABLE、
RESPONSE_VALIDATION_FAILED 等），不依据原始异常字符串猜测原因。
若结算也失败，最终主码为 USAGE_SETTLEMENT_FAILED，另保留
`request_diagnostic_stage` / `request_error_code` 两个安全字段，避免覆盖主请求故障线索。

34 个故障场景均验证：第一次仅一次模拟 Transport，第二次检索由 Circuit 阻止；
Fusion 排序和结果与无 ranker 基线一致；保守预留不被当成零费用；结算失败保留 RESERVED，
其他不确定发送失败保留 UNKNOWN；usage capture 的失败状态与收据一致。
模拟密钥、内容及异常哨兵不得出现在 receipts、usage capture、日志或 stdout/stderr。
测试包装器也对失败报告中的模拟哨兵做固定替换；不是读取真实敏感内容后再脱敏。

## 6. 执行命令与证据

所有下列命令从本次工作树根目录执行。未实现的命令不得报告通过。

| 原样命令 | 退出码 | 实际结果 / 证据 |
| --- | --- | --- |
| `git status --short` | 0 | 开始无输出；结束只包含本任务变更 |
| `git rev-parse HEAD` | 0 | `7dcf273e9dab66dc95632253245c2b5a56dfcb0f` |
| `git rev-parse '@{upstream}'` | 0 | 同上；本地缓存，不是在线查询 |
| `.\.venv\Scripts\python.exe -B scripts/verify_p4_diag_offline.py red` | 1 | 收集前 BLOCKED：Windows 平台探测触发子进程护栏；0 网络；`red/report.json` |
| `.\.venv\Scripts\python.exe -B scripts/verify_p4_diag_offline.py baseline` | 1 | 201 收集，160 通过，41 失败；夹具缺少合成 KB 许可及 redirect 参数个数错误，此轮不能当作完整故障矩阵证据 |
| `.\.venv\Scripts\python.exe -B scripts/verify_p4_diag_offline.py final` | 1 | 201 收集，167 通过，34 失败；合成 KB 门禁拒绝导致无收据，随后修正夹具；此目录不是最终通过证据 |
| `.\.venv\Scripts\python.exe -B scripts/verify_p4_diag_offline.py baseline-replay` | 1 | 修正夹具后重放 P4 快照：161 通过，40 失败；`baseline-replay/report.json` 与 JUnit 保留缺陷证据 |
| `.\.venv\Scripts\python.exe -B scripts/verify_p4_diag_offline.py repair` | 0 | **201 passed, 0 failed, 0 skipped**；`repair/report.json` / `repair/junit.xml` / `repair/pytest.log` |
| `git diff --check` | 0 | 无空白错误；有 Git LF/CRLF 提示 |

离线 runner 禁用第三方 pytest 自动插件加载；socket API 与进程审计钩子在测试导入前安装。
Windows 版本信息改用 `sys.getwindowsversion()` 缓存，避免 Python 平台识别启动 `ver` 子进程；
没有放开子进程或网络权限。各次证据目录只创建一次，拒绝覆盖；重复运行需新目录安排。
基线快照由 `git show HEAD:backend/app/adapters/models/cloud.py` 导出为 LF 文本，
仅在 baseline-replay 子进程的模块命名空间加载，不替换工作区源码。

最终 201 项分布：新诊断 44；既有模型 Provider 合同 74；既有 P4 Rerank/Merge/Parent/Citation
流水线 34；上下文邻居 SQL/范围单元检查 42；检索路由 7。
它们均为模拟 Transport、内存对象/SQLite 或 SQL 构造检查，未连接真实 PostgreSQL。

最终护栏统计：网络 **0**、密钥文件 **0**、子进程 **0**；真实 HTTP/模型发送 **0**。
运行耗时实测 12.060 秒，pytest 用例执行 2.37 秒。
保留解释器路径提示及 SWIG 的 5 条 DeprecationWarning，没有为此修改运行环境。

## 7. 文件身份与历史保全

SHA-256（代码哈希对应最终 201 项通过时的文件；报告自身不作自引用哈希）：

| 文件 | SHA-256 |
| --- | --- |
| `backend/app/adapters/models/cloud.py` | `5eddfa3ad25090b1bfec961f96ed429ae2a5229361a6ea62af19a54a49c4895f` |
| `backend/tests/test_p4_rerank_diagnostics.py` | `e95e52db6dcf9f68609b61512a522db741d164f0d983290ca02103a6faf22caf` |
| `scripts/verify_p4_diag_offline.py` | `769c8ca46f01eb31daef84f3a3c5140bb903ab797c9ac3d6a6b72ac93c178b07` |
| `var/reports/p4-diag-r1/baseline-cloud.py.txt` | `306a081b5fda54b195209d6c4ab0eec6967a381e8495de66d61619656ec16dfb` |
| `var/reports/p4-diag-r1/baseline-replay/report.json` | `aa6d6b85297a62853aa0631b04dc829ab16607b8de42a87c64bec48eed5f7d8e` |
| `var/reports/p4-diag-r1/repair/report.json` | `9e8fb5ffe259cbe872c801864bdcf5d071f9edad9ff2f7914193c01ed35e543c` |
| 首次 `live-receipt.json`，前后相同 | `e9ba3e3bfde543f9b18acd2a83ce1578aaa57828163df3bb4e79035ef0ac0147` |
| 历史 `p4-weknora-rag-report.md`，前后相同 | `8fcda3c6d86279b85cbce8df08f373bc64f5101fa393456368069963831c0a91` |

最终机器报告另外记录 pytest 日志和 JUnit 的完整哈希。
数据库与真实用量 ledger 没有读取或修改，不声称做过数据库侧复核、费用结算或额度恢复。

## 8. 结果、风险与停止

- **PASS / SIMULATED**：本轮离线诊断、反例及相关 201 项回归。
- **NOT RUN**：真实 SiliconFlow Rerank、真实 PostgreSQL P4 验证、供应商验收、在线 GitHub 核验。
- **保留 P4_BLOCKED**：首次真实失败原因仍 UNKNOWN，不能用离线通过替换真实验收。
- 无新增依赖，无 Provider 重构，无真实服务/索引/资料/额度变更。
- 本次为单个父执行器的 MANUALLY_SUPERVISED_TRIAL；无子代理，未变更模型/供应商/隐私配置。
  实际 serving model/effort、Token 与费用没有独立可观察证据，均为 UNKNOWN，不作自报推断。
- 当前变更未提交；未声称负责人验收或批准延期。已完成本轮任务并停止，
  不重新调用 Rerank、不进入 P5。任何真实复测都需要独立后续任务及授权。
