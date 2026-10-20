# D8 — ECS 应用安全增量更新

## 结论

**ECS_UPDATE_COMPLETE**。2026-10-20，仅将既有 `rag-s8` 的 API 与原本就在运行的 Worker 更新为已提交的 S8.3 应用镜像；完成零模型外发的代码身份、健康、来源回读和数据保护验收。

- 实际运行代码：`965776bd7672fa37839306c6c47537c5414775cd`；分支 `codex/s8-six-dataset-evaluation-20261019`。
- S8.3 实现：`edfaf83844c72de6bedffc4dcdf2297e17c6dfcb`、`e8c3097d1de3552ca91b7e2987159b73bc1907cb`。
- `S8_3_REAL_QA = NOT_RUN`（ECS A/B）；`S8_3_DEFAULT_SOURCE_MODE = off`。
- D8 新增 Provider **0**、Token **0**、UNKNOWN **0**；2条历史 UNKNOWN 及其未知用量保留。
- 本轮没有新增生产代码；只本地提交本报告、同名 results JSON 和 `S8_3_R_PLAN_v2.json`。报告提交完整SHA用 `git log -1 --format=%H -- docs/deployment/ECS_INCREMENTAL_UPDATE.md` 核对，区别于上面的运行镜像源码SHA。

## A. 版本、完整差异与镜像

起点工作树干净。执行 `git status --short`、`git log -5 --oneline`、`git rev-parse HEAD`、`git diff --check`，均成功；未回退代码。比较目标镜像打包范围的508个文件与旧ECS镜像及实际只读覆盖，27个字节级文件差异中，业务生产差异恰为 Search、Merge、source_evidence、pipeline、service 五个已提交文件。其他为运行/离线工具；入口脚本仅换行不同，AST及归一化文本相同。

历史 `runtime.py`、`conversation_configuration.py` 只读覆盖与当前Git逐字节相同，SHA分别为 `78aa92beeca93ce9c62d35eb21b5a78cb8a58b2189e1c9de2c12b585cb59e0c6`、`f05fd68b5844872a60b6b16c4dff7a51104ba6c3467f0c7f0cf403d710099915`。仅从API/Worker移除这两个等价源码挂载；宿主机旧文件保留。运行默认值0→快速2048/智能每轮4096的既有行为没有丢失。`/opt/rag-s8/current` 继续提供原有效Compose文件，不把该宿主机旧release目录冒称新运行源码；新源码身份由实际容器 `/app` 的508文件哈希与OCI label证明。

| 身份 | 值 |
|---|---|
| 旧镜像标签 | `rag-s8-api:50f61d1e5494951fdc96f9a9845ff7f43633a7f4` |
| 旧Image ID | `sha256:8e31042a532fc80be6f5595407fcaf30da37147a88edb1d998a773bda3a2ffa8` |
| 新镜像标签/OCI revision | `rag-s8-api:965776bd7672fa37839306c6c47537c5414775cd` / 同一完整Git SHA |
| 新Image ID | `sha256:25112dce0254955bd2dde71a6f057b97be0b47ac8a9a64177ee035df0d3602de` |
| 平台 / Python | `linux/amd64` / `3.11.16`，与旧ECS一致 |
| 镜像归档 | `rag-s8-api-965776bd7672fa37839306c6c47537c5414775cd-linux-amd64.tar.gz`，144,406,574字节 |
| 归档SHA256（不是Image ID） | `bde72cb447cb760ac8511a682d933753e84f5fe554c35766cb2e4cec3c7ca36b` |

原Dockerfile构建exit0，但安装解析使传递依赖certifi从旧2026.10.18浮动到2026.10.19；该镜像**未部署**，另存 `-dependency-drift` 标签。用一次构建层 `RUN pip install --no-cache-dir --no-deps certifi==2026.10.18` 恢复旧依赖，最终所有包版本与旧ECS完全一致，未改仓库requirements。Linux镜像使用 `--network none --read-only` 导入正式模块并核对全部508文件，exit0；密钥/本地账本/数据库导出等可疑打包路径0。构建清单与两次结果均保留，未抹去首次漂移。

## B. 服务、配置与切换

ECS Ubuntu24.04.5、2CPU、3,714,244KiB RAM；Docker29.1.3。正式身份：PG `weknora_s8` / system_identifier `7698420805889187877`；Redis DB8 / prefix `weknora-python-s8:runtime` / run_id `268d42dec48542084b1478eea849ecfb7d3f9b83`；tenant332及已验证subject哈希不变。API仍为 `127.0.0.1:18800`，来源存储仍为 `/opt/rag-s8/storage`。

