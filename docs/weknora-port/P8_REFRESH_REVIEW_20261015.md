# 任务四专项审查：打包刷新、说明、学习地图与冗余清单（零删除）

- 审查者：本轮执行者。基线 HEAD：`e22e428`（任务三提交）。
- 结论：**打包刷新 PASS**（新建 wheel + 隔离安装 + 标准 API/worker 启动 + 最小恢复，均 exit 0）；**说明与学习地图已更新**；**冗余清单已按 HEAD 刷新，未删除任何文件**。
- 未 Push、未合并、未打 Tag；`real_provider_calls=0`；未改旧业务库、未替换服务、未动 external 卷。

## 1. 项一：刷新安装包 + 隔离验证 — PASS

Artifact：`E:\WK\pkg\p8-refresh-ee5919f-20261015`（新路径，不覆盖旧产物）。

| # | 命令（cwd=工作树，除注明） | 退出码 | 结果 | 证据 |
|---|---|---|---|---|
| 1 | `python -B scripts/weknora_port_package.py --ref ee5919f --output E:/WK/pkg/p8-refresh-ee5919f-20261015` | **0** | 源 641 文件；176 native wheel 成员逐字节匹配 raw Git；wheel `personal_rag-0.1.0-py3-none-any.whl` SHA256 `0d60875bed9ef1bfeafe5d0baebcab7a0cb5274aa08d68a6c934baaeaf7604f6`（2866290 bytes） | `E:\WK\pkg\p8-refresh-ee5919f-20261015\manifest.json`、`build.{stdout,exitcode}.txt` |
| 2 | `python -B docs/weknora-port/evidence/P8_REFRESH_20261015/install_verify.py` | **0** | 新建 E 盘 venv + `pip install --no-index --no-deps` 新 wheel；断言 wheel 不含 `backend/app/`、`backend/tests/`；`weknora-python-{api,worker,init} --help` 均 **0**；wheel 含 `ConfiguredTokenAuthority` 与 `token_budget` | `…/P8_REFRESH_20261015/install.stdout.txt`、`…/installed-entry.receipt.json`（写于 artifact 目录）、`E:\WK\pkg\…\{api,worker,init}.help.txt` |
| 3 | `E:\WK\pkg\p8-refresh-ee5919f-20261015\installed\Scripts\python.exe -I -B docs/weknora-port/evidence/P8_REFRESH_20261015/installed_verify.py` | **0** | **4 passed（26.78s）**：实际 loopback HTTP 新入口/独立 worker、reference 两轮 JSONB/历史回读、summary absent/configured 上传索引问答；**101 个 backend 生产模块全部来自 installed site-packages，101 逐字节匹配 manifest，0 源 backend import** | `…/installed.stdout.txt`、`…/installed.receipt.json` |

- 安装/启动只复用显式只读第三方依赖路径（`E:\RAG quention\.venv\Lib\site-packages`、`var\weknora-port\runtime-deps`、`E:\WK\deps\reader`）；**独立依赖解析 NOT_CLAIMED**。
- wheel 源为 `ee5919f`；当前 HEAD `e22e428` 仅新增测试/文档/证据（任务二、三），**不含生产代码差异**，故该 artifact 代表当前生产源码。
- 最小任务恢复：`installed_verify` 的实际 API+worker 用例覆盖图像入库/记忆抽取/重放（历史保留），满足“最小任务恢复”。

## 2. 项二：说明文档更新 — 完成

`docs/WEKNORA_DEVELOPMENT.md` 新增一节 **“Local operations — start / stop / containers / volumes / logs / backup / rollback (2026-10-15)”**，覆盖：
- 容器用途：仅 `eviroute-rag-postgres`(56423)/`eviroute-rag-redis`(56424)，`pull_policy: never`；API/React 仍为宿主进程。
- 数据卷：external named `weknora-python-resume-pg-20261013` / `-redis-20261013`；缺卷拒绝空替代。
- 启动/停止/状态：`scripts\eviroute-rag-dependencies.ps1 start|status|stop`（`stop` 不 down、不删卷）；并说明任务一新增的 `--authority-config`（`synthetic` 共享装配；`approved` 抛 `APPROVED_METER_UNIMPLEMENTED`，真实发送仍拒绝）。
- 日志：`docker logs`；备份：`scripts/backup.ps1` / `backup_manifest.py` 与迁移前 dump 位置；回滚：以 Git 回滚源码，旧镜像/卷不动，禁止用旧镜像对新 schema 或新代码对旧库模拟回滚。

