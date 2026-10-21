# S8.1 ECS 真实端到端问答验收

**状态：`S8_1_PARTIAL`。ECS 已证明有原文支持的完整 API 问答链路；13 次提交中 12 次完成，B0434 首次运行被容器内存上限中断，保留失败、未重发。质量结果不支持推广 Top1 或 Graph。**

执行日期：2026-10-20。本轮止于 S8.1，S8.2、D 组合臂、前端部署均 `NOT_RUN`。

机器结果：[S8_1_ECS_END_TO_END.results.json](S8_1_ECS_END_TO_END.results.json)。每题包含完整 QID/q0、KB/空 document_scope、run/session、实际 Rewrite、召回/评分/Context、模型请求、答案、适用评分、用量和原始证据路径。逐事实审查见本目录 `evidence/s8-1-ecs-20261020/` 中 A9、BC 和首次故障报告。

## 1. 运行身份、版本与保护

| 项目 | 实际核验 |
|---|---|
| 分支 / 开始 HEAD | `codex/s8-six-dataset-evaluation-20261019` / `37d59aac67489a0a12d49b6ca10704ec87e00adf` |
| 本轮参数与执行器实现提交 | `4eace2ef64d8f395e511d84f1ac8267e15be1806` |
| ECS | `i-bp1flliql08bz8yftild`，cn-hangzhou，Ubuntu24.04，2CPU / 3.54GiB，无 swap |
| 实际镜像源代码 | `50f61d1e5494951fdc96f9a9845ff7f43633a7f4` |
| 镜像 ID | `sha256:8e31042a532fc80be6f5595407fcaf30da37147a88edb1d998a773bda3a2ffa8` |
| 已加载源码覆盖 | 仅本轮 `runtime.py`、`conversation_configuration.py` 只读文件覆盖；各 SHA 在 JSON 的 `environment.runtime_overlay_shas`。控制器另以只读文件装配，不能把镜像源 SHA 写成本地交付 SHA |
| API | `127.0.0.1:18800`；标准 FastAPI，服务器固定 tenant332/既有 subject，Bearer 覆盖；评测实例串行使用同一端口 |
| PG | `weknora_s8`；system ID `7698420805889187877`；PG17.9 / vector0.8.1 / pg_search0.22.6 |
| Redis | DB8；prefix `weknora-python-s8:runtime`；run ID `268d42dec48542084b1478eea849ecfb7d3f9b83` |
| 来源存储 | `/opt/rag-s8/storage`；ownership marker 匹配 PG 身份 |
| 准备语料 / 实体记录 | 11,683 份 / 27,881 向量；物理保留记录 11,708 文档 / 28,000 Chunk 与向量，含历史保留项，执行前后数量一致 |
| 默认开关 | 正常 API/Worker cloud=false；Graph=false；floorless=false。实例内仅在本轮合法子授权下临时启用发送 |

缺失/错误令牌被拒绝，tenant1 KB 访问被拒绝；客户端伪造身份头不能改变服务器 tenant332。来源 Chunk 的 KB、版本、原文件 SHA 和正文均使用正常 HTTP 回读验证。HNSW/BM25 索引仍 valid/ready；没有新 OCR、重建 Embedding、重入库或建图。

正式问答前已独立 `pg_dump`：`/opt/rag-s8/backups/weknora_s8-pre-s8-1-20261020T004041Z.dump`，145,042,978 bytes，0600，SHA256 `5ac5ed126cc66dc5b1b28e122c20d9c38214226835d934a4f8c94d6b731e93e5`。dump、209 项 TOC 读取及完整 archive 读取均 exit0；恢复 `NOT_RUN`。备份和密钥未进入仓库。

## 2. Owner 最新模型及参数决策

在首次真实提交前执行最新指令，通过正式设置 API 将 ECS 角色与本地实际配置对齐，严格解密成功。没有从 tenant1 复制模型数据库记录或密钥。

| 角色 | Provider / 实际模型 | 本轮实际发送 |
|---|---|---:|
| Query Understanding / Rewrite | SiliconFlow `Qwen/Qwen3.5-4B` | 13 |
| Embedding | SiliconFlow `BAAI/bge-m3` | 13 |
| Rerank | SiliconFlow `BAAI/bge-reranker-v2-m3` | 13 |
| Cheap | SiliconFlow `Qwen/Qwen3.5-4B` | 12 |
| Expensive | DeepSeek `deepseek-flash`，保留原 DYNAMIC 升级合同 | 0 |
| 独立 VLM | SiliconFlow `Qwen/Qwen2.5-VL-72B-Instruct`，与本地角色对齐 | 0，未授权执行 |

