# P7 阿里云 ECS 夜间数据准备与自动评测预检

**检查日期：** 2026-10-09（Asia/Shanghai）
**范围：** 只读检查本地仓库、既有 ECS、评测注册库和受限网络探测；未执行正式夜间任务。
**综合结论：** `PREFLIGHT_BLOCKED`
**工作区基线：** HEAD `e6cca0971d0f1875ca562a6b953fd5ec252b28e0`，分支 `codex/local-first-rag-v1-20260930`。检查前已有大量与本任务无关的本地未提交文件；本报告未覆盖它们，也未修改业务代码。

## 1. 目标与改动

本轮核实云端数据、当前 RAG/Evaluation Center/Langfuse 部署、模型/数据源连通性、资源和无人值守运行条件，并专项审计 P6 的已有评分与追踪能力。唯一新增项目文件为本报告；未改数据库、索引、服务、权限、配置或源代码。

状态定义：`PASS` 表示本轮取得符合合同的实际证据；`FAIL` 表示检查到明确不符合；`BLOCKED` 表示存在尚未解决的执行阻断；`UNKNOWN` 表示只读边界或现有证据无法判定。P6 子项使用用户指定的 `IMPLEMENTED_AND_VERIFIED`、`IMPLEMENTED_UNVERIFIED`、`MISSING`、`INCOMPATIBLE`。

## 2. A. ECS 访问与环境

SSH 以现有密钥和严格主机密钥校验连接到唯一已记录实例，未打印或读取密钥内容。ECS IMDS 返回实例 `i-bp1flliql08bz8yftild`、地域 `cn-hangzhou`、可用区 `cn-hangzhou-k`；Ubuntu 24.04.5 LTS，内核 Linux 6.8.0-139-generic。实例访问 `PASS`。

| 项目 | 观测值 | 状态/依据 |
|---|---:|---|
| vCPU | 2 | 真实 ECS 检查 |
| 内存 | 3,803,385,856 B 总量；约 2.96 GiB 可用 | 真实 ECS 检查 |
| 根文件系统 | 41,882,943,488 B 总量；36,582,248,448 B 可用；约 9% 已用 | 真实 `df`；未见独立语料/索引挂载 |
| 时间同步 | `NTPSynchronized=yes`；UTC 时间已记录 | `PASS` |
| PostgreSQL | 16.15，服务运行，监听回环地址 `127.0.0.1:5432` | 真实 ECS 检查 |
| Evaluation Center | `rag-eval.service=active`，监听 `127.0.0.1:8787`；健康检查返回 `ok`，Git SHA `f15aaeb374e32d7955429784b4d9dd50317608a5` | 当前只证明评测中心 HTTP 进程健康 |
| Docker | 服务 inactive，ECS 上没有 `docker` 可执行文件 | ECS 不承载 Docker 评测栈 |
| 公网服务入口 | 未观察到；评测器和 PostgreSQL 均只绑定回环地址 | 外部客户端须走 SSH 或另行批准的入口；未改网络 |
| 运行方式 | `rag-eval` 使用 systemd、`Restart=on-failure`；仅发现该评测器与 PostgreSQL 相关运行服务，没有评测任务 timer/worker/队列服务 | 对“评测进程重启”不等于对“评测作业可恢复” |

能够通过本机 SSH 发起远程只读命令，但这不证明未来作业不依赖本机前台会话。ECS 上没有当前 RAG 应用服务、RAG 作业执行器、部署语料/索引或任务持久化队列。故“Windows 关机后正式评测可继续”`FAIL`。只观察到本 ECS 的监听为 loopback；实际作业无需本机网络的云端执行链路并未部署。

实例停机、维护和到期策略未能通过本轮已授权凭据/工具读取；标记 `UNKNOWN`，不得据此假设夜间实例一定持续运行。

## 3. B. 已上传云端资产

ECS 项目路径为 `/srv/rag-eval/app`，实际符号链接指向发行目录 `/srv/rag-eval/releases/673449235bed8e693550b5774b71b2b004e5dd75d40902679e18cbda8a0a6a99`。该评测发行包 SHA 与健康接口报告的应用 Git SHA 不同属于发行目录标识与源代码 SHA 的不同字段，不推断为相同版本；未见当前本地 HEAD 的部署证明。

