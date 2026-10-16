# P8.1 — 剩余接线阻断最小处理

## 当前状态

限定检查已完成。四套已通过的数据集只读核验，新增模型调用0。MML/Omni共新增3次真实问题尝试，任务级1 PASS / 1 FAIL / 1 PARTIAL。Omni第二个投影缺陷修复后的真实供应商链路 NOT RUN；一次允许的复核已用完，不增加第三次Omni调用。不要求6/6全部变绿。

## 1. 起点与冻结边界

- 基线 `2477ecd38a1768f28dd9941e4ed13f515411b570`，分支 `codex/weknora-python-port-integrate-image-20261014`。
- 固定上游 `3e8b0bfc80b845b2d4b2ed683994748741450a97`，未切换。
- P8冻结11尝试：5 PASS、4 FAIL、2 PARTIAL；748来源、40冻结QID、24页解析Gold与22原件处理证据保持原样。P8原文丢失修复295bd0a不重复开发。
- 新产物 `E:/WK/run/p8-1-20261016`。P8历史157个权威JSON/JSONL/SSE文件的hash冻结在p8-history-hashes.json；六个原有未跟踪证据hash另存protected-existing-files.json。
- 无Prompt、TopK、阈值、Parent/Child、RRF、路由改动；无新增模型、OCR/VLM重构、Gold修改、全批次、Push/部署/Tag。

## 2. MML：原失败与8题审查

原row-0430要求统计老虎图片，P8的68页扫描PDF只有图像占位文本；直接加载文档不等于向量检索，原题无真实Rerank。此题分类CAPABILITY_LIMIT，失败保留，不开发视觉计数。

本轮实际调用当前 `ingest_document(DocumentRequest(...))` 离线解析全部3份原始PDF，未调用模型：

| 原始PDF | 当前解析 | 可读文字 | Chunk | 原路径适用性 |
|---|---|---:|---:|---|
| 11-21-16-Updated-Post-Election-Release.pdf | pdf文字层 | 70,220字符 | 162 | 有可用文字证据 |
| catvsdog…pdf | 68扫描页 | 去除图片占位后0字符 | 23 | 原row0430视觉计数不支持 |
| b3m5ka…pdf | 21扫描页 | 去除图片占位后0字符 | 6 | 默认当前路径不提供所需文字 |

全部8题逐题审查见mml-qid-audit.json；仅选择原官方row-0687，问题原样：Who has a higher median campaign grade? Trump or Clinton? 当前真实解析正文连续保留两候选各档比例，无需新增图像语义：Trump A/B30%、C19%、D15%、F35%；Clinton A/B43%、C20%、D16%、F21%。其余题涉及视觉、图表数值关系在当前解析中丢失，或无法用当前文本完整证明不可回答，未从Gold猜答案。

选择在新调用前冻结，SHA `9e8ff657cdebc23da390d334bc4534beec9ff273e6aebe5056d2bf08b53b92b1`。实际整份PewPDF走正常上传，复用既有MML知识库；原扫描PDF不移除，仍是同库候选。查询为空document_scope的KB检索，防止以direct document load冒充完整检索。标准答案只在gold-selected.json评价侧，不进入上传/生成请求。

实际新增代表题1次：整PDF上传completed，Search30→Rerank7→Context7，存在真实query embedding/rerank/answer回执，原文引用1/1正文/ID/KB/version/hash一致。**完整接线PASS，答案质量未通过**。模型回答无法判断中位数，并把部分Trump比例/平均数混淆，未按Gold得出Clinton；项目normalized EM=0、character F1=0.0224。完整原答保留，不提取含答案词来重算分数。HTTP耗时37.949秒，不再重跑该题。

官方归一化仍NOT_RUN，页码基准UNKNOWN，按typed-answer项目诊断记录，不冒称官方MML分数。

## 3. Omni：逐阶段定位及真实阻断

### 原P8拒答：Rerank质量限制

P8显式KB问题的原件hash、doc/source_version、OCR和向量一致。唯一KB为b66d8ae4…，doc为6a50b4aa…，version148c9991…；OCR139字符7行，image_ocr Chunk42b5a365…确实进入enabled1024维索引。

- Search：3候选，summary/text/image_ocr。
- Rerank：实际供应商响应存在，保留0；SSE只有rerank started，没有completed。
- Merge / 原文投影 / ContextBuilder：均未到达。
- 最终：固定拒答，无答案模型调用、无引用。

原rawscore和rewrite未保存，不能推测具体供应商分数。当前源码hash与P8已修复版本相同；无模型的接受OCR分支可通过实际merge和投影，不是295bd0a重复。分类CONTROLLED_DEBT，不修改阈值。详见omni-stage-diagnostic.json；离线夹具分数明确不作为真实分数。

