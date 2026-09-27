# progress.md — 当前开发进度

- 当前里程碑：M4.5 / V1.0 质量闭环与发布验证；P0/P1 与 LangChain Smart 迁移已完成代码路径
- 当前基线：分支 `main`；2026-09-27 V1 最后定向修复从 `2c73215` 开始，实现提交 `00bbf53` 与 Compose 验证记录提交 `50a63d4` 均已推送 `origin/main`。最新执行记录见文末；历史各节保留当时状态，不代表当前验收结论。未打 release tag。
- 当前目标：个人、本地、多知识库、多模态 RAG 知识库问答 Agent V1.0。
- 架构：FastAPI + React/Vite + LangChain Runnable/create_agent + 项目 RAG Core + PostgreSQL/pgvector；AnswerService 统一运行、取消和提交，Quick 使用固定 LangChain Runnable Chain，Smart 通过 `SmartAgentPort` 调用四个项目只读工具；两者共享 `KnowledgeGateway`、EvidenceService 和答案校验。

## 本轮已完成

- P0 组合根与门禁：M1–M4/API 冒烟统一使用 `build_container`；测试通过 `create_app(..., container=...)` 注入；新增 `scripts/release_preflight.py`，无 `from_url`、旧 `app.state.store/ollama` 可执行调用。
- 摄取一致性：原子领取、`claim_token`、后台租约心跳、过期恢复、所有权栅栏和 attempts 语义；迁移头为 `0011_doc_filename_ux`。
- 数据边界：真实文档归属校验、图谱 KB/document/active-version/index/graph 状态 SQL 过滤、精确 embedding profile 过滤、每次 Smart 检索的证据累积与冻结。
- 版本与空资料：指定 `document_id` 的新版本不会按文件名创建新文档；并发版本号受文档行锁与唯一索引保护；空文本、OCR 不可用和无 chunk 不会伪装成功。
- 取消终态：RAG/Agent 取消与完成均为条件更新；取消 API 同时协调两类持久化运行；迟到模型结果不追加成功事件、证据或 assistant 消息。
- M4 架构：加入 `langchain==1.4.2`、`langchain-ollama==1.1.0`；删除旧 `agent_runtime.py`；Smart 使用 LangChain `create_agent`、四个闭集只读工具、服务端 Scope/Run/Evidence 上下文和限制/取消检查。
- Compose 前端：Vite `preview.proxy` 与 `deploy/nginx/nginx.conf`；前端镜像改为 Node 构建阶段 + Nginx 运行时，`/api/`、`/healthz` 同源代理并关闭 SSE buffering。

## 已执行证据

| 检查 | 结果 |
|---|---|
| `\.venv\Scripts\python.exe -m pytest -q --tb=short` | PASS，91 passed |
| `\.venv\Scripts\python.exe -m compileall -q backend scripts` | PASS，exit 0 |
| `\.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade head` | PASS，exit 0；`0011_doc_filename_ux (head)` |
| `scripts\verify-m1.ps1` | PASS；真实 PostgreSQL、Ollama Quick、API/SSE |
| `scripts\verify-m2.ps1` | PASS；PDF 与 OCR_UNAVAILABLE 语义 |
| `scripts\verify-m3.ps1` | PASS；图谱、评测与失败隔离 |
| `scripts\verify-m4.ps1` | PASS；LangChain 导入、确定性 M4 API、证据回读 |
| `scripts\smoke_langchain_ollama.py` | PASS；`ornith-1.5:9b` tools、JSON 参数、多轮终止 |
| `scripts\smoke_m4.py --real-model --report var\reports\smoke-m4-real.json` | PASS；真实 Ollama Smart API，3 次只读工具调用，Agent completed，E1 回读 200 |
| `scripts\smoke_m1.py --database-url ... --real-model --report var\reports\smoke-m1-final.json` | PASS；租约心跳后真实 PostgreSQL/pgvector 摄取、Quick 回答和 E1 回读 |
| `npm run build`（frontend） | PASS |
| `docker compose -f deploy\compose.yml config` | PASS |
| Vite preview `/api/v1/knowledge-bases` HTTP 代理 | PASS，HTTP 200 |
| `docker compose ... build frontend` | FAIL/环境阻断；Docker Hub token 网络连接失败 |
| Playwright 浏览器级脚本 | PASS；使用已安装系统 Chrome，Vite preview `/api/v1` HTTP 200，前端 UI 冒烟通过 |
| `verify-release.ps1` 完整发布门禁 | NOT RUN；需先恢复 Docker 镜像拉取与浏览器运行时 |

## RAG 质量阶段（2026-09-26）

