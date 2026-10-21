# P17 — Cheap 漏引用最小真实定位

## 1. 结论

**P17_COMPLETE。** 两个提前冻结的P9 MIRACL历史漏引用问题完成真实FastAPI/PG/pgvector/原SiliconFlow链路。取得完整协议级解码前后对应证据：第一题合法引用正常转换；第二题再次漏引，确认本次是**模型没有生成引用**，不是引用格式错误或Decoder丢失。未确认生产代码Bug，因此没有修改Decoder或自动补引。

接手基线`cb7e1817b1350ca8f550b890196358fdc710768f`；真实执行源码`cc0877ec9a65b4aa802768817c1288df66e9958e`。只增加P17实验观察器、有限围栏CLI及测试，不改生产代码、Prompt、阈值、路由或解析器，无依赖/迁移。567个生产文件哈希不变。

## 2. 实验与边界

提前冻结692002#0与704170#0，知识库`214b8b2f-84f9-4d75-ab79-eccbdcd50ec2`，tenant332、原subject；新session/request_id，`knowledge_base_scope=[KB]`、`document_scope=[]`、reasoning_effort=off。复用原索引/原件，没有上传、Worker、OCR、重建语料或直接文档加载。固定CHEAP_ONLY/原fixed fallback，模型仍BAAI/bge-m3、BAAI/bge-reranker-v2-m3、XingChenAGI/Xing4.0-29B。

每题实际回答请求的system prompt与该题P9真实请求逐字相等；正常Query Rewrite/向量检索仍会运行，未人为固定其模型输出或换候选。Context/prompt SHA、候选与实际provider请求保留，因此不能把新答案当历史答案重放。

实验观察器被动记录：

1. HTTP响应的原始SSE字节：逐块记录后原样yield，不预读、不二次消费；Rerank JSON原样记录。仅本次授权公开样本链路，不保存headers/密钥；每响应上限2MiB，超限会明确标不完整，实际未超限。
2. Provider adapter原事件：content/type/done/finish逐项记录后原样yield。
3. 真实CitationStreamDecoder：registry snapshot及每次feed/flush输入、输出、缓冲、used/invalid句柄，返回值来自原实现；隔离ContextVar按attempt关联，退出恢复符号。
4. coordinator最终输出、应用SSE、持久化答案、引用及chunk回读。

观察器共享且持久化回答intent，第三次或重复回答发送在正常transport之前拒绝。实际恰好两个回答，各一个attempt，没有重试或选优。

## 3. 原始与最终输出的差异

| QID | 原始回答 | Decoder后 | 持久化引用 | 本次判断 |
|---|---|---|---:|---|
| 692002#0：美国有哪些经济学专家？ | 364字符，含合法ref c1 | 480字符，ref扩展为真实kb标签 | 1 | 历史漏引未复现，合法引用保留 |
| 704170#0：醇类化合物有哪些特征？ | 249字符，没有引用标记 | 249字符，与输入逐字一致 | 0 | 模型未生成引用 |

第一题195次answer feed中，合法ref分为`<`、`ref`、` id`、`="`、`c`、`1`、`"/>`等片段；Decoder正确缓冲并最终展开。used_handles=[c1]、invalid_count=0，对应真实chunk`43c6d4c8-3234-4071-848a-1fc0510fda58`及正确KB。引用通过正常chunk HTTP回读，未自动新增来源。身份可回读不等于整篇答案的主张均获得语义支持，本轮不做该质量宣称。

第二题全部158次answer feed输入与输出相同，没有pending、used或invalid句柄。供应商原始delta没有ref、错误角括号引用、裸cN或Markdown链接；适配器没有删标记，Decoder没有引用可丢弃，最终答案完全相同。**本次可归因于模型未遵循已启用的引用协议**，不以“根据参考资料”自然语言冒充真实引用。没有因此修改Prompt，也没有给可能缺乏语义支持的主张自动补引。

两题原始delta序列（逐个非空片段）= adapter answer序列 = Decoder feed输入序列；Decoder feed/flush拼接=attempt_result=持久化答案。finish_reason均stop，非length截断。完整文本、逐项记录与哈希见同名JSON及diagnostics/。

### 原始流完整性说明

两份body均保留finish_reason、usage及最终`data: [DONE]`，seen_bytes=saved_bytes=文件大小，哈希一致。原日志complete=false指底层HTTP迭代没有被耗尽：生产SSE parser在[DONE]即返回，所以观察器未观察network EOF。**不篡改该标记、不声称观察到网络EOF**。离线验证明确检查协议终止[DONE]及所有终止前content/finish/usage，足够逐项对应解码前后，不将标记false隐藏成true。

这次第二题的真实归因不能倒推P9四次历史失败：旧供应商解码前正文仍缺失；历史原因继续未决，历史结果未改。

## 4. P16计量的顺带真实验证

两次正常RAG各产生一次Rerank，原meta.tokens分别3404和3430输入Token、输出0，正常P1SendGate均结算为settled，usage_facts语义为siliconflow-rerank-tokens/v1，合计**6834 Token**。没有为计量增加单独请求，没有把billed_units当Token补缺。

全部链路**10次供应商请求**：2次最终回答、2次原流程Query Rewrite、4次Embedding、2次Rerank；正式问答为2次。

