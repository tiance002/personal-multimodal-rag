# Personal Multimodal RAG Knowledge Agent V1.0

一个本地优先、单机多知识库、多模态资料管理与 RAG 问答工作台。原始文件按 SHA-256 内容寻址保存，文档版本不可静默覆盖；检索范围由服务端 conversation scope 决定，引用必须能回读真实 chunk/version/locator。默认 `cloud_enabled=false`，前端不保存任何 provider 凭据。

开发状态、测试入口与产物保护见[文档索引](docs/README.md)。

## Windows 原生交付入口（待真实验收）

优先复用现有Windows Python、D原生HTML/DOCX CLI、E tessdata和明确选择的DB/storage；按[Windows原生运行与备份说明](docs/windows-native-release.md)从无应用进程状态生成并审核target，先dry-run，再单独批准启动，完成UI导入→索引→一次真实问答→引用/历史回读。最终源码冻结回归和新空目标恢复/回滚演练仍必做；当前仅离线工具验证，不是release验收。下面默认库/Compose命令仅为原有开发示例，不能作为最终交付数据源，也不能干扰raglocalfirst0930既有服务。

## 运行环境

- Python 3.11+、Docker Desktop、PowerShell
- PostgreSQL + pgvector：Compose 服务 `db`，默认 `127.0.0.1:55432`
- Ollama：当前默认 `qwen3.5:4b`（本地 Chat/Smart）与 `bge-m3:latest`（独立 Embedding，1024 维）；本机能力见 ADR-004
- 前端：Node/npm，React + Vite + TypeScript
- 扫描 PDF/图片 OCR：PyMuPDF + 本地 Tesseract `eng`/`chi_sim` 语言数据。运行 `& .\scripts\setup_ocr.ps1` 下载并校验 SHA-256；数据保存在不入库的 `var/tessdata/`。Compose worker 只读挂载此目录；未安装时明确报 `OCR_UNAVAILABLE`。

启动 Ollama 时使用可写模型目录，并保持云端关闭：

```powershell
$env:OLLAMA_MODELS = 'E:\llm_load'
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_NO_CLOUD = '1'
& 'E:\ollama\ollama.exe' serve
```

## 本地开发

本机 API 访问 Ollama 使用 `http://127.0.0.1:11434`；容器内的 `127.0.0.1` 是容器自身，Compose 通过 `COMPOSE_OLLAMA_BASE_URL`（默认 `http://host.docker.internal:11434`）访问主机 Ollama。两种模式默认 Chat 为 `qwen3.5:4b`，Embedding 为 `bge-m3:latest`。`.env.example` 中的 `OLLAMA_BASE_URL` 只供本机 API 进程使用，不会覆盖 Compose 的容器地址。

Docker Compose 会读取项目根目录 `.env` 用于变量插值；下面的原生 `uvicorn` 命令**不会自动加载 `.env`**，`Settings` 只读取启动进程的环境变量。若要自定义模型或 Ollama 地址，请先在同一 PowerShell 终端设置 `$env:OLLAMA_CHAT_MODEL`、`$env:OLLAMA_EMBEDDING_MODEL`、`$env:OLLAMA_BASE_URL` 等变量；否则使用上述默认值。

```powershell
& 'E:\Docker\DockerDesktop\resources\bin\docker.exe' compose -f deploy\compose.yml up -d db
$env:RAG_DATABASE_URL = 'postgresql+psycopg://rag:rag@127.0.0.1:55432/rag'
& .\.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade head
& .\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

另开终端启动前端：

```powershell
npm --prefix frontend run dev -- --host 127.0.0.1
```

生产式本地 Compose 会启动 API、独立 ingestion worker、前端和 PostgreSQL。需要自定义配置时先复制 `.env.example` 为 `.env`；其中 `COMPOSE_OLLAMA_BASE_URL` 是容器连接主机 Ollama 的地址：

```powershell
& 'E:\Docker\DockerDesktop\resources\bin\docker.exe' compose -f deploy\compose.yml up -d
```

访问 `http://127.0.0.1:4173`；API health 为 `http://127.0.0.1:8000/healthz`。Compose API 使用 `RAG_INLINE_INGESTION=false`，由 worker 轮询队列；本地开发默认 inline ingestion，便于无需常驻 worker 调试。

首次打开空知识库时，在页面点击“＋ 创建”，输入名称；选中新库后导入第一份 TXT/Markdown/PDF/图片。上传仅表示文件已接收，资料台会按摄取任务显示索引进度与最终状态；一次只跟踪一份正在处理的资料，完成后可继续上传，失败且仍有可用尝试次数时可点击“重试摄取”。索引显示完成后再提问，并点击回答中的 `E1` 等引用查看原文。范围同步失败或服务端范围变化时，页面会暂停问答并提示刷新，避免按错误范围发送。

### 可选 Langfuse tracing

Langfuse 默认关闭。使用 Compose 时，从 `.env.example` 复制 `.env`（`.env` 已加入忽略列表），仅在确实允许云端外发时显式设置：

