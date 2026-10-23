# S8.8 实施与真实验收进展

状态：**IMPLEMENTED_OFFLINE_VERIFIED_REAL_IN_PROGRESS — 55 题质量目标尚未达到**。

本报告更新到 2026-10-24 的 HTTP18-r3 和两次独立同输入 Expensive 配对完成记录及源码 `1f927e1`。初次收费授权前的实现、定向测试及构建证据完整保留于 [离线基线报告](S8_8_OFFLINE_ACCEPTANCE_01AE7CF.md)；其中的“0 调用、待授权、HTTP 未运行”仅描述当时阶段，不代表当前状态。当前实际用量与分批统计见 [真实质量进展](S8_8_REAL_QUALITY_PROGRESS.md)。本报告不声明生产可用或 Owner 已验收。

## 1. 当前版本与边界

- 分支：`codex/s8-8-implementation-20261023`。
- 当前代码提交：`1f927e1845a0a137242fac58ee409620dc7bf2ec`。
- 原业务接线与固定输入执行基础：`01ae7cf05767e211224edfa9c0d5e0a3267f6e82`。
- 后续最小修复：`a48f29f`（可选 Rerank 原文头部/URL 信息与委托截止时间）、`6bd49e7`（选定 MinerU 图片的原始父块定位）、`b5fc550`（验收 profile 明确绑定 Rerank 上下文开关）、`b132d9e`（登记实际测过的 v2/v3 Prompt 候选）、`dd18996`（有界原文身份送入最终 Context）、`6b63b19`（串行策略保留已验证图片派生证据）、`1f927e1`（验收 profile 元数据无损去重）。
- 原工作树、生产 API/Worker、索引、历史 Grant/Ledger/UNKNOWN 均保留。没有合并 main、打 Tag 或部署生产。
- 后续 Owner 已授予本任务项目 API 调用权限，并确定固定 55 个答案 Gold 身份、至少 50 个核心正确的目标。每批仍冻结输入、模型与版本，使用新账本；未知发送不自动重试。

## 2. 功能接线与真实验证的区别

| 项目 | 实现与离线 | 已有真实证据 | 剩余边界 |
|---|---|---|---|
| 问答 Quick/Smart、输出 Token、保存/恢复 | 前端、ASGI、实际 SQL、Provider renderer 已通过定向验证；普通 RAG 与旧 Agent API 分离 | 4 道新正式 HTTP 保存并回读 Quick2048；其中一次合法 Cheap→Expensive 升级仍关闭思考、保持额度 | 浏览器使用 Mock HTTP；尚未宣称浏览器到收费后端完整验收 |
| Smart | 同一配置模型的普通 HTTP Quick/Smart 配对已完成 | 保存设置、真实 wire、History/Replay 和用量一致，Smart 有实际思考输出 | 当前 Cheap 托管身份的 Smart 总输出合同仍 UNKNOWN，不能默认为不支持，也不能伪造硬上限；不自动换模型 |
| 图片独立设置/选定重处理 | 终态、事务切换、版本/SHA/权限、取消后 UNKNOWN 保护已实现并测试 | 9 张固定原图均 stop+非空，包含 7 张旧 length 与 2 张正常控制 | 完成不等于内容准确；语义复核 4 较忠实、2 混合、3 未决，存在可见文字转录错误；另一次正常选定图片重处理已在物理隔离副本完成 Caption+Embedding 并切换派生索引，原文保持不变 |
| 固定澄清与取消 | 明确固定完成标记、列表上限、SSE/History/回放、取消来源记录已修复 | 历史记录重放与 Mock 验证 | 历史 HTTP112 原因仍 UNKNOWN；没有补写历史原因 |
| 来源与 Rerank | 状态过滤、当前原文回读、可选原文头部构造已实现 | HTTP33、36 必需数值片段进入实际生成输入并答对 | 33 年份唯一性未证明；36 引用误指标题块；24 缺引用及范围说明 |
| Prompt/P5 | v1/v2/v3 候选版本化；生成前特征快照与影子函数已实现 | 固定证据有可核查的 Cheap/Expensive 配对 | 默认策略未全局切换；生产 P5 阈值仍 0.35，Rerank 阈值另为 0.3；未宣布完成路由校准 |