生成设置为 `temperature: 0.7`、`max_completion_tokens: 0`；**0 是有限运行时默认值哨兵**。快速模式解析为 2048，智能模式每轮默认解析为 4096。真实快速生成 wire 为 `temperature=0.7 / max_tokens=2048 / enable_thinking=false`，没有发送 0 或设置无限输出。QU 保持独立 `temperature=0.3 / max_tokens=150 / enable_thinking=false`。没有另加思考预算。

本地参考是当前 tenant1 配置和正式只读装配；当时本地 API/UI 已停止，不冒称本轮做了本地浏览器测试。智能模式参数解析通过离线测试；当前正式 smart API 仍为 501，真实智能问答 `NOT_RUN`。

阈值0.3、原 Top1 floor0.15、RRF/MMR/Merge/Parent/TopK、Prompt、DYNAMIC 评分与升级规则保持原样。A/B/C 只分别切换既有实验开关。

## 3. A9：实际质量与首次失败层

分数均为0–1。指标以完整应用答案（含 Sources）计算，没有为了匹配 Gold 裁剪答案；事实正确性另行审查。SciFact/MIRACL 没有本批官方生成答案 Gold；MML 未运行官方 GPT 评分；不合并为跨数据集“准确率”。

| A 样本 | 适用成绩 | 证据与答案结论 | 最早主要问题 | Token / POST秒 |
|---|---|---|---|---:|
| SciFact1304 | Search文档Recall@10/MRR=0/0；BM25=1/.25 | fallback 生成无输入支持的药理细节，事实 FAIL | 必要来源虽被原始检索找到，RRF chunk57/92未进入Top30；之后 Rerank 全过滤 | 15,227 / 6.650 |
| MIRACL5704915#0 | Search及Context Recall/MRR=1/1 | 海淀区关系有实际来源支持，事实 PASS；无官方QA准确率 | 未见必要事实丢失 | 7,880 / 3.807 |
| LongBench DSM | 中文F1=.260870 | “影像星下点分辨率优于5m”原文和答案一致，事实 PASS | 无；完整中文句与Sources使字符串指标较低 | 8,593 / 5.127 |
| LongBench DuReader | ROUGE-L=.255639 | 原因类别覆盖，限定词及正常范围解释有误，PARTIAL | 正确事实已送达，生成丢限定条件并误述0–40范围 | 16,885 / 9.026 |
| Hotpot Leyden/Tribometer | 完整答案EM=0，F1=.190476 | 2/2必要来源、3/3支持句；Pieter van Musschenbroek核心事实 PASS | 无必要事实丢失；纯文本Sources漏列Tribometer文件 | 8,103 / 6.019 |
| Hotpot Frost Place | EM/F1=0/0 | Context仅1/2来源与1/2支持句；识别研讨会但缺地点，明确不足，PARTIAL | 地点来源在Top30，raw分.0006216/.0038396被Rerank过滤 | 6,612 / 7.746 |
| MML0434 | 项目EM=0，字符F1=.081522 | 拒答，未答Gold；来源身份存在不代表章节事实存在 | 实际30个passage没有Coding time关联；最高.017578为imports代码，全部低于floor | 8,402 / 16.385 |
| MML0687 | 项目EM=0，字符F1=.040816 | 未答median，错把平均C−/C视为均C，事实 FAIL | 图表结构解析噪声及生成未正确使用已送达的原文/分布 | 9,119 / 9.892 |
| MML0958 | 项目EM=0，字符F1=.114833 | fallback答mirror/water，缺真实实例证据且不同Gold，事实 FAIL | max .049305，全部过滤；metadata没有两种反射面事实 | 7,259 / 10.300 |

9/9 应用终态完成；6题有正常证据 Context，3题是 SearchNothing metadata fallback。上述 PASS/PARTIAL/FAIL 是事后事实审查，不是九题统一官方准确率。

### 已证明的完整链路

