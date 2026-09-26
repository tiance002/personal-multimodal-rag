# 代码审查与架构评估报告

- **项目**：Personal Multimodal RAG Knowledge Agent V1.0（`E:\RAG quention`）
- **审查日期**：2026-09-26
- **审查范围**：`backend/`（3,781 行 Python）、`frontend/src/`（222 行 TS/TSX）、`scripts/`（27 个脚本）、`alembic/`、`contracts/`、`deploy/`、根目录与运行时产物
- **审查方式**：逐文件通读 + AST 静态分析（未使用导入 / 无引用定义）+ 跨文件引用检索 + 分层依赖方向核查 + 设计文档（`docs/superpowers/specs/...design.md`）与实际实现比对
- **未执行**：未运行测试、未启动服务、未连接数据库（本报告为静态审查，所有结论均可按"位置信息"复核）

---

## 一、结论摘要

| 维度 | 评级 | 主要问题 |
|---|---|---|
| 代码冗余 | 中 | `ports/` 与 4 个适配器为死代码；内存版摄取与 Postgres 版逻辑重复 |
| 历史遗留文件 | 中 | `archive/`、`var/`、`dist/`、`output.json`、1.9MB 报告共约 6MB 运行时产物留在工作区 |
| 代码质量 | 中偏差 | 前端组件压缩为单行；`routes.py` 399 行混合职责；脚本硬编码本机绝对路径 |
| 文件必要性 | 中偏差 | 声明的组合根 `bootstrap.py` 从未被调用；3 项 `Settings` 配置为死配置 |
| 模块内聚 | 偏差 | `PostgresKnowledgeRepository` 单类承担 8 类聚合根职责（355 行） |
| 系统耦合 | 偏差 | 适配层反向依赖应用层 6 处；HTTP 层直接 new 具体适配器 |
| 架构分层 | 偏差 | 依赖方向与设计文档声明不符；ports 层形同虚设 |
| 性能 | 偏差 | 向量检索未走 pgvector 索引，全量拉取 chunk+embedding 后在 Python 逐条算余弦 |

**统计**：共识别 **61 项**问题 —— P1（高，须在下次交付前处理）**14 项**、P2（中）**30 项**、P3（低）**17 项**。其中 `Q-02` 与 `C-02`、`F-01` 与 `A-02` 为同一缺陷在两个维度下的交叉引用，去重后约 **59 项独立缺陷**。

**总体判断**：项目**领域层（domain）质量优秀**（纯模型、无框架污染、不变量清晰），**测试覆盖扎实**（29 个测试文件、41 个用例，覆盖不变量、失败隔离、并发预算）。主要风险集中在**三层之外**：适配层/应用层的依赖方向被破坏、组合根缺失、`PostgresKnowledgeRepository` 上帝对象，以及检索路径的性能设计缺陷。这些不是"能否运行"的问题，而是**可维护性与可演进性**问题。

---

## 二、代码冗余检查

### R-01 `ports/repositories.py` 整个模块无人引用，且协议签名与实现不一致
- **位置**：`backend/app/ports/repositories.py:1-21`
- **问题**：`KnowledgeRepository` / `EvidenceRepository` 两个 Protocol 在全仓库**零引用**（AST 确认）。更严重的是其签名已与真实实现脱节：
  - 协议声明 `list_active_chunks(knowledge_base_ids, document_ids=None)`，实际实现是 `list_active_chunks(scope: Scope)`；
  - 协议声明 `get_chunk()` / `write_chunks()`，实际实现是 `get()`，且没有 `write_chunks`。
- **影响**：一个"看起来像架构资产、实际是过期文档"的模块，会误导后续开发者以为存在可替换的持久化端口。
- **建议**：二选一 —— ① 删除该模块；② 按真实调用面重写协议，并让 `PostgresKnowledgeRepository` / `InMemoryRetrievalRepository` 显式实现（`class X(KnowledgeRepository)`），从而让 `HybridRetriever` 依赖抽象而非具体类。
- **优先级**：**P1**

### R-02 内存版摄取实现与 Postgres 版逻辑重复（双份真相）
- **位置**：`backend/app/application/ingestion.py:122-196`（`IngestionService.submit_upload` / `IngestionWorker.process`）vs `backend/app/adapters/postgres/knowledge_repository.py:74-188`（`create_upload` / `process_job`）
- **问题**：上传去重策略（`new_version` / `skip`）、版本号递增、`OCR_UNAVAILABLE` 判定、`activate_if_current` 语义、job 状态机（`queued→running→succeeded/failed`）在内存实现与数据库实现中各写了一遍。两者已出现行为漂移：内存版 `put_stream(stream)` **不传 `max_bytes`**（无上传大小限制），Postgres 路径则通过 `routes._submit_upload` 传入 `settings.max_upload_bytes`。
- **影响**：任何摄取规则变更都需改两处，且测试覆盖的是内存版（与生产路径不同），存在"测试通过但生产行为不同"的风险。
- **建议**：把状态机与去重策略上提为领域/应用层纯函数（如 `IngestionStateMachine`），内存版与 Postgres 版仅保留"存取"差异；或明确将内存版标注为 `tests/fakes`，移出 `app/` 生产包。
- **优先级**：**P2**

### R-03 两套证据解析路径，其中一套仅测试使用
- **位置**：`backend/app/domain/evidence.py:31-49`（`EvidenceResolver`）vs `backend/app/application/citations.py:59-91`（`CitationService.freeze/resolve`）
- **问题**：`EvidenceResolver` 只在 `backend/tests/test_evidence.py` 中被引用，生产链路全部走 `CitationService`。两者都做"哈希校验 + 回读原文包含性检查"。
- **影响**：同一不变量有两份实现，未来只改一处会造成语义分叉。
- **建议**：让 `CitationService.resolve` 复用 `EvidenceResolver`（或反之），删除重复实现。
- **优先级**：**P2**

### R-04 未使用的私有工具函数与死方法
- **位置**：
  - `backend/app/adapters/postgres/knowledge_repository.py:23-24` `_str()` —— 定义后零引用
  - `backend/app/adapters/postgres/knowledge_repository.py:324-327` `persist_retrieval_hits()` —— 定义后零调用（`retrieval_hits` 表因此永远为空）
  - `backend/app/adapters/postgres/knowledge_repository.py:210-215` `read_chunk()` —— 定义后零调用
  - `backend/app/adapters/postgres/knowledge_repository.py:34-36` `from_url()` classmethod —— 定义后零调用
- **影响**：`retrieval_hits` 表建了却永不写入，是"半成品功能"；`read_chunk` 是 `EvidenceResolver` 本应使用的读取器，说明证据解析确实存在两套路径（见 R-03）。
- **建议**：`persist_retrieval_hits` 若属于检索可观测性需求，应在 `_answer_message` 中真正调用；否则删除方法并评估 `retrieval_hits` 表是否该保留。`read_chunk` 与 `EvidenceResolver` 一并处理。`_str`/`from_url` 直接删除。
- **优先级**：**P2**

### R-05 未使用的导入（AST 全量扫描结果，已排除 `from __future__` 误报）
- **位置**：
  | 文件 | 行 | 未使用导入 |
  |---|---|---|
  | `backend/app/api/routes.py` | 8, 9, 12 | `HTTPException`、`PlainTextResponse`、`run_in_threadpool` |
  | `backend/app/adapters/postgres/graph_repository.py` | 4, 7 | `uuid`、`Engine` |
  | `backend/app/adapters/postgres/knowledge_repository.py` | 17 | `NormalizedDocument` |
  | `backend/app/application/retrieval.py` | 4, 5 | `uuid`、`field` |
  | `backend/app/application/rag_orchestrator.py` | 13 | `RetrievalResult` |
  | `backend/app/application/agent_runtime.py` | 6 | `Any` |
  | `backend/app/application/ingestion.py` | 5 | `BytesIO` |
  | `backend/app/application/assets.py` | 4 | `Path` |
  | `scripts/smoke_m4.py` | 10 | `create_engine` |
  | `backend/tests/test_parsers.py` | 1 | `Path` |
- **影响**：`run_threadpool` 未使用意味着**同步阻塞的 SQLAlchemy 调用全部跑在 FastAPI 的事件循环线程池默认行为上**（见 P-09），属于"导入提示了一个被放弃的设计意图"。
- **建议**：清理导入；同时把 `run_in_threadpool` 的缺失作为 P-09 处理。
- **优先级**：**P3**

### R-06 标题解析逻辑重复
- **位置**：`backend/app/adapters/parsers/__init__.py:18-55`（`_sectioned_text` 解析 `#{1,6}` 并构造 `heading_path`）vs `backend/app/application/graph.py:77-80`（`GraphService._label` 用正则重新解析标题）
- **问题**：Markdown 标题的正则与层级语义在两处独立实现。
- **建议**：`GraphService._label` 直接使用 `ChunkRecord` 已有的 `heading_path`（chunk 表已存该字段），避免二次解析。
- **优先级**：**P3**

