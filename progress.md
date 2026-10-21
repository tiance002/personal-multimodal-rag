# progress.md — 当前开发进度

## 2026-10-11 主线整合与仓库清理（功能未验收）

- `main` 已快进包含当前项目提交 `2e19b75`，并合并公开评测报告分支与 M4 前 25 题 Qwen 报告分支；合并提交分别为 `7056e73`、`41885a2`。
- `.gitattributes` 冲突已人工合并：保留 PDF closure fixture 的 `-text -diff` 规则，并添加 M4 报告与结果文件的 LF 规则。
- 已删除三个已并入 `main` 的本地分支引用：`codex/eval-center`、`codex/rag-retrieval-optimization-round1-20260930`、`codex/local-first-rag-v1-20260930`。GitHub `main` 已同步；已删除远端 `codex/local-first-rag-v1-20260930` 与 `codex/m4-qwen-paired-first25-20260929`。
- M3 评测/检索实验分支、M1/M2 评测实现分支和 `snapshot/teaching-20261002` 保留：仍有主线未包含的代码或历史证据。
- 三个 C 盘链接 worktree 的只读 Git 状态检查返回访问拒绝；未删除其目录、分支或修改 ACL。
- 两组完全相同文件仍保留：`backend/app/adapters/models/usage.py` 与 `backend/app/application/model_usage.py` 分属不同依赖层且各有调用点；冻结语料 `evaluations/trust_v1/corpus/design.md` 与设计原文相同，`sources.json` 仍以原文为来源。
- `git diff --check origin/main...HEAD` 检出历史审计/设计/冻结评测文件中的行尾空白与 EOF 空行；为保留历史证据及冻结语料，未改写这些文件。测试、模型请求和数据库操作均未运行。

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



## ECS 评测中心定向清理与部署（2026-09-27）

### 1. 目标与改动

- 目标：按负责人确认的清单备份旧学习规划服务、清理精确列出的旧路径并部署本地评测中心。
- 代码改动：完成 eval_center/ 运行模块、SQLite/只读 HTTP 服务和仪表盘；新增本地 bundle 打包、校验与部署/上传脚本；评测样本增加稳定 case_id。代码审查发现上传重复 JSON key 与校验/上传 TOCTOU 风险后，增加严格重复 key 拒绝、规范化 allowlist 快照及只读文件句柄；安装器把 /srv/rag-eval/backups 设为 root:root 0700。
- 服务器备份：/var/backups/study-plan/cleanup-20260927T143810Z/；资产归档 100,895,884 字节，PostgreSQL custom dump 359,851 字节，均为 root:root 0600，备份目录 root:root 0700。归档和 dump 均重新校验；tar --list 与 pg_restore --exit-on-error --file=/dev/null 成功，SHA-256 与 VERIFIED 文件一致。
- 服务器清理：移除 /opt/study-plan、/etc/study-plan、/var/lib/study-plan、/var/www/letsencrypt、旧 Nginx study-plan 站点和 7 个 study-plan-* systemd unit；Nginx 已停用。PostgreSQL 服务、study_platform 数据库及角色、OS 用户 studyplan、15 份既有 dump 和 /etc/letsencrypt 证书/续订材料均保留。
- 部署：rag-eval.service 已启用，监听仅为 127.0.0.1:8787；仪表盘和 health API 可用，SQLite schema v1 完整，重启后仍正常。SSH 隧道本地端口验证通过并已关闭。
- 实际传输到 ECS 的只有部署源码归档；没有上传评测报告、问题/答案、片段或其他业务结果。仓库中未保存 SSH 私钥内容或私钥路径。

### 2. 执行命令与证据

| 命令/检查 | 退出码 | 关键输出 |
|---|---:|---|
| & 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest eval_center/tests backend/tests/test_quality_eval_schema.py -q | 0 | 45 passed, 6 warnings |
| git diff --check；PowerShell parser 检查部署/上传脚本；bash -n deploy/eval_center/install.sh | 0 | 三项语法/差异检查通过；凭据材料扫描无匹配 |
| 只读 SSH 环境复查（strict host-key，远程脚本经 stdin 传输） | 0 | Ubuntu 24.04.5；目标目录原先不存在；PostgreSQL active；旧服务/timers active；盘可用约 34 GiB；原 TLS 私钥只检查了元数据 |
| 远程备份脚本（strict SSH，经 stdin 传输） | 0 | BACKUP_VERIFIED utc=20260927T143810Z；归档 SHA-256 1e427fc512525ba9669ae32c8f168b1c5511ea08d85e1b9e099aab3f99c9a12d；DB dump SHA-256 76cd48d52800f6a4d5fbea10cf3cbc83b5d088f80967dea6b0809b470d5a47ad |
| 备份独立复核（重算 SHA-256、读取归档、pg_restore 校验） | 0 | BACKUP_RECHECK_PASS |
| 远端清理脚本 | 1 | 清理操作已完成；尾部 SSH 端口断言误对整行匹配，未从 ss 提取本地地址列。修正后的完整状态复核退出码 0：CLEANUP_POSTCHECK_PASS，SSH 22 保持监听，80/443/8000 无旧监听 |
| & '.\scripts\deploy_eval_center.ps1' -HostName '120.55.115.162' -User 'root' -Port 22 -IdentityFile '<运行时提供的私钥路径>' -Apply | 0 | rag-eval is active on 127.0.0.1:8787 |
| 远端 health/dashboard/API、SQLite integrity/schema、systemd restart 复核 | 0 | health {"status":"ok"}；dashboard HTTP 200；API {"experiments":[]}；schema 1；integrity ok；重启后同样正常 |
| SSH tunnel 到本机 127.0.0.1:18787 health 请求及关闭检查 | 0 | SSH_TUNNEL_PASS local_port=18787 health=ok tunnel_closed=true |

备份首次尝试曾因 postgres 用户不能写入 root-only 备份目录而退出码 1；错误处理确认恢复了原服务和 timers，未开始删除。移除失败尝试生成的未验证归档后，以 root 接收 pg_dump stdout 并重新完成、复核备份。部署前后未改防火墙或云安全组。

### 3. 结果

- 本任务范围：本地测试与代码检查 PASS；备份 PASS；定向清理复核 PASS；ECS 部署与 loopback/隧道 smoke PASS。
- 未运行 verify-release.ps1、全量发布门禁或实际评测结果导入，不能据此宣称 M4.5/V1.0 整体发布验收通过。

### 4. 风险与遗留

- 备份在同一 ECS 根盘的 /var/backups，不是异地副本；实例/磁盘整体损坏时无法依靠该份备份恢复。
- Nginx 和旧站点已经停用；证书和续订配置保留，但旧续订 timer 停用。若要恢复旧网站，先按该归档恢复并重新检查服务配置。
- 云安全组未查询或修改；服务只绑定 loopback，通过 SSH 隧道访问。没有公网暴露新的 8787 端口。
- ECS 注册库保持空；实际评测结果和任何私有数据都没有传输。

### 5. 版本与下一步

- 工作树：codex/eval-center，基线 a0a62e8ee78be4a176b81e3f61cf5b84dd6d364b；改动未提交，无新 commit 或 tag。
- 当前 ECS 服务部署已完成；下一最小任务是审阅改动，并在需要时单独确认要上传的已脱敏评测 bundle。

## 2026-09-28 评测可信度修复与真实重新验收

1. 目标与改动：当前授权Goal的P0/P1修复完成。实际执行/部署提交 f15aaeb374e32d7955429784b4d9dd50317608a5，源码前序 c4ee364。新增稳定Gold/完整排名及来源覆盖/独立ECS重算/实际usage/隔离真实runner/冻结33题/严格脱敏/版本化部署；业务改动限Ollama采集与Retriever执行元数据。最新最终报告见 docs/reviews/rag-evaluation-final-audit.md，原FAIL全文保存2026-09-27-rag-evaluation-audit-fail.md。
2. 执行命令：完整eval_center/tests和相关业务回归157passed退出0（报告原样列出参数）；正式runner退出0，A/B各33题真实PG/BGE及8次Qwen生成；deploy/upload退出0；云端66题重算/8类篡改拒绝/同内容unchanged和冲突保护退出0；本地与云逐字段66题追踪退出0。真实浏览器正式/诊断/比较/详情数值核对，13部署源码UI摘要一致，SQLite实际恢复副本6行完整摘要一致。命令及所有尝试失败/修正详见报告。
3. 结果：当前Goal核心评测验收PASS；RAG质量实验partial；M4.5发布/完整产品回归NOT RUN，不称负责人已验收，不创建Tag。正式A来源上下文召回.7166667，B.65；引用回读1/1，明确拒答1/1，语义Judge NOT_EVALUATED；源/排名计分单位及所有有效分母明确。
4. 风险遗留：仅项目资料域33题、Agent来源审核（human_review NOT RUN）；当前q0查询、无真实重试，usage已测采集路径但不伪造发生调用；明确文本拒答和字面要点不能代替语义Judge；备份同盘。上述可选P2按当前Goal记录，不擅称发布或延期批准。
5. 版本下一步：真实实验及ECS为f15aaeb（同模型digest/冻结Gold、不同真实索引）；最终提交仅文档，与运行版本明确分开。评测DB6行保留，其中2正式、2首轮拒答协议失效诊断、2原smoke未验证。正常知识库未修改、私人原文未上传、凭据不进仓库。Goal结束后不自动优化；保留隔离数据/报告及本机SSH Dashboard隧道以供复核。


## 2026-09-30 Local-first V1 收敛与真实资料验证

1. 目标与改动：CURRENT_STATE审计、WeKnora/RAGFlow固定版本对照、来源标注profile、分层Recall/Fusion/可选Rank/Context、确定性Adaptive、EvidenceQuality与ExecutionRouter接口、共享真实metrics、同范围追问与隐私原文/截断护栏。详细交付见 docs/reviews/local-first-v1-final-report.md；启动见local-first-v1-runbook.md。
2. 执行命令：一次固定24QID public sanity退出0（48 retrieval/24 query embedding/0corpus embedding/0DBwrite/0LLM）；隔离库compose/migration退出0；frontend build退出0；真实UI最终退出0（2生成）；32题真实材料runner退出0（33生成/32query embedding/0cloud），31项最终定向测试退出0；独立真实隐私guard退出0且0生成，5引用回读；命令参数和失败尝试在最终报告中明确记录。全量verify-mX/release NOT RUN。
3. 结果：有限Retrieval Gate PASS，已冻结Vector默认+条件Hybrid，公开参数优化结束。产品真实主流程已跑通；32题30返回、2 UNSUPPORTED_ANSWER，不等于30正确；46/46引用身份回读。语义准确率/引用语义支持NOT_EVALUATED，人审NOT_RUN。输入58238/输出6068真实生成Token，p50 3.933s/p95 10.057s；GPU主机采样峰4816MiB，RAM最低可用5459.38MiB。
4. 风险与遗留：原32结果保留错误隐私建议、2可见截断、部分遗漏/过拒答与Context覆盖失败；隐私/截断窄范围护栏新增并记录不同源码身份，不重跑评分。Gold5文档而实际6文档47Chunk，第6文档未完整预注册；scope用39Chunk映射核对；OCR未覆盖所有图像；不称发布验收，不自行批准延期。
5. 版本与下一步：实现0b68385/56a405f，首批执行90af812e0cdb3d3124df9f39e9e78beb42da195f，安全护栏25974ce；后续脱敏报告提交独立。用户可在http://127.0.0.1:14188开始隔离V1资料使用。停止自动实验/优化/部署，不打Tag。公开原库、原卷、M3/M3.5历史产物与ECS未修改；私有资料及原始报告保留忽略的var中。

