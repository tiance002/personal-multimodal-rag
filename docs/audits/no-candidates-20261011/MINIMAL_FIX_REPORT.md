# NO_CANDIDATES 限定修复执行报告

## 1. 目标与改动

目标是原普通网页的真实故障闭环，当前交付是原文恢复和源码诊断/准入补丁，**不是问答闭环已验收**。
实际运行身份和原 Run 见同目录 LOCAL_RUNTIME_IDENTITY.md、NO_CANDIDATES_ROOT_CAUSE.md、root-cause.json。

- `scripts/restore_scoped_storage.py`：只读规划、确认原 Scope、校验 CAS 与容器身份后补齐缺失对象；已有对象不覆盖。
- `backend/app/config.py`、`bootstrap.py`、`adapters/models/runtime_readiness.py`：普通装配可配置固定哈希 BGE tokenizer；缺失能力明确 NOT_READY；API/Worker 共用 composition root。
- `main.py`、`api/routes.py`：增加配置级 /readyz，保留 /healthz liveness；models 返回实际角色身份；API 输入上限与内部 4096 一致。
- `application/retrieval.py`、`run_metrics.py`、`ports/providers.py`：保留 q0 回退、排序与 Scope，记录逐路 NOT_SELECTED/NOT_SENT/FAILED/EMPTY/OK；缺失 profile 不冒称空命中；公开原因限固定白名单。
- `adapters/postgres/knowledge_repository.py`：摄取保留固定 provider 原因；不改版本/索引事务。
- `frontend/src/api/client.ts`：区分未知、能力故障、正常空匹配与输入超限；POST 丢失后只从同一 user/run 的已提交 scoped GET 恢复澄清，不重新 POST。
- `frontend/src/app/App.tsx`、`components/Sidebar.tsx`：真实配置状态，取消虚假云端关闭文案；显式 KB 外发许可开关默认不变。
- `contracts/openapi.json`：仅记录新增 /readyz 与输入限额；不顺带批准旧 Memory 路径。
- `backend/tests/test_runtime_readiness.py`、`frontend/tests/{sse,clarification-response}.spec.ts`：上述边界的离线/SIMULATED 验证。注册表只接收普通 `BAAI/bge-m3`；新增用例确认 Pro 被拒绝且没有回退。

没有改变检索权重、Top-K、Rerank、Parent/Child、历史 identity、引用 Validator。
没有改生产环境密钥、旧预算 ledger、旧 grant、业务 DB Schema、KB 外发标记或启动服务。

## 2. 执行命令与证据

均在 `E:\RAG quention` 执行。报告目录 `var/reports/no-candidates-20261011` 为本机证据，不含密钥或原文。

原文只读规划和实际补齐：

```powershell
& .venv/Scripts/python.exe -B scripts/restore_scoped_storage.py --api-container raglocalfirst0930-api-1 --db-container raglocalfirst0930-db-1 --kb-id d78f3434-0e8f-40bf-afcb-f1c2c311ff51 --run-id eb3b6fc5-b743-4274-991e-281657de3da1 --source-root 'E:\RAG quention\var\local-first\storage' --target-root /data/storage --report var/reports/no-candidates-20261011/storage-plan.json
& .venv/Scripts/python.exe -B scripts/restore_scoped_storage.py --api-container raglocalfirst0930-api-1 --db-container raglocalfirst0930-db-1 --kb-id d78f3434-0e8f-40bf-afcb-f1c2c311ff51 --run-id eb3b6fc5-b743-4274-991e-281657de3da1 --source-root 'E:\RAG quention\var\local-first\storage' --target-root /data/storage --report var/reports/no-candidates-20261011/storage-restore.json --execute --confirm-scope d78f3434-0e8f-40bf-afcb-f1c2c311ff51
```

退出码分别 0、0。11 个唯一对象补齐、overwrites=0、database_writes=0、provider_calls=0。
Python urllib 经原 14188 入口读取六文档 source/preview 的 HTTP 验证退出 0，12/12 HTTP 200；
仅保存状态/类型/长度，详见 source-preview-check.json，不把其当作模型调用或问答验收。

