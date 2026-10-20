# S8 六套数据集核验与 ECS 差距清单

## 当前结论

**S8.0：`BASELINE_READY`（只读核验完成，缺口已列明）。S8 整体：`PARTIAL`。**

按 Owner 最新决定，Windows 仅核验资料、Gold、已有索引和历史结果；**S8.1 / S8.2 正式全链路测评目标为 ECS**。本轮新增问答、Provider、Token、上传、解析、Embedding、数据库业务写入和部署均为 **0**。旧 UNKNOWN 不释放、不改写、不重发。

- 独立工作树：`E:/RAG-Worktrees/s8-six-dataset-evaluation`。
- 分支：`codex/s8-six-dataset-evaluation-20261019`。
- Graph 基线：`b9268e2d6707f73c48b5624a96e39879d02ce12c`。
- Top1 基线：`4145448003bc008acfa2341ea28c5bd00bb041f7`。
- 离线准备实现提交：`cc4721bab31b12e9d6ee71dfd60dca6b78ab4298`。
- 报告提交完整 SHA：`git log -1 --format=%H -- docs/evaluation/S8_SIX_DATASET_EVALUATION.md`；最终交付同时列出。没有 Push、合并或 Tag。

机器结果：[S8_SIX_DATASET_EVALUATION.results.json](S8_SIX_DATASET_EVALUATION.results.json)。完整逐项合同和只读证据见 [s8-preflight-20261019.zip](evidence/s8-preflight-20261019.zip)，包内含 SHA 清单及实际命令记录。

## 1. 本机六套资料：哪些可以复用

40题的 q0、qrels、答案与冻结 Gold 逐项一致，全部必要来源可以映射到当前 tenant332 的文档、版本、SHA 和真实 Chunk。**来源存在不等于所需事实已经被检索并送达模型。** 本轮没有产生新答案质量成绩。

| 数据集 | 原冻结范围 | 当前可复用资料/索引 | 有效评分口径及缺口 |
|---|---:|---|---|
| SciFact | 8题 | 当前11篇来源，33条非空1024维索引；原件/SHA/原文偏移有效 | 文档 qrels、Recall/MRR；本地 BEIR 导出没有生成答案或 SUPPORT/REFUTE Gold，不能计算官方问答/立场准确率 |
| MIRACL-ZH | 8题 | 当前13篇来源，26条非空1024维索引；正相关 qrels 均可映射 | 在明确的候选子语料范围计算检索指标；grade0不是正例；无生成答案 Gold，不能称官方QA成绩 |
| LongBench-ZH | 8题 | 当前8份上下文，281条非空1024维索引 | DuReader 与 multifieldqa_zh 分别使用对应 ROUGE-L / 中文F1；答案事实支持另评，配对文档命中不是精确事实召回 |
| HotpotQA | 8题 | 当前24篇文档/59条向量；8题全部16个必要来源身份完整 | 答案 EM/F1、至少一个/全部必要来源、支持句事实分开；准备包73来源中49份背景/干扰资料未入当前库，不是完整73文档实验 |
| MMLongBench-Doc | 8题 | 所需3份原始PDF均存在；2个KB共4篇当前范围文档/274条向量，含额外背景文档 | 官方 typed answer 与页/图表/事实支持分开；项目字符串评分不能冒充官方GPT判分；视觉事实是否完整仍待逐题链路观察 |
| OmniDocBench | 24页 | 原图和解析Gold 24/24 SHA有效；已保存24份本地 MinerU 4.0.12 解析回执 | 3页有已验证的项目整页文字 NED 映射，21页明确 `NOT_SCORABLE`；不能把3页口径冒充官方布局/表格/公式指标，无官方QA Gold |

这是项目冻结子集，不是六套公开基准全集。本机准备语料中 SciFact有5183篇、MIRACL候选池有6000篇、LongBench有400份上下文；当前对应KB仅11/13/8篇。其余问题及缺失来源已在本地清单登记，本轮不导入全部11,583篇，也不将未入库的背景语料暗算进召回率分母。

### Omni 的关键限制

