# P11 — MinerU 真实 RAG A/B：恢复执行与有限收口

日期：2026-10-16。**两道 MML 已完成可比较的真实 KB 向量检索 A/B；取得一例有依据的改善及一例未解决失败。Omni 仅完成有限接线诊断，未完成成对向量问答。** 不宣称全部验收PASS。

机器结果：[P11_MINERU_REAL_RAG_AB.results.json](P11_MINERU_REAL_RAG_AB.results.json)。证据根 `E:/WK/run/p11-real-20261016`，710个证据文件的哈希/大小见 `evidence-manifest.json`。原预算阻断版保留在Git提交 `770c6d955bcdf420af2af9b05fc392d05a1e1649`，并原样复制为证据根的 `P11-blocked-original.md/.results.json`；P8/P8.1/P9/P10冻结结果没有改写。

**未实现的命令不得报告通过。** 真实模型、真实服务、离线测评工具与未执行项分别记录。

## 1. 授权恢复与本轮围栏

Owner本轮明确批准本地执行上限增加600,000，最多1,000,000→1,600,000；未用部分不能作为后续实验额度。Owner对当前SF模型的不限使用授权保留，这个数字仅为本轮有限实验上界，不是Owner或供应商全局硬额度。

新增独立、明确调用的 `scripts/weknora_port_p11_fence.py`，固定grant `P11-20261016-SF-600000`。它在与正常Token门相同的月度advisory锁下核对PG/database、原account/provider/month、carry14、原approval_source及blocked状态，再CAS更新现有月行。没有修改正常P1SendGate/TokenBudgetStore守卫、换账户或新建月账。

| 操作 | 真实PG结果 |
|---|---|
| open | limit1,000,000→1,600,000；可预留占用606,147 |
| close，停止本轮进程后 | limit收口为 **1,200,898**，恰好等于已结算＋UNKNOWN占用＋carry14 |
| 未用空间 | **399,102已撤回；可预留空间0** |

收据目录：`E:/WK/run/p11-20261016/fence-600000`，保存不可覆盖的Owner intent、before/precommit/commit确认、全部历史call ID/行哈希与open/close收据。相同grant不能再次增额，收口后的旧160万配置与月行冲突；没有给后续任务留用本轮余量。未将UNKNOWN当免费或释放。

原账户 `siliconflow-owner-main / siliconflow / 2026-10-01`、原approval/carry身份保持不变。新权威配置只用于本轮API/Worker，仍走真实 `ApprovedTokenAuthority → ServerPermissionResolver → P1SendGate`。配置中的有效期续至本轮运行所需；没有读取或输出密钥正文，没有DeepSeek请求。

## 2. 源码、固定输入与实际范围

- 工作树 `E:/RAG-Worktrees/weknora-python-port-20261013`；分支 `codex/weknora-python-port-integrate-image-20261014`。
- 实际API启动SHA **b33369d86dfbacc08f1762a5d8a95e01e0bdcfbd**。其生产后端125个Python文件与P10/P11阻断版逐字哈希一致；`runtime-source.json` 保存实际指纹。state中的770c6d9是增额提交前的冻结时间标识，不冒充运行SHA。
- 评分器提交 **74b0bd7361400d397783162c1abf2a2298913f5f**，未改变运行中的后端。
- 原 `E:/WK/run/p11-20261016/frozen.json` 字节不变，问题、原件SHA、Gold、Chunking、Embedding/Rerank/Cheap模型、Prompt与检索阈值均未优化。
- 固定模型：SiliconFlow `BAAI/bge-m3`、`BAAI/bge-reranker-v2-m3`、`XingChenAGI/Xing4.0-29B`。模型身份由实际账本核对。
- 相同原件：cat68页SHA `40a74d3fc5e73c81465206a4043c19b850daf66fc5617756f5894d25ad054b4b`；b3 21页SHA `43be9592854c7da10bf011335faec017b726ff2e23bb4ccae7e8fe88d7801f22`；Omni06/10/14三张原图；普通Pew45页。全部原始图片/PDF SHA见冻结副本及11/11 HTTP source回读。

两道原官方问题及原Gold没有修改：0434询问coding time介绍的数据集；0959询问幻灯片介绍的远距离通信方式。三个Omni问题沿用原冻结“请根据这页资料概括主要内容，并引用原文证据。”没有官方QA Gold。

### 实际配置阻断及一次修正

