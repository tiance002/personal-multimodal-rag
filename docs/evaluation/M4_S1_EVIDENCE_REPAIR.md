# M4 口径收口与 S1 Rerank 输入增强

状态：**局部实现、离线回归和真实 Rerank A/B 已完成；唯一正常 API 验证未通过发送前准入。不能宣布 S1 全链路验收通过。**

本轮依据 HEAD `9350f2282659571d14931e420d79e896901033f3` 的实际工作树；实现提交 `fcfeed7abc5f6971757dbfce3711090fced2f7c0`；最终文档提交见 Git 交付记录及最终答复。历史 M4 原始 JSONL、Gold、UNKNOWN、模型配置、索引和日常 principal 均保留。没有重跑40题或24页。

## 1. 更正后的 M4 口径

- 40题：38 COMPLETE、1 新 UNKNOWN、1 历史 UNKNOWN 禁止重发。原执行器漏认 SSE `complete`，把已正常持久化的38题记为 FAILED；新 `m4_result_contract.execution_status` 同时检查 SSE、assistant.is_completed，UNKNOWN 优先。仅修工具和派生统计，不改历史回执。
- Hotpot Search：至少一个来源 **8/8**、全部必要来源 **8/8**。Rerank→Context：至少一个 **8/8**、全部 **6/8**；若只算完成的7题，则分别 **7/7、5/7**。严格核对官方支持句原文，完整到实际生成输入只有 **5/8**（已完成题5/7）。Chandamama 两个文档ID仍在，但原始 Tamil-remake 事实被丢，说明来源命中不能代替事实命中。
- Pew 0684/0685/0687 的原件真实存在且绑定正确：KB `fd444069-cc1a-42d0-9617-e95fa90b6941`，doc `35c37b2d-3ce7-49ab-98fe-de6d3e6974f9`，version `8358d755-b476-4986-bcce-fe3c979531a0`，SHA256 `8f49f93f1665fd5ff604e66ffed9649d8b18ce7f114ab43463aeac46cd3d00cb`。问题在旧文本投影缺少图表数值关系，不是错绑PDF。
- 适用的历史分数保持：SciFact、MIRACL 无生成答案Gold；LongBench DuReader ROUGE-L 0.203392（4题）、MultiQA F1 0.131287（4题）；Hotpot EM 0/7、F1 0.161882（冻结分母8、实际完成7）；MML normalized EM 0/8、char-F1 0.028792（非官方GPT评分）。不能从字符串分数直接推断语义答案全部错。
- Omni仍为 MinerU本地24/24页；3/24有可靠全文Gold映射，项目NED 0.110579；其余21页 NOT_SCORABLE，完整逐页状态保留于结果JSON。没有官方QA准确率、官方布局/表格/公式成绩。
- 历史M4：167次Provider尝试，166有回执、1 UNKNOWN；已报告401,231 Token，UNKNOWN不计0。这是旧版本数据，本轮新增用量另列。

## 2. 40题事实级审计

P=已核对的原文事实存在，部分=关系/组成项不完整，缺失=所需原文不在，?=资料不足。它们不是答案正确率。来源身份单列于JSON；每题问题、Gold适用性、原文片段、chunk/版本、回执及答案评语可在 `fact_reviews.zip` 回查。

