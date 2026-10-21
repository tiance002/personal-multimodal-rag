# ECS 剩余冻结题：对齐资料＋保留Top1评测

## 结果与范围

**本次续测范围完成：40/40新请求终态完成；不是全部答案通过，也不代表S8整体完成。** 原40题余下可执行29题＋此前已冻结允许纳入的额外SciFact10题＋工程负例1题。正式题分母39，工程负例单列1。已完成9题不重跑；历史UNKNOWN两题继续保护，不重新发送。原40题现累计38/40完成，加入extra10后为48/50正式题完成；其余2题仍为历史UNKNOWN。24页Omni沿用上轮已对齐资料/解析证据，本轮不新增上传、OCR或解析评分，不当作24道新QA。

Graph关闭、Top1无下限与ModelPassage开启，阈值仍0.3；MMR、Merge、Parent/Child、TopK、DYNAMIC及Prompt不改。全KB含干扰资料，不用每题Gold文档限定范围。tenant332服务器身份、PG物理身份和Redis前缀沿用已核验配置，tenant1日常principal不变。

起点HEAD `aa4abca38e93bb60756188411b9cf6779fb7cb80`；业务代码基线 `3d90756f064ea28810beac0fcc250e05d539d954`；分支 `codex/s8-six-dataset-evaluation-20261019`。镜像 `sha256:8e31042a532fc80be6f5595407fcaf30da37147a88edb1d998a773bda3a2ffa8`。本轮只提交报告/机器结果，不改生产代码和默认开关；最终完整commit见交付消息或 `git log -1 --format=%H -- docs/evaluation/S8_ECS_REMAINING_TOP1_EVALUATION.md`。原主工作树16个未提交路径未覆盖。

## 执行、用量与内存

- 正常FastAPI→QU→Search/RRF→Rerank→Merge/Context→生成→SSE/PG/历史：40/40完成；1984次来源HTTP回读状态/字节SHA全部通过，40/40真实Context/wire绑定、SSE/PG/历史答案一致。公开引用继续关闭，内部来源实际可回读。
- 查询理解与最终生成实际均为SiliconFlow Qwen/Qwen3.5-4B。QU：temperature0.3、max_tokens150、thinking=false；最终Quick：temperature0.7、max_tokens2048、thinking=false。40次最终attempt均cheap/stop，无length、空答案、升级Expensive或新UNKNOWN。
- 实际160次Provider，输入457,226＋输出9,442＝466,668Token，全SETTLED：Qwen80次/170,227；BGE-M3查询Embedding40次/663；BGE Rerank40次/295,778。DeepSeek实际0次。查询Embedding不等于重新建立文档向量。
- 单题POST全程6.02–69.84秒，中位10.78秒，P95 nearest-rank 24.02秒；不是单次模型时延。最慢SciFact338约69.84秒，其中最终Qwen持久化调用区间60.86秒。逐Provider区间已列机器结果；日志不能独立拆分HTTP建连/TLS/TTFT。
- ECS串行批次UTC05:05:08–05:25:12，约20分钟；首题先独立执行且runner只读跳过，未重复POST。全部40子账本及本轮master CLOSED，原API恢复healthy、默认cloud_allowed=false、1GiB。旧ECS UNKNOWN仍2条且digest未变；未使用软件空间撤回，不是复活旧授权。
- 1秒采样宿主机MemAvailable谷值 **1,878,736,896bytes＝1.7497GiB**（SciFact1101）。API cgroup memory.peak最大 **1,036,357,632bytes＝988.35MiB**（MML0430）；每题限制1GiB，OOM0。谷值是采样最小值；cgroup峰值含缓存，不等于Python RSS。本轮不据此调整内存。

## 人工质量与来源覆盖

下表“语义/全文支持”为人工工程审查：核心问题是否得到回答，与完整回答是否有来源支持分开。SciFact/MIRACL无官方生成Gold，不能计算官方QA准确率；SciFact的5个UNVERIFIED语义项包含未建立关系时的诚实不足，不伪称解决声明。MML官方GPT判分NOT_RUN。跨数据集不合成所谓官方总准确率。

| 数据集 | 本轮完成 | 语义PASS/PARTIAL/FAIL/UNVERIFIED | 全文事实PASS/PARTIAL/FAIL | Token |
|---|---:|---|---|---:|
| scifact | 16 | 3/6/2/5 | 3/7/6 | 225396 |
| miracl-zh | 7 | 5/2/0/0 | 2/5/0 | 67207 |
| longbench-zh | 6 | 3/2/1/0 | 1/4/1 | 79681 |
| hotpotqa | 5 | 4/1/0/0 | 4/1/0 | 47563 |
| mmlongbench-doc | 5 | 2/0/3/0 | 2/0/3 | 39398 |
| engineering-negative | 1 | 0/1/0/0 | 0/1/0 | 7423 |

