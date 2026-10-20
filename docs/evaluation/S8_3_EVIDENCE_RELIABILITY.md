# S8.3 原文证据可靠性与任务清理

本轮 **工程 OFFLINE_PASS，阶段 PARTIAL；REAL_QA_PASS = NOT_RUN**。三个确定性修复已实现、本地提交，真实模型质量改善等待独立授权后的 ECS A/B。新增真实 Provider 0、Token 0、UNKNOWN 0；所有历史 UNKNOWN 保留，未重发旧问题。

## 版本与范围

- 起点 `e1ef6c05fe4e62c727508bfebc169366c4ecdc0b`；实现 `e8c3097d1de3552ca91b7e2987159b73bc1907cb`；分支 `codex/s8-six-dataset-evaluation-20261019`。起点 S8 工作树干净。
- 最终交付提交由 `git log -1 --format=%H -- docs/evaluation/S8_3_EVIDENCE_RELIABILITY.md` 或交付消息核对。
- 实际变更：5个生产文件（Search、Merge、source_evidence、pipeline、service）、5个定向测试文件、1个离线回放脚本，以及本报告/JSON。
- 本轮未连接或部署 ECS；最后保存运行镜像为 `sha256:8e31042a532fc80be6f5595407fcaf30da37147a88edb1d998a773bda3a2ffa8`，不能把新本地补丁当成已在 ECS 运行。
- 原主工作树16个既有未提交路径/20个文件，末次状态和逐文件SHA与本轮记录一致。Gold、来源、数据库、索引、旧授权和账本无写入。locked holdout `1303/338/921` 排除于优化样本。

## 确认的问题与修复

| 位置 | 原行为/最小反例 | 本轮行为 |
|---|---|---|
| `search.deduplicate()` | Summary排前、正文相同但ID不同，原文在Search就被正文签名删除 | 候选模式按KB/document/两个版本字段/Chunk身份/内容类型隔离；原文与生成内容保留独立身份；同ID一次，身份域冲突直接拒绝 |
| `partial_overlap()` | 同文只差100/200、日期、not、增加/减少时，token包含率仍超过0.85而删除证据 | 候选只允许同来源域、同内容类型的**原始文本完全相等**去重；大小写及布局保持，不用Jaccard证明事实等价 |
| `merge.sequential()` | 分组只有document/type，跨KB或版本可能合并；外库同文可能先被去重掩盖 | 候选分组加入来源域，Scope在去重前校验；缺失/冲突版本不建立不同ID的来源等价关系 |
| `expand_source_evidence()` | 回取原文后仍把冲突Summary一并送入生成；回取失败仍保留摘要当事实 | `bounded_source_v1`中Summary/table_summary只作定位；最终投影使用独立原文，未获原文记录INSUFFICIENT_SOURCE；全空走既有SearchNothing/fallback |
| 已选合并块 | 只验证真实短文本是已选内容子串，未验证额外内容和旧偏移；附加断言仍可标SOURCE_AVAILABLE | 按原Chunk、已声明子块及Parent有界回读，拆成各自可回读的完整来源块；身份/偏移/版本不符或超预算拒绝；保留原anchor评分，附加回读块不伪造Rerank分数 |
| `service` | 完成Task留在_tasks、_active不清，停止监听器不退出 | 按确切Task实例清_tasks；finally按运行所有者清_active并取消/等待监听器；background_errors最多128条安全错误码 |

本地起点已有部分 Summary/原文 Merge 分离和 `get_source_unit()`。这部分复用并回归，没有重复建设。S8保存回执的source_evidence_mode均为off，历史运行代码也早于本地当前代码；不能把所有历史损失描述成最新代码尚未修的同一Bug。

`storage/knowledge.py`现有服务端tenant解析、Scope/current version/启用状态/parent链接/原始偏移验证继续使用；新增投影边界对返回行再次校验。`generation/context.py`和Provider Adapter保持原样，通过它们验证最终请求体。所有候选改动由已有`source_evidence_mode=bounded_source_v1`启用，默认off路径保留原去重/合并/摘要行为。没有全局排除Summary、改Prompt或调阈值。

## 保存回执逐案核对与离线结果

