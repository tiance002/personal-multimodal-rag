# P12 — Rerank 证据丢失定位与 Omni 真实向量 A/B

**最终状态：P12_COMPLETE。** 0434 根因已由保存的真实日志定位；三页全部完成成对真实 KB 向量问答，共6次，已停止调用。任务完成不代表全部问答正确。

## 1. 源码、冻结和范围

初始 HEAD `d7987d45c6720c97b94997c761051debf825fd91`；实际执行 HEAD `e31d1eb5915bf815dc92ec7169c8f04d4e9422d2`。两者 backend/frontend 无差异，125个 backend Python 文件哈希见 `runtime-source-binding.json`。选择状态保留初始 source_sha，不把操作围栏提交误当作生产逻辑变更。固定上游仍为 `3e8b0bfc80b845b2d4b2ed683994748741450a97`。

本轮新增仅 P12 专用有限围栏 CLI 及定向测试，复用 P11 同月锁/CAS/不可覆盖收据；没有修复或修改生产检索、解析、模型、Prompt、阈值、路由、迁移或依赖。P11 CLI/grant 未重开。

原始三页已查看，在任何模型请求之前冻结问题及源页面诊断标准，见 `frozen.json` / A,B `selection.json`。沿用 p11-omni 页面标签，但本轮独立问题和结果不覆盖 P11。Omni 没有官方 QA Gold；以下是来源页面人工核对的项目诊断，**不是官方 QA 正确率、完整语料 Recall 或新的解析 NED**。

A：原生图片/Tesseract 既有索引；B：MinerU Basic 4.0.12 既有索引。两组分别只有同样三张原图，源 SHA 相同，chunking 配置均为空默认、模型 ID 和调用参数相同。模型固定 SiliconFlow BAAI/bge-m3、BAAI/bge-reranker-v2-m3、XingChenAGI/Xing4.0-29B；真实 FastAPI→Query/Embedding→PG/pgvector→Rerank→Context→Cheap/固定拒答→正常引用回读。没有 Mock，没有重新解析、上传、建索引或启动 Worker；P11 Worker/入库证据保留。

每次 POST session/messages 都为对应隔离 KB Scope、`document_scope=[]`。六个 Scope 身份检查全 PASS；Query Embedding 和 Rerank 均有真实响应回执，不是直接文档加载。A已有9条、B已有6条1024维向量，三页小子集，不混其它KB。

## 2. row-0434 根因

正式 B run `9b35df2f-5e9f-4c69-82b4-1ecf8ecfe3d6` 的正确证据完整进入 Rerank HTTP request：index14，chunk `085643dc-5ebb-4b8a-b7f1-75427da40015`，内容含 `Kaggle's Cat vs Dog dataset (25k dog/cat pictures)`；PDF page34/idx33，派生 Unicode [7575,7625)，chunk [7276,7744)。

该证据适配器返回分数 **0.0007800887105986476**，模型排序第6；30条最高 **0.002033646684139967**。既有 threshold **0.3** 和 Top1 floor **0.15** 拒绝所有结果；默认 .3 不触发仅 `>.3` 的重试，随后 SearchNothing、空 Context、固定拒答，没有回答模型调用。

30条 enriched passage、实际 wire documents、返回 index0..29 双射及排序逐项一致；SF probability 分数路径无缩放。固定上游 rerank.go:379/413/148 及 protocol.go:130 使用相同规则。冻结结果调用当前 rerank() 的离线重放 exit0，重现零项。**没有确证可解释本次失败的代码 Bug，因此不改代码和阈值。**

实际 Rewrite 把英文问题改成中文，违反现有保持原语言指令；但旧英文诊断对完全相同答案 passage 得分也只有 .0041066184639930725，最高 .028172623366117477，仍不达 .15。旧诊断非正式向量分母，不能当受控消融；不能认定翻译是唯一原因。候选缺 literal coding time/章节关联可能影响相关性，模型内部因果未证。分数来自保存的适配器返回边界，原供应商响应字节未保存，不能声称已重验其原始 body。详细请求、版本、定位、上游映射、离线命令及一次过时 HEAD guard 失败见 `0434-root-cause.{md,json}`。

## 3. 三页成对结果

S/R/C 是最终观察到的候选数量；来源命中、完整原文证据进入 Context、回答和引用分开评价。