- 默认本地 Chat 模型改为 `qwen3.5:4b`；Embedding 保持独立的 `bge-m3:latest`。Quick 先规则规划和混合检索，查询扩展默认关闭，仅在无候选且显式开启后按需运行。
- 明确并列与成本差额问题拆为证据目标，关系问题不拆为两个答案；缺项最多一次定向补检。单个目标缺失时输出只引用已证实目标的部分回答；全部缺失时拒答。
- Quick/Smart 都在提交前校验证据覆盖、引用标签、对象/字段/金额关系；失败不持久化助手答案或未使用的引用。Smart 搜索工具提供服务端 E 标签，真实 Qwen 连续三次引用冒烟通过。
- Markdown 标题栈与 PDF chunk 页码已修正；新增文档画像与 `adaptive/v1` 策略切块。迁移 `0012_chunk_strategy` 记录 parser/chunker/实际策略；历史版本标注 `legacy/fixed-v1`。
- 固定 `quality-v1` 九场景数据集保留原必需字段，并采用显式版本化 Schema；覆盖简单、并列、干扰、部分缺失、无证据、跨文档、关系与差额。相关文件见 `evaluations/quality_v1.jsonl` 和 ADR-004。

| 本阶段命令 | 结果 |
|---|---|
| `\.venv\Scripts\python.exe scripts\model_probe.py --chat-model qwen3.5:4b --embedding-model bge-m3:latest --report var\reports\model-probe-qwen35-4b.json` | PASS；JSON 合法，BGE 1024 维 |
| `\.venv\Scripts\python.exe scripts\smoke_langchain_ollama.py --chat-model qwen3.5:4b --report var\reports\smoke-langchain-qwen35-4b.json` | PASS；真实工具调用 |
| `\.venv\Scripts\python.exe -m alembic upgrade head` | PASS；`0012_chunk_strategy` |
| `\.venv\Scripts\python.exe scripts\validate_eval.py --path evaluations\quality_v1.jsonl --schema quality-v1 --report var\reports\eval-quality-schema.json` | PASS；9 行 |
| `\.venv\Scripts\python.exe scripts\evaluate_rag_quality.py --report var\reports\eval-rag-quality.json` | PASS；9/9；确定性夹具，无 Chat 模型 |
| `\.venv\Scripts\python.exe scripts\smoke_quality_postgres.py --database-url postgresql+psycopg://rag:rag@127.0.0.1:55432/rag --report var\reports\smoke-quality-postgres.json` | PASS；真实 PostgreSQL/BGE/Qwen；双文档完整回答 1 次 Chat，缺证据与文档范围部分回答 0 次 Chat |
| `\.venv\Scripts\python.exe scripts\smoke_m1.py --real-model --database-url postgresql+psycopg://rag:rag@127.0.0.1:55432/rag --storage-root var\smoke-m1-qwen-storage --report var\reports\smoke-m1-qwen.json` | PASS；真实 Quick 与 E1 回读 |
| `\.venv\Scripts\python.exe scripts\smoke_m4.py --real-model --database-url postgresql+psycopg://rag:rag@127.0.0.1:55432/rag --report var\reports\smoke-m4-qwen.json` | PASS；真实 Smart、2 次 Chat、E1 回读；连续 3 次相同冒烟通过 |
| `\.venv\Scripts\python.exe -m pytest -q --tb=short -p no:cacheprovider` | PASS；117 passed（后续质量 Schema/标签测试的最终全量回归待重跑） |
| `\.\scripts\contract_test.ps1 -Python (Join-Path (Get-Location) '.venv\Scripts\python.exe')` | PASS；OpenAPI/SSE 契约 |
| `$env:PLAYWRIGHT_CHROME_PATH='C:\Program Files\Google\Chrome\Application\chrome.exe'; npm --prefix frontend test` | PASS；4 个浏览器用例 |
| `scripts\preview_proxy_smoke.py`（通过 `with_server.py` 启动 API + Vite preview） | PASS；浏览器同源 `/api/v1` 200；`var/reports/preview-proxy-smoke.json` |
| `scripts\playwright_smoke.py`（通过 `with_server.py` 启动 API + Vite dev） | PASS；上传、选中文档、图谱页签；`var/reports/frontend-smoke.json` |

## LangChain 编排收敛（2026-09-26）

