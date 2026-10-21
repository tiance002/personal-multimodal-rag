# P29 — Rerank 开关真实 A/B 与 Fallback 质量验证

日期：2026-10-17，Asia/Shanghai。实际基线：`14ff9b2e7b5988b2f4b12759ae205317342abe94`。
固定上游仍为 `3e8b0bfc80b845b2d4b2ed683994748741450a97`。

## 结论

**P29_PARTIAL**：3组真实配置对照已执行；7/7次问答请求完成，均未重试。真正空库的专门 Fallback 验证未达到预定路径，必须标记失败，不能以普通回答替代。调用上限已用完，停止追加。

**暂不调整默认 Rerank 策略。** 关闭重排使更多有效来源进入 Context，但没有得到更可靠的完整答案：0434只改善了答案实体命中，章节关联仍未证明；0959引入额外、不充分支持的答案项；0958仍缺少完整视觉事实。所有组的 Rewrite 响应/检索排序都有变化，且只有3组单次样本，不能归因于纯粹的重排效果或宣称全局最优。

真实链路为 FastAPI HTTP → Query Rewrite → 正常适用的 Embedding/pgvector/Hybrid → Rerank（on时）→ Merge/原文投影/Context → SiliconFlow Cheap 或原生模型 Fallback → SSE/历史及来源回读。无 Mock、无直接塞 Gold、无重新解析或入库、无 DeepSeek 请求。

## 1. 控制条件、授权和身份

- P25原有 tenant332、KB `7f1d720d-9561-446f-a8cc-6a562c339d23`：0434、0959；P27原有 tenant344、KB `63236a5c-c0d6-499d-84e5-34475564f1c0`：0958。
- 每题每条件独立新会话，7个唯一 request_id，`document_scope=[]`，两组相同KB及文档范围。两个租户的 `memory_config=null`；会话开始时无历史和账本行。
- 原始问题、模型、索引、Prompt、生成参数固定。Cheap 为 `XingChenAGI/Xing4.0-29B`，temperature=0.3、max_tokens=1024、enable_thinking=false。Embedding `BAAI/bge-m3`、Rerank `BAAI/bge-reranker-v2-m3`，均为原 SiliconFlow 配置。只对新会话保存已有 `context_config.conversation.enable_rerank`，不修改旧会话或全局默认。
- 普通产品继续 `fallback_strategy=model`；既有0.3阈值、0.15 Top1保底、Parent/Child和来源安全均未改。
- 原P21空KB属于tenant339，不能借用身份。本轮按正常API在tenant332创建空KB `4b365704-9663-431f-8d29-1f27dacf4044` 并设置测试外发许可，没有上传或索引。
- 持续Owner授权见 `REAL_EGRESS_AUTHORIZATION_20261015.md` 的 SiliconFlow“不限／之后真实测试按以上来”及本轮允许开启独立有限围栏的指令。新建 **P29-20261017-SF-300000**，未重开P25/P27旧grant。
- 沿用 ApprovedTokenAuthority、真实账户 `siliconflow-owner-main`、原 approval_source、carry-in14、真实SendGate及月锁/CAS/不可覆盖receipt。本轮只允许三个SF模型；DeepSeek不在配置allowlist中。

PG：`127.0.0.1:56423/weknora_python_port_20261013`，system identifier `7696088403504697382`。Redis：`127.0.0.1:56424/15`，run_id `dcff9d65a25725574ba0cfbc743d409fac211ef0`，独立 `weknora-python-p29-20261017:{tenant}:ab` 前缀。任务仅启动两个API，未启动Worker或消费图片/记忆队列。

## 2. 实际数值

Context块数为最终原文投影后真正送达Cheap的块数；Merge块数为投影前中间值，二者不混用。Token列包含该次完整问答的所有辅助调用。延迟取实际HTTP POST开始至完整SSE接收结束，不包括后续66次来源回读。

