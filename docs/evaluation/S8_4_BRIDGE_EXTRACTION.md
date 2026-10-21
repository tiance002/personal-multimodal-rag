# S8.4-A Cheap 首跳抽取与有界桥接重排

**交付状态：`OFFLINE_BRIDGE_PARTIAL`。真实 Frost 语义结果：`BRIDGE_UNVERIFIED`。**

本轮最小可选机制已实现，真实 ECS 试验和证据核验已结束。真实 Cheap 返回的结构化抽取没有通过字段与原文偏移校验，因而没有准入 Q1、触发 BGE 或追加证据。`REAL_BRIDGE_FEASIBILITY_COMPLETE` 表示这次有界执行、结算与状态保护完成，不能解释为桥接恢复成功。当前 Cheap 输出合同及源文主体关联条件不足，满足本任务“明确证明条件不足”的停止条件；未宣布里程碑验收通过。

## 1. 目标、代码和改动

- 基线：S8.3-F `15c8777bdaaf0cfd3e6eb5995bd88c2d6f53b709`，包含 S8.3-R 正式修复。
- 初始实现：`b10e2a07585ad51c65473ef6b02fc42dee51b16d`。
- 本轮预检修复后的实际源码 HEAD：`4fb10233f922a47b2ddb058e79e9ec1389088c9e`。
- 分支：`codex/s8-4-bridge-extraction-20261020`。仅本地提交；没有 Push、部署、服务重启、合并或 Tag。

`backend/weknora_port/retrieval/bridge_rescue.py` 实现默认关闭的 `bridge_rescue_mode=cheap_bridge_v1`，包含原文事实窗口、Cheap JSON 与 Unicode 偏移校验、独立主体/关系门控、程序 Q1、最多 8 个首轮拒绝候选的一次 BGE 请求和第二跳原文验证。窗口最多来自 2 个保留主来源和 1 个直接前邻，总计不超过 2400 个字符。Cheap 使用正式 Registry 角色，单次抽取、`temperature=0`、`thinking=false`、最多 256 输出 Token，无错误重试。

`retrieval/pipeline.py`、`retrieval/types.py`、`retrieval/context.py` 配合 `generation/context.py` 与 `service.py` 支持独立 `rescue_evidence`、补充来源标签、最终 Context 来源映射和生成预算超限时移除补充。普通 Search、第一次 Rerank、Merge、TopK 和首轮分数保持原有语义；补充最多 1 个、1600 字符的原始文本 Chunk。`storage/knowledge.py` 与 `runtime.py` 提供受 tenant、KB、文档、当前版本和索引状态约束的原文回读。`retrieval/search.py`、`retrieval/rerank.py` 和 `evidence_capture.py` 记录原始候选、首轮 request index 绑定及阶段证据。`models/types.py`、`models/provider.py` 与 `models/request.py` 通过内部 `send_temperature` 选项让该抽取请求在线路上显式携带零温度。

`scripts/s8_4_bridge_freeze.py` 保存 source-only 输入和原始 BGE request index 到 Chunk 的绑定；`scripts/s8_4_bridge_ecs.py` 提供零发送预检、精确请求身份围栏、有界账本、PG/执行账本双重结算及状态保存核验。预检中发现的两处问题已在 `4fb1023` 修复：`runtime.py` 的安全委托列表补上 `get_current_chunks`；运行器只比较冻结记录确实保存的可选来源链接，正文、身份、范围、版本和偏移仍须匹配。对应回归位于 `tests/weknora_port/test_s8_4_bridge_ecs.py`。

没有 Schema、公共 API、正式模型角色、P5 或默认开关变更。`.serena/project.yml` 是原有本机未提交改动，未纳入本轮提交。

## 2. 冻结故障基线与离线证明

Frost A/B 每臂均有 30 个真实 Search 候选。已按保存的 BGE request/response 绑定原始 index 与 Chunk ID。第二跳 `31417c7c-7abf-4723-956a-74bf7ee14683` 的第一次原始分数为 A `0.00799383781850338`、B `0.017354240640997887`，均低于原有 0.3 阈值。它在第一次 Rerank 阈值过滤处删除，早于 MMR/Merge。

真实第一跳 `ec07a1fd-8a8e-4f23-a7d6-65d385d2343b` 使用主语 `She`；直接前邻 `86c40ec8-2147-431b-a1d4-3bfbc08e1232` 没有可验证的 Jeanne Marie Beaumont 姓名先行词。问题中的姓名不能补成原文事实。现有来源还不能证明 Advanced Seminar 与 The Frost Place 的具体举办关系，更不能把机构所在地写成研讨会具体建筑。

隔离模拟正例已验证完整通道：Mock Cheap → 精确原文/主体关系核验 → 程序 Q1 → Mock BGE → 原始第二跳进入最终 Context 和实际来源映射。真实 Frost 冻结样本保留 `UNVERIFIED`；没有把模拟正例或 Gold 用作真实恢复证明。运行输入不含 Gold、预期答案或历史模型答案。