24份 MinerU 回执均记录 `database_writes=0`，可以复用为**历史解析证据**，不能视为已入当前 RAG 索引。

- 当前21页 `image_ocr` 正文与历史 Tesseract 产物 SHA 完全一致。
- 另外3页只有图片 Markdown 占位内容，虽有向量，也没有对应 OCR 正文。
- 保存的 MinerU 派生文本有815/815个有效 offset/source-hash 定位，其中387个另有 bbox；其余428个不能声称有可靠 bbox。定位有效不等于识别语义正确。
- 部分 sidecar 只有 logical manifest，不能写成实际文件已回读或已迁移。
- 本轮未重新 OCR、未切换历史 Chunk、未重算向量。后续可复用解析结果做解析评测；若要求“当前 MinerU 全部24页 RAG”，须先单独决定如何补齐新索引，不能偷偷计为已通过。

### Pew 三题原件完整

`0684 / 0685 / 0687` 均绑定当前文档 `35c37b2d-3ce7-49ab-98fe-de6d3e6974f9`，版本 `8358d755-b476-4986-bcce-fe3c979531a0`，文件 SHA `8f49f93f1665fd5ff604e66ffed9649d8b18ce7f114ab43463aeac46cd3d00cb`。不存在本轮已确认的“Pew原件缺失”；图表数字或 median/average 的事实质量是另一层问题。

## 2. 额外10题与工程反例

按“已有资料齐全才纳入”的指令，额外10道 **SciFact** 可进入后续待授权清单，无须增加文档或 Embedding：

| split | QID | 处理 |
|---|---|---|
| development | 1025、1101、1302、1305、241、944、949 | 7题问题/qrels/当前来源及向量齐全 |
| locked_holdout | 1303、338、921 | 3题只作评测，禁止用于 S8.3 调参 |

涉及8个唯一来源/24条向量，逐行索引关联复核有效。有效Gold仍只是文档qrels，不新增生成准确率。

工程邮编反例沿用 S7 的原问题和不足判据，可以作为**当前索引文本不足诊断**，不算官方第51题。S7 是 tenant1 的另一KB；当前计划是 tenant332 两PDF/88块，不能复用旧分数/答案作为当前成绩。未审全部原图像素，也未新调用BGE证明其会触发 `<0.15`；这两项保持 `UNVERIFIED`。

**完整分母保留：40原题＋10额外正式题＋1单列工程诊断＋24解析页。** 两个受历史 UNKNOWN 保护的 QID：`scifact-339`、`hotpotqa-5ae3992d5542994393b9e722`，不自动重发。因此原40题中仅38题可作为后续新提交候选；加额外10题为48道正式新提交候选，不是已执行48题。

## 3. 索引、身份与历史结果

本机只读事务复核7个相关KB，共84篇当前范围文档、**739条索引：text638 / summary80 / image_ocr21**。

- 缺失、孤立、重复、tenant/KB/doc错配、原文版本错配、启用错配、NULL向量、真实维度不符、索引正文丢失 Chunk 本文：均0。
- 739条向量实际均为1024维；版本通过真实 Chunk ID 回查当前文档。索引表没有独立 source_version，不能额外声称向量生成输入的历史证明已经完备。
- 正式 `keyword_statement` / ParadeDB 每KB一次只读烟测，7/7返回命中；这不是逐题语义召回率。
- 实际 BM25 索引有效。现有唯一 HNSW 是 **dimension=3** 的部分索引，**不是1024维ANN索引**。当前正式距离 SQL 可精确排序，不因缺ANN变成“无向量”，但其性能仍待 ECS 验证。本轮不做DDL。
- Summary 已真实入历史索引，不能虚报 Child-only。Summary缺原文版本字段按派生内容记录，不补造证明。

可信主体为 tenant332、subject SHA `00f2fd3dad2b741924376adab7fd29c12a93bc7a1fe4e8e1f0f97cacd418fa83`。本机 PG physical ID `7696088403504697382`，数据库 `weknora_python_port_20261013`，Redis DB15。7个相关KB的 `cloud_allowed=true`；本次全局外发仍关闭且没有新执行授权，不能仅凭KB许可发送。tenant1 的 principal、角色、会话和日常配置保持不变。

