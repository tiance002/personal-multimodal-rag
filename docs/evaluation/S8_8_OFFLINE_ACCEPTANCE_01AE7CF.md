# S8.8 实施与验收

状态：**IMPLEMENTED_OFFLINE_VERIFIED_REAL_PENDING_AUTH**；当前 Cheap 绑定的正式 HTTP Quick/Smart 验收另标 **BLOCKED_MODEL_GENERATION_CAPACITY_UNKNOWN**。这不是生产可用声明。

## 1. 版本、边界与本轮结果

- 基线：`f3a541ef1cb29d16f9dd81cacfaa207d894f3e5c`；分支：`codex/s8-8-implementation-20261023`。
- 业务实现提交：`08a804ddb95c6ee5ee36716d177555e4a7d84458`；最终运行源码（含固定输入执行适配层）：`01ae7cf05767e211224edfa9c0d5e0a3267f6e82`。之后的报告提交不改变运行源码。
- 隔离镜像：`rag-s88-acceptance:01ae7cf`，ID `sha256:341647bfd12c76a1cc383d30e94121e2347445f07d46579843369673f73149c6`；使用已核验的依赖基底，断网增量构建，无下载或安装。
- 最终镜像835个 backend/scripts 文件、构建归档和 Git Blob 逐字节一致。最终归档 SHA256：`524a056fb849e123f1f40f17b260a904b76e3be919b4ff6cfc8ec14bd166d598`。早期08a镜像的CRLF比较失败及纠正证明原样保留，最终归档显式使用LF。
- 原工作树的未提交修改、旧分支、历史账本、UNKNOWN、原始材料均保留。生产 API/Worker 镜像未变；没有生产迁移或索引重建。
- **新增业务 Provider 尝试 0，实际 Token 0，新增 UNKNOWN 0。** 子代理开发调用不计入项目 Provider 评测账本。未重用历史授权。

本轮由 Sol xhigh 负责 A/C、Astra high 负责 B，主代理负责 D、共享接口集成及唯一 ECS 操作；Luna high 负责证据索引和私有打包。文件责任见 `S8_8_REQUIREMENTS_AND_OWNERSHIP.md`。

| 能力 | 实现 | 离线/隔离验证 | 本轮真实业务验证 |
|---|---|---|---|
| 前端 Quick/Smart、最大输出 Token、保存/恢复 | 已实现 | Chrome + Mock HTTP、ASGI、真实 SQL、真实请求构造通过 | 当前 Cheap 元数据阻断；未发送 |
| 图片独立模式/额度与选定资产重处理 | 已实现 | 浏览器、状态机、事务与版本隔离通过 | 新 Caption 未运行 |
| Caption length/空正文/缺结束帧保护 | 已修复 | RED→GREEN；部分内容不进入完整派生证据 | 七张旧 length 图片已冻结，待授权 |
| 固定澄清、取消与 UNKNOWN | 已修复 | SSE/History/collector、取消后重试保护通过 | 历史 HTTP112 原因仍 UNKNOWN |
| 检索派生状态丢失 | 已修复 | Rerank/父块 Merge 回归通过 | 不宣称已救回 17 题 |
| 默认 Prompt 候选 | 已实现，未启用默认 | 单变量输入冻结、合同测试通过 | 改善/退化未测量 |
| P5 特征快照与影子策略 | 已实现，默认 off | dispatch 不变；历史 20 题缺特征均 NOT_EVALUABLE | 阈值未校准 |

## 2. Quick/Smart 与输出额度：实际接线及限制

`reasoning_mode=quick|smart` 是普通 RAG 最终生成的配置，独立于 Cheap/Expensive 和旧 Agent `mode=smart`。前端使用工作的 `weknora_port` 主链，旧未实现 Agent 入口没有被解禁。没有 Prompt 编辑器。

