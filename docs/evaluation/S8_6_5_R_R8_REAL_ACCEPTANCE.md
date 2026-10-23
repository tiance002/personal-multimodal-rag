# S8.6.5-R R8：真实复核最终报告

**R8 scoped observability：`PASS_REAL_RAG`。S8.6.5 总体质量：`PARTIAL_NOT_ACCEPTED`。** 本轮完成一笔 HTTP RAG turn 与固定 case 6 的 A/B/C 三条背景生成：7 次 Provider calls、11,897 actual reported tokens，7/7 settled、0 UNKNOWN。该 PASS 只覆盖本次单 HTTP + 三条背景生成的限定 R8 执行和可观测性，不代表全局生产质量或整个 S8.6.5 已通过；Owner acceptance 与 official score 均未声明。

冻结源码 revision `b4c6e4520b0d386a251a5b604553cc015eb7e143`、隔离镜像 `sha256:80f4d6dbd22eeb8155c5dd9de0835ee8b4852eb984c77308e716c697cab00f01`、profile SHA-256 `c300f8ba6ed7e109541e8bf18d7196e0a923959acb02f24811f12ab273dc7b54`。生产业务源码、镜像和配置未改变。

| 范围 | Calls | Input | Output | Total | 结果 |
|---|---:|---:|---:|---:|---|
| HTTP RAG turn | 4 | 6,563 | 86 | 6,649 | settled；来源核对、可回读引用、SSE/history/PG 和重放门通过 |
| ABC A | 1 | 1,331 | 294 | 1,625 | stop 合同通过；代理复核 PARTIAL |
| ABC B | 1 | 1,450 | 378 | 1,828 | stop 合同通过；代理复核 PARTIAL |
| ABC C | 1 | 1,364 | 431 | 1,795 | stop 合同通过；core 代理复核 PASS_SOURCE_BASED_CORE_ONLY |
| 合计 | 7 | 10,708 | 1,189 | 11,897 | 7 settled；0 UNKNOWN |

HTTP 回答的来源核对支持数据集计数 25,000，1 条 citation 可回读。HTTP client wall 220.624 秒，first answer 208.607 秒；ABC A/B/C transport-open 到 read-finished 分别为 15.880 / 14.921 / 10.941 秒，TTFB 为 3.667 / 0.501 / 0.356 秒。时延包含 admission 与审计 I/O，不能视为纯推理基准。

ABC 三臂都正常完成，`finish_reason=stop` 且 `provider_answer_done=true`；三份 actual wire body 均与 R7 冻结 arm wire 完全一致。代理复核属于小样本、source-only proxy review，不是盲审、Owner 审核或 official scorer：A 为 PARTIAL（来源不支持的属性标题）；B 为 PARTIAL（对歧义来源作无依据修补，数字/分组引用有缺陷）；C 的 core 为 PASS_SOURCE_BASED_CORE，但来源化学事实标注 UNVERIFIED_SOURCE_DEFECT、不确定性处理 PARTIAL。故没有任何 ABC arm 整体质量 PASS。当前真实 ABC 未触发负向 guard；finish-contract 负向路径由既有离线 RED/GREEN 测试覆盖。

## 数据与运行边界

只读 PG 回读 7/7 Provider keysets 与本地结算一致，11,897 tokens、UNKNOWN 0。最终 Cloud 对账状态 `LANGFUSE_SCOPE_PASS`：22 observations、7 Provider calls，native model/usage 与 PG、本地账本及 Cloud metadata 7/7 一致；prompt 10,708 + completion 1,189 = 11,897。22 条 observation I/O 明确为 null，metadata 均在 allowlist 或 SDK inventory 内，未发现 unexpected keys。18 条内部 parent links 同 trace、无环、时间包含检查通过；4 个根级 SDK parent 在导出之外，未断言外部存在。

初次本地 reconciliation harness 将内部链接数预期误设为 19，产生的 FAIL proof 已保留。正确值是 22 observations 减 4 roots，即 18；更正预期值后使用未变化输入生成的最终 proof PASS。这是本地审计脚本算术错误，不是 Langfuse 或 Provider 失败，也未触发新发送。

验收批次已关闭，所有 R8 grants 均关闭且 settled entries 未被改写，无活跃验收进程；四个生产容器仍运行，生产状态未替换。资源终态记录 memory.events high=2、max=245，OOM 与 OOM-kill 均为 0；保留 memory pressure/reclaim 信号，不表述为“无压力”。R8 7 calls / 11,897 tokens；连同 R7 累计 45 calls / 131,311 tokens，原实际用量风险上限记录余额 1,868,689。费用无 Provider invoice，标记 `UNPRICED`。

R7 的 `AUDIT_FAIL_STOP_MISSING_FINISH_CONTRACT` 与其停止点后 13 次调用仍是历史协议违例，R8 不会追认或抹去。R7 HTTP 2 的历史质量失败仍未解决，旧 10-case cohort 中越过停止点的成员也未因此重写。P5 threshold 未变，本轮没有 calibration evidence。全局 S8.6.5 质量仍为 `PARTIAL_NOT_ACCEPTED`。
本轮资源事件增量：memory.high +2、memory.max +95、OOM/OOM-kill +0。发生过回收压力；这些计数和单点采样不能作为内存峰值。下一步建议优先收敛重复审计元数据投影，保留实际 Provider Wire 和可回读原文，减少受限磁盘上的重复写入；本轮未实施该后续优化。

验证边界：本轮未重新执行既有 30 项 observability 与 29 项 finish-contract 离线套件，复用同一源码的已保存结果。实际执行为新授权的一次正式 HTTP、三次固定 Context 生成、只读 PG 回读、Cloud API 回读和本地收据核对。本轮没有注入真实 Cloud 故障，故障隔离的异常分支仍依据既有离线证据；正常路径与回放确实没有重复发送。机器结果中的证据相对路径指向私有验收包，原始请求、原文、答案及账本不随公开仓库发布。
