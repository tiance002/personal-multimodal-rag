# S8.5 首次真实测评计划：仅 Frost A

当前：已取得用户对以下精确范围的批准，并执行一次 Frost A；结果为 **`S85_REAL_STOP`**。Cheap 的 `subject_resolution=""` 不符合合同，执行到此停止，BGE 未运行。以下保留原计划范围与调用前标记；实际结果附在文末。旧 S8.4 CLOSED 账本及其剩余额度未复用。

## 唯一调用范围

| 操作 | Provider / 模型 | 最大次数 | 实际输入/输出 Token（执行前） |
|---|---|---:|---|
| 第一跳句子 ID 抽取 | SiliconFlow / `Qwen/Qwen3.5-4B` | 1 | NOT RUN；输出硬上限 256 |
| 第一跳及身份验证后才准入的 Q1 Rerank | SiliconFlow / `BAAI/bge-reranker-v2-m3` | 1 | NOT RUN；只使用初始拒绝池，最多 8 个原文候选 |

整个新账本最大 **2 次操作 / 10,000 Token 预占与实际报告总量上限**。预占用现有 wire bytes + 明确输出上限 + 1024 的保守合同，只是调用门控；不当成实际 Token 或费用。仅报告 Provider 返回、两侧账本匹配的实际 Token；缺失则保持未知并停止。重试、Frost B、Embedding、最终答案生成、Expensive 均为 0。

抽取只外发 Frost A 问题和程序从当前原文生成的句子选择：最多 2 个主来源、沿真实 pre 最多 3 个前驱，去重后最多 2400 字符。身份与关系成立后才外发程序 Q1 和同范围/版本的原始候选，每候选最多 1600 字符。不会外发 Gold 或期望答案。

## 新身份与证据

- Stage：`20261021-6c5bee3541044d649d70629ac0514b20`。
- Grant：`s85-20261021-6c5bee3541044d649d70629ac0514b20`。
- Cheap request ID：`s85-20261021-6c5bee3541044d649d70629ac0514b20/frost-A/bridge-extract`。
- BGE request ID：`s85-20261021-6c5bee3541044d649d70629ac0514b20/frost-A/bridge-rerank`。
- ECS 唯一诊断目录：`/opt/rag-s8/state/s8-5-bridge-20261021-6c5bee3541044d649d70629ac0514b20`。
- 新执行账本：`/opt/rag-s8/state/executions/s85-20261021-6c5bee3541044d649d70629ac0514b20/execution.json`。
- 输入仍为逐字冻结 `inputs.json`，SHA-256 `e3dbf7ee2f280b4b2f7b4438b89f2691faefb7e103649acc661e3d0a700b1135`；仅选择 `frost-A` 执行。其他保存案例不调用模型。
- 新合同 `sentence-id/v2`，新 runner `scripts/s8_5_bridge_ecs.py`。本地证据 `var/reports/s8-5/stage4/`；远端保留 preflight、case trace、双账本、原始 request/response、manifest 和授权关闭记录，不保存请求头或密钥。

## ECS 操作范围

1. 将明确的本轮 Python 源码、运行器与上述只读冻结输入副本传到唯一新诊断目录，校验清单/字节 hash。源码以本轮提交和逐文件清单绑定；不复制凭据/环境文件。
2. 使用已有 `rag-s8-api-1` 容器的一次性 Python 进程和新目录 `PYTHONPATH` 执行 `prepare` 零发送核验：真实 DB/租户/用户/Scope/当前原文版本、双向前驱、正式角色/配置与旧历史快照。
3. 核验通过后执行一次 `run`，只允许上述两个精确 ID。每次调用后核对唯一 PG 收据与执行记录、真实 usage、当前原文与历史/配置保护。
4. 只读取回本轮证据、校验 hash，关闭新授权、撤销未用操作额度。保留原有 UNKNOWN 占用。

这组操作不会更新 API/Worker 镜像或 Compose，不重启服务、不改正式配置/数据库 Schema、不运行入库、不删除旧代码/容器/数据；执行写入仅限新诊断文件/新执行记录和正常 Provider 结算记录。

## 立即停止条件

- 原文缺失、版本/Scope/前驱/重叠不一致，或者当前角色不再是固定模型。
- 字段/句子 ID/关系/身份合同错误；不修补真实输出，不重试。
- 不能证明实体、关系或地点时停止对应桥接分支，保留 UNVERIFIED；不强行运行 BGE。
- UNKNOWN、用量缺失/异常、权限拒绝、Token 预占或实际总量越界、双账本键/usage 不一致。
- 发现需要更多调用、Frost B、生产更新、第二次全库 Search 或其他范围扩展。

若只证明 The Frost Place 机构所在地，即使 BGE 高分且进入 Context，也须保留 `ENTITY_BRIDGE_UNVERIFIED` / `FINAL_ANSWER_UNVERIFIED`。本轮不生成最终答案，不能据此报告真实多跳问答准确率。

## 实际授权与关闭

- 精确批准原句：“批准此精确范围并执行”；对应 `call_77e140469d2c4edaa53239bf4adc979b:0`。未使用此前宽泛允许或旧额度替代本轮精确批准。
- 源码基线 `f5b0f6e732c7951cae5b993c45b3b92f5a0b785e`；新目录 205 文件校验完成，prepare 0 次发送，10 句/1433 原文字符/3 个前驱。没有生产部署。
- 唯一实际调用：`.../frost-A/bridge-extract`，`Qwen/Qwen3.5-4B`，HTTP 200，finish stop，**输入 1103 / 输出 134 / 合计 1237 Token**。原始响应与 PG/执行双侧收据完全一致。
- 虽选中正确关系句，`subject_resolution` 空值被 `BRIDGE_JSON_FIELD_INVALID` 拒绝；未运行身份准入、Q1、BGE、补充 Context 或最终生成。未修补原响应，未重试，新增 UNKNOWN=0。
- 运行器 exit 2，外层编排 exit 1，状态 `S85_EXTRACTION_OR_RESPONSE_CONTRACT_STOP`。新账本与授权均 STOP；未用 1 次操作与 8763 未占用 Token 额度撤销，既有占用和历史记录保留。
- 原始收据包 58,013 字节，SHA-256 `310b7ea37aa227ab2cb6a8e5a7805a0246605f731eab4a1ba764df5630e247a1`，22 文件清单校验通过。
- 完整逐层结论、实际命令/退出码与收据位置见 [实施报告](S8_5_IMPLEMENTATION_REPORT.md) 和 [独立真实结果](S8_5_REAL_EVALUATION.results.json)。本授权已关闭，不能用于追加或重试。