| 数据集／QID | 执行 | Search | Rerank | Merge | 实际生成输入 | 最早已观察缺口 |
|---|---|---|---|---|---|---|
| scifact-1102 | COMPLETE | P | P | P | P | NO_REVIEWED_FACT_LOSS |
| scifact-1304 | COMPLETE | 部分 | ? | ? | 缺失 | SEARCH_FACT_INCOMPLETE_OR_SOURCE_RELATION_UNPROVEN |
| scifact-1351 | COMPLETE | 部分 | 部分 | 部分 | 部分 | SEARCH_FACT_INCOMPLETE_OR_SOURCE_RELATION_UNPROVEN |
| scifact-242 | COMPLETE | 部分 | 缺失 | 缺失 | 缺失 | SEARCH_FACT_INCOMPLETE_OR_SOURCE_RELATION_UNPROVEN |
| scifact-247 | COMPLETE | 缺失 | 缺失 | 缺失 | 缺失 | SEARCH_FACT_INCOMPLETE_OR_SOURCE_RELATION_UNPROVEN |
| scifact-339 | NOT_RUN旧UNKNOWN | ? | ? | ? | ? | NOT_RUN |
| scifact-611 | COMPLETE | 部分 | 部分 | 部分 | 部分 | SEARCH_FACT_INCOMPLETE_OR_SOURCE_RELATION_UNPROVEN |
| scifact-621 | COMPLETE | 部分 | 部分 | 部分 | 部分 | SEARCH_FACT_INCOMPLETE_OR_SOURCE_RELATION_UNPROVEN |
| miracl-zh-277616#0 | COMPLETE | P | P | P | P | NO_REVIEWED_FACT_LOSS |
| miracl-zh-5704915#0 | COMPLETE | P | P | P | P | NO_REVIEWED_FACT_LOSS |
| miracl-zh-5721594#0 | COMPLETE | P | P | P | P | NO_REVIEWED_FACT_LOSS |
| miracl-zh-692002#0 | COMPLETE | 部分 | 部分 | 部分 | 部分 | SEARCH_FACT_INCOMPLETE_OR_SOURCE_RELATION_UNPROVEN |
| miracl-zh-704170#0 | COMPLETE | 部分 | 部分 | 部分 | 部分 | SEARCH_FACT_INCOMPLETE_OR_SOURCE_RELATION_UNPROVEN |
| miracl-zh-7049630#0 | COMPLETE | P | P | P | P | NO_REVIEWED_FACT_LOSS |
| miracl-zh-7696#0 | COMPLETE | P | P | P | P | NO_REVIEWED_FACT_LOSS |
| miracl-zh-91294#0 | COMPLETE | P | P | 缺失 | 缺失 | MERGE |
| dureader:11b3b7af | COMPLETE | P | P | P | P | NO_REVIEWED_FACT_LOSS |
| dureader:5726f5fc | COMPLETE | P | P | P | P | NO_REVIEWED_FACT_LOSS |
| dureader:6770736a | COMPLETE | P | P | P | P | NO_REVIEWED_FACT_LOSS |
| dureader:9938c97e | COMPLETE | 缺失 | 缺失 | 缺失 | 缺失 | QUERY_INTENT_NO_KB_RETRIEVAL |
| multifieldqa_zh:1a6c19fe | COMPLETE | P | P | P | P | NO_REVIEWED_FACT_LOSS |
| multifieldqa_zh:51abd206 | COMPLETE | P | 部分 | 部分 | 部分 | RERANK |
| multifieldqa_zh:741f3777 | COMPLETE | P | P | P | P | NO_REVIEWED_FACT_LOSS |
| multifieldqa_zh:bcedff0a | COMPLETE | P | P | P | P | NO_REVIEWED_FACT_LOSS |
| hotpotqa-5a8ad6805542992d82986f93 | COMPLETE | P | 部分 | 部分 | 部分 | RERANK |
| hotpotqa-5a745a6055429974ef308be4 | COMPLETE | P | P | P | P | NO_REVIEWED_FACT_LOSS |
| hotpotqa-5a729fe55542991f9a20c53b | COMPLETE | P | P | P | P | NO_REVIEWED_FACT_LOSS |
| hotpotqa-5ae3992d5542994393b9e722 | UNKNOWN | P | 部分 | 部分 | 部分 | RERANK |
| hotpotqa-5ab672165542995eadeeffe0 | COMPLETE | P | P | P | P | NO_REVIEWED_FACT_LOSS |
| hotpotqa-5a8911ad554299515336129c | COMPLETE | P | P | P | P | NO_REVIEWED_FACT_LOSS |
| hotpotqa-5a7b68a75542997c3ec97153 | COMPLETE | P | P | P | P | NO_REVIEWED_FACT_LOSS |
| hotpotqa-5a7631cd5542994ccc91870c | COMPLETE | P | 部分 | 部分 | 部分 | RERANK |
| 0684 | COMPLETE | 部分 | 部分 | 部分 | 部分 | SEARCH_FACT_INCOMPLETE_OR_SOURCE_RELATION_UNPROVEN |
| 0687 | COMPLETE | 部分 | 部分 | 部分 | 部分 | SEARCH_FACT_INCOMPLETE_OR_SOURCE_RELATION_UNPROVEN |
| 0685 | COMPLETE | 部分 | 部分 | 部分 | 部分 | SEARCH_FACT_INCOMPLETE_OR_SOURCE_RELATION_UNPROVEN |
| 0431 | COMPLETE | 缺失 | 缺失 | 缺失 | 缺失 | SEARCH_FACT_INCOMPLETE_OR_SOURCE_RELATION_UNPROVEN |
| 0434 | COMPLETE | 部分 | 缺失 | 缺失 | 缺失 | SEARCH_FACT_INCOMPLETE_OR_SOURCE_RELATION_UNPROVEN |
| 0430 | COMPLETE | ? | 缺失 | 缺失 | 缺失 | UNVERIFIED_BEFORE_SEARCH |
| 0959 | COMPLETE | P | P | P | P | NO_REVIEWED_FACT_LOSS |
| 0958 | COMPLETE | 部分 | 缺失 | 缺失 | 缺失 | SEARCH_FACT_INCOMPLETE_OR_SOURCE_RELATION_UNPROVEN |