| 资产 | 云端状态 | 路径或标识 | 哈希/身份核验 | 需要动作 |
|---|---|---|---|---|
| 当前 RAG 项目代码/服务 | `ABSENT` | 常见 `/opt/rag`、`/opt/personal-rag`、`/srv/personal-rag` 均不存在 | ECS 上只有独立 `/srv/rag-eval` 评测中心发行包；代码 SHA `f15aaeb…` | 正式云端执行前需按批准的最小部署方案提供当前 RAG 运行代码 |
| SciFact/MIRACL-ZH/LongBench-ZH raw、prepared 语料 | `ABSENT`（已检查已知 ECS 项目目录与 `/srv/rag-eval` 近层文件） | 未发现对应语料目录或 manifest | 无文件可核哈希 | 上传经过白名单筛选的必要数据及 manifest；本轮未上传 |
| 479 个 locked holdout QID、Gold/qrels、历史检索/context 结果 | `ABSENT` | ECS 评测发行目录/数据区无这些文件 | 无法在云端重核本地指纹 | 需上传必要评测输入/身份映射/历史摘要；历史结果不替代重做的答案生成评测 |
| 当前 RAG PostgreSQL Schema/索引 | `ABSENT` | ECS 只有旧 `study_platform` 数据库与独立 SQLite registry | PG 在只读事务中检查；匹配名称的 DB 只有 `study_platform`，不得当作本项目数据库 | 正式阶段建立经批准的目标 DB 与迁移/备份方案 |
| 历史 Evaluation Center 注册库 | `PARTIAL` | `/srv/rag-eval/experiments/registry.sqlite3`，1,499,136 B | 只读 SQLite；6 个实验、144 个 case、0 个 error 行、4 条验证；6 个实验均 `partial`，数据版本仅 `audit-synthetic-9c960258-v1` 或 `trust-docs-v1`，不是 3 套公开基准 | 可保留作为历史登记记录；不能当作新评测完成证据 |
| 评测历史报告 | `PARTIAL` | `/srv/rag-eval/reports/trust-cloud-probe.json`，52,659 B | 文件存在；未读取或输出内容，避免泄露测试数据 | 由 Owner 需要时单独批准内容审阅；文件存在本身不证明成功 |
| OmniDocBench/MMLongBench/HotpotQA 新数据 | `ABSENT` | 无云端下载记录或文件 | 未下载 | 等源可达性、许可和确切大小验证后再执行 |
| Langfuse trace 与评测记录关联 | `ABSENT`（当前 ECS Evaluation Center 范围内） | 对实际发行包 `eval_center/` 做 `langfuse|trace_id|traceId` 符号检索为 0 个文件 | SQLite 身份字段/指标键没有 QID 与 Langfuse Trace ID；无 trace 运行证据 | 复用应用已有 Langfuse 适配器；只对评测器补缺失的身份映射/span 接入，不另建追踪架构 |

ECS 的旧 `study_platform` 数据库为不相关旧平台库。本轮仅核验数据库名/大小及 `transaction_read_only=on`，未浏览其业务表内容。它既不是当前 RAG 数据库，也不能据其推断历史索引可复用。未发现需立即复用的文档向量索引、Gold 集或公开评测语料。此前 Windows Docker/PostgreSQL 资产检查不能替代云端上传证据。

## 4. C. 新数据集下载探测

本轮没有下载完整数据集，没有绕过官方源配置代理或镜像。ECS 对 Hugging Face 元数据/API 探测均超时；域名解析能返回地址，但 TLS/HTTP 未完成，不据 DNS 成功宣称可下载。Hotpot 官方主页支持受限 Range 读取，但官方开发 JSON 文件的 HEAD/小范围请求超时。精确子集大小和总新增字节数因此为 `UNKNOWN`，不能证明 200 MB 目标。

| 数据集 | 元数据访问 | 实际文件访问 | 子集下载可行性 | 需要下载的字节数 |
|---|---|---|---|---:|
| OmniDocBench v1.6 | ECS Hugging Face 元数据请求超时 | 未取得标注文件或图像响应 | ECS 当前无法证明可按 24 张图像加标注小批量下载。官方卡片总数据约 1.49 GB，目标是 24 页，不应把全量当预算；许可证标示 CC BY-NC 4.0 | `UNKNOWN` |
| MMLongBench-Doc | ECS Hugging Face 元数据请求超时 | 未取得 Parquet/PDF 响应 | 尚不能证实 3 个完整 PDF 与 8 个问答/证据标签可分开拉取；官方 HF 页面显示全库约 662 MB，页面未显示明确许可证，必须先确认许可 | `UNKNOWN` |
| HotpotQA Distractor Dev | 官方主页 `hotpotqa.github.io` 在 ECS 以 Range 获得 HTTP 206、131,072 B（总主页 144,422 B） | `hotpot_dev_distractor_v1.json` 的请求超时；未读取 JSON 片段 | 主页列出官方开发文件链接，但实际文件字节数、格式字段与小范围下载均未验证 | `UNKNOWN` |

