# P8 — 六套数据集真实接线与 Gold 基础

## 状态与范围

**本轮限定执行已完成；六套完整接线尚未全部通过，全规模质量测评NOT_RUN。全部748来源与Gold基础已核对；首轮9题+复核2次均实际执行。正式六套Cheap Only评测：暂不具备完整条件。**

本轮为小样本真实供应商接线，不是六套完整质量测评。只用 Cheap Only；不修改 Prompt、检索阈值、Rerank TopK、Parent/Child、动态路由算法，不调用 DeepSeek，不处理历史 UNKNOWN。

- 工作树：`E:/RAG-Worktrees/weknora-python-port-20261013`。
- 分支：`codex/weknora-python-port-integrate-image-20261014`。
- 运行开始源码：`ae0096ac3651975831bf704e07a0c150fab2f6a6`；首轮生产 backend/frontend 未改；复核使用295bd0a原文证据修复，frontend未改。评价工具另作本地提交，实际文件 hash 见 `runtime-provenance.json`。
- 固定上游：`3e8b0bfc80b845b2d4b2ed683994748741450a97`。
- 全部新产物：`E:/WK/run/p8-20261016`；不读取或输出密钥正文。

## A. 748 来源资产核验：PASS

| 数据集 | 来源单元 | 类型 | 哈希／字节／可解析 |
|---|---:|---|---|
| SciFact | 200 | 原始文本 | 200/200 |
| MIRACL-ZH | 400 | 原始文本 | 400/400 |
| LongBench-ZH | 48 | 原始 Markdown | 48/48 |
| HotpotQA | 73 | prepared 包中的原始文本 | 73/73 |
| MMLongBench-Doc | 3 | 完整 PDF | 3/3，页数 45/68/21 |
| OmniDocBench | 24 | 原图 | 24/24 |
| 合计 | **748 = 648 raw + 100 prepared** | **来源单元，不是 748 道题** | **748 有效，0 排除** |

三个 raw 包实际读取 ZIP 对象；100 份 prepared 的 normalized/chunks/assets/attachments 均核对。721 份文本严格 UTF-8 解码，3 PDF 用 pypdf 打开，194 张原图／附件通过 Pillow.verify。748 个来源身份及内容 hash 无重复；来源唯一不能证明题目或 Gold 独立。

全部来源与冻结完整 corpus 的身份映射通过：SciFact 200/5183、MIRACL 400/6000、LongBench 48/400、Hotpot 73/73、MML 3 PDF、Omni 24 页。40 个冻结问题的正例来源与 qrels 可追溯，Hotpot 与原始问题／答案／完整上下文／支持句核对，24 页解析 Gold 与原图对应。

- 包索引 SHA256：`a2fc0410c100f14e380db3d900d6b2f9d403b742277d0fba3df273f6f608e641`。
- 当前 `source_manifest_40.json` SHA256：`995a85aec0ab5c2020f0a1dd3fdde8196b4e0fb9d1e6699b9bbf8e747e4c00a3`。不能沿用历史报告中的另一 hash `05a11e…`。
- 证据：`asset-audit.json`、逐来源 `units.jsonl`、`frozen-gold-check.json`、`gold-audit.json`、`gold-selection-final-verification.json`。
- 索引复用：`index-reuse-audit.json` 仅审查 manifest。prepared 100 单元为模拟 validation IDs，formal_index=NOT_RUN；raw648 无正式 document/version/job ID。清单不提供可信正式索引身份，本轮只将选中22份原件入新隔离库。这不等于断言其他数据库完全无索引。

### Gold 类型与适用边界

