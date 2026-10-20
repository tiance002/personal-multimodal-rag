# S8.1 B/C 独立对照审查

**Top1 有一次证据不足应答改善，尚未获得正确完整答案；Graph 未补齐任何新的必要事实。均不足以推广为默认。** A9质量评分复用已完成结果；本次只审新完成的B0958/C两题，以及B0434合法收口。

机器报告：`final-bc-independent-audit.results.json`。内含全部原始QU输出、每通道候选ID顺序/正文SHA、Rerank输入及按index原分、Context/实际messages、Graph边与quote、逐调用usage和51调用stage/arm汇总；不将原始来源ID当事实支持。

## 实际命令与结果

工作目录`E:/RAG quention`：

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B 'E:/RAG quention/var/reports/s8-1-ecs-20261020/scoring/final_bc_review.py'
```

**exit0**：3份新完成回执独立核对；最终包1460个manifest文件SHA/长度全部匹配；A9未重新计算质量。全部新完成题实际wire SHA同时匹配ledger/PG，Provider HTTP200/协议完成，raw答案=SSE=PG=history=result；每个最终Chunk事实内容在实际wire出现，source文件SHA/KB/version一致。新增133次HTTP回读均为保存回执的200/字节SHA核验，无新HTTP或模型请求。

## 对照结论

| 对照 | 输入与混杂 | Context/必要事实 | 答案与分数 | Token / POST秒（A→实验） |
|---|---|---|---|---:|
| B0434 | 首次OOM137；只3次Provider已结算；无完整B答案 | 只到Rerank开始，MMR/Merge/Context未完成 | **NOT_SCORABLE_EXECUTION_INCOMPLETE**，不能推断floorless实际保留或答案改善 | A8402/16.385；B部分7621/未完成 |
| B0958 | QU输入完全相同，输出仅JSON空白差异；有效Rewrite相同；vector/BM25各88、全部ID顺序与正文SHA、Top30和Rerank body均同A。22/30分精确相同、maxΔ0.0000531045，Top1同.0493047945 | A无证据Context；B实际保留1个原文微波炉“shiny surfaces reflect infrared”Chunk，1734 Context字符。Gold所需两个反射面例子仍未齐全 | A无依据答mirror/water；B明确说只知shiny surfaces、缺具体例子。**不足应答改善，任务未成功**。项目EM0→0，字符F1 .114833→.212766；非官方GPT | 7259/10.300 →7573/7.488 |
| C Leyden/Tribometer | 相同QU输入但Rewrite从`Who invented…`变为`Who is credited with inventing…`；vector90→84，BM25集合/顺序和Top30/Rerank输入改变，共有ID正文SHA不变。**有Rewrite混杂** | A8块5669字→C8块5847字；C真实Graph加1块206字“jar电容单位曾用于英国海军”，未补必要发明事实；必要source全部命中，支持句仍3/3 | 两者核心Pieter van Musschenbroek答案有原文支持。完整答复EM0→0，F1 .190476→.222222不代表事实改进。C列对两个必要源文件，但多了“tribometer早期形式”等轻微未支持描述；**无可证Graph核心增益** | 8103/6.019 →8302/13.744 |
| C Frost Place | 相同QU输入但Rewrite从关键词串变为完整问句；vector122→115、BM25/Top30顺序与输入改变，共有正文SHA不变。Search Recall@10 .5→1属检索变化，不能归因Graph | A2块2632字→C3块3387字。C Graph3条路径均为Jeanne诗集`author of`，来源Chunk已被正常检索保留，Graph新增0。必要地点来源仍未进最终Context；支持句仍1/2，至少一个source=true、全部=false | A/C均只识别研讨会并明确缺位置，未答Franconia。EM/F1仍0/0。**没有两跳事实改善** | 6612/7.746 →7127/5.109 |

Context字符是`finalized_context.rendered_contexts`的真实字符数。实际wire user字符另存JSON：B0958为86→1786；C Leyden5099→5346；C Frost2260→3073。A fallback系统messages含metadata不计原文证据Context，因此A0958 Context为0。生成重新渲染XML/HTML转义后，按事实正文还原核验，不用整段wrapper SHA假装事实已送达。

所有完成题Final Qwen/Qwen3.5-4B实际wire为temperature0.7/max_tokens2048/thinking=false；QU同模型0.3/150/thinking=false，无实际升级。记录到的每题只有一个检索capture、同一query的vector/BM25两通道，没有额外Expansion Provider调用或输出；不补造未记录Expansion。后3题有工具内存护栏修复，延迟只作本次实测，不能作无混杂性能增益结论。

### Graph路径的真实含义

- Leyden：`ge-a175…`，source `087c3950…`，version `5b66d1e2…`，quote为“A jar was an early unit of capacitance once used by the British Royal Navy.”，predicate=`once used by`。quote offset、原文版本和实际wire均有效，但不是两种发明与人物的必要关系；query_relevance保存为[0,0]，属于噪声贡献。
- Frost：`ge-54bb…/ge-671…/ge-71a…`均来自`abc3a6aa…`、version`de3ec8ca…`，是Jeanne与三部诗集的`author of`。路径hops均1，无法桥接研讨会到Franconia。实际源正文回读/quote有效不等于回答蕴含成立。
- 只复用既有19份Graph evidence/28条edge；新建图/Graph Provider调用0。readiness只保存predicate和quote，未保存本次匹配实体ID标签，报告明确`NOT_CAPTURED`，未编造节点名或实体路径。

## B0434收口与总用量

旧`execution.STOP.before-audited-close.json`保持STOP，3个entry及held7621与最终CLOSED逐项完全相同；保留stop_reason/previous_status。最终仅撤回未用额度；原driver-result、SSE SUBMITTED与PG pending均保留，没有改成完成或UNKNOWN，没有重发。旧中断审查另见`b0434-partial-review.results.json`。

最终13次正式POST尝试：**12 COMPLETE＋1 OOM未完成**；51 Provider全部SETTLED，父授权和13 child全部CLOSED；新UNKNOWN0。不要将51次与13次相加，也不要把B0434已结算Token记0。

| 实际阶段 | Provider次数 | 输入Token | 输出Token | 总Token |
|---|---:|---:|---:|---:|
| Query Rewrite | 13 | 30511 | 438 | 30949 |
| Query Embedding | 13 | 199 | 0 | 199 |
| Rerank | 13 | 63990 | 0 | 63990 |
| Answer | 12 | 21298 | 2267 | 23565 |
| **总计** | **51** | **115998** | **2705** | **118703** |

| 臂 | 次数 | 输入 | 输出 | 总Token |
|---|---:|---:|---:|---:|
| A9 | 36 | 85929 | 2151 | 88080 |
| B2（含OOM3次） | 7 | 15062 | 132 | 15194 |
| C2 | 8 | 15007 | 422 | 15429 |

真实usage来源为每个CLOSED child的SETTLED entry；逐调用request_id/model/input/output/total保存在机器JSON，完成题同时匹配PG Provider回执。原始流保留stop/usage/[DONE]与networkEOF标志区别，未把未观测EOF改成true。Structured references仍空，Sources是文本；实际原文/Chunk HTTP回读已核验，不能称结构化点击引用通过。

## 交付限制

本次独立审核无云请求、DB写入、模型替换、Gold改写或重发。B只有1题完成、C有实际Rewrite混杂；不作显著性、总体正确率、官方GPT/全部benchmark或推广结论。保持B/C实验状态，S8.2仅提出受控范围建议，由Owner决定；不自动执行。