## 2026-09-30 V1 Quality Hardening

1. 目标与改动：共享 Validator 本地 candidate 审计与安全原文降级、bounded生成预算/length处理、零额外模型多意图清单、稳定Citation身份及occurrence映射；独立8题旧V1 Human Overlay。最终报告 docs/reviews/local-first-v1-quality-hardening-report.md。
2. 执行命令：报告原样列出定向pytest（59passed，退出0）；最终同32题真实runner（退出0，31生成/32embedding/0cloud/52引用回读）；不可变7文件verify及168源码哈希核对（退出0）；git diff --check退出0。原首轮、重放、经用户授权的最终回归均独立保留。全量发布/Cloud/新答案人审NOT RUN。
3. 结果：工程机制与评估协议PASS；语义完整质量门19/32=59.375%，旧62.5%不改，质量回归验收FAIL。截断0、引用身份冲突0；仍6题部分引用支持。没有Retrieval调参，32题retrieved/selected ID完全相同。
4. 风险遗留：Context充分的8题模型失败候选、3题Retrieval先行、2题Reference范围待负责人复核。原004/009 candidate缺失仍不能证明Validator误拒；8/32Overlay仅旧答案。未自行批准退化延期，不继续无限调分。
5. 版本下一步：分支codex/local-first-rag-v1-20260930，基底52d770e8418bb040c372bf8d9014381ed68bc160，修改未提交未合并，无Tag。停止本Goal工程与实验；下一阶段需负责人决定，不自动实现Cloud。

## 2026-10-11 NO_CANDIDATES 任务暂停交接

1. 目标与改动：负责人要求暂停执行；当前修复、真实服务和UI改动保留，不撤销、不提交、不打Tag。五角色模型配置表单、本机密钥安全保存接口、资料完整名称显示已实现。完整任务G4/G5尚未完成。
2. 执行命令：本轮模型配置定向pytest退出0、102 passed；Playwright退出0、12 passed（SIMULATED API）；真实网页只读脚本退出0（5角色/9名称/0模型调用/0变更请求）；Docker构建与部署退出0。完整命令、失败尝试、报告位置与证据界限见 docs/audits/no-candidates-20261011/PAUSE_HANDOFF.md。
3. 结果：局部检查PASS，真实服务/公开样本闭环有既有证据；全任务验收NOT RUN。新模型配置真实保存与Worker同步、新API的OpenAPI快照、隐私/版本保护和受影响历史回归尚未完成；未知模型适配及Rerank不可称已支持。
4. 风险与遗留：原hello/你好候选已恢复但生成仍MODEL_UNAVAILABLE，原因UNKNOWN；两笔UNKNOWN预留保持。旧回归65隔离setup错误和2边界失败需归属核查。没有自行批准延期。
5. 版本与下一步：main HEAD d13951afbd4de4432d231a5921c8657daedab853，工作树未提交。网页14188、API/Worker/Redis/DB保持运行。续做入口 PAUSE_HANDOFF.md 和忽略目录 var/codex-goals/no-candidates-20261011.json；须负责人明确恢复后执行。MANUALLY_SUPERVISED_TRIAL。

## 2026-10-12 恢复 Goal：配置、保护与验收续做

1. 目标与改动：按PAUSE_HANDOFF恢复原完整任务。五角色配置真实保存/API Worker同步与重启恢复验证通过；权限失败DTO、固定provider诊断、历史测试fixture漂移修复；来源/旧版本与失败新版本保护验证通过。当前入口说明改为docs/RUNNING.md，历史运行快照保留。
2. 执行命令：MINIMAL_FIX_REPORT.md列原样命令/退出码/报告；真实专用PG/Redis79 passed（模型SIMULATED），离线116 passed，BM25/context89 passed，浏览器40 passed（API SIMULATED），装配/窗口96 passed。真实版本保护12回读/0调用；配置保存重启0调用；失败历史两轮0变更。整体contract退出1，31基线import违规/0新增；OpenAPI/SSE各项无漂移。不存在或未运行发布命令不能称通过。
3. 结果：有限公开样本真实问答/引用/恢复有既有PASS证据；G4原hello新诊断仍MODEL_UNAVAILABLE：BGE与Xing成功、Xing输出未过Validator后DeepSeek升级TLS层异常。旧失败原因UNKNOWN，不反推；不关闭证书校验、自动重试、切模型或释放UNKNOWN。全任务未验收，Goal active。
4. 风险与遗留：DeepSeek当前真实生成需受控复核；新未知模型能力适配/Rerank实际调用未启用；31历史分层违规不为此次全仓重构。没有负责人延期批准、发布验收或Tag。
5. 版本与下一步：main基底d13951af，准备故障修复提交，再独立历史AGENTS附录归档/冗余说明提交；证据与checkpoint保持忽略。不得删除源/Gold/迁移/旧引用/失败/UNKNOWN记录。MANUALLY_SUPERVISED_TRIAL。


## 2026-10-12 WeKnora ordinary output / progress / SSE Goal

1. Goal and changes: fixed WeKnora3e8b0bfc source mapping; independent ordinary output policy/counters, immutable attempts, safe committed progress, assistant-side timeline and scoped long-request recovery. G1 local commit e90eceb; G2/G3 source and handoff staged separately. Existing retrieval/router score, final validator/PG transaction, ledger/UNKNOWN and historical grants unchanged.
2. Commands/evidence: docs/audits/weknora-ordinary-output-progress/TEST_REPORT.md and G1_NOTES/UI_NOTES list actual commands/exits. Root289offline tests passed, later14readiness/progress and41final-commit tests passed; related75browser tests plus final16passed; TypeScript/Vitebuild exit0. Globalcontract exit1,31baseline layering violations/0new; OpenAPI/SSE checks pass.
3. Result: OFFLINE_PASS for scoped implementation. Runtime identity BLOCKED: DockerDesktop engine absent,14188/8000unreachable. Real output probes, neworiginal-question replay/currentcitationHTTP, service replacement and businessDB operations NOT RUN. Not E2E_PASS or owner acceptance.
4. Risks: real1024 output unknown and refusedbeforetransport;2048/4096 notenabled. No silent512fallback. Existing grants/quotas/consent/UNKNOWN notmodified. G5cleanup NOT RUN pending functional validation. No push/tag/release.
5. Next minimum task: ownerapproved runtime restoration/identity check and bounded hosted output probes, then3distinct authorized ordinary questions with actual stages/usage/citation readback. Keep sameGoal checkpoint var/codex-goals/weknora-ordinary-20261012.json; do not rerun alloffline tests if their scopedinputs remainunchanged.


## 2026-10-12 WeKnora continuation checkpoint

Current report: [CONTINUATION_REPORT.md](docs/audits/weknora-ordinary-output-progress/CONTINUATION_REPORT.md). Ordinary output/progress/SSE offline work retained; version3 public capacity proof and internal SHA-pinned deployment added. Actual Xing2048 PASS, lower1024 support explicitly inferred, higher4096 unverified. Latest DeepSeek Run72917fff-bd37-4607-b3db-7c7e402d3fbf TRANSPORT_FAILED/UNKNOWN4804 causes immediate owner-required generation stop. PriorUNKNOWN1063 remains occupied; totalUNKNOWN5867. 4/7 probes, 0/3 webpage questions. API/Worker image5de88de8..., originalDB0024/config revision unchanged; readyz503 NOT_READY. No current factual answer/citation/reload proof, no G5 cleanup, no Push/Tag/release. Goal remains incomplete; owner decision needed before new model calls. No old Run resend or quota/ledger reset.

### G4 safe preparation after UNKNOWN (no generation)

See [TASK_FENCE_NOTES.md](docs/audits/weknora-ordinary-output-progress/TASK_FENCE_NOTES.md). Credential-free public GET observations vary between HTTP401 and connection/TLS failure; cause of the original UNKNOWN is not isolated, transport unchanged. Opt-in task-only fence now implemented: full probe/new-call bounds share75000 limit, native durable file/process lock, exact original Scope/policies, missing journal fails closed, fresh UNKNOWN blocks reservation and send including automatic expensive fallback. Original authoritative ledger/guards remain delegated. Linux10tests exit0 (native FS/process+SQLite/mock), follow-up probe stop tests6passed exit0. Real task activation NOT RUN; no new owner grant/journal, service replacement, DB mutation or model call this turn. Three pending original/short/long questions computed as2048/1024/4096 with fresh UUIDs; no POST. Fresh owner approval needed for at most2newprobes within7total and temporary fenced replay of the original3questions. Goal not accepted; G5 NOT RUN.

### Authorized follow-up probes and ordinary TLS repair

See [TLS_STATE_REPAIR.md](docs/audits/weknora-ordinary-output-progress/TLS_STATE_REPAIR.md). Owner authorized2freshprobes+temporaryfenced3questions. Xing4096 Run8b61ffc6 verified/settled4224; DeepSeek33a016bc UNKNOWN4804 triggered immediate stop. Total6/7probes,0/3questions; PG readback known+UNKNOWN17247, UNKNOWN10671 retained. No current factual answers/citations. Full HTTP memory reproduction found firstTLS-EOF close resets Request-sent toIdle, causing ResponseNotReady after secondconnection sends the one request. Minimal socket-only retry cleanup implemented, terminalfailures remaintypedNotSent, max2connect/1HTTP withguardchecks retained. Red testexit1 thenTLS18passed exit0; service/provider86passed exit0; stop7passed exit0; standardlibsmoke4casesPASS. HistoricalUNKNOWN cause not asserted; none released/reclassified. NewXing4k proof bound only inpreview, notliveconfiguration. Lastseventhprobe and3webquestions require a fresh owner decision after latestUNKNOWN. No G5,Push,Tag,release or acceptance.


## 2026-10-13 WeKnora actual replay and unit prompt repair