- Quick 不再依赖自研 `RAGOrchestrator`；新增 `KnowledgeGateway`、`EvidenceService`、`QueryPlan`、`EvidenceBundle` 和 `AnswerResult` 共享契约，新增 `LangChainQuickChain` 以 `RunnableLambda` 固定编排规划、检索、Coverage、证据上下文、模型调用和答案校验。
- Smart 的四个 LangChain 工具改为通过同一个 `KnowledgeGateway` 检索；`LangChainAgentAdapter` 使用同一 `EvidenceService` 的覆盖与答案验证，不再重复构造 `QueryRouter`、`QualityGate`、`AnswerValidator`。
- `AnswerService` 现在只负责统一 run 生命周期、取消检查、证据/引用提交和最终状态；组合根一次构造 `KnowledgeGateway` 与 Quick Chain，API 不再每次请求创建检索器和旧编排器。独立 `local_query_gateway` 会显式传入 Quick Chain。
- 预算结算异常不会再降级成成功的证据回答；确定性质量评测通过 `EvidenceResolver` 校验 quote hash 与 version/chunk 可读性。
- `backend/app/application/rag_orchestrator.py` 已删除；相关测试和 M1/质量评测脚本已迁移到 Quick Chain。

| 收敛验收命令 | 退出码与结果 |
|---|---|
| `.\\.venv\\Scripts\\python.exe -m pytest -q --tb=short -p no:cacheprovider` | 0；137 passed，6 warnings |
| `.\\.venv\\Scripts\\python.exe -m compileall -q backend scripts` | 0；语法检查通过 |
| `.\\.venv\\Scripts\\python.exe scripts\\evaluate_rag_quality.py --report var\\reports\\eval-rag-quality-langchain-quick-final.json` | 0；9/9，引用 hash/version 回读通过 |
| `.\\.venv\\Scripts\\python.exe scripts\\contract_test.py --report var\\reports\\contract-test-langchain-convergence-final2.json` | 0；OpenAPI/SSE/分层契约 PASS |
| `"E:/RAG quention/.venv/Scripts/python.exe" scripts/smoke_m1.py --real-model --database-url postgresql+psycopg://rag:rag@127.0.0.1:55432/rag --storage-root var/smoke-m1-storage-langchain-quick-final --report var/reports/smoke-m1-langchain-quick-final.json` | 0；真实 PostgreSQL/pgvector/Qwen Quick、E1 回读 PASS |
| `"E:/RAG quention/.venv/Scripts/python.exe" scripts/smoke_quality_postgres.py --database-url postgresql+psycopg://rag:rag@127.0.0.1:55432/rag --report var/reports/smoke-quality-postgres-langchain-quick-final.json` | 0；复合、缺证据、文档范围部分回答 PASS |
| `"E:/RAG quention/.venv/Scripts/python.exe" scripts/smoke_m4.py --real-model --database-url postgresql+psycopg://rag:rag@127.0.0.1:55432/rag --report var/reports/smoke-m4-langchain-convergence-final.json` | 0；真实 LangChain Smart、工具调用、Agent 终态、E1 回读 PASS |

## 风险与下一步

- P0/P1 代码与真实 PostgreSQL/Ollama/Chrome 浏览器关键路径已有证据；完整 Compose/Nginx 镜像构建仍因 Docker Hub 令牌连接超时而未通过。缓存的 `deploy-frontend:latest` 是旧 `vite preview` 镜像，不能冒充当前 Nginx 构建。
- 当前 `.venv` 使用 `include-system-site-packages = true`；`pip check` 报告本机外部 `cn-mail-agent`/`langchain-openai` 与 LangChain 1.x 的冲突。项目自身声明只锁定 `langchain==1.4.2` 与 `langchain-ollama==1.1.0`，在 Docker 的干净环境应重新安装验证。
- 扫描 PDF、PDF 内嵌图片和独立图片已接入本地 PyMuPDF/Tesseract OCR；扫描页/原图与派生文字通过资产 ID、SHA-256、页码和 `chunk_assets` 关联。空白材料报 `OCR_EMPTY`，缺少语言包报 `OCR_UNAVAILABLE`。OCR 语言包保存在不入库的 `var/tessdata/`，Compose worker 只读挂载；VLM caption 尚未实现。
- `QualityGate` 对可确定的数值目标与关系做保守检查；任意开放式推理的语义支持检验尚未实装。九场景夹具与两文档真实冒烟不足以证明普遍的回答质量。
- 未创建 release tag；项目负责人需复核 `var/reports/` 与本文件后决定提交、验收和 tag。

## OCR 与完整门禁续验（2026-09-26）

- 扫描 PDF、PDF 内嵌图片及独立图片的真实本地 OCR 已接入；原始图像 SHA-256、页码、派生 OCR 资产与 chunk 关联写入 PostgreSQL。`scripts/setup_ocr.ps1` 校验英中语言包，`verify-m2.ps1` 将其作为必需检查。
- 完整发布门禁重新执行后，18/19 项检查退出码 0，唯一失败是 `compose_frontend_build`：Docker Hub `auth.docker.io/token` TCP 连接失败，当前 Nginx 前端镜像未能构建。不要将 Vite preview 或旧缓存镜像视为 Compose 通过。

