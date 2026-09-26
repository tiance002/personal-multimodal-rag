# progress.md — 当前开发进度

- 当前里程碑：M4.5 / V1.0 质量闭环与发布验证；P0/P1 与 LangChain Smart 迁移已完成代码路径
- 当前基线：分支 `main`，用户提供基线 commit `65e5382`；本轮尚未提交、未打 tag。
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