---

## 三、历史遗留文件与产物清理

> 说明：以下文件**多数已在 `.gitignore` 中声明**，因此不会污染版本库；但当前工作区**不是 Git 仓库**（`README.md:84`、`progress.md:4` 均已声明），这些产物会直接暴露给打包/发布流程。`.planning/findings.md` 亦已记录"发布前必须排除 `.venv`、`var/`、缓存、构建产物"。

### L-01 `archive/` 目录：68 个历史输出快照
- **位置**：`archive/output_*.json`（68 个文件，约 602 KB）
- **问题**：文件名形如 `output_1790363086.2279131.json`，为早期会话的输出快照，无任何代码/脚本引用。
- **建议**：删除，或迁移到 `var/archive/` 并保留一份说明；若确需留存，改为 `docs/history/` 并加 README 说明用途。
- **优先级**：**P2**

### L-02 根目录测试产物残留
- **位置**：`output.json`（1.7 KB，pytest-json 单次运行结果，含 2026-09-26 的 5 个用例结果）、`pytest_html_report.html`（**1.9 MB**，pytest-html 报告）
- **问题**：两者均为一次性测试运行产物，位于项目根目录，易被误认为项目资产。注意 `output.json` 内容仅覆盖 4 个 suite / 5 个用例，与 `progress.md` 声称的 41 个测试**不一致**，属于过期证据。
- **建议**：删除；报告统一输出到 `var/reports/`（该目录已是权威报告位置，见 `README.md:55`）。
- **优先级**：**P2**

### L-03 `var/` 目录堆积约 3 MB 冒烟与恢复临时目录
- **位置**：`var/` 下 40+ 个目录，含：
  - `smoke-api-storage-*`（10 个）、`smoke-m2-storage-*`（8 个）、`smoke-m3-storage-*`（10 个）、`smoke-m4-storage-*`（8 个）、`smoke-m1-storage`
  - `pytest-tmp`、`pytest-tmp-m0`、`pytest-tmp-m2`、`pytest-tmp-regression`（其中多个 **权限拒绝**，无法被普通 `find` 遍历）
  - `restore-20260926-050844` / `-052309` / `-055604` / `-060050`、`restore-db-test`、`restore-manifest-test`、`restore-release-test`
  - `cleanup-storage`、`logs`、`backups`
- **问题**：冒烟脚本每次运行生成一个带随机后缀的 storage 目录且不清理，属**测试残留累积**。`pytest-tmp*` 权限异常说明存在未正常释放的临时目录。
- **建议**：① 冒烟脚本改用 `tempfile.TemporaryDirectory()` 并在结束时清理；② 立即清理 `var/` 下除 `var/reports/` 与 `var/backups/` 以外的内容；③ 在 `.gitignore` 中补充 `var/*/` 的显式排除说明。
- **优先级**：**P2**

### L-04 前端构建产物入库
- **位置**：`frontend/dist/`（`index.html` + `assets/index-CNZJpVPD.js` 233 KB + `assets/index-CmI0EH69.css` 9.4 KB，共 241 KB）
- **问题**：构建产物与源码并存。`.gitignore:12-13` 已忽略 `dist/`、`frontend/dist/`，但目录仍在工作区，且 `deploy/Dockerfile.frontend` 是否依赖它需确认（见建议）。
- **建议**：确认 Dockerfile 内自行构建后删除工作区产物；若 Dockerfile 依赖现成 `dist/`，应改为多阶段构建，避免产物与源码不同步。
- **优先级**：**P2**

### L-05 缓存与元数据目录
- **位置**：`personal_rag.egg-info/`（`PKG-INFO`/`SOURCES.txt` 等 5 个文件）、`.pytest_cache/`（**权限拒绝**）、`.docx-reference-render/`（空目录）、`frontend/test-results/`（空目录）
- **建议**：全部清理；`.docx-reference-render/` 若为 DOCX 渲染工具遗留，确认无用后删除并从 `.gitignore` 移除。
- **优先级**：**P3**

### L-06 设计文档双份并存且已漂移
- **位置**：`.planning/2026-09-26-personal-rag-v1/`（`findings.md`/`progress.md`/`task_plan.md`）与 `docs/superpowers/{specs,plans}/`（设计 + 实现计划）
- **问题**：`findings.md` 记录了 DOCX 渲染阻断、ADR 路径错误等"过程性"内容，与 `docs/superpowers/` 的正式设计并存，存在**信息重复与版本漂移**（例如 `findings.md` 提到 `docs/adr/ADR-002-budget-and-publish.md` 不存在，实际是 `ADR-002-budget-and-public-contracts.md`）。
- **建议**：`.planning/` 定位为临时过程记录（保留在 `.gitignore`），正式设计只保留 `docs/superpowers/`；清理 `findings.md` 中已失效的路径与结论。
- **优先级**：**P3**

### L-07 设计文档声明的目录结构与实际实现不一致
- **位置**：`docs/superpowers/specs/2026-09-26-personal-rag-v1-design.md:66-113`（Repository layout）vs 实际
  | 设计声明 | 实际 |
  |---|---|
  | `backend/worker.py` | `backend/app/workers/ingestion.py` |
  | `frontend/src/pages/` | 不存在（无 `pages/` 目录） |
  | `frontend/src/state/` | `frontend/src/app/state.ts` |
  | `docs/adr/glossary.md` | 不存在 |
  | `scripts/verify-m0.ps1 ... verify-release.ps1` | 存在，但另有 14 个未在文档中登记的 `.py` 脚本 |
- **影响**：文档作为"契约"被 AGENTS.md 要求优先读取（`AGENTS.md:8`），过期的目录声明会误导后续会话。
- **建议**：按实际结构更新设计文档，或在文档中注明"布局为示意，以实际文件为准"。
- **优先级**：**P2**

### L-08 空目录残留
- **位置**：`frontend/test-results/`、`.docx-reference-render/`
- **建议**：删除。
- **优先级**：**P3**

---

## 四、代码质量评估

### Q-01 前端组件被压缩为超长单行，可维护性严重受损
- **位置**：
  | 文件 | 最长行 | 总行数 |
  |---|---|---|
  | `frontend/src/components/ChatPanel.tsx` | **1,571 字符** | 11 |
  | `frontend/src/components/GraphPanel.tsx` | **1,242 字符** | 13 |
  | `frontend/src/app/App.tsx` | **1,154 字符** | 34 |
  | `frontend/src/components/KnowledgeBasePanel.tsx` | **748 字符** | 17 |
- **问题**：`ChatPanel` 把整个 JSX 树、事件处理、模式切换压进第 10 行一行；`App.tsx` 把 8 个 handler 与整棵三栏 JSX 压进第 33 行。这不是"紧凑"，而是**不可读、不可 diff、不可 code review**：任何改动都会产生整行 diff。
- **影响**：直接违反 `AGENTS.md:11`"只做当前任务所需的最小变更"的可执行性 —— 在单行组件上无法做最小变更。
- **建议**：按语义拆行；将 `App.tsx` 的 handler（`selectBase`/`newConversation`/`upload`/`send`/`openCitation`）抽到 `frontend/src/app/hooks/` 或自定义 hook（如 `useWorkbench()`）；引入 Prettier 并设 `printWidth: 100`，纳入 `npm run build` 前置检查。
- **优先级**：**P1**

### Q-02 `routes.py` 单文件混合三层职责
- **位置**：`backend/app/api/routes.py`（399 行）
- **问题**：该文件同时承担：
  1. **HTTP 边界**（Pydantic DTO、状态码、错误信封）—— 职责正确；
  2. **组合根**（`_store()` 内 `create_engine` + `OllamaGateway` + `ContentAddressedStorage` + `PostgresBudgetGate`，L81-89）—— 应属 `bootstrap.py`；
  3. **用例编排**（`_answer_message` L268-324：创建 run、写事件、构造 orchestrator、执行 smart/quick 分支、冻结证据、落库、写消息）—— 应属 `application/`。
- **影响**：单文件改动风险高；`_answer_message` 长达 57 行且内联 `from ... import`（L215、L289-291），违反"导入置顶"惯例；三条 SSE 事件序列（`run.created`→`retrieval.started`→…）散落在编排代码中，契约难以核对。
- **建议**：① 把 `_store()` 及所有适配器构造迁入 `bootstrap.py`，通过 `app.state` 注入；② 把 `_answer_message` 上提为 `application/answer_service.py::AnswerService.answer(conversation, content, mode)`；③ `routes.py` 只保留 DTO、路由与错误映射，目标 < 150 行。
- **优先级**：**P1**

### Q-03 后端 SQL 语句单行过长
- **位置**（>200 字符，共 20+ 处）：
  - `backend/app/adapters/postgres/knowledge_repository.py:138`（**581 字符**）、`:94`（381）、`:156`（369）、`:130`（226）
  - `backend/app/adapters/postgres/graph_repository.py:31`（415）、`:33`（419）、`:35`（397）、`:18`（265）、`:43`（275）、`:63`（277）
  - `backend/app/adapters/postgres/agent_repository.py:37`（307）
