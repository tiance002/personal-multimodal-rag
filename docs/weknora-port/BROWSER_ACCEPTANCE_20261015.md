# 浏览器主链验收记录 — 2026-10-15

## 结论与边界

**真实 Edge 浏览器应用链路＋模拟模型：限定场景 PASS。日常 CLI 完整启动验收：BLOCKED / NOT RUN。**

用户已打开的 Edge（扩展连接，browser ID 3）实际执行了文本和 PNG 上传、后台处理、问答、引用、追问、刷新、停止 SSE 读取后续读，以及取消。没有以 API mock 页面测试代替这些操作。所有模型传输均为断言 `fixture.invalid` 的 `httpx.MockTransport`；真实供应商请求为 **0**。这不能证明 OCR、Caption、Embedding、Rerank 或回答模型质量。

本轮使用既有 `scripts/weknora_port_browser_fixture.py` 的隔离装配方式，派生 runner 放在 E 盘。实际 FastAPI、应用服务、数据库、Redis、图片后台处理和 React 都真实运行；fixture 注入 synthetic 权限及费用 authority、模型传输替身。业务 HTTP API 没有被浏览器拦截模拟。图片配置因页面尚无配置入口，经真实 PATCH API 设置。

必须保留的缺口：没有用 `python -B -m backend.weknora_port.entrypoint` 与独立 `worker` CLI 完成同样流程。默认 `development.assemble` 未配置权限/费用 authority，`/readyz` 会拒绝为 `DENY_UNCONFIGURED`。本轮没有关闭该保护；fixture 的 HTTP app 无 `/readyz`（404）。因此不能把 fixture 成功写成正式 CLI readiness 已通过，也不能宣称开机只启动 Docker 就能使用整个应用。

用户最后确认：**前端、app/API、解析部分暂不容器化，后续仍需修改内容**。Docker 分组目前只管理 PostgreSQL / Redis。

## 源码与改动

- 开始 HEAD：`341dec08c7e550a0433bda9f18f9b2331bdad377`，分支 `codex/weknora-python-port-integrate-image-20261014`。
- 第一轮实际失败来自该基线；修复后主要应用流程基于 `6c3822bbbf6ee6511549ccd680e66444f65bc511`。
- 最终取消提示复验基于 `957048b81dffed1443d1979cb87327a5c7393662`；后续仅文档提交不改变被验代码。
- 固定上游对照仍为 `3e8b0bfc80b845b2d4b2ed683994748741450a97`，本轮没有改回答路由阈值或 Cheap→Expensive 规则。

本地逻辑提交：

| 完整 SHA | 内容 |
|---|---|
| `6c3822bbbf6ee6511549ccd680e66444f65bc511` | 后台资料状态轮询、范围切换取消旧读取、引用版本及图片信息展示、原文版本校验与定向反例 |
| `7d5369655ca59be64e954919ef0337f21ffcc236` | EviRoute RAG 依赖 Compose、启动脚本、迁移说明 |
| `957048b81dffed1443d1979cb87327a5c7393662` | 新问题/续读和完成/失败/取消时更新顶栏提示，避免终态后残留等待 |

应用修改文件：`backend/weknora_port/documents.py`、`tests/weknora_port/test_documents.py`、`frontend/src/weknora_port/WeKnoraPythonApp.tsx`、`frontend/src/weknora_port/types.ts`、`frontend/tests/weknora-port/adapter.spec.ts`。共享新增文件为根目录 `docker-compose.eviroute-rag.yml` 和 `scripts/eviroute-rag-dependencies.ps1`；无迁移、依赖锁或入口修改。报告与容器说明在 `docs/weknora-port/`。未 Push、合并、部署或打 Tag。

复用 `DocumentService.upload_document`、图片任务 worker、`read_source_chunk`、正常 session/messages/continue-stream/stop HTTP 路径和 generation coordinator。引用显示改动没有另建资产、切块或索引旁路；图片 OCR 与 Caption 分别调用已有视觉传输接口。本轮没有重做 PDF/Office 解析。

## 实际浏览器场景