1. Goal and changes: latest owner approval used seventh DeepSeek4096 probe successfully. Source repair ce3398a restores numeric-unit witness instruction for ordinary answers only; validators/router/ledger remain unchanged. Current report docs/audits/weknora-ordinary-output-progress/LIVE_ACCEPTANCE_20261013.md supersedes stale summaries without erasing them.
2. Commands: original/short native webpage collectors each exit0 but functional FAIL; targeted70 pytest passed exit0; candidate Docker build exit0. Actual runtime remains TLS image47f3445, READY_CONFIG, same DB0024/config/volumes.
3. Results: seven probes finished; observed Xing2048/4096 and DeepSeek4096, lower caps inferred. Original UNSUPPORTED_ANSWER, short MODEL_BUDGET_DENIED upgrade NOT_SENT, long NOT RUN. No current answer/citation success; G5 NOT RUN.
4. Risk: old UNKNOWN10671 retained, no new Token UNKNOWN. Full task bounds63958/75000; actual known+old UNKNOWN31858. Candidate unit guard has not been deployed; task fence remains active and blocks other mutations.
5. Next: explicit owner decision needed for new candidate replacement, two additional fresh original/short Runs plus existing unsent long Run, and higher conservative task ceiling; no old Run resend, Push, Tag, release or unilateral deferral. Goal incomplete.


## 2026-10-13 WeKnora latest actual long stop

1. Goal and changes: unit instruction repairce3398a and G3 history-interruption/default300s repair0219046 complete; runtime API/worker sourcece3398a imagea94a1696, ordinary timeout120 and V4 task-only fence350000/7webIDs active. Original DB/config/storage preserved. Detailed current report LIVE_ACCEPTANCE_20261013.md.
2. Commands: original171ca042 and shortcc79fb74 real browser collectors exit0/functionPASS with8citationHTTP200/PGScope/version/quote-hash readback and UI click/reload/SSE22events cursor recovery. G3 RED exit1 then19SIM tests/build/diff-check exit0. V4 nativeLinux14tests exit0. Latestlong9043e892 collector exit0/functionFAIL; taskreadback exit1 intentionally NEW_UNKNOWN_STOP.
3. Results: original2048/short1024 PASS; long4096 FAIL, both90s and120s REQUEST_TIMEOUT. All7probes preserved,7webattempts consumed; actual known43321 plusUNKNOWN53915=97236, fullconservative189408/350000. No costly calls afterlatestunknown.
4. Risk: provider service-side causeUNKNOWN, no response/usage to infer; neither budget increase nor120s wait provedlong availability. Unknown occupancy retained. G5 sourcecleanup NOT RUN; only REDUNDANCY_INVENTORY_PENDING.md prepared. No unilateraldeferral/acceptance.
5. Next: owner/model-service decision required forlong path under unchanged scoring/roles; no blindlongresend, no unapproved model/timeout substitution, no Push/Tag/release.


## 2026-10-13 Owner-requested pause and review handoff

Stopped implementation and real generation at owner request. Current HEAD e6848a9; seven unfinished stream integration files preserved uncommitted, not deployed. Runtime remains ce3398a nonstream/120s/V4 NEW_UNKNOWN_STOP. Real original2048 and short1024 PASS with citation recovery; long4096 FAIL twice at90/120s. Full bound189408/350000, known43321+UNKNOWN53915=97236; no UNKNOWN released. Candidate Windows56passed1skipped is simulated, Linux/native framing/build/real streaming NOT RUN. G5 NOT RUN. Current pause handoff: [PAUSE_REVIEW_HANDOFF_20261013.md](docs/audits/weknora-ordinary-output-progress/PAUSE_REVIEW_HANDOFF_20261013.md). Goal incomplete and paused pending owner review of solutions; no new paid calls, deployment, DB operations, Push/Tag/release.


## 2026-10-15 accepted branch serial integration

Local/MQ/QA nine accepted commits integrated with source SHA trailers from actual base 2bc2a06. Report: docs/weknora-port/SERIAL_INTEGRATION_REPORT_20261015.md. Necessary targeted commands returned 0: 253 offline, 27 real isolated Redis, 8 Local/ordinary HTTP+PG/Redis MockTransport tests. Test pre/post hashes and four prior untracked-file hashes match. Shared fixture conflicts were resolved by preserving both safety checks and parameters; runtime Local factory/success hook and QA fallback AST checks pass. No historical evidence overwrite, UNKNOWN release, real provider/model pull, default entry switch, P8 deletion or push/deploy. Go public entry, actual Ollama and cutover remain unverified; no milestone acceptance claimed.


## 2026-10-15 Owner-requested P8 pause

Development API/worker/React/package entry changes saved as an unaccepted checkpoint. Targeted offline checks: 23 passed, exit 0. React build: exit 0. New-entry isolated HTTP/PG/Redis test: exit 1 (expected pending memory task count 1, observed 0); worker assertions not reached. Installed-wheel and compose configuration checks NOT RUN. No real provider calls or deployment. Resume from docs/weknora-port/PAUSE_P8_DEVELOPMENT_ENTRY_20261015.md. Owner requested waiting; Goal incomplete and paused.


## 2026-10-15 P8 resumed and scoped entry proof

Latest root delegation explicitly resumed ec98695; earlier pause provenance UNKNOWN (no verifiable ID/time/sender). API enqueue-autostart defect repaired in9567c88 while retaining old default behavior. Scoped new-entry actual HTTP/PG/Redis/worker test passed, including second assembly memory/image processing and later image after idle. Installed wheel same test passed,97 backend modules from installed paths,0 source imports.11 queue/config checks and both compose config checks exit0; prior23 offline/React build0 reused unchanged. WheelSHA2565fb5215b3b4da0f12dc7af4c8bcb13815ba4d2413405c2103b632911fe784459. Third-party dependency paths reused; full fresh dependency installation NOT CLAIMED. No REAL_PROVIDER_PASS/CUTOVER_PASS or whole differential PASS. See P8_DEVELOPMENT_ENTRY_REPORT_20261015.md.


## 2026-10-15 reference metadata and receipt follow-up

Reference DTO fix4488ac1 preserves source Unicode half-open coordinates including0, and audited metadata through real final commit/JSONB/history.168 scoped offline checks and1 actual HTTP/PG/Redis two-turn check passed, exits0. Separate estimator addendum archives exact old receipt byte form (only final newline CRLF), canonical Git byte form, AST identity and1+1 targeted local-tokenizer checks0; original receipt unchanged. See REFERENCE_METADATA_REPORT_20261015.md and ESTIMATOR_RECEIPT_BINDING_20261015.md. Full paired differential independent re-review pending; no whole-stage/provider/cutover acceptance. Additional missing-summary-model POSTPROCESSOR_PENDING observation recorded for root review, not silently deferred.


## 2026-10-15 missing-summary-model closure and final installed package

Source no-summary-model behavior differs from prior Python pending: fixed upstream marks summary failed, terminal task drains, parsed/indexed document remains usable. Minimal fixb5f396d; RED1fail/1pass -> GREEN7pass0. New final wheel sourceb5f396d includes referencefix4488ac1; SHA256aebf8eb475dad38f45c8267071771f97b10adeed007e9acc80dad17498b053e8. Fresh E-installed proof4pass0,97 installed backend module hashes match,0 source imports. No complete dependency-install/provider/cutover PASS.45 exact retirement candidates with callers and21 protected support hold paths are review-only; none deleted. See P8_FINAL_PACKAGE_REPORT_20261015.md and P8_RETIREMENT_REVIEW_20261015.md. Historical evidence/four protected files unchanged; no deployment.


## 2026-10-15 explicit entry migration and scoped source retirement

Non-deletion migration e20b5d9:15new contracts PASS; actual verify-default/Schema0, refusal modes2; native collection1296/0, legacy4098/9missing-file errors/2; old289test/fixture hashes unchanged. Native/legacy Playwright lists7/97nodes0 and Reactbuild0, no browser execution. Separate exact-three smoke removal follows new Owner approval Sentinel_cbdb2f4b286081918285cf3361fdad09; matched source hashes/zero active callers,3approved leaf deletions only,356held hashes unchanged. All other42candidates/21support holds retained. Reports ENTRY_MIGRATION_REPORT_20261015.md and THREE_SMOKE_RETIREMENT_REPORT_20261015.md. Actual native eval execution, real providers, browser, deployment and full-repository GREEN not claimed.


## 2026-10-15 scoped Office source implementation

Fixed DOC/PPT/PPTX wrapper gaps implemented with attributed source functions and source hash/AST binding. RED2fail → scoped23offline+1actual native PG/Redis entry pass0; real conversion tools substituted, real converted OOXML decoding/assets/version/original readback verified. Prior stopped Redis left untouched; temporary independent RAM Redis56425 used. Original infra/PYTHONPATH failures retained, no install. Report docs/weknora-port/OFFICE_LEGACY_REPORT_20261015.md; real soffice/antiword/complex quality/providers/cutover NOT RUN.

Office review closure: empty-primary config and explicit source file_type fixed after3RED cases. Final26offline pass0 and1current-source native PG/Redis pass0; source-binding updated, original failures retained. See OFFICE_LEGACY_REPORT_20261015.md.


## 2026-10-15 scoped Provider proxy safety repair

Standard env/snapshot/CGI/IDNA, source raw-host trust and complete CONNECT deadline fixed. Initial13RED and three independent2-caseRED sets retained; final158offline/native-protocol checks pass0. Actual Go15env/10trust/9in-memoryCONNECT cases executed with E-only caches/telemetry. Empty SOCKS password and URL path/query/fragment restrictions retained pending Owner; no full P1/provider/cutover acceptance. See PROXY_BOUNDARY_REPORT_20261015.md and evidence/PROXY_BOUNDARY_20261015.


## 2026-10-15 Owner-requested closeout and wait

Direct current-session instruction: 收尾目前工作，等待之后继续. Later application-open message acknowledged; no unfinished implementation started. Office e1b945b9 and proxy7197e3e1 committed; prior26offline+1isolated Office and158proxy/15Goenv/10trust/9memoryCONNECT receipts reused, no test rerun. seq/image completion remains SOURCE_READ only, no patch. Four original untracked hashes match. Only owned temporary RAM Redis56425 stopped/auto-removed, original PG/Redis untouched. See docs/weknora-port/CLOSEOUT_HANDOFF_20261015.md and evidence/CLOSEOUT_20261015_0637/receipt.json. Waiting for later continuation; no Goal/provider/cutover acceptance, real calls, push/tag/deploy.


## 2026-10-15 seq/image work explicitly resumed

Parent provides Owner denial Sentinel_882c8f736d408191b7f0a381aa141dc7 (我没发过): prior pause text provenance is not accepted as Owner instruction. Resume seq/image completion only from3b528f22; Office/proxy ranges frozen. Plan .planning/2026-10-15-seq-image-completion; initial counterexamples added, runtime not yet changed.


## 2026-10-15 seq/image source closure

