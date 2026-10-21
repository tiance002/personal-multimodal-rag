# Qwen3.5-4B 单次正常 RAG：最终生成前次数不足

2026-10-18。最终状态 **`BLOCKED_PROVIDER_CALL_LIMIT_BEFORE_QWEN`**；候选仍为 **`CANDIDATE_NOT_VALIDATED`**。本次没有取得 `QWEN_SINGLE_VALID_SAMPLE_PASS`，也不是 Qwen 生成质量失败。

Owner 本轮明确批准1次完整问题、最多6次 Provider／100,000 Token。实际仅提交1次，通过原正式 FastAPI，不注入 Chunk、Gold、租户或测试 runtime。执行器退出0表示保存了终态证据，不能解释为问答验收通过。没有补发或扩大授权。

## 分层验收

| 层级 | 结果 | 实测证据 |
|---|---|---|
| 身份与来源 | PASS，只读核对 | tenant1；原M3.5 KB `9d3461a5-080c-4b5e-a367-c82b79d3013c`；文档 `996fda57-6cc2-4045-a3ff-7416f037459a`；版本 `91f260da-03e3-422e-ae08-b6f818ee3b7b`；既有1024维向量和来源保持 |
| Query Understanding | PASS，真实 | SiliconFlow `XingChenAGI/Xing4.0-29B`；intent=`kb_search`；改写为“课件中线性方程组的系数矩阵是什么，以及增广矩阵最后一列的三个数分别是什么” |
| Search | PASS，真实 | 2个候选，原Summary和完整MinerU正文；`document_scope=[]`，只限当前KB |
| Rerank／MMR | PASS，真实 | 原始Summary分数0.966791809，正文0.346931726；均通过原0.3阈值，MMR均保留；没有改阈值或Top-K |
| Merge／Context | PASS，真实 | 2块、794字符；正文Chunk `96ad695f-bb32-4df9-9cfb-1e8281f2fc31` 的434字符完整LaTeX及真实版本被保留 |
| Qwen发送／答案 | **BLOCKED／NOT RUN** | 准备好的Qwen请求含完整正文，`max_tokens=1024, enable_thinking=false`；在第7次Provider准入前被拒绝，无Qwen传输、回执、首Token或答案 |
| DYNAMIC／升级／fallback | 部分执行 | DYNAMIC选择Cheap；生成在发送前失败，没有Expensive调用，没有SearchNothing模型fallback；不属于模型超时或length截断 |
| SSE／历史 | PASS，错误终态一致 | SSE error和complete均为`LOCAL_EXECUTION_LIMIT_DENIED`；turn和assistant历史均error、答案为空，未伪装completed |
| 耗时／Token | 实测 | 整次HTTP至错误终态213.782899秒；6次实际Provider共2,999输入＋37输出＝3,036 Token；新增Provider UNKNOWN=0 |

原问题保持：“请写出课件中线性方程组的系数矩阵，以及增广矩阵最后一列的三个数。”本次只能证明正确资料保留到准备生成阶段；**Qwen没有收到请求，答案正确性未验证**。准备正文中的`&amp;`是既有证据渲染的HTML转义，解码后与原MinerU正文逐字一致；未更改原文。

## 首次失败及执行规划问题

正常流程实际用了：Xing理解1次＋主查询及正常扩展Embedding 4次＋Rerank 1次＝6次。Qwen至少需要第7次，已超出本轮明确授权。因此现有M3.6 Gate在实际发送前拒绝。此次调用数量预估不足属于执行规划问题，不归因于Qwen、检索或用户授权；没有关闭Expansion、移用旧额度或继续发送。

Xing从账本准入至结算为209.709354秒，包含网络和供应商响应等待；并非Qwen耗时。4次Embedding实报30／47／17／15 Token，Rerank354，Xing2,536输入＋37输出。没有HTTP/TLS分段或QwenTTFT证据，不能推断这些时间。

本轮run=`b3ad50a2-411f-49a7-80e1-476a8035fa6f`，session=`0c925faf-7939-47a2-ae86-74ff3b707ed4`。6个Provider调用及执行占用均已SETTLED，围栏CLOSED，剩余96,964 Token授权空间撤回，未重发。Provider表从30行增加到36行，没有Qwen行。