正式39题全文事实审查为12 PASS、17 PARTIAL、10 FAIL；工程负例另为PARTIAL。该口径不把文档命中或关键词答案命中计为答案正确。

来源级覆盖按完整实际阶段候选集合计算，括号内为“至少一个/全部必要来源”题数；不是具体事实覆盖，也不同于机器结果另列的Recall@10。

| 数据集/分母 | Search至少一/全部 | Rerank至少一/全部 | Context至少一/全部 |
|---|---|---|---|
| scifact/16 | 15/15 | 13/11 | 13/11 |
| miracl-zh/7 | 7/7 | 7/7 | 7/7 |
| longbench-zh/6 | 6/6 | 4/4 | 4/4 |
| hotpotqa/5 | 5/5 | 5/4 | 5/4 |
| mmlongbench-doc/5 | 5/5 | 5/5 | 5/5 |

Hotpot特别分开：导演题虽然Romeo必要来源被Rerank丢弃，Whiting等独立原文仍能直接支持Zeffirelli，因此核心答案PASS但官方两来源完整性PARTIAL。MML0684等正确文档命中不代表图表数值/视觉事实进入wire。MIRACL的来源排名可能从Search Top10之外被Rerank提升，不能把Recall@10差异误写成候选凭空出现。

LongBench所选3道DuReader平均ROUGE-L为0.256916；3道multifieldqa_zh平均F1为0.121624（0–1）。保留完整回答而非抽取理想短答案评分；低字符串分数与人工语义分开。Hotpot全回答EM/F1及MML项目字符串诊断逐题保存，不冒充整套官方成绩。

## 逐题结果

每题完整q0/Rewrite、来源/版本、候选及阶段指标、原文Context、真实参数、答案、全部Provider用量/区间和内存记录见同名results.json；原始分数/候选顺序在原字节包中。下表全文支持不等于官方QA正确率。

