# P9 Cheap Only 真实质量基线（限定子集）

## 1. 目标、执行范围与结论

本轮建立可追溯的小子集基线，未宣称六套官方评测通过。实际执行 **16/40 冻结 QID**，其中 **14 次真实 Cheap 回答、2 次系统固定拒答**；另有 **1 次独立 Omni RAG 诊断**。16题链路结果为 **10 PASS / 4 FAIL / 2 PARTIAL**，这不是答案正确率。所有实际尝试、完整回答、阶段排名、引用回读、延迟及账本记录均在同名 results.json 和原始证据中。

Omni **24/24 页**通过正常上传、后台 OCR、入库和索引完成；只有 **3/24 页**符合既有原样全文 NED 的 Gold 顺序条件。17个冻结QID因原围栏剩余可用占用不足而 NOT_RUN，7个MML问题保留 CAPABILITY_LIMIT。748是既有来源单元数，不是题数。没有改题、改Gold、重跑成功答案或挑选最佳结果。

真实依赖：React浏览器不属于本轮验收；本轮真实 HTTP应用、Query处理、Embedding、PG/pgvector、既有检索融合、Rerank、原文回取、Context、Cheap回答和引用回读，模型均为 SiliconFlow 真实请求，非Mock。图像 OCR 为既有本地 Tesseract eng+chi_sim / dpi150；Caption 跟随专用OCR的配置不能生成视觉描述，本轮不把它标为Caption成功。

## 2. 版本与冻结数据

- 接手基线：3c9b722b97b89643cca2c83d255ca2c3762382a0。
- Omni A 运行源码：上述基线；正式16题 API 启动源码：a2eaa8e44ca55b4d8b3856399c5c09350b6f4985。
- 评分代码：1be5c4305dae1fc06181a90b373781f918f0b1cc。P9期间后端123个Python文件字节一致，生产回答策略未改；不同启动Git SHA的可比依据是已保存的生产文件哈希，而不是混入P8/P8.1历史成绩。
- 冻结清单：`E:\WK\run\p9-20261016/frozen-inventory.json`、`selection-all.json`、`gold-all.json`；运行选择为 `selection.json`，只扩展本轮所需索引。
- P8/P8.1历史不并入本轮均值，冻结文件350项完整性核对通过。历史失败、旧UNKNOWN、原始来源与Gold未修改。

索引范围：SciFact11篇原文（官方5183篇中的小子集）；MIRACL-ZH13个passage（冻结6000来源池的子集，非全检索语料）；LongBench8个完整题目上下文（400冻结上下文的子集）；Hotpot24篇原文（冻结73篇上下文并集的子集，非官方全语料或官方distractor协议）；MML2份既有PDF；Omni24张Gold原图。复用23个已有来源，仅增量59个来源，未重建全部历史语料。KB范围由服务端固定各自KB，未用Gold答案构造模型请求。

## 3. 检索、链路与引用（独立维度）

指标为对应实际执行题上的**来源身份** Recall@10，不能解释为全文证据蕴含或官方全语料召回。MRR/nDCG、逐题排名、正例文档覆盖分母见机器结果。引用有效率只验证身份、KB、document、version、hash、offset与quote可回读，未做语义蕴含裁判。

| 数据集 | 执行/冻结 | Cheap回答数 | 链路状态 | Search/Rerank/Context Recall@10 | 有引用题/执行题 | 有效引用/现有引用 |
|---|---:|---:|---|---|---:|---:|
| scifact | 4/8 | 2 | {"PASS": 2, "PARTIAL": 2} | 1.0000 / 0.6250 / 0.5000 | 2/4 | 2/2 |
| miracl-zh | 5/8 | 5 | {"PASS": 3, "FAIL": 2} | 1.0000 / 1.0000 / 1.0000 | 3/5 | 3/3 |
| longbench-zh | 3/8 | 3 | {"PASS": 1, "FAIL": 2} | 1.0000 / 1.0000 / 1.0000 | 1/3 | 8/8 |
| hotpotqa | 3/8 | 3 | {"PASS": 3} | 1.0000 / 0.8333 / 0.8333 | 3/3 | 5/5 |
| mmlongbench-doc | 1/8 | 1 | {"PASS": 1} | 1.0000 / 1.0000 / 1.0000 | 1/1 | 5/5 |

