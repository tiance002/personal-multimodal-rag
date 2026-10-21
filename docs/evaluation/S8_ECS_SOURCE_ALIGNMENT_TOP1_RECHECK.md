# ECS 来源全量对齐与 Top1 九题复验

## 结果

本轮请求范围已完成：不是只更新0434。共28个来源/KB组合、27个原件SHA、52个旧版本，均建立新版本并完成真实索引。原9题各提交1次正常FastAPI问答，9/9 SSE/PG/历史终态完成；人工事实审查5 PASS、3 PARTIAL、1 FAIL。该人工结果不是六数据集统一官方准确率，也不表示S8全量评测完成。

代码基线 `3d90756f064ea28810beac0fcc250e05d539d954`；分支 `codex/s8-six-dataset-evaluation-20261019`。真实镜像 `sha256:8e31042a532fc80be6f5595407fcaf30da37147a88edb1d998a773bda3a2ffa8`。本地交付commit由 `git log -1 -- docs/evaluation/S8_ECS_SOURCE_ALIGNMENT_TOP1_RECHECK.md`及最终交付消息核对。主工作树16个既有未提交路径保持原样，本轮不改生产代码、Schema或租户归属。

## 为什么以前不同

0434原PDF SHA与原始MinerU SDK middle相同；SDK第33页的image_body原本为空。当前本地处理会补MinerU本地ONNX OCR，旧ECS仍使用P11迁移的52条正文，没有Coding time。当前得到125条正文、78节、62条image-body OCR记录；原有邻接关系可正常连接Coding与Kaggle内容。不是仅模型名称/参数不同。

发现并更新的全部范围：

- B3 PDF1组；CatvsDog同原件在2个KB共2组；24页Omni各1组（每组原来同时启用2个旧版本）；Pew原生PDF1组。
- 当前正文629条，Summary28条，657条1024维真实向量全部启用。52个旧版本及474条旧向量只停用检索，原字节、Chunk、向量内容、版本及历史回读保留。28份新原件SHA与冻结来源一致。
- 27组使用MinerU4.0.12原SDK缓存＋当前本地image-body OCR/规范化；不使用Tesseract、云OCR或Caption。Pew保持原生文本PDF解析，其当前本机/ECS正文和chunkshape SHA完全相同；差异定位到已有`2e21405`字形间距修复。
- 3个受影响tenant332 KB的摘要绑定从已有Xing调整至已配置Qwen，并保存旧配置；全局角色与tenant1不变。普通纯文本语料不属于这次已识别的旧多模态处理差异，未无条件重建。

## 九题实际结果

每题完整KB范围、独立session/request_id。Graph关闭，Top1无下限及ModelPassage增强开启；正常阈值仍0.3。QU实际Qwen3.5-4B、temperature0.3/max_tokens150/thinking=false；最终Cheap实际Qwen3.5-4B、temperature0.7/max_tokens2048/thinking=false。9题均未升级Expensive，生成finish_reason均stop。

| 样本 | 人工事实验收 | 证据与答案 | POST全程秒 | 全调用Token |
|---|---|---|---:|---:|
| SciFact1304 | PARTIAL | 目标来源在Search，重排仅保留无关Tafamidis；回答合理说明不足，未完成目标事实回答。 | 7.71 | 12910 |
| MIRACL5704915 | PASS | 海淀区，真实原文及生成输入支持；人工事实审查，不是官方生成Gold成绩。 | 6.18 | 8206 |
| LongBench-mf1a6 | PASS | “优于5m”有原文支持；全文F1为0.20689655，语义正确与字符串分数分列。 | 6.19 | 8757 |
| LongBench-DuReader11b3 | PARTIAL | 原因原文已送达；漏单核细胞增多症，新增“2倍”阈值，并混淆病理/非病理分类。 | 21.14 | 17289 |
| Hotpot5a729fe | PASS | Pieter van Musschenbroek正确；两跳3条必要支持事实均进入生成输入。 | 10.45 | 9222 |
| Hotpot5a7631 | FAIL | The Frost Place地点第二跳丢失，Context仅1/2必要事实；答案无依据推测纽约。 | 49.00 | 8110 |
| MML0434 | PASS | Coding time!保留后，现有邻接Merge带入Kaggle Cat vs Dog及25k图片原文，答案正确。 | 12.97 | 8024 |
| MML0687 | PARTIAL | Clinton核心答案正确且有据；将原文平均评分称作中位数，统计措辞不正确。 | 17.45 | 8968 |
| MML0958 | PASS | 当前OCR原文明确含shiny object or mirror，真实Context/wire及答案均支持。 | 10.73 | 7639 |

0434实际raw Rerank Top1为0.1073344499（composite0.1691335328，二者分开）。它低于0.3及旧0.15保底，当前无下限Top1将Coding片段保留，现有邻接Merge带入Kaggle原文。最终真实输入包含两者，回答为“Kaggle's Cat vs Dog dataset”，25k图片也受原文支持。0958真实输入明确包含“Reflecting surface, e.g. shiny object or mirror”，不是凭常识猜对。

