# EviRoute RAG — 唯一本机运行入口

正式主链只有：**React `WeKnoraPythonApp` → `backend.weknora_port` FastAPI → 同模块 Worker → 已核实的 PostgreSQL/Redis**。固定 WeKnora 提交与业务模型、检索参数保持原样。API、Worker、React 当前运行在 Windows 主机；Docker 分组 `eviroute-rag` 仅包含现有数据依赖。

## 启动、检查、停止

在工作树根目录运行：

```powershell
.\wk.ps1 check  -Config E:\WK\run\p81-retirement-20261018\local.json
.\wk.ps1 start  -Config E:\WK\run\p81-retirement-20261018\local.json
.\wk.ps1 status -Config E:\WK\run\p81-retirement-20261018\local.json
.\wk.ps1 usage  -Config E:\WK\run\p81-retirement-20261018\local.json
.\wk.ps1 stop   -Config E:\WK\run\p81-retirement-20261018\local.json
```

`check` 只读核实容器归属、镜像、卷、绑定端口、PG system ID、数据库名称、Redis 当前 run ID、已有本地 principal、密钥文件长度和 MinerU 路径，不调用模型。`start` 启动该配置固定的已有 PG/Redis 容器（如已停止），随后启动 API、Worker 和 React；不运行 Compose `up`、迁移、模型下载或旧 dotenv。端口占用直接拒绝，不替换已有服务。Redis 重启后 run ID 会变化：从固定容器 ID、Compose 标签、外部卷、端口匹配的实例重新读取，再共同传给 API/Worker，不盲目复用旧 run ID。

`status` 输出由此入口管理的进程身份及新 API 身份。`stop` 仅停止状态文件记录且 PID、可执行文件、创建时间、命令哈希均匹配的三个主机进程；保留 PG/Redis 及全部卷。请在上传和问答空闲后停止；Windows Stop-Process 不是应用级优雅排空协议，处理中断仍按现有持久化/UNKNOWN 合同恢复。进程身份不匹配时拒绝操作，不按端口杀进程。

本轮实际配置 `cloud_allowed=false`，日志/临时文件在 `E:\WK\run\p81-retirement-20261018\application`；页面为 `http://127.0.0.1:56426`，API 为 `http://127.0.0.1:56425`。本地配置含数据库连接凭据，仅保存在 E 盘，不提交到 Git。

## 配置

参考 [无密钥模板](local-runtime.example.json)，将绝对路径、现有容器完整 ID、镜像、外部卷、数据地址与当前机器事实核对后，另存 D/E 盘本地 JSON。`python`、`node` 必须为现有运行文件；依赖先正常安装完成，入口不自动安装。状态和缓存目录必须在 D/E 盘。`environment` 明确指定新链变量，不能回退旧 `RAG_DATABASE_URL`。不要复制或打印 API Key；模型凭据继续由已有正式模型配置接口加密保存，`SECRET_KEY_FILE` 只保存其原有文件路径。

必要变量：`WEKNORA_PORT_DATABASE_URL`、`DATABASE_NAME`、`DATABASE_SYSTEM_ID`、`REDIS_URL`、`REDIS_PREFIX`、`STORAGE_ROOT`、`SECRET_KEY_FILE`（短名均加 `WEKNORA_PORT_`）；资源身份与存储 ownership marker不匹配会拒绝。启动不会修改模型角色、业务 Schema、旧 UNKNOWN 或历史评测。

前端只支持显式 `VITE_WEKNORA_API_BASE`，此入口固定为配置中的新 API 地址。手动同源代理须同时显式指定 `/api/v1` 和 `VITE_API_PROXY_TARGET`。未配置或 `/api/v1/runtime` 未返回 `data.implementation=weknora-python-port` 时显示错误、拒绝业务访问，无旧8000/8080默认回退。`/healthz` 是服务存活；`/readyz` 是已完成运行资源装配，不代表模型质量验收或允许外发。

## OCR 和 Worker