Quick 诊断身份只证明当前配置的 2048 输出额度可运行，不代表供应商最大值认证。Quick/Smart 不是 Cheap/Expensive 的别名。

## 3. 正式 HTTP 逐题结果

四题均经正常 Session/Message 接口、Settings 持久化、Registry、SendGate、LocalExecution 和 SSE。各题独立新 Session，未修改原问题或原始整库范围。已完成账本关闭，条目和原始响应保留。

| Gold ordinal | 实际调用/Token | 核心答案 | 证据与引用 |
|---|---:|---|---|
| 39 | 5 / 8,406 | 正确 | Cheap 已答对，但无效引用触发合法升级；Expensive 修正引用。不能算核心答案救回 |
| 33 | 4 / 18,869 | 正确 | 必要数值在原候选正文；真实头部提供城市/报告年份，目标排第 1，进入实际 Context；引用可回读 |
| 36 | 4 / 19,999 | 正确 | 必要数值在已送达正文；答案引用了仅含标题的另一 Chunk，引用精确性 FAIL |
| 24 | 4 / 20,110 | 核心 Gold 正确 | 最终没有引用，Context 含多个城市数值；不能把数值命中等同于消歧和出处均通过 |

HTTP39：源码 `6bd49e7d48e2ee5a52e572b094a0104f5234aff8`，镜像 `sha256:62bccab7a5052bbbb5056ca0a96a0de7b29daf55380f4cf5e36f773b0a6a3652`。

HTTP33/36/24：源码 `b5fc55058e3096948f802a3d9f4f0bdfbab5ddb5`，镜像 `sha256:07a9c27e4fce174e3be892473511ae4a88644865da6c39637d564310ee3fd674`；835 个源码文件与冻结归档一致。

Langfuse 已实际回读 HTTP39/33/36 的 5/4/4 个 Provider Observation，与原生账本用量逐项一致，输入/输出均 null。HTTP24 的 16 个 Observation 已完成独立对账，4 个 Provider 用量合计 20,110，与原生记录一致。没有为观测新增模型调用。

### 普通 HTTP Quick/Smart 控制与隔离图片补救

同一配置 DeepSeek 模型的两次普通 HTTP 控制分别为 Quick 4 次 / 6,551 Token、Smart 4 次 / 6,606 Token。两者保存并继承 Session 设置，最终 System/User 消息与 Context 相同，wire 仅思考开关不同，均保持 2048 输出额度。核心答案及当前引用通过，SSE/History/Replay 一致；Smart 报告 35 个 reasoning Token，Quick 缺该计数，记为 UNKNOWN 而非 0。两者各 4 个 Provider Observation 与原生用量匹配，输入/输出为 null。该控制显式采用 EXPENSIVE_ONLY/OFFLINE_ARM，不证明 DYNAMIC 自动升级、Cheap Smart 能力或质量优越性。

HTTP50 第一次尝试在最终生成前因 `RetrievalScopeError` 失败，3 次已结算调用 / 7,021 Token 保留。`6b63b19` 修复后，r2b 经正常 HTTP 完成，4 次调用 / 8,734 Token、UNKNOWN 0；核心正确、整体 PARTIAL，引用可回读到当前派生 Caption。原生 stop/DONE 与已完成 History 一致。response capture 的 complete=false 表示消费者收到语义 DONE 后停止迭代、未自然读到 EOF，不表示业务回答失败。它不是固定 V2 的同输入结果。

### HTTP18-r3 与两次独立生成配对的最终结果

HTTP18-r3 正式 HTTP 完成：4 次调用 / 16,854 reported Token，6.90365 秒，UNKNOWN 0。SSE、History、`port_turns` 均完成，4 条 PG Provider 记录与原生账本逐项一致。最终只回答实际 Context 提供的基础联系路径，目标端点后缀并未送达，因此核心和整体均为 PARTIAL；不能把缺失后缀写成模型遗漏了已给出的证据，也不能改 Gold 或推断后缀。原始 `messages.turn_status=pending` 与 `is_completed=true` 是既有兼容合同：历史接口由完成标记投影 completed，最终事务同时将 `port_turns.status` 置为 completed，不是保存失败。