| 场景 | 结果 | 实测证据及限制 |
|---|---|---|
| Edge 正常 React 页面连接本次 Python API | PASS | `/api/v1/runtime` 身份、56425/56426 端口与进程路径；`runtime-identity.json` |
| 合成 handbook.txt 上传与处理 | PASS | 页面文件选择上传，实际服务处理并入库，页面 completed；`documents-v4.json` |
| 合成 widget-scan.png 上传、OCR/Caption 后台任务 | PASS | 实际 pending→completed，OCR/Caption 各一次视觉调用及正常 chunk/index 落地；`upload-state-v4.jpg`、`ledger-final.json` |
| 页面提问、SSE 增量和正常结束 | PASS | R6 首段 `Blue widgets` 显示时仍有“正在接收”；之后完成。`sse-incremental.jpg`、`sse-incremental-dom.txt`。R3 Redis 亦保留三个非空 answer 片段及一个 complete |
| 点击引用并核对原文/版本/位置/图片 | PASS（PNG 范围） | `citation-r1.jpg`、`final-citation.jpg`、`source-v4.json`；原文件链接实际 GET 200，返回附件，另用真实 HTTP 保存 E 盘并核对 PNG SHA；未声称浏览器内联展示原图 |
| 新 request_id 追问并携带历史 | PASS | R1 / R2 run_id 不同；Provider trace 中 R2 角色 system,user,assistant,user，has_first_question / has_followup 均 true；`metrics-before-docker.json` |
| 刷新后历史回读 | PASS | reload 后点击原会话，两轮问题/答案仍在；后来回读包含取消终态；`history-after-refresh.jpg`、`messages-final.json` |
| 浏览器断开 SSE 再恢复同一回答 | PASS（应用合同范围） | 页面“停止读取”后“继续读取原回答”；同一 assistant ID，续读为 GET，原 POST 只有一次。`sse-interrupted.jpg`、`sse-resumed.jpg`、访问日志、`resume-invariants.json` |
| 物理断网/系统级 offline 切换 | NOT RUN | 本轮断线采用页面 AbortController 停止读取；不扩大为 OS 网络故障恢复证明 |
| 页面取消生成与终态 | PASS | R4、R7 点击停止生成；port_turns 保存 MODEL_CALL_CANCELLED，页面恢复输入；R7 顶栏正确显示取消。`cancelled-notice-fixed.jpg` |
| 独立模型异常场景 | NOT RUN | 本轮选择用户要求中的“错误或取消”的取消分支；不把旧 mock 失败当成页面异常验收 |
| 跨库资料和原文隔离 | PASS（所列场景） | 页面创建第二个空 KB，无第一库资料；错误 KB 原文路径真实 HTTP 返回 404，`empty-kb-isolation.jpg`、`cross-kb-source-negative.json` |
| 失效版本 | PASS（单元反例）；浏览器 NOT RUN | 新定向测试拒绝 chunk/document source_version 不一致；没有篡改现有浏览器资料版本制造页面场景 |
| 扫描 PDF/页码定位 | NOT RUN | 本轮使用允许的图片分支。PNG 无页码，位置为实际返回 chunk0 / 字符0–0，不声称精确 OCR 框定位 |
| 标准 CLI / 独立 worker / readyz | BLOCKED / NOT RUN | 上述 authority 配置缺口；不弱化授权、引用或预算保护 |

## 可复核身份与调用数

