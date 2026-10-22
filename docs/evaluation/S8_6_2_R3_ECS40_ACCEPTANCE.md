# S8.6.2-R3：ECS40正式HTTP测评

日期：2026-10-22。状态：`COMPLETED_EVALUATION_WITH_QUALITY_FAILURES`。

## 1. 结论

- 40题原名单：38题各新建Session、单次提交；2题因历史UNKNOWN保护为NOT_RUN。38题均完成正式FastAPI、检索、生成、SSE回放及PG/Local精确结算。
- 代理逐题复核：**20正确、5部分正确、10错误、3未证实**。严格正确为20/38（52.63%）；原40题的正确覆盖为20/40（50%）。PARTIAL、UNVERIFIED及NOT_RUN均未计入正确数。
- 这是冻结参考答案与实际原文的项目复核；SciFact/MIRACL只有检索qrels。各数据集的原生指标不同，不能把上述复核比例称为官方综合准确率。
- 158次Provider尝试全部结算：**356,966输入 + 7,627输出 = 364,593 Tokens**；新增UNKNOWN0，未结算0，未结算held0。无费用估算。
- 引用精确回读38/41；另外3条的文本及end偏移不一致，涉及2题。**回答质量、引用合同未获生产验收。**

## 2. 版本与实际运行位置

- 分支：`codex/s8-6-adaptive-retrieval-20261021`。
- 实际运行源码：`5585f41dab1103738bd2aecd7161ded08598e198`；QU最小修复提交`9c2aa9c49768acbeae7aedb6996f8a40fdfcaf21`。
- ECS隔离镜像：`sha256:b7c89d3ec3a44f3275c968a063391d4b15341a7ee5d5a19d656551fb4de82f63`。
- profile：`s8.6.2-r3-ecs40-prompt-b/v2`，SHA256 `0510ae20eb2e1350e6aaa35ff4efb92d95ecee3d4a7a35e8bcf491e264737acc`。
- Prompt B后缀SHA256：`4501caef3b4b0d5b0de09e00e51d1ca9d50332637637e52ffc45ee90e2dc726b`。
- QU实际空历史System SHA256：`a3d246f8766829274ca758359f9c11232bc18f1f7eefaf0f41f7cc3ec49a6213`。完整模板、Registry说明和源码指纹见机器结果。
- **本批真实问答全部在ECS隔离API执行**。本地真实问答与本地40题按Owner后续指令取消。正式API/Worker仍为`29ecf2f5d3037eed99a4de783546dee272a761bd`，未被替换。
- P5 DYNAMIC、模型绑定、全局阈值、Weighted RRF、数据库Schema及索引保持原状；会话采用`query_structure_v1`、`bounded_source_v1`、Prompt B和引用，内部Expansion关闭。
- 最新50万Token授权未进行数值预算复核；运行时累计实际/在途占用保护保留。授权范围已完成并写入`COMPLETED_NO_FURTHER_SENDS`，38份子账本CLOSED，当前没有运行中的新测评API。

## 3. 分数据集结果

| 数据集 | 完成/原名单 | 正确 | 部分正确 | 错误 | 未证实 | NOT_RUN |
|---|---:|---:|---:|---:|---:|---:|
| scifact | 7/8 | 3 | 0 | 1 | 3 | 1 |
| miracl-zh | 8/8 | 4 | 2 | 2 | 0 | 0 |
| longbench-zh | 8/8 | 3 | 2 | 3 | 0 | 0 |
| hotpotqa | 7/8 | 6 | 1 | 0 | 0 | 1 |
| mmlongbench-doc | 8/8 | 4 | 0 | 4 | 0 | 0 |

复核者为代理，不代表Owner人工批准。LongBench结果为项目诊断；MML官方GPT归一化、证据页指标、Hotpot官方supporting-fact/joint指标均NOT_RUN。

## 4. 实际调用与检索行为