- **影响**：SQL 与参数 dict 压在同一行，字段与占位符错位时极难发现；`:138` 一行内混入三目运算与错误码分支。
- **建议**：SQL 用三引号多行书写（同文件 `:154-157` 已有良好范例）；参数 dict 每键一行；单行上限 120 字符，纳入 CI。
- **优先级**：**P2**

### Q-04 `chunking.py` 用动态 `type()` 伪造默认分节对象
- **位置**：`backend/app/domain/chunking.py:22-28`
```python
sections = document.sections or [
    type("DefaultSection", (), {"start": 0, "end": len(document.markdown_content), "heading_path": ()})()
]
```
- **问题**：为让后续 `section.start/end/heading_path` 可用，运行时拼装一个匿名类。丢失类型信息、无法静态检查、IDE 无法跳转，且 `heading_path` 为 `tuple` 而 `DocumentSection.heading_path` 也是 tuple —— 完全可用真实类型。
- **建议**：改用领域模型：`sections = document.sections or [DocumentSection(section_id="section-0", heading="", start=0, end=len(...), level=0)]`。
- **优先级**：**P2**

### Q-05 `patch_settings` 错误信息与实际允许项不符
- **位置**：`backend/app/api/routes.py:382-385`
- **问题**：`allowed = {"local_query_enabled", "monthly_cloud_budget_microunits"}`，但报错文案为 `"only local_query_enabled can be changed at runtime"`。同时该接口接受 `monthly_cloud_budget_microunits` 却未校验其是否受 `cloud_enabled` 约束。
- **建议**：修正文案为实际允许项；补充"预算 > 0 但 `cloud_enabled=false` 时的语义"说明。
- **优先级**：**P3**

### Q-06 默认数据库端口与文档/示例不一致
- **位置**：`backend/app/config.py:24`（默认 `...@127.0.0.1:5432/rag`）vs `.env.example:6`、`README.md:25`、`deploy/compose.yml:9`（均为 `55432`）
- **影响**：未设置 `RAG_DATABASE_URL` 时按 `Settings` 默认值会连到 5432（本机 PostgreSQL 常见默认端口），可能**静默连到错误数据库**而非明确失败。
- **建议**：默认值改为与 Compose 一致的 `55432`，或移除默认值改为必填并在启动时明确报错。
- **优先级**：**P2**

### Q-07 前端依赖全部使用 `latest`，构建不可复现
- **位置**：`frontend/package.json:12-23`（`vite`、`react`、`typescript`、`@playwright/test` 等 8 个依赖均为 `"latest"`）
- **问题**：直接违反 `AGENTS.md:49`"任何新依赖先验证包名、**锁定版本**"。虽有 `package-lock.json`，但 `npm install` 在 lock 缺失/漂移时会拉取任意最新版。
- **建议**：改为精确版本（与 lock 文件一致），并考虑 `npm ci`。
- **优先级**：**P2**

### Q-08 `playwright.config.ts` 硬编码本机 Chrome 路径
- **位置**：`frontend/playwright.config.ts:11`：`executablePath: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"`
- **影响**：换机器/换用户即失败；违反可移植性。同时 `webServer.command` 固定 `--host 127.0.0.1` 与 `baseURL` 5173 亦为硬编码。
- **建议**：删除 `executablePath`，改用 Playwright 自带浏览器（`npx playwright install`）；或改为读环境变量 `PW_CHROME_PATH`。
- **优先级**：**P2**

### Q-09 发布门禁脚本硬编码本机绝对路径与仓库外依赖
- **位置**：`scripts/verify-release.ps1:20-21`
```powershell
& 'D:\Drivers\python\python.exe' 'C:\Users\22088\.agents\skills\webapp-testing\scripts\with_server.py' --server '...' --port 8000 ...
```
- **问题**：
  1. 依赖**仓库之外**的个人技能目录 `C:\Users\22088\.agents\skills\webapp-testing\scripts\with_server.py`（`find` 确认仓库内不存在该文件），该文件不属于本项目，任何其他环境都无法复现；
  2. 硬编码 `D:\Drivers\python\python.exe` 与 `E:\RAG quention\.venv\Scripts\python.exe` 两个绝对路径；
  3. 该门禁在 `progress.md:18` 被记为 `PASS`，但实际依赖一个未纳入版本控制的外部脚本 —— 属"验收证据不可复现"。
- **影响**：`verify-release.ps1 -Fresh` 是发布门禁，其可复现性直接决定发布证据的可信度。
- **建议**：将 `with_server.py` 的等价能力（启动服务 → 等端口 → 执行命令 → 关停）内化为 `scripts/run_with_server.ps1`；所有路径改为基于 `$PSScriptRoot` / `Join-Path (Get-Location)` 的相对路径；在报告中明确标注该外部依赖曾被使用。
- **优先级**：**P1**

### Q-10 `AgentRuntime` 用空格分词估算 token 数
- **位置**：`backend/app/application/agent_runtime.py:71`：`token_count = len(answer.answer.split())`
- **问题**：`AgentLimits.max_tokens=4000` 的判定依据是英文空格分词数。对中文（本项目主要语种）几乎不产生切分，`split()` 结果接近 1，导致 **`AGENT_TOKEN_LIMIT` 永远不会触发**（限制形同虚设）。
- **建议**：改用真实 tokenizer 估算，或对中文按字符数估算（如 `len(text)` 与 2 倍系数），并在测试中覆盖中文超限场景。
- **优先级**：**P2**

### Q-11 `healthz` 重复生成 `request_id`，绕过中间件
- **位置**：`backend/app/main.py:18-34`
- **问题**：`request_id_middleware` 已为每个请求写入 `request.state.request_id`，但 `healthz` 又独立 `str(uuid4())`，导致健康检查的 `meta.request_id` 与中间件记录不一致（若后续接入访问日志将无法关联）。
- **建议**：`healthz` 复用 `request.state.request_id`。
- **优先级**：**P3**

---

## 五、文件必要性审查（非必需文件与死配置）

### F-01 声明的组合根 `bootstrap.py` 从未被调用
- **位置**：`backend/app/bootstrap.py:9-11`（`build_app`）；设计文档 `...design.md:79` 明确将其定义为 "composition root"
- **证据**：AST 扫描确认 `build_app` 全仓库零引用；`README.md:27` 与 `deploy/Dockerfile.api` 均以 `backend.app.main:app` 启动。
- **影响**：这是**架构意图与实现的直接冲突** —— 设计声明"基础设施只在组合根装配"，实际装配发生在 `routes.py`。`bootstrap.py` 成为"架构文档的装饰品"。
- **建议**：让 `bootstrap.build_app()` 成为唯一入口：在其中构造 engine / storage / ollama / repositories / budget_gate / parser registry，注入 `app.state`；`main.py` 仅保留 `app = build_app()`；`routes.py` 通过 `Depends` 或 `app.state` 取用。
- **优先级**：**P1**

### F-02 ~ F-05 四个从未被引用的适配器/服务（死代码）
- **位置与证据**（AST 全仓库扫描，零外部引用）：
  | 文件 | 符号 | 说明 |
  |---|---|---|
  | `backend/app/adapters/models/cloud.py:6` | `CloudDisabledGateway` | 云禁用边界桩，从未装配 |
  | `backend/app/adapters/ocr/local.py:13` | `LocalOcrProvider`（含 `CapabilityReport`） | OCR 能力探针，从未装配 |
  | `backend/app/adapters/graph/extractor.py:6` | `DeterministicStructureExtractor` | 图谱抽取器，从未装配（`GraphService` 被 routes 直接调用） |
  | `backend/app/application/assets.py:23` | `AssetService`（含 `AssetDetail`） | 资产服务，从未装配（`routes.py:168` 直接返回裸 dict） |
- **影响**：四者都"看起来是架构的一部分"，实际从未进入运行链路。`progress.md:23` 声称"当前本机没有已验证 OCR/VLM adapter"，而 `LocalOcrProvider` 正是该能力的占位实现 —— 占位符与真实能力声明脱节。
- **建议**：按"保留但标注 / 删除"二选一。推荐：
  - `CloudDisabledGateway`、`LocalOcrProvider`：保留，但**必须在组合根中真实装配**（云禁用与 OCR 不可用是 V1 的显式边界，应可被测试断言），并在文件头注释标注"V1 边界桩，由 bootstrap 装配"。
  - `DeterministicStructureExtractor`：删除（与 `GraphService` 职责重叠）。
  - `AssetService`：接入 `routes.get_document_asset` / `get_document_content`，替换裸 dict 返回。
- **优先级**：**P2**

