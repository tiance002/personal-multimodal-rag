# Qwen3.5-4B 四次真实问答只读诊断

日期：2026-10-18。结论：**CANDIDATE_NOT_VALIDATED**。本轮新增 Provider / Token / 数据库写入均为 **0**，未修改模型或业务配置。源码基线 `0789a8631d6f1f46628490c1eb238ad9283da551`；交付提交 SHA 由 Git 本文件所在提交确定，最终答复给出完整值。机器结果见 [results JSON](QWEN35_4B_EVIDENCE_DIAGNOSIS.results.json)。

## 可信身份与证据恢复

- PostgreSQL：`127.0.0.1:56423`，`weknora_python_port_20261013`，实际 system ID `7696088403504697382`。所有数据库检查显式 `BEGIN TRANSACTION READ ONLY` 后 `ROLLBACK`。
- 四题均为日常 tenant **1**，subject 尾号 `…da8a6`，KB `9d3461a5-080c-4b5e-a367-c82b79d3013c`，`document_scope=[]`；没有借用 tenant 332。
- 该 KB 唯一文档 `996fda57-6cc2-4045-a3ff-7416f037459a`，版本 `91f260da-03e3-422e-ae08-b6f818ee3b7b`。原件 SHA-256 实读匹配 `95a3151c2c9f5f3d9e36392065ecd1e7ab6cf49ae9c591b698cff7196ab78601`。
- MinerU 正文 Chunk `96ad695f-bb32-4df9-9cfb-1e8281f2fc31`（434 字符）实际含完整 `bmatrix`；Summary Chunk `a06e7826-577a-53ca-bd68-f53d8315a160` 是模型生成摘要，不能当作矩阵原文。
- 从 `messages.rendered_content` 恢复了后三题的**实际持久化准备消息正文**，并用原 `prepare_generation()`、请求序列化函数离线重建：三题的 Context / Prompt / Envelope / Wire 共 **12 项哈希比较全部匹配**。这是有正文、来源映射和原函数支持的离线重建，不是从 SHA 猜测正文，也不是新的模型验收。第一题 fallback 的 options envelope 亦匹配，但其完整 fallback prompt 未持久化。
- 因此修正上一轮仅查看结果 JSON 所得的“Context 正文不可核验”结论：后三题的正文现已恢复。Provider 原始 HTTP body/response 文件依旧未保存；重建材料明确标为 `OFFLINE_RECONSTRUCTION_ONLY`。

## 逐题结论

下表编号按实际执行顺序。完整 session、client request、run、Chunk 与用量身份均在结果 JSON。

| 题目 / run 前缀 | Search → Rerank → Merge → Context | 实际准备的证据 | 回答和首次失败位置 |
|---|---|---|---|
| ① 请用一句话概括这份课件的主题。 / `3477a0da` | 2 → 0 → 未执行 → 0 | model fallback，无检索 Context；可使用资料目录描述 | 回答的主题与当前来源相符，但属于 metadata fallback，不能计入正常有证据 RAG 性能样本。首次 `SearchNothing` 定位在 Rerank 包装阶段。 |
| ② 课件中线性方程组的系数矩阵是什么？ / `6cb5e421` | 2 → 1 → 1 → 1 | 仅 Summary，Context 333 字符；完整矩阵未进入生成消息 | 给出构造方法并说明缺少具体示例，没有解答课件中的具体矩阵。属于最终证据不足；不能据此判定 Qwen 不会读取 LaTeX。 |
| ③ 增广矩阵最后一列的三个数分别是什么？ / `76dbe604` | 2 → 1 → 1 → 1 | 与②相同的 Summary；没有三个数 | 明确说明材料不足，没有编造数值；行为合理，但未完成原问题。回答还残留内部 `d1` 文本，独立记录为展示问题。 |
| ④ 课件说明矩阵方法如何用于求解线性方程组？ / `34448ac6` | 2 → 2 → 2 → 2 | Summary＋完整 MinerU 正文，Context 794 字符 | 请求准备正确；本地执行器在最终生成过程中中断，没有最终回答，质量不可评分。 |

正文可直接回读到系数矩阵 `[[1,-2,1],[0,2,-8],[-4,5,9]]`，增广末列 `[0,8,-9]`；这些只用于本轮事后核对，没有注入任何模型或索引。②③的准备消息明确没有这些 LaTeX/数字。摘要内容和已有回答中的定义说明相符，但不构成具体数值提取成功。

**没有一条已完成的“所需原文送达＋正确回答来源事实”的 Qwen 数值问答，当前有效性能样本为 0。** ④虽然具备原文，执行中断使其不能补足这一条件。

## 可确认的边界与历史缺口