### 本轮冻结文本诊断

从既有真实OCR标题与Change7文字冻结一个PROJECT_DIAGNOSTIC：According to the uploaded knowledge-base page titled 'Changes to English (in America)', what is Change 7 about? Cite the page text.

不使用Omni解析Gold制题或泄漏答案，不修改生产Prompt。实际新问题Search3→Rerank3→Context1。真实Rerank分数0.6320374、0.5653096、0.4766421，达到现有阈值，原image_ocr进入Context；模型已回答并输出原OCR ID引用。

**但首轮严格回读发现BLOCKER**：引用正文241字符，多了merge注入的图片Markdown；GET/chunks/{id}原文OCR为139字符。ID/KB/version虽一致，正文不一致，故不能标完整PASS。证据omni-initial-quote-failure.json，保留首轮失败，不覆盖P8或本轮原答。

限定修复 `d711231d73de6ccb6609453c691d0f8991a1e55f` 将 image_ocr/image_caption 纳入原件投影，校验类型、KB、doc、source_version、offset，正文使用正常read_source_chunk。保持固定merge/排名/Prompt，防止合并Markdown冒充OCR原文。RED12fail/20pass退出1；GREEN86项通过退出0。

### 唯一真实复核及第二个适配缺陷

同一问题/原件/version/账户配置独立复核1次，run `2d9733f9-c714-4220-84d6-d99c016a5bdd`，HTTP31.641秒。Search3→Rerank3→Context1，引用1/1身份/版本/正文/hash一致，旧评分器输出PASS。**但逐阶段复核显示只引用图片占位，OCR未进入模型，任务级PARTIAL，实际回答失败**。旧评分文件保留不覆盖；不把占位引用算OCR成功。

- Rerank真实原始score为summary0.6118978、parent0.3465676、OCR0.3425449；生产适配后parent0.31277927、OCR0.31044496，summary被既有保护排除。
- 固定merge将OCR扩展为父Markdown+OCR；父100字符是该串子串且score稍高，partial_overlap留下parent。**merge输出仍有330字符image_info包含真实OCR**，已有enriched_chat_passage本可将其送入模型。
- 最终原件投影用数据库raw parent替换时，raw.image_info为空，聚合信息从330变0；最终images为空，模型只见占位并拒答。此处为既有适配BLOCKER，本次排名组合暴露；不是模型能力或295bd0a摘要问题，不修改固定merge。
- 第二个最小修复仅保留可信merge的image_info，引用正文仍严格取raw原文；用本次实际OCR/分数经过真实merge→投影→ContextBuilder，证明OCR以标签注入上下文且不混入quote。RED1fail/30deselected退出1；GREEN31pass退出0。**此后真实供应商复核NOT RUN**，不把离线通过倒填真实失败。

Omni无独立问答Gold；额外RAG只测项目接线。P8单页解析文字NED0.12只读复用，不重新计算漂亮分数；官方版面/表格/公式/阅读顺序仍不虚报。

## 4. 当前权限和资源

实际read-only预检PG：127.0.0.1:56423 / weknora_python_port_20261013 / system identifier7696088403504697382；Redis127.0.0.1:56424/15 / run_id87b4c2a9f76f9f94b34b5f21fe31a967926f343a。当前tenant332的22个原件/各Chunk/向量真实可读，见preflight-readonly.json。

复用P8同tenant/subject、加密模型配置、原assets/master.key（实际key文件在P8根目录master.key，不在assets下），新Redis namespace，API56429/origin56430。不清库、不flush、不重复大规模入库。仅新增一份必要的完整MML PDF。

原approved authority有效至2026-10-16 19:47:22（上海），通过原可信配置检查复用；SF账户siliconflow-owner-main、provider、月2026-10、carry14及既有approval identity保持相同。预检累计used349106、carry14，本地额外围栏100万；没有重新命名账户绕开额度。Owner对其他模型授权不取消P1SendGate/usage/预留保护。

Embedding BAAI/bge-m3，Rerank BAAI/bge-reranker-v2-m3，Cheap XingChenAGI/Xing4.0-29B；DeepSeek/VLM为空，不调用不解除阻断。初轮2次问题及1原件上传共产生21条响应：embedding14、chat5、rerank2；已知结算34371 Token，Rerank缺usage保留16221 UNKNOWN占用，原P8 tenant127条账本逐字段变化0。最终独立复核增量另列。

## 5. 四套正式准备核验（新增模型0）