Q1 只在主体及关系验证后按 `Where is {object} located?` 由程序构造，最多 1 条、192 字符。候选只来自原 Search 中首轮未保留、与经过验证对象或原文短语匹配的原始文本；最多一次 BGE、最多 8 个候选。0.3 仅为第二轮实验门槛，本次未产生实际 BGE 分数，未完成其真实校准。短名称只作为查找线索，不自动证明活动与机构同一。

## 3. 实际命令与结果

完整关联离线测试（初始实现）：

```powershell
python -m pytest -q -p no:reporter tests/weknora_port/test_provider_request.py tests/weknora_port/test_provider_transport.py tests/weknora_port/test_s8_4_bridge_ecs.py tests/weknora_port/test_bridge_rescue.py tests/weknora_port/test_bridge_pipeline.py tests/weknora_port/test_pipeline.py tests/weknora_port/test_source_evidence.py tests/weknora_port/test_service.py tests/weknora_port/test_kb_model_selection.py tests/weknora_port/test_generation.py
```

**PASS，退出码 0，384 passed，7.52 秒。** 输出与退出码：`E:\RAG quention\var\reports\s8-4-bridge-extraction-20261021\offline-tests.stdout.txt`、`offline-tests.exit-code.txt`。

预检修复后受影响模块回归：

```powershell
python -B -m pytest -q -p no:reporter -p no:cacheprovider tests/weknora_port/test_s8_4_bridge_ecs.py tests/weknora_port/test_bridge_rescue.py tests/weknora_port/test_bridge_pipeline.py
```

**PASS，退出码 0，132 passed，0.92 秒。** 输出与退出码：`E:\RAG quention\var\reports\s8-4-bridge-extraction-20261021\offline-tests-after-fixes.stdout.txt`、`offline-tests-after-fixes.exit-code.txt`。离线用例使用 Mock，没有新增 Provider 调用。`-p no:reporter` 关闭本机自动加载的报告插件；`-p no:cacheprovider` 关闭缓存写入。

ECS 零发送预检与一次真实运行，分别执行下列命令；两者均退出码 0：

```sh
docker exec -i -e PYTHONPATH=/opt/rag-s8/state/s8-4-bridge-20261021-bcb00a6d3ee94e47/source:/app rag-s8-api-1 python -B /opt/rag-s8/state/s8-4-bridge-20261021-bcb00a6d3ee94e47/source/scripts/s8_4_bridge_ecs.py prepare --root /opt/rag-s8/state/s8-4-bridge-20261021-bcb00a6d3ee94e47
docker exec -i -e PYTHONPATH=/opt/rag-s8/state/s8-4-bridge-20261021-bcb00a6d3ee94e47/source:/app rag-s8-api-1 python -B /opt/rag-s8/state/s8-4-bridge-20261021-bcb00a6d3ee94e47/source/scripts/s8_4_bridge_ecs.py run --root /opt/rag-s8/state/s8-4-bridge-20261021-bcb00a6d3ee94e47
```

预检 **PASS**：`S84A_READY_ZERO_SEND`，计划最多 4 个操作（2 chat、2 rerank），硬上限 8 次 / 30,000 Token。实际运行 **PASS（执行与保护）**：`REAL_BRIDGE_FEASIBILITY_COMPLETE`，六案例均完成，没有重试。该试验从冻结的 Search/Rerank/Merge 结果开始，未运行完整 RAG 或 FastAPI 问答端到端。

最后以保存回执、原始 Provider request/response 和冻结输入执行 `audit_real_receipts_offline.py`，退出码 0、stderr 为空；审计比对重新序列化的实际请求 SHA、Provider/PG/执行账本 usage、响应与 trace 一致性、来源快照和负控制。结果为 `PASS_RECEIPT_INTEGRITY_AND_SAFE_REJECTION`，新增网络/Provider 调用为 0，未修补或重新接纳模型输出。证据：`E:\RAG quention\var\reports\s8-4-bridge-extraction-20261021\real-evidence-audit.json` 和 `real-evidence-audit.exit-code.txt`。

## 4. 真实 ECS 结果与结算

负责人先明确授权指定源码与六案例语料传输到唯一 ECS stage，随后明确回复“授权按此范围继续”，覆盖向 SiliconFlow 发送已列明的 Frost A/B 问题、必要原文窗口及条件成立时的派生 Q1/候选片段。此前两次自动审查拒绝均保留为历史记录；本次继续执行已有新的明确授权。授权收据：`E:\RAG quention\var\reports\s8-4-bridge-extraction-20261021\external-egress-authorization.json`。

暂存目录 `/opt/rag-s8/state/s8-4-bridge-20261021-bcb00a6d3ee94e47` 仅供本轮诊断，API 容器通过 `PYTHONPATH` 加载本轮源码副本。冻结输入 SHA-256 为 `e3dbf7ee2f280b4b2f7b4438b89f2691faefb7e103649acc661e3d0a700b1135`；暂存运行器 SHA-256 为 `cb2b7b261b9badc70cfeba2c2a7bec8c3f6280c054b916bade9e94e612c7d065`，runtime SHA-256 为 `ba66fd3f773500188722dd4d537cbbf98cb2a1948cc6485d0d3c9dfccab503fd`。API 镜像/Worker 未更新；不能把本轮诊断视为生产部署。

