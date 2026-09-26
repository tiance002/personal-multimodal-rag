# Personal Multimodal RAG Knowledge Agent V1.0

一个本地优先、单机多知识库、多模态资料管理与 RAG 问答工作台。原始文件按 SHA-256 内容寻址保存，文档版本不可静默覆盖；检索范围由服务端 conversation scope 决定，引用必须能回读真实 chunk/version/locator。默认 `cloud_enabled=false`，前端不保存任何 provider 凭据。

## 运行环境

- Python 3.11+、Docker Desktop、PowerShell
- PostgreSQL + pgvector：Compose 服务 `db`，默认 `127.0.0.1:55432`
- Ollama：已实测 `ornith-1.5:9b`（Chat，可选 L1）与 `bge-m3:latest`（Embedding，1024 维）
- 前端：Node/npm，React + Vite + TypeScript

启动 Ollama 时使用可写模型目录，并保持云端关闭：

```powershell
$env:OLLAMA_MODELS = 'E:\llm_load'
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_NO_CLOUD = '1'
& 'E:\ollama\ollama.exe' serve
```

## 本地开发

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

生产式本地 Compose 会启动 API、独立 ingestion worker、前端和 PostgreSQL：

```powershell
& 'E:\Docker\DockerDesktop\resources\bin\docker.exe' compose -f deploy\compose.yml up -d
```

访问 `http://127.0.0.1:4173`；API health 为 `http://127.0.0.1:8000/healthz`。Compose API 使用 `RAG_INLINE_INGESTION=false`，由 worker 轮询队列；本地开发默认 inline ingestion，便于无需常驻 worker 调试。

## 主要能力

- 文本/Markdown/TXT、正常 PDF 导入，图片源资产保留并带 locator。
- 当前本机 OCR/VLM 能力未作为可用 adapter 验证；图片导入会明确返回 `OCR_UNAVAILABLE`，不伪造文本，原始 bytes 不丢失。
- 关键词 + 可选向量混合检索；L1 查询理解或回答模型失败时保留 q0，并返回可回读证据。
- 快速检索与 bounded smart mode 共用 `RAGOrchestrator`；smart mode 只开放 `list_documents`、`search_knowledge`、`read_document`、`query_knowledge_graph` 四个只读知识工具。
- SSE 运行事件、引用回读、文档/图谱右侧面板、三栏工作台和历史会话。
- 云端能力默认关闭；若后续启用云端，月度预算通过 `RAG_MONTHLY_CLOUD_BUDGET_MICROUNITS` 配置，并在实际 provider 调用前进行原子预留。

## 验证命令

权威报告在 `var/reports/`。已实现的门禁命令：

```powershell
& .\scripts\verify-m0.ps1
& .\scripts\verify-m1.ps1
& .\scripts\verify-m2.ps1
& .\scripts\eval.ps1 -Mode Retrieval
& .\scripts\verify-m3.ps1
& .\scripts\verify-m4.ps1
& .\scripts\contract_test.ps1
& .\.venv\Scripts\python.exe scripts\smoke_budget.py --database-url $env:RAG_DATABASE_URL --report var\reports\smoke-budget.json
& .\scripts\verify-release.ps1 -Fresh
& .\scripts\release_report.ps1
```

前端契约与行为测试：

```powershell
npm --prefix frontend test
npm --prefix frontend run build
```

备份与恢复：

```powershell
& .\scripts\backup.ps1 -OutputDir var\backups
& .\scripts\restore.ps1 -InputDir var\backups\backup-<timestamp> -RestoreDatabase
```

`make` 当前不在本机 PATH，因此没有把 `make verify-*` 报告为已执行。当前项目已初始化 Git 并推送到私有仓库 [tiance002/personal-multimodal-rag](https://github.com/tiance002/personal-multimodal-rag)，初始基线 commit 为 `e52893c`，最新提交可用 `git log -1` 查看；尚未创建 tag，也未声称项目负责人已验收，最终验收和发布 tag 仍由项目负责人确认。