ECS 网络探测还确认 `api.siliconflow.cn` DNS 与 TLS 1.3 握手可完成（本轮 API 业务请求数为 0）。HF 两个探测域名虽返回 DNS 记录，后续 TLS/HTTP 仍失败；原始异常为 Python `URLError`/`OSError`，请求约 12 秒超时，传输响应正文为 0 B。不要据此自行设置 Windows Clash、第三方镜像或中转服务；最小阻断是 ECS 的 Hugging Face/官方文件服务 egress 尚未打通或未解释。

## 5. D. 模型 API 与成本限制

| Provider / 能力 | 实际模型 ID（本地 Registry） | 网络 | 认证/模型响应 | 实际调用/结构校验 | 费用 |
|---|---|---|---|---|---|
| SiliconFlow Embedding | `BAAI/bge-m3`，1024 维 | ECS DNS/TLS 成功 | 未测试 | `NOT RUN` | 官方价格页列该非 Pro 模型免费；仍需确认实际账号/端点可用 |
| SiliconFlow Rerank | `BAAI/bge-reranker-v2-m3` | 共用 API 主机 DNS/TLS 成功 | 未测试 | `NOT RUN`；不以 TLS 代替响应结构检查 | 官方价格页列该非 Pro 模型免费 |
| Cheap Chat | `XingChenAGI/Xing4.0-29B` | 共用 API 主机 DNS/TLS 成功 | 未测试 | `NOT RUN` | 官方价格页列该模型免费；账号级限流/可用性仍未知 |
| Expensive Chat / Vision | DeepSeek `deepseek-flash` | 未做本轮业务请求 | 未测试 | `BLOCKED`：项目 AGENTS.md 的动态任务约束明确禁止进一步 DeepSeek 请求 | DeepSeek 官方价格页列当前 Flash 峰值输入/输出价格；不以理论短请求费用覆盖调用禁令 |
| Langfuse | 本地适配器默认关闭，需显式启用及凭据 | 没有发起 Langfuse 网络/API 请求 | `NOT RUN` | `BLOCKED`：同一 AGENTS.md 明确禁止进一步 Langfuse 请求；ECS 无追踪集成证据 | 未产生 Langfuse 请求/费用 |

本地模型配置中所有 `resolved_revision=UNKNOWN`；本轮未读运行时 secret/环境文件，也没有本地转发密钥。**实际 Provider API 业务请求全部为 0**。因此认证、余额、模型可用、返回字段/向量维度、finish reason、实际 usage、耗时和费用都不能标 PASS。本轮曾获一般性的每能力一次最小请求权限，但仓库 `AGENTS.md` 的具体动态执行约束写明 “no further DeepSeek/Langfuse request”，优先遵守该具体限制；未请求或显示密钥。SiliconFlow 虽可用官方当前价格页估算非 Pro 模型免费，仍未调用，不能把价格表当作 API 成功证据。

## 6. E. 索引、身份与迁移

- ECS PostgreSQL 16.15 当前只有 loopback 服务及数据库 `study_platform`（10,607,639 B）；这是无关旧应用库。本轮未读取表内容，也未发现 RAG benchmark 数据库、当前项目 Schema Revision、HNSW 索引或向量表。
- ECS 注册库保存的历史本地实验模型标签为 `local-qwen3.5-4b` 与 `local-bge-m3`。这些实验状态均为 `partial`；当前部署没有 RAG 索引可对照。不能根据历史标签或 1024 维猜测 Embedding 模型可兼容。
- 本地 Registry 的三个公开数据集历史索引采用 Ollama/BGE-M3/1024 维；当前 Registry 的 Embedding 是 SiliconFlow `BAAI/bge-m3`/1024 维，当前 RAG Chunking/Schema 身份也有差异。向量同维不意味着模型权重、归一化、预处理、Chunk ID 或索引内容兼容。没有云端索引可复用证据；目标语料需重新按当前代码切分、Embedding、索引，先以独立 DB/版本完成，不能覆盖历史 Gold/索引。
- Gold/qrels 外部文档身份映射目前仅在本地评测产物有核验结果。ECS 没有 manifest/QID/qrels 文件，不能验证与云端文档 ID 的映射；应随上传物保存原始外部 ID、规范文档 ID、映射哈希和版本，不应把未知匹配强行并入。
- 当前新增数据三个数据源的确切文件大小、传输时长、OCR/Caption 时长、Embedding 计费/速率均不可测；无法作出可靠的存储/费用/8 小时准备时间估算。根盘尚有约 34.1 GiB，但不能把它当作已满足全部语料、模型临时文件、索引和备份空间的证明。

