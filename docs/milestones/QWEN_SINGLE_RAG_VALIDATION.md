# Qwen3.5-4B 单次真实 RAG 验证：发送前拒绝

日期：2026-10-18。状态 **`BLOCKED_OUTPUT_BOUND_UNVERIFIABLE`**，不是模型质量失败。基线 `d0c57389f602739780d096ccd9f2c0461de1320f`。本轮 Owner 已明确批准1次完整问题、最多6次 Provider / 100,000 Token，以及原 DYNAMIC 可能调用的既有 DeepSeek Expensive；没有复用旧许可。

按照任务“不能证明输出上界时发送前拒绝”的要求，本轮 **问题提交0次、Provider 0次、Token 0、新UNKNOWN 0**。没有启动API/Worker、没有开启执行围栏，也没有消耗这次单题提交机会；已批准的限额未激活，不作为账户余额或以后自动额度。

| 分层验收项 | 结果 | 实际证据 |
|---|---|---|
| 源码及身份 | PASS，只读 | HEAD如上；PG system `7696088403504697382`、`weknora_python_port_20261013`；日常tenant 1，未改变principal；Redis实例与前轮实际run_id相同 |
| KB/来源/索引 | PASS，只读 | KB `9d3461a5-080c-4b5e-a367-c82b79d3013c`；文档 `996fda57-6cc2-4045-a3ff-7416f037459a`；版本 `91f260da-03e3-422e-ae08-b6f818ee3b7b`；原件SHA实读一致；已有2条非空、dimension>0的向量记录 |
| 配置模型身份 | PASS，配置层 | tenant1 revision20，Cheap=`SiliconFlow Qwen/Qwen3.5-4B`，Expensive=`DeepSeek deepseek-flash`；KB的查询基线仍为原Xing模型；本轮无供应商模型回报 |
| thinking与输出上界 | **BLOCKED，首次失败** | 现有正常请求序列化为`max_tokens=1024`，不含`enable_thinking`或`thinking_budget`；当前供应商文档与本地全输出上界合同冲突 |
| Query → Search → Rerank → Context | NOT RUN | 在提交问题前拒绝，无改写、候选、原始分数或新Context可报告 |
| Qwen答案/正确性 | NOT RUN | 没有实际答案，不能标记`QWEN_SINGLE_VALID_SAMPLE_PASS`，保持`CANDIDATE_NOT_VALIDATED` |
| SSE/历史/耗时/Token | NOT RUN / 0调用 | 没有新会话或消息，无模型耗时；本轮实际Token为0，因为没有发送，不代表旧UNKNOWN免费 |
| 旧UNKNOWN/tenant332/历史索引 | PASS，未改变 | 第四题原UNKNOWN整行指纹、usage=NULL、finished_at=NULL保持；tenant332模型/配置、目标Chunk和向量行哈希前后相同 |

## 阻断的确切原因

现有 `backend/weknora_port/models/output_contract.py:48` 将 SiliconFlow OpenAI completions 记为 `full-completion/v1`，`resolve_output_contract()` 因而把 `max_tokens=1024` 解释为包含思考的1024输出上界。纯离线调用已经确认当前代码会作此解释。

但本轮核对的 [SiliconFlow官方Chat API](https://docs.siliconflow.cn/docs/api/chat-completions-post) 明确描述 `max_tokens` **不包含思维链**；另有 `thinking_budget` 参数。其[推理模型说明](https://docs.siliconflow.cn/docs/userguide/capabilities/reasoning)也区分思考与最终回复，并提示部分模型在达到思考预算后仍可能继续思考。因此，不能仅用这张本地合同表证明当前Qwen请求的完整输出上界，也不能因过去几次实际输出较短就推定硬上界有效。

当前Qwen请求既未显式关闭思考，也没有显式思考预算。本轮未取得 `Qwen/Qwen3.5-4B` 在当前供应商下、对这些控制字段的额外硬上界合同；不把通用文档中的“大多数模型适用”视为该具体部署已验证。没有自动添加参数、关闭思考、更换模型或借付费探针测试能力。

**更正前轮结论的范围：** 前轮恢复的请求正文、Token和哈希匹配仍然成立；但“代码合同算出1024”不足以证明“供应商全部输出受1024限制”。历史结果和UNKNOWN没有改写，本轮报告单独保留该新发现。

M3.6被动观察功能本身已具备所需能力：`LocalExecution.observe_pipeline`保存Rewrite、有序候选和最终Context；`observe_model`保存脱敏请求和原始响应，Rerank原始分数可由该响应取得。此次缺口是上界语义，未为补采证据修改生产代码，也未先消耗Query Understanding后才发现无法安全调用Qwen。

## 命令、用量和下一步

实际命令：

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -B 'E:\WK\run\qwen-single-rag-20261018\preflight.py'
git diff --check
```

两者最终均退出 **0**。preflight退出0表示成功发现并执行发送前拒绝，绝非真实RAG通过。数据库操作均为显式只读事务并回滚；检查前后Provider数量、旧UNKNOWN、tenant332模型/配置、原Chunk/向量及日常local.json一致。原有10个未提交路径保留。

证据：`E:\WK\run\qwen-single-rag-20261018\preflight.json`；[机器结果](QWEN_SINGLE_RAG_VALIDATION.results.json)。没有云模型、浏览器或全项目测试；无生产代码修改，不Push、部署或启动第二轮。

下一步最小工作是纠正SiliconFlow受限发送的输出合同，并为Qwen使用有证据支持的显式思考控制/硬上界，定向验证发送前门禁。完成这项前不得称当前受限模式已证明完整Token上界；不是修改Rerank、Prompt或动态路由。然后才具备按现有授权范围恢复该单次原问题的条件。本轮不擅自扩展为全局计量改造，也不对模型质量作未经实测的结论。
