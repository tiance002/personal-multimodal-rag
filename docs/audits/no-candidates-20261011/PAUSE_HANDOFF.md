# 暂停与续做交接

记录日期：2026-10-11。负责人明确要求“任务收尾，方便之后继续，我要暂停执行了”。
本次只保存交接，不继续模型调用、测试、迁移或部署。状态 PAUSED；不是完成或验收。
执行模式 MANUALLY_SUPERVISED_TRIAL。没有自动继续任务。

## 工作树与运行状态

- 工作区 `E:\RAG quention`，分支 main，HEAD `d13951afbd4de4432d231a5921c8657daedab853`。本任务修改尚未提交，未打 Tag；勿 reset/clean 或覆盖现有修改。
- UI `http://127.0.0.1:14188/`，本轮恢复 Vite，工具终端 session 9130。API 8000，API/Worker 当前镜像 `personal-rag:no-candidates-20261011`，最新构建报告 `api-image-build-model-settings.txt`，部署退出0。
- Compose project `raglocalfirst0930`；API/Worker/Redis/DB 保留运行。DB `raglocalfirst0930-db-1`、原数据库/存储卷不重建；migration `0023_service_token_policy`。
- 本轮没有待完成的测试/构建/部署命令。两个 audit 子代理已完成，旧 review 已 interrupted；不要重复派发审查。
- 密钥由负责人指定 `E:\RAG quention\.env.local`，仅启动程序私下加载；没有查看或输出内容。不得将其、私有备份或 var 内资料加入 Git。

## 本轮 UI 修复

- `frontend/src/components/ModelSettingsPanel.tsx`：Embedding、Rerank、Chat cheap、Chat expansive、VLM 五个角色；模型提供商、名称、HTTPS 接口网址、API Key 输入；保存后清空密钥输入。
- `/api/v1/settings/model-configuration` GET/PUT：配置保存到共享 storage 的 `private/model-settings.json`，仅返回 key_configured 布尔值。原环境密钥可继续使用；空输入保留同目的地的已存密钥，更换提供商/模型/网址不搬运旧密钥。
- `user_settings.py`：原子写文件、Linux 目录0700/文件0600、revision 防覆盖；API 保存成功后重建装配，Worker 在当前 Job 完成后的下一轮读取新 revision，启动加载保存配置。
- 用户配置不能移植旧模型的免费报价、额度或容量证明。普通 BGE-M3、Xing、DeepSeek 的已有适配器继续使用；自定义模型可以保存，但没有系统能力/用量适配时明确不可用。**不能宣称任意提供商/模型均已可运行。** Rerank 目前未获启用和普通策略，仍不可用。
- `ChatPanel.tsx`、styles：图标固定宽度，名称独立 flex 区域，长名称换行、标题保留全名，取消横向溢出。
- 未配置本机身份时隐藏不可用记忆面板，先读记忆设置成功才请求列表，避免显示 MEMORY_PRINCIPAL_REQUIRED 原始错误。
- 真实网页已查看五角色表单和资料名称，无秘密值、无模型调用。真实 UI **未点击保存**；保存/重启/密钥等行为由离线 API 测试验证，不能混称真实 UI 保存验收。

## 本轮实际命令与证据

证据目录：`E:\RAG quention\var\reports\no-candidates-20261011`（忽略的本机文件）。

| 原样命令（工作目录注明） | 退出码 | 结果/证据 |
| --- | ---: | --- |
| `& '.venv/Scripts/python.exe' -m pytest backend/tests/test_user_model_settings.py backend/tests/test_model_provider_contracts.py backend/tests/test_service_configuration.py backend/tests/test_runtime_readiness.py -q` | 0 | 102 passed, 6 warnings；OFFLINE/SIMULATED；`model-settings-backend.txt` |
| frontend 中：`$env:PLAYWRIGHT_CHROME_PATH = 'C:\Program Files\Google\Chrome\Application\chrome.exe'; npx playwright test tests/model-settings.spec.ts tests/scope-sync.spec.ts --reporter=line` | 0 | 12 passed；真实浏览器/SIMULATED API；`model-settings-browser-r2.txt` |
| frontend 中：`npx tsc --noEmit` | 0 | 类型检查通过；最后一次发生在 MemoryPanel 顺序读取微调之前，最新该微调仅由真实网页检查覆盖 |
| `docker build -f deploy/Dockerfile.api -t personal-rag:no-candidates-20261011 .` | 0 | `api-image-build-model-settings.txt` |
| `& './scripts/deploy_no_candidates_service.ps1' -Deploy` | 0 | SERVICES_STARTED；未据此称提供商鉴权/模型调用通过 |
| `& '.venv/Scripts/python.exe' scripts/verify_model_settings_web.py` | 0 | `model-settings-real-ui-r3.txt/json`，5角色、9名称、0模型调用、0变更请求；两张 screenshot 已实际查看 |
| `git diff --check` | 0 | 本轮差异格式检查；本交接文档添加前执行 |