| 本轮执行命令 | 退出码与结果 |
|---|---|
| `& .\scripts\setup_ocr.ps1` | 0；`eng`/`chi_sim` SHA-256 匹配 |
| `.\.venv\Scripts\python.exe scripts\probe_pdf_ocr.py --tessdata var\tessdata --report var\reports\probe-pdf-ocr.json` | 0；纯图 PDF OCR 得到 `OCR TEST 1234` |
| `.\.venv\Scripts\python.exe -m pytest -q --tb=short -p no:cacheprovider` | 0；最终 125 passed（新增缺失 OCR 数据边界测试）；发布报告内较早的同命令为 124 passed |
| `.\.venv\Scripts\python.exe -m compileall -q backend scripts` | 0；语法检查通过 |
| `git diff --check` | 0；无空白错误，只有 Git CRLF 提示 |
| `.\.venv\Scripts\python.exe -m pytest backend/tests/test_postgres_ocr_lineage.py -q --tb=short -p no:cacheprovider` | 0；真实 PostgreSQL 资产血缘/回读通过 |
| `& .\scripts\verify-m2.ps1` | 0；OCR 数据、9 项测试、真实数据库冒烟通过 |
| `& .\scripts\verify-m4.ps1` | 0；11 项测试、LangChain 导入、确定性 Smart API/E1 回读通过 |
| `docker compose -f deploy\compose.yml config --quiet` | 0；Compose 配置有效 |
| `& .\scripts\verify-release.ps1 -Fresh` | 1；18 项通过、Compose 前端镜像构建失败，详见 `var/reports/verify-release.json` |
| `& .\scripts\release_report.ps1` | 1；`INCOMPLETE`，详见 `var/reports/v1-release-report.md` |

遗留：需恢复 Docker Hub 基础镜像访问后重新构建并实际启动当前 Nginx Compose 前端，再完成浏览器同源验证。VLM caption 与更广泛的语义证据判定未实现；当前 OCR 与保守数值/关系校验不能当作所有多模态与开放式质量问题已验收。未提交、未打 tag，负责人尚未批准发布。

## V1 Goal continuation — final local delivery (2026-09-26)

本节覆盖本次 Goal 的最新工作树与真实命令结果；早期“Docker Hub 构建失败”的记录属于当时环境状态，不覆盖本节最终证据。

### 实际改动

- 图谱默认关闭并端到端隔离：HTTP 图谱读取/重建返回结构化 `GRAPH_DISABLED`；图谱 SQL 增加非删除知识库及 `graph_enabled = TRUE` 防线；Smart 默认绑定三个只读工具，只有选中范围内全部知识库显式开启图谱时才加入 `query_knowledge_graph`；前端默认隐藏图谱入口但保留文档回读。
- 会话 scope 由服务端校验：创建/修改会话时验证文档归属及知识库范围；前端切换知识库、历史会话恢复和“仅此文档”均同步当前会话的 `knowledge_base_scope`/`document_scope`。
- 新增后端与前端回归测试；修正 `verify-m1.ps1` 中已删除旧测试文件的引用；真实 UI/Compose smoke 明确使用 graph-enabled 测试知识库；发布脚本等待四个 Compose 服务均进入 running。
- README 与 ADR-003 已同步默认关闭图谱、Smart 工具和服务端 scope 契约；交付报告见 `docs/reports/v1-delivery-report.md`。

### 最终命令证据

| 命令 | 退出码与结果 |
|---|---|
| `\.venv\Scripts\python.exe -m pytest -q --tb=short -p no:cacheprovider` | 0；141 passed，6 个既有弃用警告 |
| `\.venv\Scripts\python.exe -m compileall -q backend scripts` | 0；语法检查通过 |
| `npm --prefix frontend run build` | 0；TypeScript/Vite 生产构建通过 |
| `$env:PLAYWRIGHT_CHROME_PATH='C:\Program Files\Google\Chrome\Application\chrome.exe'; npm --prefix frontend test -- --reporter=line` | 0；7 个浏览器测试通过 |
| `& .\scripts\verify-m0.ps1` | 0；M0 PASS，真实迁移/模型探针/全量后端测试 |
| `& .\scripts\verify-m1.ps1` | 0；M1 PASS，真实 PostgreSQL/Ollama/API/SSE；27 项门禁测试 |
| `& .\scripts\verify-m2.ps1` | 0；M2 PASS，PDF/OCR/语言包/真实冒烟 |
| `& .\scripts\verify-m3.ps1` | 0；M3 PASS，20 条评测 Schema/检索和图谱回读 |
| `& .\scripts\verify-m4.ps1` | 0；M4 PASS，12 项 Smart/Agent 边界测试和冒烟 |
| `& .\scripts\contract_test.ps1` | 0；OpenAPI/SSE/分层契约 PASS |
| `& .\scripts\verify-release.ps1 -Fresh` | 0；`V1.0_RELEASE PASS`，Compose 构建、四服务运行、Nginx 浏览器、前端、备份/恢复均 PASS |
| `& .\scripts\release_report.ps1` | 0；`var/reports/v1-release-report.md/.json` status PASS |
| `Makefile` / `make verify-*` | NOT IMPLEMENTED；仓库没有 Makefile，未将其写成通过 |

