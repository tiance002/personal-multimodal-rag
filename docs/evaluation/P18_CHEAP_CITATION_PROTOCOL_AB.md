# P18 — Cheap引用协议稳定性真实A/B

## 1. 结论

**P18_COMPLETE。** 两道冻结P17问题各完成A/B一次，共4次真实Cheap生成。A、B引用生成率均 **0/2**；当前增强协议未显示收益。引用合法性和引用语义支持的分母均为0，记 **N/A**，不能报100%合法。

Q1两组主要事实均与冻结材料一致；Q2两组均传播固定来源中已预先发现的错误化学定义。B还添加了来源外陈述。本轮不建议将当前B接入生产；结论限于2个选定问题、每臂各1次，不能推断总体效果。

源码基线`92b059fc412c7d4a0abd86a14278991e9763bba8`，实际执行`c089cbe0813ed898618a966705043348ef42d7a8`。只新增P18实验脚本/围栏/冻结夹具与测试，不修改生产默认Prompt、Decoder、检索、路由或阈值；567个生产文件哈希不变。

## 2. 冻结输入与唯一变量

本轮是**复用既有RAG Context的真实生成阶段A/B**。直接复用P17每题真实provider请求body、Context和c1句柄映射，正常模型注册表/权限/Token Authority/P1SendGate执行。无上传、重建索引、检索、Embedding、Rerank、Rewrite或评测模型调用。

- Q1 MIRACL692002#0：美国有哪些经济学专家？
- Q2 MIRACL704170#0：醇类化合物有哪些特征？
- 固定SiliconFlow XingChenAGI/Xing4.0-29B、temperature=.3、max_tokens=1024、thinking=false、stream=true/include_usage；seed维持原请求未指定。
- A请求body与P17原始请求逐字canonical-wire一致。
- B只在system既有引用协议与Resource协议之间追加下述内容。user消息（证据、来源ID、问题和日期）完全相同，其余system及生成参数相同；未加入Gold、评审意见或IUPAC纠错内容。
- 顺序提前冻结Q1-A、Q1-B、Q2-A、Q2-B，每个一次，不按结果重试/选优。28,830 Token保守输入字节+输出预留总上界，真实消耗另计。
- 每次发送前核对tenant332、KB214b8b2f-84f9-4d75-ab79-eccbdcd50ec2许可、document/version、两个原chunk内容绑定；实际httpx wire与冻结hash一致。SourceRegistry按P17快照恢复当前合法c1。

B追加协议全文：

```text

- Citation output check: match each source-based factual sentence or list item to the current evidence before finishing. Place a legal <ref id="cN"/> or <ref id="wN"/> immediately beside the claim it actually supports, using only a current citable handle.
- A citation must support the whole adjacent claim. Split or qualify a mixed claim; do not use one source citation to imply support for unsupported additions. Preserve the user's exact-format restrictions above.
- If the supplied evidence does not support a claim, omit or explicitly qualify it without a fabricated citation. If no supplied evidence answers the question, state the evidence limitation without inventing a source. Do not output public <kb>/<web> tags or bare private handles.
```

这项增强要求事实与来源对应、相邻合法ref、混合主张拆分/限定、无证据不引用，并保留用户精确格式约束。生产默认协议保持原样。

## 3. 四次真实结果

| 条件 | finish | 生成引用 | 非法句柄 | 引用合法性/语义支持 | 秒 |
|---|---|---:|---:|---|---:|
| Q1-A | stop | 0 | 0 | N/A（无引用） | 5.470 |
| Q1-B | stop | 0 | 0 | N/A（无引用） | 6.864 |
| Q2-A | stop | 0 | 0 | N/A（无引用） | 4.200 |
| Q2-B | stop | 0 | 0 | N/A（无引用） | 3.441 |

四次原始SSE delta、Provider adapter、CitationStreamDecoder输入/输出逐项相等；没有可丢失的合法引用，没有模型伪造public kb/web标签，没有length截断。协议终止[DONE]、finish_reason、usage均保存；延用P17原始流观察器逐块记录，不预读/二次消费。底层network EOF未观察到（parser在[DONE]返回）如实保留，协议级完整性独立校验通过。

本轮A为0/2，而P17同两题曾为1/2，说明单次输出存在波动；P17作为历史对照单列，不并入本轮A分母。4个独立raw body、registry、真实request、adapter/decoder事件及全文答案见trials/、raw/、diagnostics/与results.json。

## 4. 语义与答案质量评价

预先冻结评价rubric，再生成结果；采用离线逐题来源审查与独立复核，无额外模型judge，没有官方QA Gold或官方准确率分数。

### Q1：核心事实匹配，A/B大致持平

冻结资料给出五位芝加哥经济学派代表、截至2019年的33位/世界首位叙述、斯蒂格勒1982年及商学院教授首次获奖信息。A/B均保留这些核心材料并限定为资料中代表人物，避免宣称覆盖全美国。

A的“其中最著名”是来源没有提供的比较判断；两组英文姓名拼写也不是冻结材料中的原文。主要事实与来源相符，但本轮没有独立重核“33位、世界首位”的统计口径，不能将其称为全答案独立事实验证通过。B未显示引用输出改善。

### Q2：来源支持不等于事实正确