DSM：Chunk `e5cf40df-01e7-4429-a669-b7d31b302a58`，doc `cc6ea68c-f3e7-48ce-9d14-0b73e77584c1`，version `934dc14a-d0c8-4c41-a0d1-1499c17e0cf9`，源 SHA `a00fca96fed4629eb7d38db5b18b25f50d5b533f8e420ccb2f72366d402fa654`。6块最终 Context 中选定正文经 XML/HTML 实体还原后逐块进入实际模型 wire；不能以重新渲染 wrapper 的字符串不同误报证据丢失。wire SHA 同时匹配发送账本和 PG；Provider原答=SSE=PG=history=result，来源正常HTTP回读一致。

全部12个完成答案都通过原始回执/SSE/PG/历史逐字对账，未发现 `length` 截断或 Expensive 升级。公开结构化引用按原配置关闭，`knowledge_references=[]`；**不能把原文件/Chunk回读通过说成“可点击公开引用通过”**。纯文件名 Sources 的完整性单独记录。

MML0434 的 ECS doc 与旧冻结 UUID 不同，但实际原 PDF SHA 与冻结原件一致；评分侧按真实 SHA/KB/版本映射别名，未改 Gold。Pew 复核完整物理28页/印刷26页：Trump average C−、Clinton C，原文已有部分分布；未把 average 改叫 median，也未把物理页换成未知基制的官方 Gold 页指标。

## 4. B/C：质量对照与混杂

| 对照 | Context变化 | 真实答案与必要事实 | 调用 / Token / POST秒 |
|---|---|---|---:|
| B0434 | 未完成MMR/Context；不能推定最终保留 | 容器OOM，本地执行中断；无生成，不计模型质量分 | 3 / 7,621 / 未完成 |
| B0958 | 0→1块；Context 0→1,734字符 | 保留合法低分红外/微波表格，未含Gold两个实例；答案转为说明不足，未答对，Sources写“无” | 4 / 7,573 / 7.488 |
| C Leyden | 8→8块；5,669→5,847字符；Graph加1块 | 必要句仍3/3；新增206字英国海军电容单位原文是问题噪声，未增加发明者事实 | 4 / 8,302 / 13.744 |
| C Frost | 2→3块；2,632→3,387字符；Graph实际加0块 | 三路径为作者/诗集关系，没补地点；仍1/2支持句，安全未答位置 | 4 / 7,127 / 5.109 |

B0958 实际 Rewrite 的有效字段、各召回通道顺序、Top30及Rerank完整请求体与A相同；QU原JSON仅空白不同。22/30分数逐值相同，最大差约.0000531，保留模型响应波动，不能使用旧分数替代新回执。B0434 的 query/30 passages亦完全同输入，Top1 index11相同；但仅评分完成，后续未执行成功。

C 两题的实际 Rewrite、候选顺序/集合均变化，共同Chunk正文SHA未变。**存在QU/检索输入混杂，不能把答案或时延差异全部归因于Graph。** Graph预检19份证据/28条边、来源12文档，均有合法版本、原文和quote偏移；本轮没有建图调用。合法原文关系不等于回答当前问题有用。

**决策：Top1 `KEEP_EXPERIMENTAL`；Graph `KEEP_EXPERIMENTAL`。本批没有足够证据推广默认配置。** B仅一题完成且未恢复必要实例；C未见可验证核心事实增益。D未运行。

## 5. OOM根因、最小修复及1GiB配置

B0434在第三次Rerank回执已结算后约0.32秒被内核MEMCG OOM杀死：容器 `OOMKilled=true / exit137`，anon RSS约494,620KiB。宿主随后available约2.5GiB；该值是故障后的快照，**不是评测期间谷值**。API、正常HTTP客户端的 `docker exec` 和健康检查处于同一512MiB cgroup；没有逐进程连续profile，不能断言只有MMR或客户端导致峰值。

保存原 `STOP` 账本、SSE `SUBMITTED`、PG pending、driver故障回执；实际仅三次Provider=2,395+19+5,207=7,621 Token，全部可信结算，没有第四生成发送或UNKNOWN。独立审计确认容器已死、PG回执/wire/usage一致后，只关闭未用授权空间；三条entry及held完全不变，保留原stop_reason/previous_status。未改PG状态、旧快照或重发0434。

