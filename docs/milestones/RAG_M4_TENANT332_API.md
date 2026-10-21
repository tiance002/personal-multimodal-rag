# M4 tenant 332 正式 API 身份接线

**状态：`IDENTITY_API_READY_ZERO_PROVIDER`。** 六个冻结 KB 可经独立、服务器固定身份的正式 FastAPI 入口读取；真实 M4 问答未运行。

## 身份与资源核验

- 起始 HEAD：`7219823b70387bb6eaf342c4bd6b47243f80d5ab`，分支 `codex/weknora-python-port-integrate-image-20261014`。原有 8 个未提交路径保留，未暂存或覆盖。
- tenant 332 subject 来源为 `E:\WK\run\p25-20261017\runtime\state.json`，脱敏指纹 `00f2fd3dad2b7419`。这份文件仅用作来源线索：冻结 M4 计划中的 subject 绑定、tenant/KB/文档版本身份、当前数据库中的主体模型绑定、密文解密和活动模型 Registry ID 均通过只读核验。
- 正常日常 principal 仍为 tenant 1（指纹 `4a8c5c959f9ffb8d`）。P81 工作台配置未改。PostgreSQL system ID `7696088403504697382`、数据库 `weknora_python_port_20261013`；Redis 实例 ID `dcff9d65a25725574ba0cfbc743d409fac211ef0`、DB 15；P25 来源存储标记与当前资产目录一致。secret-key 只比较身份，不输出内容。
- 六个 KB 均仍属于 tenant 332，13 个冻结文档/版本通过 API 可见并处于 `completed`。KB 记录的 `cloud_allowed=true`；本次独立运行全局 `cloud_allowed=false`，没有执行文件，因此 Provider 外发关闭。
- 四个角色均从正式配置接口解密校验，并通过正式 Model Registry 的活动状态及模型加载校验：Cheap SiliconFlow `XingChenAGI/Xing4.0-29B`（`mdl644750…`）、Embedding `BAAI/bge-m3`（`mdlebb99…`）、Rerank `BAAI/bge-reranker-v2-m3`（`mdl3076be…`）、Expensive DeepSeek `deepseek-flash`（`mdlfc056…`）。配置 API 没有输出密钥。
- tenant 332 有 130 个既有会话，但 memory 项与设置为 0。验证只创建一个空白身份检查会话，没有读取其他会话历史；新会话的 KB scope 固定为一个 tenant 332 KB，`document_scope=[]`。

## 独立 API 与隔离

`scripts/m4_tenant332_api.py` 复用 `assemble(settings, principal=LocalPrincipal(332, verified_subject))` 与标准 `entrypoint.create_app`。它只绑定 `127.0.0.1`，使用一次性 45 分钟 Bearer token 保护全部 HTTP 路由（包括 SSE 恢复）；token 仅留在进程内，不写文件、日志或仓库。客户端身份 Header/Query 不参与 principal 解析，API DTO 不接收租户或 subject 身份字段。Worker 未启动，使用独立随机 Redis 前缀和独立运行目录；完成检查后 API 已停止，没有碰 PostgreSQL/Redis 服务卷。

正常客户端与 M4 控制器顺序为：`create_session → POST message / SSE → GET history`。`M4EvaluationController` 只把冻结问题、KB/document scope 和 request ID 发给 API；Gold/answer_points 不进入请求，在响应后由调用方评分。受控云运行只能显式传入已存在的 M3.6 execution file；本接线脚本不创建或扩展授权文件。

## 实际验收

| 检查 | 结果 |
|---|---|
| 缺失/错误 Bearer；SSE 恢复缺失 Bearer | 401 / 401 / 401 |
| 带恶意 tenant/subject Header 与 Query 的 KB 清单；向会话创建 Body 填入 tenant/subject | 仍只返回 tenant 332 六个目标 KB；Body 身份字段被正式 DTO 拒绝（422） |
| 读取 tenant 1 KB；创建 tenant 1 scope 会话；在 tenant 332 会话查询 tenant 1 KB | 404 / 403 / 403 |
| 正式模型设置与 active Registry | 四角色已配置、解密通过、Model ID 可加载 |
| tenant 332 新会话 scope / 数据库归属 | 本轮保留两个空白身份检查会话；均由服务端绑定 tenant 332 与已验证 subject，仅各自目标 KB，无消息 |
| Provider ledger / UNKNOWN | 233 → 233；0 → 0；本任务 Provider 请求 0、Token 0 |
| 日常 tenant 1 principal、工作台和运行服务 | principal 指纹未变；API/Worker/UI 均未启动；PG/Redis 保持运行 |

一次初始接线验收因测试脚本误以为 `/api/v1/runtime` 应暴露数据库标识而失败；该接口实际按契约只返回 API 实现和版本。检查修正为以该端点确认 API 身份、以启动时的只读 runtime 摘要确认 PG/Redis 物理身份后，零 Provider 验收通过。失败时 API 已停止，未创建会话或发送请求。

## 命令与结果

- `& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest tests/weknora_port/test_m4_tenant332_api.py -q -p no:cacheprovider --basetemp 'E:\WK\run\m4-tenant332-tests-final4'` — exit 0，6 passed（Bearer ASGI 边界与 M4 API 控制器合同）。
- `& 'E:\RAG quention\.venv\Scripts\python.exe' -B scripts/m4_tenant332_api.py verify --config 'E:\WK\run\p81-retirement-20261018\local.json' --p25-state 'E:\WK\run\p25-20261017\runtime\state.json' --plan 'docs\milestones\RAG_M4_0_BASELINE.plan.json' --output 'E:\WK\run\rag-m40-tenant332-api'` — exit 0，`IDENTITY_API_READY_ZERO_PROVIDER`；最终临时 API 端口 `57998`，验证结束后停止。详细记录：`E:\WK\run\rag-m40-tenant332-api\20261018-163705-09fddcee\identity-api-verification.json`。
- `& 'E:\RAG quention\.venv\Scripts\python.exe' -B scripts/local_runtime.py --config 'E:\WK\run\p81-retirement-20261018\local.json' check` — exit 0，tenant 1 / cloud false / 数据库与 Redis 身份匹配。
- 同一配置的 `scripts/local_runtime.py ... status` — exit 0；API、Worker、UI 均 false，PostgreSQL/Redis running。
- `git diff --check` — exit 0。

## 后续条件与风险

tenant 332 的身份和正常 API 路径已接通，但本轮不执行 M4 冻结问题，也不代表 RAG/Provider 问答质量已验收。真实批次仍需按单独有效 Owner 授权使用 M3.6 持久化限额文件；UNKNOWN 必须停止该批。临时 API 当前已关闭。验证创建的一个空会话保留在 tenant 332 历史中以供审计，不删除。

本轮只应提交 API 接线脚本、对应定向测试与本报告/结果；日常 principal、六个 KB tenant 归属、既有索引和 8 个原有未提交路径保持不变。

本地提交 SHA 在交付消息中提供；不在提交内嵌入自引用 SHA。
