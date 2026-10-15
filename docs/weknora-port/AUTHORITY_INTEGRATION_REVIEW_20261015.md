# 任务二专项审查：装配改动后的整合验收（标准 CLI / worker + 五项主链检查）

- 审查者：本轮执行者。基线 HEAD：`4fa522b70ddd40667b16f783221044c33f7cca53`（任务一提交）。
- 结论：**PASS（API／HTTP 层与真实标准 CLI 进程、隔离 PG/Redis、closed MockTransport 范围）**。
  **真实浏览器本轮 NOT RUN**；**authority 模式下追问／隔离／路由升级的 wire 级断言为复用证据，非本轮新跑**。
- 调用数：`real_provider_calls=0`（全部 `fixture.invalid` 封闭替身）。

## 1. 本次装配改动的影响面（为什么必须做后端整合验收）

`4fa522b7` 相对 `551c5f0` 改动了 shared assembly 的 10 个文件：

| 文件 | 关键改动 | 是否改变默认（无 authority）行为 |
|---|---|---|
| `entrypoint.py` | `/readyz` 增加 `budget_unit`/`authority_config_sha`；`--authority-config` | 否；仅在给出 authority 时改变 |
| `worker.py` | `--authority-config` | 否 |
| `development.py` | `assemble(..., authority_settings=)`；authority 分支构造 synthetic model_factory + `bind_facts`；embedder 需 `for_operation` | 否；`authority_settings=None` 时原路径不变 |
| `runtime.py` | `token_authority` 透传进 `P1SendGate`；memory embed 传 `operation_id` | 否 |
| `memory_runtime.py` | authority 下缺 `operation_id` 抛错 | 否 |
| `initialize.py` | 应用 token schema 迁移 | 否 |
| `storage/send_gate.py`、`storage/token_budget.py`、`models/transport.py`、`models/usage_facts.py`、`source_facts.py` | token 授权／占用／防重在 authority 分支生效 | 否 |

默认装配（authority 未配置）下应用服务、generation、conversation、documents 逻辑未变，但**装配层**被改，故按任务要求对正式主链做整合验收。

## 2. 环境身份对账（本次必要修正，非降级断言）

- 提交前隔离库测试大量锁死 Redis run_id 字面量（`test_runtime_pg.py` 的 `586b92f4…`、`test_qa_followup_fallback_pg.py` 的 `fa7bde76…`）。二者对应的是**迁移前容器** `weknora-python-resume-redis-20261013`。
- Docker 迁移（见 `EVIRoute_RAG_CONTAINERS_20261015.md`）后当前批准目标为容器 `eviroute-rag-redis` @ `127.0.0.1:56424` DB15，`run_id=de03f13f2ffba1146b01434f25514c4478e046fd`（本轮 `docker exec … redis-cli INFO server` 实测）。
- 因此字面量已过期，**在修正前这些整合测试无法连接**（见 §5 保留的失败证据 `task2-http-history-sse`，exit 1）。
- 最小修正：让这两处改用项目既有约定 `tests/weknora_port/redis_fixture_identity.expected_redis_run_id()`（读 `WEKNORA_PORT_TEST_REDIS_RUN_ID`，未设时回退旧字面量）。**未改任何断言**；未设环境变量时行为与修正前一致（回退 `586b92f4…`，对本轮容器仍会失败，不会误判为通过）。
- 附带效果：`test_qa_caption_causal_pg`、`test_development_entry_pg` 从 `test_qa_followup_fallback_pg` 导入的 `REDIS_ID` 一并随之按环境解析。

## 3. 五项检查结果（本轮实跑，exit 0）

所有命令 cwd = 主工作树；`PYTHONPATH=<worktree>;E:\WK\deps\reader;<worktree>\var\weknora-port\runtime-deps;<evidence>`；
`TMP/TEMP` 与 `--basetemp` 路由到 `E:\WK\run\authority-integration-20261015`（避免 `AUTHORITY_ABSOLUTE_DE_PATH_REQUIRED` 类假失败）。
证据根目录：`docs/weknora-port/evidence/AUTHORITY_INTEGRATION_20261015/`。每条含 `.command.json / .exitcode.txt / .stdout.txt / .stderr.txt / .junit.xml`。

