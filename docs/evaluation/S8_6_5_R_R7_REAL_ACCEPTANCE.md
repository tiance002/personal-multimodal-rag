# S8.6.5-R R7 最终执行与审计摘要

**执行已结束；审计失败并已关闭：** `AUDIT_FAIL_STOP_MISSING_FINISH_CONTRACT`。本轮 2 个 HTTP 与 10 个 ABC case 共 38 calls、119,414 reported tokens，全部 settled、0 UNKNOWN。由于 ABC6/B 缺少 finish contract 后未及时停止，本轮不能标记为 PASS_REAL_RAG 或 Owner 已接受。

冻结源码 revision `9163f0916a60c5ae4ee647ea70816a278e3bf070`、镜像 `sha256:32cddd15cfb45c16f59dd465aed53adf5f0a697699bce501f38c0017a315d3b2`、profile SHA-256 `c300f8ba6ed7e109541e8bf18d7196e0a923959acb02f24811f12ab273dc7b54`。本次隔离 grant 与 runner 兼容修改没有更改生产业务代码或镜像。

## Provider 用量与 agent review

按实际账本 `(request_id, capability)` 去重：

| 范围 | calls | settled tokens | review 摘要 |
|---|---:|---:|---|
| HTTP 1 | 4 | 6,649 | 结构、source-based core correctness、citation semantics PASS |
| HTTP 2 | 4 | 16,331 | 结构 PASS；core quality FAIL，有相互矛盾陈述且无引用 |
| ABC A | 10 | 纳入总计 | PASS 6、PARTIAL 2、FAIL 2 |
| ABC B | 10 | 纳入总计 | PASS 5、PARTIAL 1、FAIL 2、UNVERIFIED 1、NOT SCORED 1 |
| ABC C | 10 | 纳入总计 | PASS 9、FAIL 1 |
| 合计 | 38 | 119,414 | 全部 settled、0 UNKNOWN；审计 FAIL |

HTTP 共 8 calls / 22,980 tokens，ABC 共 30 / 96,434 tokens；总 input/output 为 110,792 / 8,622。A/B/C 分别为 29,310 / 2,369、30,500 / 2,612、29,021 / 2,622 input/output tokens。ABC 分布来自 10-case 冻结开发集的 agent review，不是官方评分或整体准确率。B 的 NOT SCORED 对应无效 completion；C 的一个失败为 native target 未被回答。

## A/B/C 逐 case 代理复核标签与用量/时延

每行仅列 case ordinal、v2 labels、ledger input/output tokens 与 timing evidence；不含 case ID、问题、答案或 Context。Wall = read-finished − transport-open-start；TTFB = first-raw-byte − transport-open-start。transport-open 包含正常 gate admission，且受 admission/audit I/O 影响；这些数值不是纯推理基准。

### Arm A

| Case | Protocol cohort | Core | Necessary | Support | Citation | Presentation | Input | Output | Total | Wall s | TTFB s |
|---:|---|---|---|---|---|---:|---:|---:|---:|---:|
| 1 | PRE_STOP_ACCEPTED | PASS | PASS | PASS | PASS | PASS | 1125 | 37 | 1162 | 3.484 | 2.135 |
| 2 | PRE_STOP_ACCEPTED | PARTIAL | FAIL | FAIL | FAIL | PARTIAL | 4699 | 896 | 5595 | 45.881 | 1.225 |
| 3 | PRE_STOP_ACCEPTED | PASS | PARTIAL | PASS | PARTIAL | PASS | 2067 | 86 | 2153 | 4.176 | 0.882 |
| 4 | PRE_STOP_ACCEPTED | PARTIAL | UNVERIFIED | UNVERIFIED | PARTIAL | PARTIAL | 1837 | 154 | 1991 | 4.731 | 0.777 |
| 5 | PRE_STOP_ACCEPTED | PASS | PASS | PASS | PARTIAL | PARTIAL | 934 | 171 | 1105 | 7.405 | 0.752 |
| 6 | PRE_STOP_ACCEPTED | PASS | PARTIAL | PASS | FAIL | PARTIAL | 1331 | 327 | 1658 | 13.419 | 0.895 |
| 7 | POST_MISSED_STOP_OBSERVATIONAL | FAIL | FAIL | FAIL | PARTIAL | PARTIAL | 1235 | 136 | 1371 | 10.257 | 4.026 |
| 8 | POST_MISSED_STOP_OBSERVATIONAL | PASS | PASS | FAIL | FAIL | PARTIAL | 5758 | 390 | 6148 | 21.568 | 6.18 |
| 9 | POST_MISSED_STOP_OBSERVATIONAL | PASS | PASS | PASS | PASS | PASS | 6110 | 44 | 6154 | 2.88 | 1.134 |
| 10 | POST_MISSED_STOP_OBSERVATIONAL | FAIL | FAIL | PARTIAL | FAIL | PARTIAL | 4214 | 128 | 4342 | 4.276 | 1.036 |

