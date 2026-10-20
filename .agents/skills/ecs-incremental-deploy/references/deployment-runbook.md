# 增量部署运行手册

## 入口与历史坐标

本文件为**项目操作参考，不是当前ECS盘点**。运行前核实当前证据；普通部署不重装基础设施、不重导六套数据、不重Embedding/HNSW/BM25。以下路径来自D8/S8.3-R成功记录，变化时以实际受信任目标为准，不能自动改主机或猜密钥。

| 项 | 历史核实值/仓库入口 | 当前必须证明 |
|---|---|---|
| 仓库 | `tiance002/personal-multimodal-rag`；`deploy/s8/compose.yml` | 实际工作树/HEAD/合法增量，无dirty覆盖 |
| 后端镜像 | `deploy/s8/Dockerfile.api`，包括backend、eval_center、scripts；API/Worker共用 | 目标源码manifest、架构/依赖、无外部源码遮蔽 |
| 前端镜像 | `deploy/s8/Dockerfile.frontend` | 前端变更及API地址合同是否确实受影响 |
| Compose | 项目`rag-s8`；`/opt/rag-s8/current/deploy/s8/compose.yml`＋`/opt/rag-s8/config/loaded-images.json`；env文件`/opt/rag-s8/config/runtime.env` | 容器Compose标签指向的完整文件链；不能只读仓库默认值 |
| 服务 | `api`/`worker`/`frontend`/`db`/`redis`；Worker为profile | 发布前各自状态、任务是否空闲；停着的不默认启动 |
| 正式入口 | `python -m scripts.s8_ecs_runtime api/worker/check` | check会鉴权访问Ready，成功**stdout可为空**，不是JSON CLI |
| API | 主机回环`127.0.0.1:18800`→容器8000 | 实际发布端口、鉴权、tenant332，不以implementation字段单独判身份 |
| 数据 | PG `weknora_s8` / systemID `7698420805889187877`；Redis DB8/前缀`weknora-python-s8:runtime` | 当前实例物理身份、版本、挂载；Redis run_id重启会变化，须解释而非盲替换 |
| 来源 | `/opt/rag-s8/storage`→`/var/lib/eviroute/storage` | 真实来源版本/回读/索引状态 |
| 配置/凭据 | Compose声明secret文件及运行环境；本手册不存其值 | 仅核存在/权限/可用状态，禁止输出全环境或含密码DSN |

历史1GiB API/Worker限制和CPU兼容覆盖在有效配置中保留，不能用仓库`cpus`默认覆盖现有兼容设置。D8完整回滚与零云发布见`docs/deployment/ECS_INCREMENTAL_UPDATE.md`；S8.3-R实际问答见`docs/evaluation/S8_3_R_REAL_AB.md`，二者验收范围不同。

## 变更范围与阶段合同

| 阶段 | 前提/操作 | 成功证明 | 失败与下一分支 |
|---|---|---|---|
| 差异 | git目标、dirty快照、远端身份/旧版本/有效Compose已知；diff决定服务 | 文件→共享依赖/合同→服务矩阵 | 不明身份/dirty冲突/不可逆Schema：停止相关变更 |
| 本机验证 | 复用同输入已有定向测试；仅变更影响才补测 | 命令/exit/源码SHA/语义status，不只写“测试通过” | 修本机或工具合同，不把ECS当试错环境 |
| 构建 | 确定Linux架构与底座；只构建目标镜像 | 文件manifest、包版本、正式import、无密钥/数据/临时挂载 | dependency drift先阻止发布；不要将D8特定certifi pin当通用解法 |
| 传输 | 归档或增量清单不可变；固定SSH主机身份 | 本机/远端SHA、大小及新Image ID独立记录 | 超时部分文件不可load；底座/依赖不一致不能改走源码增量 |
| 暂存 | 保存旧Image ID、配置原字节/SHA、状态；无在途任务/发送 | 有效config差异只含批准项；旧镜像仍可用 | 检查挂载/合同；数据项变化先独立审批，别直接up |
| 切换 | Owner明确部署范围，目标服务/配置核实 | 每次一个批准服务；鉴权Ready/实际代码验证后再下一服务 | 失败只回滚对应镜像/配置；已发送结果不明不补发 |
| 验收 | 目标代码实际加载、Scope/数据同实例 | 改动相关API/Worker、来源、索引及持久化；真实QA另有授权 | 明确PASS/PARTIAL/BLOCKED/NOT_RUN；无法证明不宣称成功 |

