# A9 基线独立评分与事实审查

**ECS正式问答基础链已真实跑通。9/9应用答案完成并持久化，6题有实际原文Context，3题为SearchNothing后的metadata fallback。质量问题仍明显，不能将运行成功当作9题答案正确。**

机器最终报告：`a9-independent-final.results.json`；完整逐请求、Context、Chunk及原文身份：`a9-independent-audit-v2.json`；首题沿用`first-case-score.results.json`。

## 1. 目标与改动

本次仅读root导出的A9证据，仅向scoring目录写离线评分工具、JSON/Markdown报告和本地PDF渲染图。未发POST、Provider请求或修改数据库/Gold。评分字段及逐题事实criteria只在事后读取。

## 2. 实际命令与结果

工作目录`E:/RAG quention`，Python为`E:/RAG quention/.venv/Scripts/python.exe`，均带`-B`。

| 实际执行 | exit | 输出 |
|---|---:|---|
| `a_baseline_review.py --case-substring multifield --output …/scoring/a-positive-context-health.json` | 0 | DSM原始参数、wire/Provider/SSE/PG、原始source/version；初版整串Context wrapper比较false |
| `a_baseline_review.py --case-substring multifield --output …/scoring/a-positive-context-health-v2.json` | 0 | 每Chunk事实正文实际wire绑定true，正常基础链PASS |
| `a_baseline_review.py --output …/scoring/a9-independent-audit.json` | 0 | 初版A9，88080Token，UUID映射及HTML转义需修正 |
| `a_baseline_review.py --output …/scoring/a9-independent-audit-v2.json` | 0 | 9题独立对账、实际同源别名、HTML实体反转义逐Chunk绑定 |
| 本地Python调用`supporting_sentence_review`写`a9-hotpot-exact-sentence-review.json` | 0 | Leyden3/3、Frost1/2真实支持句送达 |
| 已存在Poppler `pdftoppm.exe -f 28 -l 29 -scale-to 1600 -png <原PDF回读文件> …/scoring/pew-poppler` | 0 | 相关完整页面PNG，实际视觉核对物理28页/印刷26页 |
| `finalize_a9_review.py` | 0 | 9题事实结论、1033文件manifest校验、361 HTTP回读校验、首题原回执复用证明 |