### 当前验收状态

- 代码与真实本机/Compose 证据达到本次 Goal 的 V1.0 本地交付条件；图谱为明确 opt-in，普通 ingestion/RAG 不依赖图谱。
- 工作树仍有未提交实现变更；未创建 Git tag，项目负责人仍需审阅 `docs/reports/v1-delivery-report.md` 与 `var/reports/verify-release.json` 后决定提交、验收和 tag。
- VLM caption、广泛开放式语义证据判断仍未实现，作为 V1 范围外/后续增强记录，不伪装为当前能力。

## 当前续验诊断（2026-09-26）

- 复核当前 `deploy/Dockerfile.frontend` 后确认需要的 `node:22-alpine`、`nginx:1.27-alpine` 均不在本机镜像缓存。旧 `deploy-frontend:latest` 可运行 Node 22.23.3，缓存 `nginx:alpine` 为 Nginx 1.31.6，但二者不是当前指定基底。
- `curl.exe --silent --show-error --connect-timeout 5 --max-time 12 --output NUL --write-out 'host_http=%{http_code} remote=%{remote_ip}' https://auth.docker.io/token`：退出码 28，连接超时；同类命令请求 `https://mirror.gcr.io/v2/` 亦超时。Docker daemon 代理为 `http.docker.internal:3128`，没有 registry mirror。此轮没有改变全局网络或 Docker daemon 设置。
- `npm --prefix frontend run build`：退出码 0，Vite 构建完成。`docker run --rm --pull=never deploy-frontend:latest sh -c 'npm ci --offline --ignore-scripts --no-audit --no-fund ...'`：退出码 0，仅为离线可行性探测。
- 当前发布状态依然 `FAIL`，未重新运行完整门禁；是否接受一个保留官方默认基底、仅用于本机验证的 build-arg 覆盖方案，待负责人决定。未提交、未打 tag。

## 取消竞态与发布门禁续验（2026-09-26）

- `AnswerService` 观察到外部取消后不再重复追加 `run.failed`；取消仓储在同一事务内更新 RAG/Agent 终态并写一次取消事件。修正真实 PostgreSQL 测试清理顺序，并新增 5 轮同时取消/Smart 提交测试，检查双表终态、事件和助手消息一致。
- `\.venv\Scripts\python.exe -m pytest backend/tests/test_cancellation_boundaries.py backend/tests/test_postgres_run_terminal_states.py -q --tb=short -p no:cacheprovider`：修正测试清理后退出码 0，8 passed。
- `\.venv\Scripts\python.exe -m pytest -q --tb=short -p no:cacheprovider`：退出码 0，131 passed，6 warnings。
- 发布门禁新增隔离的 Compose API/Worker/Nginx 构建、运行健康和浏览器同源检查。浏览器脚本可指定 base URL；Vite preview 参数化 UI 冒烟退出码 0，明确要求 Nginx 的负例退出码 1，防止将 Vite 冒充 Compose。
- `& .\scripts\verify-release.ps1 -Fresh`：退出码 1；`compose_stack_build` 因 Docker Hub OAuth token 连接失败，`compose_runtime_browser` 明确 `NOT RUN`；其余执行项退出码 0，报告 `var/reports/verify-release.json`。`& .\scripts\release_report.ps1`：退出码 1，`INCOMPLETE`，Compose 浏览器报告均 `NOT RUN`。
- 未修改系统 DNS/Docker daemon；未提交、未打 tag。当前镜像获取与真实 Compose 运行仍是发布阻断，需恢复官方基础镜像访问后重跑。

## V1 定向修复与验收（2026-09-27）

### 1. 目标与改动

- 目标：基于审查基线 `2e46cad65747f865bd9bbb7a498c98653cbc6a3c` 处理 P1-1 摄取任务刷新恢复、P1-2 历史引用恢复、P2 消息异步顺序与切库旧消息，并完成真实验收。未新增任务框架、未重构会话系统、未打 Tag、未删除日常数据。
- `backend/app/adapters/postgres/knowledge_repository.py`
  - `list_documents` 增加 `latest_version_no/latest_index_status/latest_version_id` 与 `latest_job{id,status,stage,progress,error_code,attempts,max_attempts}`（LATERAL 取每份资料最新版本与最新摄取任务）。活动版本仍为 `version_no/index_status`，新版本失败不再被旧版本 `ready` 掩盖。**未新增路由，避免 OpenAPI 契约漂移。**
  - `finalize_answer` 写入助手消息时记录 `run_id`；`list_messages` 返回 `run_id`，并从冻结的 `answer.completed` 事件回读 `citations` 标签（不解析答案文本）；`append_message` 增加可选 `run_id`。
