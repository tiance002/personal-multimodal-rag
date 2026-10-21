# S8.3-R：每会话证据配置与六题真实 A/B

## 结论与范围

**工程修复 PASS；两批12次真实正常API问答 COMPLETE；质量为混合结果，候选不推广；整体记录为 PARTIAL。** PARTIAL包含明确的内存观测缺口：12/12次均没有保存谷值/峰值，不能补写为0或已通过。

本轮仅补齐每会话配置，完成既定6题、每题A/B各一次。新增 **48次Provider、106,570实际Token、新UNKNOWN 0**。第一批24次/63,968Token，第二批24次/42,602Token；问题运行跨度约13.59分钟及1.53分钟，均未超过40分钟目标。没有重跑D8已完成的本机Linux三题、383项旧回归或六套全量评测。

- A：`source_evidence_mode=off`；B：`bounded_source_v1`。
- 两臂Graph关闭，floorless Top1关闭，阈值0.3、Top1最低0.15；模型、Prompt、DYNAMIC、输出上限不变。
- tenant332已有可信主体，完整合法KB含干扰文档，`document_scope=[]`；不按Gold指定Chunk或文档。
- 生产/日常默认仍为云调用关闭、证据模式off。只在本轮受控正常请求中启用B。
- 当前授权已 **CLOSED**；未使用12次/93,430Token已撤回。旧2条UNKNOWN的完整记录哈希保持一致，不释放、不重发。

机器结果：[S8_3_R_REAL_AB.results.json](S8_3_R_REAL_AB.results.json)。逐项事实、引用、实际请求身份/哈希、候选分数、SQL与完成状态见该JSON及其`evidence/s8-3-r-20261020`附件。

## 1. 实际源码、身份与最小改动

分支`codex/s8-six-dataset-evaluation-20261019`，起点`501c739305a19ee3f12b847be85591349dc3439b`。本轮产品实现及真实运行源码：**`29ecf2f5d3037eed99a4de783546dee272a761bd`**。报告交付提交使用`git log -1 --format=%H -- docs/evaluation/S8_3_R_REAL_AB.md`查询；最终交付消息给出完整SHA。

| 文件 | 改动 |
|---|---|
| `backend/weknora_port/http_api.py` | 正式`SessionCreate`接受可选`off/bounded_source_v1`，写入既有会话配置 |
| `backend/weknora_port/runtime.py` | 沿用正式配置解析，保留显式off与缺省值区别 |
| `tests/weknora_port/test_conversation_configuration.py` | 配置持久化、缺省与覆盖的回归 |
| `tests/weknora_port/test_http_api.py` | 正常会话入口与非法配置合同 |

配置路径为`sessions.context_config.conversation.source_evidence_mode`。省略/null不强制关闭已有运行配置；显式off可关闭；没有新Schema、公开测试API或默认策略变化。本轮沿用此前S8.3的来源保护/有界回取实现，没有重新修改Search、Rerank、Merge、Prompt、路由或索引。

ECS实际镜像`sha256:80e2f2d84bf67d7d22117d98676049aa9cd3101e69b20f780d5fd9eeaf931a36`；本机Linux验证镜像`sha256:786a6a9593c24de598fe2c037bff1910b6f63199b6096a0c996d2e15a717bbdf`。**镜像ID不同**，508个打包文件SHA和依赖版本一致；远端复用既有基础镜像、离线构建2文件增量，不把它描述为相同镜像ID。

ECS PostgreSQL数据库`weknora_s8`，物理system ID`7698420805889187877`，PG17.9/vector0.8.1/pg_search0.22.6；Redis DB8/前缀`weknora-python-s8:runtime`。主体SHA为`00f2fd3dad2b741924376adab7fd29c12a93bc7a1fe4e8e1f0f97cacd418fa83`。所有12次持久化记录均核对tenant332及既有Scope。每日API/Worker更新到该合法源码，PG/Redis未重建；tenant1配置不变。

验收使用现有正式`create_application()`装配的私有候选容器，无新公开端口、不挂载替换`/app`源码、无runtime_factory注入。客户端按正常创建会话→POST→SSE→历史/来源GET执行；正式Adapter、原SendGate对象、持久化账本和Provider身份检查保留。每个子执行仍≤12次/100,000Token，并串行受本轮共享60次/200,000Token约束。

## 2. 核心事实结果