`WEKNORA_PORT_MINERU_ENABLED=1` 及显式 MinerU Python、ONNX 模型根目录、临时目录为正式入口必需。图片和纯扫描 PDF 使用本地 MinerU 4.0.12 Basic；普通文字 PDF保留原生提取，混合 PDF 中扫描页自动 OCR仍不支持。独立 Caption配置保留，是否运行仍受具体资源流程及外发权限约束；Tesseract不再是默认、备用或失败回退。

API负责正常上传/同步解析索引、会话、SSE及源文回读；需要后台图像任务时持久化后由 Worker 消费。Worker入口为 `python -B -m backend.weknora_port.worker`，只领取同一已有本地 principal的图片任务和同 Redis前缀的记忆任务。MinerU已完成解析的图片不会重复派给Tesseract。

云端外发仍取决于全局及KB许可、已配置模型身份和现有实际发送 Gate；本入口不恢复已退役的 ApprovedTokenAuthority/月度账户预留系统，也不自行开启云端开关。

## 日常云问答：明确许可后启用

默认 `WEKNORA_PORT_CLOUD_ALLOWED` 为 `false`。启动、检查和打开页面都不会自动调用模型。使用现有正式模型设置和同一个日常 principal，不需要 M3.5 实验执行器。

1. 在工作台“模型设置”检查已有 embedding、rerank、cheap、expensive、VLM 配置和凭据状态。留空密钥保留现有凭据，不复制数据库密文、不将密钥放入 local.json。DYNAMIC 可能使用 Expensive，许可必须覆盖实际可能发送的角色；未覆盖时门禁拒绝，不偷偷换模型。
2. 在“资料库”选择需要的知识库，明确勾选“允许使用云端模型”。这许可问题、检索片段、图片及派生内容外发。选择多个 KB 或使用附带来源的历史/记忆时，每个真实来源均须可读且允许外发；许可不会扩大检索范围。
3. 明确同意云端处理后，在 D/E 盘**本机** local.json 的 `environment` 设置 `"WEKNORA_PORT_CLOUD_ALLOWED": "true"`，按下文选择普通模式或受限模式，然后执行 `wk.ps1 stop`、`wk.ps1 check`、`wk.ps1 start`。编辑磁盘文件不会改变已启动进程；不要同时启动第二套 API/Worker。`check` 输出 `cloud_allowed`，仍不发送模型请求。
4. 通过同一工作台提问。上传、摘要、Embedding、记忆抽取、Rewrite、Rerank、最终生成都受发送门禁约束，并且可能分别产生调用。开启云端后 Worker 也可能处理当前身份/前缀内的待办；若许可只覆盖指定任务，须使用受限模式的 KB、模型、请求前缀范围，不应把普通模式当成任务白名单。

**普通模式**：不设置 `WEKNORA_PORT_EXECUTION_FILE`。原 SendGate 验证全局开关、全部来源 KB 许可、当前租户/用户的有效模型、文档权限、取消状态和持久化调用身份。PG `port_provider_calls` 记录真实 usage，重复操作（包括 UNKNOWN）不重发。**普通模式没有应用累计 Token／次数上限，也不保证账户总消费上限**；只有明确接受此模式时才开启。供应商账户限制、其他程序的用量由其自身负责，不能假设只有本应用消费。

**可选受限模式**：继续复用 `LocalExecution`，通过 `WEKNORA_PORT_EXECUTION_FILE` 指向 D/E 盘的独立本机许可 JSON；API 和 Worker 使用同一文件，保留原 SendGate 对象。参考 [默认关闭的许可模板](local-cloud-limit.example.json)，填入 `wk.ps1 usage` 输出的真实 tenant、subject、KB 与 registry model ID，以及明确许可的 provider/model/capability；确认后才将模板副本的 `status` 从 `CLOSED` 改为 `OPEN`。模板不是授权，也不会自动安装或启用。

