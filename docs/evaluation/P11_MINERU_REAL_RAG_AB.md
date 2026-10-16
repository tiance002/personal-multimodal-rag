# P11 — MinerU 真实 RAG A/B 验收：预算阻断收口

日期：2026-10-16。状态：**BLOCKED；真实 RAG 成对 A/B 未执行，不能宣称 P11 验收通过。**

已完成真实 FastAPI 上传、本地 Parser、独立 Worker、PG 切块与资产准备。**未完成 Embedding、pgvector 索引、Search、Rerank、Context、Cheap 回答和生成引用验收。** 原因是剩余本地围栏只有 6,147 Token 占用额度，现有可信流程没有同账户同月增额操作。本轮模型调用为 0，未用离线输出、Mock、HTTP200或已有 P10 成绩替代真实 RAG 结果。

**未实现的命令不得报告通过。**

机器结果：[P11_MINERU_REAL_RAG_AB.results.json](P11_MINERU_REAL_RAG_AB.results.json)。原始证据：`E:/WK/run/p11-20261016`；`evidence-manifest.json` 保存400个证据文件的哈希与大小。后续报告审计/提交回执另存同目录。

## 1. 源码与边界

- 工作树：`E:/RAG-Worktrees/weknora-python-port-20261013`。
- 分支：`codex/weknora-python-port-integrate-image-20261014`。
- 实测源码：`55634295ca5e79aaa36853c35625056f750e6e7d`，其中 MinerU 实现提交为 `24e9782e445617f005fa8180a71a3a51ca33c09e`。
- 本轮没有修改生产源码、模型、Prompt、检索参数、Chunking、路由、数据库结构或依赖。机器结果记录125个后端Python文件的实际哈希；`git diff --exit-code -- backend pyproject.toml tests scripts` 实际exit0。
- P8/P8.1/P9/P10冻结证据与接手时已有改动，**378个保护文件哈希均不变**。`.serena/project.yml` 与六个既有未跟踪文件保留。
- 新增仓库文件仅本报告、同名results及progress记录；本轮辅助脚本、原始日志、隔离资产在E盘，不覆盖旧评测目录。

## 2. 授权与账本：不是Owner限制，而是缺少可信增额实现

### 2.1 已确认的有效授权

Owner 已授权 SiliconFlow 三模型继续使用，无需逐次询问。本轮不调用 DeepSeek。以下既有证据明确说明 **100万是MVP自行追加的有限执行围栏，非Owner或供应商硬限制**：

- `docs/weknora-port/REAL_EGRESS_AUTHORIZATION_20261015.md`。
- `docs/weknora-port/MVP_REAL_RAG_ACCEPTANCE.md`。
- `scripts/weknora_port_mvp_real.py` 的 `prepare()`，以及 `scripts/weknora_port_p8_real.py`。
- `E:/WK/run/p8-20261016/execution-fence.json` 的原文：Additional local execution fence; not an Owner SiliconFlow quota.

只读实测原授权配置仍可加载，原有效期为 **2026-10-16 19:47:22 Asia/Shanghai**。本轮未续期、未改变授权身份、未读出密钥正文。

### 2.2 实际账户占用

| 项目 | 前后实测一致 |
|---|---:|
| 真实账户/provider/月 | siliconflow-owner-main / siliconflow / 2026-10-01 |
| 原本地围栏 | 1,000,000 |
| carry-in | 14 |
| settled＋reserved/UNKNOWN占用 | 993,839 |
| 剩余可预留占用 | **6,147** |
| 历史调用记录 | 469：432 settled、37 UNKNOWN |
| 全部历史UNKNOWN保守占用 | **517,098** |
| P11新增调用、Token、UNKNOWN | **0 / 0 / 0** |

这37条是当前账户全部历史UNKNOWN，不等于P9新产生的17条；P9自己的314417占用仍完整保留。469条账本逐行、原月行与DeepSeek历史前后完全一致。原快照与最终快照见 `siliconflow-ledger-before.json`、`final-v2/ledger.json`、`final-v2/months.json`、`final-v2/deepseek.json`。