| # | 检查 | 覆盖入口 | 命令（label） | 退出码 | 结果 | 证据 |
|---|---|---|---|---|---|---|
| (a) | 上传→处理→检索→回答→引用回读 | **两个真实标准 CLI 进程**（`entrypoint --authority-config` + `worker --once --authority-config`，authority synthetic，closed mock） | `run-check.py task2-authority-cli` | **0** | 1 passed（20.7s） | `task2-authority-cli.*`、`task2-authority-cli.pg.json` |
| (a)(c) | 上传→RAG→**受控升级**→来源/历史→Redis 重放→超时；隔离/私有来源拒绝 | `development.assemble` + `entrypoint.create_app` + `worker.serve`（fixture 注入） | `run-accept.py task2-a-entry` | **0** | 1 passed（16.3s） | `task2-a-entry.*` |
| (a)(d)(e) | 上传→检索→回答→引用；`upgraded=[False,False,False,True]`（阈值 0.35 未改）；跨库/跨租户来源拒绝；memory 原文门 | 真实 loopback uvicorn + 真实 PG/Redis（fixture assembly） | `run-accept.py task2-a-runtime` | **0** | 1 passed（37.4s） | `task2-a-runtime.*`、`.redirects/…P7_…json` |
| (b) | **新 request_id 追问**：rewrite + 最终 stream 的 messages 含首轮 assistant 内容；replay 不新增发送 | 真实 loopback HTTP/SSE | `run-accept.py task2-b-followup` | **0** | 3 passed（20.1s） | `task2-b-followup.*`；附加：`evidence/QA_ENTRY_20261015/http.result.*.json` |
| (c) | SSE 同通道恢复/错误 EOF UNKNOWN；部分断开隐藏草稿与最终提交竞争（取消） | 真实 loopback HTTP/SSE | `run-accept.py task2-c-sse-cancel` | **0** | 2 passed（13.4s） | `task2-c-sse-cancel.*`；附加：`evidence/QA_ENTRY_20261015/cancel-scope.result.*.json` |
| (c) | 后台任务恢复/重放不重复记账；崩溃后保留占用不复发 | 标准 CLI + 独立 worker（同 (a)） | （含于 `task2-authority-cli`） | **0** | 见 `task2-authority-cli.pg.json`：`replay_new_claims=0`、`crash_replay_new_claims=0`、disabled 时 readyz 503 | `task2-authority-cli.pg.json` |
| (d) | 知识库隔离（ASGI/HTTP 原生 RAG，错误库不串检索） | 真实 loopback HTTP + 真实 PG/Redis | `run-accept.py task2-d-affinity` | **0** | 1 passed（8.3s） | `task2-d-affinity.*` |
| (d) | 摘要/父子引用回读（summary source readback） | 真实 loopback HTTP | `run-check.py task2-summary-readback` | **0** | 1 passed（4.9s） | `task2-summary-readback.*`、`.pg.json` |
| — | 自审复现：重跑标准 CLI authority 路径 | 同上 | `run-accept.py task2-review-repro2` | **0** | 1 passed（21.1s） | `task2-review-repro2.*` |

**边界（必须保留）**：
- (b)(d)(e) 的 wire 级断言使用 **fixture assembly**（`model_factory/permissions/costs` 注入），不是 `--authority-config` CLI 路径：CLI 场景的 mock wire 在子进程内，测试进程无 wire tap，无法断言“历史进入 Provider 消息”。因此 **authority 模式 + 追问/SSE/隔离/升级** 的组合本轮**未被 wire 级测试覆盖**。
- 上述 HTTP 测试是真实 TCP（uvicorn/loopback）与真实 PG/Redis，**非内存函数冒充**；但模型仍为 closed 替身，不代表真实模型质量。

## 4. 复用证据（源码未受本次装配影响者，注明绑定）

| 检查 | 复用来源 | 绑定说明 |
|---|---|---|
| (b)(d)(e) 浏览器 UI 全链路 | `docs/weknora-port/BROWSER_ACCEPTANCE_20261015.md` | 基于 `6c3822bb` / `957048b`（应用前端与服务代码，本次装配未改）；当时 `real_provider_calls=0`；五项（上传→处理→检索→回答→引用、追问历史、刷新/续读/取消、跨库隔离、动态路由）均 PASS |
| (d) 版本失效定向反例 | 同上“失效版本”行 | 单元反例；浏览器 NOT RUN |

## 5. 保留的失败/超范围证据（不得当作通过）

1. `task2-http-history-sse.exitcode.txt = 1`：修正身份前的首次尝试，5 个测试在 `native_fixture.build_native_fixture` 第 18 行身份断言处失败（Redis run_id 过期）。保留以记录“迁移后锁死字面量”这一真实阻塞。
2. `task2-review-repro.exitcode.txt = 1`：复现运行时因我的新 runner 未导出 `WEKNORA_SEQ_IMAGE_RECEIPT` 而在**最后一步**写回执处失败（此前断言已通过）。已修 runner（`run-accept.py` 增补该环境变量），`task2-review-repro2` exit 0。该失败保留。
3. **真实浏览器验收本轮 NOT RUN**（无受控浏览器绑定本机 loopback；沿用上条浏览器证据）。
4. **真实供应商调用 NOT RUN**（本交接无新授权，`real_provider_calls=0`）。
5. 未跑全量回归；未复核 `token_migrate` 旧金额表迁移；`approved` 模式仍 `APPROVED_METER_UNIMPLEMENTED`（沿用任务一）。
6. 其它模块（`test_qa_caption_causal_pg.REDIS_ID` 等独立字面量、`test_local_runtime_pg` 依赖 `redis-identity.json` 回执）未在本次身份对账范围内；如需在其上做验收，须同样提供 `WEKNORA_PORT_TEST_REDIS_RUN_ID`。

## 6. 被测/改动源码哈希

| 文件 | SHA256 |
|---|---|
| `tests/weknora_port/test_runtime_pg.py` | `CE8DE0998FBF2DA1BDB45E4EC39F297EB32090C49C8D8E9FFAC5E91B63B96EA4` |
| `tests/weknora_port/test_qa_followup_fallback_pg.py` | `838D39E936D216A14F9607ECA892A49612819113A7642FF93E5CC87D059B5E3E` |

上游对照：`3e8b0bfc80b845b2d4b2ed683994748741450a97`（未改）。回答路由阈值与 Cheap→Expensive 规则未改。

## 7. 交付与下一步

- 本项只提交：2 个身份对账测试文件、`docs/weknora-port/evidence/AUTHORITY_INTEGRATION_20261015/`、本报告；未 Push、未合并、未打 Tag。
- 未完成：authority 模式下的追问/SSE/隔离/升级 wire 级组合；真实浏览器；真实模型（任务三范围）。