| QID | 语义/全文支持 | 首次丢失或错误/结论 | Context块 | Token | POST秒 | 主机谷值GiB |
|---|---|---|---:|---:|---:|---:|
| scifact-1102 | PARTIAL/PARTIAL | 历史摘要已把mesoderm译为间充质；最终沿用。 | 2 | 13783 | 14.33 | 2.274 |
| scifact-1351 | UNVERIFIED/PASS | 上调dynein关系未建立；合理说明不足。 | 4 | 11590 | 14.34 | 2.245 |
| scifact-242 | PARTIAL/FAIL | 历史摘要颠倒reverse coupling方向，最终沿用。 | 2 | 13047 | 8.42 | 2.231 |
| scifact-247 | UNVERIFIED/PARTIAL | 化学损伤/酶活性关系未建立；合理不足，但来源说明有缺陷。 | 3 | 11356 | 7.51 | 2.195 |
| scifact-611 | FAIL/FAIL | 原机制送达；最终颠倒IRS1/脂解方向，并虚构PPARγ。 | 1 | 12890 | 10.63 | 2.215 |
| scifact-621 | PASS/PARTIAL | 核心人群区分有据；来源列表遗漏及研究时长表述不准。 | 4 | 15067 | 11.46 | 2.243 |
| miracl-zh-277616#0 | PASS/PASS | 完整答案的541米、1776英尺、2014开放、西半球/美国第一、旧塔527/415米均有送达原文支持。无officialAnswerGold，不能称官方QA正确。 | 5 | 9989 | 10.23 | 2.260 |
| miracl-zh-5721594#0 | PASS/PARTIAL | 核心13项中文/原英文名单完整。东映1948、业务、Animax及Gangan/钢炼的补充均有原文；史克威尔段落并未被直接列作动画制作公司。原文已明确日本動畫是一项公司名单，答案额外猜测可能为统称缺少依据。 | 6 | 10238 | 22.47 | 2.251 |
| miracl-zh-692002#0 | PASS/PARTIAL | 罗默完整答案有直接原文支持；其余五名姓名和诺奖/芝加哥联系、斯蒂格勒细节得到原文支持，但不得把这些支持替代美国国籍证据。 | 2 | 8751 | 15.45 | 2.235 |
| miracl-zh-704170#0 | PARTIAL/PARTIAL | 结构、弱酸碱、醇盐等有原文支持；答案把黄酮类化合物段落的难溶于水/易溶于有机溶剂性质套给醇类，主语错误。sp杂化来自原文受损字样，应表达不确定；未凭外部知识修复。 | 6 | 8839 | 20.46 | 2.220 |
| miracl-zh-7049630#0 | PARTIAL/PARTIAL | 五家大型国企总部可在同SHA原文回读证实，但wire只送摘要。原文只说华夏银行、首旅、北投、建院、保障房中心疏解进展，并未逐一指定央企/市属，答案新增前三项央企分类及后两项单独类别；中国人寿、人保国企背景也未有wire证据。 | 4 | 9074 | 24.02 | 2.223 |
| miracl-zh-7696#0 | PASS/PASS | 孔子为创始人、前5世纪、核心价值与返鲁编六经都有送达原文支持。以六经确立体系属综合转述；source对六经作者存在争议，答案说编订整理未说孔子原创，不构成该争议的反向断言。 | 16 | 12089 | 10.56 | 2.238 |
| miracl-zh-91294#0 | PASS/PARTIAL | 八个邻近星座及方向完整准确复述；但wire中该事实仅来自generated summary。相关原始text在Search和Rerank存在、Merge被移除。独立回读该摘要所附source原文支持事实；原文未实际送达生成端，不能把摘要当原文证据。 | 7 | 8227 | 8.83 | 2.202 |
| LongBench-dureader-5726f5 | PARTIAL/PARTIAL | 三套Gold方案/原文均进入wire。回答概括调时间+放弃任务循环，但漏重新建号、关自动时间、循环时其他任务尽量不做及50食物25瓶盖必须做的具体条件，把互异方案合成单一路径。“需要通过调时间”过强；“食物任务也能奖励箱子”有文章2支持却与文章4的食物给瓶盖条件未区分。存读档、房屋勿升级可在原文核实。 | 2 | 16490 | 16.60 | 2.215 |
| LongBench-dureader-677073 | PARTIAL/PARTIAL | Gold十九节日中答十八项，漏植树节，wire存在该项。大多数日期/庆祝描述有原文，但答案自行加母亲节送花、父亲节纪念、国旗日纪念诞生等未在送达段落明确支持的细节，退伍军人节被收窄为二战及以前服役者而wire为曾服役老兵；林肯日期及复活节/万圣节等来源有冲突，答案未提示。此审查只依冻结原文，无联网纠错。 | 7 | 19873 | 38.00 | 2.189 |
| LongBench-dureader-9938c9 | PASS/PARTIAL | UTC加8小时核心公式、东正西负和01:45到09:45例子完整支持，语义答对。附加“参考资料未提及中国实行夏令时，因此通常固定UTC+8”不能由资料沉默推出，属于超出原文的推论；不是核心换算事实缺失。 | 6 | 14226 | 10.10 | 2.250 |
| LongBench-multifieldqa_zh-51abd2 | PASS/PARTIAL | 数值2.2万人与Gold一致且有原文wire支持；但送达43521…段落仅“一年来”，未明确2005。Search有含2005的相关候选，Rerank筛掉目标qrel来源；回答以5500万元数据特征推定2005年份，未表达不确定。第2章说法也超出chunk index=2所能证明。数字正确不能代替完整年份/市身份绑定。 | 5 | 11873 | 7.29 | 2.246 |
| LongBench-multifieldqa_zh-741f37 | PASS/PASS | 关闭扫描仪、取出、无尘干布擦喷嘴金属板、重装四步完整。湿布、金属轮等补充在另一原文相邻维护段落中有支持（混入打印盒座注意事项，未改变核心步骤）。 | 6 | 8865 | 9.41 | 2.240 |
| LongBench-multifieldqa_zh-bcedff | FAIL/FAIL | 正确HJ1134—2020、2020-08-27在Search两个同内容候选（其中包含本题qid）存在；Rerank均删除，Merge/Context/实际wire均无目标日期。回答列2015-11-20和2007-04-09来自其他规范，虽各自能回查原文，仍未回答本题目标规范，不属合理不足。 | 3 | 8354 | 10.69 | 2.220 |
| Hotpot-5a8ad6 | PASS/PASS | 导演Zeffirelli正确且有原文；Romeo必要来源/事实在rerank丢失，核心答案仍支持。 | 4 | 9262 | 8.37 | 2.186 |
| Hotpot-5a745a | PARTIAL/PARTIAL | 选Wind in the Willows正确，但把官方1983电影换成1962同名小说比较。 | 10 | 10648 | 16.47 | 2.236 |
| Hotpot-5ab672 | PASS/PASS | Azadirachta两种对Azorina一种，两来源原始事实完整入wire且比较正确。 | 4 | 8385 | 15.45 | 2.222 |
| Hotpot-5a8911 | PASS/PASS | 加冕人Ximena与2010-08-23的Miss Universe关系正确，两跳原文齐全。 | 10 | 10442 | 8.44 | 2.229 |
| Hotpot-5a7b68 | PASS/PASS | 最大湖Winnipesaukee与Kanasatka高差原文齐全，正确回答低9英尺。 | 4 | 8826 | 19.65 | 2.263 |
| MML-0684 | FAIL/FAIL | 原图Men81%>Women78%；检索起缺性别数值绑定，答案拒答并混淆79/75/67的实体指标。 | 2 | 7888 | 8.11 | 2.204 |
| MML-0685 | PASS/PASS | 相关原图无White/Black拆分；诚实不足，与官方不可回答一致。 | 2 | 7921 | 6.68 | 2.189 |
| MML-0431 | FAIL/FAIL | 人脸网络原图共5层；wire缺人脸关联，答案虽提5却错称猫狗并拒绝用于本题。 | 1 | 7755 | 16.66 | 1.977 |
| MML-0430 | FAIL/FAIL | 68页无虎=0；wire只含局部代码，答案拒答且把真实人脸图误判为狗。 | 1 | 8175 | 12.27 | 1.937 |
| MML-0959 | PASS/PASS | radiowaves与microwaves两类别及机制均获原PDF和实际wire支持。 | 2 | 7659 | 14.70 | 2.047 |
| scifact-1025 | PARTIAL/PARTIAL | 机制大致有据；开头否定强于来源，末尾说明因果证据不足。 | 4 | 15430 | 14.79 | 2.243 |
| scifact-1101 | PARTIAL/PARTIAL | 抑制关系回答正确；沿用历史摘要的mesoderm误译。 | 2 | 15136 | 8.35 | 1.750 |
| scifact-1302 | UNVERIFIED/FAIL | Search无CK/Tirasemtiv证据，回答另无据推断Tafamidis心肌保护。 | 1 | 16950 | 10.86 | 2.227 |
| scifact-1305 | UNVERIFIED/PASS | CK作用原文有据；别名关联未建立，合理说明不足。 | 1 | 13752 | 10.02 | 2.258 |
| scifact-241 | PASS/FAIL | 相伴生核心关系正确；摘要/最终方向说明颠倒。 | 2 | 10397 | 10.10 | 2.204 |
| scifact-944 | FAIL/PARTIAL | 混淆非痴呆认知试验、痴呆ADL终点，结论过强。 | 3 | 14814 | 10.23 | 2.265 |
| scifact-949 | PARTIAL/PARTIAL | 伤口表达上调被泛化为TG2酶活性，不受送达事实支持。 | 1 | 11540 | 6.02 | 2.258 |
| scifact-1303 | UNVERIFIED/PASS | CK来源在Rerank丢失，别名仍缺；最终合理说明不足。 | 1 | 16147 | 6.03 | 2.204 |
| scifact-338 | PASS/FAIL | 数字有据；末句将8个再手术者均用药反成全部用药者均再手术。 | 5 | 20227 | 69.84 | 2.225 |
| scifact-921 | PARTIAL/FAIL | 摘要增加未证实显著性判断，最终再泛化并错引其他终点。 | 3 | 13270 | 14.31 | 2.218 |
| engineering-unanswerable-postcode | PARTIAL/PARTIAL | 低分Top1地图噪声；无编邮编/误用道路号，但把研究机构臆断为会议举办地。 | 1 | 7423 | 12.71 | 1.852 |