源端实际版本为 **PostgreSQL17.9 / vector0.8.1 / pg_search0.22.6 / uuid-ossp1.1**，halfvec存在；当前整库约433,944,243字节。该大小包含范围外数据，不能据此把整库含tenant1一并迁移。目标需满足当前查询所需扩展，不必为迁移额外安装源库所有无关扩展。

历史 M4 有38条完成运行、1条UNKNOWN、1条受旧UNKNOWN保护未运行；原执行器的 `FAILED` 和 SSE/PG完成状态的更正口径只用于分析，未改原回执。历史使用的 Xing/DeepSeek 不能当成当前Qwen成绩。新S8各题全部标记 `NOT_RUN`，不把历史调用量加到本轮。

本机严格配置读取及已有Qwen候选解密通过，不代表已向供应商验证账户。tenant332数据库 Cheap 仍为 Xing；仅离线准备/只读测试服务器内存显式使用该身份已有 Qwen3.5-4B 候选作为 QU/Cheap，数据库角色未写入。ECS正式模型必须在迁移后重新核验。

## 4. ECS 当前实际状态（不是旧报告推断）

通过既有 SSH、严格 known_hosts 和 IMDS，确认既有实例 `i-bp1flliql08bz8yftild`、`cn-hangzhou`。只读实测时间 **2026-10-19 15:20–15:23 UTC**；没有更换主机、部署、安装或开启外发。

| 项目 | 实测现状 | S8复用/差距 |
|---|---|---|
| 主机 | Ubuntu24.04、Python3.12.3、2vCPU、内存约3.54GiB/可用约2.94GiB，无swap；根盘约31.89GiB可用 | 可筹备小子集；新PG/Redis/API峰值尚未实测，不承诺全量本地OCR资源 |
| 正在运行应用 | `/srv/rag-eval/app` → release `673449...`；`eval_center.server`、loopback8787、健康ok；Git `f15aaeb374e32d7955429784b4d9dd50317608a5` | 旧Evaluation Center，不是本次S8代码。已知项目路径未找到当前 `weknora_port` 运行源码/服务 |
| PG | 16.15、physical ID `7687882228252794550`，loopback5432 | 不是本机PG，必须绑定新目标身份 |
| 扩展 | vector0.6.0；`halfvec`不存在，`pg_search`不可用 | 不满足当前halfvec/ParadeDB查询合同；不能原地当作兼容运行库 |
| 当前旧库 | `rag_p7_r1`，legacy迁移 `0022_memory_vector_index`；7KB/751文档/5351块/4777条旧向量 | 只有旧 `documents/document_versions/chunk_embeddings` 等Schema；无 `port_schema_migrations` 或当前 `embeddings`，7个当前冻结KB ID均不存在 |
| 旧向量profile | SiliconFlow BGE-M3、1024维，revision `UNKNOWN` | 同模型名/同维不能证明Chunk、预处理、版本及索引输入一致，不能直接挂给当前主链 |
| Redis | 无redis-cli、无运行服务或TCP监听；旧私有socket不存在 | 需独立Redis身份/前缀，不恢复旧任务队列 |
| Provider | 无当前正式Registry与正常设置接口解密证据；只见旧凭据登记回执元数据 | `UNVERIFIED`，未读出密钥值、未做付费探针；不能把旧回执当成模型可用 |
| 六套原始包 | 6/6完整SHA与本机冻结包一致 | **直接复用，禁止重复上传** |
| 40题公开问题/Gold | 24题核心cohort＋8Hotpot＋8MML均存在；q0、版本、qrels、answers逐项 **40/40一致** | 可复用，不重新下载/生成Gold |
| Omni Gold | 原1651页标注文件SHA `a45cd84b...`；选定24页逐页 **24/24 SHA一致** | 原件与Gold都可复用；有效评分映射仍只有本机已核实的3页 |