正式16题共有23条引用，23/23原文回读有效；10/16题有引用。6题没有引用：2题固定拒答，4题已经生成真实回答但漏引用。引用合法不等于答案正确，也不等于引用完整。

## 4. 答案 Gold 与成绩

| 数据集/任务 | 本轮有效答案样本 | 指标与成绩 | 可信范围 |
|---|---:|---|---|
| SciFact | 4题检索诊断 | 无答案分数 | 使用冻结证据qrels，不把claim当自由问答Gold；2题固定拒答保留 |
| MIRACL-ZH | 5题检索诊断 | 无答案分数 | passage级qrels与本地子集区分 |
| LongBench-ZH / dureader | 2题 | ROUGE-L均值 **0.2772571034792455**（27.73/100） | 既有官方任务函数，小样本原始完整答案 |
| LongBench-ZH / multifieldqa_zh | 1题 | 中文QA F1 **0.09375**（9.375/100） | 与dureader分别统计，不合并准确率 |
| HotpotQA | 3题 | answer EM **0**；token F1均值 **0.11426836672738312** | 完整回答计分，支持句/joint NOT_RUN，无真实句级映射 |
| MMLongBench-Doc | 1题 / 冻结8题 | 项目 normalized EM **0**；character F1 **0.017834394904458598** | row-0687；非官方LLM归一化裁判，7题当前能力限制 |
| OmniDocBench | 24页解析；另1题项目RAG诊断 | 无官方QA分数 | 原始解析Gold不冒充QA Gold |

答案评价仅移除系统kb标签，保留完整生成文本；不把答案中的正确实体单独提取后重新评分。**EM=0不能一律解释为事实错误**：Hotpot前两题正文中包含正确答案但附带解释；P9的MML正文选择Clinton与Gold一致，但理由使用平均等级而非严格中位数推导，且输出冗长。P8.1的旧错误答案仍是历史对照，不混为本轮输出。LongBench逐题官方函数版本和SHA在机器结果中。

## 5. Omni A 修复后的真实复核与解析

复用P8.1同一图片、KB、文档版本、有效索引和原问题，真实Search/Rerank/Context候选为3/3/1。原始引用1/1有效，offset0/0与回读匹配；OCR文本存在于Merge image_info、最终Context、准备消息及**实际发出HTTP请求的body**，请求体哈希与可信账本一致。该请求实际接收回执finish_reason=stop。分别验证了原始引用和补充OCR进入模型，未用引用合法替代OCR交付判断。

证据 `E:\WK\run\p9-20261016/omni-a-stage-proof.json` 及 `omni-a/provider-requests/`。本次真实Rerank为OCR child胜出，parent胜出分支仍以P8.1定向回归为证，不宣称本轮真实复现该分支。生成内容仍把OCR噪声 m@ 解释成字母M，属于OCR/回答质量现象，没有官方QA Gold，不报告问答准确率。

24页普通入口后台处理全部收口，只有3页满足既有Gold所有纳入文字块均有唯一整数order。3页**原样字符串全文NED均值 0.4367952773**（越低越好），21页由于原Gold部分order为null而 NOT_RUN；未删改Gold或编造阅读顺序。此NED包含文字及Gold中的公式表达差异，不能转成OCR准确率。官方版面/表格/公式/阅读顺序指标均 NOT_RUN，原因是本地OCR几何text_block没有官方语义类别映射。

## 6. 失败分类与本轮限定修复

非互斥计数：缺引用6/16（其中生成回答漏引用4/14）；无最终Context2/16（SciFact1304、242，Search均命中后被后续策略筛除）；答案EM零分4/4适用EM题（Hotpot3+MML1），答案重叠分数为零0题；非法现有引用0；provider/pipeline错误0；length截断0。未执行：预算17题、能力7题。这些分母不同，不相加为总错误率。

**未发现需要修改生产代码的新BLOCKER。** 实际最小修改都属于本轮测评工具：