SciFact/MIRACL无生成Gold，不编造官方问答准确率。LongBench按冻结任务F1/ROUGE计算；MML只列项目规范化/人工事实审查，官方GPT Judge NOT RUN。来源ID命中与必要事实送达分开。新旧Rewrite、来源版本及ModelPassage同时有变化，不能将全部收益归因于Top1单变量。

## 用量、耗时和内存

- 来源更新96次Provider、141,106Token；9题36次Provider、89,125Token。合计132次、输入224,005＋输出6,226＝230,231Token，全SETTLED，新UNKNOWN0。
- 全部实际调用为SiliconFlow：BGE-M3 77次/88,845Token；Qwen46次/89,916Token；BGE Rerank9次/51,470Token。DeepSeek已在原DYNAMIC允许范围但本轮实际0次。
- ECS旧UNKNOWN2条及摘要校验值不变。本轮独立master已CLOSED，未使用软件空间撤回；不是复活旧授权或创建账户/月度预算系统。原API已恢复healthy且默认云发送关闭。
- 9题POST实测范围6.18–49.00秒，不是单次生成耗时。串行任务UTC04:22:59–04:32:38（包含B3已完成项的只读跳过）。
- API均实测1GiB限制；1秒采样的宿主机MemAvailable谷值1,970,536,448bytes＝1.835GiB。容器cgroup memory.peak最大1,033,715,712bytes＝985.83MiB（0434），接近1GiB上限，无OOM。本结果是采样最小值，不宣称连续瞬时谷值；peak包含cgroup计入的缓存，不等于Python RSS。

## 验证、工具问题与证据

23项来源工具定向契约、18项QA工具测试、10项冻结准备测试退出码均0。全部28来源另在实际ECS容器零Provider重放，prepared chunkshape与本地逐源完全一致。真实28来源与9题命令全部退出0。来源新旧身份、每题真实wire/response、SSE、PG、HTTP回读均保存在原字节证据包中。

首份导出器重排JSON导致HTTP manifest字节哈希不符，已保留首份失败记录；修复为原字节导出后，9题readback_failures均0。此为零Provider工具修复，不是重新问答、修改历史回执或隐藏失败。其他准备脚本属性/字段错误均发生在零发送预检并已保留。

9/9实际请求满足原始问题/范围冻结、无Gold字段、同一run身份、Context/wire绑定、SSE/PG/历史答案一致；363次HTTP回读状态/字节数/SHA检查无失败。

主要实际命令（工作目录`E:/RAG quention`，以下最终版本退出码均0，详细stdout/stderr保存在本轮证据目录）：

```powershell
python var/reports/s8-aligned-top1-20261020/stage_all_remote.py
python var/reports/s8-1-ecs-20261020/ssh_run.py var/reports/s8-aligned-top1-20261020/remote_finalize_sources.py ../s8-aligned-top1-20261020/all-source-freeze-v2 320
python var/reports/s8-1-ecs-20261020/ssh_run.py var/reports/s8-aligned-top1-20261020/remote_first_source.py ../s8-aligned-top1-20261020/first-source 450
python var/reports/s8-1-ecs-20261020/ssh_run.py var/reports/s8-aligned-top1-20261020/remote_start_all_runner.py ../s8-aligned-top1-20261020/runner-start 60
python var/reports/s8-1-ecs-20261020/ssh_run.py var/reports/s8-aligned-top1-20261020/remote_finish_all.py ../s8-aligned-top1-20261020/final-operation-summary 120
python var/reports/s8-1-ecs-20261020/ssh_run.py var/reports/s8-aligned-top1-20261020/export-tools/remote_export_aligned.py ../s8-aligned-top1-20261020/raw-evidence-export 180
python var/reports/s8-aligned-top1-20261020/download_final_evidence.py --raw
.venv/Scripts/python.exe -B var/reports/s8-aligned-top1-20261020/scoring/score_aligned_top1.py --evidence-root var/reports/s8-aligned-top1-20261020/raw-final-evidence --source-snapshot var/reports/s8-aligned-top1-20261020/raw-final-evidence/metadata/actual-source-snapshot.json --output var/reports/s8-aligned-top1-20261020/scoring/R9-final-scores-v2.json
python var/reports/s8-aligned-top1-20261020/build_final_report.py
git diff --check
```

权威证据 `E:\RAG quention\var\reports\s8-aligned-top1-20261020\raw-final-evidence`；ZIP SHA256 `7a3063e740ff21e18cf3d762d0713f457f993b221a84756f8e00e357f503760b`，1859文件全部校验。机器报告包含28个新旧doc/version映射、逐题答案/来源指标/用量、命令退出码、资源记录和详细审查路径。Gold只在调用结束后评分，未注入检索或生成。

## 遗留与下一步

1. SciFact相关证据和Frost第二跳仍在Rerank丢失；Top1无下限不能解决非Top1第二跳，并会让无关Tafamidis进入Context。
2. DuReader漏项/错误分类及Clinton平均/中位数混用属于生成质量问题。保留本次失败，不追加问答刷PASS。
3. 当前对齐版本保留；本轮不推广生产全局Top1/ModelPassage、不修改阈值、不启Graph、不扩大为六套全量重测。API1GiB下0434峰值接近限制，后续分配应参考实测，不自动调整。
4. 本轮仅提交报告/结果，不自动Push、部署新版本、合并或进入下一阶段。