Latest Owner continue resumes existing uncommitted patch. Seq now maps chunk_index, complete hybrid search unconditionally fills empty parent image_info via shared two-level collector, exactly6upstream fields; known parent KB/doc/source_version anchors on both levels, existing child strings/nonempty data preserved. Original12RED and summary guardRED retained;124scoped offline pass0, actualGo16cases0 reused by SHA. Final ASGI upload/chat/imageworker+realPG/Redis search/Context/reference HTTP/JSONB/history1pass0. Current full search vs existing actualGo frozen10results:18sharedfields inclseq/image data equal,4child literals intact,12index/10source rows unchanged, read-only0writes/models. Ties UNDEFINED_FUSION_TIE_ORDER and scores not reasserted after segment changes. Only approved test PG56423/Redis56424 restarted after Owner opened Docker; data/config preserved. See SEQ_IMAGE_COMPLETION_REPORT_20261015.md and source-binding. No real provider, cutover, push/tag/deploy or wholeGoal acceptance.


## 2026-10-15 parent image JSON boundary follow-up

Independent review accepted42c1a278 named closure and found uppercase/int64 boundary gaps. Parent-only pair-preserving Go-tag decoder added; shared parser, seq and batch/scope AST preserved. ActualGo30case0, RED14fail/20pass1, scopedGREEN158pass0, minimal ASGI/PG/Redis1pass0 and existingGo frozen10x18/image0. Original child/nonempty parent literals intact, no clamping. See IMAGE_JSON_BOUNDARY_REPORT_20261015.md. Owner assigns browser acceptance elsewhere; BROWSER_FACTS_20261015.md is source-verified handoff only, no browser launched. Four original untracked and history/UNKNOWN retained; no realprovider/push/deploy.

## 2026-10-16 MVP real public text RAG

1. Goal/changes: Owner switches to existing-code-first minimal real loop. Base e20e06c7 includes Recipe repair. Minimal commits e128855 (SF provider/account isolation), 6141656 (existing fixed no-evidence refusal configuration), eabf2d6 (final raw text evidence projection), 2999323 (explicit real runner). No router/retrieval threshold changes, parser rewrite, schema/dependency or container packaging changes.
2. Execution: actual standard API approved ready200, original PG56423/physical7696088403504697382 and Redis56424/DB15 isolated namespace, Vite56426. Public complete RFC2606 uploaded, 10 original chunks +1 generated summary and11real1024-dimensional vectors. Real SF bge-m3/rerank/Xing:62requests including initial failures,54settled/8usage-missingUNKNOWN. Directed offline26+15+77pass exit0; read-only final audit exit0; actual Edge final answer/citation readback. Full commands and failures in docs/weknora-port/MVP_REAL_RAG_ACCEPTANCE.md.
3. Result: MVP_REAL_RAG_PASS limited cases / CHEAP_REAL_RAG_PASS / EXPENSIVE_BLOCKED. Direct factual Q4 and two-passage Q2 have exact raw citation content/ID/version/offset readback; no-evidence Q3 uses pre-existing fixed refusal, zero answer calls. Original Q1 still observed wrong/no-citation and empty-truncation FAILs, all preserved; this is not universal answer-quality or release acceptance.
4. Usage/risks: observed input39983/output2654/total42637;8Rerank lack settleable usage, held74497not free or actual. NewDeepSeek0, original DS month blocked/UNKNOWN snapshot unchanged. Langfuse/no cross-history multimodal guarantee/standaloneworker/full evaluation NOT_RUN. User explicitly permits other models without repeated approval, DS cumulative1m remains.
5. Next: stop added requests/development, begin six-dataset wiring and small sample checks using existing748unit data/Gold strategy. Formal three-mode quality evaluation still blocked on DS decision. API10876/UI3432 kept for local readback, temporary authority expires2026-10-16 13:38:56Asia/Shanghai. Original six untracked files/.serena retained; no Push/deploy/Tag.


## 2026-10-16 P8 six-dataset real wiring / bounded closeout

1. All748source units (648raw+100prepared) offline valid/hash/parse PASS,0exclusions;40frozenQID source/qrels and24Omni pageGold mapped. Sourceunits are not748questions; prepared mockIDs not reused as realindexes. Fixed9questions/22originals uploaded via standardAPI to tenant332, real PG56423/Redis56424DB15 ownprefix.
2. Initial9 actualquestions:5strictwiringPASS/1PARTIAL/3FAIL. Two documentedrechecks retained separately; total11attempts/10dataset-QIDs:5PASS/2PARTIAL/4FAIL. SciFact/Hotpot samplesPASS; MIR/Long have uncitedanswers; MML visualcounting and OmniOCR-to-answer path remainBLOCKED. Fullsixqualityevaluation NOT_RUN, formalall-sixCheapOnly readiness NO. Do not turn plumbingPASS into answeraccuracy.
3. P1 fixed in295bd0ac5aeca1f4227899797f495a2cca02c9a7: generatedsummary suppressed rawtext before final sourceprojection. Production merge-inputview excludes generated evidence before fixedmerge, preserves originalrankings, prevents summary-only fallback, retains sourceversion/KB/quote guards. Fixedmerge/search/threshold/router/ParentChild unchanged. Actualmerge negativecounterexample and91targetedtestsPASS0; realMIR contextRecall0→1, modelcitationstillmissing sooverallFAIL retained.
4. RealSF127responses embedding77/chat41/rerank9,118settled knownactual120879tokens;9missingusageUNKNOWN held111093 notfree. NewDeepSeek0; originalDSfullhistoryunchanged; oldSF8UNKNOWN/74497held unchanged. No outstandingreserved. Originalfailures/Gold/rawsource/UNKNOWNnotreset.26utilitytests andfinal16scorer testsPASS0; nofullregression.
5. StandardAPI/worker andrepairAPI stoppedbyverifiedPID/module/authority;56427/56428nolisteners, noownedprocessleft. Shared EviRouteRAG PG/Redis kepthealthy. NoPush/Tag/deploy/merge. Productionsharedfilesonlyruntime.py+retrieval/pipeline.py inatomic295bd0a; nomigration/productionlockchanges. Original.serena+6untrackedpreserved. Fullreport docs/evaluation/P8_SIX_DATASET_REAL_WIRING.md andmachinecompanion; evidence E:/WK/run/p8-20261016. StopafterboundedP8; nextscope requires Ownerdecision, notautomaticPrompt/model/thresholdoptimization.

## 2026-10-16 P8.1 bounded blocker resolution

1. Owner P8.1 completed in scope from2477ecd38a1768f28dd9941e4ed13f515411b570. All8frozen MML QIDs audited against current native parsing. Original visual row0430 failure preserved CAPABILITY_LIMIT; official text-supported row0687 frozen before calls and attempted once: actual wholePDF upload, KB search30/rerank7/context7, valid original quote, wiringPASS; answer median reasoning incorrect EM0/charF1.0224. No quality tuning or repeat.
2. Omni oldP8 loss located at rerank0, no merge/context reached, no rawscore invented. New actualOCR-based question frozen before calls exposed image-child quote contamination; fixed d711231d73de6ccb6609453c691d0f8991a1e55f. One real same-question recheck has valid placeholder quote but noOCR in finalcontext; taskPARTIAL despite oldscorerPASS. Parent wins fixedoverlap, aggregate OCR survives merge but sourceprojection replaces image_info with emptyraw. Fixed4b45c70cf6b5c9f138312143ae1ad1d44db415ba retaining trusted enrichment and rawquote. Lastfix actualmodelverification NOT_RUN because allowedrecheck exhausted; offline actualmerge→projection→Context regression PASS. No fixedmerge/Prompt/threshold/routing change.
3. New3real questionattempts: task1PASS/1FAIL/1PARTIAL (rawscorer2PASS/1FAIL preserved). 30SFresponses20embedding/7chat/3rerank;27settled38375actualtokens(input37505/output870);3UNKNOWNheld17091, reserved0. DeepSeeknew0/historyunchanged. Four priorPASSdatasets read-only, newmodel0; SciFact/MIRACL/LongBench READY_SUBSET_ONLY, Hotpot METRIC_PARTIAL. Canstart boundedsubset availablemetrics next; lowquality notgate, nofullcorpusofficialclaim.
4. Directed observer3PASS0; imagequoteRED12fail1→GREEN86PASS0; parentenrichmentRED1fail1→GREEN31PASS0 (overlappinggroups). Onlyproductionruntime.py, no schema/migration/dependency changes. Evaluationhelpers304defd0184c09003e59236dcf2bb4d3f530b695. P8original11attempts5PASS4FAIL2PARTIAL,157artifacthashes/127ledgerrows/6protectedfilesunchanged. OwnAPIparents/children stopped,56429/56430 listeners0; sharedPG/Redis healthy. NoPush/deploy/merge/Tag. Report docs/evaluation/P8_1_MINIMAL_BLOCKER_RESOLUTION.md+results.json; evidence E:/WK/run/p8-1-20261016. Boundedstop, noautomaticnextbatch.

## 2026-10-16 P9 bounded Cheap Only quality baseline

1. Goal/scope: Owner P9 from3c9b722b97b89643cca2c83d255ca2c3762382a0. Real model QA16/40 frozenQIDs: SciFact4, MIRACL5, LongBench3, Hotpot3, MML1;14realanswers/2fixedrefusals. Omni24/24pages processed via normal HTTPupload→OCRworker→index and1separate realRAG diagnostic. Remaining17budget NOT_RUN +7MML CAPABILITY_LIMIT; no success cherry-picking or Gold changes. Backend123sourcefile bytes unchanged throughoutP9.
2. Commands/evidence: new scripts/weknora_port_p9_real.py and observedAPI actual P1-gated SF requests, balanced remaining E:/WK/run/p9-20261016/run_remaining.py exit0; finalsource Omni quote+OCR wire delivery verified independently. Scorer11tests exit0 +newimmutablefinalsnapshot counterexample1pass/11deselected exit0; realWindows transient/persistent filelock checks exit0. Final offline scorer/summary/custodyaudit exit0. Fullcommands/receipts in docs/evaluation/P9_CHEAP_ONLY_QUALITY_BASELINE.md +results.json and E:/WK/run/p9-20261016. No fullregression or officialfullcorpus evaluation.
3. Results: chain10PASS/4FAIL/2PARTIAL !=answeraccuracy. Search/Rerank/Context sourceRecall@10: Sci1/.625/.5; MIR1/1/1; Long1/1/1; Hot1/.8333/.8333; MML1/1/1 on actualsubset. Longdureader2 ROUGE-L.2772571, multifieldQA1 F1.09375; Hot3 EM0/F1.1142684; MML1 projectEM0/charF1.0178344. Sci/MIRnoanswerGold. Quotes23/23valid,10/16questionscited;4/14modelanswersmissingcitations+2refusals. Omni rawliteralNED.4367953 on3/24eligiblepages only;21nativeGoldorderunavailable; officialsemanticlayout/table/formula/QA scoresNOT_RUN.
4. Usage/integrity:250providerresponses (145embedding/88chat inclaux/17rerank);233settled actual274850(input260442/output14408),17missingusageUNKNOWN held314417,0reserved; finaloriginalfenceheadroom6147. Formal16queryledger actual77426/UNKNOWN313562 separate. NoDeepSeek requests/historychange, original219SFrows/350protectedartifacts/oldimagetasks unchanged. PGsystem7696088403504697382, Redisrun87b4c2a9f76f9f94b34b5f21fe31a967926f343a DB15 verified. OwnAPI/worker stopped,56429/56430nolisteners;shared EviRoute PG/Redishealthy.
5. Changes/stop: only P9evaluationrunner/observer/scorer/tests (a2eaa8e44ca55b4d8b3856399c5c09350b6f4985,b7820471dcf0f6a8df29ed24dd746de5db03ab0c,cdcc198f8bac71e0539f3cdf54740898a6194715,3d2755f85fa9fb805091033a2866f27ce00f5241,1be5c4305dae1fc06181a90b373781f918f0b1cc). Fixed measurementfalsepositives, attemptdenominator, refusaldenominator, boundedcheckpointWindowsreadercontention and explicitfinalsnapshotselection; no new productionBLOCKER. Nextsuggestions: citationomission; rerank/contextevidenceloss; task-specific conciseanswerquality. Do notautostart optimization, fullcorpus, MMLvisualcapability or routing changes. NoPush/deploy/merge/Tag; original.serena+6untrackedpreserved. Boundedqualitybaseline delivered, notOwner milestoneapproval.
## 2026-10-16 P10 MinerU local parser A/B and explicit integration