| 服务 | 操作与结果 |
|---|---|
| API | 更新至新镜像；容器 `3941ca3f26f9f05c9096d28b738eccb843e9480000e14ddf4ebae4efdfbe4504`，运行、重启0、OOM=false |
| Worker | 发布前已运行；确认无在途Provider、图片任务、记忆任务后单独更新；容器 `4ebf1c8d2b7aa47766e979de8ccf5df34b750e72d8df282a5c996b6b784a7523`，运行、重启0、OOM=false |
| PG / Redis | 容器ID、镜像、运行状态及资源身份不变，未重建或重启 |
| Frontend | 预检时该Compose前端容器不存在；本轮未创建、启动或修改前端 |

API/Worker各1GiB、既有禁用CPU CFS配额的兼容覆盖均保留。全局cloud=false、Graph=false、Top1无下限=false、默认source_evidence_mode=off。其他端口、网络、卷、密钥挂载、配置不变。旧授权文件SHA与原件相同，旧父执行状态仍STOP，无S8.3临时API在运行；父记录中的16次调用是此前本机3题的共享统计，不是本轮ECS新增调用。

仅原子替换 `/opt/rag-s8/config/loaded-images.json`。旧SHA `73bdede768b735506aac083c53176cd464415916882050712a0220669f58fe4e`，新SHA `07f3070d3b5dcb36cf2f9c04302234202ce4cbcec737f1f07731642d9ad27821`。暂存有效Compose与原配置相比，仅API/Worker镜像引用及上述两处等价源码挂载变化。Compose主文件与runtime.env旧字节未改。

## C. 验证及实际命令

**复用，不重跑：**同源码S8.3的383项定向测试exit0；此前本机Linux3题真实API全部完成，16次Provider /10,888实际Token /UNKNOWN0。它们不是D8新增验证，也不是ECS两臂质量回执。D8只做以下发布必需检查。未实现的命令不得报告通过。

本机工作目录 `E:/RAG-Worktrees/s8-six-dataset-evaluation`；收据目录 `E:/RAG quention/var/reports/d8-ecs-incremental-update-20261020/`。SSH脚本全部通过既有固定主机密钥连接执行，无模型请求；每条脚本SHA、实际命令、退出码和输出SHA在results JSON中。

| 实际命令/脚本 | 退出码与结果 |
|---|---|
| `git status --short`、`git log -5 --oneline`、`git rev-parse HEAD`、`git diff --check` | 0；源码HEAD965776，起点干净 |
| `docker build --platform linux/amd64 --pull=false --label org.opencontainers.image.revision=965776bd7672fa37839306c6c47537c5414775cd --tag rag-s8-api:965776bd7672fa37839306c6c47537c5414775cd --file deploy/s8/Dockerfile.api .` | 0，70.363秒；依赖漂移镜像被阻止部署 |
| 同版本标签、`--file .../Dockerfile.api.preserve-dependencies` 的修正层构建 | 0，5.662秒；仅恢复旧certifi |
| `docker run --rm -i --network none --read-only --memory 512m --env WEKNORA_PORT_CLOUD_ALLOWED=false --entrypoint python <新镜像> -B -`（`verify_image.py`） | 0；导入/508文件/包版本通过 |
| `docker save <新镜像>`（gzip） / SCP / `remote_load_image.py` | 各0，5.873 /31.940 /6.680秒；归档SHA、平台、Image ID及源码验证一致 |
| `remote_preflight.py`、`remote_stage_config.py`、`remote_idle_and_redis.py` | 各0；身份、仅应用差异、空闲保护通过 |
| `remote_data_before.py` | **2 / REVIEW_REQUIRED**；旧物理总数参考落后于已完成来源对齐，原失败保留，见下一节对账 |
| `remote_apply_update.py` | 0；有效配置检查及API、Worker各自 `up` 均0 |
| 容器内 `python -B -m scripts.s8_ecs_runtime check` | 0；API切换后正式Ready入口通过 |
| 新API容器 `python -B /tmp/d8-readonly_source_probe.py run --source-root /app --input /tmp/d8-readonly-source-frozen-input.json` | 0；1条只读PG连接/37个SELECT/写入0/Provider0 |
| `remote_final_health.py`、`remote_data_after.py`、`remote_rollback_materials.py` | 各0；身份、HTTP、数据/账本、回滚材料通过 |
| `check_protected_worktrees.py` | 0；原16个未提交路径/20文件逐SHA不变 |
| 最终 `git diff --check`、`git diff --cached --check` | 0；本地提交收据另保存完整交付SHA |

实际切换使用以下固定主配置，每次只更新一个服务，未执行down、prune、remove-orphans或强制重建全服务：