| 数据集 | 有效 Gold | 冻结数量 | 本轮可计算／不可冒称 |
|---|---|---:|---|
| SciFact | BEIR 文档 qrels | 8 QID | 文档 Recall/MRR/nDCG；没有答案及原始 SUPPORT/CONTRADICT rationale Gold，不算 QA 准确率 |
| MIRACL-ZH | 官方 topic/qrels | 8 QID | passage 检索指标；没有答案 Gold。小候选池非官方全语料成绩 |
| LongBench-ZH | 原任务答案列表 | 8 QID | multifieldqa_zh 中文分词 F1；dureader 中文 ROUGE-L。paired-context qrels 仅项目检索诊断 |
| HotpotQA | 原答案与支持句 | 8 QID | 答案 EM/token F1；支持句须有真实偏移映射才评分；文档命中不等于句命中，不冒称 joint |
| MMLongBench-Doc | 原 typed answer | 8 QID | 可作明确的项目答案诊断；官方 GPT 归一化未运行，页码基准 UNKNOWN；有2题无页证据 |
| OmniDocBench | 官方原页解析标注 | 24 页，0 QA | OCR/版面可用范围诊断；没有独立问答 Gold，不算 QA 准确率 |

结构字段检查、hash 及身份映射不是人工语义认证。禁止字段递归审查覆盖100份 prepared JSON；真正上传只有原件，Gold 与 selection/模型输入分离。原文自然包含答案属于正常证据，未插入 Gold 答案、qrels、支持句标注。

## B. 预先冻结的小样本

选择不参考本轮模型输出。SciFact/MIRACL 取冻结 QID 字典序首尾可映射题，各加前2个非相关原件；LongBench 每任务取字典序首题完整 context；Hotpot 取首个两支持来源且原始10上下文完整的题；MML 首题完整 PDF；Omni 首原图，问题显式为项目诊断。

| 数据集 | 题数 | 原件数 | QID |
|---|---:|---:|---|
| SciFact | 2 | 4 | 1102；621 |
| MIRACL-ZH | 2 | 4 | 277616#0；91294#0 |
| LongBench-ZH | 2 | 2 | dureader:11b3b7…；multifieldqa_zh:1a6c19… |
| HotpotQA | 1 | 10 | 5a729fe55542991f9a20c53b |
| MMLongBench-Doc | 1 | 1 | 固定 revision 2ff6aa…:row-0430 |
| OmniDocBench | 1 诊断问题 | 1 | PPT_EnglishtoAmericanTransition_page_012.png:p8-diagnostic |
| 合计 | **9** | **22** | 完整 QID／原问题／版本见 selection.json |

- `selection.json` SHA256：`8160c8b9bce97ad42d6cf5fe29ee3e831748c7da88da368901f25a4ba33fccf2`，只含可入模问题及原件引用。
- 独立评价侧 `gold-selected.json` SHA256：`8f9bc4a20949310e9b7f0a44a9beed97920dc80224ba2c33d5f2f8862e8ec55a`。
- 原件按包字节解出，不增加 title、不上传旧 prepared OCR 来冒充本轮解析。所有失败留在9题分母。

## C. 实际环境、权限和调用

PG：127.0.0.1:56423，库 weknora_python_port_20261013，system identifier **7696088403504697382**，PostgreSQL17.9。Redis：127.0.0.1:56424/15，run_id **87b4c2a9f76f9f94b34b5f21fe31a967926f343a**，独立 prefix **weknora-python-p8:3de2eca0bea340babcde75ce60c9a366**。新 tenant332，资产位于证据目录 assets/。

Python3.13.0，入口使用项目 venv。API56427 和预期 origin56428 启动前无监听。`process-identities.json`、`container-identities.txt` 和 `runtime-provenance.json` 绑定实际进程、服务及源码；不替换原服务、不 flush Redis、不重建卷。

- Embedding：SiliconFlow BAAI/bge-m3。
- Rerank：SiliconFlow BAAI/bge-reranker-v2-m3。
- Cheap：SiliconFlow XingChenAGI/Xing4.0-29B；普通问答使用已有 reasoning_effort=off。
- Expensive/VLM 配置空，不调用 DeepSeek。
- 新有效 approved 配置绑定原有 siliconflow-owner-main／siliconflow／2026-10 月账户，沿用原 approval identity 与保守 carry14，不新建额度池。Owner 对 SF 无总量限制；额外100万只作为本轮既有本地围栏，不声称是 Owner 限额。保留 P1SendGate／预留／结算／权限校验。
- DeepSeek before/after 全账比较；本轮不迁移或释放任何历史 UNKNOWN。

### 真实 API 与只读观察

