# P14 — 纯机器上下文增强验证

## 结论

**P14_COMPLETE：限定实验完成，质量结果不支持直接接入生产。** 仅测真实 SiliconFlow Rerank，不是完整 RAG 问答验收。固定邻接机器文本没有改善0434正例分数/排名；0959仍第一，但分数下降至现有top1回退区间。未改全局阈值、Prompt、路由、MinerU或计量适配器。

## 1. 冻结范围及实际使用的上下文

源码基线 `9dcf1de7e08ada631e4ebae263d1de12a12d88f9`；四次真实执行源码 `b1ca2b20b2192777a7dbbe73daf625442ff7d5d1`。模型固定 SiliconFlow `BAAI/bge-reranker-v2-m3` / cohere-rerank / probability，沿用已有注册表、外发许可、Token Authority、P1SendGate。无聊天、Embedding、DeepSeek调用，无重新解析/上传/索引。供应商部署内部版本不可独立固定，不作确定性承诺。

候选为P13相同31个ID及顺序：原30候选加0959正例。对所有30个原文text候选执行同一规则；生成summary slot8原样保留。0434 Query保持P13实际中文Rewrite；0959使用P13冻结的忠实中文译文（历史研究者翻译，并非新机器Rewrite）。完整Query和全部payload见同名JSON与 frozen-v2.json。

构造函数不接受Query、Gold、标题或分数：只从同document/version、有效的MinerU原文text中取前后各一个邻居；Unicode来源offset重叠去重，再按空白归一化比较的段落/行去重，保留原始字符。原anchor完整保留；到anchor边界最近的新增字符优先，总上限1200 Unicode字符，分隔换行也计入。来源页/块只作定位，未人工生成章节关联。**这是机器邻接上下文，不是已恢复的真实章节层级。**

0434 slot14原anchor为chunk17、`085643dc-5ebb-4b8a-b7f1-75427da40015`，原source Unicode `[7276,7744)`，冻结wire466字符。机器前邻chunk16 `[6855,7356)`，后邻chunk18 `[7713,8225)`；坐标重叠80/31字符。去重限长后实际添加 `[6911,7276)` 和 `[7744,8109)`，前后各365字符，合计1200（含4字符分隔符），anchor在wire offset367。新增片段locator相交页为22/23和35，anchor本身跨多页；不能把该拼接冒称一个真实章节。未补入P13人工读取的“Coding time!”图片标题。

实际0434增强wire全文如下，截断首尾也是来源原样substring：

```text
p learning provides a very flexible, (almost?) universal, learnable framework for representing world, visual and linguistic information.

•Deep learning can learn unsupervised (from raw text/audio/images/whatever content) and supervised (with specific labels like positive/ negative)

(as summarised by Richard Socher 2014)

2006+ : The Deep Learning Conspirators



Audio Recognition

(chart by Clarifai)

Image Recognition

(chart by Clarifai)

Natural Langauge Processing

8

Natural Langauge Processing

cloudy days don't last forever.

chibird

DL? How?

almost at the code...

Deep neural networks learn hierarchicalfeature representations

(picture by Dato)

Kaggle's Cat vs Dog dataset (25k dog/cat pictures)

(picture by Dato)

Dashboard

Competition Details » Get the Data » Make a submission

Home

Data

Make a submission

Create an algorithm to distinguish dogs from cats

Information Description Evaluation Rules Prizes Winners

Forum

Leaderboard Public Private

Visualization

My Team GitHub C

In this competition, you'll write an algorithm to classify whether images contain either a dog or a cat. This is easy for humans, dogs, and cats. Your computer will find it a bit more diffi
```

0959同样用前后邻居，保留原“Long distance communication…ionosphere refracts radiowaves…microwaves…satellites”证据，加上相邻的设备/优缺点/无线信号文本；完整1200字符与逐段来源见JSON actual_context_records。

长度控制：同doc/version按eligible ordinal+7固定选远处seed，排除anchor及直接邻居，取真实原文substring。逐候选与near字符数和anchor位置相同；0434使用chunk23/24/25。**不是语义无关填充，也不等于模型Token等长**。所有30候选的完整映射/去重记录：`E:/WK/run/p14-20261016/machine-source-materials.json`，SHA256 `2866d7219963815adf71facae02a4b7aa0faa6287881b36a009de6d04a5a0f19`。