`service.py:311` 捕获 `SearchNothing` 后创建空 `rendered_contexts` 的 fallback，再设置 `is_fallback=True`（`:330`、`:340`）。①真实事件为 Search completed/count=2 → Rerank started → generation started；没有 Rerank completed、Merge 或 Context，结合 `rerank.py:150` 的空集合抛出点，可将首次空结果定位到 Rerank。`is_fallback` 表示这条业务分支，不表示 Expensive 升级或 Provider 重试。实际 Qwen 回执证明①走的是 model fallback，而非固定拒答。`sessions.fallback_strategy` 的旧默认 `fixed` 字段不能代替 `runtime.py:500` 实际使用的服务装配配置。

四题**实际 Rewrite/原始 intent、Search 有序候选 ID 与分数、Rerank 完整响应和逐候选分数均未保存**。Redis 只保留阶段数量；不得从两个 Embedding 操作名中的查询哈希反推 Rewrite，也不能用当前重新检索结果冒充历史。阈值配置为 **0.3**、Top1 最低 **0.15**、Top-K **30**；不知道候选的真实分数，不能补写“该 Chunk 得分多少”。

②③在最终 Context 中确定丢失原文；Rerank 从 2 降至 1 与此吻合，但历史 Rerank/Merge 输出未保留身份，无法严格证明原文第一次在哪一个内部子步骤被排除。标为“最终 Context 缺失已确认，Rerank 精确丢失机制 UNVERIFIED”，不虚构阈值原因。④两份准备证据的身份和顺序可从持久化正文恢复为 Summary → 原文。

## 模型、思考参数、时间和用量

四次查询理解均有 **SiliconFlow `XingChenAGI/Xing4.0-29B`** 真实回执，最终角色均为 Cheap，请求 **SiliconFlow `Qwen/Qwen3.5-4B`**。前三题模型实际回报身份一致，`finish_reason=stop`、没有 `length`、没有升级 Expensive。④只记录到 Cheap STARTED，不能将未回报的身份当作供应商确认。

②③④精确匹配的重建 wire 都是 `stream=true`、`temperature=0.3`、`max_tokens=1024`、`stream_options.include_usage=true`，**没有** `enable_thinking`/`thinking`/`reasoning_effort` 字段。其请求选项 `thinking=None`，使用供应商默认行为；②③实际 reasoning tokens 为 **141 / 265**，已经包含在 completion tokens 内，不能再相加。① fallback 的 options envelope 确认 `thinking=False`、输出配置 1024，回执 reasoning tokens=0；缺少其完整 wire，具体思考开关字段标为 UNVERIFIED。查询理解调用源码指定 `thinking=False`、temperature 0.3、输出150；实际 Rewrite wire 未保存，保留“代码配置”和“发送字段”区别。

| 题号 | 原脚本整次 elapsed | Xing 准入至结算 | Qwen 准入至结算 | 全流程已报告 Token | Qwen 输入 / 输出 / 合计 |
|---|---:|---:|---:|---:|---:|
| ① | 10.558s | 5.505s | 1.082s | 3,762 | 838 / 28 / 866 |
| ② | 27.987s | 8.321s | 16.374s | 4,251 | 944 / 381 / 1,325 |
| ③ | 27.921s | 12.119s | 13.252s | 4,374 | 945 / 521 / 1,466 |
| ④ | 未完成 | 7.207s | UNKNOWN | 2,939＋未报告部分 | UNKNOWN |

10.558s 是脚本的整次问答计时，不能称作 Qwen 延迟。独立 Redis agent_query→complete 区间①为9.526s；脚本计时边界没有独立代码存档，不再细分剩余开销。Qwen 的完整 generation attempt 区间①②③分别 **1.156 / 16.454 / 13.292s**。上述账本时间包含发送、等待、解码和结算附近的时间，不能单独分出 HTTP 建连、TLS、模型排队或 TTFT。DYNAMIC 缓冲后一次发布完整答案，应用侧 answer 时间也不能充当供应商首 Token 时间。

合计 **20 次 Provider 尝试，19 次 RESPONSE_RECEIVED，1 次 UNKNOWN；已报告输入14,290、输出1,036、合计15,326 Token**。每题正常5次分别是1次理解、2次不同查询的 Embedding、1次 Rerank、1次 Cheap；没有重复 operation 或记录到的超时重试。前三题的 `messages.usage`、`generation_attempts.usage`、`port_provider_calls.usage` 已在数据库持久化，答案与旧结果文件及 Redis SSE 一致。`cost_status=UNKNOWN` 是未计现金费用，不等于 Provider outcome UNKNOWN。