| 数据集 | 状态 | 已有可执行指标 | 范围／限制 |
|---|---|---|---|
| SciFact | READY_SUBSET_ONLY | 文档qrels的Recall/MRR/nDCG | 当前真实索引4原件；没有生成答案Gold |
| MIRACL-ZH | READY_SUBSET_ONLY | passage qrels检索指标 | 当前4passage；不能称全MIRACL，缺引用/低分保留 |
| LongBench-ZH | READY_SUBSET_ONLY | multifieldqa_zh中文F1、dureader ROUGE-L分别计算 | 当前2完整context；旧dureader无引用不阻止答案指标测评 |
| HotpotQA | METRIC_PARTIAL | 答案EM/token F1、子集文档检索 | 当前1题原始10context，支持句/joint缺真实句级映射NOT_RUN |

四套均至少已有1次真实完整链路PASS。原FAIL和PARTIAL必须保留在后续测评分母中，EM0、低F1、漏引用均为质量结果，不是开始已适用指标评价的修复门槛。完整语料文件存在不等于已建好可验证正式索引；prepared模拟ID不复用为正式索引。

完整四套矩阵和证据hash见four-dataset-readiness.json；本轮不扩大语料、不启动正式批次。

read-only preflight当前tenant332共22原件，四库分别4/4/2/10。冻结题Gold正例来源覆盖：SciFact两题各1/1、MIRACL两题各1/1、LongBench两题各1/1、Hotpot一题2/2；只是当前小子集可执行证据，不等于整套冻结40QID或官方全语料已经覆盖。矩阵SHA256 `2dd78650d107db24ae275824d539288ee86d702ecd24a96bc05f2d897844cf3d`；preflight SHA256 `609c37fef8b87ad1d095ed0fe99046297e3982a1fb9dfd41ad0b37a753466051`。

## 6. 命令、测试、调用和最终状态

### 新增尝试与费用

| 本轮尝试 | Search/Rerank/Context | 旧评分器 | 任务级 | 实际结果 | HTTP秒 |
|---|---|---|---|---|---:|
| Omni新文本诊断 | 3/3/1 | FAIL | FAIL | 已回答，OCR引用正文不一致 | 55.614 |
| MML官方row0687 | 30/7/7 | PASS | PASS | 接线完整，答案EM0/质量FAIL | 37.949 |
| Omni唯一同题复核 | 3/3/1 | PASS | PARTIAL | 合法占位引用，实际OCR丢失、拒答 | 31.641 |

任务级1PASS/1FAIL/1PARTIAL；旧评分器2PASS/1FAIL原样保留，差异有明确阶段证据。P8原11尝试仍5PASS/4FAIL/2PARTIAL，不修改历史分母。三次完整HTTP合计125.203秒，非模型推理净耗时。

最终新增真实SF响应30：Embedding20/Chat7/Rerank3（包括现有摘要、查询理解/重写等）；27笔settled，已知实际 **38,375 Token=input37,505+output870**；3笔Rerank usage缺失，保留 **UNKNOWN占用17,091**，不作为免费或实际Token。reserved0；DeepSeek新增0、历史阻断未解除。初轮21响应实际34371/UNKNOWNheld16221；复核9响应实际4004/UNKNOWNheld870。最终全量差集见closeout-audit.json。

### 实际命令与退出码

工作目录为本报告所在仓库根；Python始终 `E:/RAG quention/.venv/Scripts/python.exe -B`。共同环境为PYTHONPATH三路径：仓库根、E:/WK/deps/reader、仓库var/weknora-port/runtime-deps；TEMP/TMP均E:/WK/run/p8-1-20261016。**本轮真实HTTP、PG/Redis和SF模型；不是API mock，也不是浏览器验收。** observer旁观既有真实调用，不发额外探测。