- `backend/app/ports/persistence.py`：`append_message` 协议增加可选 `run_id`（默认 None，兼容既有内存假实现）。
- `alembic/versions/0013_message_run_link.py`（新增）：`conversation_messages.run_id`（可空 UUID）→ `rag_runs.id` 外键 + 索引；**纯增量、无数据删除**。
- `frontend/src/app/state.ts`：`DocumentItem` 增加 `latest_version_no/latest_index_status/latest_job`。
- `frontend/src/app/App.tsx`：历史消息加载增加取消守卫（防乱序覆盖）；切换知识库清空当前展示消息（防旧消息混杂）；新增刷新后从资料列表恢复最新非终态/失败任务（含重试入口），一旦本会话已持有该库任务则不再被资料列表覆盖；`retryIngestion` 支持按 jobId 重试。
- `frontend/src/components/KnowledgeBasePanel.tsx`：资料行展示“新版本 N · failed · 错误码”并给出“重试索引”入口。
- `backend/tests/test_postgres_run_terminal_states.py`：修正 3 处清理顺序（先删 `conversation_messages` 再删 `rag_runs`），以适配新外键。
- 新增测试：`backend/tests/test_ingestion_recovery.py`、`backend/tests/test_message_run_link.py`、`frontend/tests/ingestion-refresh-recovery.spec.ts`、`frontend/tests/history-citation.spec.ts`、`frontend/tests/stale-message-scope.spec.ts`；新增脚本 `scripts/smoke_v1_repair.py`。

### 2. 执行命令（真实执行，含退出码）

- `git rev-parse HEAD` → `2e46cad65747f865bd9bbb7a498c98653cbc6a3c`（修复改动为工作区未提交变更）。
- 隔离库 `rag_v1_accept`（同一 pgvector 实例、独立 database）：`RAG_DATABASE_URL=.../rag_v1_accept python -m alembic -c alembic.ini upgrade head` → 退出码 0，`0013_message_run_link (head)`。
- `RAG_DATABASE_URL=.../rag_v1_accept python -m pytest backend/tests/test_message_run_link.py backend/tests/test_ingestion_recovery.py -p no:cacheprovider` → 退出码 0，`2 passed`。
- `RAG_DATABASE_URL=.../rag_v1_accept python -m pytest backend/tests -q -p no:cacheprovider` → 退出码 0，`146 passed`，6 warnings。
- `python scripts/contract_test.py --report var/reports/contract-test-v1-repair.json` → 退出码 0，`status=PASS`，`openapi_snapshot_drift=false`，`layering_violations=[]`，`required_paths=20 / actual_paths=21`。
- `npm --prefix frontend run build` → 退出码 0（`tsc --noEmit` + Vite 构建）。
- `npx playwright test --reporter=list --timeout=25000`（`PLAYWRIGHT_CHROME_PATH` 指向系统 Chrome）→ 日志 `var/reports/playwright-v1-repair.log` 中 22 个用例全部 `ok`（无 failed/skipped）。**注意**：该进程在 Windows 上于 Playwright 的 webServer teardown 阶段长时间不退出（已知环境现象），故未捕获到汇总退出码，最终由人工终止；测试结果本身以日志逐条 `ok` 为准。
- `python scripts/smoke_v1_repair.py --database-url .../rag_v1_accept --real-model --report var/reports/smoke-v1-repair.json` → 退出码 0，`status=PASS`（真实 qwen3.5:4b + bge-m3:latest）。
- 隔离 Compose 全栈（项目名 `rag-v1-accept`，端口 55433/18001/14174）：`docker compose -p rag-v1-accept -f deploy/compose.yml build api worker frontend` → `build_exit=0`；`up -d --no-build` → `up_exit=0`，`healthz_ready_attempt=2`；运行服务 `db, api, frontend, worker` 全部 running；`preview_proxy_smoke.py --expect-server nginx` → `proxy_exit=0`；`playwright_smoke.py` → `ui_exit=0`；`down --volumes` → `down_exit=0`。日志 `var/reports/compose-verify-v1-repair.log`，报告 `var/reports/compose-proxy-smoke-v1-repair.json`、`var/reports/compose-ui-smoke-v1-repair.json`。
- 开发库增量迁移（必要且非破坏）：`python -m alembic -c alembic.ini upgrade head` → 退出码 0，`0013_message_run_link (head)`；迁移后 `knowledge_bases=465 / documents=627 / conversation_messages=108`，日常数据未丢失。