| 页面 | 组 | S/R/C | 最终证据 | 回答诊断 | 引用 | 实际Token | UNKNOWN占用 | HTTP秒 |
|---|---|---:|---|---|---|---:|---:|---:|
| omni-06 | A | 9/3/1 | PARTIAL | PARTIAL | 0 | 3976 | 2834 | 226.350 |
| omni-10 | A | 9/2/1 | PRESENT | PASS | 1 | 3865 | 2842 | 131.673 |
| omni-14 | A | 9/1/0 | ABSENT | FAIL | 0 | 2682 | 2894 | 110.446 |
| omni-06 | B | 6/2/1 | PRESENT | PASS | 0 | 3870 | 1885 | 225.094 |
| omni-10 | B | 6/1/1 | PRESENT | PASS | 1 | 3847 | 1893 | 132.772 |
| omni-14 | B | 6/2/1 | PRESENT | PASS | 1 | 4165 | 1945 | 264.886 |

- **06 囚歌**：A OCR 截断、字词错误；回答“革命者在敌人监狱里写下的歌”，保留主体与情境但未明确“诗歌”，按冻结标准 PARTIAL。B 完整诗歌定义进入实际模型请求，回答全部要点 PASS。两组均遗漏结构化引用；不把回答正确当引用完整。
- **10 英语变化**：两组 Context 均含 Change7 pronunciation，答案均正确。A/B 各1条引用，ID/KB/document/version/quote/offset/原件 SHA 回读全通过。B文本更整洁，但该事实A已可读，不声称此题答案提升。
- **14 矩阵**：A Search/Rerank 命中同来源，其中最终 Rerank 仅留模型生成摘要；最终 Context 为空，固定拒答，未发回答请求。不能把摘要或来源命中当作矩阵证据。B完整 LaTeX 矩阵进入实际模型 wire，回答系数矩阵 [[1,-2,1],[0,2,-8],[-4,5,9]] 和末列 [0,8,-9] 正确，有1条合法相关来源引用。

源页面人工诊断：A **1 PASS / 1 PARTIAL / 1 FAIL（固定拒答）**；B **3 PASS / 0 PARTIAL / 0 FAIL**。完整目标证据进入最终 Context：A1/3且另1/3部分、B3/3。五次回答请求都核验了绑定派生文本实际送入模型；A14没有回答请求，原评分器的 payload-unverified 在此解释为固定拒答不适用，不是假称已送达。完整回答、finish_reason、每阶段来源、实际 Query Rewrite/Rerank query、Context、wire及ledger绑定见机器结果。

生成引用共3条，**3/3身份、版本、quote及offset合法，均来自目标来源**；这是已有引用的有效性。六次尝试只有3次生成引用，五次模型回答中两次遗漏。评分器不做语义蕴含判断，回答诊断另行核对。A10是 standalone image，page_number=null，原图chunk offset[0,0)是合法数据，OCR另在image_info；不伪造其逐字OCR位置。B10派生Unicode[0,135)、B14[0,434)，page1/idx0，并保留 source SHA、中间JSON哈希、block_path；部分公式 bbox 为null，不能声称都有框级定位。

## 4. Token、保护与收口

本轮按现有 Owner SiliconFlow 授权设置一次性 **200000** Token 增量围栏，保留原账户 siliconflow-owner-main、2026-10月、carry14、approval及历史账本。open把当前已收口1200898提升到1400898；正常 Token Authority/P1SendGate 仍逐次检查。

- 6次正式向量问答：5次真实回答、1次固定拒答；没有为了提分重复调用。
- **45条实际模型调用记录**：28 Embedding、11 chat（6辅助Rewrite+5回答）、6 Rerank；均 RESPONSE_RECEIVED，39 settled、6 UNKNOWN、0 reserved。
- **实际22405 Token = input21898 + output507**。另列6条 Rerank usage缺失 UNKNOWN **14293** 保守占用，合计本轮占用36698，不当免费或实际已知Token，不估算人民币。
- 剩余 **163302** 增量撤回；close后本月limit **1237596** 等于全账户占用+carry，headroom=0。原P11配置及本轮打开配置不能继续获得余额。
- 原563条账本逐行不变，原45条UNKNOWN不变；DeepSeek历史不变、调用0。1215个P11/报告文件与继承378个原保护文件哈希不变。knowledges、chunks、image tasks前后全等，索引数量/维度/启用状态不变。