```dotenv
RAG_CLOUD_ENABLED=true
RAG_LANGFUSE_ENABLED=true
RAG_LANGFUSE_CAPTURE_CONTENT=false
LANGFUSE_BASE_URL=https://us.cloud.langfuse.com
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
```

也可以提供 `LANGFUSE_AUTHORIZATION`，其值为 `Basic ` 加 Base64 编码的 `pk-lf:sk-lf`。公钥/密钥仅放在本机 `.env` 或进程环境中，不要提交到 Git 或粘贴到聊天里。对于一个回答涉及的**每个**知识库，还必须把 `cloud_allowed` 显式设为 `true`，否则该回答不会创建 Langfuse trace。`RAG_CLOUD_ENABLED=true` 是全局外发许可；正文采集仍需另行打开 `RAG_LANGFUSE_CAPTURE_CONTENT=true`。正文关闭时仍会记录模型、时延、状态和可用 token 统计，但不会导出问题、文档内容、工具参数或回答正文。

使用本地 `uvicorn` 启动时，需在启动 API 的进程环境中设置相同变量；Compose 自动从 `.env` 读取并只把这些变量传给 API。

## 主要能力

- 文本/Markdown/TXT、PDF 与图片导入；扫描页和 PDF 内嵌图片先提取原图，再以本地 OCR 生成派生文字。原图 SHA-256、页码、派生资产和 chunk 关联保存，可回读；不调用云端 VLM。OCR 无法启动报 `OCR_UNAVAILABLE`，正常运行但无文字报 `OCR_EMPTY`，均不伪装索引成功。
- 关键词 + 可选向量混合检索；L1 查询理解或回答模型失败时保留 q0，并返回可回读证据。
- Quick 使用固定 LangChain `Runnable` Chain，Smart 使用 LangChain `create_agent`；两者共用 `KnowledgeGateway`、证据覆盖、引用冻结和答案校验。Smart 默认只开放 `list_documents`、`search_knowledge`、`read_document` 三个只读知识工具；只有服务端选中的全部知识库显式启用 `graph_enabled` 时，才追加 `query_knowledge_graph`。
- 服务端确定知识库/文档范围；切换知识库或“仅此文档”会同步当前会话的 scope，并由服务端校验文档归属。SSE 运行事件、引用回读、文档右侧面板、图谱默认关闭的三栏工作台和历史会话。
- 云端能力默认关闭；若后续启用云端，月度预算通过 `RAG_MONTHLY_CLOUD_BUDGET_MICROUNITS` 配置，并在实际 provider 调用前进行原子预留。

## 验证命令

权威报告在 `var/reports/`。已实现的阶段检查命令与native验收计划入口（verify-release仅生成显式计划，真实最终回归另行批准；legacy -Fresh即时拒绝）：

```powershell
& .\scripts\verify-m0.ps1
& .\scripts\verify-m1.ps1
& .\scripts\verify-m2.ps1
& .\scripts\eval.ps1 -Mode Retrieval
& .\scripts\verify-m3.ps1
& .\scripts\verify-m4.ps1
& .\scripts\contract_test.ps1
& .\.venv\Scripts\python.exe scripts\smoke_budget.py --database-url $env:RAG_DATABASE_URL --report var\reports\smoke-budget.json
& .\scripts\verify-release.ps1 -Python $python -TargetFile $targetFile -DatabaseName $dbName -StorageRoot $storage -OutputDir $newBackupDir -RestoreTargetFile $restoreTargetFile -RestoreDatabaseName $newDbName -RestoreStorageRoot $newStorage
& .\scripts\release_report.ps1
```

前端契约与行为测试：

```powershell
npm --prefix frontend test
npm --prefix frontend run build
```

备份与恢复：

```powershell
& .\scripts\backup.ps1 -TargetFile $targetFile -DatabaseName $dbName -StorageRoot $storage -OutputDir $newBackupDir -Python $python
& .\scripts\restore.ps1 -TargetFile $restoreTargetFile -DatabaseName $newDbName -StorageRoot $newStorage -InputDir $backupDir -Python $python
```

`make` 当前不在本机 PATH，因此没有把 `make verify-*` 报告为已执行。当前项目已初始化 Git 并推送到私有仓库 [tiance002/personal-multimodal-rag](https://github.com/tiance002/personal-multimodal-rag)，初始基线 commit 为 `e52893c`，最新提交可用 `git log -1` 查看；尚未创建 tag，也未声称项目负责人已验收，最终验收和发布 tag 仍由项目负责人确认。

## Local-first V1 checkpoint (2026-09-30)

[Isolated local runbook](docs/reviews/local-first-v1-runbook.md) · [Final scope/evidence report](docs/reviews/local-first-v1-final-report.md) · [Frozen retrieval decision](docs/decisions/v1-retrieval-decision.md). Real materials and raw results stay local; this checkpoint does not imply release acceptance.