失败尝试保留：浏览器测试初轮缺少对象花括号语法错误；真实 UI 初轮14188未监听；第二轮记忆列表 POST 导致严格只读断言失败，顺序读取设置后第三轮通过。曾从仓库根目录误执行 `npx tsc --noEmit`，取得非项目 tsc 并失败；其后在 frontend 使用真实已安装 TypeScript 退出0。不得将这些初轮报告记作通过。

## 原文件任务已实现/实测部分

- 缺失11个 scoped CAS 对象恢复；6份原文/6份预览回读HTTP200的原证据保留。
- 普通服务与旧 V6 授权分离：持久 Token 预留、UNKNOWN 占用、并发/预算/外发门；DeepSeek expensive/vision 共用累计100万输入+输出，不重置旧7 calls/855 tokens或旧 UNKNOWN。
- 原DB备份、隔离恢复和0013→0023迁移/资料聚合不变验证已执行；实际业务迁移/部署已完成。备份路径 `business-db-before-0023.dump`，只在忽略目录；SHA见 `business-db-backup.json`。
- 普通 BGE（禁止Pro）真实公开文档上传/索引成功，真实问答运行 `953a90d1-13ad-4fc3-8cc2-9d0f1135ee5c` 和网页运行 `1d8aa365-adf7-48a4-82a1-b13531577bb4` 回答负责人陈澄、日期2026-11-05，引用点击/历史刷新成功。路由包含 Xing 和 DeepSeek escalation，不能称只使用 Xing。
- 原Scope六文档均通过新版本 API 建立不可变新版本，Job成功、活动版本兼容。旧版本、原始文件未覆盖；升级报告 `scoped-new-versions-real.json`。
- 原 hello/你好回放候选已恢复。第一轮 MODEL_CONTEXT_WINDOW_EXCEEDED；普通 Xing 窗口修复后第二轮 **MODEL_UNAVAILABLE/ProviderFailure**，不能称原问题回答成功。最新新run分别 `2b0a1c42-9256-40de-8225-c50d3e4ed5af` / `b999363f-879c-4394-81c6-cb19fc52de95`。约30秒失败不证明 timeout；transport cause UNKNOWN。两笔UNKNOWN继续占额度，不重复发送。
- 通用失败 presentation 已接 POST、服务端终态历史 GET 和 SSE 恢复，区分检索失败/空召回/回读不足/生成失败。后端28检查退出0（`run-presentation-r1.txt`），浏览器36检查退出0（`run-presentation-browser-r1.txt`）；最新真实旧失败历史刷新展示仍待验证。

## 恢复后的最小顺序

1. 先读本文件、checkpoint、工作区 diff、root-cause.json 和当前运行身份，核对仍是此工作树/容器；不要重跑已有真实 POST、上传或升级。
2. 先验证本轮模型配置的真实保存与 API/Worker 同步、实际服务重启后恢复；仅沿用原模型/目的地，避免引入新模型调用或改原额度。更新实际 OpenAPI snapshot（新配置 GET/PUT 尚未同步）、当前配置文档和必要合同检查。未知提供商能力/维度仍需系统适配，不向用户索要 Token 数字。
3. 检查最新失败的真实历史展示。`verify_normal_privacy.py` **NOT RUN**，先检查它的 receipt 是否存在；未许可公开KB外发0调用、新版本失败不激活、旧版本/源/片段保护聚合检查仍待完成。
4. 历史受影响回归：原直接pytest有165 passed/2 failed/65 setup errors；65为 ISOLATED_RUNNER_REQUIRED。旧 isolated runners 固定端口/revision与现有专用fixture不匹配；勿放宽guard、指向业务DB或修改旧冻结协议。2项 context_pool 边界失败需核实基线归属。保留原失败，必要隔离验证尚未完成。
5. 更新 MINIMAL_FIX_REPORT、LOCAL_RUNTIME_IDENTITY、ROOTCAUSE、progress 与逐条 G0–G5 状态；前文过时 NOT RUN 仅代表当时状态，本交接是当前说明。修复提交后，再独立处理历史 AGENTS 附录归档/有证据的冗余清理，不能先大规模删除。
6. 最后分开提交修复与清理，列真实验证、风险、SHA；不得自行验收、批准延期或打Tag。

既有授权继续适用：仅目标 KB `d78f3434-0e8f-40bf-afcb-f1c2c311ff51` 对本任务外发；Embedding 普通BGE不限次数，Xing不限次数，DeepSeek累计100万Token不限次数，禁止BGE Pro；无需重复索取密钥路径。恢复需负责人明确说继续。