首轮错误地传入 `document_scope`，触发现有正常的**直接文档加载**语义，跳过Query Embedding/向量检索。这8次是真实HTTP及适用的真实模型调用，完整回答、失败、Token和引用均保留，但**不能当作要求的完整RAG A/B**。

发现后只修正测评Scope：使用两组内容相同的隔离MML KB（每组相同cat＋b3两份来源），`document_scope=[]`，在正常KB查询入口补做0434/0959各一对，共4次。没有改模型、问题、Gold、索引、阈值或Prompt，没有重建已成功的索引，没有重发任何UNKNOWN请求。

总问答尝试严格为 **12：首轮文档加载诊断8＋正式KB向量复核4**。两种合同分开保存：根目录A/B与 `vector/A/B`，不混成绩。由于修正占用了剩余4次，B组三个Omni不追加问答；保留真实上传/索引，明确其QA未执行。普通Pew也没有追加向量问答。

## 3. 正常入口与真实存储证据

没有Mock应用API、模型响应或预写完成状态。复用P9被动观察器启动真实FastAPI，正常HTTP创建KB、授权外发、上传、轮询、会话/SSE、原文和引用回读；模型传输经过真实Token门。

| 项目 | 实际结果 |
|---|---|
| 原件与文档 | 11/11原件SHA回读匹配；11份新文档最终completed |
| 切块与索引 | **295 chunks / 295已启用索引行，维度1024**，真实PG/pgvector |
| 解析资产 | **219个**真实文件，SHA全部核验；原件/sidecar/crop与document/version绑定 |
| A图片任务 | 独立真实Worker完成3个Tesseract任务；OCR结果保存，Caption未配置视觉模型时保持unavailable，不冒充描述 |
| B MinerU | 4.0.12 Basic/ocr在正常API ingestion运行；按P10设计跳过旧OCR任务队列，未假称B由旧图片Worker解析 |
| 正式查询 | 4次KB复核均有真实Query Embedding回执；实际向量检索、适用的Rerank、Context与Cheap/固定拒答均留证 |

普通PDF对照：Pew默认路径162个原文块、70,220 Unicode字符与P10原默认正文的每个对应区间完全一致，SHA `89e9e2fa069a6973984da116565ea7b993de7921f216896abfe09e1a60e01c11`；索引真实完成，并执行一次真实直接文档加载回答。结论为解析/切块/索引无退化；**Pew向量问答对照NOT_RUN**。

解析统计不包含把模型生成摘要当原文。295行包含既有正常入库后处理；原文块和生成内容的来源身份仍由现有引用机制区分。

## 4. MML正式KB向量A/B：独立维度

| QID/组 | 关键解析文字 | Search关键文字 | Rerank保留 | 最终Context | 回答事实要点 | 引用 |
|---|---|---|---|---|---|---|
| 0434 A | 无，只有图片占位 | 无 | 本轮策略未触发真实Rerank | 无关键证据 | 错答CIFAR-10，并编造材料支持 | 1条身份合法，但来自b3负例文档，不支持答案 |
| 0434 B | 有Kaggle Cat vs Dog dataset | **命中** | **未保留** | 空 | 固定拒答 | 无 |
| 0959 A | 无，只有图片占位 | 无 | 本轮策略未触发真实Rerank | 无关键证据 | 明确缺少OCR，未给出通信方式 | 无 |
| 0959 B | 有radiowaves/ionosphere/microwaves | **命中** | **保留** | **含关键证据** | **回答无线电波、电离层折射及微波卫星通信，要点与Gold一致** | 1条合法、来源相关，实际定位回读通过 |

Source身份Recall不等于关键证据召回。A即使检索到PDF ID，仍没有文字；B0434即使Search命中正确文字，仍没进Context。A的两次Rerank未执行如实标NOT_RUN，不用B的模型回执填补A。

### 0959：真实贡献成立的具体证据

B的原文chunk `df6098cb-68e6-4546-b9e0-8682b4bf19e9`，Unicode区间 **[10575,11061)**，物理页17/page_idx16，包含真实段落：电离层折射radiowaves、microwaves向卫星发信号。回读包含document/source_version、原PDF SHA、block/index/JSON pointer、归一化bbox、middle_json SHA与原件source URL。

该派生文字在最终Context、准备消息及**实际HTTP发送body**均存在；发送body哈希与可信账本RESPONSE_RECEIVED一致，finish_reason=stop。`vector-score.json` 的 `derived_text_delivery=PASS`，不是仅因引用合法就判送达。offset是派生Unicode字符区间，不是PDF字节偏移。