| 范围 | Token |
|---|---:|
| 最终回答1：1265输入+195输出 | 1460 |
| 最终回答2：1205输入+160输出 | 1365 |
| 两次Rerank | 6834 |
| 两次Rewrite | 5090 |
| 四次Embedding | 44 |
| **总计：14393输入+400输出** | **14793** |

10条新记录全部settled，无新UNKNOWN/reserved。原618条记录、61条UNKNOWN、DeepSeek调用与月账本逐值不变。没有重结算/释放旧UNKNOWN。

有限增量200000经原账户/月advisory lock/CAS流程打开：1453502→1653502；完成后关闭至1468295，headroom0，未使用185207撤销。本轮没有未来可继续消耗的残余额度，也不估算人民币。

## 5. 实际命令与验收

工作树E:/RAG-Worktrees/weknora-python-port-20261013。Python原venv，PYTHONPATH含仓库/E:/WK/deps/reader/runtime-deps；新产物E:/WK/run/p17-20261016，TEMP/TMP在其tmp/。

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m pytest tests/weknora_port/test_p17_observer.py tests/weknora_port/test_p17_fence.py -q --tb=short -p no:cacheprovider --basetemp E:/WK/run/p17-20261016/unit-final
```

退出码 **0**；日志 `E:\WK\run\p17-20261016\unit-final.log`。

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p17-20261016/prepare.py
```

退出码 **0**；日志 `E:\WK\run\p17-20261016\prepare.log`。

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m scripts.weknora_port_p17_fence open --approval-file E:/WK/run/p17-20261016/owner-approval.txt
```

退出码 **0**；日志 `E:\WK\run\p17-20261016\fence-open.log`。

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p17-20261016/run.py start
```

退出码 **0**；日志 `E:\WK\run\p17-20261016\start.log`。

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p17-20261016/run.py ready
```

退出码 **0**；日志 `E:\WK\run\p17-20261016\ready.log`。

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p17-20261016/run.py query --case 1
```

退出码 **0**；日志 `E:\WK\run\p17-20261016\query1.log`。

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p17-20261016/run.py query --case 2
```

退出码 **0**；日志 `E:\WK\run\p17-20261016\query2.log`。

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m scripts.weknora_port_p17_fence close
```

退出码 **0**；日志 `E:\WK\run\p17-20261016\fence-close.log`。

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p17-20261016/closeout.py
```

退出码 **1**；日志 `E:\WK\run\p17-20261016\closeout.log`。

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p17-20261016/closeout-v2.py
```

退出码 **0**；日志 `E:\WK\run\p17-20261016\closeout-v2.log`。

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p17-20261016/analyze.py
```

退出码 **0**；日志 `E:\WK\run\p17-20261016\analyze.log`。

离线37项（2.06s）通过，包括字节无预读/原样yield、记录上限不改变流、规范/无标记/伪造标记各分片对比、实际coordinator关联、重复/第三次发送拒绝、围栏29项。首次小集35pass保留；增加实际coordinator/cap反例后37pass。两次真实collector、分析、最终对账exit0；**未实现的命令不得报告通过。**

### 环境与保护

PG127.0.0.1:56423，weknora_python_port_20261013，system identifier7696088403504697382；Redis56424/15，run_id87b4c2a9f76f9f94b34b5f21fe31a967926f343a，P17唯一前缀。调用前核对租户、KB cloud_allowed、文档版本、权威预算余额与固定模型允许清单；API /readyz正常configured，不关闭校验。

KB/文档/chunks/完整embeddings前后相同；2247份既有证据及567个生产文件哈希验证不变。新session/消息/调用账本来自本次正常入口，保留用于回查。独立只读复核确认两题字节/Decoder/持久化对应，不把模拟结果当真实证据。

保留的设置/审计异常：

- 子代理开始误在原E:/RAG quention新建一个任务脚本；核对确为本任务新建未跟踪文件后，复制到P17证据并比对哈希，再仅移除该文件，未触碰原有改动。
- 初次清理命令因新证据目录尚未创建而停止，未删除文件；创建目录后完成上述精确回收。
- 停进程首轮把本任务conhost也纳入模块名校验而停止，未误杀；后续仅对命令行及父子关系匹配的两个Python进程执行停止。P17端口56429已无监听，共享PG/Redis继续运行。实际worker PID2964及venv启动器27656身份记录在owned-processes-before-stop.json；无Worker/浏览器/容器启动。
- 首次closeout exit1发现P16旧manifest把正在写的deliver-v2.log记录为空文件hash；该日志实际完成时间早于P17。保留旧manifest、日志和失败，独立记录该既有例外后完成对账，不重写历史证据。其余151个直接绑定文件及额外2247份历史保护检查通过。

## 6. 交付与停止

实际生产修复：**无**。没有确认Decoder缺陷，不通过补引用掩盖模型协议问题。保留观察器/围栏/定向测试用于复核；不自动部署或接入普通运行日志。主要剩余问题为Cheap引用协议遵循不稳定，及历史P9缺失解码前证据；没有验证所有答案事实正确或引用语义完整。

已达到2次上限并停止；无追加请求、Prompt/阈值/路由优化、OCR/0434实验，无Push/部署/合并/Tag。原.serena及六个未跟踪文件保留。后续优化需要另起任务，本轮不自动执行。