1. 分开核验引用原文、OCR enrichment和实际provider wire body，避免旧“链路PASS”掩盖OCR未送达；新增offset必检及反例。
2. 不把仅有冻结selection的40题算成40次请求；完成的固定拒答按适用Gold进入系统答案分母，模型调用数单独统计。
3. Windows读取竞争出现WinError5导致检查点rename失败。保留首失败，限定P9目录内仅对瞬态错误做6次有界退避；真实Windows句柄反例验证临时锁恢复和持久锁不覆盖原文件。未改ACL，未重复发模型请求。
4. 收尾续读曾请求已存在的after标签而退出1，原快照保持不变；保存omni_final/final新标签，并让评分显式选择final，缺少final时阻断而非回落旧快照。

生产runtime/检索/Prompt/路由阈值/ParentChild均未修改。共享文件仅progress.md文档；无迁移、依赖锁、通用配置或入口修改，无协调者合并需求。

## 7. Token、权限、延迟与资源

本轮模型身份固定为 Embedding `BAAI/bge-m3`、Rerank `BAAI/bge-reranker-v2-m3`、Cheap `XingChenAGI/Xing4.0-29B`；实际请求与账本模型名逐项保留。

- 实际供应商响应250：Embedding145、chat88（含辅助及入库调用，**不是88道问答**）、Rerank17；233 settled，17 UNKNOWN，0 reserved。
- P9全部新调用实际 input260442 / output14408 / total **274850**；UNKNOWN **314417**保守占用单列，17条均保留，未当免费或再次发送。正式16题关联请求实际77426 / UNKNOWN313562；其余为入库及Omni诊断等。
- 原有1,000,000本地围栏不变；开场可用595414，结束6147。每次批次/问题前检查原账户、授权有效期、模型身份、PG/Redis身份及账本。未估算人民币费用。DeepSeek新增0，历史完整性通过。
- 正式16题HTTP时长合计1267.857781秒（不含上传，非纯模型耗时），逐题延迟已记录；finish_reason为14 stop，2固定拒答无模型finish_reason。
- PG127.0.0.1:56423 / weknora_python_port_20261013 / system identifier7696088403504697382；Redis127.0.0.1:56424 DB15 / runid87b4c2a9f76f9f94b34b5f21fe31a967926f343a；隔离prefix weknora-python-p9:6eaa76b989b743aaa46aefa604f20ea8。
- A及正式API、后台worker均按已验证命令与PID停止。正式API parent15620/child32944，worker parent42120；实际完整停止清单见closeout stop_receipts。56429/56430无监听；eviroute-rag-postgres、eviroute-rag-redis仍healthy，未停止共享服务、清库或flush。
- 原219条SF账本逐行不变，DeepSeek历史与旧image tasks不变，350个保护文件和123个生产源码文件哈希不变。原.serena修改与6个未跟踪文件保留。

## 8. 实际命令与证据

工作目录 `E:\RAG-Worktrees\weknora-python-port-20261013`；Python `E:/RAG quention/.venv/Scripts/python.exe -B`。PYTHONPATH为仓库、E:/WK/deps/reader、仓库var/weknora-port/runtime-deps；TEMP/TMP设为本轮E盘目录。没有新增下载/依赖。完整逐命令退出码索引在同名机器结果 `evidence.exit_code_receipts`，对应日志均位于 `E:\WK\run\p9-20261016`。

实际入口命令及结果（重复dataset参数代表逐个实际调用，非待执行建议）：

