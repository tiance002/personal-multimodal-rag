# S8.8 集中真实复验授权申请（尚未批准）

本申请来自本轮执行合同第11节。此前 S8.7、ECS118、R7/R8 等授权不复用。当前新收费 Grant、业务 Provider 尝试、Token 与 UNKNOWN 均为0。

## 冻结身份

- 运行源码：`01ae7cf05767e211224edfa9c0d5e0a3267f6e82`。
- 镜像：`sha256:341647bfd12c76a1cc383d30e94121e2347445f07d46579843369673f73149c6`。
- 归档 SHA256：`524a056fb849e123f1f40f17b260a904b76e3be919b4ff6cfc8ec14bd166d598`。
- Profile SHA256：`0a3fb4be39515d3598179640c65bc7a69822e8892b7b45c0b5ee2fcf143ff384`。
- 私有集中 manifest SHA256：`387b9ffabed3cd6a0a6189749057e9341e61c39305a6cb1478c2fb103f531ef3`。
- 私有 manifest 内包含逐次完整 body、来源范围、body SHA、输出限制和条件；正文不上传 GitHub。旧未批准草稿原样保留，不能据旧61次计划发送。

## 请求范围

| 实验 | 模型 | 最多发送 | 单次输出上限 | 固定条件 |
|---|---|---:|---:|---|
| Prompt 候选：ordinal2/4/13/34，控制39/49 | SiliconFlow `Qwen/Qwen3.5-4B` | 6 | 2048 | 原实际消息/范围/Context/温度/非思考；只改一个 System 内容 |
| 上述新 Cheap 为 WRONG/PARTIAL 的同输入配对 | DeepSeek `deepseek-flash` | 6 | 2048 | 新 Prompt、原证据、同模式；必要 Provider 格式差异记录 |
| 原7张 length 图及2张正常图控制 | DeepSeek `deepseek-flash` vision | 9 | 5000 | 原图、原 Prompt、温度.1、原输出上限，只增加明确 thinking disabled |
| 固定证据 Quick/Smart 模式控制 | DeepSeek `deepseek-flash` | 2 | 2048 | 同一控制题、原 Prompt B/Context；仅思考开关不同 |
| 正式 HTTP Quick/Smart/小额度 | 当前原 P5 绑定 | **0** | — | **BLOCKED，不在本次付费范围内** |

最多 **23次** Provider 准入；没有新的检索、Embedding、Rerank 或 OCR。最多6次匹配 Expensive 已包含，不能另行叠加。正确控制题也在失败时适用一次配对；不以旧 S8.7 的贵模型结果冒充新 Prompt 的配对。

当前 Cheap 缺少可核验的托管输出能力，新显式产品模式会发送前拒绝。六次固定 Prompt 实验沿用真实历史固定输入评测路径与原 wire 选项，不声称它们验证了新 UI 的 Quick/Smart，也不绕过产品能力 guard。正式 HTTP 必须先补齐能力依据，再提交具体可执行范围；本申请不含修改模型绑定或虚构上限。

## Token、费用与停止

23个冻结请求的输入风险上界+有限输出合计 **323,458 Tokens**（包含条件配对）。它是风险计算，不是计量、不代表实际花费。申请新独立 **500,000 actual reported Tokens** 停止线；不复用旧额度。费用 **UNPRICED**，仅记录真实可核验账单，不估算人民币。

严格串行，已有 SendGate、结算、UNKNOWN 与子请求保护保留。reported-only 停止线可能被一个已经准入的在途调用跨过，下一次调用必须拒绝；无并发结算扩张。未知发送、用量不明、权限/版本/SHA/wire不符立即全局 STOP，不自动重试。意外 length/空正文/缺结束信号保留已发生用量并停止相关实验分支；语义错答不反复刷到 PASS，按批准的匹配配对一次执行。

ECS 使用独立临时进程/持久目录，后台脱离本机运行，1CPU/1280MiB及既有I/O隔离；不设整批40分钟上限，单次超时与单题 deadline 保留。Root 为唯一远端操作者。来源当前20份/191Chunk、9个图像资产均只读核验；发送时再次检查身份与有效许可。

在一个已安排的完成调用上核验 Langfuse metadata-only；不上传问题、答案、文档或图像正文到 Langfuse，不增加追踪专用调用。Provider 仅收到其列明的必要原文或原图。保留完整私有收据、实际 wire、Token、耗时及输出。

本申请不授权：生产 API/Worker 切换、原数据库重建、索引更新、旧授权重开、正式 HTTP 模型绑定替换、自动重试或扩大题集。

## 批准后的评价

Prompt 固定证据评价核心答案、必要条件、额外事实及引用分别记录，允许中间自我纠错；新结果与旧结果逐题匹配。图像检查实际 finish、正文、reasoning usage 与完整性。DeepSeek模式控制不作为 P5 阈值收益。没有实际结果时不宣称模型质量改善。

执行适配层 `scripts/s8_8_fixed_acceptance.py` 复用正常 Registry、SendGate 与单次 LocalExecution，父账本先持久记录准入、再发送，未决发送不重放。23项当前来源校验通过；最终镜像断网渲染23项通过；53项相关定向冒烟通过。原S8.7 runner的20次/纯文本限制未被直接套用为本轮执行器。这里的通过均未产生收费请求。