## 2. 四次真实结果

| 条件 | 正例分数 | 排名/31 | 当前过滤保留 | 供应商input Token | 秒 |
|---|---:|---:|---|---:|---:|
| R1-0434-ZH-original | 0.000780088711 | 6 | 否 | 5157 | 1.102 |
| R2-0434-ZH-near | 0.000316650257 | 23 | 否 | 12157 | 0.782 |
| R3-0434-ZH-length | 0.000304521236 | 25 | 否 | 12189 | 0.877 |
| R4-0959-ZH-near | 0.277368694544 | 1 | top1回退 | 12188 | 0.815 |

现有生产 `filtered()` 源码AST逐字抽取离线重放：常规threshold=.3；无人达标时只保留top1且score≥.15。0434三次均为空，无额外阈值重试（当前threshold=.3）。0959增强.277369低于.3，通过既有top1回退。此处只重放raw过滤，不代表执行了最终MMR、Context/回答/引用。

0434正例near较原文下降约59.4%，排名6→23；near与等长控制都很低，不能主张添加长度即可提高相关性，也不能据单次试验将下降唯一归因于长度。整个pool同规则增强，排名同时受其他候选变化影响。近邻与等长控制供应商input为12157/12189，确有32Token差异；纯语义与纯长度效应尚不能严格分离。

0959原文历史P13 R6为.5810116529464722、rank1；本批near为.2773686945438385、rank1。只作跨批历史对照，无本批原文重复调用、无方差估计，不能将差值完全归因于邻接扩展。

## 3. 负例与来源风险

| Query/变体 | 负例6 | 负例24 | 负例28 | 跨主题负例 |
|---|---:|---:|---:|---:|
| R1-0434-ZH-original | 0.002043959 /#1 | 0.000032805 /#30 | 0.000237299 /#18 | 0.000431359 /#12 |
| R2-0434-ZH-near | 0.001621029 /#3 | 0.000106364 /#30 | 0.000217012 /#26 | 0.000440168 /#20 |
| R3-0434-ZH-length | 0.001170729 /#11 | 0.000302319 /#26 | 0.000247902 /#28 | 0.000787047 /#17 |
| R4-0959-ZH-near | 未预标 | 0.000127071 /#27 | 0.000234578 /#25 | 0.000098150 /#29 |

全部预标负例均未被当前过滤保留；但0434负例6在原/near中都高于正例，不能用降低全局阈值解决正负分离。负例24在length控制中由.0000328升至.0003023，仍远低门限，也不构成全局安全性证明。未预标的候选不自动算无关。

构造后标签复核（未反馈构造）：near slot4从真实chunk19得到Dogs-vs-Cats/Kaggle竞赛信息，rank8/.00141315；length slot26同样加入该来源，rank1/.00411170，仍未过过滤。应记新增邻居支持，不是单凭原anchor无答案就称false-positive。机器字面触发检测缺失不证明语义无关。若未来只重排原anchor ID、Context/引用仍仅回取原anchor，新增支持会丢失或引用错配；必须携带邻居chunk IDs、document/version、offset映射并验证回读。本轮未接入生产引用路径。

## 4. Token、保护与失败保留

4个真实HTTP请求/响应，全部31结果与adapter分数一致、index双射，恰好每case一次发送。供应商raw `meta.tokens` 与 `meta.billed_units` 一致：**input41691 / output0 / image0**；仅供应商报告观察，不是账本已结算值或独立账单验证。

现有adapter不识别meta.tokens，因此4条均UNKNOWN，保守占用125694；新增已结算0。**41691与125694不能相加，也不能把UNKNOWN算实际消费0。** 本轮不修计量兼容，不释放UNKNOWN。

同一原账户/月账本，有限增量140000：1327808→1467808；完成后关闭至1453502，剩余14306撤销、headroom0、reserved0。原614条记录、57条UNKNOWN及DeepSeek账本全部不变；新增4条UNKNOWN保留。1877既有证据、378继承保护文件、567生产文件哈希不变；KB、文档、chunks、完整embeddings、image tasks前后逐值相同。

