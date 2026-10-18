# RAG-M4.0：六套统一评测首轮准备

**最新状态：M4_PARTIAL_STOPPED_UNKNOWN。tenant 332 正常 API 接线已完成；真实问答 1/11 完成，下一题在最终 Cheap 请求超时并留下 UNKNOWN，按授权停止后续外发。**

下文较早的准备和身份阻断内容均按发生时状态保留；tenant 332 的独立正式 API 接线及真实问答续报见本报告末尾“真实问答执行续报”。

已完成所选样本、Gold、源文件、版本、向量/关键词索引与评分规则的离线冻结。离线准备收口时尚无新的云调用授权，因此未提交问答；不把历史M3/P32分数并入M4。原有8个未提交路径保留。

开始HEAD：5099efe5aae1de1bc5849c7db2f081c32fe22d5c。准备代码提交：ffcab7de9dce21b9692bbd7dd38cd70a96379d08；最终报告提交以本文件所在提交/交付消息为准。固定WeKnora：3e8b0bfc80b845b2d4b2ed683994748741450a97。

## 资料与范围

冻结资料共40个QID（前五套各8）和Omni 24页解析Gold。748是来源单元数，不是QA题数；本轮只重核所选来源文件，不重复审计全部748。全部当前索引均为小子集。

| 数据集 | 可用冻结资料及当前来源覆盖 | 当前选定KB文档/Chunk/向量 | 首轮计划/实际执行 | 可用指标；本轮分数 |
|---|---|---:|---:|---|
| SciFact | 8 QID；8/8标注正来源在选定KB | 11/33/33 | 2/0 | 子集来源Recall/MRR/nDCG；无答案Gold，QA准确率不适用；新分数NOT RUN |
| MIRACL-ZH | 8 QID；8/8正passage有映射 | 13/26/26 | 2/0 | 子集passage检索指标；无生成答案Gold；新分数NOT RUN |
| LongBench-ZH | 8 QID；8/8配对上下文有映射 | 8/281/281 | 2/0 | DuReader ROUGE-ZH、MultiFieldQA-ZH F1分别报告；NOT RUN |
| HotpotQA | 8 QID；8/8正来源有映射 | 24/59/59 | 2/0 | 答案EM/token F1＋子集来源命中；句级support/joint无已验证映射；NOT RUN |
| MMLongBench-Doc | 8 QID；当前两PDF覆盖5/8来源，非5题视觉证据充分 | 2/88/88 | 2/0 | typed Gold的项目EM/字符F1＋独立事实支持；官方judge NOT RUN |
| OmniDocBench | 24页解析Gold、无官方QA Gold；历史3页可靠全文NED对齐 | 24/66/66 | 1诊断/0 | page012既有工程诊断；不计算官方QA准确率；新解析/QA分数NOT RUN |

当前六个KB共82文档、553 Chunk、553条非空1024维向量；同553条索引的关键词正文均非空，BM25与HNSW索引有效/ready。所选11题共13份原件逐一SHA绑定，版本和索引只读核对通过。来源存在不等于Search命中，更不等于答案得到支持。

MML当前统一KB缺Pew来源，对应0684/0685/0687；不声称其他历史KB也缺失。0430/0431/0958有PDF来源不代表所需视觉/全篇计数证据足够，暂不纳入这次少量样本。采用0959常规对照与0434困难题；跨页章节归属仍保持既有不确定性，不把Gold反填正文。

Omni只冻结已有page012问题，不复测M3.5 page007矩阵上传、检索、生成或刷新；历史page012成功也不是M4新成绩。既有OCR/解析版本保持原样，未调用Tesseract、MinerU或云OCR；不将旧索引描述为本轮新MinerU解析。

## 冻结样本与评分

11题为SciFact247/339、MIRACL91294/7049630、LongBench6770736a/51abd206、Hotpot5a7631/5a8ad6、MML0434/0959、Omni page012。前五套各一个常规与已有困难对照；标签仅实验选样，不改生产分类器。