请求显式值 > Session 保存值 > 系统默认。0 解析成有限默认值，不能解释成无限；整数、额度、上下文与能力均由后端检查。前端可保存、刷新回读、恢复默认、查看有效值；执行中不能改写已经准入的快照。问答设置保存在现有 Session 字段，每轮的请求值、有效值、版本和真实 Provider 参数映射进入现有运行记录与历史投影。

首次生成、已有 fallback 和合法升级继承同一模式/额度。QU 的思考与输出设置未改，P5 角色选择不由 Quick/Smart 决定。图片设置单独保存到已有 KB 图像配置，明确传入 `ChatOptions`，不改变 MinerU 内部配置。

主要位置：`generation_settings.py`、`models/generation_capabilities.py`、`http_api.py`、`runtime.py`、`service.py`、`generation/coordinator.py`、`turns/`、`storage/`；前端 `GenerationControls.tsx`、`ImageTaskDetails.tsx` 及 API/SSE 适配器。

**最后核对发现的真实绑定阻断：** 当前 Cheap 是 `siliconflow / Qwen/Qwen3.5-4B`。实际 `SourceCatalogue.resolve` 没有覆盖条目，得到 context/output=0、reasoning=false、cataloged=false。Quick 和 Smart 均在 transport 打开前返回 `MODEL_GENERATION_CAPACITY_UNKNOWN`。新增两种模式的实际身份回归证明 sent=false、transport.opens=0。先前 397B fixture 仅证明构造协议，不能作为 4B 的可用性证明。