HTTP 仍经过现有 entrypoint.create_app、runtime_factory、普通上传、后台任务、普通问答/SSE、检索及 P1SendGate。评价侧启动器仅包装既有 prepare_turn 返回结果，记录 search/rerank/context 排名与实际消息，不提供模拟结果、重跑检索或读取 Gold。实际 API/worker 命令保存在 api-command.json/worker-command.json。

Omni 使用现有独立 Tesseract OCR。已实测 PyMuPDF1.26.4 与本地 eng/chi_sim 语言数据可用；工作树默认相对 var/tessdata 缺失，运行环境显式设 RAG_TESSDATA_DIR=E:/RAG quention/var/tessdata。Caption 跟随专用 OCR 时明确 IMAGE_CAPTION_VISION_MODEL_REQUIRED，保持 OCR 与 Caption 分项状态，不以 OCR 文本冒充 Caption。标准独立 Worker 消费任务。MML 保持现有原生 PDF 默认路径，不新增视觉模型。

## D. 实测结果

首轮22/22原件完成处理并回读哈希；9/9问题均已实际发送。严格完整链路：**5 PASS / 1 PARTIAL / 3 FAIL**。这是接线状态，不是答案正确率。全部首轮 SSE、消息、引用、capture、原账本与评分仍保留于证据根目录。

- PASS：SciFact2、MIRACL1、LongBench multifieldqa_zh1、Hotpot1。
- PARTIAL：MIRACL 91294#0。search和rerank均命中Gold文档，但最终context0，返回固定拒答，无答案模型调用与引用。下述P1修复后单独复核。
- FAIL：LongBench dureader有真实答案但没有引用；MML row0430为图片计数题，原生PDF产出68页图像占位，文字模型明确无法据此数老虎；Omni初始诊断问题被理解为问当前附件，未检索已上传KB。
- 首轮账本115条真实响应：embedding70、chat38、rerank7。7条rerank usage未知保持UNKNOWN；DeepSeek全账before/after比较unchanged=true。Token总量在最终收口统计，UNKNOWN不作为免费或实际消耗。
- 单页Omni真实本地OCR与原始Gold文字作有限原样对比，page-text NED=0.12。页图、版本、task与OCR结果绑定通过；不是官方Omni分数、版面分数或QA准确率。

### 已确认并限定修复的 P1

真实落盘MIR中文原文与摘要经过实际default_segmenter，原文27 token、摘要32 token、共享27，包含率1.0。原merge允许高分生成摘要通过重叠消除原文，生产末端投影随后移除摘要，得到空context。这是**当前适配器与固定merge的交互缺陷**，不是Rerank阈值调优。

修复commit：`295bd0ac5aeca1f4227899797f495a2cca02c9a7`。生产runtime提供独立merge输入视图：先按原rerank-or-search选择，再剔除生成摘要；固定merge只处理该视图，仅回写merge_results。summary-only时不回退到被既有rerank拒绝的search候选；历史summary在视图内过滤，原始阶段排名/对象不改写。原件ID、KB、版本、offset与回读保护保留。

**固定merge.py/search.py、0.85重叠阈值、ParentChild、路由与模型Prompt没有修改。** 未注入适配器的pipeline仍走固定原路径。旧mock断言和全部跨KB/version等负例保留；新增实际merge交互反例。定向4文件测试实际91 passed，exit0，见source-evidence-test.log。

### 复核合同（不覆盖首轮）

复核单独目录recheck/，复用已验证的原件、KB、索引、tenant332及原有SF月账户，不重新入库。

1. MIRACL原问题/QID不变，用新request_id复核P1修复。
2. Omni独立诊断问题明确适配为“请从当前知识库中检索已上传的图片，说明图片页面的主要内容，并引用图片中的文字证据。”；scope为同一KB，document_scope为空，使用正常KB检索，避免把它当成当前附件。原诊断题及失败保留；新题仍没有QA Gold。

复核选择先冻结再调用，SHA `60f1362c78327aee623d985bea965b56a07b3939de3ee82c36fdbaac23d79838`。这不是生产Prompt修改，也没有更换模型或将Gold答案送入模型。两个新增尝试与首轮分开计数；不得将首轮失败改写为成功。