以下命令实际执行且各exit0。查询exit0只代表证据采集完成，场景状态以上表为准。命令日志/exitcode在证据目录。

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B scripts/weknora_port_p8_1_real.py prepare
& 'E:/RAG quention/.venv/Scripts/python.exe' -B scripts/weknora_port_p8_1_real.py start
& 'E:/RAG quention/.venv/Scripts/python.exe' -B scripts/weknora_port_p8_1_real.py configure
& 'E:/RAG quention/.venv/Scripts/python.exe' -B scripts/weknora_port_p8_1_real.py query --dataset omnidocbench
& 'E:/RAG quention/.venv/Scripts/python.exe' -B scripts/weknora_port_p8_1_real.py upload --dataset mmlongbench-doc
& 'E:/RAG quention/.venv/Scripts/python.exe' -B scripts/weknora_port_p8_1_real.py query --dataset mmlongbench-doc
& 'E:/RAG quention/.venv/Scripts/python.exe' -B scripts/weknora_port_p8_1_real.py snapshot --label after
& 'E:/RAG quention/.venv/Scripts/python.exe' -B scripts/p8_score_real.py --run-dir E:/WK/run/p8-1-20261016
& 'E:/RAG quention/.venv/Scripts/python.exe' -B scripts/weknora_port_p8_1_real.py stop
```

唯一复核的完整脚本E:/WK/run/p8-1-20261016/recheck.py已保存；同一Python逐次执行prepare/start/configure/query/snapshot/stop，各一次、均exit0，对应recheck-*.log和exitcode。评分 `python -B scripts/p8_score_real.py --run-dir E:/WK/run/p8-1-20261016/recheck` exit0。原件解析审查mml-parse-audit.log、read-only预检preflight.log均exit0。最终 `python -B E:/WK/run/p8-1-20261016/closeout_audit.py` exit0。未实现的命令不得报告通过。

定向测试实际命令（完整Python路径同上）：

```powershell
python -B -m pytest tests/weknora_port/test_p8_1_observer.py -q -p no:cacheprovider --basetemp=E:/WK/run/p8-1-20261016/observer-test-temp
python -B -m pytest tests/weknora_port/test_mvp_source_evidence.py tests/weknora_port/test_references.py tests/weknora_port/test_reference_metadata.py tests/weknora_port/test_search_image_completion.py -q -p no:cacheprovider --basetemp=E:/WK/run/p8-1-20261016/image-projection-final-temp
python -B -m pytest tests/weknora_port/test_mvp_source_evidence.py -q -p no:cacheprovider --basetemp=E:/WK/run/p8-1-20261016/parent-enrichment-green-temp
```

依次3pass/86pass/31pass、exit0；后两批有重叠，不累加为独立测试数。对应observer-test.log/image-projection-final.log/parent-enrichment-green.log。RED失败日志、原始fixture缺方法失败一并保留，不隐藏。没有全库回归或其他四套模型重跑。

### 版本、资源和下一轮

本轮代码提交304defd0184c09003e59236dcf2bb4d3f530b695（真实runner/旁观器/3个测试）、d711231d73de6ccb6609453c691d0f8991a1e55f（图片原文引用投影）、4b45c70cf6b5c9f138312143ae1ad1d44db415ba（parent聚合OCR保留）。生产共享文件仅runtime.py；无迁移、依赖/锁文件、入口、配置Schema变更。整合顺序为原件引用修复→parent image_info保留→报告/评测脚本。未自行合并、Push、部署或Tag。

| 文件/函数 | 本次职责 |
|---|---|
| backend/weknora_port/runtime.py / _project_source_evidence | 仅有的生产修改，引用原文校验与可信图片上下文保留 |
| retrieval/merge.py / resolve_parents、merge；retrieval/search.py / partial_overlap | 固定上游对应行为，直接复用、未修改 |
| retrieval/context.py / enriched_chat_passage；generation/context.py / Evidence.from_result | 原件quote与派生OCR上下文分离机制，直接复用、未改Prompt |
| scripts/weknora_port_p8_1_real.py、weknora_port_p8_1_observed_api.py | 复用P8真实HTTP入口和权限门，隔离证据采集与旁观 |
| tests/weknora_port/test_p8_1_observer.py、test_mvp_source_evidence.py | 必要旁观器及实际图片引用/parent胜出反例 |
| 本报告、同名results.json、progress.md | 交付与下一轮边界 |

实际初轮生产源码绑定2477ecd38a1768f28dd9941e4ed13f515411b570（旁观脚本随后提交304defd），唯一复核绑定d711231d73de6ccb6609453c691d0f8991a1e55f；最终parent修复仅离线验证。每个stage原始输入/输出见stages、rerank、captures、queries及sources目录。

最终审计：P8原157份权威文件hash一致、127行账本逐字段不变，DeepSeek前后完全一致，六个原有未跟踪文件hash一致；.serena原改动保留。新两个独立Redis前缀见closeout-audit.json。

初轮API父/子30400/32292、复核30304/31436按命令身份停止，remaining=[]；56429/56430监听0。共享EviRoute PG89c5cb748e2e/Redis15fe772fab7d仍healthy，不停止、不flush、不重建。

**可以开始下一轮当前已验证子集/有效指标的Cheap Only质量测评**，全部旧FAIL/PARTIAL和低分进入分母，不以低准确率设置额外修复门槛；本轮不启动正式批次。不能称官方全语料：SciFact/MIRACL/LongBench索引范围仍小子集、Hotpot支持句/joint缺映射、MML官方归一化/视觉计数未支持、Omni修复后真实OCR→回答尚未复核且无官方QA Gold。完整解析指标和全语料索引覆盖留待后续任务，不为本次6/6扩大开发。