1. Scope/base: Owner P10 from5e2b4fc83d2b816a70b53e6bf6f560890d8bf2ec. Independent E:/WK/mineru-env Python3.12.7/mineru4.0.12; no RAG venv dependency install, DB/provider calls or ledger writes. Hardware i7-13650HX/32GiB/RTX4060Laptop8GiB. Local Basic ONNX CPU and bounded Standard GGUF/Vulkan actually ran; Advanced NOT_RUN.
2. Actual A/B: Flash nativePew45pages; Basic27cases44pageexecutions (20MMLkeypages+24Omni), then fullcat68/fullb321scans. Basic119unique pages/133executions, repeated keypages distinguished. Standard4cases6executions/4uniquepages, actualVLM in forcedOCR/PDFreflection/formula; firstPewauto usesTXT and is notVLM proof. All successful rawmiddleJSON/schema2.0/producer4.0.12 plus zip/assets retained, nine first Windows spawn harness failures preserved.
3. Results: sameP9 literalNED3/24pages .4367953→.1063907, onepage worsened .12→.1333333; remaining21missingGoldorder NOT_RUN, notofficialscore. Standardformula .2515723 worseBasic .1320755. MML prior7limits:0434datasetidentity and0959radiowave/microwave evidence restored (2/7parse-ready, noQA). Other5notproven; originalTesseract standalone also recovers text, notuniqueOCRclaim. P9task completion24 !=OCRsuccess:21nonempty+3OCR_HTML_SOURCE_CONVERTER_REQUIRED; correction sidecar only, frozenP9unchanged.
4. Implementation24e9782e445617f005fa8180a71a3a51ca33c09e: optionalmineru-basic/ocr forscannedPDF/singleframeimages; independent subprocess, strictsource/page/block/bbox/zip mapping; existingNormalizedDocument pureDTO→ParsedDocument→unchangedsplitter; derivedUnicode offsets distinctoriginalSHA; real sidecars/assets/sourcequote validation. Defaults unchanged, native/mixedPDF rejectedexplicitly inMinerUprofile dueobservedtext/chartregressions. No schema/migration/lock/strategy changes; sharedcore/documents/development/pyproject minimaledits listedreport.
5. Verification: finaltargeted49passedexit0; previousingestrelated44overlapexit0. Real localOmni06subprocess smokeexit0; final4savedrealoutput rechecks/sourcequoteassets exit0; isolatedwheelcontent/importexit0 (oldappmodules=[]). DefaultPew45pages70220chars162chunks exactUnicodeunchangedexit0; initialCRLFfile-vs-texthash harnessfailure retained. Frozen376fileshashesunchanged, original.serena+6untrackedpreserved. Evidence E:/WK/run/p10-mineru; docs/evaluation/P10_MINERU_PARSER_AB.md +results.json. OutcomeA minimaloptinofflinePASS; HTTP/DB/index/RAGAB NOT_RUN. Stopnow; recommendpartialBasic, notStandard/broadPDFreplacement. NoPush/deploy/merge/Tag.

## 2026-10-16 P11 MinerU real RAG A/B — budget blocked closeout

1. Base55634295ca5e79aaa36853c35625056f750e6e7d. Read-only actual account audit: SF100万 is MVP local fence, notOwner/provider hardquota. Same siliconflow-owner-main/siliconflow/2026-10 month remains469rows/432settled37UNKNOWN; occupied993839+carry14,headroom6147,UNKNOWNheld517098. Existing trusted TokenBudgetStore forbids same-month config-limit change; no increment API/CLI/manualrunbook exists. Config/real PGsnapshot ASTcomparisonexit0 distinguishes this from actual PG denial. No new authority/quota/ledger mutation, no per-call permission requested.
2. Frozen originalMML0434/0959 +Omni3eligiblepages +Pewcontrol,11plannedQA (cap12), actualQA0 budgetNOT_RUN. No fake/offline scores substituted. Modelidentitiesfixed bge-m3/reranker-v2-m3/Xing29B. All newKBs cloud_allowed=false forcloud-free preparation; vector/keyworddefaults kepttrue, nofakevectors.
3. Actual FastAPI normalupload/source readback11/11hashmatches; A PDF/Tesseract andB independentMinerU via normalingest. PG286chunks/216assets verified;0vectors, all11docs failedatMODEL_ORIGIN_KB_EGRESS_DENIED (intentionalnoegress preparation). Independent Aworker3localOCRcompleted jobs, captionunavailable, tasksfailedterminal; Bworkerempty (BasicrunsAPI). MMLAcriticaltextabsent vsBpresent in52cat/34b3chunks andsource/page/blocklocators. Search/Rerank/Context/answer/generatedcitations NOT_RUN. Failedsourcechunk HTTP404guard2/2. Pewdefault162chunks/70220Unicodecharacters exactlyoldtext, parsing-onlyPASS, fullRAGNOT_RUN.
4. Commands:evidence E:/WK/run/p11-20261016 preflight,budget_proof,prepare_no_cloud freeze/start/uploadA/B/workerstarter/inspect exit0; final_audit_v2 exit0; compose_deliveryexit0. Workerchildexitcodesnotcaptured, stdout/taskstate evidenceexplicit. FixtureparallelstatejoinlostA localonly, reconstructedoncefromimmutableHTTPreceiptswithoutreplay. Firstauditwrongembeddings.tenant_id queryexit1 retained; schema-read fixv2exit0. No productioncode/schema/dependencychanges orpytest/fullregression. Reports docs/evaluation/P11_MINERU_REAL_RAG_AB.md +results.json.
5. SF469rows/month/DeepSeekhistory unchanged;378protectedfiles unchanged; old.serena+6untrackedpreserved. Own APIparents35024/10448 children16352/44524 stoppedverified, worker/parseprocessesexited;56429/56431nolisteners, sharedPG56423/Redis56424healthy. OutcomeBLOCKED realRAG, partialHTTP/worker/PGparsing proved; notmilestoneacceptance. KeepBasicexplicitcandidate/ordinaryPDFdefault; cannotrecommendformalRAGenablementfromunmeasuredquality. NeedseparatetrustedincrementoperationbeforefrozenpairedQAresume. StopnoPush/deploy/merge/Tag/optimization.

## 2026-10-16 P11 resumed by Owner600000 bounded fence / real vector A/B

1. Owner explicitly increased currentSF localfence1m→max1.6m solelyP11; unusedheadroomcannotcarryforward. New explicitone-timeCLI b33369d86dfbacc08f1762a5d8a95e01e0bdcfbd usesnormalmonthlock/CAS andimmutableOwnerintent/receipts, preservesrealaccount/month/carry/approval/allcalls. RealPGopenexit0 headroom606147; actualcloseexit0 capsatoccupied+carry1200898,headroom0,unused399102revoked. P1normalgateunchanged,noDS/nohistoryrelease.
2. RealFastAPI/Worker/PG/pgvector fixed3SFmodels;11/11sourcesreadbackSHA,11docscompleted,295chunks/295enabled1024dimindices,219assetsSHAverified,3AimageOCRworkerjobscompleted. BBasicnormalAPIparse,notoldimagequeue. Pewdefault162originalchunks/70220Unicodechars exactP10baseline; actualindex+oneDocLoadanswer, vectorPewNOT_RUN.
3. FoundmeasurementBLOCKER:first8QA document_scope triggereddirectdocload,notrequiredQueryEmbedding/vector. Preservedallcalls/results/UNKNOWN. Correctedonlyscope to identicalisolated2sourceMMLKBs/docscopeempty;4additionalMMLpairedrequests, total12cap. NoPrompt/model/retrievalthresholdchanges/nouploadreplay. Original8diagnosticsandformal4vectorscoresseparate. OmniA3DocLoaddiagnosticscontextempty/fixedrefusal;BOmni3parsed/indexedbutQA NOT_RUN duetoattemptcapafterfix,notclaimedfullOmniacceptance.
4. FormalMML:0434A noOCR, hallucinatesCIFAR10+legalbutnegativeb3quote; Bparse/SearchcorrectKaggleCatvsDog, realrerankmax.00203365<existing.3→emptyContext/fixedrefusal.0959A noOCR/nonanswer;Bkeyradio+microwave evidence retainedSearch/Rerank/Context/actualproviderwire,correctcoreanswer+1validrelevantderivedquote(page17/idx16,Unicode10575:11061). Formalexistingquotes2/2identity+offsetvalid,only1Goldsourcepositive; sourceidentitynotentailment. FullanswerprojectEM0all4;charF1A0434.1217391,A0959.0658979,B0434.4081633refusal,B0959.1851852; noofficialjudge,donotreadF1asqualityranking.
5. Actual94responses=58embedding/28chat/8rerank;86settled8UNKNOWN0reserved. Actualinput128188/output2799,total130987;newUNKNOWNheld76058,occupancy207045.12QA=7answercalls+5fixedrefusals;21auxchatseparate,72.9751sQAHTTPsum. Globalold469rows/original37UNKNOWN517098held andDShistoryunchanged;378protectedfilesunchanged. Newallledger563rows. NoRMBestimate.
6. Codeonlyfence/scorer+tests(74b0bd7361400d397783162c1abf2a2298913f5f). Finaloffline32passedexit0; actualsnapshot/score/closeoutv3/deliveryexit0. Firsthelperlongpathreadfailed→existingnativeIOhelper;secondtenantvsaccountbaselineerror→original469globalbaseline;alllogsretained,noleledgeredittofixaudit. Runtime125backendfilesunchanged;NoMigration/dependency/strategychange. ReportsP11...md/resultsupdated;originalblocked770c6d9retainedGit+Ebackup. EvidenceE:/WK/run/p11-real-20261016. OwnAPI/workerstopped,56429/56431nolisteners,PG/Redishealthy. KeepnativePDFdefault/explicitBasic,only0959demonstratesgroundedgain;0434rerankqualityandOmnivectorleftunverified. StopnoPush/deploy/merge/Tag/newcalls.