### F-06 `PostgresRetrievalRepository` 空子类从未使用
- **位置**：`backend/app/adapters/postgres/retrieval_repository.py:6-10`
- **问题**：无任何方法体的空子类，注释称"retrieval port 的具名适配器"，但该 port（`ports/repositories.py`）本身也是死的（R-01）。
- **建议**：与 R-01 一并处理 —— 若重建 ports，则此类应实现协议；否则删除。
- **优先级**：**P3**

### F-07 `M1_TABLES` 常量未被使用
- **位置**：`backend/app/adapters/postgres/schema.py:10-25`
- **问题**：`M0_TABLES` 与 `V2_TABLES` 被 `backend/tests/test_schema_check.py` 使用，唯独 `M1_TABLES` 定义后零引用。测试只断言"M0 恰好 4 表且与 V2 不相交"，未覆盖 M1 的 12 张表。
- **建议**：在 `test_schema_check.py` 中补充 M1 表集合断言（这本身是有价值的不变量），或删除常量。
- **优先级**：**P3**

### F-08 `ports/providers.py` 中 3 个符号未被使用
- **位置**：`backend/app/ports/providers.py:12-18`（`ChatResult`）、`:28-29`（`ChatProvider.complete_json`）
- **问题**：`EmbeddingProvider`/`EmbeddingResult`/`ProviderUnavailable` 被 `ollama.py` 使用；但 `ChatProvider`（声明 `complete_json`）从未被实现或使用 —— `OllamaGateway` 实际提供的是 `query_expand` / `answer`，方法名与协议不符。与 R-01 同类的"协议与实现脱节"。
- **建议**：把 `ChatProvider` 改为反映真实能力的协议（`query_expand` / `answer`），或删除。
- **优先级**：**P3**

### F-09 死配置：`max_chunk_chars` / `chunk_overlap` 从未生效
- **位置**：`backend/app/config.py:30-31, 56-57`（定义 + 环境变量读取）vs 实际调用点
  - `backend/app/adapters/postgres/knowledge_repository.py:142`：`chunks = chunk_document(normalized)` —— 未传参
  - `backend/app/application/ingestion.py:184`：`version.chunks = chunk_document(normalized)` —— 未传参
- **问题**：`chunk_document(document, max_chars=1200, overlap=120)` 的默认值恰好等于 `Settings` 默认值，因此**表面一致掩盖了配置失效**：用户设置 `RAG_MAX_CHUNK_CHARS=2000` 后不会产生任何效果。若设置 `RAG_CHUNK_OVERLAP=2000`（≥ max_chars）还会在 `chunk_document:19-20` 抛 `ValueError`，但错误发生在摄取任务内部，仅被记录为 job 失败。
- **建议**：把 `Settings` 的这两个值注入 `ParserRegistry`/`process_job` 调用链；或在 `Settings.from_env` 中做 `overlap < max_chars` 的前置校验并快速失败。
- **优先级**：**P2**

### F-10 `QualityReason.LOW_COVERAGE` 为死枚举
- **位置**：`backend/app/application/quality.py:11`
- **问题**：`QualityGate.evaluate` 只在 `result.items` 为空时拒绝（`NO_CANDIDATES`），"覆盖率不足"这一质量门从未实现，`LOW_COVERAGE` 定义后零引用。设计文档 `...design.md:166` 提到的"Quality Gate → 最多一轮定向补检"中的"覆盖率"维度缺失。
- **建议**：要么实现覆盖率判定（例如检索结果数与查询词覆盖率阈值），要么删除枚举并在文档中说明 V1 仅做空结果判定。
- **优先级**：**P3**

### F-11 `SourceLocator.kind` 的 `"image"` 分支从未被产出
- **位置**：`backend/app/domain/models.py:11`（`Literal["text","markdown","pdf","image"]`）vs `backend/app/adapters/parsers/__init__.py:131-148`（`ImageParser` 只产出 `assets`，`source_locators` 为空）
- **问题**：图片文件的 `SourceLocator` 永远为空列表，`kind="image"` 无生产者。设计文档 `...design.md:123` 要求"图片源资产保留并带 locator"。
- **建议**：让 `ImageParser` 产出 `SourceLocator(kind="image", quote=None)`（或补充 bbox），使定位契约自洽；或从 Literal 中移除 `"image"`。
- **优先级**：**P3**

---

## 六、模块设计分析（内聚性与单一职责）

### C-01 `PostgresKnowledgeRepository` 是上帝对象，违反单一职责
- **位置**：`backend/app/adapters/postgres/knowledge_repository.py:27-355`（355 行，1 个类）
- **职责清单**（可归并为 8 类聚合根）：
  1. 知识库 CRUD —— `create/list/get/update/delete_knowledge_base`
  2. 文档与版本 —— `create_upload`、`get_document`、`list_documents`
  3. 摄取任务 —— `process_job`、`get_job`、`retry_job`
  4. 检索 —— `list_active_chunks`、`get`、`read_chunk`
  5. 会话与消息 —— `create/list/get/update/delete_conversation`、`append_message`、`list_messages`
  6. 运行与事件 —— `create_run`、`append_event`、`list_events`、`complete_run`、`cancel_run`
  7. 证据 —— `persist_evidence`、`persist_retrieval_hits`、`get_citation`
  8. 资产与内容 —— `list_assets`、`get_asset`、`get_document_content`、`list_chunks`
- **影响**：
  - 任何一层改动都会触碰同一文件，**回归面 = 全量**；
  - `PostgresGraphRepository` 通过**继承**复用它（C-03），使图谱适配器隐式获得全部 8 类能力；
  - 该类的类型被 `routes.py` 用作 `_answer_message(store: PostgresKnowledgeRepository, ...)` 的参数类型 —— 即**应用编排直接依赖具体适配器**（D-03）。
- **建议**：按聚合边界拆为 `KnowledgeBaseRepository` / `DocumentRepository` / `IngestionJobRepository` / `ChunkRepository` / `ConversationRepository` / `RunEventRepository` / `EvidenceRepository`，共享一个 `Engine`。配合 R-01 的 ports 重建，使应用层依赖协议集合而非单一巨类。**建议分批：先拆"会话/消息/运行/事件"（与 RAG 主链路解耦），再拆"检索/证据"。**
- **优先级**：**P1**

### C-02 HTTP 层与组合根/编排层混居
- **位置**：`backend/app/api/routes.py`（同 Q-02）
- **建议**：见 Q-02。
- **优先级**：**P1**

### C-03 `PostgresGraphRepository` 以继承方式复用父类，属 is-a 误用
- **位置**：`backend/app/adapters/postgres/graph_repository.py:15`：`class PostgresGraphRepository(PostgresKnowledgeRepository)`
- **问题**：图谱适配器只需要 `engine` 与 `storage`，却通过继承获得 30+ 个方法。这是**继承换组合**的典型误用，导致图谱模块与知识库模块形成最强的耦合形式（代码继承）。
- **建议**：改为组合：`PostgresGraphRepository(engine, storage)` 独立持有所需依赖；若确需复用查询，抽为共享的 `SqlExecutor` 辅助类。
- **优先级**：**P2**

### C-04 `HybridRetriever` 三类算法内聚在一个方法
- **位置**：`backend/app/application/retrieval.py:75-108`
- **问题**：`retrieve()` 单方法内完成"关键词打分 + 向量打分 + RRF 融合 + 排序 + 记录来源"，共 34 行，三处排序逻辑重复（`sort(key=lambda hit: (-float(hit.raw_score or 0), hit.chunk_id))` 出现两次）。
- **建议**：抽出 `_keyword_hits(chunks, plan)`、`_vector_hits(chunks, question)` 两个私有方法，排序键提为模块级常量；融合已由 `domain.fusion.rrf_fuse` 承担（此处设计良好）。
- **优先级**：**P2**

### C-05 `RAGOrchestrator.answer_query` 单方法承载 5 类关注点
- **位置**：`backend/app/application/rag_orchestrator.py:68-122`（55 行）
- **关注点**：云外发准入判定、模型查询计划、检索+质量门+重试、预算预留/结算/异常记账、引用标签校验。
- **建议**：抽出 `_guard_cloud_egress()`、`_retrieve_with_quality_gate()`、`_reserve_budget()`、`_validate_citations()`；主流程保持线性可读。
- **优先级**：**P2**

---

## 七、系统耦合度检查

### D-01 适配层反向依赖应用层（6 处），违反设计声明的依赖方向
- **位置**：
  | 文件 | 行 | 被依赖的应用层符号 |
  |---|---|---|
  | `backend/app/adapters/models/ollama.py` | 10 | `application.model_policy.QueryGatewayResult` |
  | `backend/app/adapters/postgres/knowledge_repository.py` | 15 | `application.retrieval.ChunkRecord` |
  | `backend/app/adapters/postgres/graph_repository.py` | 10, 11 | `application.graph.{GraphEdgeDraft,GraphNodeDraft}`、`application.retrieval.ChunkRecord` |
  | `backend/app/adapters/postgres/agent_repository.py` | 8 | `application.agent_runtime.AgentStep` |
  | `backend/app/adapters/graph/extractor.py` | 3 | `application.graph.GraphService` |