随后两次 **独立同输入 Expensive 生成** 均 stop 完成，各使用已有正式 HTTP 的相同消息与 Context；它们本身不是新的 HTTP 全链路运行：

| 配对 ordinal | 调用 / reported Token | 核心 / 整体 | 比较结果 |
|---|---:|---|---|
| 50 | 1 / 1,559（1,506 输入 + 53 输出） | CORRECT / PARTIAL | 仍复制 Caption 的错误节点数量，未改善整体忠实性 |
| 18 | 1 / 1,904（1,714 输入 + 190 输出） | PARTIAL / PARTIAL | 仍给出基础路径并说明页码相关内容缺失，未解决精确端点问题 |

HTTP18-r3 和 ordinal 18 配对使用源码 `1f927e1` / 镜像前缀 `256856`；ordinal 50 配对使用 `6b63b19` / `a6ac`。结果各自保留，不混入固定 V2 分数。此次源码 `1f927e1` 的正式 HTTP 正常完成不代表那两项已记录的旧 ASGI fixture 测试转为通过。

## 4. 固定 55 题与真实质量变化

固定 Prompt v2 的 Cheap 回答已完成 53 题；35、48 没有该版本生成结果，仍计入 55 题分母。主代理暂定核心正确 **40/55**；同证据 V2 Cheap 加匹配 Expensive 复核并集为 **41/55**，不是自动路由的实测准确率。旧子代理把 34 判对的记录保留，主代理因未撤回的错误首句将其纠正为 WRONG；没有修改 Gold 或原始回答。

匹配条件的 Expensive 在第 6 题纠正了正常/异常条件区别。第 4 题 v3 的同证据配对补齐此前遗漏的清单项；第 34 题 v3 修正了成立与更名日期混淆。第 33/36 的新 HTTP 使用了修复后的检索证据，属于不同输入版本。这些改善各有真实记录，**不能拼成一个统一 profile 的通过率，也不是生产中已知错题自动路由**。

第 50 题经只读、按问题词选择的当前原文检查及 4 个原图 SHA 验证，确认人脸网络结构原图存在；OCR 未完整保留视觉层数。选定图片的正常 Caption+Embedding 已在保留原整库范围的物理隔离副本完成（2 次调用 / 1,416 Token）；新增证据保持 `image_caption` 派生类型，267 个原始块和 4 个文档保留。随后正式 HTTP r2b 回答的核心层数正确，但沿用了 Caption 中错误的节点数量，整体为 PARTIAL。此改动证据后的 Prompt v3 结果不并入固定 V2 成绩。

第 47/48 原请求范围与所问报告不匹配；25/26/35/38 缺少明确对象或文档绑定；52 的局部 Context 不能证明全篇图像总数。已请求 Owner 明确是否另建修正输入版本，原实验独立保留。未获范围选择前不擅自换库或绑定 Gold 文档。

## 5. 实际调用、资源与失败保留

截至 HTTP18-r3 及两次独立配对完成的规范化原生回执快照：**135 次实际 Provider 调用，543,542 个 reported Token，UNKNOWN 0**。其中包括生成、图片和 Rerank 诊断，不等于 135 道问答。原始批次有 1 次缺必要结束信号的响应，被安全拒绝；已发生的真实用量保留。费用无可核验结算金额，记为 UNPRICED。

所有新批次独立、严格串行、后台持久运行。测试资源组为 1 CPU / 1280 MiB，保留已有磁盘 I/O 限制；当前检查未出现新的 OOM/kill。CPU、内存峰值没有完整采样，未宣称压力测试通过。生产镜像与配置保持原状。停止临时服务不删除结果、原始资料或 Volume。

零发送准备错误、原始不完整响应、旧评分冲突、导出工具错误都保留，不把准备错误算作模型语义错误，也不将同一回执的多个快照重复计数。

## 6. 测试与当前未完成项