### 3. 结果

- P1-1 摄取任务刷新恢复：**PASS**。`smoke-v1-repair.json` 显示首份 Markdown `first_job_status=succeeded`、`active_index_status=ready`；新版本失败时 `latest_index_status=failed`、`latest_job_status=failed`、`latest_job_error=EMPTY_TEXT`、`retry_entry_available=true`、`retry_status=queued`。Playwright `ingestion-refresh-recovery` 通过（刷新后仍显示任务状态/错误/重试入口）。
- P1-2 历史引用恢复：**PASS**。`history_run_linked=true`、`history_citations=["E1"]`、`citation_readback=true`（含 locator 定位）。`get_citation` 的 quote SHA-256、版本与 chunk 可读性校验保持不变；Run 关联来自显式 `run_id` 列，未解析答案文本。Playwright `history-citation` 通过。
- P2 消息异步顺序与切库旧消息：**PASS**。Playwright `stale-message-scope`（迟到响应不覆盖新会话、切库清空旧对话）通过。
- 回归：完整后端测试 `146 passed`、契约测试 PASS、前端构建 PASS、Playwright `22 passed`、隔离 Compose（PostgreSQL + Worker + Nginx + 浏览器）PASS。全部为本轮真实执行，未复用历史报告，未使用 Mock 冒充真实验收。
- 未运行：`verify-m0..m4`、`verify-release.ps1` 全量门禁、`backup/restore` 演练（本轮不在授权范围；未声明通过）。

### 4. 风险与遗留

- `conversation_messages.run_id` 外键使“仍有助手消息引用的 run”不可硬删除；应用层与脚本均无硬删除 run 的路径，仅测试清理受影响，已修正顺序。如需维护脚本删除 run，应先解绑消息。
- 开发库已应用 `0013`（增量、可空列 + 外键 + 索引）；回滚可用 `alembic downgrade 0012_chunk_strategy`，但会丢弃消息与 run 的关联。
- 刷新恢复以“本会话是否已持有该库任务”为准：若用户在别处（非本会话）触发新版本摄取失败，资料行会显示失败与“重试索引”，但顶部横幅不会自动切换。属可接受的局部限制。
- Playwright 需系统 Chrome（`PLAYWRIGHT_CHROME_PATH`）或 `npx playwright install chromium`；本机 Playwright 1.63 期望的 `chromium_headless_shell-1243` 未安装。本轮使用系统 Chrome 完成，属环境配置项，非代码缺陷。
- 未提交、未打 Tag；是否提交与验收由项目负责人决定。

### 5. 版本与下一步

- HEAD：`2e46cad65747f865bd9bbb7a498c98653cbc6a3c`；迁移版本 `0013_message_run_link`；修复改动位于工作区未提交。
- 建议下一步（需负责人确认后执行）：审阅本轮 diff → 提交 → 在需要时打 `v1.0` 相关 Tag；随后按 V1.1 计划推进，本轮不开展 V1.1 优化。

## V1 最后定向修复（2026-09-27，覆盖上节验收结论）

- 基线 `2c73215`。`list_documents` 的最新摄取任务只从最新文档版本选取，并用任务 ID 稳定打破同时间排序；旧版本较晚创建的任务不会伪装成新版本任务。
- 前端文档列表、任务、错误及上传占用按知识库归属；异步 A 库结果无法覆盖 B 库列表，A 库任务不占用 B 库导入或重试入口。历史消息加载按会话与知识库/文档范围失效；切换历史会话立即清空旧内容，新建会话后立即发送不会被迟到的空历史记录清除。
- 真实资料摄取产生 chunk/version/locator 后冻结 E1；新仓储实例及 HTTP 历史消息、引用接口均回读同一 E1。历史 `run_id IS NULL` 的助手消息即使正文含 `[E1]` 仍返回空引用。新增 A/B 隔离、异步响应、版本任务和真实 E1 回归。原有一项测试改为尊重 `RAG_DATABASE_URL`，避免隔离库执行时错误跳过。
- 只在新建 Compose 项目 `ragv1final0927` 的 25436 端口及其临时数据库验证；没有修改 55432 日常数据库。`0012 → 0013` 迁移往返保留临时旧消息正文、`run_id NULL` 与空引用；新鲜 Worker 数据库升级到 `0013_message_run_link`。原有数据未删除。