冻结材料本身写“孤电子对，即没有成对的电子”，该问题在调用前已记录在rubric中，原Context未改。[IUPAC Gold Book lone pair](https://goldbook.iupac.org/terms/view/L03618)将其定义为原子价层上的成对非键合电子；来源将其解释为未成对电子有误。网页定义已通过公开官方来源核对；plain文本直接下载失败，不声称保留了成功下载快照。

- A复述错误的“孤电子对（即没有成对的电子）”，同时保留弱碱性描述；另将“最核心结构特征”强化为来源未给出的判断。结尾有资料范围限制，举出其他常见性质仍属来源外知识。
- B进一步明说“具有未成对的电子”，同样保留该事实错误；增加羟基结构及因果解释，完整陈述不受冻结Context支持，也没有A的资料覆盖限制说明。

因此，两组对这道题都存在**有来源支持但事实错误**；B没有修复来源错误，并增加证据外内容。既不将错误原文匹配当作答案正确，也不为这些内容自动加引用。没有真实生成引用，引用与主张的语义支持精度为N/A；有来源可支持的事实仍缺乏引用，覆盖问题保留。

## 5. Token与时延

| 方案（2题） | 输入Token | 输出Token | 合计 | 有引用答案 | 已知事实错误答案 |
|---|---:|---:|---:|---:|---:|
| A | 2470 | 376 | 2846 | 0/2 | 1/2（已知源错） |
| B | 2782 | 321 | 3103 | 0/2 | 1/2（已知源错） |

B相对A输入+312、输出−55、合计 **+257 Token（约+9.0%）**。两题总量差分别+128、+129；引用率变化均为0。输出长短也有随机性，不能把本批差值当作稳定成本或速度估计。

本轮共 **5949 Token：5252输入+697输出**，4个新请求全部settled，新增UNKNOWN0/reserved0。原628条账本记录、61条UNKNOWN及DeepSeek账本逐值不变；没有历史结算/释放。

有限增量100000，经同账户/月lock/CAS打开1468295→1568295，完成后关闭至1474244，headroom0，未使用94051撤销。无其他模型请求，无人民币估算。

## 6. 实际命令与证据

运行目录E:/RAG-Worktrees/weknora-python-port-20261013；PYTHONPATH为仓库、E:/WK/deps/reader、var/weknora-port/runtime-deps，所有新日志/临时产物在E:/WK/run/p18-20261016。

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m pytest tests/weknora_port/test_p18_protocol_ab.py tests/weknora_port/test_p18_fence.py tests/weknora_port/test_p17_observer.py -q --tb=short -p no:cacheprovider --basetemp E:/WK/run/p18-20261016/unit-final
```

退出码 **0**；日志 `E:\WK\run\p18-20261016\unit-final.log`。

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p18-20261016/prepare.py
```

退出码 **0**；日志 `E:\WK\run\p18-20261016\prepare.log`。

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m scripts.weknora_port_p18_fence open --approval-file E:/WK/run/p18-20261016/owner-approval.txt
```

退出码 **0**；日志 `E:\WK\run\p18-20261016\fence-open.log`。

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m scripts.weknora_port_p18_protocol_ab --frozen E:/WK/run/p18-20261016/frozen.json
```

退出码 **0**；日志 `E:\WK\run\p18-20261016\real-ab.log`。

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m scripts.weknora_port_p18_fence close
```

退出码 **0**；日志 `E:\WK\run\p18-20261016\fence-close.log`。

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p18-20261016/closeout.py
```

退出码 **0**；日志 `E:\WK\run\p18-20261016\closeout.log`。

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p18-20261016/analyze.py
```

退出码 **0**；日志 `E:\WK\run\p18-20261016\analyze.log`。

最终47项离线定向检查2.22s通过：唯一变量、A精确原请求、Context/参数突变拒绝、句柄身份、重复/不确定intent不重发、原始流保真和围栏。首次测试8项仅因测试预期RuntimeError与共享guard实际ValueError不符而失败，39项通过；修正新测试异常类型并把冻结夹具移入仓库，未修改旧安全断言/实验输入，首失败unit.log保留。validate-only实际执行输出P18_FROZEN4_VALID。正式4次、围栏open/close、对账/分析均exit0。**未实现的命令不得报告通过。**

PG127.0.0.1:56423，weknora_python_port_20261013，system id7696088403504697382；Redis56424/15，run_id87b4c2a9f76f9f94b34b5f21fe31a967926f343a，P18独立prefix。未启动API/Worker/浏览器/容器；普通runtime/Registry加载已批准模型，密钥只经正常配置加载，不输出正文，不保存headers。

115个P17既有证据文件及567个生产文件哈希不变；KB、原文版本、chunks、完整embeddings前后逐值相同。原.serena及六个既有未跟踪文件保留。无迁移/依赖/共享生产接口改动。

## 7. 是否继续接入

**不建议接入当前B方案。** 本批没有引用率收益，追加协议增加输入Token；Q2也揭示纠正源文与引用格式是不同的评价维度。若以后继续，需要独立冻结样本与来源质量检查后再决定，当前2题单次结果不支持总体有效性判断。本轮不改生产默认Prompt，不实施自动补引、不调整模型/Decoder/检索/路由，不启动下一阶段。

达到4次上限即停止，完成后未Push、部署、合并或打Tag。