六问HTTP时长合计为 **1091.2220** 秒，包含模型/服务/网络，不是纯生成时间。没有省略慢响应或选最好答案。实际模型限制、原生解析不足、漏引用均保留。

PG127.0.0.1:56423，库weknora_python_port_20261013，system identifier7696088403504697382；Redis127.0.0.1:56424 DB15，run_id87b4c2a9f76f9f94b34b5f21fe31a967926f343a，各组新唯一前缀。资产根复用P11真实原件，以免新空根导致引用越根。已核验命令后只停止本任务PID22444/37256/15080/40384；56429/56431无监听，共享EviRoute PG/Redis仍healthy，未flush或重建容器。

## 5. 实际命令与退出码

cwd为工作树，Python为 `E:/RAG quention/.venv/Scripts/python.exe -B`；PYTHONPATH为工作树、E:/WK/deps/reader及var/weknora-port/runtime-deps。所有新日志/临时文件在E/D。下表省略上述Python可执行路径，完整每次命令见results.json.commands和E盘保存脚本。

| 命令 | exit | 证据 |
|---|---:|---|
| `prepare-v3.py`；`inventory.py` | 0 / 0 | frozen、身份及protected-before；前两版prepare失败原文件和setup-errors保留 |
| `-m pytest tests/weknora_port/test_p12_fence.py tests/weknora_port/test_p11_score_real.py -q --tb=short -p no:cacheprovider --basetemp E:/WK/run/p12-20261016/unit-final` | 0 | unit-final.log：33 passed；离线测试不代替真实服务 |
| `-m scripts.weknora_port_p12_fence open --approval-file E:/WK/run/p12-20261016/owner-approval.txt` | 0 | fence-open.log及不可覆盖真实PG收据 |
| `stage.ps1 snapshot A/B -Label before/final`；`stage.ps1 start A/B` | 各0 | before/final、ready.json、实际API命令 |
| `stage.ps1 query A/B -Dataset omnidocbench -Qid p11-omni-06/10/14 -Tag <group>-query-<page>` | 六次各0 | A/B-query-*.log/exitcode、每个intent、SSE、messages、引用及chunk回读 |
| `scripts/p11_score_real.py --root-dir E:/WK/run/p12-20261016 --output E:/WK/run/p12-20261016/score-real.json` | 0 | RECORDED6，query_identity全PASS |
| 命令匹配后 `Stop-Process -Id 37256,40384,22444,15080` | 0 | stop-owned-before.json/exitcode |
| `-m scripts.weknora_port_p12_fence close`；`closeout.py` | 0 / 0 | 真实PG close收据、closeout-audit.json |

一次index inventory误用不存在的stage列，exit1已保留原日志；仅将只读进度查询改为实际capability列，未改schema或账本。0434离线重放、过时HEAD guard的完整命令/输出在专属根因报告，不将失败guard当成重放失败，也不隐藏失败。未实现的命令不得报告通过。

## 6. 剩余问题与停止

没有本轮确证需修的生产BLOCKER。0434仍为 Rerank 相关性/证据选择质量问题；Rewrite保持语言的指令遵循问题存在但因果未证明。Omni06漏引用两次，原解析器OCR与摘要存在字词错误，完整公式证据仅B恢复；部分公式缺bbox精度。这些不能因已有引用合法而消除。

下一轮最值得优先做：**对正确实体证据被极低Rerank分数过滤建立小型冻结诊断，比较章节关联、Query Rewrite和分数校准，再决定策略**；其次是Cheap引用完整性。当前不盲降阈值、不改Prompt、不自动解析器路由。MinerU继续建议扫描PDF/图片显式可选；本轮证明三页子集的真实贡献，不能推为全官方语料保证。

任务达到停止条件：0434根因明确、三对最多六次正式问答结束、全部失败/Token/UNKNOWN纳入结果、围栏关闭。没有继续优化或追加实验；无Push/部署/合并/Tag。原.serena修改与六个历史未跟踪文件保留。报告与机器结果在本地提交，不代表Owner上线批准。

原始证据：`E:/WK/run/p12-20261016`；哈希索引 `delivery-evidence-manifest.json`。完整JSON包含每题实际答案、Context、定位、调用数/用量及P11只读评分原结果，P8/P9/P10/P11冻结结果未改。
