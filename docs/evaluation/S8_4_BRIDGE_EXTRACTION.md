# S8.4-A Cheap 首跳抽取与有界桥接重排

**整体状态：`BLOCKED_AUTHORIZATION`（真实模型可行性阶段）**

**离线状态：`OFFLINE_BRIDGE_PARTIAL`**

**基线：** S8.3-F `15c8777bdaaf0cfd3e6eb5995bd88c2d6f53b709`（含 S8.3-R 修复）
**实现提交：** `b10e2a07585ad51c65473ef6b02fc42dee51b16d`，分支 `codex/s8-4-bridge-extraction-20261020`

## 1. 目标与改动

本轮实现默认关闭的 `bridge_rescue_mode=cheap_bridge_v1`。普通 Search、第一次 Rerank、Merge 和 TopK 仍先完成；补救只读取正常 Merge 后保留的原始文本证据。新增的补充证据独立保存在 `rescue_evidence`，不改写正常结果数组或首轮分数。

`backend/weknora_port/retrieval/bridge_rescue.py` 实现窗口定位、Cheap JSON 校验、主体/关系门控、程序生成 Q1、最多 8 个首轮已拒候选的一次 BGE 请求及第二跳原文验证。Cheap 最多收到 2 个主来源与 1 个直接前邻来源，总计不超过 2400 个 Unicode 字符；调用使用 Registry 的 Cheap 角色、单次请求、`temperature=0`、`thinking=false`、256 输出 Token。Provider 请求渲染增加仅供内部调用的 `send_temperature` 标记，让这次请求在线路上显式携带零温度；原有默认行为和不支持 temperature 的 Provider 行为不变。

第二跳必须来自原始 Search 候选、正文不超过 1600 字符，并与第一跳互补。补充最多 1 个原始文本 Chunk。它走正常来源映射和生成输入预检；Context 超限时只移除补充证据。当前来源读取沿用 tenant、KB、document、启用状态、解析状态、版本和索引状态约束，没有 Schema/API/P5 或生产默认变更。

改动文件和模块职责见提交 `b10e2a0`；完整离线输入及原始请求索引绑定保存在 `tests/weknora_port/fixtures/s8_4_bridge/`。输入不含 Gold、预期答案或历史模型答案。

## 2. 已保存故障证据

S8.3-R Frost A/B 各有 30 个 Search 候选。已按实际保存的 BGE request/response 重建原始 request index 到 Chunk ID 的映射。第二跳 Chunk `31417c7c-7abf-4723-956a-74bf7ee14683` 在第一次 0.3 阈值处被删除：A 原始分数 `0.00799383781850338`，B 原始分数 `0.017354240640997887`。删除发生在首轮 Rerank 阈值过滤，早于 MMR/Merge；首轮正常排序与分数保持原样。

Frost 第一跳原文含 `She`，而保存的直接前邻来源没有可验证的 Jeanne Marie Beaumont 主语回指。问题中的姓名和包含在另一个实体短语里的字符串都不能证明该代词的身份，也不能证明 Advanced Seminar 与 The Frost Place 的举办关系。因此，离线实现对真实 Frost A/B 样本保留 `UNVERIFIED`，不生成 Q1、不调用第二次 BGE，也不把机构位置说成研讨会会场。该语义结果是 `PARTIAL`，不是恢复成功。

通用正向路径由隔离模拟样本验证：Mock Cheap → 原文关系校验 → 程序 Q1 → Mock BGE → 原始第二跳文本进入最终 Context。Frost A/B 本身没有通过这条语义门控。Leyden、邮编、SciFact 611、MML 0687 的保存负例不触发无关桥接。负例和正例均没有通过 Gold 指定目标 Chunk。

## 3. 离线执行命令与结果

命令：

```powershell
python -m pytest -q -p no:reporter tests/weknora_port/test_provider_request.py tests/weknora_port/test_provider_transport.py tests/weknora_port/test_s8_4_bridge_ecs.py tests/weknora_port/test_bridge_rescue.py tests/weknora_port/test_bridge_pipeline.py tests/weknora_port/test_pipeline.py tests/weknora_port/test_source_evidence.py tests/weknora_port/test_service.py tests/weknora_port/test_kb_model_selection.py tests/weknora_port/test_generation.py
```

**退出码 0，384 passed，7.52 秒。** 完整命令输出与退出码分别保存在 `E:\RAG quention\var\reports\s8-4-bridge-extraction-20261021\offline-tests.stdout.txt` 和 `offline-tests.exit-code.txt`。`-p no:reporter` 仅关闭本机自动载入的 `pytest-html-reporter`：它会在测试结束序列化未配对代理字符并导致 pytest teardown 退出 1；pytest 用例本身已运行完。此前的 Provider MockTransport 线路测试还发现缺少 `temperature=0`，现已增加显式内部选项并由本命令重新验证。