[完整计划](RAG_M4_0_BASELINE.plan.json)包含原问题、原生Gold哈希、来源/版本/文件SHA、限定KB、document_scope=[]、评分器及运行源码指纹。Gold和answer_points仅供离线评价；未来API请求只能包含问题、request_id和真实Scope，不能传理想证据。

分开记录Search、Rerank、Merge/Top-K、Context、实际Provider证据送达、答案、SSE/历史一致性。来源ID命中和精确事实Chunk命中分开；原文、OCR/Caption、Summary身份分开。对格式差异、来源自身矛盾、错误推理、无依据补充无独立证据时保留UNDETERMINED。当前各题所有运行阶段均NOT RUN、指标null、首次RAG失败为null；授权/身份问题发生在运行前，不伪装为模型质量失败。

## 离线准备阶段的阻断（后续状态见文末续跑检查）

1. **BLOCKED_AUTHORIZATION**：M4指令明确不授权新云发送。旧M3.5许可已关闭；全局仍false，无新许可文件，未启动API/Worker/UI。
2. **LOCAL_PRINCIPAL_MISMATCH**：所选六套统一索引属于tenant332 / p8-real-3de2eca0bea340babcde75ce60c9a366，日常wk为tenant1。当前日常API不能借身份检索这些KB。未改port_local_principal、未改tenant、未复制模型或资料。需要先确定正常API的合法统一评测身份，不自动注入实验principal。

旧M3执行器还固定旧HEAD、强制A/B并打开云端、修改实验会话配置；没有照搬它运行。正常FastAPI/RAG主链保留，评分仅复用现有纯函数。没有新建特殊数据集链路、临时公开API或云权限系统。

未来若批准执行，建议总上界为11次完整请求/132次Provider/600,000 Token（包含未决预留），每题一批且每批≤12次Provider/100,000 Token、目标≤40分钟。**这只是未批准建议上界，不是现有额度或预计消耗。** 正常流程的Rewrite、查询/扩展Embedding、Rerank及可能Cheap→Expensive均计入；不得关闭DYNAMIC来凑限额。所有批共享Owner总上限，不通过新文件续额；UNKNOWN立即停止。

## 实际修改、命令与结果

新增 scripts/rag_m40_preflight.py、9项定向测试及冻结计划；生产backend/frontend、模型、Prompt、Rerank、OCR、索引和Gold均无修改。脚本仅文件核验与本机PG READ ONLY事务，校验物理system ID/数据库并ROLLBACK；不导入Provider或旧启动器。它返回OFFLINE_DATA_READY_ONLY，明确区分数据就绪与运行授权。

| 实际执行 | 退出码与结果 |
|---|---|
| Python -B -m pytest tests/weknora_port/test_rag_m40_preflight.py -q --basetemp E:\WK\run\m40-tests-frozen | 0；9 passed in 0.18s，离线/模拟连接测试 |
| scripts/rag_m40_preflight.py --plan docs/milestones/RAG_M4_0_BASELINE.plan.json --config E:\WK\run\p81-retirement-20261018\local.json --output E:\WK\run\rag-m40-20261018\preflight-frozen-final.json | 0；真实只读PG，11题数据绑定通过；真实质量NOT RUN |
| E:\WK\run\rag-m40-20261018\inventory_current.py | 0；六套当前元数据、索引、40QID来源覆盖快照 |
| 既有official_longbench及本地ranking/answer scorer导入冒烟 | 0；固定revision/hash匹配，无答案评分、未装新依赖；纯Python SequenceMatcher仅提示较慢 |
| 原配置wk.ps1 check / status | 各0；PG/Redis正确且运行，API/Worker/UI未启动，cloud=false |
| git diff --check / git diff --cached --check | 0；提交前实际检查 |