工具最小增加问答前 `docker update --memory 1g --memory-swap 1g` 及实际读回检查；后三个未运行实例均确认1,073,741,824 bytes并完成。之后Owner明确要求日常也统一：持久化 ECS Compose override 的API/Worker限额，并将仓库 `deploy/s8/compose.yml` 共享app anchor设为1g、memswap1g。正常Compose重建API/Worker后，实际1GiB、同一身份/cloud=false；PG/Redis资源保留。重启前后Provider行数12628、旧UNKNOWN2完全一致，新增模型调用0。

首次零云内存验证工具将Compose JSON数字字符串与int直接比较而exit1，保留错误；只读确认值为字符串`"1073741824"`后修正类型校验，重建及回读exit0。没有将工具类型错误记成模型失败。

本轮未连续记录主机内存谷值。按最新要求，后续执行器memory-v4已增加每秒被动读取MemAvailable/SwapFree与容器cgroup current/peak/limit，保存样本、最低值和发生UTC时间；缺失字段为null/UNKNOWN，不能记0。12项定向离线测试exit0；ECS正常空闲API短自测3.2秒、5样本、无采样错误、无模型调用，确认主机/容器字段可读。该空闲窗口最低MemAvailable为2,712,477,696 bytes（约2.526GiB），**不是本轮真实RAG评测谷值，不能用于声称并发容量**。本轮真实问题谷值仍 `NOT_MEASURED`，没有补发问题。默认1秒记录的是采样谷值，无法保证捕捉所有亚秒瞬间；cgroup peak另列。已实测v2/v3工具原字节同时保留，未来v4不冒充本轮执行版本。

## 6. 调用、结算与证据

| 阶段 | 调用 | 输入Token | 输出Token | 总Token |
|---|---:|---:|---:|---:|
| QU / Rewrite | 13 | 30,511 | 438 | 30,949 |
| Query Embedding | 13 | 199 | 0 | 199 |
| Rerank | 13 | 63,990 | 0 | 63,990 |
| 最终生成 | 12 | 21,298 | 2,267 | 23,565 |
| **合计** | **51** | **115,998** | **2,705** | **118,703** |

累计13次POST、12完成、1本地OOM；51调用全部SETTLED。父授权和13个child均CLOSED，撤回未用92次/281,297 Token；Owner参数更新前的零调用旧plan单独CLOSED，额度没有叠加。新增UNKNOWN0；历史2条UNKNOWN完整行摘要仍 `82002b507d1e099536c161f9c3051349`，未释放、修改或重发。

A9 HTTP提交至终态耗时合计74.952秒，平均8.328秒，median7.746秒；包含完整RAG调用，不包含容器启动、来源回读和运维等待。Provider timing记录发起至响应头/读完；DNS、TCP、TLS、供应商排队不可可靠拆分，首raw byte也不冒称首个有效内容Token。

最终原始证据包：44,669,517 bytes、1,460项manifest、SHA256 `03da6d95f22e0e99cf377dbcfb0e08c84b325ee8ae35b3e07aa912edca30ef5c`。ECS路径 `/opt/rag-s8/s8-1-20261020/exports/evidence-20261020T014829Z.zip`；本地 `E:/RAG quention/var/reports/s8-1-ecs-20261020/final-evidence.zip`。已逐文件SHA/长度核验，保留全部首次失败，原始包不上传Git。索引和评分审查进入仓库；镜像/覆盖源码、逐请求输入、原始回执、SSE、PG及HTTP来源在manifest中可定位。

Omni本轮新OCR/QA=0。24页历史解析回执另列：3页项目文字NED映射、21页NOT_SCORABLE；不冒称官方QA/布局/表格成绩，也不把历史Tesseract/P10数值当作当前MinerU新增成绩。

## 7. 实际命令与退出码

完整argv和回执见机器结果与evidence。**未实现的命令不得报告通过。**