前三题 `port_turns=completed`、`messages.is_completed=true`、SSE complete 一致；旧 `messages.turn_status` 仍为 pending，是另一个元数据不一致，不能据此抹去成功落库事实。④则是 turn error、assistant cancelled、attempt STARTED、Provider UNKNOWN；取消完成没有结算供应商请求。

## 第四题 UNKNOWN

Qwen attempt 于北京时间 **18:24:11.230** 开始，Provider 准入 claim 于 **18:24:11.251** 持久化，至今无 `finished_at`、模型回报、usage 或 finish_reason。重建的完整请求和 wire hash 匹配，但准入 claim 位于实际 HTTP send 前；**供应商是否实际接收，缺独立回执，仍为 UNVERIFIED**。

旧 addendum 及执行记录说明执行器在 in-flight 时手动中断；没有模型超时回执。**18:25:29** 的 `stop(reason=user_requested)` 来自执行器后续通过正常 stop API 清理，不能说是 Owner 主动取消；**18:26:13** 的 complete/cancelled 来自后续生命周期收口。它们都不能替代中断时的供应商结果。原 `three-question-timeout-check.json` 的 `ALL_THREE_COMPLETED` 是只写入前两题时的错误中间字段，已有 addendum 更正，本轮不改历史文件。

该 call 在 `send_calls`、`port_token_calls`、`port_call_slots` 的匹配行均为0；本次执行没有挂载 M3.6 execution file，既有运行 JSON 中未找到对应限额 entry。这里保留的是 durable no-resend claim，**未发现该调用的 Token 预留或本地 STOP 围栏**；不能因此将未知用量当作0，也不能声称受限模式已实测。当前普通模式与受限模式行为不同：新受限发送必须遵守既有 UNKNOWN/STOP 政策，不能借普通模式绕开。

## 选型与下一次最小验证建议

保持 **CANDIDATE_NOT_VALIDATED**。当前数据库已保存 Cheap=Qwen 是 Owner 之前的配置决定，本轮只读，不自动切换模型。①仅证明 fallback 很快；②③有完整调用，但缺少所问原文；④有完整证据，却因执行中断无法评分。现有证据既不支持依据10.6s推广，也不支持宣布 Qwen 的 LaTeX 能力失败。Xing 的5.5～12.1s是明确的延迟组成，本轮不调整它。

若需后续验证，建议仅1次正常 FastAPI 请求：tenant1、现有上述 KB、`document_scope=[]`，原问题“请写出课件中线性方程组的系数矩阵，以及增广矩阵最后一列的三个数。”使用新 request_id、正常检索、现有 DYNAMIC，禁止注入证据。预计5次 Provider，原规则若升级则最多6次，建议有限上界100,000 Token，实际预留若不能容纳则发送前拒绝。先按现有政策处理未决 UNKNOWN 的后续执行许可，再取得本次有效授权；**本轮 NOT RUN，无新增云授权**。应保存实际 Rewrite、候选顺序、原始 Rerank、最终 wire、思考字段及1024输出上界、回执、SSE和用量；正常链路未送达原文时直接记录，不重问选优。

## 命令与交付

以下命令均实际退出 **0**；Python 均为 `E:\RAG quention\.venv\Scripts\python.exe`，使用 `-B`。完整命令、原件/快照SHA在结果JSON。

1. `-B E:\WK\run\qwen-evidence-diagnosis-20261018-collect.py`：只读 PG/Redis，四题、8条消息、20条Provider记录。
2. `-B E:\WK\run\qwen-evidence-diagnosis-20261018\collect-generation.py`：只读 generation runs/attempts及不含凭据的模型元数据。
3. `-B E:\WK\run\qwen-evidence-diagnosis-20261018\reconstruct.py`：从真实持久化准备消息恢复源内容，9项 Context/Prompt/Envelope比较通过。
4. `-B E:\WK\run\qwen-evidence-diagnosis-20261018\verify-wire.py`：3项 wire比较及fallback options比较通过，无发送。
5. `-B E:\WK\run\qwen-evidence-diagnosis-20261018\verify-evidence.py`：原件SHA和上述匹配通过，记录10个原有未提交路径。

最终只读复查首次退出1：采集器脱敏时移除了 Redis `key` 字段，比对工具读取该字段失败；只修正诊断工具，按已核验的 prefix/session/message 组合键，再执行退出0。两次结果均保留在机器结果中，没有数据或模型操作。最终 `git diff --check` 退出0，目标 Provider/消息/attempt 记录未变化，原有10个未提交路径哈希保持不变。生产代码无修改，pytest/云模型/浏览器重测均 **NOT RUN（本轮不需要）**。仅本地提交本报告和结果JSON，未Push、部署、合并或打Tag。原始补充证据位于 `E:\WK\run\qwen-evidence-diagnosis-20261018`。