- tenant：222；subject：`fixture-owner:40bdbfd2-fb5e-40dc-af02-6bc48ee035c4`。
- KB：`3022db2e-51d6-48db-be04-b39268eb336c`；image document：`b7ef9dbc-6373-4d0b-9068-7ec0bed1543f`。
- 图片版本：`038a5c4c-74be-4376-941c-2f18e44ec322`；引用 chunk：`10cbf0f0-da56-4a39-8f03-401ceba52523`。
- 会话：`a7fb8e5d-e57e-475f-9f9b-157db825b692`。
- R1 run：`811e4d51-4698-4acc-a8bd-0ba1e689032c`；R2：`3b128553-b65a-4b0c-af86-f999dba8d6f1`。
- 续读 R3 run：`a5a21ad3-0d62-4be4-84b3-95406db98376`，assistant：`4abdb8be-05a9-43d7-8c65-3d3375ff3bbb`。只有一条 turn、七条 send 记录，其中 answer 为 Cheap 和 Expensive 各一条、一个 complete。续读没有新增生成 attempt 或 send 账目，没有重复答案提交。并不声称供应商恰好执行一次。
- R3 answer 片段：`Blue widgets`、` use W-17. `、合法引用标记；时间间隔约1秒。费用 UNKNOWN 保留，未因模拟响应或取消强行清零；取消中已发送但未确定结果的调用仍 UNKNOWN。
- 最终七轮结果、每次模型配置和账目分别在 `turns-final.json`、`metrics-final.json`、`ledger-final.json`；R1/R2 的传输历史在旧进程的 `metrics-before-docker.json`。这些模型名只是 fixture 配置，不是生产模型认证。

PostgreSQL：`127.0.0.1:56423`，数据库 `weknora_python_port_20261013`，物理 system identifier **7696088403504697382**。新容器 ID `89c5cb748e2ec7aca3b1e3b181dfe936a4054137ddb4de5c4e8689e86387e720`。

Redis：`127.0.0.1:56424`，DB15；本轮唯一前缀 `weknora-python-browser-v4-20261015:811e8a21-04b5-4520-819e-6585a17c79cc`。迁移后 run_id `de03f13f2ffba1146b01434f25514c4478e046fd`，新容器 ID `15fe772fab7d9ac5b6023ff2075ebb5e12ccee80e274f91b16fe612e8748e897`。未 flush、未消费其他前缀队列。

资产目录：`E:\WK\run\browser-accept-20261015\assets-v4-f3a560bb-a324-40a6-906b-4fc52b0c711e`。API 最终实际 Python PID40396（venv launcher34788），UI Vite PID32460。命令行及可执行文件路径保存在 `process-identities-final.json`。

## 实际命令与退出码

命令在源码工作树执行；Python 共用原项目 venv，但 PYTHONPATH 指向当前工作树及已存在的 reader/runtime-deps。TMP/TEMP、本轮创建的 profile、日志、样本和下载放 E 盘。合成 fixture 配置仅进程内传入，不读取真实供应商密钥。

| 实际执行命令 | 结果与证据 |
|---|---|
| `E:\RAG quention\.venv\Scripts\python.exe -B E:\WK\run\browser-accept-20261015\browser_fixture_v4.py --ui-scenarios` | 服务启动成功，停止时正常 shutdown；后台进程退出码未采集，`api-v4.stdout.txt` / `.stderr.txt` |
| `E:\RAG quention\.venv\Scripts\python.exe -B E:\WK\run\browser-accept-20261015\browser_fixture_v5.py --reuse-fixture` | 同 scope 重启，服务启动/关闭成功；后台进程退出码未采集，`api-v5.stderr.txt` |
| `node E:\RAG-Worktrees\weknora-python-port-20261013\frontend\node_modules\.bin\\..\vite\bin\vite.js --host 127.0.0.1 --port 56426 --strictPort`（cwd frontend） | 页面实际访问成功；验收完成后停止本任务进程，监听已释放，后台进程退出码未采集 |
| `E:\RAG quention\.venv\Scripts\python.exe -B -m pytest tests/weknora_port/test_documents.py -q --basetemp E:\WK\run\browser-accept-20261015\pytest-documents` | exit0，4 passed；`test-documents.txt` / `.exitcode.txt` |
| `node node_modules\@playwright\test\cli.js test adapter.spec.ts --config E:\WK\run\browser-accept-20261015\ui-targeted.config.cjs`（cwd frontend） | 最终 exit0，6 passed；`ui-mock-final.txt`、`ui-mock-report.json`。这是 API mock 契约，单列，不作真实链路替代 |
| `.\node_modules\.bin\tsc.cmd --noEmit`（cwd frontend） | exit0；`tsc-final.txt` / `.exitcode.txt` |
| `E:\RAG quention\.venv\Scripts\python.exe -B E:\WK\run\browser-accept-20261015\collect-final.py` | exit0；最终7轮及 R3 唯一性检查，`collect-final.stdout.txt` / `.exitcode.txt` |
| `E:\RAG quention\.venv\Scripts\python.exe -B E:\WK\run\browser-accept-20261015\check-source-scope.py` | exit0；错误 KB 原文404，`.exitcode.txt` |
| `docker compose --project-name eviroute-rag --file docker-compose.eviroute-rag.yml config --quiet` | exit0 |
| `docker compose --project-name eviroute-rag --file docker-compose.eviroute-rag.yml up -d --pull never --no-build --wait --wait-timeout 30` | exit0，双 healthy；`docker-migration/compose-up.txt` |
| `.\scripts\eviroute-rag-dependencies.ps1 status` | exit0，双 healthy |
| `git diff --check` | exit0 |

