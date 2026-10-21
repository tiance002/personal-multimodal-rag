# S8.6.1 获批零Provider隔离准备：实际执行结果

日期：2026-10-21。**本授权范围 `ZERO_PROVIDER_PREPARATION_PASS`，已完成并关闭；四轮真实问答仍 `NOT_RUN`，预算仍 `BLOCKED_RESERVATION_BUDGET`。**

Owner明确回复“批准仅零调用隔离准备”。本次上限Provider0、Token0、问答提交0，实际均0。没有创建Session、发送messages、上传、运行collector或创建Provider执行账本。

## 1. 可追溯版本

| 项目 | 实际值 |
|---|---|
| 获批源码 | `8ba0fcaf0ab665b28a04c006267e05ab10fd7e8f` |
| 当前文档HEAD打包前 | `d282dbbf7e7b926681cb1db0c772e964e50a4a89`；归档明确取8ba而非工作树 |
| profile SHA256 | `ead97b230a9bf5a5a7b42976a9d9f2a2cac961fef5a9f49fb052d17fb871fbc8` |
| 基底镜像 | `sha256:80e2f2d84bf67d7d22117d98676049aa9cd3101e69b20f780d5fd9eeaf931a36` |
| **新镜像Image ID** | **`sha256:c9b5c4b9c1d4f87d94e3109a22def827ea157c44e6800ac277a590a6544f0d43`** |
| 新镜像tag | `rag-s861-api:s861-zero-20261021t151200z-e20abf33` |
| 源码归档 | 520个文件，11,591,680字节；SHA256 `3ee854c453a5a850a32a17ad148c4fce977fbfa0e792c3e4f77a97048d5ed234` |
| 清单SHA256 | `46c0114de9da3eb12a34b7861368de4504694430b38ee9ad0d10516d8841c02a` |
| ECS证据目录 | `/opt/rag-s8/diagnostics/s861-zero-20261021t151200z-e20abf33` |
| 本地证据目录 | `E:\RAG quention\var\reports\s8-6-1\s861-zero-20261021t151200z-e20abf33` |

归档采用Git原始blob字节，包含整个backend/weknora_port、所需scripts、eval_center与requirements；不含密钥、环境文件、附件或用户数据。21个依赖pin与原运行版本29ecf2f5相同，基底镜像内实际版本逐一核对。构建使用 `--pull=false --network=none`，未安装依赖。linux/amd64身份、source/profile/manifest标签均检查。

构建时和运行容器内均核对520个源码/资产文件。运行时检查是在同一容器内另起的零网络Python校验进程完成：其导入的13个关键模块均在 `/opt/s861/app`。同时核对API进程入口、镜像、工作目录、PYTHONPATH及无源码遮蔽挂载；未读取API进程内Python对象，不能声称所有13模块都在Ready前被API进程导入。source_prompt_templates与default_segmenter实际执行，YAML/字典manifest通过；cl100k外置资产SHA正确。

## 2. 真实API零调用检查

- 独立临时容器 `beac60b73c6499f5939ffb71784eb7020fc9b9efb2f523a7be95555477148899`，只发布 `127.0.0.1:18861 → 8000`，1 API进程，没有Worker。
- 命令 `python -B -m scripts.s8_6_1_api_acceptance api --port 8000`；只读根文件系统，原storage、四个secret/principal文件及tokenizer资产均只读。仅新 `logs-v2` 可写。
- `cloud_allowed=false`、Graph=false、DYNAMIC、top1floorless=false；未传Execution文件、真实授权文件或旧额度。
- 新Redis前缀 `weknora-python-s8:s861:s861-zero-20261021t151200z-e20abf33:`，运行前后均0个key。
- 正常私有Guard鉴权GET `/readyz` 返回 **HTTP200**；缺Bearer返回401。401只证明鉴权拒绝，不冒称合法第二用户隔离。
- 既有 `python -B -m scripts.s8_ecs_runtime check` **退出码0，stdout为空**，按原合同判成功。
- Ready实际核对Tenant332、既有Owner指纹、PG `weknora_s8`/systemID7698420805889187877、Redis DB8/run_id及4个现有模型ID。DeepSeek仍deepseek-flash，其余Cheap/Embedding/BGE绑定保持。

## 3. 停止状态与工具失败