保存输入的来源证明包括：每臂 30 个 BGE 请求 passages 的哈希及其原始候选绑定、原始分数和阈值/Merge/Context 阶段 ID；冻结输入保留原始问题、Chunk ID、正文、Unicode 字符偏移、来源版本、候选顺序和首轮分数。两套 Frost 源文原始回读及其 SHA-256 证据位于已批准的 `E:\RAG quention\var\reports\s8-3-r-20261020\downloaded-final` 记录中；报告和测试均未改写这些文件。

## 4. 真实 ECS/Provider 阶段

负责人已明确授权最多 8 次 Provider 调用及总计 30,000 Token 的真实可行性测试。冻结运行计划精确列出最多 4 个请求身份（2 Cheap、2 BGE，分别对应 Frost A/B）；保存的非 Frost 对照不在计划内。真实使用量仍是 **0 次 Provider 调用、0 Token、0 UNKNOWN**：模型请求尚未发出，也没有生成新的执行账本。

ECS 只读盘点确认当前 API/Worker/PG/Redis 容器身份；API 镜像为 `sha256:80e2f2d84bf67d7d22117d98676049aa9cd3101e69b20f780d5fd9eeaf931a36`，OCI revision `29ecf2f5d3037eed99a4de783546dee272a761bd`。该镜像早于 F 基线。为准确测试本轮代码，已在 `/opt/rag-s8/state/s8-4-bridge-20261021-bcb00a6d3ee94e47` 建立空的私有诊断目录；API、Worker、镜像、配置和运行中数据服务均未修改、重启或部署。目录尚无源码、冻结输入或预检/执行记录。

自动审批审查拒绝了将包含项目源码和六个冻结案例输入的 2,844,989 字节传输包通过 SCP 发送到 `root@120.55.115.162:/opt/rag-s8/state/s8-4-bridge-20261021-bcb00a6d3ee94e47/transfer_bundle.zip`。审查给出的理由是“授权真实测试”没有明确授权该目的地接收这份具体源码和语料载荷；被拒的 SCP 工具命令没有执行。包的本地 SHA-256 为 `e71e588cef13e10cc9f19dd1d558a53729f6b02a0bd8ab345330935cf8cdd7df`，其中包含 S8.4 源码、`s8_ecs_runtime.py`、六案例 source-only 输入和当前授权界限文件，不含凭据、Gold 或历史答案。此后没有改用其他传输方式。

因此，ECS 零发送 preflight、实际 Cheap/BGE 请求和新执行账本状态均为 `NOT RUN`。本阶段代码可执行的预算、请求身份、实际 Provider 线路校验和账本/PG 双重结算检查已由 Mock 与单元测试覆盖；这不能替代真实服务验证。

## 5. 风险、遗留与下一步

- Frost `She` 的身份、Advanced Seminar 与 The Frost Place 的实体关系，以及具体会场都仍未被现有证据证明；此限制必须保留。
- 真实模型语义、Provider 线路、真实 Rerank 分数、外部计费 Token 和运行时来源预检没有实测，均为 `NOT RUN`。
- 要继续真实阶段，需负责人明确确认允许将上述 2.84 MB 源码与六案例冻结输入传输到指定 ECS 路径。确认后使用本次唯一新 Stage、先执行零发送预检；预检核实 Registry 角色、现有 storage marker、当前源文、请求计划及历史账本后才进入 Provider 测试。任何新 UNKNOWN 即停止且不得重发。
- 当前未部署、未 Push、未合并、未改正式角色/P5/default，未复用历史授权。分支只包含本地提交；`.serena/project.yml` 保持未提交，以保留本机原改动。

## 6. 版本与执行记录

- 基线：S8.3-F `15c8777bdaaf0cfd3e6eb5995bd88c2d6f53b709`。
- 实现提交：`b10e2a07585ad51c65473ef6b02fc42dee51b16d`。
- 离线：`PASS`（受影响测试 384 项）。
- Frost 语义恢复：`PARTIAL`（代词及机构/活动关系未验证）。
- 真实测试：`BLOCKED_AUTHORIZATION`（精确目的地/载荷 SCP 被自动审查拒绝，零 Provider 调用）。
- 下一个最小任务：获得具体传输授权后，执行本地包 SHA 校验、私有阶段接收和零发送 preflight；若没有授权，保留当前代码及本报告，不声称真实阶段通过。