**UNKNOWN语义区分：** 新增Provider UNKNOWN为0；旧第四题UNKNOWN整行指纹、usage=NULL、finished_at=NULL均保持。发送前失败的generation_attempt仍沿用通用`usage.status=UNKNOWN / cost_status=UNKNOWN`空值标记，但没有Provider发送记录、执行预留或未知外发；不能把这个空值标记当成新的供应商UNKNOWN或虚构usage=0。

## 必要局部修复及测试

首次预检发现旧合同将SiliconFlow `max_tokens`误当含思考的全输出上界，先以0次发送拒绝。该检查点和原报告完整保留于JSON及证据ZIP。随后复用正常接口`reasoning_effort=off`，仅本条请求显式关闭思考；生产默认配置未改。

实现提交 **`abcac6963aa9df7d03cc093dcbff55c24a616ddc`**，本次实际运行源码同此SHA：

- `backend/weknora_port/models/output_contract.py`：将SiliconFlow合同改为不含思考的可见输出上限；受限发送必须实际带布尔`enable_thinking=false`，否则发送前拒绝。未猜测通用thinking_budget硬上界。
- `tests/weknora_port/test_output_budget_contract.py`、`test_execution_fence.py`：覆盖缺失／开启／伪布尔值拒绝及显式关闭；验证拒绝时底层传输0次。保留原synthetic split-cap的250-token预留断言，新增独立50-token非思考反例。

供应商依据：[Chat API](https://docs.siliconflow.cn/docs/api/chat-completions-post)、[Reasoning说明](https://docs.siliconflow.cn/docs/userguide/capabilities/reasoning)。这修正了受限模式的安全合同，没有修改普通Prompt、模型、Rerank、MinerU或DYNAMIC。没有完成Qwen服务端响应验证，不能以预备请求冒充供应商已遵守参数。

实际命令（工作目录均为项目worktree）：

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest --noconftest -q -p no:cacheprovider --tb=short tests/weknora_port/test_output_budget_contract.py tests/weknora_port/test_execution_fence.py
& 'E:\RAG quention\.venv\Scripts\python.exe' -B 'E:\WK\run\qwen-single-rag-20261018\run_single.py'
& 'E:\RAG quention\.venv\Scripts\python.exe' -B 'E:\WK\run\qwen-single-rag-20261018\finalize_readonly.py'
git diff --check
```

前三条最终均退出0；pytest为**83 passed**。首轮有1个旧observer夹具因未显式关闭思考失败，补齐夹具后通过。`finalize_readonly.py`仅离线核对实际回执、候选、正文、终态及10个原有未提交路径哈希。`git diff --check`结果见机器结果最终核对。未运行全仓测试、浏览器或第二次问答。

## 保留项与最小后续

旧UNKNOWN、tenant332模型与配置、原Chunk／向量和日常`local.json`哈希未变；持久配置仍cloud=false、无execution_file。仅启动的API已在终态后停止，没有启动Worker、上传、重建索引或修改其他会话文件。

历史读取的user消息仍有`turn_status=pending`字段，assistant和turn已明确error；作为字段一致性遗留记录，没有扩大修复。本轮不能推广默认Cheap，也没有Qwen有效性能样本。

如Owner仍需完成正例验证，需要**新的明确授权**覆盖新的问题提交和完整正常调用数量，至少包括本次已观察到的7次，以及需要保留的受控升级余量；不在本轮自动执行。后续不能沿用已关闭围栏或假设新增请求仍只有同样的Expansion数量。

证据：[机器结果](QWEN_SINGLE_RAG_VALIDATION.results.json)、[脱敏原始请求／回执／SSE／历史及初始失败报告](evidence/qwen-single-rag-20261018/evidence.zip)。本机完整目录`E:\WK\run\qwen-single-rag-20261018`。最终报告提交SHA以Git日志为准；本报告绑定的真实运行实现SHA已在上文完整列出。不Push、部署或进行第二轮。