## 2026-10-16 P12 rerank diagnosis / Omni real vector A/B — COMPLETE

1. Execution source e31d1eb5915bf815dc92ec7169c8f04d4e9422d2; production backend/frontend unchanged from d7987d45c6720c97b94997c761051debf825fd91, 125 backend Python hashes saved. Added only fixed P12 fence CLI/tests using P11 same-month lock/CAS/immutable receipts; no production bug fix, threshold/Prompt/model/router/parser/schema/dependency change.
2. Saved P11 B0434 real input preserves Kaggle Cat vs Dog passage at index14; score .0007800887105986476, all max .002033646684139967 below .3/.15 => SearchNothing/fixedrefusal. 30 documents and index mapping match wire; current frozen replay exit0; same fixed upstream. Rewrite translated to Chinese, but older English exact same passage also below floor; causal effect unproven. No confirmed code defect; quality finding only.
3. Frozen three Omni pages/questions before calls, reuse original completed A/B KB/index/assets; no upload/parser/worker/index rebuild. Six formal KB vector queries, document_scope=[] all identity PASS; real FastAPI/PG/pgvector/three fixed SF models. A diagnostic1PASS1PARTIAL1FAIL/refusal; B3PASS. Full target Context A1/3 plus1partial, B3/3. Five answer requests bound derived text to actual wire; sixth fixed refusal no answer call. Citation3/3 identity/version/quote/offset valid and target-source; 2/5 model answers omit citations. No official Omni QA score/whole-corpus claim.
4. Actual45responses=28embedding+11chat(6rewrite/5answer)+6rerank;39settled actual22405(input21898/output507),6missingusageUNKNOWN held14293,0reserved. Occupancy36698. P12 increment200000 opened normal ledger and closed headroom0 at1237596; unused163302 revoked. Old563SFrows/45UNKNOWN unchanged, DeepSeek unchanged.1215P11/report+378inheritedprotectedfiles hash unchanged; documents/chunks/tasks/indexcounts unchanged. Own API stopped; shared PG/Redishealthy.
5. Offline33passedexit0; all6query collectors/snapshots/scorer/open/close/audit/delivery exit0. First two setup selection/QID mismatches and one read-only inventory wrong-column failure preserved. Evidence E:/WK/run/p12-20261016; report docs/evaluation/P12_RERANK_OMNI_REAL_AB.md/results.json. Stop at6queries; no push/deploy/merge/tag or automatic optimization. Next separately study low-score relevant evidence/section context and Cheap citation completeness; don't blindly lower threshold.

## 2026-10-16 P13 rerank low-score diagnosis / bounded controls — COMPLETE

1. Sourcebaselinecbac422d98e56a2b7aec8511762f7dd7a0aa9e5c; actualsix-callruntimea92da46337cb5fd62f431af50182de654541e278. AddedP13experimentfence/sender/tests(d959e0a4a5b409bf0a6c2dd188edaaa714d31c06); onlyfixedsenderpreflightforgeneratedsummarymissingchunkversion, bindingfrozendocumentversions withstalenessguards preserved(a92da463...). Production567filesunchanged; noPrompt/router/MinerU/threshold/registry/budgetcorechange.
2. ReusedP11/P12exact0434/0959inputs/scores; frozen31pool(original0434all30+0959positive), 2x2queryEN/actualZHRewrite vs originalslot14/sourcechaptercontext plus0959EN/manualZHlanguagecontrols. Rootvisuallyverifiedp33Codingtime! originalimage; manualtitlesmarkednotautomaticOCR; pages34..38all45locatorsmatchmiddle. Originalslot spansphysical24..35textbearingpages withmissingtitle. Contextbundle length/duplicate/confoundexplicit, noindexchanges/Goldexplanations.
3. Exactly6realSF BAAI/bge-reranker-v2-m3 calls throughnormalRuntime/Registry/Authority/P1gate. Raw/adapter186of186scoresidentical/indexbijective/sorted.0434ENoriginal.004106618rank4/ZH.000774569rank6; ENcontext.340859950rank1/ZHcontext.243264154rank1.0959EN.471884936/ZH.581011653rank1. Currentfiltered() ASTreplayexit0: R1/R2empty, R3/R4positive14(R4existing.15Top1fallback), R5/R6positive30. NoRerankproductionbug; nofullRAGQAclaim.
4. Contextbundle effectEN+ .336753331(~83x), ZH+.242489585(~314x); Englishaloneunderfloor,0959ZHbetter so notglobalChinesepenalty/unitmismatch. Prelabelledpylearnimportnegativeoutranksoriginal0434positive: loweringT.01EN/T.002ZHadmitswrongcodewhilemissingpositive. No globalthresholdchange justified. Cannotisolatetitlevsnearbytrainingtext/length/repetition; modeldeploymentversionnotpinned/singletrialno varianceestimate.
5. TrustedP13increment120000 opened1237596→1357596. SixHTTPresponses, allUNKNOWNheld90212,0newsettled/reserved; rawmeta.tokensreportinput32266/output0independently, currentadapterunrecognized—do notcallactual0/90212,do notaddreported+held,do notreleaseUNKNOWN. InitialauditlabelUNKNOWN_NO_PROVIDER_USAGE preservedbutcorrectedsidecar. Closeexit0 limit1327808/headroom0,unused29788revoked. Old608rows/51UNKNOWN/DeepSeekcalls+monthunchanged;1530priorfiles+378inherited/567productionhashunchanged;KB/docs/chunks/wholeembeddings/tasksunchanged. NoAPI/Worker/containerstarted, noembedding/chat/DeepSeekcalls;sharedPGRedishealthy.
6. Firstsenderexit1beforeexternalcall(summaryversionprecheck,0trialintents); minimalpreflightfix+6safetycounterexamples,49targetedtestspassedexit0(first43also preserved). ResumesixcallsallRECORDEDexit0, noactualrequestretry. Inventory/freeze/validate/open/close/audit/analyze/deliveryexit0. EvidenceE:/WK/run/p13-20261016, reportsdocs/evaluation/P13_RERANK_CONTROLLED_ANALYSIS.md/.results.json. Recommendationsonly: source-bound/deduplicatedmachinechaptercontextpair beforeproduction; separateofflineusagefieldcontractfixforfuturecalls, historicalUNKNOWNremain. Stopat6,nofurtheroptimization,Push/deploy/merge/Tag. Original.serena+6untrackedfilespreserved.


## 2026-10-16 P14 pure machine context validation — COMPLETE

Source baseline9dcf1de7e08ada631e4ebae263d1de12a12d88f9; executionb1ca2b20b2192777a7dbbe73daf625442ff7d5d1. Added experiment-only builder/fence/sender+tests;51targetedpass. Frozen31IDs, all30text receive same qagnostic sameDoc/version neighbor expansion capped1200chars, source/overlap/line dedup, originalanchor and generatedsummary preserved; matchedchar/anchorposition nonadjacent control. Exactly4real SFbge-reranker calls, raw/adapter124scores equal.0434original.000780089rank6/near.000316650rank23/length.000304521rank25, allcurrentfilterempty;0959near.277368695rank1 existingtop1fallback;P13original.581011653historicalonly. Knownnegativesnotretained, newsourceanswersslots4/26 separatelylabelled, futurecitationprovenance risk. No evidence to enable fixedadjacency production rule;noPrompt/router/threshold/MinerU/usageadapterchanges. Provider meta input41691/output0;4UNKNOWNheld125694 notadditive,0newsettled. Trusted140kgrant closedlimit1453502/headroom0,unused14306revoked; old614rows/57UNKNOWN/DeepSeek unchanged,1877priorfiles+378inherited+567production preserved;KB/docs/chunks/fullvectors/tasks unchanged. FirstRedis-prefixsetupfailedbeforeHTTP, frozen-v2 onlyprefix corrected; oldlogs retained,4physicalsendsonly. No newAPI/worker/container. Report docs/evaluation/P14_MACHINE_CONTEXT_VALIDATION.md/.results.json; evidenceE:/WK/run/p14-20261016. Stop afterboundedexperiment, noPush/deploy/merge/Tag/productionoptimization.


## 2026-10-16 P15 machine section boundaries — COMPLETE_OFFLINE_STOP

Baseline064f1c459f8a77160ba624dfd2812308176b3a60. Audited two sourcePDFidentities/sixsavedMinerU4.0.12Basic JSONformats and5frozencandidates/source locators.0434Codingtime machine text absent inall3;physical33imagebodyempty/footnoteonly;physical34Kaggletitle exists but no reliablechapterassociation/parent/sectionboundary;level1/2 alone insufficient.0959localwirelesstitle recognized onp16,question evidencep17titleordinarytext;negative6/24/28havereal unrelatedtitles. Do notfabricate association fromadjacency/Gold. Owneroffline-stopcondition met, realRerank0/newtokens0/newUNKNOWN0,no fenceopened. Actualfinalauditexit0,prior2audithelperfailures preserved/fixedonlystatisticsandclippedlocatormapping. ReadonlyPGLedgercheckexit0,618SFrows/61UNKNOWN/allmonths/DeepSeekunchanged vsP14;567productionhashes/priorprotected unchanged. Addedonlyofflineaudit/script+report+progress;no productionfix/strategy/parser/usagechanges. Reportdocs/evaluation/P15_MACHINE_SECTION_BOUNDARIES.md/.results.json,evidenceE:/WK/run/p15-20261016. Stop,nopush/deploy/merge/Tag orRerankoptimization.


## 2026-10-16 P16 real local image-title OCR — COMPLETE_STOP_NO_SECTION_LINK

Executionbaseline5bc48ba149f7212c7e89cb9621bbd1103dac2b2a. Exactly1existingTesseractOCR.recognize on unchangedsavedphysical33image; PyMuPDF1.26.4/Pillow12.3.0,existingeng+chi_sim150dpi/full,original1753x1277JPEG. Realoutput9lines includes Coding time! at[5,17),0.455s,noise preserved,notmanual/Gold. All9lineoffset/bboxesvalid;sourcePDF/image/middleSHA/doc/version/page32pointer retained;PDFpointprojectionapprox dueMinerUbboxrounding. Noheading/sectiontreefromOCR;P15missinglinkto34/35dataset stillunresolved,notinferredfromadjacency. StopperOwner,Rerank0/cloudtokens0/newUNKNOWN0/nofence. OCR/verify/readonlyledgerexit0;618SFrows/61UNKNOWN/allmonth/DeepSeekunchanged,567production+378inherited+P14evidence/P15inputs unchanged. Addedonlyverificationhelper/report/progress,no production/Prompt/router/threshold/MinerU/usageadapterchanges. EvidenceE:/WK/run/p16-20261016;reportsdocs/evaluation/P16_TITLE_OCR_ASSOCIATION.md/.results.json. NoPush/deploy/merge/Tag orautomaticproductionintegration.