### 2.3 为什么没有建立P11增量额度

正常可信链路仍是：

`--authority-config → ApprovedTokenAuthority → ServerPermissionResolver → P1SendGate → TokenBudgetStore.reserve`

`authority.py::_approved_provider_limit_valid` 允许SF任意正整数有限围栏；但 `storage/token_budget.py::TokenBudgetStore.reserve` 对同一真实account/provider/month加锁后，要求已存 `limit_tokens/carry/approval_source` 与配置一致。直接提高配置会触发 **TOKEN_MONTH_AUTHORITY_CONFLICT**。

全仓与交接核对未发现现有受信的同账户同月增额API、CLI或人工runbook。旧“root reconciliation required”只是错误文案；既有授权续期只更改有效期，未改变100万围栏。`P1_SEND_GATE.md` 同样说明没有自动账本重置/协调命令。

`budget_proof.py` 对**真实只读PG快照**执行实际源码AST判断，exit0：原limit不冲突，同账户同月limit仅加1也冲突。**这是源码条件实测，不是实际PG admission拒绝，也没有发送模型请求。** 未直接UPDATE月行、移除守卫、换account/approval或修改历史占用。

沿用P9已有保守准入参考，同链MML所需观察占用为36,006、Omni为26,713，均超过6,147；这些是既有观察的准入参考，**不是本轮实际Token，也不是尚未生成的P11精确wire上界**。不消耗残余额度去得到无法配对的单边结果。

本轮按Owner“仅现有可信流程允许时建立增量”的约束，将增量流程记 **NOT_IMPLEMENTED**、依赖它的真实问答记 **BLOCKED/NOT_RUN**；没有扩大到开发新的额度治理接口。

## 3. 冻结范围与可比条件

`frozen.json` 在本轮上传前保存全部原件SHA、原问题、模型、源码与Scope计划；`gold-evaluation-only.json` 单独保存原官方Gold，不进入Parser/模型输入。

| 样本 | 原件SHA-256 | 计划 |
|---|---|---|
| row-0434 / cat PDF68页 | `40a74d3fc5e73c81465206a4043c19b850daf66fc5617756f5894d25ad054b4b` | A/B各1次 |
| row-0959 / b3 PDF21页 | `43be9592854c7da10bf011335faec017b726ff2e23bb4ccae7e8fe88d7801f22` | A/B各1次 |
| Omni06 yanbaoPPT4185 | `3471a533f957c8aed9d8732878a763c039017f454c70eecf1cc832038d76e956` | A/B各1次诊断，无官方QA Gold |
| Omni10 English transition | `428cca795816237571181f0b2f491ecb81d895359a142c07aa93d160ab58dd9d` | A/B各1次诊断，无官方QA Gold |
| Omni14 lecture1公式 | `95a3151c2c9f5f3d9e36392065ecd1e7ab6cf49ae9c591b698cff7196ab78601` | A/B各1次诊断，无官方QA Gold |
| Pew普通文字PDF45页 | `8f49f93f1665fd5ff604e66ffed9649d8b18ce7f114ab43463aeac46cd3d00cb` | 默认路径1次对照 |

原始官方MML问题保持：

- row-0434：Which dataset was introduced in the 'coding time' section of the PPT for training?
- row-0959：What are used for long-distance communication as introduced in this slide?

三个Omni页面统一冻结简短诊断：“请根据这页资料概括主要内容，并引用原文证据。”没有官方QA分数。普通PDF冻结原中位数问题作为后续诊断，本轮只完成默认解析回归。

计划合计11次问答，上限12；实际**0次**。全部11个group/sample记录保持NOT_RUN，未将“计划”计为执行题数。P9历史结果不合并为P11成绩。