| 请求模型 / 操作 / 角色 | 调用 | 输入Token | 输出Token | 总Token |
|---|---:|---:|---:|---:|
| deepseek-flash / generation / expensive | 2 | 2950 | 302 | 3252 |
| BAAI/bge-m3 / embedding / NOT_RECORDED | 39 | 664 | 0 | 664 |
| BAAI/bge-reranker-v2-m3 / rerank / NOT_RECORDED | 40 | 224635 | 0 | 224635 |
| Qwen/Qwen3.5-4B / chat / NOT_RECORDED | 1 | 1335 | 132 | 1467 |
| Qwen/Qwen3.5-4B / generation / cheap | 38 | 82534 | 5954 | 88488 |
| Qwen/Qwen3.5-4B / query_understanding / NOT_RECORDED | 38 | 44848 | 1239 | 46087 |

逐调用的响应`reported_models`保留在机器结果中；表中请求模型和路由角色不单独作为响应模型身份的证明。

- Local、PG和真实wire三侧均为158个唯一`(request_id, capability)`键，键集合完全相同；158条wire哈希与完整usage均一致。892个离线证据文件哈希复核通过。
- 最终正常结束，SSE/回放/PG回答内容及HTTP历史状态一致；replay新增发送0。PG原始message列`turn_status=pending`与既有HTTP规范化`completed`的差别保留，未修改历史状态凑一致。
- QU：DIRECT9、DEPENDENT7、SIMPLE_COMPARE1、UNKNOWN21；37次ACCEPTED，1次安全FALLBACK。Pieter题模型把`eith`改为`with`，违反字面改写保护后回退q0，未补发。
- **本批实际执行的38题没有激活规则比较或并列拆分**：最终计划均PLAIN/1条主子查询。37题使用1个检索单元，Frost使用2个；不能据此宣称规则拆分的真实准确率已通过。
- 本批句号精确恢复触发0次；此前独立R3首题确实触发恢复并执行2条比较子查询（6调用/5,441Tokens），证据见[S8.6.2-R3独立首题报告](S8_6_2_R3_QU_HTTP_ACCEPTANCE.md)。该旧账本未加入本批158调用或50万授权。
- 29题的最终真实请求可核对原文投递，共154次片段投递（含跨题重复）；9题最终为metadata-only fallback。实际Search候选、最终Context及引用分开记录。
- Frost实际Q1为`Where is The Frost Place Advanced Seminar located?`，观察到新召回、第二组Embedding/Rerank及最终原文投递；状态仍RELATED_ONLY、`final_answer_supported=false`。Franconia, New Hampshire按Owner口径记最终答案正确，**EVENT_VENUE_UNVERIFIED**保留。未观察到名为HYBRID的显式Trace标签，不以找到第二跳代替完整证据链证明。
- MML8题最终请求为文本内容，没有直接图像载荷。复核者读取冻结PDF属于离线审查，不代表模型看到了原图。

## 5. 已确认质量问题

1. **明确错答**：SciFact1304把fast-skeletal-troponin相关机制说成cardiac，并附加无依据药物细节；MIRACL存在姓名、化学术语与星座对应错误；LongBench存在培训人数变成人次和技术规范日期错答。
2. **必要证据未进入生成**：Pieter的Tribometer原文已在Search出现，但最终只收到Leyden jar相关证据，回答对两项发明的联合结论保留否定性不确定。正确人名出现不能算完整答对。
3. **原文范围投影问题**：LongBench DSM题2条、MML median题1条引用保存了扩大后的Context文本/end，而HTTP回读只返回较短原Chunk。KB、document、version、start一致，3个回读均为引用文本前缀；文本与end仍判FAIL。DSM主答案better than5m及median主答案Clinton分别判正确，引用问题单列。
4. **无证据及参数知识**：9题最终只有元数据目录，部分回答依赖参数知识或拒答。最终答对与知识库支持分开计分；模型中间答案未登记为事实来源。
5. **补充事实与呈现**：个别主答案正确但因果叙述、历史补充或英文别名错误；部分输出残留内部cN句柄。逐题次级结论见机器结果。

## 6. 执行记录与保留的失败

所有操作使用本轮新request_id；无问答自动重试。前台串行执行过程中修复了辅助脚本问题，已完成题目均未重发：