## 2026-10-16 P16 common fixes — PARTIAL

Baseline3055bf5d8f41d42ee5fb13d3f4355292e45dc937 alreadycontainsP16OCR. Productionfixc2f7197abbf9336bea7d19e047f7a5f7c7b713ae: onlymodels/provider.py usage.py usage_facts.py, SF rerankmeta.tokens adapter separatesbilled_units,validtextinput/output+explicitimage0 derivedsum factsnotfakereportedtotal;invalid/partial/imageambiguousUNKNOWN,lowerboundpreserved,oldOpenAIrulesunchanged. Frozen10P13/P14rawresponsesSHAequal,offlineall310scoresunchanged/meta73957historicalinput,MockGatefactsverified;104affectedtestspass exit0. Citation4P9MIR/Long applicationoutputs/context/protocolreconciled,100syntheticdecoderboundariespass; providerrawbody/deltanotcaptured, generationvsdecoderUNRESOLVED, no speculativefix/auto-ref. ReadRAGFlowtaggedinsert_citations/LiteLLMfixedreranktypes,notcopythresholdstrategy. ReuseexistingrealOCRCodingtime!,missingreliablecrosspageassociation=>RerankNOT_RUN. Noactualnewmodelrequest/Token/UNKNOWN/fence;old618SFrows61UNKNOWN/month/DeepSeekunchangedreadonlyauditexit0,oldprotected/OCR/historypreserved,other564productionfilesunchanged. ReportsP16_COMMON_FIXES.md/results.json,evidenceE:/WK/run/p16-common-20261016. NoPush/deploy/merge/Tag;stopwithcitationcauseevidencelimit explicit.


## 2026-10-16 P16 Owner supplement — PARTIAL_SAFE_STOP

Baseline978e2ba67556a566dacde64ae44568cb9165ef70. ReusedP16TITLEOCRno rerun; TD-P16-0434-SECTION explicitlyrecordedoutofroundperOwner. OfficialSFresponseformat crosscheckedwith10rawP13/P14responses;billingmutationsdo notchangetokens,billingonlyUNKNOWN,10/10exit0. ProviderTokenfieldsnotinvoiceequivalence;oldUNKNOWNnotreconciled. Fourcitationfailuresrequestprotocolvalid,modelomission/modelprotocolviolation/decoderlossallUNRESOLVEDbecausepredecoderoutputNOT_CAPTURED;postdecodestorageconsistent,notconfirmedrootcause. Noautocitations/speculativefix/productionchanges. Reuse104passedtests,no redundantregression;newrealmodelNOT_RUN/token0/UNKNOWN0,stop. Reportdocs/evaluation/P16_ACCEPTANCE_SUPPLEMENT.md/.results.json,evidenceE:/WK/run/p16-common-20261016/acceptance-supplement.


## 2026-10-16 P17 Cheap real citation diagnosis — COMPLETE

Baselinecb7e1817b1350ca8f550b890196358fdc710768f; executioncc0877ec9a65b4aa802768817c1288df66e9958e. Experiment-only observer/fence/tests,37pass exit0;567production unchanged. Frozen2P9MIR queries/newsession/IDs/originalKB/docscope[]/sameactualsystemprompt/CHEAP_ONLY config. RealrawSSE→adapter→actualdecoder→attempt→persistence correlated:692002#0raw364charsvalidref→480expanded,refs1;704170#0raw249/no ref→249identical/refs0 confirmsMODEL_DID_NOT_GENERATE_CITATION,notdecoderloss/wrongformat. No productionbug/fix/auto-citation. Rawprotocol[DONE]/stop/usage present;networkEOFnotobserved flagfalse preserved. Twoanswers2825tokens+2rewrite5090+4embedding44+2rerank6834=14793actual(input14393/output400);all10newsettled,0newUNKNOWN/reserved. P16metatokensnormalP1settlementverified2reranks noextracalls.200kgrantclosedlimit1468295/headroom0/unused185207revoked;old618rows61UNKNOWN/DeepSeekunchanged;KB/docs/chunks/fullvectors unchanged.151directmanifestfilesverified plus1preexistingP16runninglogemptyhash mismatch documented,2247historical filesunchanged. OwnAPI2964+venvlauncher27656stopped,56429closed,shareddepshealthy. Wrongworkspacenewscriptbackedup/hashverified/removedonlyown;allpriorfilesretained. Reportsdocs/evaluation/P17_CHEAP_CITATION_REAL_DIAGNOSIS.md/results.json,evidenceE:/WK/run/p17-20261016. HistoricalP9causesstillunresolved;stopat2,noPush/deploy/merge/Tag/nextstage.


## 2026-10-16 P18 citation protocol generation A/B — COMPLETE

Baseline92b059fc412c7d4a0abd86a14278991e9763bba8; runtimec089cbe0813ed898618a966705043348ef42d7a8. FreezeP17actual2questions/body/context/options/registry, Aexactoriginal/Bonlycitationprotocolappend;4realCheapgeneration(no retrieval/upload/embedding/rerank/rewrite/extrajudge). Q1A/Bzero citations,core facts matchsource/limitedindependenttruthcheck;Q2A/Bzero,repeatpreregisteredsource lonepairdefinitionerror(IUPAC),Baddssourceexternalclaims. CitationgenerationA0/2 B0/2;validity/semanticsN/A denominator0,allstop/noDecoderloss. Ainput2470/output376/total2846;B2782/321/3103,+257total(~9%),all5949(input5252/output697),4settled/newUNKNOWN0/reserved0.100kgrantclosed1474244/headroom0/revoked94051;old628rows61UNKNOWN/DeepSeekunchanged;115P17files/567productionunchanged/KBdocsfullvectorsunchanged.47offlinepass2.22sexit0;first8testexpectationValueErrorfixonly,noinputchange,logsretained. No prodPrompt/Decoder/router/thresholdchange;do not enablecurrentB,smallNnotgeneral. ReportsP18_CHEAP_CITATION_PROTOCOL_AB.md/results.json,evidenceE:/WK/run/p18-20261016. Stopat4,nofurthercalls/Push/deploy/merge/Tag/nextstage.

## 2026-10-16 P19 remaining valid Gold — COMPLETE

Preparation92c90b626667e952c85f7b27cecb8f2ed443f367; real executionbec7c282be4c1cc4b49979e786e949082392d49b. Exactly17 P9 budget-not-run QIDs completed once: SciFact4, MIRACL3, LongBench5, Hotpot5; historical16 separate, MML7 no new calls, Omni no new evaluation. Real normal FastAPI/PG/pgvector/Redis/SiliconFlow, correctKB/docscope[], existing indexes; no upload/parser rebuild. Source Recall@10 Search→Context: Sci1→1, MIR1→1, Long.8→.8, Hot1→.9; source identity not answer-span sufficiency. LongDureader2 ROUGE.3370173565, Multi3 F1.2142686182; Hot5 fullanswerEM0/F1.1471853081,4 correcttarget/surface mismatch separate. NoanswerGold Sci/MIR => noQAaccuracy. Missingcites6/17; unsupported/wronginference4/17; supportgap/sourceconflict/omission4/17, nonexclusive; existingrefs13/13valid notentailment.69realcalls allsettled167408tokens (rewrite43549,embedding285,rerank86227,answer37347),17stop; newUNKNOWN0/reserved0,old632rows/61UNKNOWN unchanged.1.5M finiteincrement closedlimit1641652/headroom0,unused1332592revoked.16rawRerankmeta.tokens matchsettlement,billedunitsunused.36offlinepass exit0; preparezeroqrelpreflight corrected withpositivecoverage17; ownprocesscleanup launcherexit race fixed andstart/stop targetedrealregressionexit0/noextrasends.567production/1333historyfiles/KBdocsfullvectors/DeepSeekunchanged. ReportsP19_CHEAP_ONLY_REMAINING_GOLD.md/results.json; evidenceE:/WK/run/p19-20261016/manifest477files. Onlyexperiment scripts/tests/reports; no productionpolicychange,Push/deploy/merge/Tag/P20/GAG. Stop.

## 2026-10-17 P20 answer/evidence/citation consistency — COMPLETE

Baseline d61a26316f6b7cfff4b2e279ffa46fafdcb6d190; real audit execution 4c9854c75651221372cd5fe3b80d58be3d096601. P19 6/4/4 overlapping issues map to8uniquequestions; six rawSSEanswers exactly match persisted uncited text => modelomission, noDecoderloss. Reuse P9old4 separately, predecoderhistorystillunresolved. Frozen3supportdefectnegative+1correctpositive; AexactoldP19answers, BonlyaddsstructuredCheap postaudit, originalgeneration/query/Context/Goldunchanged.4realauditcalls stop/allsettled4934tokens(input3667/output1267), noextraanswer/retrieval/embedding/rerank/DeepSeek. Strict4/4auditoutputs fail; offline singlewholeJSONfence compat replay retainspositive1/4, holds3/4; originalfailurespreserved,noadditionalcalls. Exactsourcebinding catchesbodyc2quotewronglyassignedtotitlec1; rawCheapjudge still gives3knownfalse/overbroadSUPPORTEDentries, notsemanticdetection3/3. Paragraphsupport3/8; retained1/1conditionalbutavailability1/4, not100%overallaccuracy. Citationidentity3/3 originallyvalid butprimaryanswersemanticsupportonlypositive; noautocites/answerrewrite, ready_auto_publishfalse. Experimentalbindingchecks22tests+fence29tests pass; no productionPrompt/threshold/model/parser/routerchange.100kgrantclosedlimit1646586/headroom0/revoked95066; old701rows/61UNKNOWN/DeepSeek unchanged;567production/1812historyfiles/KBdocsfullvectors unchanged. EvidenceE:/WK/run/p20-20261016; reportsP20_ANSWER_EVIDENCE_CITATION_CONSISTENCY.md/results.json. Recommend onlyboundedprovenanceauditfurtherverification, noautomaticsemanticpublicationgate. Stopat4,noPush/deploy/merge/Tag/nextstage.


## 2026-10-17 P21 public citations and KB retrieval boundaries — COMPLETE