源码验证使用进程内环境隔离，防止导入普通 app 时连真实服务：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:RAG_DATABASE_URL='sqlite+pysqlite:///:memory:'
$env:RAG_STORAGE_ROOT='E:\RAG quention\var\reports\no-candidates-20261011\test-storage'
$env:RAG_CLOUD_ENABLED='false'
$env:RAG_LANGFUSE_ENABLED='false'
$env:RAG_LOCAL_ANSWER_ENABLED='false'
$env:RAG_LOCAL_QUERY_ENABLED='false'
& .venv/Scripts/python.exe -B -m pytest -c pyproject.toml -p no:reporter -p no:cacheprovider --basetemp 'E:\RAG quention\var\reports\no-candidates-20261011\pytest-final' backend/tests/test_runtime_readiness.py backend/tests/test_bootstrap.py backend/tests/test_model_provider_contracts.py backend/tests/test_retrieval_routing.py backend/tests/test_p55d_retrieval.py backend/tests/test_p55b_context_window.py -q --tb=short
& frontend/node_modules/.bin/tsc.cmd --noEmit -p frontend/tsconfig.json
$env:PLAYWRIGHT_CHROME_PATH='C:\Program Files\Google\Chrome\Application\chrome.exe'
npm --prefix frontend test -- tests/sse.spec.ts tests/clarification-response.spec.ts tests/stale-citation.spec.ts --reporter=line --output=../var/reports/no-candidates-20261011/frontend-final
& .venv/Scripts/python.exe -B scripts/contract_test.py --report var/reports/no-candidates-20261011/contract-r2.json
git diff --check
```

| 检查 | 退出码 | 实际结果/证据 |
| --- | --- | --- |
| 最终定向 backend | 0 | 156 passed，5 warnings，backend-final.txt；离线/SIMULATED，不是 PostgreSQL 或供应商实测 |
| 最终 TypeScript | 0 | 无输出 |
| 最终三文件 frontend | 0 | 27 passed，frontend-final.txt；mock API、独立测试 Vite/Chrome |
| contract | 1 | FAIL，五条旧 Memory 路径快照漂移、31 个 layering imports；required paths 全在、SSE sample errors=[] |
| baseline layering 比较 | 0 | 原 HEAD 和当前均 31，新增/删除边为零；layering-baseline.json，忽略行号移动但保留导入边重数 |
| diff whitespace | 0 | 无输出 |

保留失败尝试：frontend-r1 退出 1，22 passed/14 failed；其中三个 SSE 动态 import 被过宽 mock 路径匹配，已修正 mock 为 /api/v1/ 并验证。
重复请求测试原先 5 秒断言短于已存在的十秒 scoped Run 启动轮询，已调整断言等待并验证。
Scope-sync 的十项旧界面测试未通过（旧 #kb-select / “仅此文档”定位）；尚未完成当前界面对应的替代验证，**仍记 FAIL，不算修好**。
contract-r1 的 /readyz 新增漂移已在限定 snapshot 中补齐，contract-r2 仍 FAIL；未通过的总门不改称 PASS。
早期只读命令有不存在路径/PowerShell 参数输入错误，已使用正确路径/参数复读；不将其当项目测试通过。

## 3. 结果

真实服务原文/预览恢复：PASS（有限 Scope）。
源码定向离线机制与模拟恢复：PASS。
关联 Scope UI 回归：FAIL；全合同门：FAIL。
真实云端正常摄取/生成/最终引用/原 q0 重放：NOT RUN。
业务库迁移、新版本索引、发布/里程碑：NOT RUN。
**未实现的命令不得报告通过。** 没有运行 verify-mX 或 verify-release，不能把本报告视为其通过。

## 4. 风险与遗留

P1：普通服务的真实 Chat ModelWindow 协议计数来源和持续用量策略尚未接通，readiness 必须保持 NOT_READY。
已有本地能力适配绑定冻结评测 grant/执行租约，不能延长旧 grant 或填造 hosted tokenizer 验证。
只读复核进一步限定复用：Xing counter 的 ModelWindow 预检查可脱离 grant，
但只支持空 tools、16 条消息、4096 本地输入 bound、512 输出及 8192 内容 UTF-8 字节；
仍须最终 Request.data 核验。DeepSeek text counter 只接受固定单 user/thinking disabled/512 输出协议，
不是当前通用 ModelWindow counter；普通 CloudChat 发送 body 与该 recipe 不一致，不能直接注入放行。
Vision 的图像计数上界/结算合同尚无可复用证明，text counter 明确拒绝图片。
这些是源码复核结论，不是现场 Run 的直接根因；上游实际模型身份 UNKNOWN。
用户新的普通 BGE/Xing/DeepSeek 授权记录不等于已经有并发安全的服务预留/UNKNOWN 账本实现。
当前 API 为旧版本、DB 为 0013、Scope 外发许可为 false；不能把新源码测试套在旧运行版本上宣称云端闭环通过。
旧索引真实不兼容尚未在当前新进程下验证，没有全库重建依据。
预览恢复仅覆盖本次活动版本；没有声称验证全部历史引用。
Scope UI 失败和合同失败保留，未批准延期；没有自行降级核心流程风险。

## 5. 版本与下一步

HEAD 仍为 d13951afbd4de4432d231a5921c8657daedab853，补丁未提交、没有 Tag、没有发布或负责人验收。
下一个最小任务是复用已审核能力建立普通服务的注入合同与独立新授权策略；
在明确容量与预算证据后再准备一致 API/Worker 配置，以及经备份核验的业务库升级/不可变新版本方案。
仅在明确当前 KB 外发许可后进行限定正常入库问答；原 hello/你好按原 Scope 重放并记录真实终态。
MANUALLY_SUPERVISED_TRIAL，不声称控制面自动强制全部协作护栏。

## 续做记录：普通服务预留合同（2026-10-11）

任务 Goal 保持 active；以下是后续实现进展，不改变上面的闭环 NOT RUN 判定。

新增 `ports/service_policy.py`：不可变服务政策、模型角色绑定、报价与 scoped request 的零报价预留。
零报价仅来自显式普通服务 policy；没有用旧授权窗口或单独的零费用环境值产生许可。
`application/budget.py` 仅增加该固定类型，现有月度预算 gate 继续执行。

新增 `adapters/postgres/service_usage.py` 与 `application/service_usage.py`：
在发送前同时预留 Token 与既有月度成本，绑定请求、Scope、最终 wire 哈希和成本 reservation。
政策首次持久化后，同 ID 的 fingerprint 改动被拒绝；多个角色共用政策额度，
reserved/unknown 计入用量，已 unknown 的记录不会被稍后重复的 not-sent 结算释放。
报告用量超过输入/输出预留时持久暂停政策，并阻断已等待发送的其他请求。
成本发出后仍为 UNKNOWN；Token 用量结算不能冒称供应商账单已核实。

新增迁移 `0023_service_token_policy.py`；已有旧授权/资料表不改写。
**该迁移在实际业务 DB NOT RUN，普通模型发送路径也尚未接入。**
SQLite 只用于离线 fixture；生产跨进程保护使用 PostgreSQL advisory lock，真实 PostgreSQL并发验证尚未运行。

实际新增检查命令（沿用前文隔离环境变量）：

```powershell
& .venv/Scripts/python.exe -B -m pytest -c pyproject.toml -p no:reporter -p no:cacheprovider --basetemp 'E:\RAG quention\var\reports\no-candidates-20261011\pytest-service-policy-r2' backend/tests/test_service_usage.py backend/tests/test_budget_gate.py backend/tests/test_quick_chain_budget.py backend/tests/test_pretransport_budget.py -q --tb=short
```

退出码 0，21 passed / 5 warnings，`var/reports/no-candidates-20261011/service-policy-r2.txt`。
测试包括第11次显式普通免费请求、共享额度、unknown/重启、重复派发、并发预留、
政策上调拒绝、报价/月度预算拒绝、报告超界后暂停与发送前 fencing；全为离线/SIMULATED。
首轮六个新机制测试也退出0，见 service-policy-r1.txt。

上述156项/27项属于先前输入版本的检查记录；本轮预算改动之后实际重跑的是这21项，
不把旧大范围结果冒称本轮所有新增模块已经通过。
下一步：普通服务能力 loader → API/Worker 同一装配 → 最终 wire 准入与普通角色调用 →
受控实际环境迁移/新版本/原问题/正常入库问答验收。


## 续做记录：普通角色装配、容量与迁移准备（2026-10-11）

以下替代前文“普通发送路径尚未接入/资源缺失”的源码状态说明，业务闭环依然 NOT RUN。
普通服务策略、固定容量 loader 与最终发送门已经接入 API/Worker 共用的 composition root；
角色缺失政策或容量时保持 NOT_READY，不能回退旧一次性评测许可。全局禁止外发时普通角色禁用。
新增普通 VLM 摄取适配器，以服务端 Job/document/version/Scope 建立调用身份，每次发送前复核租约与当前 KB 授权；
生成 Caption 保持 UNVERIFIED，保留原文定位。DeepSeek expensive/vision 共享百万累计 Token 策略。

固定 BGE、Xing 五文件与 DeepSeek tokenizer/recipe 资源已经核验。Xing 在线下载的 TLS 失败不再阻断：
找到本机既有公开资源目录后，只复制并核验列明的五个文件，没有执行远程 Python。
本地托管协议等价性仍 UNKNOWN，供应商请求使用最终 wire 复核与用量超界暂停策略。
部署 API/Worker 共享公开模型配置和只读资源挂载；新增无宿主端口的固定 Redis 镜像。
Docker context 排除本地环境文件、数据/备份、虚拟环境和前端缓存。

实际执行证据：

- `.venv/Scripts/python.exe -m pytest -c pyproject.toml -p no:reporter -p no:cacheprovider backend/tests/test_service_caption.py backend/tests/test_service_configuration.py backend/tests/test_service_usage.py backend/tests/test_model_provider_contracts.py backend/tests/test_local_caption_enricher.py backend/tests/test_runtime_readiness.py backend/tests/test_bootstrap.py -q --tb=short`：退出0，129 passed / 5 warnings；`service-integration-r1.txt`。OFFLINE/SIMULATED，不是实际云端调用。
- 独立 Linux 容器加载固定官方 recipe 并执行 `scripts/smoke_service_capacity_linux.py`：退出0，真实本地计数器5个正例、3个拒绝例；图像为 SYNTHETIC，provider calls=0；`deepseek-capacity-local-r2.txt` / `deepseek-capacity-local.json`。初轮正例使用超过既有4百万像素上限的图像导致失败，修正夹具后通过，超限反例保留。
- `docker pull redis:7.4.2`：退出0；`redis-image-pull.txt`。尚未据此宣称 Redis 服务已运行。
- `docker exec raglocalfirst0930-db-1 pg_dump -U rag -d rag -Fc`：退出0；二进制输出仅写入忽略的本地备份，随后 `pg_restore --list` 退出0。639416字节，SHA-256 `2267e91d5729245296d44ea039a46549bcde32f6267fbb70d8370ff11e3b89a8`；`business-db-backup.json`。不提交私有备份。
- `.venv/Scripts/python.exe scripts/verify_service_migration_restore.py`：退出0；实际隔离 PostgreSQL 恢复备份并由0013升级0023；documents=6、versions=7、chunks=47、runs=101及活动指针/原始来源/片段hash迁移前后一致。`migration-restore-isolated.json`包含精确命令数组、退出码和聚合身份。临时数据库/网络按标签核验后清理，业务迁移 NOT RUN，provider calls=0。
- `npm --prefix frontend exec -- tsc --noEmit --project frontend/tsconfig.json`：退出0。范围同步测试正在按当前 popover 界面更新，未报告通过。

负责人已明确授权目标 KB `d78f3434-0e8f-40bf-afcb-f1c2c311ff51` 仅本任务云端处理。
经现有 PATCH API 将其 cloud_allowed 从 false 改为 true，再回读列表证明其他 KB 未改；HTTP200、provider calls=0；`kb-egress-authorization.json`。
旧 API 和当前执行环境没有供应商密钥；负责人指定 `.env.local` 后，仅由隔离容器加载，输出两家密钥存在性均 true，退出0；`provider-key-presence.txt`。没有查看或输出密钥内容，也不据此宣称鉴权通过。

新 API 镜像构建仍在运行，报告 `api-image-build.txt`。普通服务尚未替换现有业务容器；
真实入库、原 hello/你好 Scope 回放、问答引用/恢复、业务迁移和完整 G4 验收仍 NOT RUN。
HEAD 不变，未提交、未打 Tag、未获得负责人验收。Goal 保持 active，MANUALLY_SUPERVISED_TRIAL。


后续定向检查：`scripts/verify_service_migration_restore.py --quota-check` 退出0，
真实隔离 PostgreSQL 六独立进程同时预留，2获准/4因额度拒绝；UNKNOWN 重连保留、
扩大同ID政策拒绝、超界用量暂停和发送前 fencing 均检查通过。政策为 SIMULATED，provider calls=0，
`migration-quota-isolated.json` 保留命令和聚合结果；原始资料/版本/片段/Run/active hash 不变。

范围同步测试按当前 popover 和显式选会话流程更新；证实服务端409范围拒绝仍等待不存在Run的恢复，
`client.ts` 对明确 CONVERSATION_SCOPE_CHANGED 立即中止恢复并交由 App 只读回查范围。
`$env:PLAYWRIGHT_CHROME_PATH='C:\Program Files\Google\Chrome\Application\chrome.exe'; npm --prefix frontend test -- --reporter=line tests/scope-sync.spec.ts tests/clarification-response.spec.ts tests/sse.spec.ts`
退出0、35 passed，`scope-recovery-current-ui-r6.txt`；真实浏览器/SIMULATED API，非真实模型网页闭环。
早先缺少Playwright默认浏览器、旧可访问名称/未选会话以及输入框可编辑的旧测试假设失败记录全部保留。
`git diff --check` 退出0。实际业务迁移与云端模型闭环依然 NOT RUN。

## 2026-10-12 恢复与当前交付状态

本节替代前文当时的“未部署/未迁移/云端闭环未运行”，不改写历史失败证据。实际 API/Worker 已部署同一镜像，业务库0023，公开小文档普通闭环已通过。原hello/你好最终回答仍未证明成功；Goal继续active，不称已验收。

### 实际修改与兼容

- Composition root、Settings、ProviderFactory、CloudAdapter：普通服务政策/真实最终wire容量/外发/持久预留；保留旧评测截止和有限次数政策，不复用、重置旧grant。
- 摄取：独立Embedding与scoped VLM；固定公开失败码；历史来源不覆盖，新版本成功才切active。
- 检索和运行指标：逐路NOT_SELECTED/NOT_SENT/FAILED/EMPTY/OK、固定provider诊断；排名、Top-K、RRF权重、Parent/Child、引用和Validator保持原逻辑。
- POST/GET/SSE/历史：失败presentation绑定user/run/Scope，权限、澄清、输入、检索和生成分类恢复一致，不再次生成。
- 用户补充的系统设置：五角色提供商/名称/HTTPS网址/API Key，本地原子保存、不回传密钥、revision防覆盖、API/Worker实际同步与重启恢复；未知模型未配系统能力时不可用，不自动给免费/容量/额度。Rerank仍未启用。
- 资料选择器：图标不再挤占名称，长知识库/文件名完整换行；没有改变服务端范围同步。
- 受影响历史测试：确认证明既有HEAD的两项ContextPool失败是BM25夹具连接查询词造成；只修测试样本。隔离容器重启后临时端口变化，严格核验原container/image/database/OID/system identifier后更新专用测试metadata和共用guard，未指向业务库、未放宽外网/secret guard。

完整实际文件与原因映射见 FILE_CHANGE_MAP.md。

### G4 当前矩阵

| 要求 | 当前结论 | 权威证据 |
| --- | --- | --- |
| 普通装配 | PASS配置/实际容器；非供应商全时可用保证 | current-runtime-20261012.json；API/Worker模块hash与同镜像、相同revision |
| 正常入库问答 | PASS有限公开样本真实闭环 | normal-upload-real.json / normal-answer-real.json / normal-web-real.json，两个run及引用 |
| 原 q0 / Scope | 复现PASS；成功回答未证实 | 两轮原回放与original-hello-diagnostic-20261012.json；旧Run未改 |
| 新KB禁止外发 | PASS；初轮文案FAIL已修并保留 | normal-privacy-real.json / normal-privacy-real-r2.json；0调用，POST/GET权限分类一致 |
| provider不可用 | PASS固定原因/无静默fallback；DeepSeek真实失败仍存在 | provider_diagnostics；TLS类型错误，UNKNOWN占用保留；86定向检查 |
| 旧指纹/失败不激活 | PASS | version-preservation-real-20261012.json；7旧版本/47旧片段hash一致，12回读，故意解析失败无切active/0调用 |
| 模型窗口/持续服务 | 局部检查PASS；旧冻结协议保留 | ordinary-xing-window-r1.txt；service-policy/PG quota报告；新配置不移植旧policy |
| 恢复和输入 | PASS当前受影响范围 | final-browser-20261012.txt；failure-history-real-ui-20261012.json；4096 API合同 |
| Context/Memory/引用/取消/BM25 | PASS定向真实隔离与OFFLINE/SIMULATED范围 | affected-isolated-20261012-r1.txt：79；affected-offline-20261012.txt：116；context-bm25-regression-20261012.txt：89 |
| 新模型配置真实保存/恢复 | PASS API/Worker，UI保存测试SIMULATED | model-config-save-real-20261012.json；102旧范围/28恢复范围检查；浏览器五角色保存 |
| 全局contract_test | FAIL既有31 import违规；本轮0新增，其他契约项PASS | contracts-20261012.json；layering-baseline-20261012.json（normalized 22 pairs均为基线）；不为了本任务全仓重构 |

### 本轮命令与退出码

以下均实际执行；报告在忽略目录 var/reports/no-candidates-20261011。

- `& '.venv/Scripts/python.exe' scripts/verify_model_configuration_service.py`：0；真实相同模型配置保存、API/Worker同步、重启保留；0模型调用、预留/UNKNOWN/active hash不变。
- `& '.venv/Scripts/python.exe' scripts/verify_context_pool_baseline.py`：1；独立HEAD源码2 failed/49 passed。归因而非通过证据。
- `& '.venv/Scripts/python.exe' scripts/refresh_isolated_target.py`：0；只更新已核验原专用fixture两个临时loopback端口，不改DB或业务数据。
- `& '.venv/Scripts/python.exe' scripts/verify_p55b2_memory_isolated.py no-candidates-20261012-r1 backend/tests/test_p55a_lifecycle_integration.py backend/tests/test_p55b_context_integration.py backend/tests/test_p55b2_memory_integration.py backend/tests/test_p55d_retrieval_postgres.py`：0，79 passed；真实专用PG/Redis，模型SIMULATED；guard网络/secret/非法子进程/数据库计数均0，Windows stdlib socketpair6次单独记录。
- `& '.venv/Scripts/python.exe' -m pytest -p no:cacheprovider backend/tests/test_context_pool_policy.py backend/tests/test_p55d_retrieval.py backend/tests/test_p55d_smart_reading.py -q`：0，89 passed，OFFLINE/SIMULATED。
- `& '.venv/Scripts/python.exe' -m pytest -p no:cacheprovider backend/tests/test_p55b_context_window.py backend/tests/test_p55b2_memory.py backend/tests/test_global_memory_cloud_egress.py backend/tests/test_cancellation_boundaries.py backend/tests/test_cancel_route.py backend/tests/test_citation_resolution.py backend/tests/test_context_and_locators.py backend/tests/test_context_expansion.py -q`：0，116 passed，OFFLINE/SIMULATED。
- frontend中：`$env:PLAYWRIGHT_CHROME_PATH = 'C:\Program Files\Google\Chrome\Application\chrome.exe'; npx playwright test tests/model-settings.spec.ts tests/p55b21-memory.spec.ts tests/p55c-memory-scope.spec.ts tests/scope-sync.spec.ts tests/clarification-response.spec.ts tests/sse.spec.ts --reporter=line`：0，40 passed，真实浏览器/SIMULATED API。初轮memory glob错误拦截Vite `/src/api/client.ts`，修正到`/api/v1/**`，原失败保留。
- frontend中：`npx tsc --noEmit`：0。
- `& '.venv/Scripts/python.exe' -m pytest -p no:cacheprovider backend/tests/test_model_provider_contracts.py backend/tests/test_service_usage.py backend/tests/test_run_metrics.py -q`：0，86 passed；固定诊断含恶意异常文本不外露测试。
- `& '.venv/Scripts/python.exe' scripts/generate_contracts.py`：0；新GET/PUT配置、密钥writeOnly、现有Memory路径写入真实OpenAPI快照。
- `& '.venv/Scripts/python.exe' scripts/contract_test.py --report var/reports/no-candidates-20261011/contracts-20261012.json`：1，31既有分层违规；无缺失/意外路径、无快照漂移、固定SSE样本无错。
- `docker build -f deploy/Dockerfile.api -t personal-rag:no-candidates-20261011 .` 与 `& './scripts/deploy_no_candidates_service.ps1' -Deploy`：各0；最新源码镜像与模块hash已核对。
- `& '.venv/Scripts/python.exe' scripts/verify_normal_privacy.py`：1；真实0调用/失败不激活通过，但初轮presentation错误。`--resume-presentation`：0；只复用原失败文档，新的拒绝Run无模型调用，POST/GET一致；原Run/receipt保留。
- `& '.venv/Scripts/python.exe' scripts/replay_original_questions.py --diagnostic-only`：0仅表示诊断回放命令完成，**业务结果仍MODEL_UNAVAILABLE，不是答案成功**。
- `& '.venv/Scripts/python.exe' scripts/verify_scoped_version_preservation.py`：0；真实12回读、旧hash保护与失败候选不激活。
- `& '.venv/Scripts/python.exe' scripts/verify_failure_history_web.py`：0；三个真实失败会话各两轮权威历史分类一致，0变更/0调用。
- `& '.venv/Scripts/python.exe' scripts/capture_current_runtime.py`：0；仅公共身份与hash，未查看Config.Env/密钥。

### 风险、未运行与下一步

- 原问题Xing输出未通过校验；DeepSeek升级真实TLS层失败。该问题尚未闭环，不批准延期，不把问候改写成有利问题或伪造引用。需要受控后续诊断/复核，不能不停重试刷到成功。
- 正常额度为新独立普通策略；最新DeepSeek UNKNOWN5883 Token与旧Xing UNKNOWN22980 Token保留。实际费用和hosted等价性UNKNOWN。
- 未知自定义模型的容量/协议/维度适配不是自动发现功能；只保存配置不能称其已能调用。Rerank配置入口存在，但当前未启用普通真实调用。
- 全量发布/全部数据集/六完整评测/新答案全面人审：NOT RUN。原备份仅同盘，不是异地备份。
- 先提交本任务故障修复，再单独归档历史AGENTS附录/整理冗余说明。不得删除迁移、原文、Gold、旧引用、失败或UNKNOWN记录。整体Goal保持active，不自称负责人已验收或打Tag。

最新装配/窗口回归：`$env:XING_TEST_ASSETS = 'E:\RAG quention\var\model-assets\xing4.0\baae3c3e813cad5f888f1f485cfff659c89076c5'; & '.venv/Scripts/python.exe' -m pytest -p no:cacheprovider backend/tests/test_siliconflow_xing_capacity.py backend/tests/test_runtime_readiness.py backend/tests/test_service_configuration.py backend/tests/test_service_caption.py backend/tests/test_bootstrap.py -q`：退出0，96 passed / 7 warnings，final-assembly-window-20261012-r2.txt。首轮未设置既有夹具必需的XING_TEST_ASSETS而26 passed/70 setup errors，保留为FAIL；设置已核验资源目录后重跑，不改测试guard、不下载或调用模型。