完整原样命令见[机器结果](RAG_M4_REPORT.results.json)。首次只读预检将历史Summary缺自身source_version误判为不就绪，首次退出1保留：正式loader从同tenant/KB/doc当前文档JOIN获得版本，工具据此最小修复；显式旧版本、text/image缺版本仍拒绝。P19目录未含后导入B3导致早期覆盖少计，补用已冻结P32文件SHA后为5/8。两者均为诊断工具问题，没有修改业务或历史数据。

**本轮问答尝试0、Provider0、实际Token0、新UNKNOWN0。** 历史账本/Gold/索引保留；本轮没有真实Search、Rerank、生成或新增质量分数，也没有全量六套复测。

## 后续三个优先点与停止

本轮M4授权已取得；待确定合法API身份后，再用这11题得到当前版本数据。没有新运行，不能声称得到新的前三类质量失败分布；历史待验证候选仍是：①Rerank精确/多跳证据丢失，②证据不足时的无依据生成，③视觉及来源覆盖不足。本轮不据此调整阈值、Prompt、OCR或路由。

原始只读证据及复现脚本见[准备证据ZIP](evidence/m40-offline-20261018/preparation.zip)，本地根E:\WK\run\rag-m40-20261018。ZIP不含密钥、本机配置、完整模型参数或源文档。初始离线准备按无授权分支停止；续跑状态见下文。仅本地提交，不Push、部署、合并或Tag。

## 授权续跑检查（2026-10-18）

Owner 已明确批准本轮冻结 11 题：最多 132 次 Provider、600,000 Token；每批最多 12 次 Provider、100,000 Token；仅现有 SiliconFlow/DeepSeek 与原 DYNAMIC。该授权不改变模型与数据权限，也不授权绕过正常 API 身份。

实际日常 `wk.ps1 check` 返回 PostgreSQL system ID `7696088403504697382`、数据库 `weknora_python_port_20261013`，principal 为 tenant 1 / `web_user:local:bba83fda-3749-499b-9b1a-1f42aa2da8a6`，`cloud_allowed=false`。Owner 的 M4 授权已收到，但尚未写入/打开本地执行围栏。用同一正式配置再次运行只读 M4 预检（退出码 0）确认 11/11 数据绑定 READY，API scope 仍为 `LOCAL_PRINCIPAL_MISMATCH`；预检 Provider calls 为 0。M4 冻结知识库所有者为 tenant 332 / `p8-real-3de2eca0bea340babcde75ce60c9a366`。

代码确认正式 `entrypoint.create_app()` 将请求 principal 解析为已启动 runtime 的 principal，正常 `development.assemble(settings)` 从 `port_local_principal` 的唯一 `slot='local'` 读取身份。随后对同一数据库执行完整只读盘点，结果只有这一个 tenant 1 principal；冻结的六个评测 KB 全部属于 tenant 332。当前 daily 配置没有 tenant 332 身份；通过 `assemble(..., principal=...)` 测试注入、修改该数据库行、改动知识库归属或启动旧实验执行器都不构成合法的正常 API 身份。本轮未采用这些方式。原始盘点输出见 `E:\WK\run\rag-m40-20261018\identity-inventory-postauth.json`，重现脚本为 `identity_inventory_postauth.py`。

因此没有启动 API/Worker/UI，没有开启云发送，没有建立或变更额度文件，也没有写数据库、Redis 或知识库。M4 真实问答 0/11、Provider 0、Token 0、UNKNOWN 0；各阶段仍 `NOT RUN`。要继续，需要项目提供一种可由正式 API 解析、且不改变日常 tenant 1 principal 和现有 KB 归属的 tenant 332 身份路径；当前代码没有这类多身份入口。当前只读预检原始输出保存在 `E:\WK\run\rag-m40-20261018\preflight-authorized-identity-20261018.json`。机器记录见 [授权续跑检查结果](RAG_M4_AUTHORIZED_EXECUTION_CHECKPOINT.results.json)。