首次sender启动exit1：测试Redis前缀缺少开发装配要求的weknora-python-，在assemble/claim/HTTP前失败。保留原frozen.json及real-rerank.log，修正仅runtime prefix为frozen-v2.json；四body/hash/ID不变。后续exit0，未重复供应商调用。无生产Bug确认/修复。报告生成第一次因历史language字段大小写不匹配exit1、未写报告，修正为P13实际ZH后生成；读取证据时一次缺失analysis.py路径和一次控制台GBK输出失败均仅本地展示，不影响模型或评分。

## 5. 命令与证据

运行目录 `E:/RAG-Worktrees/weknora-python-port-20261013`；PYTHONPATH包含本仓库、`E:/WK/deps/reader`、`var/weknora-port/runtime-deps`。TEMP/TMP为E盘；API/Worker/浏览器/容器本轮启动数0。PG127.0.0.1:56423，weknora_python_port_20261013，system_id7696088403504697382；Redis127.0.0.1:56424 DB15，run_id87b4c2a9f76f9f94b34b5f21fe31a967926f343a。独立prefix见frozen-v2.json，不消费共享队列。

实际执行命令、退出码及日志：

- `& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m pytest tests/weknora_port/test_p14_machine_context.py tests/weknora_port/test_p14_rerank.py tests/weknora_port/test_p14_fence.py -q --tb=short -p no:cacheprovider --basetemp E:/WK/run/p14-20261016/unit-final` — exit **0**；`E:\WK\run\p14-20261016\unit-final.log`
- `& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m scripts.weknora_port_p14_rerank --frozen E:/WK/run/p14-20261016/frozen.json --validate-only` — exit **0**；`E:\WK\run\p14-20261016\validate.log`
- `& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m scripts.weknora_port_p14_fence open --approval-file E:/WK/run/p14-20261016/owner-approval.txt` — exit **0**；`E:\WK\run\p14-20261016\fence-open.log`
- `& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m scripts.weknora_port_p14_rerank --frozen E:/WK/run/p14-20261016/frozen.json` — exit **1**；`E:\WK\run\p14-20261016\real-rerank.log`
- `& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m scripts.weknora_port_p14_rerank --frozen E:/WK/run/p14-20261016/frozen-v2.json` — exit **0**；`E:\WK\run\p14-20261016\real-rerank-v2.log`
- `& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m scripts.weknora_port_p14_fence close` — exit **0**；`E:\WK\run\p14-20261016\fence-close.log`
- `& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p14-20261016/inventory.py` — exit **0**；`E:\WK\run\p14-20261016\inventory.log`
- `& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p14-20261016/inventory-after.py` — exit **0**；`E:\WK\run\p14-20261016\inventory-after.log`
- `& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p14-20261016/analyze.py` — exit **0**；`E:\WK\run\p14-20261016\analyze.log`

离线51项通过与真实4次Rerank分开记录。**未实现的命令不得报告通过。** 全部raw请求见provider-requests/，响应见trials/*/response.body，preflight/结果/intent均不可覆盖。结果JSON含全部124个分数、原始响应哈希、来源映射及保护审计。此前P8—P13结果未修改。Git初始.serena与六个既有未跟踪文件保留；仅增加P14实验脚本/测试、报告与progress记录，无共享接口/迁移/依赖/生产配置改动，无需生产整合。

## 6. 建议与停止

**不建议把本轮固定前后邻居规则直接启用到生产。** 0434没有获得过滤收益；0959历史对照也未显示分数改善。P13含人工标题的高分不能作为纯机器方案已验证的证据，P14不复制该人工标题。

若下一轮继续，最小方向是先验证机器可回查的真实章节边界/标题关联，保留来源映射，再做独立冻结正负例；不要先把所有Chunk加长或降低全局阈值。引用携带邻居来源属于未来接入前置验证。计量meta.tokens契约单独处理，历史UNKNOWN继续保留。上述均为建议，本轮未实施。

未验证：完整生成/引用质量、官方检索指标、多样本泛化、生产时延、供应商重复方差。到4次上限即停止，无Push/部署/合并/Tag，不自动开始优化。