| 实际命令入口 | 退出码 | 证据/语义结果 |
|---|---:|---|
| `prepare_source.py --source 5585f41...`及`remote_operator.py before/transfer/build/fetch/protect` | 0 | 源码522文件、15模块、profile/依赖/镜像身份匹配 |
| `runtime40_operator.py initialize` | 1 | Docker不支持NanoCPUs，Provider0；原始错误保留 |
| `runtime40_operator.py initialize-zero-send-recovery` | 0 | 仅移除不支持启动参数，38空新账本及新grant |
| `runtime40_operator.py launch-all` | 1 | 首题4调用完成后，只读PG进程缺Redis前缀；无重发 |
| `runtime40_operator.py reconcile-first` | 0 | 首题精确对账关闭账本，条目不变 |
| `runtime40_operator.py resume-unsent37` | 1 | 到26题后历史诊断容器枚举超过100；新发送均已结算 |
| `runtime40_operator.py resume-after-metadata-limit` | 0 | 枚举限定本轮目录，复用26题闭合证明，仅发送剩12题；最终38完成 |
| 最终`runtime40_operator.py fetch`与`remote_operator.py protect` | 0 | 3307文件/38闭合账本校验；source/旧UNKNOWN/正式服务保护通过 |
| `score_actual40.py --records-root <最终快照> --output actual40-final-extracted.json` | 0 | 38完成/2保护，实际用量完整；无模型调用 |
| `audit_actual40.py --input actual40-final-extracted.json --output audit-actual40-final.json` | 0 | 38协议证明、158三侧唯一键、892文件哈希；3引用范围失败保留 |
| 5份逐数据集离线复核及root合并校验 | 0 | 38份answer哈希/引用原句/schema验证，20/5/10/3 |

完整参数、stdout/stderr/退出码及冻结输入在私有证据目录`E:/RAG quention/var/reports/s8-6-2-r3/followup40/`。未实现的命令不得报告通过。

离线代码证据复用本轮已保存的83项受影响测试及此前R3精确QU修复的82项/3份真实输出离线回放；未重新运行整仓922项。旧配置基线两项失败保持历史记录及期望不变。

Windows279字符路径和只读快照文件大小护栏失败也已保留；修复仅影响本地证据读取。实际重复的Go-map `parser_metadata`先完整哈希后去重投影，完整1,094,806,489字节原始记录保留。冻结数据/Gold/旧账本未修改。

此前已取消的整库预算导出曾导致正式API一次OOM事件；该失败、容器仍存活/重启0及健康恢复证据均保留，不能声称OOM标志始终未变。本次38问答的诊断API终止检查无新OOM，正式服务未更新。

## 7. 逐题主结论