## E. 命令、定向验证与复用

全部命令在工作树运行；Python 为 `E:/RAG quention/.venv/Scripts/python.exe`，PYTHONPATH 包含工作树、`E:/WK/deps/reader` 和工作树 `var/weknora-port/runtime-deps`。TMP/TEMP、pip cache、jieba cache、测试临时目录均在 D/E 盘。

| 实际命令（入口） | 退出码 | 关键证据 |
|---|---:|---|
| `python -B scripts/p8_dataset_asset_audit.py --pypdf-site D:/Drivers/anaconda/Lib/site-packages --replace-existing-outputs` | 0 | asset-audit.stdout.txt/exitcode.txt；748/748 |
| `python -B -m eval_center.weknora_frozen_gold --manifest <原source_manifest_40.json> --sha256 995a85… --report <frozen-gold-check.json>` | 0 | 40题、24页 hash 通过 |
| `python -B scripts/p8_gold_selection.py --package-root D:/RAG-v6-final-ingestion-pack-r1/attempt3-ocr-layout --freeze-manifest <原source_manifest_40.json> --freeze-sha256 995a85… --output-dir E:/WK/run/p8-20261016` | 0 | gold-selection-attempt1.log；最终重核见 gold-selection-final-verification.json |
| `python -B -m pip install --target E:/WK/run/p8-20261016/eval-deps --no-deps jieba==0.42.1 rouge==1.0.1 fuzzywuzzy==0.18.0 six==1.17.0` | 0 | eval-deps-install.log/exitcode.txt；仅离线评价依赖 |
| `python -B -m pytest tests/weknora_port/test_p8_asset_audit.py tests/weknora_port/test_p8_gold_selection.py tests/weknora_port/test_p8_score_real.py -q -p no:cacheprovider --basetemp E:/WK/run/p8-20261016/pytest-p8-final` | 0 | p8-tests-final.log/exitcode.txt：**26 passed**，fuzzywuzzy 纯 Python 实现提示1条 |
| `python -B scripts/weknora_port_p8_real.py prepare` | 0 | prepare.log/exitcode.txt，tenant332 |
| `python -B scripts/weknora_port_p8_real.py start` | 0 | start.log/exitcode.txt；API/worker PID与命令落盘 |
| `python -B scripts/weknora_port_p8_real.py configure` | 0 | configure.log/exitcode.txt；6 KB，ready configured |
| `python -B scripts/weknora_port_p8_real.py upload` | 0 | upload.log；不得仅凭采集脚本退出码宣称上传/质量通过 |
| `python -B scripts/weknora_port_p8_real.py query` | 0 | query.log；9题实际发送 |
| `python -B scripts/weknora_port_p8_real.py snapshot --label after` | 0 | snapshot-after.log；首轮账本/文档/任务 |
| `python -B scripts/p8_score_real.py` | 0 | score-initial.log；首轮results.json |
| `python -B scripts/weknora_port_p8_real.py stop` | 0 | stop-initial.log；首轮API/worker均已停，复核另记 |

26项为**离线评价工具反例与官方函数冒烟**，不是26道真实RAG问答；包含来源路径/hash/重复、QID与支持事实、Gold泄漏字段、缺结果保分母、跨库/原件/版本/引用反例、client/server请求ID差异、实际收据和时长等。没有跑全项目回归。未实现的命令不得报告通过。