- **问题**：设计文档 `...design.md:64` 明确声明："Dependency direction is `frontend -> API -> application -> domain/ports`; infrastructure implements ports and is wired only in the composition root."。实际却是 `adapters → application`（外层依赖内层），即**依赖倒置被反向执行**。DTO（`ChunkRecord`/`GraphEdgeDraft`/`AgentStep`/`QueryGatewayResult`）被定义在 application 层，适配器必须导入它们才能返回数据。
- **影响**：
  - 无法独立替换/测试适配器（需拉起 application 层）；
  - `ChunkRecord` 定义在 `application/retrieval.py:14-23` 却在 `adapters` 与 `domain` 两侧被引用，其"正确归属"其实是 `domain/models.py`（它已有 `RankedHit`、`ChunkDraft`）。
- **建议**：把跨层 DTO（`ChunkRecord`、`GraphNodeDraft`、`GraphEdgeDraft`、`AgentStep`、`QueryGatewayResult`）下沉到 `domain/models.py`；适配器只依赖 `domain`；application 通过 ports 协议与适配器交互。
- **优先级**：**P1**

### D-02 应用层直接依赖具体适配器
- **位置**：
  - `backend/app/application/assets.py:7`：`from backend.app.adapters.storage import ContentAddressedStorage`
  - `backend/app/application/ingestion.py:9-10`：`from backend.app.adapters.parsers import ParserRegistry`、`from backend.app.adapters.storage import ContentAddressedStorage`
- **问题**：应用服务以**具体类**为构造参数（`IngestionService(repository, storage: ContentAddressedStorage, parsers: ParserRegistry)`），而非 `ports` 中的协议。
- **影响**：`IngestionService`/`IngestionWorker` 无法在不加载 `fitz`/`PIL`（PDF/图像库）的情况下被测试或复用。这也是 R-04 中"内存版摄取只在测试用"的根因之一。
- **建议**：为 storage 与 parser 定义 ports（`ports/storage.py`、`ports/parsers.py`），应用层依赖协议。
- **优先级**：**P1**

### D-03 检索器契约靠鸭子类型，类型标注与实际注入不符
- **位置**：
  - 声明：`backend/app/application/retrieval.py:70` `HybridRetriever.__init__(self, repository: InMemoryRetrievalRepository, ...)`
  - 实际注入：`backend/app/api/routes.py:278` `HybridRetriever(store, embedding_provider=ollama)`，`store` 为 `PostgresKnowledgeRepository`
- **问题**：类型标注说 `InMemoryRetrievalRepository`，运行时却是 Postgres 实现。二者仅靠"都有 `list_active_chunks(scope)` 与 `get(chunk_id)`"这一**隐式约定**兼容。任何一方签名变化都不会被静态检查捕获（`mypy` 未启用，`pyproject.toml` 无类型检查配置）。
- **建议**：引入显式 `RetrievalRepository` Protocol（R-01 重建），`HybridRetriever` 依赖协议；在 CI 中启用 `mypy --strict` 或至少 `pyright` 基础检查。
- **优先级**：**P2**

### D-04 运行期能力探测（`hasattr`）替代显式契约
- **位置**：
  - `backend/app/application/knowledge_tools.py:35`：`if lister is None and self.retriever is not None and hasattr(self.retriever.repository, "list_documents")`
  - `backend/app/application/graph.py:64, 68`：`if hasattr(self.repository, "set_graph_status")`
- **问题**：用 `hasattr` 探测可选能力，使"某适配器是否支持 list_documents / set_graph_status"变成运行期不可见的分支。`GraphService` 对不支持的仓库会静默跳过状态写入。
- **建议**：把可选能力提为显式协议（`DocumentListingRepository`、`GraphStatusWriter`），由类型系统保证；或使用 `NotImplementedError` 明确失败而非静默跳过。
- **优先级**：**P2**

### D-05 `query_graph` 的 `JOIN ... ON a OR b` 可能产生重复/放大行
- **位置**：`backend/app/adapters/postgres/graph_repository.py:57-62`
```sql
FROM graph_edges ge JOIN graph_nodes gn ON gn.id=ge.source_node_id OR gn.id=ge.target_node_id
JOIN graph_edge_evidence gee ON gee.edge_id=ge.id
```
- **问题**：`ON ... OR ...` 使一条边在源/目标节点都匹配时产生 2 行；配合 `LIKE` 模糊匹配，同一条边可能被返回多次。上层 `GraphService.query`（`graph.py:72-75`）与 `KnowledgeToolGateway` 均未去重。
- **影响**：Agent 的 `query_knowledge_graph` 工具可能返回重复证据，影响 token 消耗与回答质量。
- **建议**：改为 `JOIN graph_nodes gn ON gn.id IN (ge.source_node_id, ge.target_node_id)` 并用 `SELECT DISTINCT`；或先按节点查边再 UNION。
- **优先级**：**P2**

### D-06 `_store()` 的惰性初始化存在并发竞态
- **位置**：`backend/app/api/routes.py:77-90`
- **问题**：`if current is None: ... request.app.state.store = current` 无锁。FastAPI 默认多线程处理同步 handler，首个并发请求可能同时创建两个 `Engine` 与两个 `PostgresBudgetGate`，后者会丢失前者的预算状态引用。
- **建议**：在 `bootstrap.build_app()` 中一次性构造并注入（与 F-01 同一修复），从根上消除竞态。
- **优先级**：**P2**

---

## 八、架构分层验证

### A-01 分层结构总体清晰，但依赖方向未被执行
- **正面**：
  - `domain/` **纯净**：不导入 FastAPI / SQLAlchemy / 任何 provider 客户端（已核查全部 11 个文件）。`domain/` 内 `chunking`、`fusion`、`scope`、`text_normalization`、`evidence` 均为纯函数/纯数据，是**本项目最健康的一层**。
  - `application/` 的 `rag_orchestrator`、`retrieval`、`model_policy`、`quality`、`context_builder` 保持了"不直接触库"的边界。
  - `adapters/` 按技术维度分目录（postgres / models / ocr / graph / parsers），命名清晰。
- **问题**：见 D-01 / D-02 —— 声明的 `API → application → domain/ports` 与实际的 `adapters ↔ application` 双向依赖不符。
- **建议**：在 CI 中引入分层约束检查（如 `import-linter` 或自定义 AST 脚本），把"适配层不得导入 application"变为可执行门禁。**当前已有 `scripts/contract_test.py` 的 forbidden-term 检查机制，可扩展为分层检查。**
- **优先级**：**P1**

### A-02 组合根缺失（`bootstrap.py` 未被使用）
- **位置与建议**：见 F-01。这是分层验证的核心缺陷：没有组合根，装配逻辑必然泄漏到 HTTP 层。
- **优先级**：**P1**

### A-03 生产环境云/预算路径不可达，与设计声明不符
- **位置**：
  - `backend/app/api/routes.py:310`：`RagSettings(local_query_enabled=request.app.state.settings.local_query_enabled)` —— 未传 `cloud_enabled` / `prefer_cloud`，二者取默认 `False`
  - `backend/app/application/rag_orchestrator.py:20`：`prefer_cloud: bool = False`
  - 全仓库 `prefer_cloud=True` **只出现在测试**（`test_budget_orchestrator.py:37,47`、`test_egress_matrix.py:22`）
- **问题**：`routes.py:89` 装配了 `PostgresBudgetGate`，`rag_orchestrator.py:93-106` 有完整的预算预留/拒绝逻辑，但**生产代码永远不会进入该分支**。设计文档 `...design.md:178` 与 `AGENTS.md:28` 要求"云端调用要经过实际外发规则与月度预算门"，该门在 API 层实际是"死门"。
- **影响**：预算门的正确性只有单测保证，没有端到端路径；`/settings` 可修改 `monthly_cloud_budget_microunits` 但对回答链路无影响（见 Q-05）。
- **建议**：明确 V1 语义 —— 若 V1 不支持云端回答，则在 `routes.py` 显式构造 `RagSettings(cloud_enabled=settings.cloud_enabled, prefer_cloud=False)` 并**删除**不可达的预算分支与 `budget_gate` 装配（或加 `# V2 预留` 注释与 `pytest.mark.skip` 说明）；若 V1 支持，则需把 `prefer_cloud` 接入 `/settings` 或请求参数。**不允许"装配了但永不执行"的中间态。**
- **优先级**：**P2**