- 单个文件最多 12 次 Provider、100,000 Token，可设更低上限。每次发送先原子保留“实际请求 UTF-8 字节数＋完整输出硬上限＋1024”，完成后只按已报告且成功持久化的真实 Token 结算；预留值不是实际消耗。所有角色/辅助调用/失败都占用，不按用户提问次数计算。
- 受限模式仅接受当前有硬输出合同的 SiliconFlow/DeepSeek 纯文本 chat，以及 SiliconFlow 文本 Embedding/Rerank；图片、未知协议、未知输出上界等返回 `LOCAL_EXECUTION_BOUND_UNVERIFIABLE`，在发送前拒绝。普通模式的既有能力未变，不能将受限模式宣称为所有多模态请求的可靠限额器。
- `allowed_request_prefixes: ["*"]` 明确允许该许可范围内任意非空 request ID，适用于日常 UI 的随机 UUID；需要限定任务时填写已知前缀。无论前缀如何，租户、用户、KB、provider/model 和 registry model ID 都须匹配。更换模型配置产生新 ID 时不自动继承许可。
- 重启保留已用次数、真实 Token 和未决预留，不自动充值。活的 API/Worker 的 INFLIGHT 不会因另一进程装配误停；孤立/未知进程、UNKNOWN、usage 无法确认、账本损坏或结算异常均拒绝新增发送。取消不能证明供应商未执行，不能退还不确定占用。
- `capture_evidence` 默认 `false`，不额外保存完整 Prompt/回答；PG 仍保留正常用量记录。不要清空 `entries`、删除旧 UNKNOWN、将 STOP 直接改 OPEN 或复用已关闭的 M3.5 文件。新的额度需要新的明确许可，旧账本完整保留；没有自动异常释放功能。

## 关闭、额度与用量

关闭某个 KB：在资料库取消其云许可，后续发送重新检查，立即拒绝新发送。关闭全局：先在应用内取消正在运行的任务，空闲后 `wk.ps1 stop`，把本机开关改回 `false`，再 `start`。已有在途请求不因撤销许可而被宣称未执行；必要时保留 UNKNOWN。受限许可改为 `CLOSED` 后也会拒绝新增发送；不会删除历史占用。

`wk.ps1 usage -Config ...` 是只读命令：核对 PG system ID/库名和本地 principal，查询该 tenant/subject 的 `port_provider_calls`，按模型/能力显示已报告的实际输入、输出和总 Token；只报告 total 的回执不伪造输入/输出拆分。UNKNOWN outcome 与缺失/异常 usage 单列，不能当作免费。该命令同时列出当前用户可用的 registry 模型 ID、KB 和云许可，不返回密钥。它可在 API/Worker 停止时执行，不连接模型或启动容器；数字是当前用户的历史累计，**不是本轮新增消耗**。受限模式的预留另外保存在许可文件的 `entries[].held_tokens`，与实际 Token 分开查看。

常见提示：全局关闭→检查 local.json 并重启；KB 禁止→检查来源 KB 的许可；模型不可用→检查正式模型设置；限额不足→停止新增调用并核对已用/预留；UNKNOWN/结算失败→保留账本，核实原运行状态，不能重复发送不确定请求。HTTP、SSE、失败历史均显示明确错误；刷新/回读原运行不会获得新的发送许可。

usage 的 registry 清单可能包含历史 active 行；登记/启用状态不表示它就是当前角色绑定，也不证明凭据已实测可用。应与正式模型设置的 source/provider/model 和实际 registry 身份核对，不按同名配置继承许可。

## 数据与旧入口

现有正式依赖为 PostgreSQL `127.0.0.1:56423`（system ID `7696088403504697382`）及 Redis `127.0.0.1:56424/15`。保留 external volumes `weknora-python-resume-pg-20261013`、`weknora-python-resume-redis-20261013`。不能清空、重建或用旧服务连接这些资源。

`scripts/start_backend.py` 已改为退役提示并以非零退出；正常入口不再选择 legacy React。旧 Compose、旧 backend.app 服务及旧运行报告仅作历史/回滚材料，不参与正式启动。`scripts/eviroute-rag-dependencies.ps1` 为数据服务维护工具，start仅执行已有Compose容器的start，不创建替代容器；日常仍使用 `wk.ps1`。

容器删除、卷删除、旧库清理均未执行，也不由上述命令授权。详见 [本轮退役结果](milestones/P8_1_ENTRYPOINT_RETIREMENT.md)。历史 [旧入口说明](LEGACY_ENTRYPOINTS.md) 保留其历史事实，已失效操作以本页最新入口为准。