## 已确认问题与本轮未做的工作

1. **历史Summary与原文冲突**：SciFact1101/1102将mesoderm误译为间充质，241/242颠倒reverse coupling，921加入未证实显著性判断。错误最早已在历史Summary，不全部归因最终模型。原文与最终生成均保存，本轮不重写Summary。
2. **证据选择仍会丢事实**：LongBench技术规范日期在Search后被Rerank丢弃；农村人数年份绑定也缺失；北京/人马座在Merge移除核心原文，仅由Summary承载。Hotpot导演题第二必要来源丢失。MML0684缺男女百分比绑定，0431缺人脸与网络的视觉关联，0430缺全篇图片计数覆盖；最早只能定位至已保存Search/下游缺失，不能据此断言解析器首错。
3. **正确资料送达仍有生成错误**：SciFact611机制反转/PPARγ、338量词反转；MIRACL醇与黄酮属性混淆、国籍/英文别名补充；工程负例不捏造邮编，却把研究机构地图当作会场。细节见逐题原文对照。
本轮有6题raw Top1<0.15，确实可能触发相对旧保底的策略差异；包括低分工程反例0.0009481641。它保留噪声并引出无依据场地归属，说明“不会编邮编”不等于无幻觉。没有同输入A臂完整生成对照，不把当前质量变化全归因Top1，不推广默认。Graph本轮关闭、未评价新增收益；S8整体仍PARTIAL。

