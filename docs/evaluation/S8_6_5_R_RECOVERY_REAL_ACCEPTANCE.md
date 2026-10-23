# S8.6.5-R 首个真实 HTTP 验收结果

> 此文保留 R6 失败及当时 R7 待授权的历史快照。R7 后经独立授权执行，最新实际结果见 [R7 最终审计报告](S8_6_5_R_R7_REAL_ACCEPTANCE.md)；本 R6 失败没有改为成功。

**状态：`FAILED_TURN_TRACE_READBACK_ONLY`，不是 `PASS_REAL_RAG`。** Owner 已批准冻结范围：源码 `9163f0916a60c5ae4ee647ea70816a278e3bf070`、镜像 `sha256:32cddd15cfb45c16f59dd465aed53adf5f0a697699bce501f38c0017a315d3b2`、profile SHA-256 `c300f8ba6ed7e109541e8bf18d7196e0a923959acb02f24811f12ab273dc7b54`，批准文档 SHA-256 `5805f8033c33a9a5e189e6d002df19bdf76f69fa6b5df17ffdc7eb0668292975`。

## 首个 HTTP case

首个新会话向隔离临时 API 提交一次，但 exact QU preflight 在 Provider delegate 前停止：grant 缺少必需的 `qu_request_sha256s` 映射项（client request UUID → request body SHA），因此无法完成 QU 正文绑定。调用链记录为 QU `MODEL_CALL_FAILED`，随后触发 incomplete-QU stop，SSE 返回普通链路失败。API submission 已发生，但 **Provider attempts=0、actual reported tokens=0、new UNKNOWN=0**；因此不能称为 Provider 调用成功或真实 RAG 通过。

只读回查显示 assistant turn 未完成且为 error，没有 generation run。Telemetry 只读回到普通 turn 及 history、memory、understand 共 4 条 metadata observation，inputs/outputs 均为空，没有 Provider observation。一次旧 Cloud Trace GET 路由 HTTP 410 是独立工具问题，不影响模型发送；支持的 MCP getObservations API 回读到 4 条观察。

## 修正核验与待授权的 R7

R6 execution 与 grant 已关闭，原 checkpoint 及 receipt/backup 保留，关闭时没有未解释账本变化。隔离 acceptance staging 的精确 QU grant/wrapper 修正已完成（SHA-256 `2f2c13fe1a0632b27f4f9a2fdef6b66f125ae117dc9bf0c4e0bfda10dac962ae`）：RED exit=1，缺失 hash 时 delegation=0；GREEN exit=0，缺失/错误 hash 均拒绝，只有正确 hash 放行 1 次 synthetic fake delegate。批准镜像断网渲染两份 QU body，长度分别 5,694 和 5,642 bytes，Provider calls=0。没有生产业务源码或镜像变更。这些证据只覆盖 grant/wrapper 与 body rendering，不是真实 HTTP/RAG 验收。

一次合并 R7 recovery request 已异步提交，当前 `PENDING`。新 POST 需要新的明确授权，因为既有合同限制每个 case 一次 POST；不是新的预算审查。R7 尚未运行、尚无 grant。待明确授权前不提交下一 HTTP、不运行 ABC、不创建 grant。

## 已通过的独立检查与未完成范围

source eligibility 10/10 cases、83/83 rows 只读通过；schema 检查 PASS；独立 PG R6 定向集成检查 8/8 PASS。native R6 checkpoint 为 complete=0、attempts=0、tokens=0。该次 acceptance slice memory peak 为 384,155,648 bytes（约 366.36 MiB），memory.events high/max/oom/kill 均为 0；CPU usage 6,193,512 μs、throttled 537,419 μs；设备 `259:0` read/write 分别为 53,051,392 / 647,168 bytes。临时 API 已停止，生产状态未变。这些前置检查不等于 HTTP 真实 RAG 通过。

第二个 HTTP case、全部 30 个固定 A/B/C generations、P5 calibration 均 `NOT RUN`。实际费用 `UNPRICED`，不表示免费或零费用；S8.6.5-R 整批尚未完成。

首个失败仍是隔离 acceptance grant 缺少 QU body hash。后续窄范围修正确已完成并通过上述 RED/GREEN wrapper 核验；该结果没有改变首个 HTTP case 的失败状态。下一次 HTTP 与剩余 A/B/C 仍需新的明确 POST 授权，且 R7 当前待答复；第一次冻结授权不构成自动重试许可。

本摘要不含问题、Context、答案、请求/响应正文、trace/session/request UUID、模型或租户 ID、主机信息和私有绝对路径。它不是 Owner 验收、官方 scorer 结果或整批完成声明。