扫描限于已记录项目根、有限深度/文件数及本机PG目录。未对其他机器、外置RDS、所有挂载和未知目录作无界扫描；因此“不存在当前服务/资料”的结论严格限于已验证目标与范围。

## 5. 最小迁移方案——本轮仅提交计划

1. **先固定目标身份和备份边界。** 另建当前端口实现的隔离DB/目录/服务；保留旧 `rag_p7_r1`、`study_platform`、Evaluation Center及历史账本。不覆盖或DROP。
2. **补齐兼容运行依赖。** 优先准备与源端 PostgreSQL17.9 / vector0.8.1 / pg_search0.22.6 查询合同兼容的隔离实例、Redis和当前Python依赖，不能把PG17逻辑转储未经验证直接恢复到旧PG16。ECS无Docker；采用原生安装还是隔离容器需在执行准备前确认，不在此次只读阶段安装。
3. **复用已有云端原件。** 六包已经相同，不重传、不重新解析。按显式白名单复用现有对象，补传仅本轮确实缺少的清单、当前代码、已解析资产/sidecar和评测结果，逐文件核SHA。
4. **搬运当前已验证数据与向量。** 仅导出获准 tenant332 的主体、7KB范围、文档/Chunk/版本/邻接、739条实际向量及必要FK闭包；C另搬已有19份合法Graph贡献。不调用Embedding或重新建图。`pg_dump --table`不能按tenant筛行，不能直接把整表/tenant1搬走；需先审查显式范围导出。
5. **只映射目标物理路径。** 当前 `knowledges.file_path` 是Windows绝对路径，asset path也可能为反斜杠；新Linux存储根不能直接读它们。对目标副本建立物理位置映射，保留doc/version/Chunk ID、正文、SHA、逻辑来源和offset；按新PG真实身份创建合法storage marker，不能关闭原保护。
6. **安全配置现有模型。** 密文迁移需要安全提供原cipher密钥或通过正式设置重新登记；不把密钥装入源码、数据包或普通日志。保持可信tenant332/subject，Qwen隔离绑定和角色差异显式记录；tenant1日常环境不变。
7. **保留终态与检查点。** 保留相应Provider/执行账本、UNKNOWN、已完成历史和S8检查点；不把CLOSED/STOP授权改OPEN。Redis用新前缀，不复制旧Worker租约/ready队列，不启动旧未决任务。
8. **零云验收后才申请真实评测。** 在ECS核对扩展、7KB/版本/739向量、来源/资产回读、模型设置解密、跨租户拒绝、A/B/C互斥和checkpoint恢复，重新冻结远端物理身份与代码指纹；取得新调用授权后执行S8.1，再逐批S8.2。不能先在Windows把题做完。

逻辑恢复可能需要恢复数据库的BM25/HNSW索引定义，这属于数据库恢复，区别于重新生成Embedding；本轮两者都 **NOT RUN**。旧 `backup.ps1/restore.ps1/native_release.py` 仍针对legacy表/迁移，不能直接用于当前端口库；只可复用其空目标、定向备份、事务恢复和SHA清单原则。

## 6. 离线准备实现与实际验证

S8独立评测树仅增加默认关闭的Top1实验开关及其传递，Graph与Top1同时开启会拒绝；A仍0.3/0.15，B才无下限。公共非法Rerank index/NaN/Inf/重复index校验是单独的安全差异；6份合法历史回执的A结果与S7原A **6/6一致**，没有用旧分数给新Passage评分。没有改生产默认、模型角色、Prompt、MMR、Merge或路由。

控制器只走正式API；Gold不进POST；UUID36、发送前checkpoint、恢复只GET、全阶段父授权及单文件限制、真实身份/模型快照均有定向测试。启动器不创建授权、不启动Worker；云启动需新批准的单case身份。它们是离线准备，**不是已经在ECS验收的运行器**。

本轮实现文件：`backend/weknora_port/{development.py,runtime.py,retrieval/pipeline.py,retrieval/rerank.py}`；工具为 `scripts/s8_{evaluation_controller,text_contracts,multimodal_contracts,trusted_api}.py`，对应5个 `tests/weknora_port/test_s8_*.py`。其余仅本报告、results及显式白名单证据包。代码准备发生在此前获准的S8隔离集成范围，后续“只做S8.0/ECS差距”决定后没有部署或扩写业务主链。