共2次临时启动尝试，原始失败全部保留：

1. 首次工具transport给三引号脚本加了缩进，`IndentationError`使校验中断。容器已停止。只修操作脚本，并在新容器/日志目录继续同一零调用范围；同时将临时Redis前缀格式修为profile要求的 `s861:<unique>`。
2. 第二次Ready200、check0和源码校验均成功，但包装器错误要求 `docker stop` 后ExitCode必须0，故包装器返回1。实际容器停止为 **143，OOM=false、restart=0、Running=false**。
3. 只读取回该已停止容器中实际安装的uvicorn0.35 `server.py`。`capture_signals` 在serve结束后重新抛出已捕获信号，因此SIGTERM可返回128+15=143。保存源码SHA和函数原文，恢复同一次已完成结果；**没有第三次启动、没有补发HTTP业务或模型请求**。原run-v2退出1记录保持。

最终两台本Goal临时容器均停止，保留新镜像、归档和日志。只恢复汇总判据，没有修改生产Adapter、生成、history、profile源码或旧失败账本。该现象与项目Skill ECS-006/ECS-007的“检查实际活性、只读恢复原操作、保留信号记录”规则一致；Skill规则未改。

## 4. 运行前后保护证据

| 检查 | 实际前后结果 |
|---|---|
| 正式API/Worker | 同container ID、原image80e2、Running=true、restart0 |
| 三份有效配置 | runtime.env、loaded-images.json、Compose的SHA相同；原字节备份只保留新ECS rollback目录 |
| PG Provider账本 | 13,041行 → 13,041行；UNKNOWN3 → 3；unfinished0 → 0 |
| 账本整表指纹 | `8afa8c2067de684920b3ff73361dcfde` → 同值 |
| sessions / messages / turns | 248 / 524 / 260 → 同值 |
| knowledges / chunks / KB | 11,736 / 28,657 / 7 → 同值 |
| 当前范围原文 | 11行来源/版本/内容SHA相同，含7个原始text |
| 索引 | 87个定义及聚合SHA相同，无重建 |
| memory与image task | 相应表计数保持；memory/image待办0 |
| Redis | 同run_id/DB8；两次检查的新前缀均0key |
| storage marker与tokenizer | SHA保持 |
| 新Provider/结算/UNKNOWN/held/实际Tokens | **0 / 0 / 0 / 0 / 0** |

这些检查证明对应行数、定义、账本整表和当前范围原文不变，不声称全库所有原始文件逐字节检查过。

## 5. 实际执行命令与退出码

主操作命令为：

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -X utf8 -B 'E:/RAG quention/var/reports/s8-6-1/root/zero_remote.py' <action>
```

| action | 原始退出码 | 用时 |
|---|---:|---:|
| `before` | 0 | 1.66s |
| `transfer` | 0 | 2.59s |
| `build` | 0 | 12.98s |
| `run` | 1 | 1.86s |
| `diagnose` | 0 | 0.78s |
| `run-v2` | 1 | 5.55s |
| `diagnose-v2` | 0 | 1.05s |
| `recover` | 0 | 1.1s |
| `after-recovered` | 0 | 1.68s |
| `fetch` | 0 | 13.57s |

`before/after-inventory`另调用 `current_inventory.py --run-readonly --output <fresh-path>`，均退出0，仅PG只读事务和Docker读取。`verify_zero_result.py`退出0，结果 `ZERO_PROVIDER_PREPARATION_PASS`。完整argv、远端脚本、日志与SHA在机器结果和本地证据目录，失败退出码没有重写。

## 6. 验收边界和下一步

这次通过的是**真实最新镜像的API启动、鉴权Ready、PG/Redis读取身份及来源加载**。没有执行QU、检索、桥接、最终生成、引用回读或SSE/history恢复；四轮真实问答仍NOT_RUN。此前的离线67/76测试本轮未重复运行。

原完整预算仍为off/on比较5/109805、7/132474，Frost5/109798、9/136836，均超过现有单账本100000保守预留上限。没有借本次零调用授权激活模型。

本授权已执行并关闭。保留模型预算阻断及第二合法用户阻断，按原报告最小建议交Owner决定后续范围。本轮不继续模型验收，不Push、Tag或部署生产；没有触发数据库/应用回滚，只停止本Goal临时服务。