### A-04 `domain/models.py` 依赖 pydantic（轻微分层污染）
- **位置**：`backend/app/domain/models.py:5`：`from pydantic import BaseModel, ConfigDict, Field`
- **问题**：设计文档 `...design.md:64` 声明"Domain code does not import FastAPI, SQLAlchemy, Docker SDK, or provider clients"—— 未提及 pydantic，但 `BaseModel` 引入的校验/序列化语义（`model_copy`、`Field`）已使领域模型与 pydantic 绑定（`RankedHit.model_copy` 在 `retrieval.py:87` 被使用）。
- **评价**：这是**可接受的权衡**（pydantic v2 是轻量数据校验库，非 Web 框架），但应在文档中显式记录该例外，避免"声明与实践不符"。
- **建议**：在 ADR 中补记"领域模型使用 pydantic v2 作为数据契约"这一决策，或改用 `dataclass(frozen=True)`。
- **优先级**：**P3**

### A-05 前端目录结构与设计声明不符
- **位置**：设计 `...design.md:85-89` 声明 `src/api/`、`src/components/`、`src/pages/`、`src/state/`；实际为 `src/api/`、`src/components/`、`src/app/`（含 `App.tsx` + `state.ts`），**无 `pages/` 与 `state/` 目录**。
- **建议**：更新文档，或把 `state.ts` 移入 `src/state/` 以对齐；`App.tsx` 中的三栏布局若继续增长，建议按 `pages/` 拆分。
- **优先级**：**P3**

---

## 九、性能专项（检索路径与 I/O）

> 本节按性能视角补充，均已在代码中定位。

### P-01 检索全量拉取 chunk 与 1024 维向量，无 LIMIT
- **位置**：`backend/app/adapters/postgres/knowledge_repository.py:190-208`（`list_active_chunks`）
- **问题**：
  ```sql
  LEFT JOIN LATERAL (SELECT embedding FROM chunk_embeddings WHERE chunk_id=c.id ORDER BY created_at DESC LIMIT 1) ce ON TRUE
  ... ORDER BY c.id
  ```
  无 `LIMIT`、无 `WHERE` 过滤候选集。**每个查询**都会把所选知识库内**全部** active chunk 的 `content` **与 1024 维 embedding**（约 4–8 KB/条）载入内存。
- **量级估算**：1 万 chunk × 4 KB 向量 ≈ 40 MB 网络传输 + 内存占用，**每次提问**；`rag_orchestrator` 的重试会**再来一次**（P-04）。
- **建议**：关键词分支下推 SQL（利用已有 `chunk_terms` 表做 `JOIN` 过滤），向量分支走 pgvector ANN 索引（见 P-02），两者各取 top_k 后融合。**这是本项目性能上最关键的改进点。**
- **优先级**：**P1**

### P-02 向量相似度在 Python 中逐条计算，pgvector 索引被完全绕过
- **位置**：`backend/app/application/retrieval.py:90-102`（`_cosine` 逐 chunk 计算）与 `:58-66`（`_cosine` 实现）
- **问题**：项目已引入 `pgvector==0.4.1`，`chunk_embeddings.embedding` 为 `vector` 类型（`knowledge_repository.py:175` 用 `CAST(:embedding AS vector)` 写入），并建立了 `embedding_profiles` 表 —— 但**检索时从不使用向量算子**：`list_active_chunks` 把 embedding 取回 Python，再由 `_cosine` 用 `math.sqrt`/`sum` 逐条计算。
- **影响**：
  - O(N) 全表余弦，无法利用 pgvector 的 HNSW/IVFFlat 索引；
  - 每 chunk 一次纯 Python 循环（1024 次乘加 × N 条），GIL 下不可并行；
  - `embedding` 经 `LEFT JOIN LATERAL` 取回后又只用于本地计算，网络与内存双重浪费。
- **建议**：改为 `SELECT ... ORDER BY embedding <=> CAST(:query AS vector) LIMIT :k`，在 `chunk_embeddings.embedding` 上建 HNSW 索引（`alembic/versions/0003_m1_indexes.py` 需核查是否已建向量索引）。
- **优先级**：**P1**

### P-03 `append_event` 用 `MAX(seq)+1` 生成序号，O(n) 且存在并发竞态
- **位置**：`backend/app/adapters/postgres/knowledge_repository.py:312-317`
```sql
SELECT COALESCE(MAX(seq),0)+1 FROM retrieval_events WHERE run_id=:run_id
```
- **问题**：每次写事件都要对已有事件做一次聚合扫描（O(事件数)），且**独立于 INSERT 的另一条语句**；并发写入同一 run 时可能取到相同 `seq`（除非表上有唯一约束，需核查 `0002_m1_rag.py`）。SSE 重连依赖 `Last-Event-ID` 与 `seq` 单调性（`routes.py:340-351`、`frontend/src/api/sse.ts:22-39`），序号重复将直接破坏前端去重逻辑。
- **建议**：改用 `BIGSERIAL` 或 `GENERATED ALWAYS AS IDENTITY` 列；或在 `rag_runs` 上维护 `next_seq` 计数器并用 `UPDATE ... RETURNING` 原子递增。
- **优先级**：**P1**

### P-04 质量门重试用完全相同的参数重复检索
- **位置**：`backend/app/application/rag_orchestrator.py:83-87`
```python
if not decision.accepted:
    retry_count = 1
    retrieval = self.retriever.retrieve(scope, question, query_plan=query_plan)   # 参数与上一次完全相同
    decision = self.quality_gate.evaluate(retrieval)
```
- **问题**：`retrieve()` 是确定性的（相同 scope/question/query_plan → 相同结果），且 `QualityGate` 只判空。因此**重试必然得到完全相同的空结果**，只会再消耗一次全量 chunk+embedding 拉取（P-01）与一次 `embed()` 调用。
- **影响**：纯浪费（最坏情况：一次失败提问 = 2 倍检索开销），且设计文档声称的"最多一轮**定向补检**"（`...design.md:166`、`findings.md` RAG 链路）实际未实现 —— 没有"定向"（未改变查询、未放宽阈值、未扩大 top_k）。
- **建议**：① 若 V1 不做定向补检，删除重试并直接返回 `NO_EVIDENCE`；② 若要做，重试必须改变输入（如放宽 `top_k`、使用 L1 扩展词重写 query_plan、或降低质量阈值），并断言两次检索的输入不同。
- **优先级**：**P2**

### P-05 关键词打分对 CJK bigram 逐词全量扫描
- **位置**：`backend/app/application/retrieval.py:79-85` + `backend/app/domain/text_normalization.py:26-35`
- **问题**：`normalize_query` 为每个 CJK 连续段生成所有 2-gram（长度 L 产生 L-1 个词），随后 `retrieval.py:81` 对每个 chunk 的全文执行 `lowered.count(term)`。复杂度 ≈ `O(词数 × chunk 长度 × chunk 数)`，且 `str.count` 会重复扫描同一文本。
- **影响**：中文长文档 + 长查询时开销显著；`retrieval.py:80` 的 `lowered = chunk.content.lower()` 每次重建小写副本。
- **建议**：① 检索改为 SQL 侧基于 `chunk_terms` 表打分（该表已在摄取时构建，见 `knowledge_repository.py:171-172`，**但检索时完全没用**）；② 若保留内存打分，先对 chunk 做一次 `tokenize` 得到词频 dict 再匹配，避免 N 次全文扫描。
- **优先级**：**P2**

### P-06 摄取时逐行 INSERT，事务内 N 次往返
- **位置**：`backend/app/adapters/postgres/knowledge_repository.py:164-175`
- **问题**：对每个 chunk 依次执行 3 条 INSERT（`chunks`、`chunk_terms` 每词一条、`chunk_embeddings`），全部在同一个 `engine.begin()` 事务内。一个 1000 chunk 的文档 ≈ 1000 + 词数(可能上万) + 1000 条语句，逐条网络往返。
- **建议**：改用 `executemany` / `INSERT ... VALUES (...),(...)` 批量插入；`chunk_terms` 用 `executemany` 或 `COPY`；embeddings 同理。
- **优先级**：**P2**

### P-07 前端 SSE 使用 `response.text()` 整体缓冲，非流式
- **位置**：`frontend/src/api/sse.ts:32`：`const events = parseStreamEvents(await response.text(), lastSeq)`
- **问题**：`EventSource` 或 `ReadableStream` 未使用，`await response.text()` 会等待响应体**完全结束**才解析。虽然本项目的 SSE 是"回放已落库事件后立即结束"（`routes.py:347-351` 的 `stream()` 遍历完即返回），因此当前影响有限，但：
  - 一旦后端改为"边生成边推送"，前端将退化为"全部生成完才显示"，**失去 SSE 的流式价值**；
  - 且 `routes.py:344-345` 在无事件时直接返回 404，前端 `sse.ts:31` 静默 `return`，用户无任何反馈。
- **建议**：改用 `fetch` + `response.body.getReader()` 增量解析（按 `\n\n` 分帧）；404 时给出可见提示。
- **优先级**：**P3**