| 实际执行命令（工作目录为S8树） | 退出码/结果 |
|---|---|
| `& 'E:/RAG quention/.venv/Scripts/python.exe' scripts/s8_multimodal_contracts.py --output 'E:/WK/run/s8-20261019/multimodal-contracts.json'` | 0，40题/24页来源与Gold合同产物 |
| `& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/s8-20261019/index_integrity.py` | 0，显式只读事务；739行核对/7次BM25读探针 |
| `& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/s8-20261019/source_db_versions.py` | 0，源端PG/扩展/库大小实读，read_only=on |
| `& 'E:/RAG quention/.venv/Scripts/python.exe' E:/WK/run/s8-20261019/readonly_api_check.py` | 0，A/B/C正式只读API；缺/错Bearer401、tenant1KB404、正确332清单与模型可读；POST0/Provider0；三服务已停止 |
| `& 'E:/RAG quention/.venv/Scripts/python.exe' -m pytest tests/weknora_port/test_s8_experiment_arms.py tests/weknora_port/test_s8_evaluation_controller.py tests/weknora_port/test_s8_trusted_api.py tests/weknora_port/test_s8_text_contracts.py tests/weknora_port/test_s8_multimodal_contracts.py tests/weknora_port/test_pipeline.py tests/weknora_port/test_development_entry.py tests/weknora_port/test_graph_retrieval.py tests/weknora_port/test_conversation.py -q` | 0，**221 passed in12.13s**；这是离线/合成依赖定向回归，不是新真实问答 |
| `& 'E:/RAG quention/.venv/Scripts/python.exe' scripts/s8_evaluation_controller.py prepare --plan 'E:/WK/run/s8-20261019/evaluation-plan.json' --output 'E:/WK/run/s8-20261019/controller-readiness.final.json'` | 0，`S8_OFFLINE_PREPARED Provider0 POST0` |
| 既有SSH参数＋`python3 -`，stdin分别为 `ecs_readonly_probe.py`、`ecs_readonly_supplement.py`、`ecs_gold_profile_probe.py` | 三次均0；脚本及不含密钥的命令包装在证据包；SQL显式READ ONLY，只读健康/IMDS/文件SHA，不上传脚本到磁盘 |
| `& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/s8-20261019/finalize_s80.py` | 0；6包、40题Gold、24页Gold、18保护文件核对 |
| `git diff --check` | 0；最终提交前再核对 |
| 真实问答、迁移、安装/部署、新索引/Embedding、Graph建图 | **NOT RUN** |

使用已有Python环境；`PYTHONPATH=E:/WK/deps/reader;E:/RAG-Worktrees/weknora-python-port-20261013/var/weknora-port/runtime-deps`，`PYTHONIOENCODING=utf-8`，pytest关闭自动插件加载。早期盘点曾有两次只读SQL工具错误（不存在的embedding tenant列、百分号参数绑定），随后在工具中修正；不算产品Bug或模型失败，没有外发/写库。

## 7. 保护、推广结论与停止

- 主工作树 HEAD仍为 `855e2d32bf85f50fbb9fa12ace9b0a51e1adbf67`；原18个未提交路径逐字SHA未变。S7 Graph/Top1参考工作树均干净、HEAD不变。
- 本机Provider总记录704→704，历史UNKNOWN8→8；principal、模型配置、会话计数及日常配置前后相同。未知历史用量没有记成0。本轮新调用0，因此本轮新Token0是实测“没有发送”。
- B：`KEEP_EXPERIMENTAL`；C：`KEEP_EXPERIMENTAL`。S8.0没有新增答案证据，不具备推广Top1或宣布Graph稳定增益的依据；D继续禁止。
- 优先顺序：①ECS当前端口运行库/身份与依赖；②保留现有向量和版本的定向迁入及来源回读；③远端零云验收后申请S8.1，再逐批S8.2。Omni新MinerU索引、官方评分映射与完整语料缺口单列，不静默抹掉。