## Windows本机：只读差异与构建命令

在实际目标仓库运行。PowerShell先查命令/路径，不从其他工作树复制未提交文件。

```powershell
git rev-parse --show-toplevel
git rev-parse HEAD
git status --short
git diff --stat
git diff --check
# 将已核实远端源码SHA代入，不把示例占位当有效ref
git diff --name-status $deployedCommit $targetCommit -- backend/weknora_port scripts deploy/s8 frontend
# 仅在本次确需构建且基础镜像已核实后执行
docker build --platform linux/amd64 --pull=false --label "org.opencontainers.image.revision=$targetCommit" --tag "rag-s8-api:$targetCommit" --file deploy/s8/Dockerfile.api .
```

构建exit0不够：D8曾因传递依赖漂移被语义status拒绝。先对实际依赖manifest做diff，目标是批准的依赖，不永远固定历史版本。无依赖变化的少量源码增量可复用**已验证**本地/远端底座离线build，前提是全部目标文件/依赖/配置一致并保存两地Image ID；不能因标签相同就跳过证明。归档SHA、Image ID、OCI revision、源码SHA是不同对象。

## ECS Linux：定位服务与核验版本

以下为命令模板，只有已授权目标ECS操作时执行。本轮Skill建立禁止执行。`$api_id`/`$worker_id`由当前Compose标签和实际容器清单确定；不猜容器名。

```bash
docker ps -a --filter label=com.docker.compose.project=rag-s8 --format '{{.ID}} {{.Names}} {{.Status}}'
docker inspect --format '{{.Name}} {{.Image}} {{.State.Running}} {{.State.OOMKilled}} {{.RestartCount}}' "$api_id"
docker inspect --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "$api_id"
docker inspect --format '{{range .Mounts}}{{println .Type .Source .Destination .RW}}{{end}}' "$api_id"
docker image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}} {{index .Config.Labels "org.opencontainers.image.revision"}}' "$target_image"
# check成功可能没有输出；读取exit状态。需要结构化身份时用现有鉴权Ready合同，不打印token
docker exec "$api_id" python -B -m scripts.s8_ecs_runtime check
# 只输出模块位置和SHA，不打印配置/环境/凭据
docker exec "$api_id" python -B -c 'import hashlib,pathlib,backend.weknora_port.http_api as m; p=pathlib.Path(m.__file__); print(p,hashlib.sha256(p.read_bytes()).hexdigest())'
```

`docker ps -a`用于盘点所有状态；判断在运行必须读`.State.Running`或`docker ps`，两者不可混同。实际模块若被bind mount遮蔽，先对比挂载文件与镜像文件/合同。D8删除了两个**逐文件证明完全相同**的旧runtime脚本挂载；这不是删除所有业务/配置/来源挂载的授权。

**有效配置检查和单服务应用更新**：下面这条文件链由D8核实。只有当前标签/文件SHA仍吻合时复用；变化先重新定位。

```bash
docker compose -p rag-s8 --env-file /opt/rag-s8/config/runtime.env   -f /opt/rag-s8/current/deploy/s8/compose.yml   -f /opt/rag-s8/config/loaded-images.json config --quiet
# 真实切换命令，不是只读检查；需本次部署授权和旧配置/空闲证明
docker compose -p rag-s8 --env-file /opt/rag-s8/config/runtime.env   -f /opt/rag-s8/current/deploy/s8/compose.yml   -f /opt/rag-s8/config/loaded-images.json   up -d --no-deps --pull never --no-build api
# worker仅在本次确实受影响、原本运行且空闲时单独更新；前端同理独立确定
```

config --quiet仅证明语法。比较完整有效配置时将结果保存到权限受限证据目录，输出前仅提取镜像、挂载、端口、资源、环境变量**名称/批准的非敏感标志**等allowlist；不要把`docker inspect`或Compose config的全量环境写进聊天/Skill。

## 故障分支速查

每次廉价检查回答一个问题，记“当前假设/证据/下一步”，没有新证据不重复同一命令。