| 本轮命令/场景 | 结果 |
|---|---|
| 定向后端 PostgreSQL 回归 | 退出码 0，3 passed；原版本任务案例先红后绿 |
| 完整 `pytest backend/tests -q --tb=short -p no:cacheprovider`（隔离库） | 退出码 0，147 passed，6 warnings，无 skipped |
| `npm --prefix frontend run build` | 退出码 0 |
| `npm --prefix frontend test -- --reporter=line`（系统 Chrome） | 退出码 0，29 passed；A/B 与消息竞态案例先红后绿 |
| `scripts/release_preflight.py`、`scripts/contract_test.py`、`compileall` | 均退出码 0；契约 PASS，无 OpenAPI drift |
| 真实 PostgreSQL/pgvector + Ollama `scripts/smoke_v1_repair.py --real-model` | 退出码 0；qwen3.5:4b/bge-m3，摄取/Quick/历史 E1 回读 PASS；Ollama 临时进程已停止 |
| 新鲜隔离库本机 Worker CLI `python -m backend.app.workers.ingestion --once` | 退出码 0；领取本次 job、`succeeded`，资料 `ready`。首次共用测试库尝试领取了旧测试排队 job 并失败，故使用独立数据库重验；两次结果均记录 |
| 当前源码 `docker compose ... build api worker frontend` | 退出码 1；Docker Hub `auth.docker.io/token` TCP 连接失败，无法取得基础镜像 metadata |
| 当前源码 Compose 全栈运行及正式 `verify-release.ps1` | NOT RUN；镜像未构建。正式脚本还固定使用 55432 日常库，不可在“保留旧数据、隔离验证”条件下原样执行。此前任何 Compose PASS 都属于旧代码/旧构建，不覆盖本次结论 |

结果日志位于 `var/reports/v1-final-*.txt/json`。本轮实现提交 `00bbf53` 已推送 `origin/main`，代码与测试回归 PASS。Compose 镜像构建与全栈运行结果以后续“Compose 全栈续验”为准；正式 V1 发布门禁未运行，不能宣称 V1 发布验收通过。未创建 release tag，未部署，未开展 V1.1。旧的 P2 语义质量/VLM caption 限制仍作为后续技术债。

## Compose 全栈续验（2026-09-27，覆盖上一节 Compose 构建结果）

- Docker Hub 访问短暂恢复后，按原始 Dockerfile 拉取 `python:3.11-slim`、`node:22-alpine`、`nginx:1.27-alpine`；`docker compose -p ragv1final0927 -f deploy/compose.yml build api worker frontend` 退出码 0。镜像内前端 `tsc --noEmit && vite build` 通过。
- 同一 Compose 源码全栈在隔离项目运行：`POSTGRES_PORT=25436`、`API_PORT=18086`、`FRONTEND_PORT=14186`。`up -d --no-build` 退出码 0；`db, api, worker, frontend` 全部 running；迁移 `0013_message_run_link`；Nginx `/healthz` HTTP 200。项目入口 `http://127.0.0.1:14186`。
- `preview_proxy_smoke.py --base-url http://127.0.0.1:14186 --expect-server nginx` 退出码 0；报告 `var/reports/v1-final-compose-proxy-smoke-clean.json`，确认 Nginx 1.27.5、同源 API HTTP 200。`playwright_smoke.py --base-url http://127.0.0.1:14186` 退出码 0，三栏 UI、资料上传可见、图谱页签和构建入口通过；报告 `var/reports/v1-final-compose-ui-smoke-clean.json`。
- 容器 API 经 `host.docker.internal:11434` 能读取本机 Ollama 模型。Playwright 上传的隔离 smoke job 由 Compose Worker 实际处理为 `succeeded/ready`，attempts=1。第一次服务演练时 Ollama 按此前的“只临时启动”约束处于停止状态，job 报 `ProviderUnavailable`；临时启动 Ollama 后重试成功。验证结束 Ollama 已停止；未更改其自启设置。Compose 栈保留运行。
- 正式 `verify-release.ps1` 仍未执行：脚本把主测试/备份恢复数据库固定到 host port 55432；`netsh interface ipv4 show excludedportrange protocol=tcp` 显示 55376–55475 为保留范围，Docker 无法绑定 55432（`ports are not available`）。为保持数据库隔离，未改 Windows 全局端口范围或脚本默认地址。尝试创建的空隔离项目 `ragv1gate0927` 已清理。
- 本节覆盖上一节“Compose 镜像构建失败”的状态：**当前源码 Compose build/runtime/Nginx 浏览器 smoke 均 PASS**。但 V1 正式发布门禁仍为 NOT RUN，不能据此宣称整套发布门禁通过。日志：`var/reports/v1-final-compose-build-final.txt`、`v1-final-compose-up.txt`、`v1-final-compose-proxy-smoke-clean.txt`、`v1-final-compose-ui-smoke-clean.txt`。
