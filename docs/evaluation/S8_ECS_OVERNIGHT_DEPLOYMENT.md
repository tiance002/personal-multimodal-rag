# S8 ECS 全语料入库与索引交付

## 结论

**INDEX_PREPARATION_COMPLETE / REMOTE_BACKGROUND_VERIFIED**。六套已准备语料共11,683份，保留已完成748份，新补齐10,935份及25,054个Child向量。未执行S8正式问答，不宣称回答质量或完整官方语料评测通过。

第二轮于2026-10-20 **01:42:03—03:58:02（北京时间）**独立运行，systemd退出0。11,332次BGE请求全部SETTLED，实际5,934,546 Token；两轮累计12,077次、6,491,636 Token。新增UNKNOWN0，历史UNKNOWN未清零。两轮父/子授权全部CLOSED，第二轮未用64,065,454 Token空间撤回，完成后的两个一次性服务已disable，API/Worker/PG/Redis保持运行。

## 逐套范围（第二轮新增列，不能与全库物理历史行混用）

| 数据集 | 准备源总数 | 保留748中的份数 | 第二轮新增 | 第二轮Child/向量 | 第二轮调用 | 第二轮实际Token |
|---|---:|---:|---:|---:|---:|---:|
| scifact | 5183 | 200 | 4983 | 9936 | 4983 | 2051584 |
| miracl-zh | 6000 | 400 | 5600 | 5743 | 5600 | 865305 |
| longbench-zh | 400 | 48 | 352 | 9375 | 749 | 3017657 |
| hotpotqa | 73 | 73 | 0 | 0 | 0 | 0 |
| mmlongbench-doc | 3 | 3 | 0 | 0 | 0 | 0 |
| omnidocbench | 24 | 24 | 0 | 0 | 0 | 0 |

前三套全集来自ECS既有`corpus.jsonl`，字段仅doc_id/title/text；SourceID、原记录行SHA、正文与忠实Markdown投影绑定，Gold没有进入资料。Hotpot73、MML3、Omni24全部保留首轮结果。该范围是冻结prepared pool，不是官方全部MIRACL/Hotpot等原始大语料。

### 最终范围内的Chunk与向量实查

| 数据集 | 正文text Chunk | 保留历史Summary | 非空1024向量 | parent_text |
|---|---:|---:|---:|---:|
| scifact | 10339 | 11 | 10350 | 0 |
| miracl-zh | 6151 | 13 | 6164 | 0 |
| longbench-zh | 10844 | 8 | 10852 | 0 |
| hotpotqa | 136 | 24 | 160 | 0 |
| mmlongbench-doc | 219 | 2 | 221 | 0 |
| omnidocbench | 134 | 0 | 134 | 0 |

上述11,683源范围共27,881条向量；物理数据库是11,708文档、28,000 Chunk/向量，差额为明确保留的25份历史资料（24 Omni＋1 MML）及119条历史Chunk/向量，不与当前评测范围混算。全部新增原件逐文件SHA相等；全部新增文档状态/版本/KB/Child数、向量维度/非空、孤儿/重复/禁用/邻接检查通过。六套原件和Chunk正式HTTP GET回读、无/错误Token401通过；6套真实HNSW EXPLAIN ANALYZE均用`embeddings_embedding_idx_1024`，存量自身最近邻距离0。每套分侧实查向量150＋BM25候选150，正式RRF融合Top30与实际Hybrid输出逐序一致。正式Hybrid/RRF→Merge→TopK→Context为零云基础设施探针，未执行新QU/Rerank/生成，不能当作QA通过。

## 实现与运行身份

- API/Worker镜像源码：`50f61d1e5494951fdc96f9a9845ff7f43633a7f4`；镜像ID `sha256:8e31042a532fc80be6f5595407fcaf30da37147a88edb1d998a773bda3a2ffa8`。
- 新工具提交：`161ef84632e6a669ee79e8850ce295eb57a7a2cc`。新增` s8_full_corpus_ingestion.py`仅构造真实差集输入；`s8_execution_counter.py`在预分配总额和单文档限额不变的条件下避免每文档重扫一万份账本，首尾全扫，UNKNOWN拒绝，不替代SendGate。
- 新PG17.9 / vector0.8.1 / pg_search0.22.6，DB `weknora_s8`，systemID `7698420805889187877`；Redis DB8/prefix `weknora-python-s8:runtime`。可信tenant332，客户端不能指定主体。API18800、PG15432、Redis16379仅127.0.0.1。
- host storage `/opt/rag-s8/storage` → container `/var/lib/eviroute/storage`。原tenant1、旧PG16/Evaluation Center、18条受保护未提交路径保持不变。
- 密文模型记录及必要32字节解密主密钥通过私密文件迁移；不含明文APIKey、Bearer或密码的报告/代码提交。实际BGE-M3/1024/Registry身份和结算已验证，不凭名称推断能力。
- 正式API/Worker的cloud=false，Graph=false、floorlessTop1=false。夜间仅有限Embedding runner外发，没有启用QA权限；默认Cheap仍是迁入配置，未由本任务改模型。

## 实际验证与失败记录