Registry、实际请求及 Provider 响应中的模型字段均为 SiliconFlow `Qwen/Qwen3.5-4B`；正式 rerank 角色配置为 `BAAI/bge-reranker-v2-m3`，本次没有调用它。模型字段是 Registry/Provider 报告的身份，未对服务端权重作独立鉴定。

| 实际调用 | 输入 tokens | 输出 tokens | 合计 tokens | 结果 |
|---|---:|---:|---:|---|
| Frost A Cheap chat | 617 | 156 | 773 | HTTP 200 / stop / SETTLED |
| Frost B Cheap chat | 617 | 156 | 773 | HTTP 200 / stop / SETTLED |
| BGE rerank | 0 | 0 | 0 | NOT RUN：首跳抽取未准入 |
| **总计** | **1234** | **312** | **1546** | **2 次，0 UNKNOWN** |

这些数值为 Provider 报告 usage，与 PG 和执行账本逐项一致，不是货币账单。两臂 Cheap 的原始问题和原文窗口相同，实际 body SHA 均为 `50c4d3727f27459c87b5875b58e1f1c2e96407fd7f3bd93d09ac76969d5c39b2`，请求身份不同、输出相同；本轮不构成两套 Rewrite 对 Cheap 影响的独立比较。

两臂的真实抽取均为合法 JSON、字段集合完整，但校验首先抛出 `BRIDGE_JSON_FIELD_INVALID`：`subject_resolution` 返回空字符串，合同要求 `EXPLICIT` 或 `UNVERIFIED`。同时存在独立偏移错误：

| 原文校验 | Frost A | Frost B |
|---|---|---|
| 模型声称 quote 区间 | `[73,311)` | `[73,311)` |
| 逐字 quote 的真实原文区间 | `[155,311)` | `[155,311)` |
| quote 的 Unicode 字符数 | 156 | 156 |
| 声称区间对应字符数 | 238 | 238 |
| 主语 / subject_resolution | `She` / `""` | `She` / `""` |
| 实际首跳 span / 主体关系准入 | 未通过 / 未证明 | 未通过 / 未证明 |
| Q1 / BGE / rescue | 无 / 无 / 0 | 无 / 无 / 0 |

73 恰为主窗口起点；“模型误用了窗口起点”是诊断推断，区间不一致本身已核实。时间 `2007–2010` 的 en dash 在原文和响应中均是 U+2013，没有存储编码损坏。引用正文在原文中存在，不能抵消模型给错偏移及空枚举的事实。实际 trace 没有 `FIRST_HOP_SPAN_VERIFIED`，完整语义链状态是 **UNVERIFIED**。

四个负控制 Leyden、postcode、SciFact 611、MML 0687 全部按 `NOT_APPLICABLE / LOCATION_RELATION_ONLY` 跳过，无 Cheap/BGE 调用，无 rescue。实际正常检索数组保护与 Context 来源映射检查通过，未向 Context 加入模型 JSON、Q1 或未验证第二跳。

两条执行记录均 `SETTLED / RESPONSE_RECEIVED`；账本状态 `CLOSED`，新增 `UNKNOWN=0`。剩余 6 次调用及 28,454 Token 额度已撤销。runner 核对的旧历史/角色/模型/来源计数、当前来源证明与 storage marker 均保持一致。最终答案生成 0、embedding 0、正式配置变更 0、部署 0。完整回执归档共 21 个文件，44,029 字节，SHA-256 `3ca96d4c886963c0ae372cf4e01e9c36a76eab15688d95fcd70ddd2ac7042beb`；逐文件下载校验通过，目录为 `E:\RAG quention\var\reports\s8-4-bridge-extraction-20261021\downloaded-final`。

## 5. 遗留、版本与下一步

- **已完成：** 可选桥接机制、关联离线验证、真实 Cheap 调用、双账本结算和实际拒绝路径证明。
- **语义未通过：** 真实 Cheap 输出字段和偏移不合格；`She` 的人物身份及活动/机构关系仍无足够原文证明。本轮不报告 Frost 恢复成功。
- **NOT RUN：** 真实 Q1、第二次 BGE 分数变化、真实补充 Chunk 到 Context 的成功路径、FastAPI 完整端到端 A/B。模拟正向通道已验证，不能替代这些真实结果。
- 按原任务停止条件结束本轮；实验开关保持默认 `off`。这不是负责人批准延期或里程碑验收。
- 后续最小决策可使用已保存响应研究抽取合同是否适合当前 Cheap，并先确认现有原文能否支持人物/活动关系。当前授权账本已关闭；本轮不自动扩充模型、语料、解析、索引或调用额度。
- 代码 HEAD 为 `4fb10233f922a47b2ddb058e79e9ec1389088c9e`，文档提交另行记录；`.serena/project.yml` 原有改动保留。没有 Push、合并、部署或 Tag。
