# EviRoute RAG — 唯一本机运行入口

正式主链只有：**React `WeKnoraPythonApp` → `backend.weknora_port` FastAPI → 同模块 Worker → 已核实的 PostgreSQL/Redis**。固定 WeKnora 提交与业务模型、检索参数保持原样。API、Worker、React 当前运行在 Windows 主机；Docker 分组 `eviroute-rag` 仅包含现有数据依赖。

## 启动、检查、停止

在工作树根目录运行：

```powershell
.\wk.ps1 check  -Config E:\WK\run\p81-retirement-20261018\local.json
.\wk.ps1 start  -Config E:\WK\run\p81-retirement-20261018\local.json
.\wk.ps1 status -Config E:\WK\run\p81-retirement-20261018\local.json
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

## 数据与旧入口

现有正式依赖为 PostgreSQL `127.0.0.1:56423`（system ID `7696088403504697382`）及 Redis `127.0.0.1:56424/15`。保留 external volumes `weknora-python-resume-pg-20261013`、`weknora-python-resume-redis-20261013`。不能清空、重建或用旧服务连接这些资源。

`scripts/start_backend.py` 已改为退役提示并以非零退出；正常入口不再选择 legacy React。旧 Compose、旧 backend.app 服务及旧运行报告仅作历史/回滚材料，不参与正式启动。`scripts/eviroute-rag-dependencies.ps1` 为数据服务维护工具，start仅执行已有Compose容器的start，不创建替代容器；日常仍使用 `wk.ps1`。

容器删除、卷删除、旧库清理均未执行，也不由上述命令授权。详见 [本轮退役结果](milestones/P8_1_ENTRYPOINT_RETIREMENT.md)。历史 [旧入口说明](LEGACY_ENTRYPOINTS.md) 保留其历史事实，已失效操作以本页最新入口为准。