~~~powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B scripts/weknora_port_p9_real.py prepare --run-dir E:/WK/run/p9-20261016/omni-a
# 同入口 start / configure / query / snapshot / stop；a-*.log，各退出0
# query使用selection中原样Omni diagnostic QID，不另造题。
& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p9-20261016/hydrate.py
# exit0，hydrate.log
& 'E:/RAG quention/.venv/Scripts/python.exe' -B scripts/weknora_port_p9_real.py batch --run-dir E:/WK/run/p9-20261016/baseline --dataset scifact --phase first
# 同样逐个执行miracl-zh / longbench-zh / hotpotqa / mmlongbench-doc，first-*-batch.log，均exit0。
# 各套upload、prepare/start/configure、start-worker命令退出0；见对应exitcode.txt。
& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p9-20261016/poll_omni.py
# 首次续读因旧after标签保护exit1，omni-poll-continuation.log；改新标签后的只读收口exit0，omni-poll-reconcile.log。
& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p9-20261016/run_remaining.py
# exit0；remaining.log；24个剩余计划项中执行7，预算跳过17，逐项预检/调用/检查点保留。
& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p9-20261016/finalize_live.py
# exit0；finalize-live.log；final只读快照及停止自己进程。
& 'E:/RAG quention/.venv/Scripts/python.exe' -B scripts/p9_score_real.py --root-dir E:/WK/run/p9-20261016 --output E:/WK/run/p9-20261016/cohort-results.raw.json
# exit0；cohort-score.log；评分不发请求。
& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p9-20261016/enrich_cohort.py
& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p9-20261016/closeout_audit.py
& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p9-20261016/summarize_baseline.py
# 三者exit0；保留原始评分，仅给未执行项附实际admission原因；完整性审计；离线聚合。
& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m pytest tests/weknora_port/test_p9_score_real.py -q
# scorer-closed-test.log：当时11 passed，exit0。
& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m pytest tests/weknora_port/test_p9_score_real.py -q -k recorded_final_snapshot
# snapshot-selection-test.log：新增反例1 passed/11 deselected，exit0。
& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p9-20261016/checkpoint_native_check.py
# checkpoint-native-final.log：原生Windows瞬态/持久句柄锁反例，exit0。
~~~

没有全项目回归、完整官方语料评测或真实浏览器验收。未实现的命令不得报告通过。原始模型请求、阶段观察和引用回读在baseline/provider-requests、captures、stages、queries、requests及ledger-final.json；账本完整差量在all-new-ledger.json。helper脚本哈希在机器结果，原样可复核。

## 9. 下一轮最值得做的事及继续搁置项

1. **先对照优化引用输出稳定性**：4/14真实答案漏引用，MIR2、Long2，即使Context命中也发生；有引用时23/23可回读，因此问题集中在输出完整性而非当前引用身份校验。冻结本基线，另起版本做小范围对照。
2. **检查Rerank到最终Context的证据保留质量**：SciFact4题Search Recall1→Context0.5并拒答2题，Hotpot Context0.8333。先复核候选分数与筛选理由，不直接降低阈值。
3. **按具体任务改善答案贴合度和简洁度**：LongBench分数低，Hotpot整段答案EM0。区分事实错误、冗余输出及评分格式影响，后续优化前后源码与样本分开统计。

供应商Rerank用量缺失造成17个UNKNOWN、314417占用，是继续同一围栏测评的实际运行限制；可另做用量来源核实，不能释放旧UNKNOWN或换账本绕过。

继续搁置：MML视觉计数和其它7题能力补足、Omni语义类别映射/完整官方打分、官方全语料索引、GraphRAG/Agent、Cheap→Expensive策略或阈值、大规模重构。本轮不为6/6而开发它们。

## 10. 逐QID结果与提交

以下PASS/FAIL/PARTIAL只表示既有严格链路口径；答案Gold、引用及检索的独立结果请看机器文件。未执行的预算或能力限制不被包装成PASS。