固定模型为 bge-m3 / bge-reranker-v2-m3 / Xing4.0-29B，复用原tenant332已加密登记的模型，未重登记密钥。A/B新KB的模型ID、默认chunking与索引配置相同；分别使用新KB、document/version及资产根，计划查询再按原件document范围限定。

**云阻断下的准备状态不是正式A/B运行条件**：全部新KB保持 `cloud_allowed=false`，不授予云外发；vector/keyword配置仍保持真实默认True，没有用关闭vector或伪造向量来“完成”入库。普通Pew只走A的默认PDF；B实例显式扫描PDF选择不用于Pew，以免已知原生PDF拒绝被误认成默认路径退化。

## 4. 实际经过的链路与落地结果

两组使用真实正常FastAPI业务API；观察器复用P9，只被动记录真实调用，未Mock应用或模型。A使用现有PDF/Tesseract；B使用独立MinerU4.0.12 Basic/ocr，仍由正常上传装配选择。

| 部分 | 实际状态 | 证据含义 |
|---|---|---|
| HTTP ready、创建KB、上传、原件回读 | PASS，11份文档记录，11/11原件SHA一致 | 真实业务API，不是测试旁路 |
| A原生PDF解析、B MinerU解析 | PASS到PG来源/切块 | 无模型质量结论 |
| A独立Worker本地Tesseract | 3次实际领取，OCR均completed | Caption因无视觉模型unavailable，未冒充OCR为Caption |
| B Worker | 实际--once运行，image_ingest=false | Basic解析在API ingestion执行，不冒称走了图片队列 |
| PG source chunks/assets | 286块、216资产逐文件SHA核对 | 保存真实document/version和定位，尚非可检索索引 |
| Embedding/pgvector | **NOT_RUN，向量行0** | 全部文档在MODEL_ORIGIN_KB_EGRESS_DENIED停止 |
| Search/Rerank/Context | **NOT_RUN** | 无证据召回成绩 |
| Cheap回答/finish_reason/Gold评分 | **NOT_RUN** | 无答案正确率 |
| 生成引用及派生OCR引用回读 | **NOT_RUN** | 无生成回答，不能给引用通过率 |

11份文档均保留真实失败状态，没有预写completed；3个A图片任务也都失败收口，没有遗留pending/processing。此处的 **MODEL_ORIGIN_KB_EGRESS_DENIED 是为零云调用准备保留cloud_allowed=false产生的预期拒绝**，不是实测预算门拒绝，更不是MinerU解析失败。

### 4.1 每源解析差异

| 原件 | A：PG块/关键证据 | B：PG块/关键证据 | B资产 |
|---|---|---|---:|
| cat68页 | 23，图片占位；未含数据集名称证据 | **52，含Kaggle's Cat vs Dog dataset**；page_idx33，物理34页 | 73 |
| b3 21页 | 6，图片占位；未含完整通信证据 | **34，含ionosphere refracts radiowaves及microwaves通信文字**；page_idx16，物理17页 | 39 |
| Omni06 | 2，本地Tesseract OCR真实保存 | 1，MinerU结构化文字 | 5 |
| Omni10 | 2，本地Tesseract OCR真实保存 | 1，MinerU结构化文字 | 5 |
| Omni14 | 2，本地Tesseract OCR真实保存 | 1，MinerU结构化文字/公式 | 5 |
| Pew45页 | 162，默认解析逐字回归通过 | 不改为MinerU | — |

MML B关键块的真实chunkID、quote、页/块locator和source_version保存在机器结果 `real_http_worker_pg_preparation.sources[].key_chunks`。原件SHA与派生文本SHA分开；派生offset仍是Unicode codepoints，不是PDF原始字节偏移。216个资产在隔离资产目录实际存在且SHA匹配。

这比P10多证明了 **真实HTTP→Parser→PG切块/资产** 的接线，但不证明Search命中、Context交付模型、回答正确或引用可用。P10的3页NED不作为P11新成绩；本轮不新增NED总分或官方QA指标。