### Arm B

| Case | Protocol cohort | Core | Necessary | Support | Citation | Presentation | Input | Output | Total | Wall s | TTFB s |
|---:|---|---|---|---|---|---:|---:|---:|---:|---:|
| 1 | PRE_STOP_ACCEPTED | PASS | PASS | PASS | PASS | PASS | 1244 | 48 | 1292 | 2.168 | 0.38 |
| 2 | PRE_STOP_ACCEPTED | PARTIAL | FAIL | PARTIAL | FAIL | PARTIAL | 4818 | 694 | 5512 | 26.115 | 0.75 |
| 3 | PRE_STOP_ACCEPTED | PASS | PARTIAL | PASS | PARTIAL | FAIL | 2186 | 173 | 2359 | 7.59 | 0.428 |
| 4 | PRE_STOP_ACCEPTED | FAIL | FAIL | FAIL | FAIL | FAIL | 1956 | 367 | 2323 | 12.676 | 0.432 |
| 5 | PRE_STOP_ACCEPTED | PASS | PASS | PASS | FAIL | PARTIAL | 1053 | 246 | 1299 | 15.028 | 5.544 |
| 6 | INVALID_COMPLETION | NOT_SCORED_INVALID_COMPLETION | NOT_SCORED_INVALID_COMPLETION | NOT_SCORED_INVALID_COMPLETION | NOT_SCORED_INVALID_COMPLETION | NOT_SCORED_INVALID_COMPLETION | 1450 | 311 | 1761 | 27.371 | 15.03 |
| 7 | POST_MISSED_STOP_OBSERVATIONAL | UNVERIFIED | UNVERIFIED | UNVERIFIED | PARTIAL | FAIL | 1354 | 191 | 1545 | 7.709 | 0.42 |
| 8 | POST_MISSED_STOP_OBSERVATIONAL | PASS | PASS | FAIL | PARTIAL | PARTIAL | 5877 | 200 | 6077 | 11.197 | 3.442 |
| 9 | POST_MISSED_STOP_OBSERVATIONAL | PASS | PASS | PASS | PASS | PASS | 6229 | 39 | 6268 | 2.404 | 0.798 |
| 10 | POST_MISSED_STOP_OBSERVATIONAL | FAIL | FAIL | PARTIAL | FAIL | FAIL | 4333 | 343 | 4676 | 15.546 | 2.292 |

### Arm C

| Case | Protocol cohort | Core | Necessary | Support | Citation | Presentation | Input | Output | Total | Wall s | TTFB s |
|---:|---|---|---|---|---|---:|---:|---:|---:|---:|
| 1 | PRE_STOP_ACCEPTED | PASS | PASS | PASS | PASS | PASS | 1163 | 32 | 1195 | 1.675 | 0.153 |
| 2 | PRE_STOP_ACCEPTED | PASS | PASS | PASS | PASS | PASS | 4710 | 1121 | 5831 | 60.36 | 0.708 |
| 3 | PRE_STOP_ACCEPTED | PASS | PASS | PASS | PASS | PASS | 1915 | 167 | 2082 | 3.474 | 0.737 |
| 4 | PRE_STOP_ACCEPTED | PASS | PASS | PASS | PASS | PASS | 1873 | 216 | 2089 | 4.66 | 0.254 |
| 5 | PRE_STOP_ACCEPTED | PASS | PASS | PASS | PASS | PASS | 1007 | 183 | 1190 | 3.913 | 0.298 |
| 6 | POST_MISSED_STOP_OBSERVATIONAL | PASS | PASS | PASS | PASS | PASS | 1364 | 385 | 1749 | 9.545 | 0.421 |
| 7 | POST_MISSED_STOP_OBSERVATIONAL | PASS | PASS | PASS | PASS | PASS | 1266 | 141 | 1407 | 3.421 | 0.443 |
| 8 | POST_MISSED_STOP_OBSERVATIONAL | PASS | PASS | PARTIAL | PARTIAL | PASS | 5577 | 49 | 5626 | 2.168 | 0.756 |
| 9 | POST_MISSED_STOP_OBSERVATIONAL | PASS | PASS | PASS | PASS | PASS | 6073 | 116 | 6189 | 2.914 | 0.364 |
| 10 | POST_MISSED_STOP_OBSERVATIONAL | FAIL | FAIL | PARTIAL | PASS | PARTIAL | 4073 | 212 | 4285 | 4.375 | 0.265 |

Case 10 native target 未被 A/B/C 任一 arm 回答；median boundary 另列 UNVERIFIED，因为 rounded totals 与 median convention 不能证明唯一结果。