## 3. 项三：架构与数据流学习地图 — 完成

新增 `docs/weknora-port/ARCHITECTURE_LEARNING_MAP_20261015.md`（基于 HEAD `e22e428`），含：运行入口与装配、模型传输/发送门/Token 预算/用量记账、RAG 主链各阶段（上传→解析→切块→嵌入→索引→混合检索→融合→重排→合并→TopK→上下文→生成→路由 0.35→升级→引用→SSE）、会话/记忆队列/文档版本/图像任务、`http_api.py` 路由清单、建议阅读顺序。

**独立抽查**（逐条读文件核对，均一致）：`entrypoint.create_app` L7 / `main` L48；`http_api.create_app` L262 / `_response_stream` L237；`documents.upload_document` L255；`worker.run_once` L5 / `serve` L8；`generation/coordinator.GenerationCoordinator` L53 / `_attempt` L86；`service.accept_turn` L146；`generation/router` 阈值 `Decimal('0.35')` L11 / `decide` L44。地图文首已声明“未列符号表示未核实，宁缺勿猜”。

## 4. 项四：冗余清单（零删除） — 完成

- 刷新命令：`python -B scripts/weknora_port_retirement_inventory.py` → **exit 0**；`candidates=42`、`parse_errors=[]`、`protected_code_files=21`、`new_runtime_import_callers=0`；`missing` 列出 3 个早已不存在的脚本路径（如实记录，非新增失败）。证据：`…/P8_REFRESH_20261015/retirement-refresh.stdout.txt`。
- 刷新后 `docs/weknora-port/P8_RETIREMENT_REVIEW_20261015.md` 与 `evidence/P8_RETIREMENT_REVIEW_20261015/inventory.json` 的 `source_commit` 更新为当前 HEAD，逐文件 SHA/caller 行号随源刷新。
- **未删除任何文件**；`status=REVIEW_ONLY_DO_NOT_DELETE`；历史报告/失败证据/旧业务数据/受保护支持码（21 个）保留。删除仍需**另行批准**的删除批次。

## 5. 未运行 / 边界

1. 全项目回归、真实模型、真实浏览器本轮 **NOT RUN**（沿用任务一/二/三结论）。
2. `.doc/.ppt/.ods`(soffice)、`.doc`(antiword) 真实转换 NOT RUN（本机无二进制，任务三已记录）。
3. 云端真实调用、Cheap/Dynamic/Expensive 对照、准确性指标 **BLOCKED**（无授权；`approved` 计量未实现）。
4. 未容器化 API/React（按 Owner 指示保留宿主进程）；未做镜像构建、compose up、迁移、cutover。
5. 未删除旧代码；`frontend/src/main.tsx` 旧 App 分支、`deploy/legacy`、旧 provider/route 模块调用者仍在（见 inventory.json）。

## 6. 关键哈希

| 项 | 值 |
|---|---|
| wheel | `0d60875bed9ef1bfeafe5d0baebcab7a0cb5274aa08d68a6c934baaeaf7604f6`（2866290 bytes） |
| wheel source commit | `ee5919fc9fa4cddf7e5e41e7965913c9f6f78d25` |
| 刷新前 HEAD | `e22e4289f76f4497e455c5e00bee302fb24960ae` |
| 上游对照 | `3e8b0bfc80b845b2d4b2ed683994748741450a97`（未改） |

## 7. 交付与下一步

- 本项提交：`docs/WEKNORA_DEVELOPMENT.md`、`docs/weknora-port/ARCHITECTURE_LEARNING_MAP_20261015.md`、`docs/weknora-port/P8_REFRESH_REVIEW_20261015.md`、`docs/weknora-port/P8_RETIREMENT_REVIEW_20261015.md`、`evidence/P8_RETIREMENT_REVIEW_20261015/inventory.json`、`evidence/P8_REFRESH_20261015/` 及 installed 验证的 UUID 证据。
- 剩余：按批准批次删除确无调用者的旧代码（本轮未授权）；真实云端的 approved 计量与来源接线（需授权）。