## 真实问答执行续报（2026-10-18）

本节更新上面的历史阻断状态。复用 M4 正式评测入口与 tenant 332 独立 API；没有修改日常 tenant 1 principal、工作台配置或六个 KB 归属。运行身份为 tenant 332（subject fingerprint `00f2fd3dad2b7419`），PostgreSQL system ID `7696088403504697382`、数据库 `weknora_python_port_20261013`，DYNAMIC，云发送在本次获批执行范围内开启；API 仅绑定 `127.0.0.1:51443`，Worker 未启动。

| 冻结样本 | 状态与真实链路 | 评分解释 |
|---|---|---|
| SciFact `247` | 完整 FastAPI → Search 30 → Rerank → Merge/Context → Cheap → DYNAMIC 升级 → SSE/持久化。预期来源文档在 Search 排名 1、2、4；Rerank 与 Context 均保留 2 个证据块，Context 2,253 字符。Cheap `XingChenAGI/Xing4.0-29B` 以 `length` 截断，按既有升级规则由 DeepSeek `deepseek-flash` 完成，最终 `stop`。答案谨慎说明来源是 TG2 抑制剂结合结构研究，不能支持问题所述的化学损伤结论。 | Gold 是官方文档 qrels，QA 答案准确率不适用；可确认来源证据进入生成并完成端到端持久化，不报告 QA 正确率。 |
| SciFact `339` | Search 30；Rerank 3；Merge 2；Context 2 个证据块、3,040 字符。Query Rewrite、Embedding、Rerank 已结算；Cheap `XingChenAGI/Xing4.0-29B` 请求超时 600 秒，SSE 完成事件为失败 `MODEL_CALL_TIMEOUT`，历史 assistant 消息为空且未完成。 | 没有答案可评分。Provider 已发送但未收到可确认回执，记为 `UNKNOWN`，保守占用 9,468 Token；不重发。 |

实际用量为 9 次 Provider 请求，供应商报告 Token 20,640；另有 1 次 UNKNOWN，保守占用 9,468，累计保守预算占用 30,108 / 600,000。分批上限均未超出：batch-002 为 5 次、12,424 Token；batch-004 为 4 次、17,684 Token 预留/保留。批次累计账本见 `E:\WK\run\rag-m40-20261018\m4-accounting.json`。由于授权规则规定 UNKNOWN 即停止，本轮 M4 评测停止；其余 9 个冻结问题均 `NOT RUN`，不把部分 SciFact 结果称作六套统一基线。

前置的 batch-001 被发送门在 Provider 前以 `LOCAL_EXECUTION_SCOPE_DENIED` 拦截（Provider 0）；batch-003 在本地 runner 导入阶段失败（HTTP 0、Provider 0）。两项均保留证据，不计入 Provider 用量。batch-004 的 API 记录已保存；外层 runner 随后因其汇总变量 `run_status` 未初始化而退出 1。该工具错误不覆盖已保存的 API 超时/UNKNOWN，不构成重试依据。

证据目录为 `E:\WK\run\rag-m40-20261018\batch-002` 与 `batch-004`。批次 API、执行账本及阶段轨迹的 SHA-256 见 [机器结果](RAG_M4_REAL_EXECUTION_20261018.results.json)。API 进程状态为 stopped、退出码 1，端口 51443 无监听；没有停止或清理原有 PostgreSQL/Redis。真实 Edge 页面验收未执行。

结果状态为 **`M4_PARTIAL_STOPPED_UNKNOWN`**。本轮未运行六套其余问题，也未计算 SciFact QA 准确率；如要继续，必须先由负责人决定如何处理本次 UNKNOWN 之后的评测授权与批次。未因超时改变模型、问题、Rerank、路由或索引。机器核对与累计账本见 [真实执行结果](RAG_M4_REAL_EXECUTION_20261018.results.json)。