LongBench 直接加载作者固定 scorer revision `c5ea10bcd06285223c58dfed76bbc92d22273709` 的 `metrics.py`，SHA `e22e2a2662e0f7e683137fa3541f64edb6a801e9138d16d2f3459a6ab9941323`。原文件及 eval.py、下载URL/hash均在 official-longbench/receipt.json；每题多个参考取最大。该 scorer revision 单独记录，不假定与 HF 数据 revision 相同。官方定义：[task dispatch](https://github.com/THUDM/LongBench/blob/c5ea10bcd06285223c58dfed76bbc92d22273709/LongBench/eval.py)、[metric functions](https://github.com/THUDM/LongBench/blob/c5ea10bcd06285223c58dfed76bbc92d22273709/LongBench/metrics.py)。

### 代码复用／本次适配

- 复用 `load_frozen_gold` 的现成冻结校验、PublicCase、`project_native_documents`、`ranking_metrics`、`score_native_qa`、Omni `_ned`；不改现有六集评测框架。
- 复用 MVP 的真实隔离连接、可信配置、凭据仅进程内传递；现有 DocumentLifecycleService/SourceImageProcessor/独立ImageTaskQueue 走正常入口。
- 新增薄资产核验、样本冻结、HTTP编排、只读准备结果观察与离线评分脚本及最小反例。
- 共享生产文件只因真实P1修复修改2个：backend/weknora_port/runtime.py 与 backend/weknora_port/retrieval/pipeline.py，同一295bd0a提交串行整合；测试文件test_mvp_source_evidence.py随该提交。**迁移、生产依赖锁：0 修改。** 评价额外包只安装在本轮E盘eval-deps；不引入新模型注册或新问答策略。


## F. 最终收口（2026-10-16）

### 六套逐集结论

严格完整接线要求：全部选中原件处理完成且hash可回读；本题真实query embedding、Rerank和答案回执齐备；答案完成；至少一条同库、同文档版本、正文一致的可回读引用。PASS只表示这个合同通过，**不表示答案符合Gold或语义引用已人工认证**。

| 数据集 | 实际尝试 | 严格PASS | FAIL | PARTIAL | 最终状态／原因 |
|---|---:|---:|---:|---:|---|
| SciFact | 2 | 2 | 0 | 0 | PASS；只评检索，不计算无Gold的QA准确率 |
| MIRACL-ZH | 3（2题+1复核） | 1 | 1 | 1 | FAIL；原文丢失已修复，复核search/rerank/context Recall@10均1，但答案仍无引用 |
| LongBench-ZH | 2 | 1 | 1 | 0 | FAIL；dureader真实回答未带引用；multifield有可回读引用 |
| HotpotQA | 1 | 1 | 0 | 0 | PASS接线；两个支持来源引用可回读，答案Gold指标另列 |
| MMLongBench-Doc | 1 | 0 | 1 | 0 | BLOCKED；所选PDF为68页图像，题目要求数老虎；本轮文字模型不能完成视觉计数 |
| OmniDocBench | 2（两种诊断问题） | 0 | 1 | 1 | BLOCKED；本地OCR/索引完成，显式KB问题实际检索/Rerank后上下文为空，固定拒答；无独立QA Gold |
| 合计 | **11次／10个独立dataset-QID** | **5** | **4** | **2** | 所有初次失败和复核均入分母，未以新结果替换旧失败 |

四套存在完整真实链路样本；MML和Omni的必要检查已实际执行并保留具体阻断，不能称六套全部跑通。未增加模型、阈值或问答策略来提高本轮通过率。

### Gold 实测值（首轮原答去除系统引用标签，其余文字不改写）

| 样本 | search / rerank / context Recall@10 | 答案指标 | 引用有效数 |
|---|---|---|---:|
| scifact 1102 | 1.0 / 1.0 / 1.0 | NOT_APPLICABLE_NO_ANSWER_GOLD | 1/1 |
| scifact 621 | 1.0 / 1.0 / 1.0 | NOT_APPLICABLE_NO_ANSWER_GOLD | 1/1 |
| miracl-zh 277616#0 | 1.0 / 1.0 / 1.0 | NOT_APPLICABLE_NO_ANSWER_GOLD | 1/1 |
| miracl-zh 91294#0 | 1.0 / 1.0 / 0.0 | NOT_APPLICABLE_NO_ANSWER_GOLD | 0/0 |
| longbench-zh dureader | 1.0 / 1.0 / 1.0 | {"rouge_zh_score": 0.26578072684407456} | 0/0 |
| longbench-zh multifieldqa_zh | 1.0 / 1.0 / 1.0 | {"qa_f1_zh_score": 0.12} | 1/1 |
| hotpotqa 5a729fe55542991f9a20c53b | 1.0 / 1.0 / 1.0 | {"hotpot_answer_em": 0.0, "hotpot_answer_token_f1": 0.1875} | 2/2 |
| mmlongbench-doc row-0430 | 1.0 / NOT_RUN_NO_REAL_RERANK_EXECUTION / 1.0 | {"normalized_exact_match": 0, "character_f1": 0.0} | 0/0 |
| omnidocbench p8-diagnostic | NOT_APPLICABLE_NO_RETRIEVAL_GOLD / NOT_RUN_NO_REAL_RERANK_EXECUTION / NOT_APPLICABLE_NO_RETRIEVAL_GOLD | NOT_APPLICABLE_NO_ANSWER_GOLD | 0/0 |

MIR复核context Recall由0恢复到1，真实模型形成了临近星座答案，但没有引用，所以仍FAIL。Omni复核确实有query embedding与Rerank响应，未保留最终上下文，随后固定拒答，没有额外答案调用。两次实际HTTP耗时分别80.492秒和29.867秒；完整每题Token/延迟见机器结果。

- LongBench分别使用官方任务函数：dureader ROUGE-L=0.26578072684407456；multifieldqa_zh中文分词F1=0.12。不同任务不合成统一准确率。
- Hotpot答案EM=0、token F1=0.1875。完整RAG答案含解释，直接按官方短答案归一化评分，未事后提取姓名重算漂亮分数；支持句和joint仍NOT_RUN，文档命中不冒充句命中。
- MML答案为信息不足拒答；项目typed-answer诊断EM/F1均0，官方GPT归一化NOT_RUN，所选题无evidence_pages。源文档命中不证明识别了动物。
- Omni原始解析Gold有效；单页原样文字NED=0.12。只有原生OCR行框，没有可证明的语义版面分类，官方全量解析、块/表格/公式/阅读顺序及QA准确率没有伪造。
- MML的直接文档加载不等于真实query向量检索或Rerank。最终评分已把“未调用Rerank”标NOT_RUN，未用recall0假装执行过；原错误阶段标签版本保留results-initial-pre-stage-label-fix.json。

### 真实供应商与历史不变量

**127条真实供应商响应**：Embedding77、Chat41、Rerank9；均为SiliconFlow，DeepSeek调用**0**，无Mock。

- 已结算可知实际Token：**120,879**（118条settled）。
- 9条Rerank响应缺usage，继续保守占用**111,093 Token**，state=UNKNOWN；不视为0、免费或已知实际消耗。
- 最终reserved=0，没有未确认的在途预留。这个“usage UNKNOWN”与模型是否收到响应分开：本轮127条outcome均RESPONSE_RECEIVED。
- 原MVP 8条SF UNKNOWN共74,497占用逐字段比对保持原样；DeepSeek原账户全账before/after unchanged=true。文件historical-sf-unknown-invariance.json、recheck/deepseek-invariance-after.json可核对。

### 修复、剩余阻断与停止

已修复：P1原文被生成摘要挤出；新增回归使用实际merge而非只替换merge输出。已解决运行配置：有效同账户授权续期、标准worker、绝对tessdata目录。已修复评价错误：client/server请求ID绑定、缺来源不能PASS、未执行Rerank不计已执行指标。

**正式验收仍受阻**：MIR复核和Long dureader缺引用；MML当前题目需要本轮未配置的视觉理解能力；Omni当前诊断检索未形成答案上下文，且没有独立QA Gold。这些失败没有获得虚假PASS，也没有由AI自行批准里程碑或延期。

后续可提出的最小工作：先针对已有失败冻结引用输出反例与视觉/解析评价合同，再由Owner决定下一轮范围。Prompt优化、TopK/阈值调整、模型替换、ParentChild/RRF、全量评测均未实施。短答案格式影响指标、部分排名/时延不理想、可选官方指标缺口分别记录，不用调参隐藏失败。

**当前不能开始声称覆盖六套完整合同的正式Cheap Only测评。** 文本检索和已有答案Gold的评价工具可用，但本轮没有取得六套全链路全部PASS或完整质量分数。

首轮API/worker及复核API全部按PID/命令/authority身份停止；56427/56428无监听，本任务无残留进程。共享eviroute-rag PostgreSQL/Redis仍健康运行，未flush、清库、重建卷或停止。没有Push、部署、合并或Tag。

### 最终命令补充与版本

- 真实原件上传、9题query、初轮snapshot/score/stop均exit0；退出码仅证明采集命令完成，案例状态按上述结果。
- `python -B E:/WK/run/p8-20261016/recheck.py prepare/start/query/snapshot/stop` 各阶段exit0，分别有recheck-*.log及exitcode文件。
- `python -B scripts/p8_score_real.py --run-dir E:/WK/run/p8-20261016/recheck` exit0：1 FAIL、1 PARTIAL；recheck-score.log。
- 真实P1关联验证：`python -B -m pytest tests/weknora_port/test_mvp_source_evidence.py tests/weknora_port/test_pipeline.py tests/weknora_port/test_pipeline_tokenizer.py tests/weknora_port/test_affinity_pipeline.py -q -p no:cacheprovider --basetemp=E:/WK/run/p8-20261016/source-evidence-test-temp`，exit0，91 passed（source-evidence-test.log）。
- 最终评价阶段验证：`python -B -m pytest tests/weknora_port/test_p8_score_real.py -q -p no:cacheprovider --basetemp E:/WK/run/p8-20261016/score-final-temp`，exit0，16 passed（score-tests-final.log）。先前26项工具集成测试和旧评分版本证据仍保留。

机器摘要：`docs/evaluation/P8_SIX_DATASET_REAL_WIRING.results.json`；逐题原答、排名、引用、模型收据、Token、延迟及证据SHA位于该文件和E盘完整结果中。首次运行生产SHA为ae0096ac；复核运行SHA为dce7c002；生产修复仅295bd0ac。

本地提交（完整SHA）：

- 474fbd379c6bc29e2648b2c26ad8deaf9f6f8de8 eval: distinguish skipped rerank from actual empty rankings
- dce7c0029b9603075fed0f8753ec745a76560716 eval: allow explicit knowledge base scope in P8 rechecks
- 295bd0ac5aeca1f4227899797f495a2cca02c9a7 fix: preserve original evidence before generated summary overlap
- af5f619c1ff87e266b5614e72a9fd9587ed3e78c eval: score P8 recorded evidence with independent gold and receipt checks
- 6b73b2534a722d3a9a203b16a08d88314234fe99 eval: add resumable P8 real HTTP runner and passive stage capture
- 6b20f078733ee8fd07d641303c20362114ad679e eval: audit and freeze P8 six dataset source and gold bindings

整合顺序：资产/Gold工具6b20f078 → HTTP观察工具6b73b253 → 评分af5f619 → **同一原子提交295bd0ac中的pipeline/runtime/test** → KB范围复核工具dce7c002 → 阶段标签474fbd37 → 本报告。共享生产修改仅前述两个Python文件；迁移/生产锁依赖不变。原.serena/project.yml及六个既有未跟踪证据保留，未纳入本任务提交。

### 改动文件与可核对命令

实际文件清单：生产 `backend/weknora_port/runtime.py`、`backend/weknora_port/retrieval/pipeline.py`；评价脚本 `scripts/p8_dataset_asset_audit.py`、`scripts/p8_gold_selection.py`、`scripts/weknora_port_p8_real.py`、`scripts/weknora_port_p8_observed_api.py`、`scripts/p8_score_real.py`；测试 `tests/weknora_port/test_p8_asset_audit.py`、`test_p8_gold_selection.py`、`test_p8_score_real.py`、`test_mvp_source_evidence.py`；文档为本报告、机器摘要和progress.md。

完整可复现参数、工作目录、非敏感环境及实际退出码见 `E:/WK/run/p8-20261016/commands.json`，表中的SHA/路径简写不能代替该命令记录。最终证据一致性检查exit0，`completion-audit.json`确认11次尝试、六套检查、127响应、历史不变量、停止状态与证据hash；该PASS不是质量验收。完整API/Worker进程身份见根目录process-identities.json及首轮state.json，复核API父PID42632及其child命令见recheck/state.json；停止前逐个重新校验身份。