```bash
docker compose -p rag-s8 --env-file /opt/rag-s8/config/runtime.env   -f /opt/rag-s8/current/deploy/s8/compose.yml   -f /opt/rag-s8/config/loaded-images.json config --quiet
docker compose -p rag-s8 --env-file /opt/rag-s8/config/runtime.env   -f /opt/rag-s8/current/deploy/s8/compose.yml   -f /opt/rag-s8/config/loaded-images.json   up -d --no-deps --pull never --no-build api
# 正式Ready及空闲再次通过后，单独执行同样命令的 worker 目标。
```

授权 `/healthz` 200、`/readyz` 200，匿名Ready401。正式模型绑定仍为BGE-M3、BGE Rerank、Qwen3.5-4B Cheap、DeepSeek-flash Expensive；本轮未调用它们。实际模块从 `/app` 加载，旧覆盖不再遮蔽新代码。来源HTTP和Chunk HTTP均200，目标84字符原文的document/version/offset/SHA与源文件一致。

## D. 数据、索引和历史保护

| 范围 | 更新前 → 更新后 |
|---|---:|
| 原冻结准备池物理资料 | 11,683 → 11,683 |
| 原冻结准备池向量（1024维） | 27,881 → 27,881 |
| 全库物理文档 | 11,736 → 11,736 |
| 全库物理Chunk /向量 | 28,657 → 28,657 |
| Provider记录 | 12,920 → 12,920 |
| 历史UNKNOWN | 2 → 2，完整行哈希集合相同 |

历史参考11,708/28,000是在上一次S8来源对齐前：当时已新增28份文档、657向量，因此11,708+28=11,736，28,000+657=28,657。52个旧版本及474条旧向量此前被停用，物理数据保留；其中27份/355条属于原11,683池（MML3份、Omni24页），所以该历史池当前ready为11,656，不把“物理仍在”冒称“旧版仍全部启用”。六套准备语料跨7个物理KB。D8未再次迁移/新增/停用这些资料。

首次只读探针因旧参考值返回REVIEW_REQUIRED，未覆盖回执；本地对账复用已完成的来源对齐ID/版本证据。最终用发布前实际快照逐项对比，14项保护检查全通过：物理/准备池/KB/迁移/扩展/索引/Provider/UNKNOWN等不变。PG17.9、vector0.8.1、pg_search0.22.6；HNSW1024与BM25索引仍有效。旧UNKNOWN用量仍为UNKNOWN，不按0入账。一个更早OOM遗留pending turn未改终态；其Provider均已结束，无活动generation任务，不是本次在途请求。

## E. 新镜像中的S8.3只读功能检查

新API实际镜像使用正式ScopedKnowledgeRepository、IsolationPolicy和可信tenant332，对保存的MIRACL91294候选及旧BGE分数进行Merge→TopK→来源回取→Context→生成请求体构造。事务read-only /repeatable-read，未运行新查询理解、Embedding、Search/Rerank或模型生成。冻结输入SHA `2e79e1a6525ef2a050a14c00b56f7650878597570e062e2928d4b9f2d9909d5b`。

| 只读重建臂 | 最终块数 | 目标原文进入构造请求 | Summary |
|---|---:|---|---|
| A /off | 7 | 否 | 仍作为事实正文 |
| B /bounded_source_v1 | 3 | 是 | 仅定位，不进入事实正文 |

目标 `3f6a7679-4adf-4f93-9c2f-5d1bfd649478`，文档 `541b3a8a-70a0-48dc-b31a-19ee96df3b94`，版本 `73301c48-abc1-4f05-9a30-24dccb10f41e`，偏移[0,84)，源文件/文本SHA `f59e42e6916ab57d0b2e15cd6b61e4169552442724cc052ffaab35ba534e4b8c`。B的另外2块也通过当前数据库行与原文件切片证明。这里只证明投影及本地wire构造，**未发送Provider，不是REAL_QA_PASS**。

仍省略的4个投影：

- `f6632136-5414-494b-8136-1f8f4f97b712`
- `74ea19e6-ca34-4f5f-8b76-a2be3a41c893`
- `50eeab77-2be7-4a59-a943-b423558f6192`
- `bc01acf9-abc1-482c-b2b8-e1fe3620df2c`

其持久化anchor确有104/80/28/100字符及正确原文件切片；但是所选投影被标记 `content_rewritten=true`、没有sub_chunk_ids，既有3组预算已用于其他投影，未完成该选中投影的来源物化/关系证明，真实trace为 `INSUFFICIENT_SOURCE / UNMATERIALIZED_SELECTED_PROJECTION`。不能据此说原文件不存在，也不能把它们认定无关或声称遗漏事实已解决。未加预算或放宽安全条件。具体版本/偏移/各臂ID、证明、wire SHA保存在results JSON。

## F. 用量、时间、内存与日志