SiliconFlow 的官方家族说明支持 256K 上下文；其通用 API 说明 `max_tokens` 不包括思考。已保存 2048/off 成功请求只证明这个历史点，不证明托管 4B 的最大输出或 Smart 总输出界限。没有凭此编造上限、换模型或绕过保护。现有 `ConfiguredRole.parameters.spec_override` 可承载有依据的能力配置；本轮没有向生产写入覆盖值。来源：[家族公告](https://www.siliconflow.cn/news/yg6n19y2g6frnp4koxu8ye48)、[Chat API](https://docs.siliconflow.cn/docs/api/chat-completions-post)。

因此必须区分：**界面至持久化、快照、实际请求 renderer 的接线通过；当前生产 Cheap 身份下收费 HTTP 验收没有通过。** DeepSeek Flash 的 Quick/Smart 参数已离线构造，真实模式效果另列待授权。未知能力不自动换成 Expensive。

## 3. 可靠性修复与独立审查

### Caption 与局部重处理

`image_multimodal.py` 按规范化结束语义区分正常、length、空正文、缺结束信号、取消和失败。保留真实 usage 与部分正文；usage 缺失保留 null。Caption 不完整不生成完整 `image_caption` 索引；已有 OCR 在 Caption 调用前持久 checkpoint，失败不清除 OCR。

选定资产重处理绑定 SHA、版本、路径、配置版本与幂等键，只处理选中的派生项。新结果和必要索引准备成功后在事务内切换；失败保留旧可用证据。迁移 `017_image_selected_retry.sql` 仅增加 `retry_key` 并扩展已有唯一约束，旧任务的空 key 仍有原唯一性。初始化入口已按 010→017 顺序接线。

独立审查找到并关闭一项高影响问题：取消不能把尚在 `sending` 的 Caption/Embedding 简单改成 `cancelled`，再允许用新 key 重发。现改为 UNKNOWN，并独立检查未决发送 checkpoint；成功的 Embedding checkpoint 只在最终切换事务提交时完成。RED 5 fail/1 pass → GREEN 6 pass，关联 42 pass；真实 PG 也验证取消 SQL 与原文保持。

### 固定澄清与取消

固定文档澄清最多展示 5 个、每个 120 字符的标题，完整资料仍由既有选择 UI 访问。只对显式服务端固定完成标记放行 collector，不放松模型结束合同。

取消记录实际观测到的发起者、阶段、deadline 类型、发送标志和结算信息；缺失字段不补造。SSE 与 History 同源。HTTP099 的误拒已用重放/Mock 复现并修复；HTTP112 历史发起原因仍为 UNKNOWN，新增记录能力不等于补齐旧证据。

### 派生证据过滤

`retrieval/rerank.py` 与 `retrieval/merge.py` 同时保留状态过滤，防止父块合并丢失状态后把截断文本再次当作旧版完整来源。RED 5 fail → GREEN；没有完成状态的历史 payload 保持兼容，未批量重写旧索引。摘要生成也不注入显式未完成派生内容。

## 4. 17 个定向证据链样本

Gold ordinal 为 0 基，HTTP 为 1 基；按唯一 case_id 共 **17** 条。每条实际 Rerank passage/index 的单批映射均与冻结输入一致，未发现所怀疑的跨批错位或 passage 偷换。下表不是 17 个“代码 Bug”或 17 个已修复错答。

| 唯一标识 | HTTP / Gold ordinal | 确认断点与处置 |
|---|---|---|
| SciFact 1302 | 13 / — | 保存 Search 无 qrel 来源；必要事实损失仍未证实 |
| SciFact 1304 | 14 / — | qrel 正文 .000374、标题 .001005；无关 top1 .039394 被 floorless 保留；无映射错误 |
| SciFact 1305 | 15 / — | qrel 标题 .013604、正文 .001967、摘要 .007031；无关 top1 .092956；无映射错误 |
| SciFact 1330 | 16 / — | qrel 被实际 Rerank 阈值淘汰；未改默认阈值 |
| SciFact 1331 | 17 / — | 同上；来源缺失不直接等于答案错误 |
| SciFact 241 | 20 / — | 部分 qrel 丢失，另一个正例来源保留 |
| SciFact 242 | 21 / — | 实际 Search30/Rerank1；摘要只作定位，来源投影以 SUMMARY_LOCATOR_ONLY 移除 |
| SciFact 434 | 25 / — | qrel 低于阈值；替代来源事实充分性未判定 |
| SciFact 435 | 26 / — | 同上 |
| SciFact 949 | 39 / — | qrel 低于阈值；未擅自改写原问题拼写 |
| SciFact 978 | 40 / — | qrel 低于阈值；保留原问题口径 |
| LongBench URL 样本 | 82 / 18 | `/21` 存在 Search C23 的另一来源版本，分数 .079362；未选中，不是已证实的解析裁切 |
| LongBench 数值样本 | 97 / 33 | 必要 35.1% 在 Search C4/C7，.108380；在 Rerank 丢失 |
| LongBench 数值样本 | 100 / 36 | 必要 97.7% 以全角字符在 C2/C4，.128034；在 Rerank 丢失 |
| MML0684 | 111 / 47 | 保存 Search 无目标调查事实，最终收到别的 PDF；原实验未显式绑定文档 |
| MML0431 | 114 / 50 | 有泛化网络片段，未证明匹配目标人脸图；不能据此冒称证据充分 |
| MML0430 | 116 / 52 | 无完整幻灯片图像清单；局部 Context 不能证明全篇计数或 0 |

SciFact242 的旧汇总来自错误的 root 空快照。新恢复使用匹配 case/qid/问题/scope/哈希的子检索原始记录，并验证30条 passage。旧文件原样保留，新增版本化纠正。其保存 `merge_results` 是来源投影后的最终选择，不能冒充未保存的投影前完整 Merge 列表。

对 63 题执行的 `search_anchor_v1` 候选在相同保存片段槽位预算下改善 1304/1305，退化 SciFact1135、247 与 MIRACL692002#0，58 不变；**NOT_PROMOTED**。这是来源相关性，不是新答案救回率。没有添加 Gold 别名、日期、目标文档或 URL。

### 保存结果复算（来源去重后；grade0 非正例）

| 数据集 / 阶段 | 可计算分母 | Hit@5 | Recall@5 | MRR（保存顺序） |
|---|---:|---:|---:|---:|
| MIRACL Search | 23 | .956522 | .905797 | .774396 |
| MIRACL Rerank | 23 | 1.000000 | .985507 | .786232 |
| MIRACL 最终 Context | 23 | 1.000000 | .985507 | .789855 |
| SciFact Search（纠正后） | 40 | .825000 | .800000 | .755193 |
| SciFact Rerank（纠正后） | 40 | .775000 | .750000 | .692500 |
| SciFact 最终来源选择 | 40 | .750000 | .737500 | .669583 |
| SciFact 最终 Context | 39 | .769231 | .756410 | .686752 |

SciFact242 的最终 Context 缺失，Context 分母排除它；已证明为空的最终来源选择则在40题分母中计0。没有推测缺失排名，也没有编造这两套的官方 QA 准确率。

## 5. Prompt 候选与 P5 影子结果

`generation/prompt_candidates.py` 的 `s88-source-precision/v1` 只补四条短规则：不同来源步骤分别表达；事件/时间与结论一致；来源冲突与无据补充；来源人物不冒称当前用户。保留来源协议与有效旧 Prompt，不改变默认。

固定候选题：Gold ordinal **2、4、13、34**；正确控制 **39、49**。六份原始实际输入保持问题、History、Scope、Context、引用句柄、模式、温度和输出上限，仅变一个 System 消息。新 Cheap 若 WRONG/PARTIAL，各预留一次同输入 Expensive 配对；不以旧 S8.7 的贵模型回答代替新配对。

**本轮候选改善数、退化数、救回率均 NOT_RUN，不能写成 0 或 PASS。** S8.7 已有三个有证据改善案例包含原 PARTIAL，未夸大成三个完全错误。完整问题、Gold、旧 Cheap/Expensive 回答和 Context 在私有包；公开报告只列标识与方法，不公开私有原文。新答案尚不存在。

`generation/shadow.py` 记录生成前允许字段：校验后的 QU 结构、明确对象、片段数、带版本的文档数、输入负载、歧义状态与实际检索分支；原文仅使用哈希。未知不当简单，也不自动送贵模型。候选只对满足明确条件的 2–3 对象、2 个版本化文档的比较/并列结构输出影子建议；实际 dispatch 仍由原 P5 决定。

历史20题没有足够的当时生成前特征，**20/20 NOT_EVALUABLE**，不得用今天的 Gold/贵模型结果补造特征。无法证明选中多少受益题或控制题，新增调用比例也未测量。相同输入的 dispatch 不变测试通过，生产阈值仍 **0.35**。本轮没有调整阈值的证据。

## 6. 执行命令与结果

以下测试集合重叠，不能相加为独立测试总数。未实现的命令不得报告通过。

| 验证 | 实际结果 | 关键证据 |
|---|---|---|
| 最终10模块组合回归 | exit0，291 passed | 私有 `integration-tests.log` |
| 增补实际 Cheap 身份保护 | exit0，27 passed（含两种模式） | A 机器结果 |
| 现有/新 HTTP profile 接线 | exit0，91 passed | 受影响 profile/settings 测试 |
| 实际 Chrome，Mock HTTP | exit0，27 passed | A 机器结果；不是收费浏览器 E2E |
| TypeScript/Vite | exit0，276 modules | `npm --prefix frontend run build` |
| 08a业务源码隔离镜像内真实 PostgreSQL | exit0，2 passed | `pg-final-image.log`；Session+image retry，非 Mock |
| 17样本/63回放 | exit0；17映射一致，63回放 | C 私有机器记录 |
| 最终镜像源码核对 | exit0，835文件与Git逐字节一致 | `image-source-verification-01ae7cf.json` |
| 固定输入适配层定向回归 | exit0，26 passed | 新鲜授权/账本、条件配对、STOP、真实wire一致性 |
| 最终受影响模块冒烟 | exit0，53 passed | `final-smoke-01ae7cf.log`；含上述26项 |
| 最终镜像断网渲染 | exit0，23个实际请求 | `fixed23-final-image-offline-r2.log` |

组合命令（在本轮工作树，已配置项目 Python）：

```powershell
$env:PYTHONPATH=(Get-Location).Path
& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest tests/weknora_port/test_s88_generation_settings.py tests/weknora_port/test_s88_reliability.py tests/weknora_port/test_s88_shadow_prompt.py tests/weknora_port/test_s88_image_evidence_status.py tests/weknora_port/test_s88_retrieval_replay.py tests/weknora_port/test_generation.py tests/weknora_port/test_turn_storage.py tests/weknora_port/test_http_api.py tests/weknora_port/test_image_processing.py tests/weknora_port/test_pipeline.py -q --tb=short -p no:cacheprovider --basetemp 'E:\RAG quention\var\reports\s8-8-20261023\integration-temp'
```

镜像内 PG 命令：

```sh
python -m pytest tests/weknora_port/test_s88_generation_pg.py tests/weknora_port/test_s88_image_retry_pg.py -q --tb=short -p no:cacheprovider
```

PG测试在08a业务源码镜像内运行；01ae仅新增验收适配层与测试，未改变该SQL和业务链。PG 使用独立临时集群、显式 DSN/system identity、随机 schema，测试最终完整回滚；运行后临时容器已停止移除。API/Worker 生产 Volume 没有挂载到测试容器。

保留失败证据：Caption RED、派生 Merge RED、取消/UNKNOWN RED、Windows C: 临时路径触发现有 guard、旧 UI selector 错误、首次测试依赖缺失及镜像 CRLF 比较。最后两个旧 `test_conversation_configuration.py` 失败已在基线 f3a541 独立重现：expected greeting / actual kb_search；expected LOCAL / actual empty。未改其期望，未算本轮通过。

## 7. 真实环境前置证明与集中申请

只读回查当前20份固定 Context：**20/20 eligible、191/191 Chunk**，Scope、版本、当前原文、云许可一致；模型绑定只读回查通过。图片 **9/9** 当前版本/资产 SHA/实际文件一致。两张正常控制的历史完整 body 从真实模板重建，其 canonical body hash 与历史收据精确一致；这不同于声称原始字节文件当时已保存。

七张旧 length 图保留 Prompt、图像、temperature=.1 和 max_tokens=5000，仅新增明确关闭 thinking。旧 body 没有 thinking 字段，不能把“缺字段”解释成已经关闭。离线图片上下文检查7/7通过；原始 base64 传输大小与模型图片输入上界分开，原费用预留配方未被降低。新的9图质量与真实 usage 尚未产生。

本次集中申请仅包含当前可准入的 **最多23次**：6次候选 Cheap、最多6次条件 Expensive 配对、7张旧 length+2张正常图片、2次受支持 DeepSeek Quick/Smart 固定证据控制。固定请求输入与输出风险上界合计 **323,458 Tokens**，不是实际用量或费用；申请实际 reported Token 停止线 **500,000**。费用 **UNPRICED**，以可核验实际结算为准。

原计划3次正式 HTTP 暂不申请或发送，原因是第2节当前 Cheap 能力阻断。旧 HTTP renderer 的65,257预留结果只证明旧合同，不证明显式产品模式可用。没有通过绑定另一模型冒充当前 P5 验收。

原正常 Runtime/Registry/SendGate/LocalExecution 路径、新 ID 与独立账本、严格串行、独立持久目录和资源隔离继续使用；不设整批40分钟限制。一个已准入在途请求可能跨过实际 Token 停止线，单次输出仍有限。UNKNOWN/权限/用量不明即停止且不重试；语义错答保留并仅做批准的一次匹配配对。Langfuse 在一条已安排的固定生成上核验 metadata-only，不额外发送观测专用调用，也不冒称正式 HTTP RAG 已通过。

`scripts/s8_8_fixed_acceptance.py` 是本轮固定输入执行适配层，复用正常 Registry、SendGate、LocalExecution，未另造Provider适配器。父账本先原子记录准入；一题单次子账本，UNKNOWN全局停止、不完整响应停止所属实验分支，所有已发生usage保留。条件配对逐项校验同消息、温度、输出上限、流式选项和关闭思考；模型实际身份与捕获wire再次对账。旧S8.7脚本只支持20次纯文本，不能原样执行本轮23项，故做此局部适配。独立审查无确认重要发现。23项来源回读函数在ECS真实只读运行通过，发送时的完整Runtime回调尚未收费执行。

最终profile：`0a3fb4be39515d3598179640c65bc7a69822e8892b7b45c0b5ee2fcf143ff384`；manifest：`387b9ffabed3cd6a0a6189749057e9341e61c39305a6cb1478c2fb103f531ef3`。

详细授权摘要与冻结哈希见 `S8_8_REAL_ACCEPTANCE_AUTHORIZATION.md`；所有正文和请求体仅存私有包。截止本报告没有创建新收费 Grant。

## 8. 发布、回滚与剩余问题

本轮只构建隔离 backend 镜像，前端本地构建通过；没有启动新正式 HTTP 服务或更新生产镜像。后续获批准且消除能力阻断时，先在隔离环境应用017、核验当前 Source/Model identity，再通过正常 Session/Message/Settings API验收。无需全库 OCR/Embedding。

回滚入口：原生产 API/Worker 镜像未变，可继续原 Compose 配置；隔离临时进程可停止，证据目录保留。若未来生产应用017，回旧 Worker 前先停止选定重处理接单并排空新任务，保留兼容列和历史尝试，不删除 retry 行或降级唯一约束以掩盖记录。

真正阻断：当前 Cheap 元数据/Smart 输出合同、尚缺本轮收费授权、未验证的真实产品 HTTP。已界定的剩余质量问题：低分但必要证据、未绑定文档、全篇图像计数、历史特征缺失、旧任务资产元数据不足。未将这些自行批准延期，也未靠更换评分/Gold宣称完成。

下一个动作是本报告集中授权范围内的真实固定输入复验；当前绑定的 HTTP 需要先取得可靠能力依据。没有开启新 Goal、新 Agent 系统、HyDE 或额外检索循环。

## 附：最终增量命令与限制

```powershell
$env:PYTHONPATH=(Get-Location).Path
& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest tests/weknora_port/test_s88_fixed_acceptance.py tests/weknora_port/test_s88_generation_settings.py -q --tb=short -p no:cacheprovider --basetemp 'E:\RAG quention\var\s88-final-smoke-01ae7cf'
```

退出0，53 passed in 4.11s。独立profile接线命令为同一Python执行 `-m pytest tests/weknora_port/test_s88_acceptance_profile.py tests/weknora_port/test_s865_http_profile.py tests/weknora_port/test_s861_api_acceptance.py tests/weknora_port/test_s862r3_40_acceptance.py -q --tb=short -p no:cacheprovider`，使用独立E盘basetemp，退出0、91 passed。最终镜像使用 `docker run --rm --network none --cpus 1 --memory 1280m`，只读挂载冻结文件后运行 `python scripts/s8_8_fixed_acceptance.py check --manifest /frozen/manifest.private.json`，退出0、23项PASS、Provider0。第一次SSH脚本传入路径末尾CR而失败，修正命令传输后通过；两份日志均保存。

本轮没有对新Prompt、七张图或正式HTTP产生新的质量标签。历史全文与图像仅放私有证据包；公开报告不展示私有问答原文。源于同一旧证据的回放不得冒充真实新模型效果。