| 命令/范围 | 退出码与结果 |
|---|---|
| `python3 /opt/rag-s8/state/full-corpus-status.py`、`status.py`及本轮只读身份预检 | 0；来源/索引/旧授权健康 |
| PG17 `pg_dump`、`pg_restore --list`、`pg_restore --file=/dev/null` | 全0；没有执行恢复 |
| `E:/RAG quention/.venv/Scripts/python.exe -B -m pytest tests/weknora_port/test_conversation_configuration.py tests/weknora_port/test_s8_1_ecs_acceptance.py -q -p no:cacheprovider --basetemp E:/RAG quention/var/reports/s8-1-ecs-20261020/controller/pytest-final-captured` | 0；61 passed，包含18项工具测试，不能相加；纯离线/合成 |
| `python3 …/ecs_case_driver.parameters-v2.py --case-id <冻结case_id> --arm A --execute` | 9题各0；每题一次POST；全部实际case_id/argv在raw包 |
| 同一driver `--arm B` 的0434 | driver2；内部客户端137/OOM，保留失败，未重发 |
| `python3 …/ecs_case_driver.memory-v3.py --case-id <冻结case_id> --arm B/C --execute` | 后续3题各0；实际1GiB读回 |
| `a_baseline_review.py --output …/a9-independent-audit-v2.json`、`finalize_a9_review.py`、`b0434_partial_review.py`、`final_bc_review.py` | 全0；只读保存证据后评分，无模型调用 |
| memory-v3 AST/内存编译/新增11行/执行顺序/3个合成限额用例 | 0；不替代真实实例读回 |
| `python -B -m pytest tests/weknora_port/test_s8_1_ecs_case_driver_memory.py -q -p no:cacheprovider --basetemp …/pytest-memory-v4-1` | 0；12 passed，0.41秒；只验证新被动采样；未重跑旧61项 |
| `remote_memory_sampler_selftest.py`（只导入MemorySampler，现有空闲API采样3.2秒） | 0；Linux /proc/cgroup真实可读，0问题/0模型调用，非RAG负载验证 |
| 正式Compose `config --format json`、`up -d --no-deps --pull never --no-build api worker`及日常内存回读 | 最终全0；首次类型校验exit1另存；0模型调用 |
| `remote_final_audit_and_close.py`、最终zip导出与独立manifest核验 | 全0；父授权CLOSED、数据与旧UNKNOWN不变 |

没有运行全仓测试、六套全量测评、smart真实请求或新的视觉解析。本地实际 `git diff --check` 退出0；交付检查退出0，13份JSON可解析、28个交付文件敏感值模式检查无命中，Compose仅请求的内存项变化，51次/118,703 Token与终态一致。回执在evidence中；该检查没有新增模型请求。

## 8. 修改文件、保留问题与下一步

实现改动：`conversation_configuration.py`、`runtime.py`及对应定向测试负责0哨兵有限默认值；`scripts/s8_1_ecs_acceptance.py`与其测试负责正式身份/配置比对、受限发送、被动阶段证据；一次性case driver和离线scoring脚本用于可复查执行；Compose只调整app内存。未改RAG算法、Prompt、来源结构、数据库Schema或索引。

原主工作树HEAD仍 `855e2d32bf85f50fbb9fa12ace9b0a51e1adbf67`。18个受保护路径逐SHA不变；其外仅保留本次Owner要求的两个参数源码改动。S8独立分支的本轮相关文件统一本地提交；完整交付SHA见最终消息和外部 `delivery-version-receipt.json`，本报告不写伪造的自引用commit哈希。未Push、合并或打Tag。

建议后续优先顺序（Owner决策，未自行批准延期）：

1. **P1质量：证据不足时的自由知识fallback和来源说明。** Sci/0958存在无输入支持的详细断言；Sources纯文件名存在漏列/“无”。不以此推导所有fallback都应固定拒答。
2. **必要证据选择：RRF Top30与低分第二来源。** Sci在融合截断丢来源，Frost在Rerank丢地点；Top1和本批Graph均没恢复所需完整事实。分别处理，不全局降阈值。
3. **解析与生成的数值/限定词使用。** Pew有图表噪声与原文冲突，DuReader正确事实到达后生成仍误述。避免用新Prompt掩盖解析问题。

S8.2建议仅从冻结40题中减去本轮A9，剩余31个候选（Sci7/MIR7/Long6/Hotpot6/MML5）另列机器清单，尚未运行。保守最大341次Provider、3,100,000 Token（每题11次/100k）用于Owner审批讨论；本轮每正常题4调用只是经验值。留存材料缺少历史UNKNOWN权威QID清单，已有4个可能重叠项单列，**须先核实保护集合与新授权再发送**，不能借新request_id重发旧未决操作。

本轮可确认ECS链路可运行，仍不具备“直接无条件开始S8.2”或默认推广B/C的依据。完成报告、本地提交后停止。