D8新增真实调用、输入/输出Token、新UNKNOWN均为0。API/Worker启动以来日志读取未返回内容，未发现Traceback/启动失败/OOM标记；这不是持续日志覆盖承诺。重启次数均0，OOMKilled=false。

检查后单次Docker采样：API128MiB /1GiB、CPU98.86%；Worker80.98MiB /1GiB、CPU0.45%；宿主MemAvailable2,577,648KiB。API采样发生在只读检查附近，不能作为稳定空闲CPU或QA峰值。未启动新的长期采样；QA内存峰值、TTFT、真实模型延迟均 **NOT_RUN**。

来源投影37个SELECT；SSH整条检查2.398秒包含装配、回读和本地请求构造，不能当成SQL耗时。补充只读 `SELECT 1` 的Docker exec＋psql总墙钟65.254ms，SQL执行时间没有单独测量。不估算模型Token节省或现金成本。

## G. 回滚入口与材料

**回滚未触发（NOT_TRIGGERED）**。旧镜像仍在ECS，旧覆盖文件仍在。原Compose、loaded-images、runtime.env字节保存在 `/opt/rag-s8/state/d8-incremental-20261020/rollback/`，SHA与发布前一致；旧配置的 `docker compose ... config --quiet` exit0。应用回滚不依赖数据库恢复。

只有再次确认身份正确、无在途发送/任务、Compose主文件和runtime.env未变化后，才能按下列旧配置恢复。命令本轮仅冻结，**未实际执行回滚**：

```bash
cd /opt/rag-s8/current
# 先校验旧镜像、回滚文件SHA、任务空闲和当前配置身份。
install -m 600 /opt/rag-s8/state/d8-incremental-20261020/rollback/loaded-images.json   /opt/rag-s8/config/loaded-images.json.rollback-tmp
mv /opt/rag-s8/config/loaded-images.json.rollback-tmp /opt/rag-s8/config/loaded-images.json
docker compose -p rag-s8 --env-file /opt/rag-s8/config/runtime.env   -f /opt/rag-s8/current/deploy/s8/compose.yml   -f /opt/rag-s8/config/loaded-images.json config --quiet
docker compose -p rag-s8 --env-file /opt/rag-s8/config/runtime.env   -f /opt/rag-s8/current/deploy/s8/compose.yml   -f /opt/rag-s8/config/loaded-images.json up -d --no-deps --pull never --no-build api
docker exec rag-s8-api-1 python -B -m scripts.s8_ecs_runtime check
# 本次Worker发布前确已运行；确认空闲后恢复它，原本停止的Worker不得借此启动。
docker compose -p rag-s8 --env-file /opt/rag-s8/config/runtime.env   -f /opt/rag-s8/current/deploy/s8/compose.yml   -f /opt/rag-s8/config/loaded-images.json up -d --no-deps --pull never --no-build worker
# 再核对旧Image ID、实际源码、Worker running、来源/PG/Redis/账本；不做DB还原。
```

既有恢复材料 `/opt/rag-s8/backups/weknora_s8-pre-s8-1-20261020T004041Z.dump` 仍在、145,042,978字节、0600。既有内容SHA `5ac5ed126cc66dc5b1b28e122c20d9c38214226835d934a4f8c94d6b731e93e5` 与当时归档读取证明复用；本轮只检查文件存在/大小/权限，未重复备份、重新hash或做数据库恢复。未删任何镜像、卷、历史资料或用户文件。

## H. 停止与后续待授权计划

D8工程交付完成。原主工作树16个既有未提交路径/20个文件SHA及状态不变；S8评测工作树只新增本次交付文件。无Push、合并、Tag、后续问答或S8.4。

`S8_3_R_PLAN_v2.json` 只是一份 **PLAN_ONLY_NOT_AUTHORIZED** 的后续申请：MIRACL91294、Sci1101、Sci241、Frost第二跳负控制、Leyden成功控制、无证据邮编，共12个正常提交（每题每臂一次），分两批。两臂只比较off /bounded，Graph=false、Top1无下限=false。原计划Top1=true，因此必须用新版本计划，不能冒称原配置。建议60次Provider/200,000Token仅是待批准上界，旧144次/1,200,000Token许可不复用。

另有一个明确配置缺口：正式HTTP `SessionCreate` 只支持范围/标题，没有每会话 `source_evidence_mode` 设置入口。现有 `WEKNORA_PORT_SOURCE_EVIDENCE_MODE` 可在进程启动时选择模式，但不是同一服务内每会话A/B开关。后续须先决定合法配置方式；本轮未为此改正式接口、写SQL或启用临时绕门禁runtime_factory。**S8.3真实质量改善仍NOT_RUN，不推广bounded默认。**