## 7. F. P6 Evaluation Center 与 Langfuse 云端适配审计

代码存在不等于在线链路可用。下表分开记录**仓库当前实现**与**当前 ECS 实际证据**；不得将后者缺失说成整套产品代码不存在。

| 能力 | 状态 | 代码/真实运行证据与缺口 |
|---|---|---|
| 1. SciFact、MIRACL-ZH、LongBench-ZH 及 OmniDocBench、MMLongBench-Doc、HotpotQA 协议 | `INCOMPATIBLE` | `eval_center/public_runner.py`、`public_data.py` 提供 SciFact/MIRACL-ZH/LongBench-ZH 的公开集加载与检索评分；LongBench 可选生成不是统一 48 QID QA 契约。OmniDocBench 是解析指标任务，应独立于 QA；新 MMLong/Hotpot 的专项证据/多跳评分适配不存在。ECS 历史 6 个实验只属 synthetic/trust-docs，未跑所列数据集。 |
| 2. Cheap Only / Dynamic Router / Expensive Only 公平三组对照 | `INCOMPATIBLE` | `backend/app/application/rule_router_v1.py` 已有三种策略/固定角色路径，Quick Chain 可运行动态路由；但 `eval_center/runner.py` 只跑两组 Chunking A/B（A 1200/120、B 700/70），`public_runner.py` 主要比较检索模式。不存在一次冻结同一输入/context、执行三条生成策略、记分并回传三臂结果的评测 Runner。 |
| 3. Experiment ID、Run ID、QID、数据/模型版本及 Langfuse Trace ID 关联 | `INCOMPATIBLE` | 本地 bundle/注册库保存 Experiment ID、dataset version、Git SHA、corpus/config hash、model/embedding profile；公开 Runner 有 QID、run token 和 query-set hash。但两者没有一致关联合同，v1/v2 bundle 的 case ID 是匿名 case ID，不包含 QID/trace ID；ECS 的实验历史无 QID/Trace ID。Langfuse 适配器给 trace metadata 设置应用 `run_id`，未将 trace ID 反写评测 case/experiment。 |
| 4. 阶段 P50/P95、总耗时、Token、费用、异常 | `IMPLEMENTED_UNVERIFIED` | 本地 `eval_center.metrics.aggregate_metrics` 对实际 `*_ms` 生成 p50/p95/p99；telemetry 记录分阶段调用/token/阶段耗时，V2 有 errors/stage/error_code，历史 ECS JSON 指标键也包含 end-to-end、ingestion、embedding、retrieval/context/generation 等耗时及部分分位数/token。费用当前支持的是局部成本/美元字段或应用预估，不等同各 provider 实际人民币账单；ECS 6 个 `partial` 历史实验不能证明本次三组 48 题有完整阶段耗时/真实费用。 |
| 5. Langfuse 不可用时仍保留本地评测结果 | `IMPLEMENTED_UNVERIFIED` | 应用 `LangfuseObservability` 对初始化、trace/span 收尾和 callback 错误 fail-open；评测数据由独立本地 SQLite Store 保存，概念上与 Langfuse 解耦。但当前 ECS `eval_center` 发行目录不含 Langfuse/trace 字符号，尚无线上断链运行记录证明新云端 Runner 会先持久化结果并耐受 Langfuse 故障。 |
| 6. 云端后台任务、持久化进度、有限重试、费用门禁、中断恢复 | `INCOMPATIBLE` | ECS 的 systemd 只管理评测器 HTTP 进程；没有评测 Worker/queue/timer/任务检查点记录。Evaluation Center 的 SQLite 可以持久保存已导入的 experiment/case/error，却不是正在执行任务的队列/checkpoint/恢复状态机。当前模型 adapter 禁止自动重试、预算组件属于 RAG 请求路径，并无 48 题总任务级并发/次数/总价预留与恢复协议。 |
| 7. 当前 ECS 连接已有 Evaluation Center 与 Langfuse，不依赖 Windows 网络 | `INCOMPATIBLE` | 可经 SSH 访问 ECS 的 Evaluation Center loopback 健康接口；未见当前 RAG 服务、完整公开数据、云端执行 Runner 或 Langfuse span 接入。ECS 实际 release 中 `eval_center` 下 Langfuse/trace ID 字符检索 0 个文件，历史 registry 也无 trace 字段。应用仓库有 Langfuse 适配器不构成 ECS 部署证据。 |
| 8. 检索评测、解析评测、答案生成评测指标隔离 | `IMPLEMENTED_UNVERIFIED` | 当前代码路径把 public retrieval ranking/context 指标、`backend/app/domain/parsers.py` 与 parser 测试、质量/答案指标分开建模；OmniDocBench 不能直接当 QA。可是公共集 runner 的 generation 仅有限可选，远端历史记录为旧 synthetic/trust-docs 部分实验；还没有云上 48 题分任务报告证明不会把 parser accuracy、retrieval recall 和 answer quality 汇为同一指标。 |