**本次在S8.0收口。ECS准备/迁移及真实测评尚未执行，不复活旧授权，不自动进入S8.1。**

## 2026-10-20 夜间接管进度（后续新授权，非 S8.0 历史结果）

Owner 后续批准 ECS 部署、限定迁移及缺失689份资料的 BGE-M3 入库：最多745次请求、5,200,000 Token；未授权真实问答。部署代码为 `50f61d1e5494951fdc96f9a9845ff7f43633a7f4`。前端和完整部署文档按最新指令留到明天。

- 已迁入 tenant332 的7KB/84文档/739条真实1024向量及288资产，原Provider500条含2UNKNOWN保留。原PG16、Evaluation Center、本机tenant1未改；新PG17/Redis/API/Worker独立且仅回环监听。
- 首次后台启动遇到数据包目录缺少容器挂载，在发送前失败，Provider0。保留原现场并证明对应文档尚未建立、全部新账本无entry后，审计恢复同一授权；未修改UNKNOWN或补发已发送操作。
- 远端 `rag-s8-ingestion-20261020.service` 于00:55:25 CST启动；关闭启动SSH后，两次全新SSH仍见同一PID868620及进度增加。00:56:58快照：188新增已索引、36复用，244次调用全部结算、430,530实际Token，新增UNKNOWN0；Hotpot49份、Omni24页、LongBench40份新增已完成，MIRACL正在运行。此为运行中快照，不是全批完成成绩。
- 所有镜像、源码、六ZIP、密钥、授权、MinerU回执和检查点都在ECS，任务不依赖Windows。持久化状态命令：`python3 /opt/rag-s8/state/status.py`；详细检查点 `/opt/rag-s8/state/checkpoints/ingestion-20261020/checkpoint.json`。正常结束撤回剩余额度，新UNKNOWN保留占用并停止发送。
- 实测迁移/身份/索引/新SSH接管命令均退出0。实现与定向验证详见当前规划进度；完整构建和首次错误日志保留在 `E:/WK/run/s8-overnight-20261020` 及远端 `/opt/rag-s8`。机器快照见 `evidence/s8-overnight-20261020/handoff-live-proof.json`。当前不宣称S8全量问答完成。


## 2026-10-20 全语料补齐终态（Owner后续澄清，非问答评测）

**当前状态：BASELINE_READY / INDEX_PREPARATION_COMPLETE。** 保留已经达标的748源，仅新增其余10935源；六套prepared池合计11683，不是所有上游官方语料。新任务2026-10-20 03:58:02 CST退出0，全部INDEXED；11332次请求全部SETTLED、实际5934546 Token，新UNKNOWN0。两轮累计12077次／6491636 Token。两个父及全部子账本CLOSED，完成单次服务已disabled，不重开授权或重发。

最终物理库11708文档／28000非空1024向量，其中当前11683源覆盖27881向量；额外25历史文档／119向量保留且不混入评测分母。全部新增10935原件SHA、版本、KB、Child／向量关联校验通过；6套实际BM25／HNSW／Hybrid-RRF／Merge／Context／来源GET探针完成，无新模型请求。实际HNSW EXPLAIN证明使用1024索引，不只依据DDL。

报告：[S8_ECS_OVERNIGHT_DEPLOYMENT.md](S8_ECS_OVERNIGHT_DEPLOYMENT.md)；机器结果同名results.json；逐源证明在`evidence/s8-ecs-full-corpus-20261020/`压缩包及ECS检查点。原S8.0机器结果逐字语义保留于v2结果的historical_S8_0_snapshot，不把旧739向量盘点数冒充当前全量。

API/Worker/PG/Redis在ECS继续运行，cloud关闭、Graph关闭、原Top1／DYNAMIC／模型角色未改，旧PG16／Evaluation Center及原18未提交路径保留。Windows关机不影响数据或服务。前端由Owner延期，本Goal未部署；S8.1／S8.2／S8.3及真实问答仍NOT_RUN，需另行授权。不能把索引准备完成写成六套问答质量完成。