| Dataset | 冻结QID | 状态 | 原因 |
|---|---|---|---|
| scifact | 1102 | PASS | QUOTE_READBACK_AND_CHAIN_PASS |
| scifact | 1304 | PARTIAL | NO_CURRENT_SCOPE_CITATION;MISSING_REAL_QUERY_EMBEDDING_RERANK_OR_ANSWER_RECEIPT;FIXED_FALLBACK_WITHOUT_ANSWER_MODEL |
| scifact | 1351 | PASS | QUOTE_READBACK_AND_CHAIN_PASS |
| scifact | 242 | PARTIAL | NO_CURRENT_SCOPE_CITATION;MISSING_REAL_QUERY_EMBEDDING_RERANK_OR_ANSWER_RECEIPT;FIXED_FALLBACK_WITHOUT_ANSWER_MODEL |
| scifact | 247 | NOT_RUN | NOT_RUN_BUDGET_RESERVATION_HEADROOM |
| scifact | 339 | NOT_RUN | NOT_RUN_BUDGET_RESERVATION_HEADROOM |
| scifact | 611 | NOT_RUN | NOT_RUN_BUDGET_RESERVATION_HEADROOM |
| scifact | 621 | NOT_RUN | NOT_RUN_BUDGET_RESERVATION_HEADROOM |
| miracl-zh | 277616#0 | PASS | QUOTE_READBACK_AND_CHAIN_PASS |
| miracl-zh | 5704915#0 | PASS | QUOTE_READBACK_AND_CHAIN_PASS |
| miracl-zh | 5721594#0 | PASS | QUOTE_READBACK_AND_CHAIN_PASS |
| miracl-zh | 692002#0 | FAIL | NO_CURRENT_SCOPE_CITATION |
| miracl-zh | 704170#0 | FAIL | NO_CURRENT_SCOPE_CITATION |
| miracl-zh | 7049630#0 | NOT_RUN | NOT_RUN_BUDGET_RESERVATION_HEADROOM |
| miracl-zh | 7696#0 | NOT_RUN | NOT_RUN_BUDGET_RESERVATION_HEADROOM |
| miracl-zh | 91294#0 | NOT_RUN | NOT_RUN_BUDGET_RESERVATION_HEADROOM |
| longbench-zh | dureader:11b3b7afb73f5bf65a51b3083e885a60825b17421c58a828 | PASS | QUOTE_READBACK_AND_CHAIN_PASS |
| longbench-zh | dureader:5726f5fc6feedda99cafb275a27a8fd67483185b6fb8efa7 | FAIL | NO_CURRENT_SCOPE_CITATION |
| longbench-zh | dureader:6770736a958b78c4c1b1a0057b34a709bdd869f307f3dc40 | NOT_RUN | NOT_RUN_BUDGET_RESERVATION_HEADROOM |
| longbench-zh | dureader:9938c97e6ccf006c5fc66ea3294b9835e1584d95713576a9 | NOT_RUN | NOT_RUN_BUDGET_RESERVATION_HEADROOM |
| longbench-zh | multifieldqa_zh:1a6c19fed6361cbfbfd0df3fb7ca1e01f2a2ffa0da9903bc | FAIL | NO_CURRENT_SCOPE_CITATION |
| longbench-zh | multifieldqa_zh:51abd2060651563dfb6982a9e5380c6dfdf7c4917056feed | NOT_RUN | NOT_RUN_BUDGET_RESERVATION_HEADROOM |
| longbench-zh | multifieldqa_zh:741f37778b75805b150988026cf24e53885d61d47a64bd05 | NOT_RUN | NOT_RUN_BUDGET_RESERVATION_HEADROOM |
| longbench-zh | multifieldqa_zh:bcedff0a6829ca68f2c33247af3bea6f5fa81c8d2d940fa2 | NOT_RUN | NOT_RUN_BUDGET_RESERVATION_HEADROOM |
| hotpotqa | 5a8ad6805542992d82986f93 | PASS | QUOTE_READBACK_AND_CHAIN_PASS |
| hotpotqa | 5a745a6055429974ef308be4 | PASS | QUOTE_READBACK_AND_CHAIN_PASS |
| hotpotqa | 5a729fe55542991f9a20c53b | PASS | QUOTE_READBACK_AND_CHAIN_PASS |
| hotpotqa | 5ae3992d5542994393b9e722 | NOT_RUN | NOT_RUN_BUDGET_RESERVATION_HEADROOM |
| hotpotqa | 5ab672165542995eadeeffe0 | NOT_RUN | NOT_RUN_BUDGET_RESERVATION_HEADROOM |
| hotpotqa | 5a8911ad554299515336129c | NOT_RUN | NOT_RUN_BUDGET_RESERVATION_HEADROOM |
| hotpotqa | 5a7b68a75542997c3ec97153 | NOT_RUN | NOT_RUN_BUDGET_RESERVATION_HEADROOM |
| hotpotqa | 5a7631cd5542994ccc91870c | NOT_RUN | NOT_RUN_BUDGET_RESERVATION_HEADROOM |
| mmlongbench-doc | mmlongbench-doc:2ff6aa9237fc777b6627dc57a486e9225ac5fb86:row-0684 | BLOCKED_CAPABILITY_LIMIT | CAPABILITY_LIMIT_FOR_THIS_NATIVE_TEXT_PATH: exact chart values lost in current parser output or unanswerability requires unavailable chart coverage; no guessing from Gold |
| mmlongbench-doc | mmlongbench-doc:2ff6aa9237fc777b6627dc57a486e9225ac5fb86:row-0687 | PASS | QUOTE_READBACK_AND_CHAIN_PASS |
| mmlongbench-doc | mmlongbench-doc:2ff6aa9237fc777b6627dc57a486e9225ac5fb86:row-0685 | BLOCKED_CAPABILITY_LIMIT | CAPABILITY_LIMIT_FOR_THIS_NATIVE_TEXT_PATH: exact chart values lost in current parser output or unanswerability requires unavailable chart coverage; no guessing from Gold |
| mmlongbench-doc | mmlongbench-doc:2ff6aa9237fc777b6627dc57a486e9225ac5fb86:row-0431 | BLOCKED_CAPABILITY_LIMIT | CAPABILITY_LIMIT: native parsed content only image placeholders; needs visual counting/figure interpretation or currently unprocessed image text |
| mmlongbench-doc | mmlongbench-doc:2ff6aa9237fc777b6627dc57a486e9225ac5fb86:row-0434 | BLOCKED_CAPABILITY_LIMIT | CAPABILITY_LIMIT: native parsed content only image placeholders; needs visual counting/figure interpretation or currently unprocessed image text |
| mmlongbench-doc | mmlongbench-doc:2ff6aa9237fc777b6627dc57a486e9225ac5fb86:row-0430 | BLOCKED_CAPABILITY_LIMIT | CAPABILITY_LIMIT: native parsed content only image placeholders; needs visual counting/figure interpretation or currently unprocessed image text |
| mmlongbench-doc | mmlongbench-doc:2ff6aa9237fc777b6627dc57a486e9225ac5fb86:row-0959 | BLOCKED_CAPABILITY_LIMIT | CAPABILITY_LIMIT: native parsed content only image placeholders; needs visual counting/figure interpretation or currently unprocessed image text |
| mmlongbench-doc | mmlongbench-doc:2ff6aa9237fc777b6627dc57a486e9225ac5fb86:row-0958 | BLOCKED_CAPABILITY_LIMIT | CAPABILITY_LIMIT: native parsed content only image placeholders; needs visual counting/figure interpretation or currently unprocessed image text |