| 样本/条件 | Search | Rerank输入/保留 | Merge | Context块/字符 | 输入/输出/总Token | POST秒 | 模型Fallback |
|---|---:|---:|---:|---:|---:|---:|---|
| 0434 on | 30 | 30/0 | 未执行 | 0/0 | 8,148/221/**8,369** | 7.770 | 是 |
| 0434 off | 30 | 跳过 | 15 | 28/15,130 | 9,152/192/**9,344** | 10.118 | 否 |
| 0959 on | 30 | 30/2 | 2 | 2/1,720 | 8,138/136/**8,274** | 6.768 | 否 |
| 0959 off | 30 | 跳过 | 11 | 28/16,410 | 8,994/237/**9,231** | 9.547 | 否 |
| 0958 on | 9 | 4/0 | 未执行 | 0/0 | 4,921/342/**5,263** | 8.672 | 是 |
| 0958 off | 9 | 跳过 | 3 | 8/4,940 | 6,416/167/**6,583** | 17.079 | 否 |
| UTC空库 | 0 | 无调用 | 0 | 0/0 | 3,335/145/**3,480** | 3.020 | **否，未验证目标路径** |

关闭重排相对开启的完整请求Token增量分别为 **+975、+957、+1,320**；POST耗时分别增加 **2.348、2.779、8.407秒**。这是本次单次实测差值，包含改写/召回差异，不是统计稳定的因果估计。没有把Context增多判为质量改善。

全部7次最终Provider `finish_reason=stop`。普通SSE事件本身未携带该字段，原始case文件的 `finish_reason=null` 保留，不改写历史记录；报告从真实Provider回执补齐来源明确的stop字段。

## 3. 答案、证据支持和幻觉分别评价

| 样本 | 开启重排 | 关闭重排 | 判断 |
|---|---|---|---|
| 0434 | 拒答，未回答Gold；但还断言两份文档都没有训练数据集信息，超过未读正文metadata能支持的范围 | 回答Kaggle的Cat vs Dog dataset，实体命中Gold；Context确有该文字及训练代码，但没有Coding time章节关联 | 实体回答改善，完整证据支持仍为PARTIAL；不能认定章节归属已证明 |
| 0959 | 无线电波、微波及长距离通信关系均有两块原文支持，语义正确 | 两个核心概念正确，但另加光纤、有线通信。扩大的Context分别提到TIR和数据速度，未充分支持其作为原题所问长距离通信答案项 | 原本正常答案未改善，精确性下降；更大Context带来关联越界 |
| 0958 | 0证据的原生Fallback列出镜子、水面、墙壁等7种常识例子；未声明不是知识库答案，也未完整回答Gold的两个指定例子 | 实际派生证据进入请求，但模型把残缺OCR补成“shiny surface”，引入Context没有的mirror，随后承认缺第二个例子 | 关闭后仍非完整、有证据支持的Gold答案；谨慎表述有所增加，但引用式补全文字不可靠 |
| UTC | 普通常识回答UTC+8、例子正确；无来源声明，无虚构文档名 | 不执行第二次 | 实际并非原生Fallback，无证据Fallback专门验收失败，不能计入其成功率 |

### 0434：不能把真实差异全部归因于重排

两组Rewrite请求的wire逐字节相同，模型独立响应分别改写为：

- on：`PPT中coding time部分引入的用于训练的数据集是什么`
- off：`PPT中coding time部分介绍的训练数据集是什么`

Search各30，交集27。明确的Kaggle片段 `085643dc-5ebb-4b8a-b7f1-75427da40015` 仅在off候选池中；on的30个候选没有明确的数据集名，只有相关PPT、分类器Summary和训练代码。因此本轮on并不是“Search命中该Kaggle片段后被Rerank丢弃”。P28相同冻结候选离线对照仍是历史证据，不能与本轮混称一次实验。

本轮on原始30个Rerank响应的最高分约0.0244743，低于0.3/0.15，因此合法来源仍在原生门限下全部退出，Merge未执行。off的28块中27块来自目标PPT、1块来自物理课件。即使包含数据集和训练文字，也不能把它们拼成已证明的章节关联。

### 0959：重排仍有过滤收益

on只保留物理课件的两块直接证据，准确回答两种波。off的28块中18块来自物理课件、10块来自无关机器学习PPT；原文身份都合法，但合法不代表与问题相关。答案把Context中的其他通信内容扩展成原题答案，说明关闭重排的噪声风险真实存在。

两组Search交集也是27/30，顺序不同；仍需标记Rewrite/召回混杂，不能单凭本题估计普遍退化率。

### 0958：视觉证据质量和重排分开

两组候选ID集合相同（9/9），顺序不同；on用中文改写及4个中文扩展，off保留英文及4个英文扩展。on实际只有4个非空enriched passage送入Rerank，分数约0.0185024、0.0148857、0.0127014、0.0019461，均低于0.15/0.3；其余纯图片链接清理为空。Rerank确实丢掉了OCR/Caption等合法相关片段，但这些片段并没有完整Gold事实。

off的实际请求含对应OCR/Caption及原文，OCR原句仍为 `Reflecting surtace, eg shinyet`，无mirror、无两个清晰例子。答案的镜子和“Reflecting surface, eg shiny surface”引文是模型补全，不能当成来源原句。本轮没有重跑OCR、Caption或DeepSeek。

### 真正空库场景覆盖缺口

空KB是真的，也确实没有文档/索引。但这次实际记录显示 `is_fallback=false`、system override为空、普通生成system，所有阶段完成且Context为空，未发生原生SearchNothing短路。精确classifier响应/intent没有保存，不能臆造其为某个具体标签。

这是本轮测试选题/路径准备未确保到位的失败，不是Fallback通过。7次限额用完后没有追加第8次或改写问题重试。其答案只能评价为知识库外的普通UTC换算，不能证明空检索Fallback会如实声明来源。

## 4. 三组共同控制与混杂

原问题、租户/KB范围、空document_scope、模型、生成非messages参数、Rewrite请求wire均相同。实际Rewrite响应、Embedding初始输入均不同：Search集合交集分别为27/30、27/30、9/9，三个组的顺序均不同。完整输入与差异ID保存于 `metrics.json::pairs`。

主动改变的配置只有Rerank；独立实时模型造成的随机差异未被人为消除，没有重复调用选优。因此可报告配置下的观察结果，不能报告冻结候选的纯Rerank因果效应。0434尤其同时存在检索召回变化。

## 5. Gold与评分口径

复用P25/P27冻结Gold：0434=`Cat vs Dog dataset`；0959=`['radiowaves', 'microwaves']`；0958=`['shiny object', 'mirror']`。仅事后评分，不进入请求、索引或Caption。

沿用 `eval_center/weknora_scoring.py::answer_diagnostics` 的normalized EM/character F1，全部6条完整长答案的normalized EM为0；这不是语义准确率。character F1分别为0434 on/off 0.107143/0.053860、0959 0.242424/0.132013、0958 0/0.106996。完整答案、Gold和手工事实/证据判定都保留在 `quality.json`。官方MML Judge **NOT_RUN**；UTC没有冻结答案Gold，不伪造准确率分数。

## 6. 调用、结算与收口

7次真实问答POST；**31次真实SiliconFlow调用**：7次Rewrite、7次最终Cheap、14次Embedding、3次Rerank。共 **49,104输入＋1,440输出＝50,544 Token**，31条均settled；新增UNKNOWN=0、reserved=0；DeepSeek请求=0。

围栏打开前已占用1,739,220（含14 carry和61条UNKNOWN的823,355 held）；临时上限2,039,220。执行后收口为1,789,764、headroom=0，**撤回249,456未用空间**。历史调用行哈希由原锁/CAS收据验证保留；61条UNKNOWN、carry/approval不变，DeepSeek调用摘要不变。两个新authority配置已disabled，原使用配置另存used文件供审计。

66/66个正常API来源回读为200，KB/document/version/原文哈希一致。7个SSE答案与保存历史一致，公共references均为空；7个解码前真实流也没有内部ref标签。不存在虚构可点击引用ID，但这不能洗白上表的无依据主张或伪原句。原有3文档、97chunks、97向量的全行摘要前后相同，没有重建索引或改写Caption。

任务两个API进程在全部结算、零reserved后停止；未启动Worker。仅终止本轮记录并核对命令身份的进程树，PG/Redis保持运行，端口56425/56427已关闭。

## 7. 修改、命令与证据

没有生产代码修改。新增 `scripts/p29_rerank_ab.py` 只负责冻结会话/已有配置、单次正常HTTP请求及证据保存；实际外发仍由应用ApprovedTokenAuthority/SendGate处理。围栏/启动/停止/只读索引检查及复用P17原始响应观察器均为E盘任务脚本，随证据归档。

主要实际命令（均在本工作树、Python为 `E:\RAG quention\.venv\Scripts\python.exe`；完整环境及文件在结果JSON/归档）：

| 命令 | 退出码 | 结果 |
|---|---:|---|
| `python -B E:\WK\run\p29-20261017\p29_fence.py open` | 0 | 独立300k围栏 |
| `python -B E:\WK\run\p29-20261017\start.py`（修正任务Redis前缀后） | 0 | 两个真实API ready |
| `python -B scripts/p29_rerank_ab.py prepare` | 0 | 新空KB及7个独立会话；0问答发送 |
| `python -B scripts/p29_rerank_ab.py query <case>`，表中7个case各一次 | 全部0 | 7次真实请求；逐次stdout、SSE、账本和回读保留 |
| `python -B E:\WK\run\p29-20261017\stop.py` | 0 | 停止本轮API，零reserved |
| `python -B E:\WK\run\p29-20261017\p29_fence.py close` | 0 | 围栏收口 |
| `python -B E:\WK\run\p29-20261017\index_snapshot.py before/after` | 均0 | 原索引摘要相同 |
| `python -B E:\WK\run\p29-20261017\verify_ledger.py` | 0 | 31条/50,544、UNKNOWN和DeepSeek保留 |

保留初次Redis前缀配置校验失败（子进程exit1、父进程被终止exit-1），以及一次Windows非强制终止失败/随后重复启动保护的exit1。它们均发生在问答发送前，修正的只是任务配置/自有进程管理，不是额外问答尝试或生产Bug修复。

原始证据：`E:\WK\run\p29-20261017`。仓库证据为本报告同目录 `evidence/p29-rerank-real-ab-20261017/` 下的汇总、哈希清单及无损压缩归档；原始大JSON保留，不通过改写历史或丢弃失败来缩减。没有跑全项目回归、浏览器或新一轮模型试验；对执行工具仅做语法/help和最终证据一致性校验。

## 8. 对六项问题的最终回答

1. 有效原文进入Context分别为0434 **0/28**、0959 **2/28**、0958 **0/8**（on/off），但不是同等数量的“回答所需事实”。
2. 0958的合法图像派生相关片段确实被重排清空；其Gold事实本来不足。0434本轮on连明确Kaggle片段也未召回，不能把该片段缺失完全归因于重排；P28离线证据与本轮分开。
3. 0434实体命中改善但章节未证实；0959没有改善，出现额外答案项；0958没有得到完整有依据的答案。
4. off使Context和请求成本/耗时增加，逐题差值见上表；不作小样本总体推断。
5. 有：0958原生Fallback无来源声明，0434原生Fallback对未读正文作无依据确定性否定。UTC虽给出正确常识，但未走原生Fallback，不能当其验证成功。
6. **样本不足且存在Rewrite/召回混杂，暂不调整默认。** 优先另行验证改写/召回稳定性和Fallback的metadata/来源声明遵循，并补足真正空KB路径；本轮不实施任何策略修改或新增调用。

原有 `.serena/project.yml`、`progress.md` 修改及六个历史未跟踪文件保留，不暂存。不Push、部署、合并、打Tag或启动下一轮。