- 初始迁移7KB/84文档/739向量/288资产已逐SHA核验；新任务完整文档/版本/原文/向量和索引核验以同名results.json中`data_audit`为准，包含实际Probe及范围，不能用sourceID替代事实送达。
- Windows原18文件SHA全相同；源主HEAD855e2d32未变。旧748授权及账本终态与首次保存SHA一致。
- Linux离线原生ParserRegistry对全10,935输入重算与冻结计划一致（exit0，Provider0）；17项准备器/旧runner测试、23项计数器/旧runner测试、7项审批工具合成合同均exit0，各组有重叠，不相加伪造唯一测试数。既有Linux UNKNOWN/flock/symlink冒烟PASS（合成，Provider0）。
- 真实断开启动SSH后，两次新SSH仍见PID901220及进度增长；01:44:13快照337次全结算/120文档已索引，证明不依赖Windows。最后exit0且10,935均INDEXED，所有真实调用独立全扫描对账。
- 首轮目录挂载缺失失败发生于发送前：当时账本全空、对应文档不存在；保留原错误/检查点，审计修复后继续，不重发UNKNOWN。
- 最后数据审计工具首次exit137，原因是把一万条记录嵌成Python字面量造成容器内存压力；该次NOT PASS。工具改用分批/JSON输入后结果独立保存，不改变服务限额、模型请求或原数据。

### 实际执行命令与退出码

下列命令由固定SSH路径执行；密钥路径不等于密钥内容，未记录敏感请求头。

| 命令/保存回执 | 实际结果 |
|---|---|
| `python scripts/s8_full_corpus_ingestion.py --corpus-root ... --prior-plan ... --prior-checkpoint ... --prior-authorization ... --missing-manifest ... --output ...` | exit0；10935/25054/11332，最大文档4次/94337，超限0；完整参数见证据包preparation-receipt |
| `python -m pytest tests/weknora_port/test_s8_full_corpus_ingestion.py tests/weknora_port/test_s8_overnight_ingestion.py -q` | 17PASS，exit0 |
| `python -m pytest tests/weknora_port/test_s8_execution_counter.py tests/weknora_port/test_s8_overnight_ingestion.py -q` | 23PASS，exit0 |
| `python var/reports/s8-full-corpus-20261020/test_prepare_executions.py` | 7项合成PASS，exit0；不创建真实授权 |
| `python ssh_script.py remote_stage.py remote-stage 180` | exit0，SHA/新审批/精确预分配通过；Provider0 |
| `python ssh_script.py remote_linux_preflight.py linux-preflight 150` | exit0，Linux全10935输入一致；Provider0 |
| `python ssh_script.py remote_start.py new-job-dispatch 100` | exit0，启动实际systemd后台任务 |
| `python ssh_script.py remote_handoff.py fresh-ssh-2-proof 90` | exit0，同PID/进度/来源原件SHA/无Windows依赖 |
| `python collect_completion.py` | exit0，下载实际CLOSED授权和INDEX_READY检查点；Provider0 |
| `python ssh_script.py remote_retire_completed_units.py retire-completed-units 90` | exit0，仅禁用两个成功完成单次服务的自启；业务数据不改 |
| 最终只读数据/完整账本审计 | 以evidence内脚本、首次失败与成功回执为准；新模型调用0 |
| `git diff --check` | 提交前实际执行exit0，最终提交号另见commit-receipt |
| 新QU/Rerank/生成/S8.1/S8.2；前端部署；备份恢复演练 | NOT RUN |

**未实现的命令不得报告通过。** 以上省略号是显示简写，实际完整参数/输出在附件，不代表未执行命令通过。

## 状态、停止与安全恢复

```sh
python3 /opt/rag-s8/state/full-corpus-status.py
python3 /opt/rag-s8/state/status.py
cd /opt/rag-s8/current
docker compose --env-file /opt/rag-s8/config/runtime.env -f deploy/s8/compose.yml -f /opt/rag-s8/config/loaded-images.json ps
docker compose --env-file /opt/rag-s8/config/runtime.env -f deploy/s8/compose.yml -f /opt/rag-s8/config/loaded-images.json exec -T api python -m scripts.s8_ecs_runtime check
```

两个任务已完成且disabled，**不要重启入库服务、重开CLOSED文件或复制额度**。数据、检查点、父/子账本和日志已在ECS，关Windows不影响服务。若以后要继续新增范围，先差集、独立新授权，不重发历史操作。

复现需保存`deploy/s8`源码、镜像导出SHA、runtime.env、loaded-images覆盖、root-private secrets、migration r4 manifest与资产、source projections、两个plan/检查点及全部账本。API/Worker普通启动不运行初始化；只有新空目标才调用正式迁移。镜像自带PostGIS表不能误判为旧业务，保留它；真实迁移为001–011及014–016（不存在012/013），另建1024 HNSW。当前ECS不支持CPU CFS，覆盖cpus=0，保留内存/pid限制并串行。

只在有意恢复新实例时做正常PG逻辑恢复并重新观测PG/Redis身份、校验storage marker；不得伪造旧物理身份。禁止down -v/prune/DROP/删除旧来源。备份与恢复演练本轮NOT RUN。密钥独立加密保管，不加入公开证据ZIP。

## 剩余边界与下一步

- Omni保存回执已真入库，但缺原SDK middle/archive/crop字节，21页仍NOT_SCORABLE；没有在Linux新装/运行MinerU，不承诺任意新扫描资料可用。
- MML图表事实、ParentChild4096/384未在此Goal扩展。现KB配置默认关闭ParentChild，只能把实际邻接回取记为邻接回取。
- 新Rerank/生成质量NOT_RUN；不能把索引通过外推成QA准确率，Graph/Top1推广结论不变。
- 前端由Owner延期，冻结构建时CRLF导出问题已记录；不可部署工件未上传。该项不纳入本次补齐Goal。
- 后续最小任务：另行批准S8.1的冻结QID/模型/有限请求包络，使用当前ECS实际KB/document/version映射。不中途改Gold，不自动开始。

最终报告提交号：见本地Git最终回复及ECS `/opt/rag-s8/reports/commit-receipt.json`；这与实际运行镜像/工具SHA分别记录，避免自引用提交号。