Baseline 8e45001737c4d0e99ff5a0c5b72ee0a16136d2b0; implementation 2c08fc6c494e2bab80eefe53709823fa35719e8d. Normal GenerationRequest citations_enabled false; Python-port UI no citation controls/chunk cards; stream/history display strips transport tags outside code without rewriting old DB messages. Unknown intents retrieve; original explicit KB/source acquisition takes precedence over wrong social/attachment/followup labels; actual history/attachment prerequisites preserved. KB SearchNothing fixed evidence-insufficient completion, no free-form answer call. No router/threshold/model/MinerU/schema/dependency changes. 273 offline tests exit0; 4 actual HTTP/PG/Redis + mock model tests exit0; Edge API-mock UI 7 unchanged cases pass plus final edited partial-stream case pass in focused rerun, build exit0. Supplemental optional matched_content assertion fails identically with baseline-loaded modules; original assertion/logs retained per bounded nonblocking issue rule. P19 UTC bypass confirmed, exact historical rewrite label NOT_CAPTURED, no invented root string. Real cloud0/token0; SF705 rows/61UNKNOWN, month/DeepSeek exactlyP20 unchanged;18 historical files preserved. Evidence E:\WK\run\p21-20261017; report P21_PUBLIC_CITATIONS_RETRIEVAL_BOUNDARIES.md/results.json. Own EviRoute containers restored stopped, test servers/workers closed, unrelated containers preserved. Original.serena+6untracked untouched. Stop; noPush/deploy/merge/Tag/nextphase.


## 2026-10-17 P22 bounded real browser product acceptance — MVP_READY

P21 production SHA1bd7d993bc56e2b9182b86476e75f3fe30742ae7 byte unchanged. Existing user Edge + real React/API/PGvector/Redis/SiliconFlow, 3 frozen UI submissions against existing KBs with document_scope=[]: normal30→8→8/source readbacks8/actualmodelcontext, empty6→0→0/fixed refusal/finalmodel0, historyfollowup no newretrieval/actualprioranswerpassed. FinalCheap2/2 stop, no retries;179/26APIanswerdeltas, SSE=persisted=UI. Refreshreopen historysameanswers/runIDs, newcalls0; midstreamdisconnect NOT_RUN. Real10calls allsettled23141tokens(input21877/output1264), rewrite7923/embed71/rerank9284/finalanswers5863; normal15.47s/refusal4.55s/followup9.32s. Old705rows/61UNKNOWN/monthidentity/DeepSeek preserved; newUNKNOWN0;100kgrantclosed1669727/headroom0/unused76859revoked. Existing11docs/287vectors unchanged/noingest. Manualsourcefactsupported, minorbase/bottomlabel imprecision; historytitlehorizontaloverflow keyboardrecoveryworks, controlledUI debt. No productionfix/model/threshold/router change, only P22 observer/fence + reports. Toolinitialfailures retained/noextra sends. Own8processes stopped, EviRoutecontainers restoredstopped, unrelatedservices intact; originaldirtyfiles preserved. Evidence E:/WK/run/p22-20261017 and reportsP22_REAL_PRODUCT_ACCEPTANCE.md/results.json. Stop/noPush/deploy/merge/Tag ornextphase.


## 2026-10-17 P23 MVP frontend UI polish — COMPLETE

Baseline `ee566490ef27b8c45c433834929e799028ea2dc1`; only `frontend/src/weknora_port/WeKnoraPythonApp.tsx`, its styles and targeted adapter tests changed. Sidebar intrinsic sizing caused long titles to expand history rows; min-width/width/overflow constraints keep Chinese, English and continuous titles clipped within the sidebar, with mouse and Enter selection verified in real Edge. Reused installed `react-markdown` + `remark-gfm`; headings, lists, bold, plain text, code, links and an HTTP image render. HTML remains escaped (no raw HTML plugin), internal reference markup is stripped, disallowed empty image URLs render no node. Updated welcome/progress wording so disabled public citations are not advertised. Playwright adapter suite 10 passed; `npm run build` exit 0; `git diff --check` exit 0. Real Edge loaded the current React build against a local read-only UI fixture; no backend, database, cloud model or user data writes. P22 acceptance files remain unchanged. No dependency, Python, DB, retrieval or model changes.


## 2026-10-21 S8.5 implementation and bounded evaluation

Branch codex/s8-5-conversation-bridge-20261021; code f9e8723b374e78de6a1a2ec9a44207035ad0d044. Scoped completed history, literal rewrite/factual retrieval, sentence-ID bounded bridge and semantic/settlement guards implemented. Final affected922pass exit0. Actual C3 seven-turn26settled40965tokens; rewrite T2/T3 correct, T7sourcecited, T1/T3unfinished and T5ambiguousfallback historical failures preserved/currentcode raw replay protected. A2/B3 firsthop/Q1/oneBGE/originalinstitutionContext verified; eventvenue and B finalidentityunsupported. Continuation 54attempts/53settled/1UNKNOWN, 77200reportedtokens +6365UNKNOWNheld; initial1237separate. No fake fullQAaccuracy, no phase5Search, P5/threshold/RRF/TopK/defaultoff unchanged, frozen4hashsame. Report docs/evaluation/S8_5_IMPLEMENTATION_REPORT.md and S8_5_CONTINUATION.results.json. No productiondeploy/push/tag or Owner milestoneacceptance.


## 2026-10-21 S8.5.1 final code acceptance gate

Baseline cb7f08cde32bd41bb56e6e56b4f96d6f0428ecf1; branch codex/s8-5-conversation-bridge-20261021. Local query fix4d3af42f8236962a63f67048df490847cbf6576c preserves ambiguity across closed answer transforms; identity/source fix46bfa329fd495cca98a361ba89c76a4eda6b9dc7 re-reads verified bounded B chain, admits original full-name anchor into actual generation Context with both encoder/citation budgets, and clears added evidence on fixed clarification. First/second hop scores unchanged; EVENT_VENUE_UNVERIFIED and old GB3 PARTIAL remain. Offline RED->GREEN: identity9fail/1pass ->242pass, citationboundary1fail -> fixed, final narrow14pass; query7fail/1pass ->186pass; selected completion21pass. These overlap, not summed or prior922rerun. Final committed code raw C3 T1/T3 +stop/length4serviceSQL cases pass: incomplete failures persist/replay without resend, old reportedusage1226/1299 retained; SQL/Redis mocked, not formalAPI realacceptance. Old476protectedfiles/455receiptfiles/11manifests preserved; B2UNKNOWN6365held unreleased. NewProvider0/settled0/UNKNOWN0/input0/output0/total0. ECS readonly actualAPI+Worker still image80e2f2d/revision29ecf2f, no bridge modules; no deploy/build/model writes. FormalAPI NOT_RUN, state BLOCKED_REAL_API_PROFILE_PREFLIGHT_AND_OWNER_AUTHORIZATION: citationprofile, per-ledger12call vs24cap aggregation, legitimate other-user HTTP and budgetdryrun required before independent Owner grant. Reports docs/evaluation/S8_5_1_ACCEPTANCE_GATE.md/.results.json; local evidence E:/RAG quention/var/reports/s8-5-1. No Push/deploy/Tag or production-ready claim.


## 2026-10-21 S8.6 A/B offline delivery

Legal baseline08ebd90; independent branch codex/s8-6-adaptive-retrieval-20261021. A commit dc093c05b4b22b6c4ed92656f633cda20cd6fcd1: opt-in QUv2/query_type, closed comparison, serial retrieval-only executor/source aggregation/configuration. B: structured validated firsthop bridge, at most one scoped newHybrid, direct relation assessment, original identity/source preservation and actual final-message delivery. A/B IMPLEMENTED/OFFLINE_PASS; combined374 preceded final bounded compatibility repairs; affected97 then Graph/Pipeline134 passed exit0, counts overlap. Two old Go-oracle intent assertions reproduced as FAIL on full08ebd90 baseline and retained. FrozenB3/GB3 original name+institution delivered to Mock final model; RELATED_ONLY, EVENT_VENUE_UNVERIFIED, historicalPARTIAL preserved. Actual Provider/settled/UNKNOWN and input/output/totalTokens all0;37protectedsourcecontracts/476oldfiles unchanged. RealQUquality/newrecallbenefit/formalAPI/PGRedis NOT_RUN; complexplanner/HyDE NOT_IMPLEMENTED. Reports docs/design/S8_6_ADAPTIVE_RETRIEVAL_V1.md and docs/evaluation/S8_6_OFFLINE_IMPLEMENTATION.md/.results.json. Defaultoff, P5/bindings/generationPrompt/threshold/RRF/TopK/schema/index preserved; noPush/deploy/build/Tag/oldgrant. Stop after local A/B delivery.


## 2026-10-21 S8.6.1 minimal review / API preflight

- Source fix f39d347; test-only profile/source 8ba0fcaf0ab665b28a04c006267e05ab10fd7e8f. Two requested production fixes only.
- Offline: affected source tests67 pass; final profile/preflight76 pass, exit0. No922 rerun.
- Current read-only ECS inventory done; old API/Worker image80e2 remains. Provider attempts/Tokens/UNKNOWN0.
- Complete no-source fallback budget blocks every proposed single ledger:5/109805,7/132474,5/109798,9/136836. No Provider batch recommended; earlier underbounds retained/superseded.
- Formal API real acceptance NOT_RUN; new image NOT_BUILT; independent second principal BLOCKED.
- Delivery: docs/evaluation/S8_6_1_API_ACCEPTANCE.md and .results.json. Pending freshOwner zero-Provider isolated preparation request only; noPush/Tag/production deploy.


## 2026-10-21 S8.6.1 approved zero-Provider isolation

- Owner批准0Provider/0Token/0QA；source8ba0fca/profileead97已构建新镜像sha256:c9b5c4b9c1d4f87d94e3109a22def827ea157c44e6800ac277a590a6544f0d43.
- Real authenticatedreadyz200/check0, PG/Redisidentity,520sourceassets/21pins/13loadedmodules verified. Both temporary candidates stopped; successfulcandidate SIGTERMexit143 explained by installedUvicorn source, rawwrapperFAIL preserved/read-onlyrecovered.
- ProductionAPI/Worker IDs/images/restarts/configSHA, ledger13041/UNKNOWN3/fingerprint, counts87indexes/scopedsourceSHA unchanged. Newcalls/Tokens/UNKNOWN0.
- Report docs/evaluation/S8_6_1_ZERO_PROVIDER_PREPARATION.md/.results.json. FormalrealQA NOT_RUN; budgetBLOCKED retained. Authorization consumed/closed; noPush/Tag/productiondeploy.


### S8.6.2：闭合规则扩展与 QU 分类接线

源码提交 `5c3f7d2db9fa832cc4c2ede070bb405a6ac96d4e`，基线352b1ba包含0c496c2；S8.6.1零Provider准备已收尾。COMPARE_SHARED/v1、PARALLEL_ATTRIBUTE/v1和SIMPLE_PARALLEL已接入单次非思考QU与原串行retrieve_once，最多3单元。原B在安全性方面误拆先RED后修复；固定66例24/24正例、0/31反例误拆、11/11门控；扩展集合Owner逐条确认NOT_RUN。最终受影响离线322passed exit0，实际Provider/Token0。112保护文件、91既有合同、476旧证据与旧UNKNOWN冻结记录未改；真实QU/答案/API验收NOT_RUN。无Push/部署/P5/Schema/索引/阈值改动；本轮结束。报告：[S8_6_2_RULE_COVERAGE.md](docs/evaluation/S8_6_2_RULE_COVERAGE.md)。