| 样本 | A真实结果 | B真实结果 | 最早问题/本轮结论 |
|---|---|---|---|
| MIRACL91294 | 84字符目标原文在Merge丢失；wire有生成摘要，答案仍列对8个邻近星座/方向 | 原文完整进入wire，目标Summary退出；答案正确 | 原文送达改善；不把摘要碰巧答对当原文送达 |
| SciFact1101 | 原文+错误Summary均送达；REFUTES正确，但将mesoderm称为“间质” | 原文送达、Summary退出；正确使用“中胚层”，抑制/增强方向正确 | 历史摘要术语误译干扰；B本次完整事实审查通过 |
| SciFact241 | 原文+错误Summary送达；SUPPORTS与约5小时正确，却补充反向因果解释错误 | 保留1:1锁定、峰值前约5小时；不再断言错误方向 | B回答窄问题正确；未复述的可选机制不算事实矛盾 |
| Frost Place | 第二跳地点在Rerank丢失，回答明确说无法确定地点 | 第二跳仍丢失；把Beaumont居住地误推成seminar在纽约，新增无依据断言 | 两臂1/2必要关系；B存在回答退化，不要求本修复补回另一文档 |
| Leyden成功对照 | Pieter van Musschenbroek身份、1746年Leyden归属及tribometer三项事实齐，回答正确 | 同样三项齐、回答正确，生成Summary退出 | 代表性成功题保留；未宣称发明史排他归属 |
| 无证据邮编 | SearchNothing→model fallback，不编造邮编；却把未读文档列表元数据当成事实 | 同样不编造邮编，但把KTH元数据当会议地点事实 | 邮编克制通过，完整事实可靠性失败；无正常Context，不冒充RAG正确答案 |

前3题目标原文实际wire覆盖 **2/3→3/3**，目标生成Summary **3/3→0/3**。两个SciFact的官方立场标签均为两臂2/2正确，术语/因果扩展另行评价；MIRACL只有文档qrels，没有官方生成答案Gold，本报告对8个名称/方向进行来源人工审查。Hotpot答案与支持关系分开；邮编是工程反例。**不混合这些指标计算官方全局问答准确率。**

出处为实际原文文件名，引用粒度仍为文件级。已验证来源HTTP回读、身份/版本/quote与对应wire；未做本轮浏览器点击或逐句引用UI验收。Frost引用Beaumont文件只支持任职年份，不支持被编造的城市。邮编fallback中的文档标题不构成其细节断言的有效引用。

### 检索、合并与最终输入

下列数量顺序为Search→Rerank保留→Merge→最终Context：

| 样本 | A | B | 必要事实 |
|---|---|---|---|
| MIRACL91294 | 30→9→8→8 | 30→9→9→3 | B完整84字符原文；额外2个同星座块46+104字符不支持本问邻接事实 |
| SciFact1101 | 30→2→2→2 | 30→2→2→2 | 1182字符正文两臂均有；B另有89字符真实标题 |
| SciFact241 | 30→2→2→2 | 30→2→2→2 | 1196字符正文两臂均有；B另有100字符真实标题 |
| Frost | 30→3→3→3 | 30→3→3→3 | 2007–2010任职事实有；Franconia/New Hampshire地点事实两臂均无 |
| Leyden | 30→7→7→7 | 30→7→7→4 | 身份＋两项发明关系三项原文均保留 |
| 邮编 | 30→SearchNothing | 30→SearchNothing | 无完成的Rerank/source_unit/Context快照；实际Rerank回执与fallback请求均保留 |

判断以最终Provider请求JSON为准。保存的`finalized_context.rendered_contexts`是另一层旧渲染，可能仍带`<description>`，不能用其代替实际wire。5个正常B请求实际仅含可回读source_text，目标Summary不再入wire；邮编走另一条fallback，SYSTEM中的生成文档描述仍在，不能因没有`# Summary`标签就声称摘要影响已消除。A未标`evidence_kind=source_text`不代表没有原文。

严格原文回取会省略未获有界证明的合并投影；被省略不等于已证实无关。MIRACL B还保留2个不支持邻接问题的同文原文块，不能将Context缩短一律解释为降噪收益。Frost B新增的传记邻块也没有地点支持；各目标和噪声说明见逐案JSON。

### 比较限制

第一批QU请求/Rewrite、Embedding/Rerank请求和Search ID/顺序/内容/分数一致，但**同输入的新BGE真实分数有波动**，MIRACL Merge数量也不同。因此它是一次系统真实对照，不能声称所有差异唯一由证据模式造成。此前冻结post-Rerank离线回放仅作独立历史证据，未重跑。

第二批3对QU请求体相同，但Rewrite输出均不同，包含措辞、大小写、中文/英文变化；Embedding/Rerank输入、Search集合/顺序也不同。Frost退化、Token/延迟变化均保留，**不全部归因于B**，也不为消除混杂而重发。