两次真实 `/api/v1/chunks/{id}` 读取A/B失败文档的代表块均返回 **404**，保留 `citation-unavailable-guards.json`；这是阻止失败文档被当作答案来源的正常保护，**不能写成生成引用验证PASS**。

### 4.2 普通PDF默认路径

通过真实HTTP上传同一Pew PDF，PG实际保存162块；逐块内容与P10原默认正文的对应区间完全相等，覆盖全部 **70,220 Unicode字符**，文本SHA仍为 `89e9e2fa069a6973984da116565ea7b993de7921f216896abfe09e1a60e01c11`。

结论仅为 **默认解析与切块无退化**。其Embedding、检索和问答未运行，不宣称普通PDF完整RAG回归通过。

## 5. 调用、Token、延迟及资源身份

- 正式问答尝试0；Embedding0、Rerank0、Cheap0、DeepSeek0；没有供应商请求wire记录。
- 本轮实际input/output Token均0，新增UNKNOWN占用0；既有37条UNKNOWN完整保留。
- QA延迟 **NOT_RUN**。B组上传到响应的实测墙钟（包含本地解析与正常处理）：cat **132.96秒**、b3 **58.80秒**、Omni06 **33.57秒**、Omni10 **14.87秒**、Omni14 **23.00秒**。这不是模型回答延迟。A组上传延迟因本地状态汇总问题未保留完整，标 **NOT_RECORDED**，不以时间戳猜补。
- PG：127.0.0.1:56423，`weknora_python_port_20261013`，system identifier **7696088403504697382**。
- Redis：127.0.0.1:56424，DB15，run_id **87b4c2a9f76f9f94b34b5f21fe31a967926f343a**；A/B各自唯一 `weknora-python-p11:*` prefix，详见机器结果。未flush。
- 两组各自隔离资产/TEMP根 `E:/WK/run/p11-20261016/A`、`B`。模型环境复用 `E:/WK/mineru-env`；权重复用P10 E盘模型；加密模型登记继续使用原master.key路径，没有输出密钥。
- API A端口56429，parent35024/child16352；B端口56431，parent10448/child44524。独立Worker starter PID：A23008/10156/39440，B22568。启动前确认旧同tenant图片任务均已terminal，未消费其他任务队列；B空Worker未领取A任务。
- 仅停止本任务命令核验的API进程树；Worker/本地解析进程已退出。收尾56429/56431无监听，PG/Redis仍healthy，无清库/卷重建或容器变更。

## 6. 实际命令、退出码及失败保留

所有命令cwd为工作树；Python为 `E:/RAG quention/.venv/Scripts/python.exe -B`，辅助纯JSON聚合使用 `E:/WK/mineru-env/Scripts/python.exe -B`。PYTHONPATH为工作树、`E:/WK/deps/reader`、工作树var/weknora-port/runtime-deps，TEMP/TMP为P11 E盘目录。