没有运行全项目回归、整批语料或真实模型调用。未实现的命令不得报告通过。

## 失败保留与修复说明

所有证据根目录：`E:\WK\run\browser-accept-20261015\`。访问日志记录实际 method/path/status，未记录密钥；`console-final.json` 为本轮捕获的 error/warn（空数组）。

1. 机器关机后 Docker 未启动导致首次 PG 连接超时；`api-start-first-failure.txt` 和初始 stderr 保留。只启动已批准依赖，没有替换旧业务服务。
2. 早期自动创建 Edge 与用户要求不符；启动失败/警告记录保留，之后改用用户现有 Edge 扩展会话，没有管理员浏览器、ACL 或禁用沙箱绕过。
3. 基线前端不自动刷新图片后台状态，截图 `first-ui-stale-pending.jpg`；后端真实失败但页面仍 pending。修复轮询及范围切换，`upload-state-v4.jpg` 证明后台正常完成后页面更新。
4. 旧 fixture 的 Caption 提示断言要求 `Chinese (Simplified)`，首轮配置 zh-CN 触发 `IMAGE_REQUEST_OUTCOME_UNKNOWN`；是替身断言/配置不一致，不能当真实模型质量失败。原 UNKNOWN / sending 记录未清除，换新合成 scope 正确设置后复验。
5. 临时 v3 runner middleware 组装错误及两次 API mock 测试选择器错误的原日志均保留。没有删除旧断言；只修复新测试不唯一/不可见 locator。
6. 取消或恢复后顶栏 notice 残留旧等待文案；`957048b...` 修复，真实 Edge R7 的 `cancelled-notice-fixed.jpg` 显示取消终态。

## 容器迁移、保留文件与收尾

Compose 项目 **eviroute-rag**（人类项目名 EviRoute RAG）。两个依赖服务分别为 `eviroute-rag-postgres`、`eviroute-rag-redis`。原 external named volumes 保留；未改端口、未清库、未重建卷。切换前 PG dump / Redis RDB 在 `docker-migration/`，数据计数前后一致后才移除旧停止容器 wrapper，未带 `-v`。具体操作见 `EVIRoute_RAG_CONTAINERS_20261015.md`。

四个原未跟踪文件保留：`OWNER_DEEPSEEK_HANDOFF_20261014.md` 及三份 `P6_ORDINARY_GO_BUILD_20261014` exitcode/stderr/stdout。`.serena/project.yml` 的工具自动元数据更新独立保留，未纳入提交。没有重置工作树或覆盖其他会话修改。

验收后通过唯一 stop 文件让 API 正常退出，仅停止核对路径及端口后的本任务 Vite；56425/56426 已无监听。`cleanup.json` 为收尾证据。用户已有 Edge 保留。按新增容器整理请求保留双依赖容器 running/healthy；没有停止原 WeKnora 或旧业务服务。

剩余最小工作为：提供正常、隔离、无真实请求的 authority/模型传输配置并复验标准 API＋独立 worker 启动；该缺口未被批准延期，也未被算作 PASS。前端、API、解析容器封装按 Owner 最新指示暂不进行。此次报告不代表 Owner 里程碑验收或生产可用性批准。