历史精确定位：MIRACL91294的84字符原文在Search30、Rerank8存在，Merge7后缺失，真实wire只有Summary。SciFact1101（原文1182字符）、241（1196字符）原文和错误摘要都已在真实wire；1101的mesoderm误译、241的关系方向颠倒最早已见于历史摘要。它们不是原文未送达的同一种问题。

固定保存的post-Rerank候选/分数，通过现有Merge→TopK→get_source_unit→Context→prepare_generation→ProviderModel.build_request_body做A/off、B/bounded离线回放。另有MockTransport定向测试实际经过Adapter/Transport/合成Gate，验证quote和引用映射；没有真实云发送。

| 开发题 | A原文/摘要在wire | B原文/摘要在wire | 最终块数A→B | 渲染Context字符A→B | 实际用户wire字符A→B |
|---|---|---|---:|---:|---:|
| miracl-zh-91294#0 | False / True | True / False | 7→3 | 1351→660 | 2105→1119 |
| scifact-1101 | True / True | True / False | 2→1 | 1870→1562 | 2055→1714 |
| scifact-241 | True / True | True / False | 2→1 | 1722→1495 | 1944→1684 |

三题目标原文最终wire覆盖从2/3变为3/3，目标生成摘要从3/3变为0/3；仅适用于本次固定来源回放。MIRACL由7块变为3块，用户wire减少986字符；另4个合并投影因未在有限回取组内获得精确原文位置证明而标记INSUFFICIENT_SOURCE并省略。它们未被认定为无关噪声，其余事实是否受影响仍须真实复验；Sci1101减少341、241减少260字符。不把块数/字符变化当成噪声或答案质量评分。

回放是 **RECONSTRUCTED_CONTEXT_ONLY**，不是历史完整Prompt的逐字复现或新的模型A/B；两个离线臂共享冻结候选、真实分数和当前构造函数，没有新Rerank评分。原文SHA/版本/偏移逐行对真实source.bin验证；没有从source.bin或Gold造Chunk。保存行适配器的tenant/enabled/completed是明确的fixture假设，Sci缺标题/前邻行保留SNAPSHOT_LIMITED；当前数据库完整邻居、线上延迟与最终答案均未验证。完整结果、证据文件SHA和6份构建请求位于 `E:/RAG quention/var/reports/s8-3-evidence-reliability/replay/final-v6/`。

## 实际测试、命令与回执

工作目录`E:/RAG-Worktrees/s8-six-dataset-evaluation`。未实现的命令不得报告通过。

- Search/Merge最初RED：21 failed/3 passed，exit1；补充来源类型、同ID冲突、单位大小写/布局RED：12 failed/28 passed，exit1。
- 原文投影RED：5 failed/11 passed，exit1；已选合并块附加断言/旧偏移RED：2 failed，exit1。
- Task RED：连续12个完成对象未释放，exit1。修复后12成功/140失败对象弱引用可回收，取消、20并发领取、旧回调、新Task、重放/重启及UNKNOWN不重发均通过；安全错误码最多128条。
- **最终联合GREEN：383 passed in 4.27s，exit0**：

```powershell
& "E:/RAG quention/.venv/Scripts/python.exe" -B -m pytest -q --tb=short -p no:cacheprovider tests/weknora_port/test_bounded_source_dedup.py tests/weknora_port/test_merge_source_retention.py tests/weknora_port/test_source_evidence.py tests/weknora_port/test_source_reliability_wire.py tests/weknora_port/test_pipeline.py tests/weknora_port/test_reference_metadata.py tests/weknora_port/test_service_task_cleanup.py tests/weknora_port/test_service.py tests/weknora_port/test_generation.py tests/weknora_port/test_turn_storage.py tests/weknora_port/test_history_storage.py tests/weknora_port/test_http_api.py tests/weknora_port/test_conversation_configuration.py
```

最终源码3题×2臂离线回放exit0：

```powershell
& "E:/RAG quention/.venv/Scripts/python.exe" -B scripts/s8_3_evidence_replay.py --inventory "E:/RAG quention/var/reports/s8-3-evidence-reliability/source-evidence-inventory.json" --output "E:/RAG quention/var/reports/s8-3-evidence-reliability/replay/final-v6"
git diff --check
git diff --cached --check
```

