# V1 Local-first 本机运行说明

本次隔离实例：数据库 127.0.0.1:25438，API 127.0.0.1:18088，UI http://127.0.0.1:14188。旧服务与公开评测数据库保留。以下 PowerShell 命令在本分支根目录执行；已有实例正在运行时不要再启动第二套。

## 已有依赖与模型

本次使用已有 Python venv、已安装前端依赖和 Docker Desktop/Ollama。检查依赖与模型：

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -m pip show fastapi sqlalchemy ollama langchain
ollama list
ollama show qwen3.5:4b
ollama show bge-m3:latest
```

不要为启动重新下载模型。本次 Qwen digest 为 `2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd`，模型完整清单保存在本地 manifest。8GB GPU 建议单请求、8192 num_ctx；不自动重启 Ollama 改全局设置。

## 数据库与 API

仅在隔离数据库未运行时启动已创建的同名项目；不要使用 down -v：

```powershell
$env:POSTGRES_PORT='25438'
docker compose -p raglocalfirst0930 -f deploy/compose.yml up -d db
```

将隔离库连接串只设置到当前 PowerShell 进程，使用已有账号；不要写入 Git 文件或聊天。下面是占位格式，不是可直接执行的凭据：

```powershell
$env:RAG_DATABASE_URL='postgresql+psycopg://<USER>:<PASSWORD>@127.0.0.1:25438/<DATABASE>'
$env:RAG_STORAGE_ROOT=(Join-Path (Get-Location) 'var/local-first/storage')
$env:RAG_CLOUD_ENABLED='false'
$env:RAG_LANGFUSE_ENABLED='false'
$env:RAG_LANGFUSE_CAPTURE_CONTENT='false'
$env:RAG_LOCAL_QUERY_ENABLED='false'
$env:RAG_LOCAL_ANSWER_ENABLED='true'
$env:RAG_INLINE_INGESTION='true'
$env:OLLAMA_BASE_URL='http://127.0.0.1:11434'
$env:OLLAMA_CHAT_MODEL='qwen3.5:4b'
$env:OLLAMA_EMBEDDING_MODEL='bge-m3:latest'
$env:RAG_RETRIEVAL_MODE='adaptive'
$env:RAG_RERANK_ENABLED='false'
$env:RAG_MMR_ENABLED='false'
$env:RAG_QUERY_REWRITE_ENABLED='false'
$env:RAG_CLOUD_FALLBACK_ENABLED='false'
& 'E:\RAG quention\.venv\Scripts\python.exe' -m alembic -c alembic.ini upgrade head
& 'E:\RAG quention\.venv\Scripts\python.exe' -m uvicorn backend.app.main:app --host 127.0.0.1 --port 18088
```

本次已迁移到 `0013_message_run_link`。默认 inline ingestion 在本次启动中完成上传；若另行关闭 inline ingestion，需启动项目现有 worker，不能把 queued 当作索引完成。

## 前端

另一个 PowerShell 窗口进入 frontend：

```powershell
$env:VITE_API_PROXY_TARGET='http://127.0.0.1:18088'
npm.cmd run build
node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port 14188 --strictPort
```

本次构建退出 0，保留既有 bundle 大小警告。依赖已存在，无须再次安装。

## 用户操作和运行记录

1. 打开 UI，进入“资料库”，创建知识库或打开现有验证库卡片。
2. 在资料区拖放 PDF/DOCX/Markdown；等待索引状态完成后提问。扫描 PDF 若缺少 OCR 语言资源应明确显示不可用，不假称已识别图中文字。
3. 选择知识库和 Quick/Smart，发送问题；点击 E1 等引用打开原文。关闭引用抽屉后可继续输入。
4. 同一会话、相同知识库/文档范围可追问；刷新页面验证历史和引用。换范围不会继承先前问题背景。
5. 浏览器 DevTools Network 中查看 messages 响应的 `trace.metrics`，或 `/api/v1/runs/<run_id>/events` 的 metrics 事件，检查真实 input/output/total tokens、检索/生成/总延迟、路线、错误和云端调用。context_tokens 缺失显示 NOT_AVAILABLE，字符数不等于 Token。
6. 失败上传可用同名正确文件形成新版本；旧失败版本保留。不要手工删除卷或索引来“修复”。

私人源文件、问题、答案、DB、截图、结果与32题集均在忽略的 var/ 内。公开 GitHub 只保留代码、脱敏汇总和文档。首批已经完成，不运行剩余公开网格或重复真实资料计分。
