# Rerank Top1 无下限保底：独立分支离线 A/B

## 结论

**完成限定实现与六例真实历史回执离线回放；决策 `KEEP_EXPERIMENTAL`。不推广生产。**

实验工作树：`E:\RAG-Worktrees\rerank-top1-experiment`；分支：`codex/rerank-top1-experiment-20261019`。起始 HEAD `78bf9839d2030749f715a9395ce355209d8f52ff`，开始时工作树干净。未修改 S6 工作树、日常服务配置、模型、数据库或索引；不 Merge、Push 或部署。

- **A**：直接从上述 Git blob 载入原产品 `rerank.py`：阈值0.3，空过滤结果只在 Top1≥0.15时保底。
- **B**：同一 Query、有序候选、同一真实模型回执，阈值仍0.3；只有成功返回合法候选且过滤为空时，保留最高分一条，不再要求0.15。
- **C**：`NOT_DEFINED`。Owner没有定义第三种策略，未自行设计。

新增 Provider/Embedding/问答 **0**，新增 Token **0**，新增 UNKNOWN **0**。历史调用不重复计入新增消耗，不重发 UNKNOWN。新答案质量与延迟均 **NOT RUN**。

## 最小实现

`backend/weknora_port/retrieval/rerank.py::rerank/filtered` 删除空过滤保底的绝对分数下限，并用实际最高分候选处理可能未排序的返回。`InvalidRerankResponse/_validate_raw_results` 拒绝非法/重复 index、bool index、NaN/Inf、非数值、缺字段及不能转换为有限数的超大整数；异常响应不作为保底依据。空真实回执仍 `SearchNothing`，没有Search候选及未配置Rerank的既有行为保留。取消继续抛出，不产生伪造分数；既有Provider调用异常合同没有被改成成功Top1结果。

Search、Weighted RRF、MMR、融合公式、Merge、Parent、邻接、FilterTopK、Context、模型及Prompt代码均未修改。B仅在实验分支生效，正式分支仍为A。非默认阈值>0.3时，已有合法Top1现在也会直接保底，因而不再进入原来的降阈值重试；定向测试明确记录这个派生变化，没有新增调用。

## 冻结证据与回放口径

- 0434采用 `E:\WK\run\s1-0434-section-20261019\real-check` 的真实新解析请求：包含物理第33页Coding time的实际Search候选及原始BGE回执。该次wire确实启用ModelPassage，A/B都保持这个输入，不改标题。
- 其余五例采用 `E:\WK\run\s1-rerank-input-20261019\live-*` 的原版Passage真实A回执。原始Provider `relevance_score` 与保存Adapter score逐项相等；A/B没有重新计算模型分数。
- 两组来源的查询/候选/配置不同，**只做每例内部策略对照**，不作为统一版本总体评测。五例TopK=5沿用S1已有离线replay固定值，不冒称原问题完整真实API Context；0434沿用该保存运行的正常runtime TopK默认30。历史没有捕获的运行细节不补造。
- 通过实际 `rerank → mmr → merge → filter_top_k → into_chat_message → prepare_generation` 回放。最后一步只构造输入，不调用模型；结果保存真实渲染正文及待生成消息，不能称为新真实模型输入送达。
- 已有553行只读source快照覆盖五例全部候选。0434上传汇总未SELECT邻接字段，因此另做**一次只读补充**：核实PG系统ID `7696088403504697382`、数据库、现存tenant1 local principal与保存owner一致、新doc/version/SHA不变；通过正式ScopedKnowledgeRepository读取126行，事务READ ONLY并ROLLBACK。它是当前来源补充，不是假称历史DB快照。每个冻结候选的正文、来源、版本、offset、parent/pre/next与来源快照严格相等后才回放。
- 5/5 S1样本的A最终IDs与既有native_analysis一致。0434的A重现原 `SearchNothing`。

完整候选顺序、原文、分数、SHA、A/B选择IDs、Merge正文、Context正文及各quote到达情况见同名results JSON。所有153个实际候选分数逐项保留，原始分数与融合分数字段分开。

## 六例结果

下表“目标”为需要检查的精确Chunk，不以同文档Summary代替。Context字符统计是产品渲染Context，未包含整段系统Prompt。

| 例 | Search数 | 目标raw分数／排名 | A/B通过MMR数 | A/B Merge数 | A/B Context字符 | 核验结论 |
|---|---:|---|---:|---:|---:|---|
| 0434第33页 | 30 | 0.1093258485／1 | 0→1 | 0→1 | 0→1048 | Coding time原图OCR成为锚点，现有邻接规则另回取真实第34页Kaggle文字 |
| Frost第二来源 | 30 | 0.0027902289／5 | 2→2 | 2→2 | 2632→2632 | 地点来源仍缺；已有高分第一跳使新保底不触发 |
| Romeo第二来源 | 30 | 0.0786516368／4 | 3→3 | 3→3 | 2226→2226 | 第二标注来源仍缺；既知干扰电影片段仍保留 |
| 0959成功对照 | 30 | 0.3873645067／2 | 2→2 | 2→2 | 1720→1720 | radiowaves与microwaves两条原文事实均保留，无证据退化 |
| Omni page007 | 30 | 0.0944274515／2 | 1→1 | 1→1 | 295→295 | Summary高分，原始数字OCR仍被过滤；本方案对此无效 |
| 缺答案反例 | 3 | 无答案目标；Top1 Summary 0.8761125803 | 1→1 | 1→1 | 295→295 | 已审查三条主题Summary不含所需数字；仍有主题Context，不能判有答案 |