## 必须停止条件与 telemetry

ABC6/B upstream 返回 312 个有效 JSON SSE frames 及 DONE sentinel，但没有非空 finish_reason。Adapter 标记 answer 尚未完成，direct ABC guard 却漏掉强制停止条件；第 25 个 call 后又发出 13 calls。停止前 24 calls 中有 16 个 ABC arms。第 25 个及后续调用按协议审计处理，后续 13 个只作观察性质量资料，不计作普通接受 cohort。原因是 upstream 缺少完成信号且本地 guard 漏检，不是 parser bug。

PostgreSQL readback 与 execution ledger 的 38/38 Provider keysets、119,414 tokens 对齐，状态 PASS。Cloud metadata 也对齐全部 38 个 unique Provider keys：model、wire、outcome、usage、status 与 settlement 均相等，prompt 110,792 + completion 8,622 = 119,414，metadata reconciliation PASS。单独的 native usageDetails 字段仅 36/38 有值（105,905 tokens）；两条 rerank observations 存在但缺 native usage/model 字段，其 13,509 tokens 已由 metadata usage 对账。另有 92 条 I/O observation 明确为 null；38 个 Provider parent associations 和 60 条内部 links 已验证同 trace 且无环，但 32 个根级 synthetic remote-parent IDs 不在该 inventory 中，ancestry 未完全解析。

客户端时延包含采集和审计工作，不能当作纯推理基准。费用为 UNPRICED，没有价格估算；Cloud 计算值不是 Provider bill。

## 离线修复、关闭状态与后续

两个离线 source/test 修正的实际命令与结果：`python -m pytest tests/weknora_port/test_s865_rerank_native_telemetry.py -q --tb=short` 修正前 exit 1（3 failed），修正后 exit 0（3 passed，6.40s）；组合 `python -m pytest tests/weknora_port/test_s865_observability.py tests/weknora_port/test_s865_rerank_native_telemetry.py -q --tb=short` exit 0（30 passed，8.87s，已含那 3 项，不重复计数）。Finish-contract RED `python -B -m pytest tests/weknora_port/test_s865_fixed_generation_finish_contract.py -q` exit 1（6 failed、4 passed）；GREEN `python -B -m pytest tests/weknora_port/test_s865_fixed_generation_finish_contract.py tests/weknora_port/test_s865_generation_compare.py -q` exit 0（29 passed，3.95s）；相关 diff-check exit 0。隔离候选镜像 `80f4d6dbd22eeb8155c5dd9de0835ee8b4852eb984c77308e716c697cab00f01` 的 zero-network check 实际 PASS：Python 3.11.16、SDK 4.15.6、imports PASS、30 个 ABC wire hashes 与两份 QU body hashes 相同（5,694 / 5,642 bytes），network/database/Provider calls 均为 0；patched real verification 仍为 NOT RUN。本轮使用冻结镜像，未产生新的真实调用或 grant、未改已结算 entries 与用量，也未改生产镜像。

最终资源读回记录了 memory pressure/reclaim 信号（memory.events max=150），oom 与 oom_kill 均为 0；不能据此声称全程无内存压力。R7 已关闭且无 active acceptance process，未设置自动重发。

处理 HTTP 2 core-quality 失败，并在任何后续真实验证前取得精确 source/image/profile 与 stop semantics 的新授权；native usageDetails 两条 rerank 字段缺口与未解析 ancestry 仍作为 telemetry 字段/关联限制记录。P5 threshold 未变，本轮没有 calibration evidence。本文不含问题、Context、答案、请求/trace ID、内部模型或 tenant/KB ID、主机或私有路径，也不代表 Owner 接受或 official scorer 结果。
## 最终隐私核对与修复版本

真实 metadata-only 补充核对：92 条 observation 的 input/output 均为空；69 个 metadata 字段均属于 59 个项目允许字段或 10 个 SDK 字段，未见额外内容字段。对 78 份已哈希本地输入进行已知文本窗口扫描，20 个命中均被逐项归因为既有 KB 标识与引用标识的重叠，未见未分类命中或已知自然语言正文片段。该检查支持此范围的内容保护，不能证明任意短片段绝对不存在。

修复源码提交 `b4c6e4520b0d386a251a5b604553cc015eb7e143` 已有定向离线及隔离镜像断网证明；真实修复版本仍未运行。已准备仅一次 HTTP 加原第 6 个固定 Context 的三 arm 复核，须新版本明确授权。申请文件 SHA256 `ed411d68ff1d77b03b127fdc2fd26cdeed9b8c779ca86e008958d9ef55bdce6c`。不追加 Token、不重开旧 Grant、不重跑 10 题或 ECS118。