### 0434：未解决的是Rerank/Context质量

B原文及Search都找回数据集名称，但真实Rerank返回的最高分约 **0.00203365**，低于现有 **0.3** 阈值，最终Context为空，系统按原规则固定拒答。这是本轮实际质量失败；没有证据证明适配器错误丢失候选，不修改阈值或反复重问。

A0434的引用ID/KB/版本/offset/quote确实可回读，但引用的是b3文档；它与正确数据集问题无关。引用合法性、Gold来源相关性与语义支持分别记录，不能用“引用合法”洗掉错误回答。

### 原Gold评分（保留完整答案）

| 正式样本 | 项目全文normalized EM | character F1 | 补充解释 |
|---|---:|---:|---|
| 0434 A | 0 | 0.1217391 | CIFAR-10事实错误、引用负例来源 |
| 0434 B | 0 | 0.4081633 | 固定拒答；字符重合高不表示答案更正确 |
| 0959 A | 0 | 0.0658979 | 缺证据，未回答Gold要点 |
| 0959 B | 0 | 0.1851852 | 人工核对核心通信要点正确，但完整中文解释与简短Gold不完全相等 |

这是沿用现有评分代码的项目诊断，非官方GPT归一化裁判。只做两题，不能推成MML整体准确率；无额外付费judge调用。正式4条系统响应中3次真实Cheap回答、1次固定拒答；固定拒答也留在适用答案分母中。

正式阶段2条现有引用均身份/原文/offset有效，1条为Gold相关来源、1条为同KB负例来源。没有引用的题单列，不能只用现有引用作完整性分母。

## 5. Omni及首轮诊断的实际限制

三张Omni图片两组均完成真实解析、入库、索引；A通过独立Worker，B通过MinerU正常上传。A三次原冻结诊断都走了文档加载路径，Rerank后最终Context为空，固定拒答；没有证明OCR进入回答模型。

B三张图片没有新增问答，原因是首轮配置错误后需要一次4问修正，12次尝试上限已满。因此：**Omni成对KB向量问答、B OCR实际进入回答wire、官方QA分数均NOT_RUN**。不以P10的3页NED或本轮合法引用替代这些指标，不补造QA Gold。

首轮8次全部保留在 `diagnostic-score.json`。例如原直接文档模式A0434曾答出Kaggle猫狗、B0959曾正确且有2条引用，但它们不进入正式向量A/B成绩。没有选择更漂亮的答案作为正式成绩。

## 6. 实际调用、Token与延迟

| 项目 | 本轮实际 |
|---|---:|
| 问答尝试 | **12**，其中8文档加载诊断、4正式KB向量 |
| 真实回答模型调用 / 固定拒答 | **7 / 5** |
| 全部供应商响应 | **94**，不是94道问答 |
| Embedding / chat / Rerank | **58 / 28 / 8**；chat中7次回答、21次辅助 |
| settled / UNKNOWN / reserved | **86 / 8 / 0** |
| 实际input / output / total Token | **128,188 / 2,799 / 130,987** |
| 新增UNKNOWN保守占用 | **76,058**，单列，不当实际Token或免费 |
| 本轮新增占用 | **207,045** |
| 12问HTTP时长合计 | **72.9751秒**，不含上传，不是纯模型时间 |

正式四问HTTP时长：A0434 7.087秒、A0959 7.336秒、B0434 4.270秒、B0959 6.273秒；逐题关联调用数、实际Token和UNKNOWN见机器结果 `actual_call_usage`。上传、Embedding、摘要及其它辅助调用计入全部94条，未混成问答数。

新UNKNOWN均为缺少可结算usage的Rerank响应；回执及正常结果仍保存。原469条账本逐行不变，原37条UNKNOWN/517098占用保留；DeepSeek历史不变，新增DeepSeek0。结束全账户563条记录，月上限1200898与当前占用＋carry严格相等，未用399102撤回，不估算人民币费用。

## 7. 命令与验收证据

cwd为上述工作树，RAG Python为 `E:/RAG quention/.venv/Scripts/python.exe -B`。PYTHONPATH复用工作树、E:/WK/deps/reader、工作树var/weknora-port/runtime-deps；所有新下载/临时文件/日志/资产均在D/E，没有新依赖。