### P-08 每个请求重建检索/编排对象
- **位置**：`backend/app/api/routes.py:275-286`
- **问题**：每次 `_answer_message` 都新建 `CitationService`、`InMemoryCitationStore`、`HybridRetriever`、`RAGOrchestrator`（后者内部又 new `ModelPolicy`/`QualityGate`/`ContextBuilder`）。
- **评价**：这些对象均为轻量、无状态或请求级状态（`CitationService.snapshots` 必须请求级），**当前设计是合理的**。仅记录：若未来为 `RAGOrchestrator` 引入缓存或连接池，需改为单例并显式区分请求态。
- **优先级**：**P3**

### P-09 同步阻塞的数据库调用未隔离到线程池
- **位置**：`backend/app/api/routes.py`（所有 handler 均为 `def` 而非 `async def`）+ `run_in_threadpool` 已导入但未使用（R-05）
- **评价**：FastAPI 对 `def` handler 会自动调度到线程池，因此**当前实现是安全的**；`run_in_threadpool` 的未使用导入说明曾计划在 `async def` 中显式隔离，但最终采用了 `def` 方案。
- **建议**：删除未使用导入；在代码注释中明确"同步 SQLAlchemy 依赖 FastAPI 的 `def` handler 线程池调度"这一设计决策，避免后续有人把 handler 改成 `async def` 而阻塞事件循环。
- **优先级**：**P3**

---

## 十、优先级汇总与处置建议

### P1 —— 高优先级（建议在下一次交付前处理，共 14 项）

| ID | 问题 | 位置 | 关键动作 |
|---|---|---|---|
| Q-09 | 发布门禁依赖仓库外个人脚本与本机绝对路径 | `scripts/verify-release.ps1:20-21` | 内化 `with_server` 能力，路径改相对 |
| Q-01 | 前端组件压缩为单行（最长 1,571 字符） | `frontend/src/{app,components}/*` | 拆行 + Prettier 门禁 |
| Q-02 | `routes.py` 混合 HTTP/组合根/编排三层 | `backend/app/api/routes.py` | 抽 `AnswerService` + 迁组合根 |
| C-01 | `PostgresKnowledgeRepository` 上帝对象（8 类职责） | `.../knowledge_repository.py:27-355` | 按聚合拆 7 个仓储 |
| C-02 | HTTP 层与组合根混居 | 同 Q-02 | 同 Q-02 |
| D-01 | 适配层反向依赖应用层 6 处 | `backend/app/adapters/**` | DTO 下沉 domain，适配器只依赖 domain |
| D-02 | 应用层依赖具体适配器 | `application/{assets,ingestion}.py` | 建 storage/parser ports |
| F-01 | 组合根 `bootstrap.py` 从未被调用 | `backend/app/bootstrap.py:9` | 让 `build_app()` 成为唯一入口 |
| A-01 | 依赖方向与设计声明不符 | 全局 | 引入 import-linter 门禁 |
| A-02 | 组合根缺失 | 同 F-01 | 同 F-01 |
| R-01 | `ports/repositories.py` 死模块且签名过期 | `backend/app/ports/repositories.py` | 删除或按真实调用面重写 |
| P-01 | 检索全量拉取 chunk + 1024 维向量，无 LIMIT | `.../knowledge_repository.py:190-208` | 下推过滤 + LIMIT |
| P-02 | 向量检索绕过 pgvector 索引，Python 逐条算余弦 | `application/retrieval.py:90-102` | 改用 `<=>` 算子 + HNSW 索引 |
| P-03 | `append_event` 用 `MAX(seq)+1`，O(n) 且并发竞态 | `.../knowledge_repository.py:312-317` | 改 IDENTITY / 原子计数器 |

### P2 —— 中优先级（共 30 项）

| 类别 | ID |
|---|---|
| 冗余 | R-02、R-03、R-04 |
| 遗留文件 | L-01、L-02、L-03、L-04、L-07 |
| 代码质量 | Q-03、Q-04、Q-06、Q-07、Q-08、Q-10 |
| 文件必要性 | F-02~F-05、F-09 |
| 内聚 | C-03、C-04、C-05 |
| 耦合 | D-03、D-04、D-05、D-06 |
| 分层 | A-03 |
| 性能 | P-04、P-05、P-06 |

### P3 —— 低优先级（共 17 项）

| 类别 | ID |
|---|---|
| 冗余 | R-05、R-06 |
| 遗留文件 | L-05、L-06、L-08 |
| 代码质量 | Q-05、Q-11 |
| 文件必要性 | F-06、F-07、F-08、F-10、F-11 |
| 分层 | A-04、A-05 |
| 性能 | P-07、P-08、P-09 |

### 建议的处置顺序（4 个批次）

1. **批次一 · 零风险清理（P2/P3 中的删除类）**：L-01~L-05、L-08、R-05、R-06、F-06、F-07、F-10、F-11、Q-05、Q-11。**不改变任何运行行为**，可一次性完成并跑全量测试确认无回归。
2. **批次二 · 性能与正确性（P1 中的 P-01/P-02/P-03 + P-04）**：这是**唯一有明确量化收益**的一组，且集中在检索与事件两处，回归面可控（`test_hybrid_retrieval.py`、`test_agent_trace_sse.py` 已存在）。建议先补向量索引迁移与检索评测基线（`evaluations/core.jsonl`、`scripts/evaluate_retrieval.py` 已具备）再改。
3. **批次三 · 架构纠偏（F-01/A-02 → D-01/D-02 → R-01/D-03）**：先把组合根立起来（`bootstrap.build_app`），再下沉 DTO 到 domain，最后重建 ports。**必须按此顺序**，否则中间态会出现"ports 已建但组合根仍散落在 routes"的双重混乱。
4. **批次四 · 内聚性重构（C-01/C-02/C-03）**：拆分 `PostgresKnowledgeRepository`。这是**风险最高**的一批（涉及 355 行核心数据访问），建议配合已有的 `backend/tests/` 29 个测试文件 + `scripts/contract_test.py` 作为安全网，逐聚合迁移、每个聚合一个 PR。

---

## 十一、可执行的门禁建议（防止问题回归）

| 目标 | 手段 | 现状 |
|---|---|---|
| 分层依赖方向 | `import-linter` 或扩展 `scripts/contract_test.py` 的 forbidden-term 机制，禁止 `adapters.*` 导入 `application.*` | 无 |
| 未使用代码 | `ruff`（`F401` 未使用导入、`F841` 未使用变量）+ `vulture` | 未安装（已确认 `.venv` 无 ruff/pyflakes） |
| 行长度 | `ruff format` / `black`（`line-length=120`）+ 前端 Prettier | 无 |
| 类型契约 | `mypy` 或 `pyright`（至少覆盖 `application/` 与 `ports/`） | 无（`pyproject.toml` 无类型检查配置） |
| 前端可读性 | Prettier `printWidth=100` 纳入 `npm run build` 前置 | 无 |
| 死配置 | 测试中断言 `Settings` 的每个字段都被至少一个调用点消费 | 无 |
| 检索性能 | 将 `evaluations/core.jsonl` 的 Hit@5/Recall@5/MRR 与 **p95 检索延迟**一起纳入 `verify-m3` | 仅有效果指标，无延迟指标 |

---

## 十二、审查范围与局限声明

- 本报告为**静态审查**：未运行 `pytest`、未启动服务、未连接 PostgreSQL/Ollama、未执行 `verify-*.ps1`。所有"未使用/无引用"结论均由 AST 全仓库扫描 + 跨文件文本检索得出，可按"位置信息"独立复核。
- `var/pytest-tmp*`、`.pytest_cache/` 因**文件系统权限拒绝**未能遍历，其内部内容未纳入评估（已在 L-03、L-05 标注）。
- 未评估：`frontend/node_modules/`（第三方）、`.venv/`（第三方，186 MB）、`alembic/versions/*.py` 的迁移正确性（仅核对表集合常量）、`contracts/openapi.json` 与实现的逐字段一致性（`scripts/contract_test.py` 已承担该职责）。
- 建议在实施批次一（零风险清理）后，重新运行 `scripts/verify-m0.ps1` … `scripts/verify-release.ps1` 以确认清理未引入回归。

---

## 十三、修复执行记录（2026-09-26）

> 本节记录对上述问题的**实际处置**。只写真实执行过的命令与结果；未执行的一律标注 `NOT RUN` / `BLOCKED`。

### 13.1 已完成的代码修复