0434目标ID `684b1220-4546-4235-8b18-53b7d9be3baa`，wire index1；B的**原始分数0.1093258485**保持不变。其Search分数0.0155311355，通过原融合公式得到 **0.1702548498**。后者不是BGE重新评分，也不表示已通过原0.15原始分数下限。

0434的现有邻接Source为同doc `2f283a53-de65-41c1-a1f5-e8a7ea75e64d`／version `43204b4c-5253-4106-8e6d-f43bf097708a`，page33指向page34的 `956cf096-d183-4a96-adf5-75e9fd71a4e2`。该第34页**不在原Search候选**，是Merge回取后才出现；原Kaggle段落完整进入离线待生成正文。**相邻片段同时送达不等于“Coding time章节包含Kaggle”的跨页语义已证明，也未执行最终答案验证。** 新Context还包含相邻神经网络背景与OCR噪声，其全部相关性没有独立标签，不能记新增噪声为0。

Romeo的缺失标注包含Romeo人物及死亡句；第一来源已有Whiting／Zeffirelli导演关联。此处同时报告标注支持来源与逐句送达，不将第二来源缺失机械解释为导演答案必然没有事实支持。

## 三项指标与决策

| 指标 | A | B | 分母及限制 |
|---|---:|---:|---|
| 已审查原文quote字面到达 | 4/10 | 6/10 | 5个正向案例的10段检查；不含缺答案例、已知干扰；不是QA准确率/语义支持率 |
| 已知干扰电影quote到达 | 1/1 | 1/1 | 只有这一明确干扰quote有标签，其余未审查项为UNKNOWN |
| `SearchNothing` | 1/6 | 0/6 | 仅这六份冻结输入，不能外推真实用户空结果率 |
| Context字符总量 | 7168 | 8216 | 增加1048（14.62%），全部来自0434；不同来源配置仅作本次描述 |

缺答案反例原Top1已经超过0.3，两臂原样保留Summary，**没有触发B新增的低分保底路径**。因此“无新增已标干扰”不是“新增低分保底安全”的证明。全量无关候选／邻接片段数、实际答案质量、Token增量和云延迟影响仍UNKNOWN/NOT RUN。

选择 `KEEP_EXPERIMENTAL` 的理由：确实恢复一例此前被绝对分数拒绝的真实锚点，并由已有机制找回邻接原文；成功对照不变。但另外三类必要来源/OCR竞争问题不改善，低分缺答案风险缺少有效真实反例，尚不足以推广。`PROMOTE`证据不足；已有局部来源送达收益，也不以六例宣布彻底无效或`REJECT`。

## 实际命令与退出码

```powershell
$env:PYTHONIOENCODING='utf-8'
& 'E:\RAG quention\.venv\Scripts\python.exe' -B scripts/rerank_top1_source_snapshot.py --config 'E:\WK\run\s4-quality-20261019\local.json' --saved-upload 'E:\WK\run\s1-0434-section-20261019\real-check\upload\source-and-index.json' --saved-request 'E:\WK\run\s1-0434-section-20261019\real-check\question\evidence\b4cc9b77c3c0406c85aa3d961322c6c1.request.json' --output 'E:\WK\run\rerank-top1-20261019\source-0434.json'
& 'E:\RAG quention\.venv\Scripts\python.exe' -B scripts/rerank_top1_fallback_ab.py --extra-snapshot 'E:\WK\run\rerank-top1-20261019\source-0434.json' --output docs/evaluation/RERANK_TOP1_FALLBACK_AB.results.json
```

两条均 **exit0**：126行来源补充；6例×2策略完整原生离线回放。未重跑75次阈值网格，没有Provider实例化、发送或新答案。源文件SHA保存在results。

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:PYTHONIOENCODING='utf-8'
& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest --noconftest -p no:cacheprovider -q --tb=short tests/weknora_port/test_rerank_top1_fallback.py tests/weknora_port/test_pipeline.py tests/weknora_port/test_p28_native_fallback.py tests/weknora_port/test_rerank_model_passage.py -k 'not default_model_template and not source_failure_is_terminal'
git diff --check
```

最终 **exit0，106 passed、5 deselected，2.03s**；`git diff --check` **exit0**。覆盖本次Top1、畸形响应、正常排序、MMR、Scope、原生空结果fallback及ModelPassage合同。新增测试修复前为 **19 failed、7 passed，exit1**，不是历史质量成绩。

首次 `git diff --cached --check` 发现新测试文件EOF多余空行，**exit2**，当时未执行commit；仅删除该空行后重新暂存并检查，最终 **exit0**。

首次把完整四模块一起运行：**105 passed、5 failed，exit1**。5项为模板资产原始字节SHA装配失败，以及4个仍传已移除 `final_evidence_projection` 参数的旧测试。另用 `git show 78bf983…:backend/weknora_port/retrieval/rerank.py` 在独立Python进程装入未修改A模块，仅重跑这5项：**同样5 failed，exit1，0.73s**，确认并非B引入；未修改这些旧问题或冒称全套通过。复现命令：

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -B 'E:\WK\run\rerank-top1-20261019\baseline_existing_failures.py'
```

仅为新实验语义，原低分=空结果的两项测试改为真正空response；高阈值重试测试更新为合法Top1直接保留。没有删除反例或改Gold。审查提出的超大整数有限性检查溢出已修复并加入定向反例。

## 交付范围与停止

实际修改：`retrieval/rerank.py`、三个受影响测试文件、两个一次性只读/离线脚本、本文及results JSON。新增依赖、Schema、索引、生产配置均0。旧Gold、原件、Provider回执和UNKNOWN不修改；正常工作树与S6分支不合并本实验。

本轮止于离线工程结论。若Owner后续评估推广，最小缺口是能真实触发低分Top1的缺答案负例及一次正常API答案验证；本轮未获云授权，也未自行执行。