**Langfuse 判断：** 本地仓库确有真实的 opt-in SDK 适配器（`backend/app/adapters/langfuse_tracing.py`），能建根 observation、传播 session/name/tags、添加 run_id/status/call/token/cost 元数据，并提供 LangChain callback；bootstrap 将其接入应用。ADR-005 规定必须显式启用云外发、知识库允许 cloud、凭据有效，内容捕获默认关闭。源码代码路径属于 `IMPLEMENTED_UNVERIFIED`；没有真实 Langfuse 请求/trace 证据，且 ECS 评测中心没有该链路。因此回答“项目是否有 Langfuse 代码”是有；“当前 ECS 评测中心是否已连上并能把 trace 关联到实验/QID”是没有证据且当前不兼容。

**复用现有能力的最小缺口清单（供后续单独正式授权实施）：**

1. 扩展既有 Evaluation Center 注册契约，给 run/case 保存稳定 QID、策略臂、数据/gold/index/config/model revision、Langfuse Trace ID；向后兼容旧 bundle。
2. 在现有 public/专项 dataset adapters 中增加三组策略统一调用器，让同 QID 的 Gold、RAG Context 与环境身份冻结复用；禁止重建第二个评测平台。
3. 只增补缺失的检索/重排/Context/Router/Generation/Validator span 和评分器映射；引用已有 Langfuse SDK/适配器，离线 SQLite 落盘为权威结果，导出 trace 失败不得丢结果。
4. 在既有云端 Evaluation Center 周边补充作业状态/检查点、总预算预留、并发/超时/有限重试和可恢复的阶段状态；不要把现有服务进程 Restart policy 当作作业恢复。
5. 分开报告：检索 ranking/context recall；解析 OCR/layout/table 等专项分；答案正确性/引用/多跳证据分。对 48 QID/144 次生成的 40 分钟目标只能在正式小样本阶段实测，当前不做推断。

以上是差异归纳，不是本轮实施授权，也未修改任何应用代码。

## 8. G. 最终门禁结论

| 门禁 | 状态 | 最小证据 |
|---|---|---|
| `ECS_ACCESS` | `PASS` | SSH 严格主机密钥校验成功；获取真实实例身份、OS、loopback health 状态 |
| `CLOUD_ASSETS` | `FAIL` | 当前 RAG、479 题/Gold/qrels/公开语料/索引均未发现；ECS 仅有独立评测器、部分旧实验 registry 和无关 `study_platform` DB |
| `DATASET_DOWNLOAD` | `BLOCKED` | HF metadata/TLS/HTTP 超时；Hotpot homepage Range 成功但官方 dev JSON 请求超时，精确字节未知 |
| `MODEL_API` | `BLOCKED` | SiliconFlow 仅网络/TLS 成功，所有业务请求 `NOT RUN`；DeepSeek/Langfuse 受 AGENTS.md 明确约束禁止进一步请求 |
| `INDEX_COMPATIBILITY` | `FAIL` | ECS 无目标 RAG DB/索引；旧 Ollama 与当前 SiliconFlow profile/Chunk schema 身份不同，维度相同不足以兼容 |
| `UNATTENDED_RUNTIME` | `FAIL` | 没有云端 RAG/评测任务 Worker/持久进度/恢复机制；实例夜间生命周期还未知 |
| `EVALUATION_READINESS` | `FAIL` | P6 三臂 Runner/QID+Trace 映射/远端专项协议/恢复门禁不齐；ECS 历史实验都 partial，非目标数据集 |