最后两个差异检查均exit0。来源证明补充回归曾使MIRACL整条离线回放因未证明的其他合并块被安全拒绝（final-v5，exit2）；最终按同一有界合同省略这些未证明投影并记录不足，保留已验证原文（final-v6，exit0），两次原记录均保留。RED/GREEN原始日志与SHA见同名results JSON和 `E:/RAG quention/var/reports/s8-3-evidence-reliability`。保留首次工具问题：测试夹具close/aclose误用、夹具异常traceback阻止弱引用回收、sandbox权限与Windows本地asyncio socketpair阻塞；均在零外发条件修正，旧失败日志保留。没有把工具失败算成模型质量失败。

测试仅覆盖受影响Search/Merge/来源/生成、service、幂等、历史、HTTP/SSE及配置合同；未跑全仓、六套数据集或真实浏览器新回归。9个关键未修改文件的基线SHA比较通过，包括query、rerank、filter_top_k、generation/context、router、Provider、runtime、storage/knowledge、execution；默认模型/Prompt/0.3阈值/Top1配置/P5规则不变。

## 用量、时间、内存与剩余问题

新增真实Provider 0，输入/输出Token均0，新UNKNOWN 0。模拟回执的usage不计真实用量，历史未知用量未清零。本轮没有新的真实POST耗时、TTFT、模型finish_reason或ECS内存谷值；不估算Token节省或费用。383测试耗时4.27秒仅是离线测试时间。Task引用回收已实测，RSS降低幅度NOT_MEASURED。

预算沿用unit≤2048字符/6块、最多3组、额外≤4096、最终Context≤16000；不扩大Context。保存行回放SELECT次数MIRACL2→11，Sci各1→4，实际PG时延尚未测量。大型合并块的完整证明超过预算时会拒绝；这是候选可用性风险，需要ECS小样本检查。

尚未解决：

1. 新增真实答案质量、噪声、Token和延迟对照NOT_RUN，不能宣布REAL_QA_PASS或推广默认。
2. 原文仅局部被选中而Summary指向长文时，有限回取可能不足；候选会记录不足，不用摘要填补。既有model fallback仍可能泛化，不能称事实校验已完成。
3. Hotpot第二跳在Rerank前后已移除，当前同文档回取无法补回；不启Graph或桥接。
4. 正文已齐后的主体、因果、量词/数值错误及DYNAMIC不识别语义错误继续保留，未新增Judge、路由或Prompt规则。

## 下一步ECS受控验证计划（未授权、未执行）

已冻结6个非holdout开发场景：MIRACL91294、Sci1101、Sci241、Hotpot Frost第二跳、Hotpot Franklin/Leyden成功、无证据邮编反例。每题A/B各一次，共12次正常API；来源为整个合法KB含干扰，不按Gold限定文档。两臂同模型/Prompt/原问题/范围/0.3阈值/Top1实验配置/输出限制，仅source_evidence_mode不同。Qwen查询理解temperature0.3/max_tokens150/thinking=false，生成temperature0.7/max_tokens2048/thinking=false沿用真实记录；必要DYNAMIC升级保持现有模型。

输入manifest在 `E:/RAG quention/var/reports/s8-3-evidence-reliability/replay/final-v6/planned-six-case-ecs-ab.json`。每请求现有软件围栏≤12Provider/100000Token，12请求的保守软件外界为144次/1200000Token；这**不是Owner授权**，不通过新建文件续额。观察到的历史相似请求通常每题4次，不以此替代发送前真实上界检查。开始前需Owner批准独立ECS候选部署/执行范围及总额度，并核验每批≤40分钟、资源身份/模型/旧UNKNOWN保护与总量共享。新UNKNOWN即停相关发送，不重发。

新回执需记录原文实际wire、Summary冲突、事实覆盖、无依据断言、HTTP来源回读、SSE/PG/历史一致性及实际usage/延迟/内存谷值。如果两臂Rewrite/Search不同，标注混杂因素，不把回答差异都归因候选。没有新授权，本轮在离线工程交付处停止，不Push或进入下一阶段。