| 实际命令 | 退出码/结果 | 证据 |
|---|---|---|
| RAG Python `E:/WK/run/p11-20261016/preflight.py` | 0，只读PG/Redis/授权/账本 | preflight.log、preflight-read-only.json |
| RAG Python `E:/WK/run/p11-20261016/budget_proof.py` | 0，配置/源码条件审计；增额BLOCKED | budget-proof.log/.json |
| RAG Python `E:/WK/run/p11-20261016/prepare_no_cloud.py freeze` | 0，冻结6源/6问题 | freeze.log、frozen.json |
| 同脚本 `start` | 0，两个API进程启动 | start.log、A/B/api-command.json |
| 同脚本 `upload --group A`、`upload --group B` | 各0，6份/5份HTTP接收并原件回读 | upload-A/B.log、A/B/http与intents |
| 同脚本 `worker --group A` 三次；`--group B` 一次 | starter命令各0；实际Worker stdout有正常run_once结果 | worker-A*-start.log、A/B/worker.stdout、PG task snapshots |
| 实际Worker子进程 `-m backend.weknora_port.worker --once --authority-config <A或B/authority.json>` | **子进程退出码未单独捕获**；启动命令exit0不能冒称worker验收exit0 | 每次stdout与terminal任务状态已保留 |
| 同脚本 `inspect --label checkpoint`、`interim` | 各0，早期局部快照 | inspect-*.log；保留，不作最终全集 |
| RAG Python `E:/WK/run/p11-20261016/final_audit.py` | **1**，只读SQL误假设embeddings有tenant_id | final-audit-first.log、first final/快照保留 |
| RAG Python `E:/WK/run/p11-20261016/final_audit_v2.py` | **0**，按实际schema/五个P11 KB收口 | final-audit-v2.log、final-v2/、audit-results.json |
| 命令核验PID后PowerShell `Stop-Process -Id <owned PID>` | 0，无本任务API残留 | stop-owned-before.json、stop.exitcode.txt、ports-after.json |
| MinerU Python `E:/WK/run/p11-20261016/compose_delivery.py` | 0，机器结果/400文件哈希索引 | compose-delivery.log、evidence-manifest.json |

API实际命令复用 `-m scripts.weknora_port_p9_observed_api --port 56429/56431 --authority-config <A/B/authority.json> --evidence-dir <A/B>`，其内部创建真实FastAPI应用、受信装配和正常业务API；不是业务API Mock。完整argv见各command.json。

首次失败全部保留：

1. 初始化检查默认GBK读取UTF8失败，未发生请求或文件修改；显式UTF8修正。
2. 准备脚本并行保存整个共享state，导致本地汇总丢失A组join。原HTTP回执、PG、原件均未丢失；按不可变HTTP回执一次重建为 `state-reconciled-v2.json`，没有重发任何上传/模型请求。原state、checkpoint、interim保留，不当全集。
3. 首次收尾SQL误认为upstream embeddings表有tenant_id；只读失败exit1。通过information_schema核实，以确切P11 KB IDs查询，v2 exit0，未修改schema或隔离保护。

这些是评测辅助代码问题，不是本轮生产RAG Bug。**没有生产修复、迁移或新增依赖，未运行额外pytest或全项目回归。** 没有把旧49项离线测试作为P11真实A/B通过证据。

## 7. 独立评分与停止决定

| 维度 | row-0434 A/B | row-0959 A/B | Omni3 A/B | Pew默认 |
|---|---|---|---|---|
| 新解析证据/PG保存 | A缺失 / B存在 | A缺失 / B存在 | 两组真实OCR产物保存 | 正文/切块逐字PASS |
| 向量/检索/Rerank/Context | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| 答案Gold一致性 | NOT_RUN | NOT_RUN | 无官方QA Gold，诊断也NOT_RUN | NOT_RUN |
| 生成引用合法性/完整性 | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |

**MinerU是否值得正式用于真实RAG：本轮尚不能给出肯定结论。** 已证明扫描件/图片的正常HTTP→PG解析接线可行，未证明检索、答案或引用收益。建议保留P10显式Basic候选、普通PDF旧默认，不切换生产默认或扩大Standard/自动解析器路由。

剩余明确阻断是 **可信的同账户同月增量围栏协调操作尚未实现**，不是Owner未授权使用SF、供应商账户余额已证实不足或MinerU模型无法运行。下一步应单独处理该可信操作并定向审计；保留原账户/月、全部历史记录与UNKNOWN，然后恢复这份冻结计划的有限成对实测。当前不自行开发新额度接口。

模型答错、漏引用、Rerank分数或Context证据丢失，本轮都没有新执行数据，不能归因于解析，也不据此修改Prompt/检索/路由。P9历史质量问题继续原样保留。

按本轮允许的预算阻断停止条件收口；这不是负责人确认的里程碑验收。无Push、部署、合并或Tag，不继续六套全量评测或其它优化。