因此综合为 **`PREFLIGHT_BLOCKED`**。进入正式云端实施前的最小待办是：确认 Hugging Face 出口/许可和文件字节；由 Owner 批准正式运行费用上限与必要外发；决定并部署当前 RAG 代码、最小语料/Gold/manifest 到目标 ECS；在复用 Evaluation Center/现有 Langfuse 的前提下完成上表 P6 缺口和作业恢复能力；确认 ECS 停机策略与预计负载存储/时长。未启动夜间任务，未运行 479 或 48 题评测，也未形成任何正式验收/里程碑批准。

## 9. 执行命令与证据

只读命令按类别列出（PowerShell 本地与 SSH 远端；所有 SSH 使用 BatchMode、IdentitiesOnly、StrictHostKeyChecking=yes、显式 known_hosts、ConnectTimeout=10）：

- 本地：`git status --short`、`git rev-parse HEAD`；`rg -n` 检索指定评测、遥测、路由、Langfuse 源码/报告；`Get-Content` 读取指定源码、ADR 与用户提供的任务文本。成功读取/检索，未执行测试。
- ECS：SSH `cat /etc/os-release`、读取 IMDS 实例标识、`lscpu`/`free`/`df`/`lsblk`/`findmnt`/`timedatectl`、`systemctl is-active`/`show`/`list-units`/`list-timers`、`ss -lnt`、有限目录枚举、`curl` loopback `/health`。只读成功；未读 `/srv/rag-eval/configs/runtime.env`。
- PostgreSQL：只读列出版本/数据库名/大小，显式 `BEGIN READ ONLY` 并核验 `transaction_read_only=on`；没有扫描业务表。
- Registry：Python SQLite `mode=ro`、`PRAGMA query_only=ON`；查询表计数、实验状态/版本/身份字段，以及 JSON 指标键名聚合（不输出问题、答案、指标载荷）。成功；历史记录 `6 experiments / 144 cases / 0 errors / 4 verifications`。
- ECS trace 检查：`grep -R -l -E 'langfuse|trace_id|traceId' /srv/rag-eval/app/eval_center` 返回 0 个文件；systemd timer/services 只读枚举。
- 网络：ECS urllib 对官方 HF 元数据与 Hotpot dev 文件作有界请求；HF 超时，Hotpot 主页 Range 206/131072 B，dev JSON 超时；ECS SiliconFlow DNS/TLS 握手成功。所有 provider/Langfuse API 业务请求数均为 0。
- 一条早期远端 Python heredoc 因 PowerShell/CRLF 传递错误退出码 1；之后使用 Base64 只读脚本重复了所需的只读 SQLite 检查并成功。不会把失败脚本报告为成功；数据库始终只读。
- 本轮**没有运行测试**：`NOT RUN`。没有执行未实现的 `make verify-*` 命令。没有触发部署、上传、下载全集、索引重建、外部 API 请求或正式评测。

## 10. 版本与下一步

- 检查前/后 HEAD 均为 `e6cca0971d0f1875ca562a6b953fd5ec252b28e0`；没有提交、tag、部署或改变用户已有工作区改动。
- 实际模型/effort 路由身份：`UNKNOWN`（应用工具没有提供本次会话上游模型实际身份的可核验回执）；不得将模型自述当作平台确认。
- 下一步仅为负责人审阅本报告并决定后续云端实施范围/费用门禁。本轮不代表已批准夜间运行。

### 参考资料

- OmniDocBench v1.6 官方数据页：<https://huggingface.co/datasets/MinerU25Pro-NIPS26/OmniDocBench-v1.6>
- MMLongBench-Doc 官方数据页：<https://huggingface.co/datasets/yubo2333/MMLongBench-Doc>
- HotpotQA 官方主页：<https://hotpotqa.github.io/>
- SiliconFlow 官方价格：<https://siliconflow.cn/pricing>
- DeepSeek 官方价格：<https://api-docs.deepseek.com/quick_start/pricing/>
- 项目 Langfuse 决策：`docs/adr/ADR-005-langfuse-tracing.md`