下一步只建议按错误分布另立受控修复：先处理摘要事实与原文冲突、再处理重排/合并的必要原文保留，最后针对已送达事实的归属与量词错误做生成回归。视觉计数/关联单列能力缺口；本轮未实施这些调优。

## 命令、证据与工作树保护

以下最终命令实际执行退出码均0（首题一行为ECS内记录的实际driver命令，其余本机命令在 E:/RAG quention 执行）；本轮没有修改产品代码，不重跑全仓测试。未实现的命令不得报告通过。

```powershell
.venv/Scripts/python.exe var/reports/s8-remaining-top1-20261020/cohort/build_cohort.py
.venv/Scripts/python.exe var/reports/s8-remaining-top1-20261020/controller/test_remaining_contract.py
python -m compileall -q var/reports/s8-remaining-top1-20261020/controller var/reports/s8-remaining-top1-20261020/scoring/score_remaining.py var/reports/s8-remaining-top1-20261020/aggregate_technical.py var/reports/s8-remaining-top1-20261020/remote_finish_remaining.py
python var/reports/s8-remaining-top1-20261020/stage_remote.py
python3 -B /opt/rag-s8/s8-remaining-top1-20261020/tools/s8_top1_ecs_case_driver.py --case-id scifact-1102 --arm B --execute
python var/reports/s8-1-ecs-20261020/ssh_run.py var/reports/s8-remaining-top1-20261020/remote_start_runner.py ../s8-remaining-top1-20261020/runner-start 60
python var/reports/s8-1-ecs-20261020/ssh_run.py var/reports/s8-remaining-top1-20261020/remote_finish_remaining.py ../s8-remaining-top1-20261020/final-operation-summary 120
python var/reports/s8-remaining-top1-20261020/export_checkpoint.py final-raw
python -B var/reports/s8-remaining-top1-20261020/scoring/score_remaining.py --evidence-root var/reports/s8-remaining-top1-20261020/evidence/final-raw --source-snapshot var/reports/s8-aligned-top1-20261020/raw-final-evidence/metadata/actual-source-snapshot.json --output var/reports/s8-remaining-top1-20261020/scoring/final.results.json
python -X utf8 var/reports/s8-remaining-top1-20261020/aggregate_technical.py --evidence-root var/reports/s8-remaining-top1-20261020/evidence/final-raw --score-json var/reports/s8-remaining-top1-20261020/scoring/final.results.json --output var/reports/s8-remaining-top1-20261020/technical-final.json
```

9项执行工具合同测试PASS；40个driver真实命令均0；全部Provider回执/PG/账本一致。人工审查脚本：`review/scifact/review_final.py`（16）、`review/miracl-longbench/build_complete_review.py`（13）、`review/specials/validate_review.py final-raw`（11/85检查），均退出0。最终`git diff --check`与暂存差异检查结果见提交核对记录。

保留了工具首次错误：初次导出器因ledger schema选择0个已完成项，修正后原字节导出；额外10题的expected_documents为string列表，评分器初报TypeError，补只读来源版本/SHA适配后40/40审计完成；一次聚合命令早于最终评分文件完成而FileNotFound，等待评分完成后正常执行。均无模型补发，也不计作答案失败。原始失败输出和中间快照不删除。

原字节证据包：`E:/RAG quention/var/reports/s8-remaining-top1-20261020/evidence/final-raw.zip`，SHA256 `d127035b1dad8774a3cc258e2b2601300897cd21667805a5324c83548d743fb3`，5,473文件逐项校验。ECS原记录保存在 `/opt/rag-s8/s8-remaining-top1-20261020`，执行账本 `/opt/rag-s8/state/executions/s8-remaining-top1-20261020`；密钥/访问令牌不进入报告。

实际新增文件仅本报告、同名results.json及本地ignored `var/reports/s8-remaining-top1-20261020/`诊断工具/回执。不提交巨大原始包、不修改Gold、原件、索引、旧UNKNOWN或其他任务文件；不Push、合并、部署、打Tag或自动进入下一轮。