| 触发 | 只读证据/判据 | 首选处理 | 仍失败/停止 |
|---|---|---|---|
| 本机测试失败 | 实际目标SHA、失败用例、依赖和工具退出码 | 修最早失败处，仅重跑受影响范围 | 未通过或证据不适用则不上传 |
| 构建失败/平台不同 | build阶段日志、OS/arch、package diff | 修明确输入/依赖；保留失败镜像/日志 | 不强拉新底座、升级包碰运气 |
| SCP/SSH失败 | pinned host、错误码、两端大小/SHA、磁盘/权限 | 区分身份/权限/链路/容量；保留部分传输 | 不关闭host-key检查；可验证底座才走delta，否则报告受阻 |
| Compose异常 | 正在运行容器的labels/文件链、config --quiet | 修已定位的叠加/变量/旧挂载差异 | 不靠裸`docker compose up`换配置或整栈重建 |
| 容器启动失败 | State.ExitCode/OOMKilled/日志，实际CMD/entrypoint、secrets可读状态 | 修明确局部配置或回滚应用 | 不读出secret内容；不更换数据实例 |
| 版本错/API-Worker不一致 | ID→revision→实际模块/manifest→共享任务合同 | 仅修被证实错误的容器/映射/挂载 | import/合同未证实前不放Worker消费 |
| PG/Redis连接错误 | DNS/目标数据库物理ID、Redis DB/前缀、挂载 | 正确实例才修连接装配 | 不能以关闭身份检查/建新空DB替代 |
| 检索/生成/引用异常 | 同一run的Search→Context→wire→SSE/PG/来源身份 | 确认代码、配置还是已有质量缺口；必要定向回滚 | 不调整模型/阈值刷PASS；无云许可就保留NOT_RUN |
| SSE或上传汇总工具失败 | 原request_id的发送/终态/usage/持久化 | 只读回查；已完成不重传/重POST | 新UNKNOWN停止相关发送，旧UNKNOWN不改、不释放 |
| 资源指标缺失 | 样本文件/时间戳/进程状态/限额 | 明确NOT_RECORDED；修采样退出合同 | 限额不等于峰值，日常服务无OOM不证明候选无OOM；不为补数据重跑已完成云题 |

## 回滚：应用、配置、Git、数据分别处理

- 发布前保存原Image ID、镜像仍存在证明、实际配置原字节/SHA、服务原状态、来源/数据库/索引及账本before快照。保存到本次唯一证据目录，不覆盖历史路径。
- 应用/配置回滚：确认目标、无在途发送/任务、当前未发生其他合法更新；原子恢复本次已验证的配置字节，以上同一Compose文件链仅up受影响服务。Ready、实际旧模块SHA、API/Worker合同、数据/UNKNOWN保护再次核验。旧配置过时或出现并发合法变更则停止盲恢复。
- D8旧材料路径`/opt/rag-s8/state/d8-incremental-20261020/rollback/`、S8.3-R路径`/opt/rag-s8/state/s8-3-r-20261020/rollback/`仅是历史案例，**不能当下一次发布的默认回滚源**。两次都保留了材料，未触发实际回滚；不要说实战恢复通过。
- Git回退仅处理本次独立提交，可用`git revert`形成新提交；先确认dirty/冲突，不自动stash或覆盖他人的变更。Git回退不自动改变运行镜像。
- 数据库Schema/数据恢复须发布前单独批准的备份/恢复计划；普通应用发布没有DB恢复步骤，旧镜像不保证Schema兼容。禁止DROP、删卷或reset充当恢复。

## 输出与业务验收

记录目标/旧版本与镜像、实际SHA/配置差异、受影响服务、每条实际命令/exit/语义status、来源/索引/账本/UNKNOWN保护、rollback `AVAILABLE/NOT_TRIGGERED/EXECUTED_VERIFIED`。计数一致仅证明相应计数，不冒称全库逐字节未变。

验收按改动选最小路径：普通代码更新先零云鉴权/来源回读/索引及合同；真实问答只在当前有效模型许可下走正常FastAPI→检索→Context→LLM→SSE→PG→历史，不注入Gold、不重发UNKNOWN。没有新云许可就`REAL_QA_NOT_RUN`，可报告应用部署证据，但不能宣称全链路真实问答通过。