## 3. Token、延迟、数据库与内存

每次实际均4次Provider：QU、Embedding、Rerank、最终生成。Qwen chat24次/42,320Token（输入40,138、输出2,182）；BGE-M3 12次/226Token；BGE Rerank12次/64,024Token。合计输入104,388、输出2,182、总计106,570。实际DeepSeek0次，无升级。所有usage为供应商报告，不估算费用；新UNKNOWN为0，不把旧UNKNOWN未知消耗算0。

实际QU为SiliconFlow `Qwen/Qwen3.5-4B`，temperature0.3/max_tokens150/enable_thinking=false；最终Cheap同模型，temperature0.7/max_tokens2048/enable_thinking=false。12次最终生成finish_reason均stop，没有length截断。POST耗时包括SSE完成和随后历史读取，**不是单次生成耗时**。

| 样本 | 全链Token A→B | POST至历史回读 秒 A→B | wire证据正文字符 A→B | wire用户消息字符 A→B |
|---|---:|---:|---:|---:|
| MIRACL91294 | 7,941 → 7,165 | 9.93 → 6.43 | 791 → 234 | 2495 → 1089 |
| SciFact1101 | 14,660 → 14,528 | 11.70 → 10.12 | 1463 → 1271 | 2055 → 1916 |
| SciFact241 | 9,884 → 9,790 | 9.81 → 9.32 | 1396 → 1296 | 1944 → 1897 |
| Frost Place | 7,155 → 7,079 | 7.88 → 11.15 | 2354 → 1243 | 3073 → 2043 |
| Leyden成功对照 | 8,029 → 7,673 | 10.38 → 9.40 | 3026 → 1505 | 4633 → 2960 |
| 无证据邮编 | 6,285 → 6,381 | 8.89 → 11.17 | 0 → 0 | 58 → 114 |

邮编行的证据正文0不代表SYSTEM没有描述；两臂仍带未验证生成元数据。字符差异是实际观察值，不等于Token节省因果估计。一次/臂无法支持延迟显著性结论。

记录的first_nonempty_content是首个可见SSE正文，通常在模型生成后缓冲输出，**不是Provider首个有效Token时间**。raw timing可区分传输open/header/首原始字节/read完成；无法可靠拆出DNS、TCP、TLS、模型排队/计算，也不新增探针。

| 样本 | 窗口内SQL execute次数 A→B | execute耗时和 ms A→B |
|---|---:|---:|
| MIRACL91294 | 157 → 165 | 767.92 → 560.62 |
| SciFact1101 | 155 → 159 | 567.88 → 503.45 |
| SciFact241 | 155 → 158 | 517.65 → 496.73 |
| Frost Place | 161 → 162 | 521.71 → 683.00 |
| Leyden成功对照 | 157 → 169 | 506.07 → 534.09 |
| 无证据邮编 | 136 → 136 | 584.59 → 780.35 |

SQL仅观察私有API进程在本问题ASGI窗口内的`AsyncConnection.execute`，包括持久化和Provider记账，不是SELECT-only或整个PG工作量；不含fetch/decode及其他进程，时间和可重叠。12次共1870个execute，窗口内完成1868、失败0；2个未在结束快照计完成，不声称所有SQL完成。累计execute时间7024.052ms，不解释为总墙钟数据库等待。

**内存：12/12缺失历史采样文件，空闲MemAvailable谷值与候选内存峰值均NOT_RECORDED。** 只有1GiB限额可证；候选快照没记录OOMKilled，不宣称候选全无OOM。最终日常API/Worker独立inspect为运行、restart0、OOMKilled=false，不能反推历史候选峰值。

旧工具只在serve外层finally落盘。Uvicorn0.35.0结束时重发捕获信号可使进程先于外层finally退出，这是与缺失相符的机制；没有逐次信号记录，不能声称12次原因均已独立证明。诊断工具现改为存活期间逐样本fsync检查点＋原子快照，保留已有文件；正常退出/子进程native terminate等11项离线测试通过。**此修复未用于已结束的真实运行，不补造历史内存，也不重跑问题。**

## 4. 首次工具错误与恢复