关键改判：MIRACL91294 的原文通过Rerank，却被Merge的重叠清理压掉；LongBench2005的人数仍在但年份被Rerank丢掉；0959两种波的事实都已送达，答案只答微波，属于生成遗漏。0684图表、0431图层、0958图示语义在Search前就不完整。0687平均数不能证明中位数，0685的局部未见不能证明整份45页无答案。UTC答案虽对，但当时intent=web_search，没有KB原文送达。

## 3. 实际实现与新版差异

参考固定tag [WeKnora v0.8.3 passage.go](https://github.com/Tencent/WeKnora/blob/v0.8.3/internal/reranking/passage.go) 与 [rerank.go](https://github.com/Tencent/WeKnora/blob/v0.8.3/internal/reranking/rerank.go)，仅选择性移植正文构造，不调整评分策略。

- `SearchResult.context_header` 与 `storage.knowledge._read_chunks` 补齐已有数据库字段的映射。原 `matched_content` 是完整Embedding索引文本，不能当精确原文；保留原body、ID、版本和偏移。
- `model_passage` =可信文档标题＋持久化ContextHeader＋EnrichedPassage；FAQ保持独立，空正文不能由标题变成证据。MMR继续仅比较不带公共标题的EnrichedPassage。
- 增强路径只去除与原正文或已见图片文字**完全相同**的OCR/Caption，不做模糊删除。原版构造仍可复现；Provider原有单项/总长与index合同保持。
- `rerank_context_enabled` 从正常ConversationSettings／已有启动环境 `WEKNORA_PORT_RERANK_CONTEXT_ENABLED=true` 接入。默认false；模型、阈值0.3、Top1保底0.15、路由不变。隔离验证才启用。
- `merge._deduplicate_origins` 仅在已有 `bounded_source_v1` 下分开处理生成Summary与原文，避免跨类型重叠误删；仍全局ID唯一。不补回未过Rerank的候选，不改分数或索引。
- 本库101,488个有效chunk的非空ContextHeader实际为0；真实A/B验证的是已有文档标题传递。非空Header的数据行映射及最终wire分别由合成repository行fixture和合成HTTP测试覆盖，真实PG非空Header链路NOT_RUN，**没有把历史章节恢复写成已发生**。Hotpot标题是哈希，0434无可靠跨页章节，本轮不补造。

真实例：0434原wire只有“...Kaggle's Cat vs Dog dataset...”；增强wire前置数据库标题`B3`，没有“Coding time”章节。Omni原wire把相同OCR正文又从image_info追加一次；增强wire保留原始噪声正文一次并前置真实页面标题。具体完整文本、顺序、模型参数及SHA见inputs.json与真实request回执。

## 4. 离线与真实对照

旧回执12题离线重放：12/12基线最终ID一致，0 Provider，0数据库写。已有有界原文回取＋本次Merge保留模式下，Sci242原文499→1849字符、MIR91294原文319→430、Long2005年份767→1529；成功对照事实保留。后两种bounded模式最终结果相同，**不能把已有回取收益全部归于新Merge修复**。新增原文来源有效，但未逐句证明都相关；已知Romeo干扰电影片段仍在。

真实BGE A/B：6组每臂1次，共12次。冻结Query、候选ID/顺序、原文、模型及参数，A=旧Enriched，B=标题/Header＋精确去重的新ModelPassage。旧分数没有用于新输入。下表以新回执调用真实过滤、MMR、Merge、Top-K和生成准备做离线选择分析，**不是新增完整回答**。

| 冻结组 | 目标分数 A→B | 排名 A→B | 最终 Context A→B | 原文结果 |
|---|---:|---:|---:|---|
| mml-0434 | 0.000800 → 0.000517 | 6 → 10 | 0 → 0字符 | False → False |
| hotpot-frost | 0.002790 → 0.001522 | 5 → 5 | 2632 → 2632字符 | False → False |
| hotpot-romeo | 0.078652 → 0.023171 | 4 → 5 | 2226 → 2226字符 | False → False |
| mml-0959 | 0.387365 → 0.509650 | 2 → 2 | 1720 → 1720字符 | True → True |
| omni-page007 | 0.094427 → 0.557629 | 2 → 2 | 295 → 623字符 | False → True |
| omni-no-exact-fact | 无目标 | — | 295 → 295字符 | None → None |

Omni确切OCR ID `51316a20-67fd-4732-b520-c049999933d0` 从未通过阈值变为实际prepare_generation正文包含，Summary共存；Context增加399字符。该证据是旧带噪OCR，不能据此宣称矩阵行列或新MinerU问答正确。两道Hotpot仍缺第二来源，0434仍SEARCH_NOTHING。0959事实保留，未出现该对照的证据退化。构造缺答案反例两臂仍高分保留主题Summary，**不能视为问题有答案或幻觉已解决**；同源或高分不等于事实支持。六组不足以推广生产全局。

B同时改变标题前缀和确切重复去除，Omni输入53,871→30,431字符，真实Token28,793→16,665；不能将全部评分收益单独归因标题。其它标题使输入略增，详见JSON。未测所有候选的相关性，不给虚假的精确噪声率；已确认干扰与缺答案Summary均明确保留为负例风险。

## 5. 唯一正常API验收与失败

tenant1原KB `9d3461a5-080c-4b5e-a367-c82b79d3013c`；正常entrypoint/56425，未启Worker，PG system7696088403504697382、Redis DB15身份核实。配置只在此进程启用增强路径，原DYNAMIC不变。

首次只读预检工具误用不存在的KB详情GET，404时尚无session、问题或Provider。保留first-failure后，在原授权、同request_id、同额度下审计续行，修正为正式KB列表接口。

实际唯一问题提交后，在Understand发送前失败：`LOCAL_EXECUTION_SCOPE_DENIED`。工具许可错误绑定客户端request_id `7802b8a0-44da-451f-b1dc-db279eba2b98`，正式Provider Context使用服务端run_id `9cefe30e-2e45-4c7c-8f16-0dae9514ce6d`。两者并非同一个ID。已修正工具UUID前缀合同及终态分析，**未再次提交**。SSE与持久化均是明确失败，无答案；Search/Context/Qwen质量均NOT_RUN。Qwen配置身份已核实，不能称实际调用Qwen。

本次API侧0 Provider、0新增Token，历史UNKNOWN及原文/模型/principal指纹不变；受限模式正确拒绝。工具修复之后尚缺一次新的授权完整问答，这是后续验证项，不是模型质量结论。

## 6. 命令、用量、停止

实际命令（工作目录为本项目；完整回执SHA与退出记录在证据清单）：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest --noconftest -p no:cacheprovider -q --tb=short tests/weknora_port/test_rerank_model_passage.py tests/weknora_port/test_merge_source_retention.py tests/weknora_port/test_knowledge_storage.py tests/weknora_port/test_conversation_configuration.py tests/weknora_port/test_pipeline.py tests/weknora_port/test_source_evidence.py tests/weknora_port/test_development_entry.py tests/weknora_port/test_m4_result_contract.py
# exit0:167 passed in7.02s；生产Provider wire的单元测试为合成HTTP响应，和真实AB分开。
```

`scripts/m4_result_contract.py --raw ...qa_results.jsonl --cases ...qa_cases.json --out ...metric_tools.json` exit0；`scripts/m4_s1_replay.py --raw ... --plan ...replay_plan.json --config ...local.json --out ...saved_receipt_replay.json` exit0（12基线匹配、0错误）。`scripts/s1_rerank_input_ab.py --case <六组各一次> ... --send` 六个exit0，各2次真实调用。`scripts/s1_rerank_ab_analysis.py --input ...s1-rerank-input-20261019 --config ...local.json --quote-plan ...replay_plan.json --out ...native_analysis.json --threshold 0.3 --topk 5` exit0；历史快照未保存阈值/topk，当前显式配置用于选择模拟，不伪称恢复历史隐藏参数。

`scripts/s1_normal_api_check.py ... --send` 首次工具exit1；`... --send --resume-unsubmitted` exit0仅证明观察流程结束，业务SSE失败，**不计PASS**。脚本编译及最终diff检查见verification.json。无全仓测试、无全量重评。

本阶段合计**12真实Provider、83,583已报告输入Token、0生成输出Token、0新UNKNOWN**；各次延迟与用量见结果JSON。Owner总围栏20次/300,000；最终CLOSED，撤回未使用8次/216,417 Token。旧UNKNOWN没有修改、释放或重发。

保留首次工具失败、原始回执、Gold和历史文件。环境pytest_html_reporter曾汇总编码失败，忽略的HTML产物可能被置空；后续禁用插件，跟踪报告未被覆盖。另一次子代理在工具日志暴露本机PG DSN，未入交付/模型；Owner明确批准保留本机凭据继续，本轮没有暗改密码。

## 7. 结论与剩余问题

1. **可保留的最小局部实现**：正常字段映射、ModelPassage/MMR分离、完全重复OCR去除及bounded模式原文/Summary身份保留。默认仍关闭增强，不根据小样本推广。
2. **优先复验**：修正执行工具后，另获一次有限许可，再核验正常API的真实生成输入与回答。此次没有取得新的完整答案正例。
3. **质量缺口**：Hotpot第二来源与0434章节关系仍未解决；按Owner最新决定，本阶段不做关键词/轻量桥接、实体关系匹配、LLM跨片段验证、条件式二次检索。解析/视觉缺证据与生成遗漏仍应分别处理，不以降全局阈值掩盖。

仅提交本轮文件；不Push、不部署、不合并、不打Tag、不进入S2。原有未提交路径单列于version.json，不纳入本次提交。所有比率保留分母，历史与本轮真实验证明确分开。

## 文件与逐项用量

8个生产文件、5个一次性诊断/执行工具、3个定向测试随实现提交；精确清单见该提交和 `version.json`。完整40题JSON与24页状态在同名results.json，详细原文审计和新请求/回执在两个ZIP，逐文件SHA在manifest.json。

| 冻结组 | A Token / 秒 | B Token / 秒 |
|---|---:|---:|
| hotpot-frost | 3414 / 0.846 | 4600 / 0.540 |
| hotpot-romeo | 3982 / 0.739 | 5188 / 0.574 |
| mml-0434 | 5028 / 0.840 | 5718 / 0.517 |
| mml-0959 | 4336 / 0.848 | 5102 / 0.568 |
| omni-no-exact-fact | 356 / 0.478 | 401 / 0.245 |
| omni-page007 | 28793 / 1.408 | 16665 / 0.886 |