| 实际命令 | exit | 证据 |
|---|---:|---|
| `-m scripts.weknora_port_p11_fence open --approval-file E:/WK/run/p11-real-20261016/owner-approval.txt` | 0 | fence-open.log；真实PG open-after收据 |
| `E:/WK/run/p11-real-20261016/run.py freeze` | 0 | freeze.log；原冻结清单字节哈希绑定 |
| `stage.ps1 -Stage start/configure -Group A/B` | 各0 | A/B-start、configure.log及实际API argv |
| `stage.ps1 -Stage upload -Group A/B -Dataset mmlongbench-doc/omnidocbench`；A另pew | 各0 | 对应upload.log、HTTP/source回执及completed状态 |
| `stage.ps1 -Stage worker -Group A` | starter0 | 独立backend.weknora_port.worker实际处理3个图片任务；PG最终completed |
| `stage.ps1 -Stage query ...` | 8次各0 | 根目录A/B/queries，首轮文档加载诊断 |
| `stage_kb.ps1 -Stage query -Group A/B -Dataset mmlongbench-doc -Qid <原0434/0959>` | 4次各0 | vector/A/B/queries；实际Query Embedding、stages、wire、引用 |
| `stage.ps1 -Stage snapshot -Group A/B -Label before/final` | 各0 | 只读PG快照，原始P9被动观察产物原样复制至vector用于独立评分 |
| `scripts/p11_score_real.py --root-dir .../vector --output .../vector-score.json`；另根目录diagnostic-score.json | 各0 | 四正式结果与八诊断结果严格分开 |
| 命令核验后的PowerShell Stop-Process | 0 | stop-owned-before.json、stop.exitcode.txt |
| `-m scripts.weknora_port_p11_fence close` | 0 | fence-close.log；真实PG close-after收据 |
| `E:/WK/run/p11-real-20261016/closeout_v3.py` | 0 | closeout-v3.log；v3/closeout-audit.json、原行/资产/索引审计 |
| `-m pytest tests/weknora_port/test_p11_fence.py tests/weknora_port/test_p11_score_real.py -q --tb=short -p no:cacheprovider --basetemp E:/WK/run/p11-real-20261016/unit-final` | 0 | **32 passed**，unit-final.log；离线边界测试，不替代真实服务 |
| `E:/WK/run/p11-real-20261016/deliver.py` | 0 | delivery.log、机器结果和710证据哈希索引 |

`stage*.ps1`只是传参/日志包装，完整argv及HTTP intent保留。API仍为真实 `scripts.weknora_port_p9_observed_api` 装配普通FastAPI，并以被动观察器保存正常stage与实际HTTPX body；无业务API Mock。

PG为127.0.0.1:56423、库weknora_python_port_20261013、system identifier **7696088403504697382**；Redis127.0.0.1:56424 DB15、run_id **87b4c2a9f76f9f94b34b5f21fe31a967926f343a**。A/B新KB、独立资产与Redis前缀，原tenant332和加密模型登记复用。收尾本任务API/Worker停止，56429/56431无监听；共享PG/Redis仍healthy。没有操作旧业务服务或flush。

## 8. 失败、改动与决定

实际修复只限测评阻断：document_scope→KBscope一次修正；评分器相应严格核对KB完整文档集合，区分合法负例引用与Gold相关性。所有首轮回答/拒答/费用保留，未重发UNKNOWN。

两个收尾辅助脚本失败也保留：首版用普通Python路径读长资产文件失败，改用现有 `_native_io_path`，没有改ACL或系统长路径设置；第二版误用tenant级账本作全账户基线，改为原全账户469条快照，v3通过。没有改账本来使审计通过。

代码新增仅两套实验工具及测试：P11一次性围栏open/close、只读P11评分器；正常服务入口、模型、Parser、Chunking、检索、Prompt、P1门均未改。无schema/migration/依赖锁变化，无整项目回归。

**建议：保留普通PDF默认，扫描PDF/图片仅显式使用Basic。** 0959已证明真实的“恢复文字→检索→Context→模型→可回读引用”贡献；0434证明更好解析不自动等于更好回答。尚不扩大自动替换或Standard，不宣称通用质量保证。

下一轮最值得单独研究的是0434低Rerank分数与关键证据保留；Omni应另做真正KB向量诊断。不要把A0434幻觉、负例引用或Cheap漏引用全部归因于解析。当前不优化这些策略、不追加调用。

所有378个原保护文件哈希不变；原.serena修改及六个未跟踪文件保留。完成限定执行与收口后停止，无Push、部署、合并或Tag。这是有限实测交付，不是负责人确认的全面上线验收。