本轮本地逻辑提交（文档交付提交在最终回复另列）：

- 1be5c4305dae1fc06181a90b373781f918f0b1cc eval: select immutable final P9 snapshots without stale fallback
- 3d2755f85fa9fb805091033a2866f27ce00f5241 fix: retry P9 checkpoint rename only for bounded Windows reader contention
- cdcc198f8bac71e0539f3cdf54740898a6194715 eval: include completed system refusals in end-to-end answer denominators
- b7820471dcf0f6a8df29ed24dd746de5db03ab0c eval: require citation offset readback and preserve unexecuted P9 denominators
- a2eaa8e44ca55b4d8b3856399c5c09350b6f4985 eval: freeze P9 real batches and verify provider OCR delivery separately from quotes

所有实现和定向证据已保存。未Push、部署、合并或打Tag；停止本轮，不自动进入下一轮优化。当前状态是**限定范围质量基线已交付**，不是负责人确认的里程碑验收。


交付一致性检查：`E:/RAG quention/.venv/Scripts/python.exe -B E:/WK/run/p9-20261016/delivery_verify.py` 实际退出0，核对40题分母、16次尝试、14次模型回答、17预算未运行、7能力限制、23/23引用、Token和UNKNOWN、350保护文件及模型身份。日志 `E:/WK/run/p9-20261016/delivery-verify.log`；端口证据 `final-ports.txt`。