1. 静默runtime check被当JSON解析；改用鉴权readyz，均在发送前，Provider0。
2. 完整镜像SCP180秒超时；保留部分文件，改传2个源码增量，用既有远端基础镜像network=none构建并核验508文件/依赖。不是模型UNKNOWN。
3. 首个MIRACL A实际已SSE complete、4条SETTLED、usage一致，但诊断工具因流解析读到`[DONE]`后未读传输EOF而误报FAIL。保留原http-result和STOP记录，补充严格的HTTP200/SETTLED/真实usage匹配/finish_reason stop/最终DONE判定。只读GET历史两次、来源、PG、保存SSE/wire恢复同一次结果，**没有第二次POST或Provider**。
4. 停止保留的容器被`docker ps -a`误判active，随后包装器输出文件名冲突；保留零POST失败，修工具后仅继续未提交案例。没有重复已执行B。
5. 邮编SearchNothing由正式service处理，无finalized_context；工具只在确切fallback完成、PG空引用/答案匹配、真实wire和账本均通过时允许该合法终态。未修改产品fallback。
6. 内存落盘缺口按上节记录，补的只是诊断工具和离线测试。

## 5. 实际命令与退出码

工作目录及历史失败文件均保留；未实现的命令不得报告通过。完整原命令和receipt见JSON/附件。

```powershell
# 产品配置RED：exit1，5 failed/10 passed/56 deselected
& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m pytest tests/weknora_port/test_conversation_configuration.py tests/weknora_port/test_http_api.py -k session_source_evidence -p no:cacheprovider
# 修复后两个受影响模块：exit0，71 passed
& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m pytest tests/weknora_port/test_conversation_configuration.py tests/weknora_port/test_http_api.py -p no:cacheprovider
# 诊断工具最终离线：exit0，11 tests OK（已执行，不重复跑旧用例）
& 'E:/RAG quention/.venv/Scripts/python.exe' 'E:/RAG quention/var/reports/s8-3-r-20261020/test_tools_offline.py'
# 本轮保护文件回查：exit0，16路径/20文件原SHA和git状态一致
& 'E:/RAG quention/.venv/Scripts/python.exe' -B 'E:/RAG quention/var/reports/s8-3-r-20261020/check_protected_worktrees.py'
```

远程SSH包装器以现有固定主机/密钥执行保存的Python脚本：`root_remote_preflight.py`修复后exit0、`root_remote_delta_build.py`exit0、`root_remote_update.py`exit0、`root_install_execution.py`exit0；`root_start_batch1.py`、仅未执行部分恢复脚本及`root_start_batch2.py`启动exit0。启动成功本身不作问答通过依据，12个最终HTTP/PG/child ledger结果与parent COMPLETE才作完成证据。`root_recover_first.py`exit0/新增Provider0；`root_final_audit_close.py`exit0，48条新调用与usage逐项对账/授权关闭。原始preflight失败、firstA误报、零POST包装器错误与恢复文件均保留。

最终`git diff --check`、`git diff --cached --check`及本地提交结果见交付receipt/最终消息。未跑全仓、浏览器新场景、D8旧Linux三题或旧383项；未上传/解析/Embedding/建图/改Gold或旧授权。

## 6. 数据保护、结论和停止

PG末次11736文档/28657Chunk/28657向量与前测计数一致，Provider记录12920→12968、未决非UNKNOWN0；旧UNKNOWN2条完整哈希不变。不能将计数一致扩大成全库逐字节快照相同。主工作树既有16个未提交路径/20文件SHA与状态一致。来源/版本/真实引用通过各请求只读审计；仅新增本轮会话、合法Provider记录和执行证据。

**保留候选，生产默认不推广。** 核心3题说明真实原文恢复和去掉冲突摘要有工程价值，但样本小、BGE/Rewrite波动、Frost退化和fallback元数据风险均阻止作全局改善结论。

下一步最小建议（本轮不执行）：

1. 单独审查SearchNothing fallback的生成描述是否被当事实来源，建立无答案反例；不把本轮正常Context的原文约束错误宣称覆盖fallback。
2. Frost保留Rerank第二跳质量缺口及主体居住地/事件地点混淆；不以Summary清退冒充关系事实校验。
3. 若后续批准继续候选评测，使用已离线修复的持久化内存采样，另核查有界投影省略的证据；不为本轮缺指标重跑已完成问题。

本轮授权关闭、实验候选进程已停；日常API/Worker保留云关闭/证据off及1GiB限制，现有PG/Redis保持。仅本地提交，不Push、合并、Tag或进入新阶段。

完整真实证据归档保留于`E:/RAG quention/var/reports/s8-3-r-20261020/checkpoint-1792495678128822528.tar.gz`，8,325,289bytes，SHA256 `d0a69d3af0f042f09f6d4c26fa4bcba815b462d813a7a5049a3459949839da3d`。Git只提交紧凑JSON、必要测试日志和诊断工具，不提交密钥、DSN、Bearer或敏感头。诊断工具后续执行仍需新有效授权，原grant不会因工具留在仓库而重开。