| 原序号 | case_id | 主结论 | 实际Token |
|---:|---|---|---:|
| 1 | `scifact-1102` | PASS | 10755 |
| 2 | `scifact-1304` | FAIL | 8917 |
| 3 | `scifact-1351` | UNVERIFIED | 10557 |
| 4 | `scifact-242` | PASS | 11218 |
| 5 | `scifact-247` | UNVERIFIED | 8699 |
| 6 | `scifact-339` | NOT_RUN | 0 |
| 7 | `scifact-611` | PASS | 11106 |
| 8 | `scifact-621` | UNVERIFIED | 13403 |
| 9 | `miracl-zh-277616#0` | PASS | 8121 |
| 10 | `miracl-zh-5704915#0` | PASS | 7150 |
| 11 | `miracl-zh-5721594#0` | PARTIAL | 8501 |
| 12 | `miracl-zh-692002#0` | FAIL | 7073 |
| 13 | `miracl-zh-704170#0` | PARTIAL | 7347 |
| 14 | `miracl-zh-7049630#0` | PASS | 7452 |
| 15 | `miracl-zh-7696#0` | PASS | 8774 |
| 16 | `miracl-zh-91294#0` | FAIL | 6878 |
| 17 | `longbench-zh-dureader:11b3b7afb73f5bf65a51b3083e885a60825b17421c58a828` | PARTIAL | 14802 |
| 18 | `longbench-zh-dureader:5726f5fc6feedda99cafb275a27a8fd67483185b6fb8efa7` | FAIL | 13302 |
| 19 | `longbench-zh-dureader:6770736a958b78c4c1b1a0057b34a709bdd869f307f3dc40` | PARTIAL | 13466 |
| 20 | `longbench-zh-dureader:9938c97e6ccf006c5fc66ea3294b9835e1584d95713576a9` | PASS | 14711 |
| 21 | `longbench-zh-multifieldqa_zh:1a6c19fed6361cbfbfd0df3fb7ca1e01f2a2ffa0da9903bc` | PASS | 17120 |
| 22 | `longbench-zh-multifieldqa_zh:51abd2060651563dfb6982a9e5380c6dfdf7c4917056feed` | FAIL | 15955 |
| 23 | `longbench-zh-multifieldqa_zh:741f37778b75805b150988026cf24e53885d61d47a64bd05` | PASS | 18156 |
| 24 | `longbench-zh-multifieldqa_zh:bcedff0a6829ca68f2c33247af3bea6f5fa81c8d2d940fa2` | FAIL | 11758 |
| 25 | `hotpotqa-5a8ad6805542992d82986f93` | PASS | 6623 |
| 26 | `hotpotqa-5a745a6055429974ef308be4` | PASS | 8033 |
| 27 | `hotpotqa-5a729fe55542991f9a20c53b` | PARTIAL | 6772 |
| 28 | `hotpotqa-5ae3992d5542994393b9e722` | NOT_RUN | 0 |
| 29 | `hotpotqa-5ab672165542995eadeeffe0` | PASS | 6252 |
| 30 | `hotpotqa-5a8911ad554299515336129c` | PASS | 8116 |
| 31 | `hotpotqa-5a7b68a75542997c3ec97153` | PASS | 6387 |
| 32 | `hotpotqa-5a7631cd5542994ccc91870c` | PASS | 11362 |
| 33 | `mmlongbench-doc-mmlongbench-doc:2ff6aa9237fc777b6627dc57a486e9225ac5fb86:row-0684` | FAIL | 6766 |
| 34 | `mmlongbench-doc-mmlongbench-doc:2ff6aa9237fc777b6627dc57a486e9225ac5fb86:row-0687` | PASS | 9938 |
| 35 | `mmlongbench-doc-mmlongbench-doc:2ff6aa9237fc777b6627dc57a486e9225ac5fb86:row-0685` | PASS | 6928 |
| 36 | `mmlongbench-doc-mmlongbench-doc:2ff6aa9237fc777b6627dc57a486e9225ac5fb86:row-0431` | FAIL | 6248 |
| 37 | `mmlongbench-doc-mmlongbench-doc:2ff6aa9237fc777b6627dc57a486e9225ac5fb86:row-0434` | FAIL | 7304 |
| 38 | `mmlongbench-doc-mmlongbench-doc:2ff6aa9237fc777b6627dc57a486e9225ac5fb86:row-0430` | FAIL | 6233 |
| 39 | `mmlongbench-doc-mmlongbench-doc:2ff6aa9237fc777b6627dc57a486e9225ac5fb86:row-0959` | PASS | 6130 |
| 40 | `mmlongbench-doc-mmlongbench-doc:2ff6aa9237fc777b6627dc57a486e9225ac5fb86:row-0958` | PASS | 6280 |

机器结果包括q0、有效改写、真实Embedding/Rerank查询、计划/Bridge Trace、最终答案、来源ID/版本/哈希、调用request_id、模型、结算和次级评价。

## 8. 原始证据与下一步

- 完整gzip：129,799,439字节，SHA256 `5f06ca3479bf4d3dad5548183ce8fcbf5a20af10056bfae4d8735e5e35cb93a6`；3307文件manifest全部校验。ECS原件保留，本地快照已获取。
- 最终离线提取SHA256：`79ecc4f19dd1d08b415270387be8df7003627d8005c14e906c21fc90665943a7`。
- 最终技术审计SHA256：`2caac5b494b06eef17d1da7a5417cf10003678745b539115a3543d269f483d38`。
- 已写Owner完成标记，授权grant及38账本条目未重开；无后续自动Provider发送。

下一个最小任务：先修复已定位的引用范围投影合同，再用现有失败题核查候选到Context的证据丢失路径；随后按同一份固定证据做Cheap/Expensive对照和现有P5校准。规则COMPARE/PARALLEL需另有实际触发样本，当前40题不能证明其效果。未修改P5、阈值或生成Prompt，没有新增路由器、第三规则族或复杂规划。

本次仅本地提交报告；未Push、打Tag、替换正式服务、重新入库或重建索引。当前结果不构成生产可用批准。
