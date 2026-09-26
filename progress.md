# progress.md — 当前开发进度

- 当前里程碑：M4.5 / V1.0 实现与发布证据已完成，等待项目负责人确认验收
- 当前工作分支 / commit：`main` / `081b3cf12542fd4c94b0220e46b3d8c69ca8f5f6`；远程为 `https://github.com/tiance002/personal-multimodal-rag.git`；无 tag
- 当前唯一主要目标：交付个人、本地、多知识库、多模态 RAG 知识库问答 Agent V1.0。
- 本次任务：从空项目完成 M0→M1→M2→M3→M4→M4.5 纵切，并保存可核查报告。
- 已完成并有证据的工作：
  - `& .\scripts\verify-m0.ps1` → exit 0；41 tests、PostgreSQL/pgvector migration、Ollama Chat/Embedding probe、evaluation schema 均 PASS；`var/reports/verify-m0.json`。
  - `& .\scripts\verify-m1.ps1` → exit 0；真实本地模型上传/索引/混合检索/回答/引用回读与 API/SSE PASS；`var/reports/verify-m1.json`。
  - `& .\scripts\verify-m2.ps1` → exit 0；PDF PASS，图片明确 `OCR_UNAVAILABLE` 且源 bytes 保留；`var/reports/verify-m2.json`。
  - `& .\scripts\verify-m3.ps1` → exit 0；图谱同版本证据、失败隔离、固定评测 Hit@5/Recall@5/MRR=1.0 PASS；`var/reports/verify-m3.json`。
  - `& .\scripts\verify-m4.ps1` → exit 0；闭集只读工具、scope/limits/cancel、真实 API smart trace/citation smoke PASS；`var/reports/verify-m4.json`。
  - `& .\.venv\Scripts\python.exe scripts\smoke_budget.py --database-url ...` → exit 0；PostgreSQL advisory-lock 月度预算并发、settle 和 follow-up reservation PASS；`var/reports/smoke-budget.json`。
  - `& .\scripts\contract_test.ps1` → exit 0；OpenAPI snapshot、SSE event schema/sample、V2 forbidden-term 检查 PASS；`contracts/openapi.json`、`contracts/sse/`、`var/reports/contract-test.json`。
  - `& .\.venv\Scripts\python.exe scripts\evaluate_retrieval.py --report var\reports\eval-retrieval.json` → exit 0；20 条 core cases，Hit@5/Recall@5/MRR 均为 1.0；`var/reports/eval-retrieval.json`。
  - `npm --prefix frontend test` → exit 0；4 个 Playwright 用例覆盖三栏、SSE seq 去重/Last-Event-ID 重连、引用面板与冻结证据回读；发布门禁中在真实 API/Vite 服务下复跑通过。
  - `with_server.py ... scripts\playwright_smoke.py` → exit 0；三栏、上传、选中文档、图谱绿色选中和“建立图谱”入口 PASS；`var/reports/frontend-smoke.json`、`var/reports/frontend-smoke.png`。
  - `& .\scripts\verify-release.ps1 -Fresh` → exit 0；全量 release gate、M0–M4、预算、契约、前端、backup/isolated restore PASS；`var/reports/verify-release.json`。
  - `& .\scripts\release_report.ps1` → exit 0；V1.0 汇总 PASS；`var/reports/v1-release-report.json`、`var/reports/v1-release-report.md`。
  - `docker compose -f deploy\compose.yml build api worker frontend` → exit 0；后端包含 0007/0008 迁移、前端包含最新 SSE 客户端的 Compose 镜像构建 PASS。
  - `docker compose -f deploy\compose.yml up -d api worker frontend` → exit 0；重建镜像后 API `/healthz`=200、前端首页=200，随后按约定停止应用容器。
  - `git init -b main`、`git commit -m "chore: establish personal RAG project baseline"`、`git push -u origin main` → exit 0；GitHub 私有仓库已创建并接收 `e52893c`。
- 当前阻断（P0/P1）：无已识别 P0/P1。
- 可控问题待负责人决定（P2/P3）：当前本机没有已验证 OCR/VLM adapter，因此图片/扫描资料显式失败而不生成伪文本；LibreOffice 缺失导致附件 DOCX 视觉渲染 BLOCKED（不影响软件运行）；`make` 不在 PATH，等价 PowerShell 门禁已执行。
- 已批准延期及批准依据：无；以上能力限制未被擅自标记为延期通过。
- 当前实际可执行 make targets：NOT IMPLEMENTED；未发现 Makefile，`make` 不在 PATH，未报告任何 `make` 命令通过。
- 本次最小下一步：项目负责人复核 `var/reports/v1-release-report.md` 与 GitHub 基线，确认验收后再决定是否建立 release tag。
- 最近一次里程碑验收：PASS（`& .\scripts\verify-release.ps1 -Fresh`，exit 0；`& .\scripts\release_report.ps1`，exit 0）。
- 对应 ADR 与设计章节：`docs/adr/ADR-001-model-capabilities.md`；`docs/superpowers/specs/2026-09-26-personal-rag-v1-design.md`；`docs/superpowers/plans/2026-09-26-personal-rag-v1-implementation.md`。

## 最近工作记录

| 日期 | 任务 | 实际命令/证据 | 结果 | 后续 |
|---|---|---|---|---|
| 2026-09-26 | 完成 V1.0 M0–M4.5 与完成审计闭环 | `verify-release.ps1 -Fresh`、`release_report.ps1`；41 tests、预算并发、20 条评测、OpenAPI/SSE、4 个 Playwright、backup/restore | PASS | 负责人验收/版本管理 |
| 2026-09-26 | Compose API/worker/frontend 构建与运行 | `docker compose build api worker frontend`；healthz 200、frontend 4173 200 | PASS | 容器已按约定停止，数据库卷保留 |
| 2026-09-26 | 最终前端镜像运行时复核 | `docker compose build frontend`、`up -d api worker frontend`；healthz 200、frontend 200 | PASS | 应用容器已停止，数据库保持健康 |
| 2026-09-26 | 后端迁移镜像重建与 Compose 运行时复核 | `docker compose build api worker frontend`、`up -d api worker frontend`；API healthz 200、前端 200；之后 stop 应用容器 | PASS | 数据库保持健康 |
| 2026-09-26 | 真实恢复演练 | `restore.ps1 -RestoreDatabase`；active/version、chunk、asset、graph orphan 检查均为 0 | PASS | 负责人复核 manifest |
| 2026-09-26 | GitHub 初始化与上传 | `git init -b main`、首个 commit `e52893c`、`git push -u origin main`；`git ls-remote --heads origin main` 返回同一 SHA | PASS | 负责人复核仓库与启动说明 |