| 编号 | 问题 | 修改位置 | 状态 |
|---|---|---|---|
| F-01 / A-02 | 组合根未被调用 | `backend/app/bootstrap.py` 重写为 `Container` + `build_container`，`main.py` 唯一装配点 | 已修复 |
| F-02~F-05 | 4 个零引用适配器/服务 | 删除 `ports/repositories.py`、`adapters/postgres/retrieval_repository.py`、`adapters/models/cloud.py`、`adapters/ocr/local.py`、`adapters/graph/extractor.py`、`application/assets.py` | 已删除 |
| F-06 / F-07 / F-10 / F-11 | 空子类 / 未用常量 / 死枚举 / 无生产者分支 | `M1_TABLES` 纳入 `test_schema_check.py` 断言；`QualityReason` 仅保留 `NO_CANDIDATES`；`ImageParser` 补出 `SourceLocator(kind="image")` | 已修复 |
| R-04 | `_str`/`from_url`/`read_chunk` 死方法；`persist_retrieval_hits` 零调用 | 删除死方法；`persist_retrieval_hits` 由 `AnswerService` 经 `on_retrieval` 真实调用 | 已修复 |
| R-05 | 10 处未使用导入 | 全部清理；AST 复扫结果 `REAL unused imports: 0` | 已修复 |
| R-06 | Markdown 标题正则双份实现 | `GraphService._label` 只读 `chunk.heading_path`，移除重复正则与 `re` 导入 | 已修复 |
| D-01 | 适配层反向依赖应用层 | `graph_repository.py` 改从 `domain.graph` 取 DTO；`ChunkRecord`/`GraphNodeDraft` 等下沉到 `domain` | 已修复 |
| D-02 | 应用层依赖具体适配器 | 新增 `ports/ingestion.py`（`BlobStore`/`DocumentParser` 协议），`StoredObject` 下沉 `domain`；`ingestion.py` 只依赖协议 | 已修复 |
| D-05 | `JOIN ... OR ...` 行放大 | `query_graph` 改为 `EXISTS` 子查询 | 已修复 |
| D-06 | `_store()` 惰性初始化竞态 | 组合根一次性构建 | 已修复 |
| P-01/P-02 | 检索全量拉取、向量在 Python 计算 | 新增 SQL 侧 `keyword_candidates`/`vector_candidates`（`<=>` 余弦距离 + LIMIT）；迁移 `0009` 建 HNSW 索引 | 代码完成，**DB 验证 NOT RUN** |
| P-03 | `append_event` 用 `MAX(seq)+1` | 迁移 `0009` 增加 `rag_runs.next_event_seq`，改为 `UPDATE ... RETURNING` | 代码完成，**DB 验证 NOT RUN** |
| P-04 | 质量门用相同参数重试 | 删除无效重试 | 已修复 |
| P-07 | 前端 SSE 用 `response.text()` 整包缓冲 | `frontend/src/api/sse.ts` 改为 `getReader()` 增量解析 | 已修复（见 13.3） |
| Q-05 | `patch_settings` 错误信息不符 | 改为实际允许项 | 已修复 |
| Q-06 | 默认 DB 端口 5432 与 Compose 55432 不一致 | `config.py` 默认改 55432 | 已修复 |
| Q-07 | 前端依赖全为 `latest` | `package.json` + lockfile 根依赖锁定为实际版本 | 已修复 |
| Q-08 | `playwright.config.ts` 硬编码 Chrome 路径 | 改为 `PLAYWRIGHT_CHROME_PATH` 可选覆盖，默认用内置 Chromium | 已修复 |
| Q-09 | 发布门禁脚本硬编码本机绝对路径 | `verify-release.ps1` 改用 `$env:USERPROFILE` / `$env:SystemRoot` / 环境变量覆盖 | 已修复（语法校验通过，运行 **NOT RUN**） |
| Q-10 | 空格分词估算 token，中文不可达 | 新增 `domain.agent_policy.estimate_tokens`，中文按字计 | 已修复 |
| Q-11 | `healthz` 重复生成 `request_id` | 复用 `request.state.request_id` | 已修复 |
| F-09 | `max_chunk_chars`/`chunk_overlap` 死配置 | 纳入 `Settings` 校验并传入 `chunk_document` | 已修复 |
| 门禁 | 无分层依赖检查 | `scripts/contract_test.py` 新增 `layering_violations` | 已新增 |

### 13.2 已完成的文件清理

- 已删除：`archive/`（74 个快照）、`output.json`、`pytest_html_report.html`（2.2 MB）、`personal_rag.egg-info/`、`frontend/dist/`、`frontend/test-results/`、`.docx-reference-render/`、`var/cleanup-storage/`、`var/ui-smoke.md`，以及 `var/` 下 40 个 `restore-*` / `smoke-*-storage-*` / `pytest-tmp-m0` / `pytest-tmp-m2` 临时目录。
- 有意保留：`var/reports/`（权威验证证据，被 `progress.md`、`.planning/`、ADR 引用）、`var/backups/`（备份/恢复证据）、`var/storage/`（`config.py` 的默认 `storage_root`，**不属于**冒烟残留，L-03 的条目列表亦未包含它）。
- 删除均通过系统回收站完成，未使用不可逆删除。
- **更正 L-01 / L-02 的定性**：`output.json`、`pytest_html_report.html` 与 `archive/output_*.json` **并非历史遗留文件，而是测试运行集成在每次 `pytest` 执行时自动生成的产物**（`archive/` 会自动归档上一份 `output.json`）。实测：每次执行 `pytest` 后三者在根目录重新出现，而 `pyproject.toml` 的 `[tool.pytest.ini_options]` 中并无 `pytest-json-report` / `pytest-html` 配置。因此删除只是**一次性美化**，真正的持久处置是保持 `.gitignore` 忽略（现已覆盖 `archive/`、`output.json`、`pytest_html_report.html`）。若要根治 `archive/` 的无界累积，需在测试集成侧限制保留份数或重定向输出到 `var/reports/`。
- `frontend/dist/` 与 `var/` 下的临时目录会在下次 `npm run build` / 冒烟脚本运行时重新生成；彻底解决需要修改 `scripts/smoke_*.py`（L-03 ①）。

### 13.3 验证命令与结果（真实执行）

| 命令 | 退出码 | 结果 |
|---|---|---|
| `.\\.venv\\Scripts\\python.exe -m pytest backend/tests -q` | 0 | `66 passed`（基线 41） |
| `.\\.venv\\Scripts\\python.exe scripts\\contract_test.py` | 0 | `PASS`；`layering_violations: []` |
| `npm --prefix frontend run build` | 0 | `tsc --noEmit` + `vite build` 通过 |
| `npm --prefix frontend ci --dry-run` | 0 | lockfile 与 `package.json` 同步 |
| 独立 Node 校验 `sse.ts` 流式解析 | 0 | 7/7 通过（增量下发、断线续传去重、坏块跳过、CRLF） |
| AST 未使用导入复扫 | — | `REAL unused imports: 0` |
| 分层依赖 AST 扫描 | — | `violations: 0` |
| `verify-release.ps1` PowerShell 语法解析 | 0 | `SYNTAX OK` |

> 独立 Node 校验在修复过程中发现一处**既有缺陷**：CRLF 行尾时事件名会带 `\r`（`"answer.completed\r"`），导致终止事件判断失效。已在 `parseEventBlock` 中改为按 `/\r?\n/` 切分并复验通过。

### 13.4 未执行 / 被阻断

| 项目 | 状态 | 原因 |
|---|---|---|
| 迁移 `0009_retrieval_perf.py` 与新增 pgvector SQL | `NOT RUN` | 无可用 PostgreSQL/Ollama 实例 |
| `scripts/verify-m0.ps1` … `verify-release.ps1` 端到端 | `NOT RUN` | 同上 |
| `verify-release.ps1` 运行期行为 | `NOT RUN` | 仅完成语法解析校验 |
| 前端 Playwright SSE 用例 | `NOT RUN` | 需同时启动 API + Vite |
| 清理 `.pytest_cache/`、`var/pytest-tmp/`、`var/pytest-tmp-regression/` | `BLOCKED` | 目录所有者 SID 无法解析，当前用户 `拒绝访问`，需提权 `takeown`/`icacls /reset` |
| 清理 `var/logs/` | `BLOCKED` | 正在运行的 `ollama.exe` 持续写入其日志，文件被占用 |

### 13.5 未开始

- **批次四（C-01/C-02/C-03）**：拆分 `PostgresKnowledgeRepository`（439 行、多聚合）、`routes.py` 三层职责、`PostgresGraphRepository` 由继承改组合。**风险最高**，建议逐聚合迁移、每聚合一次提交，并以现有 66 个测试 + `contract_test.py` 为安全网。
- **Q-01 / Q-03**：`ChatPanel.tsx`（最长单行 1571 字符）、`GraphPanel.tsx`（1242）、`App.tsx`（1154）与后端超长 SQL 行的格式化。属纯格式改动，建议引入 Prettier（`printWidth=100`）与 `ruff format`（`line-length=120`）一次性处理，避免人工改动引入行为差异。
- **A-03**：`RagSettings.prefer_cloud` 仍未被生产代码设置，预算门端到端不可达；需决定「接线」或「移除」。
- **L-06 / L-07**：`.planning/` 与 `docs/superpowers/` 的文档漂移、设计文档目录结构与实际不符。
- **L-03 ①**：冒烟脚本仍使用 `var/smoke-*-storage-*` 固定前缀而非 `tempfile.TemporaryDirectory()`，残留仍会累积。