原样完整命令示例：

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B 'E:/RAG quention/var/reports/s8-1-ecs-20261020/scoring/a_baseline_review.py' --output 'E:/RAG quention/var/reports/s8-1-ecs-20261020/scoring/a9-independent-audit-v2.json'
& 'E:/RAG quention/.venv/Scripts/python.exe' -B 'E:/RAG quention/var/reports/s8-1-ecs-20261020/scoring/finalize_a9_review.py'
```

前两版报告保留，没有覆盖。第一次UUID映射把ECS同字节PDF别名漏算为0；最终版根据实际HTTP源文件SHA、KB、Chunk版本修正，仅改评分侧映射。部分整串Context比较最初false来自生成阶段XML重新组织/HTML实体转义，实际6题每个选中Chunk正文还原后均进入真实wire，未发现事实内容丢失。无新增依赖安装。

## 3. A9逐题成绩与事实

所有分数按0–1报告。LongBench两任务分别计分；MML为项目规范化诊断，未运行官方GPT评分。完整应用答复含Sources行均保留为指标输入，不按Gold字符串裁剪。

| A题 | 适用指标实际分数 | 实际事实支持/未答 | 首次必要来源或事实问题 | Token / 秒 |
|---|---|---|---|---:|
| SciFact1304 | Search文档Recall@10=0，MRR@10=0；BM25=1/.25；无答案/立场Gold | FAIL：fallback生成无输入支持的GFRα/心衰/IGF-1细节 | Gold原始召回存在，RRF chunk57/92截到Top30之外；Rerank随后全清空 | 15227 / 6.650 |
| MIRACL5704915#0 | Search与Context正qrel文档Recall@10=1，MRR=1；无答案Gold | PASS：海淀区关系有真实源原文；模型输入含派生summary和其他原文，summary不冒充原文 | 无必要事实丢失；grade0单列不作正例 | 7880 / 3.807 |
| LongBench DSM | 中文F1=0.260870 | PASS：原文与答案均为星下点分辨率优于5m | 无；Sources/完整句使字符串F1较低，不代表5m事实错误 | 8593 / 5.127 |
| LongBench DuReader | ROUGE-L=0.255639 | PARTIAL：七大原因类别有覆盖；传染性单核细胞增多症细项遗漏；原文0–40正常范围被写成轻度升高；慢性病毒性肝炎限定词丢失 | 检索完整，生成未保留限定条件与数值含义 | 16885 / 9.026 |
| Hotpot Leyden/Tribometer | 完整答复EM=0，F1=0.190476；至少一source=true，全部source=true；必要支持句3/3 | PASS核心答案Pieter van Musschenbroek和1746原文依据完整 | 无；Sources纯文本行漏Tribometer文件，是来源列举完整性问题 | 8103 / 6.019 |
| Hotpot Frost Place | EM=0，F1=0；至少一source=true，全部source=false；支持句1/2 | PARTIAL：正确识别研讨会，缺Franconia地点；明确不足，未编造地点 | 地点来源raw vector9/11、RRF19/23进入Top30，Rerank .0006216/.0038396后丢失 | 6612 / 7.746 |
| MML0434 | 项目EM=0，character F1=.081522 | 未答Gold；metadata下作证据意识拒答，不能算正确有依据回答 | 正PDF来源命中；30个实际Rerank passage无Coding time；最高.017578是imports代码，全部清空 | 8402 / 16.385 |
| MML0687 | 项目EM=0，character F1=.040816 | FAIL：未回答median，平均C−/C冲突处理错误，错误声称没有分布信息 | 原图表结构解析噪声＋生成忽略原文C−与已有百分比分布 | 9119 / 9.892 |
| MML0958 | 项目EM=0，character F1=.114833；完整回复非literal list，不强行提取 | FAIL：metadata fallback答mirror/water而非Gold shiny object/mirror；具体物理细节无实际输入支持 | 最高.049305为微波炉shiny surfaces，全部Rerank清空；拒答能力未体现 | 7259 / 10.300 |

不汇总成一个跨数据集“正确率”。样本不足以推断总体成绩或显著改进。Hotpot EM0不自动视作事实错：Leyden核心事实正确，但完整中文说明＋Sources与简短英文Gold字符串不相同。

### 完整证据链正例

DSM原文Chunk `e5cf40df-01e7-4429-a669-b7d31b302a58`，doc `cc6ea68c-f3e7-48ce-9d14-0b73e77584c1`，version `934dc14a-d0c8-4c41-a0d1-1499c17e0cf9`，原文件SHA `a00fca96fed4629eb7d38db5b18b25f50d5b533f8e420ccb2f72366d402fa654`。实际HTTP回读、版本/KB、源字节和正文一致，事实“影像星下点的分辨率应优于5m”进入最终真实模型messages，wire SHA同时匹配授权ledger和PG Provider回执。原始模型答案=SSE=PG=history=result。由此可确认正常RAG链不是仅fallback跑通。

### 来源存在与支持句是不同指标

Frost在完整Top30内仍有两个必要来源，但按文档去重的Recall@10只有.5，Rerank后Context也只剩1个必要来源，不能把Top30来源数或总文档命中当作两跳成功。Leyden的3条原始支持句逐条存在于实际最终wire，并已绑定可回读原文，才可判断核心事实支持。

MML0434当前运行doc `4019d2aa-c21d-42c2-8e8f-4d5aee4039ce` 与冻结旧doc不是同一UUID，HTTP原件SHA与冻结PDF `40a74d3f…`完全一致。最终版保留实证同源别名，不修改Gold，检索source Recall从初版误记0修正为1。题目事实关联是否存在于Context仍独立判定。

### Pew原图复核

完整原件物理第28页（印刷26页）图示Trump A/B30%、C19%、D15%、F35%，正文average C−；Clinton A/B43%、C20%、D16%、F21%，average C。实际wire含原文C−及Clinton分布，另有扁平表格噪声把Trump读成C。答案“二者平均均C”和“完全缺少分布”不符合该输入；也未回答median问题。

Clinton累计F+D=37%、加C=57%，第50百分位位于C。Trump显示F+D=50%，上面等级合计49%，涉及整数取整与未显示未答，严格中位边界需保留限制。此处不把average改叫median；不因Gold里写Clinton就给生成答案正确。native Gold页码基制继续UNKNOWN，视觉复核的物理页定位不偷偷转换为官方页召回指标。

## 4. 健康、回归与保护

- 9/9原始模型输出、SSE、PG、历史与结果逐字一致；原问题、KB、空document_scope和run/session身份匹配。首题四份question/SSE/history/result与首份导出SHA完全相同，复用未重发。
- 1033导出文件manifest逐SHA/长度核验；共361个history/chunk/source HTTP回读均200且保存字节SHA一致。正常Context所选Chunk的source SHA、KB与version均匹配。
- 36 Provider调用全部HTTP200、协议完成、SETTLED；9个child CLOSED；新增UNKNOWN0。流式raw有[DONE]/stop/usage，网络EOF未观测标志单列保留。
- 所有最终生成均Cheap Qwen/Qwen3.5-4B；本轮实际wire 0.7/max_tokens2048/thinking=false。QU同模型0.3/max_tokens150/thinking=false；独立Embedding BGE-M3和Rerank BGE reranker未更换。
- 实际Token：rewrite21401、embedding128、rerank47493、answer19058，总88080。9次POST用时合计74.952秒，均8.328、median7.746、最短3.807、最长16.385。该合计不含实例启动、查询回读与串行运维等待，不代表整批墙钟耗时。
- structured knowledge_references为0；当前产品输出是Sources纯文本文件名，因此结构化引用点击链路不能写PASS。原文件/Chunk HTTP回读已有独立证据。无必要来源身份错位、越权或用量不明停止条件。
- 本轮只审已保存数据，不重新OCR/Embedding/建图。Omni保留历史3页项目NED映射与21页NOT_SCORABLE，RAG分母0；官方布局/表格/公式分数NOT_RUN。

## 5. 风险与下一步

建议重点登记P1质量问题：①SearchNothing仍可自由知识生成且混入貌似Sources；②RRF候选截断或Rerank去掉必要第二来源；③原始数值/限定词与派生/解析噪声冲突时的回答校验。此为审查建议，不自行批准延期，也不改本轮生产默认。

B/C对照具备基础链健康前提，仍分别要求原题原模型配置、互斥开关和Graph合法来源预检。B保留一个低分Chunk不等于事实改善；C的实体边也必须带回必要原文地点事实。当前未接受新的实验成绩，不提前宣布B或C有改善。

本目录无commit、Push或Tag；S8.2未执行。后续4臂如有新回执，继续用同一离线指标与逐事实口径评分。A9本身不支持直接全量扩大为S8.2正式质量验收通过；应把观察到的失败模式和授权范围单列供Owner决策。