初次集成的 291 项组合验证、真实 PG 定向验证、Chrome/Mock HTTP、TypeScript 构建及关联失败记录见离线基线报告；数量重叠，不相加。最新局部 profile 开关验证 46 passed，Prompt 版本合同 RED 12 failed/14 passed → GREEN 26 passed。实际命令及日志保存在对应私有证据中。未实现的命令不得报告通过。

当前调用已完成，不再继续当前批次发送。HTTP18-r3 及两次同输入 Expensive 配对的回执和评审均已纳入；公开材料检查和私有证据打包已完成；Owner 范围/输入澄清与最终统一版本的 55 题评估仍未完成。目标尚未完成，不以报告提交代替验收。没有调整 P5 规则、全局阈值、Gold，或新增 Agent/HyDE/全库递归检索。

### 最新两项定向修复的已有验证

- `6b63b19`：只允许已物化、当前有效且范围匹配的图片派生证据经过串行策略，保留原文 offset 长度合同。合成 RED 5 failed / 13 passed；定向 GREEN 65 passed、exit 0。私有实际三行证据从 `retrieve_once` 到 `execute_plan`、Context 预算、来源头部和 `prepare_generation` 回放通过，`generated_caption` 类型保留；冻结旧代码在同数据上复现 `RetrievalScopeError`。回放无 Provider/DB 调用。
- `1f927e1`：仅验收 profile 将完整 metadata/chunk_metadata 按 SHA 去重到 sidecar，版本 `metadata-blobs/v1`；旧直接快照默认不变，读取时校验 SHA/长度后恢复。完整实际快照由 60,191,721 字节变为 247,357 字节 profile + 6,911,742 字节的 40 个完整 sidecar，首份总计 7,159,099 字节（减少约 88.1%），重复快照不新增 sidecar。全部原始 state 恢复后精确相等，无截断，Provider wire 不变；这是本地冻结证据回放，不是 ECS 性能基准。新归档必须包含 sidecar，需读取完整元数据的工具使用 `restore_state_snapshot`；现有 HTTP collector 使用 SSE/usage。

已有定向命令（本次报告更新未重跑）：

```text
python -m pytest tests/weknora_port/test_s88_strategy_image_evidence.py tests/weknora_port/test_s86_strategy.py -q
# exit 0: 65 passed
python -m pytest tests/weknora_port/test_s88_profile_snapshot_metadata.py tests/weknora_port/test_s862r3_http_preflight.py tests/weknora_port/test_s864_top1_default.py -k 'not actual_asgi_session_message' -q
# exit 0: 17 passed, 2 deselected
```

快照测试初始 RED 5 failed。上述排除的两项 ASGI fixture 在旧 serializer 上同样因 `MODEL_GENERATION_CAPACITY_UNKNOWN` 失败；完整中间失败日志保留，未放宽生产能力护栏或改断言，不能将这两项计为通过。HTTP50 r2b 的延迟经实际栈定位到反复写入大型 profile，最终成功完成；不将 I/O 延迟写作业务死锁。

## 7. 本批交付与资源收尾

- 公开改动经过定向源码审查、脱敏检查、机器结果解析和回执对账；本次文档更新没有重跑业务测试。
- 私有包：`S8_8_PRIVATE_EVIDENCE_FINAL_R3_1.zip`，110,444,370 字节，SHA-256 `a081265123619f14d274d0bafb81999530835128a76ffee8ced783e54d726667`。仅受控本地交付，不上传 GitHub。
- ZIP CRC、全部成员大小/哈希、别名还原及 HTTP18 元数据快照还原均通过。包含 55 个原始目标身份；其中 53 份保存的原始最终答案及生成请求可关联，另外两份明确缺失，不补造内容。原始问答、Gold、实际 Context、wire、图片及新批次账本在私有包中保留；密钥、环境文件、授权凭据和数据库数据目录不入包。
- 当前没有在途 Provider 调用；临时 API、执行器及隔离 Caption 数据库已停止，结果和 Volume 保留。生产服务保持原状。
- 此交付完成本批证据收尾，不表示 55 题目标完成，也未将不同输入/Prompt 的改善混算成达标。
