# S8.5 多轮对话检索与受控多跳检索

阶段 0–4 的实现、分阶段提交及本轮测评已完成。多轮问题补全、事实追问重新检索，以及显式开启的第一跳验证/已有池第二跳补救均有真实执行证据。Frost 现有原文只能证明机构所在地，不能证明研讨会举办地；最终问答不能认定完整正确。负责人里程碑验收、生产发布与 GitHub 推送不包含在本交付中。

| 能力 | 离线及记录重放 | 真实验证与限制 |
|---|---|---|
| 当前授权 Session 的完成历史、有限指代改写 | 隔离、完成筛选、q0/条件保护、失败回退已验证 | C3 第二/第三轮正确补全 Beaumont，Search/Embedding/关键词/Rerank 使用有效查询；真实 PG 历史、异用户拒绝/空 Session、7 轮 SSE 回放无重复调用 |
| 纯格式转换/要求原文依据 | 前者免检索；事实/来源及未知意图重新检索 | C3 T6 无 Search/Embedding/Rerank；T7 重新检索并回读 Leyden jar 原文引用。T6 转换的是旧 T5 错误回答，不能计为事实正确 |
| 歧义与未完成响应保护 | 原始 C3 T5、T1/T3 经当前代码重放通过；保留全部旧失败结果 | 最后两处保护未再次跑完整 7 轮云端对话；不能把旧源 C3 全部完成写成回答质量全部合格 |
| 第一跳/身份/Q1/有界 BGE/原文 Context | 句子 ID、3 前驱、2400 字符、对象/时间/地点反例、账本唯一性已验证 | A2/B3 均真实通过；B 身份链验证通过，但最终 Context 缺完整姓名锚点 |
| 最终多跳答案 | 机构地点与事件地点保持不同状态 | GA3/GB3 是已结算真实输出的当前原文投影 + 新 Cheap 生成；不是新检索端到端。最终问题支持状态保持 UNVERIFIED，引用及语义结果见后文 |

主要机器证据：[继续执行结果与逐请求轨迹](S8_5_CONTINUATION.results.json)。早期离线轨迹和首次受限真实 STOP 分别保留在 [离线结果](S8_5_IMPLEMENTATION_REPORT.results.json)、[首次真实结果](S8_5_REAL_EVALUATION.results.json)，均未以新成功覆盖。

## 基线与阶段 0

实际初始 HEAD 为 `main@0606063`，其尚不包含 `backend/weknora_port`。隔离分支 `codex/s8-5-conversation-bridge-20261021` 从它建立，合入已提交 S8.4 分支 `9931ffe`，集成提交 `7e5fb4a`。两个 ECS skill add/add 冲突保留当前 main 的增补案例；未改历史 S8.4 输入、Gold、收据、报告。主工作区原有 `.serena/project.yml` 保留。

实际调用链：服务端准入 → `prepare_turn()` 读取历史/记忆 → `understand_query()` → `resolve_intent()` → Search → Rerank → Merge → TopK → 可选来源扩展/Bridge → Context → Generation → 引用注册/持久化/SSE。恢复请求回放已有运行，跳过 `prepare_turn()`。

下表保留阶段 0 开始时的只读审查快照；其中的缺陷和“待执行”状态在后续阶段逐项处理，当前结果以上方能力表与阶段记录为准。

| 检查点 | 已实现 | 缺陷 | 缺少验证 |
|---|---|---|---|
| Session 历史 | `prepare_turn` 调用 `OrdinaryHistoryService.load`，SQL 和 `process_history` 均按 tenant/subject/session 过滤，按时间取 max_rounds | 未排除未完成、取消/错误和当前 request；异常大范围吞掉权限/超时 | 本轮隔离真实 PG/多用户测试未运行 |
| Query Understand | 收到历史并保留 `state.query=q0`，复用一个已有改写调用 | 仅非空字符串校验；不能阻止条件遗漏/歧义实体选择 | 当前模型在 S8.5 新合同上的真实输出未验证 |
| 检索查询 | Embedding、关键词和 Rerank 已用 `rewrite_query` | 需增加失败回退轨迹及对有效查询的断言 | 现有真实记录不替代新合同验收 |
| 意图保护 | 有知识库/来源约束规则、附件前提 | 任意有历史的 `follow_up` 可绕过检索；事实性追问被误放行 | 需模拟模型误分类回归 |
| Merge / Context | 当轮证据注册和来源映射已具备 | Merge 会注入历史引用内容，可能替代当前检索 | 需证明旧引用不进入最终 Context |
| Bridge | 默认 off、显式模式执行；单 Cheap/单有界 Rerank/当前原文回读 | 旧偏移合同、对象截断/时间归属/嵌套地点误判、并列谓词误拒、代词不全 | S8.4 真实仅 2 次 Cheap 拒绝，0 BGE，不能认作链成功 |
| 运行账本 | 双侧结算和原始收据保存 | `validate_settlements` 缺执行侧键唯一性及键集合相等检查 | 新反例需先 RED 再 GREEN |
| SSE 恢复 | replay_only / 已有 active 运行避免重入；持久化事件回放 | 暂未发现需重写的缺陷 | 本轮离线恢复断言待执行；不宣称浏览器真实验证 |

阶段 0 只读检查的相关模块包括 service、conversation/history/types/configuration、retrieval/query/search/rerank/pipeline/merge/bridge_rescue/context、generation/context 和 storage/memory/turns/history。未重构或新增主链。

## 阶段记录

以下阶段 1–3 和“首次”阶段 4 保留当时的执行记录；历史 NOT RUN 与初始 STOP 不代表后续批次未执行。当前结论以“阶段 4 继续执行”及报告开头为准。

后续逐阶段记录修改、RED→GREEN、命令退出码、离线与真实结果。未实现的命令不得报告通过。

### 阶段 1：多轮对话最小修复（OFFLINE_PASS）

修改 `conversation/history.py:process_history/load`、`conversation/types.py:StoredMessage`、`storage/memory.py:_message/recent_messages`、`service.py:prepare_turn`：复用原存储的完成/终态字段，筛选完整成功轮次，明确排除本轮 request，保留 tenant/subject/session 过滤和时间顺序。权限、Scope、取消、运行超时和读取超时传播；普通可选读取错误保留原降级合同。没有新增表。

修改 `retrieval/query.py` 与 `PipelineState`：保留 q0，只接受可核实的字面指代替换，保护当前问题的其余条件；未知/歧义/空值/解析或调用失败回到 q0。追问的模型 intent 仅作建议；完整匹配少量“上一条回答转表格/翻译/换说法”操作才免检索，其余事实性追问保守检索。请求内保留 q0/rewrite，诊断轨迹仅保存 hash、原因和历史轮次数，避免公开日志保存历史正文。

修改 `retrieval/merge.py:merge`，从活动链去掉 `inject_history` 调用；既有辅助函数保留，历史引用不再绕过当前 Search/Rerank 进入 Context。`generation/context.py` 增加历史回答的证据边界提示。Embedding/关键词/Rerank 原有有效查询传递逻辑未重写；离线验证检查实际实参。

改写有意采用窄合同：任意同义改写和只在历史回答中出现、无法唯一确认的先行词都保守回退；不宣称通用语义指代解析能力。需要真实模型验证其实际改写接受率。

同一 Session 的历史还须检查当轮原有 `knowledge_base_scope/document_scope`。双方消息快照不一致时抛出权限错误；缺失来源、跨当前 KB、当前限制文档而历史未限文档、或引用与原快照冲突的历史整轮排除，SSE 只记录排除数量和固定原因。当前门控继续检查当前 KB 的外发权限，不扩大 Scope。旧记录若没有可核查快照，将失去多轮补全能力并回退当前问题检索，这是保守的兼容限制。

RED：初始多轮新增套件 33 failed / 4 passed，exit 1（其中 SQL 测试初稿缺 identity-guard，不能计为实现缺陷；已另用实际基线方法 AST 做正确 RED）；补充符号/编号/歧义/部分响应/SSE 反例 7 failed；关闭历史反例 1 failed；历史外发来源反例 9 failed，均 exit 1。初始测试 import 拼写错误 exit 2 单独保存，不算功能 RED。

最终关联 GREEN 命令：

```powershell
python -B -m pytest -q -p no:reporter -p no:cacheprovider tests/weknora_port/test_s8_5_conversation.py tests/weknora_port/test_conversation.py tests/weknora_port/test_memory_storage.py tests/weknora_port/test_pipeline.py tests/weknora_port/test_p21_boundaries.py tests/weknora_port/test_m31_source_constraints.py tests/weknora_port/test_service.py tests/weknora_port/test_fallback_evidence_contract.py
```

**273 passed，exit 0，3.24 秒**，`var/reports/s8-5/stage1/history-origin-green.txt/.exit`。生成边界另行 RED 1 failed → GREEN **85 passed，exit 0**：`python -B -m pytest -q -p no:reporter -p no:cacheprovider tests/weknora_port/test_s85_generation_history_boundary.py tests/weknora_port/test_generation.py`，证据 `generation-{red,green}.txt/.exit`。重复过渡运行不累加为测试总量。

### 阶段 2：证据校验与账本修复（OFFLINE_PASS）

修改 `retrieval/bridge_rescue.py:validate_extraction/_relation_ending/_location_facts/PRONOUN`：完整对象后只允许直接属于该关系的时间短语与少量无新主体的并列谓词；对象后多出的 Annex/II/Foundation、后续 moving/另一个人物的时间均不准入。地点匹配从句首的完整实体开始，只允许直接地点谓词或窄范围的机构类别补语；rival-of、laboratory 嵌套主体、否定和不确定句拒绝。补全英语/中文第一、二、三人称代词。原文 span、关系句式、主体身份分开记录；Frost 原句已证明 span/form 有效而主体仍未验证。

修改 `scripts/s8_4_bridge_ecs.py:validate_settlements`：PG/执行两侧都按 `(request_id, capability)` 唯一建键，要求键集合完全相同，之后再逐项对比模型、wire 和真实 usage。仅修复校验函数，不改冻结收据和历史结果。

RED：新增语义/账本测试 **34 failed / 58 passed，exit 1**；GREEN（增加两个边界测试后）**183 passed，exit 0，1.63 秒**。

```powershell
python -B -m pytest -q -p no:reporter -p no:cacheprovider tests/weknora_port/test_s85_bridge_semantics.py tests/weknora_port/test_bridge_rescue.py tests/weknora_port/test_bridge_pipeline.py tests/weknora_port/test_s8_4_bridge_ecs.py
```

证据：`var/reports/s8-5/stage2/{red,green}.stdout.txt/.stderr.txt/.exit-code.txt/.command.json`。阶段 2 使用旧偏移合同隔离复现语义缺陷；阶段 3 随活动合同迁移工程测试，旧冻结 JSON 和真实响应保持原样。

### 阶段 3：句子 ID 与有界身份/第二跳（OFFLINE_PASS）

`bridge_rescue.py` 的活动合同统一改为 **`sentence-id/v2`**：程序从当前原文切句，建立稳定 ID、原始 Chunk/版本/Unicode 起止位置/逐字正文映射；Cheap 只返回选中 ID 和候选关系字段，程序派生偏移。旧偏移/quote/chunk ID 作为额外模型字段拒绝。切句保护 B.A./M.F.A./Ltd. 等源文缩写；重叠原文须逐字一致才去重。主来源仍最多 2 个，代词来源沿真实 pre 指针最多回溯 3 步，验证当前版本/Scope/连续 index/双向指针；去重后的句子正文总计 ≤2400 字符。

新增窄范围 `bridge_identity.py:prove_identity`，从原文明确姓名、唯一姓氏的句首重建与连续第三人称句式证明身份。第一/第二人称、同姓歧义、其他人物/引语/关系从句、缺链/版本或重叠冲突不准入。姓名只在问题中出现不能成为原文身份证据。不引入通用 NLP。身份证据在源文存在、以及原句在最终普通 Context 存在，分开校验；没有将缺失姓名原句拼进另一个文档或自动扩充第二跳配额。

程序继续仅从经过验证的 object/target_attribute 构造一个 Q1，重用首轮阈值淘汰池，最多 8 候选/一次 BGE/1 个 ≤1600 字符补充；无匹配候选记录 `SECOND_HOP_SOURCE_NOT_IN_POOL`，不增加全库 Search。首轮分数与三组普通排名数组不变。`retrieval/context.py` 与 `generation/context.py` 共用补充证据限定属性。预算预检不记录虚假的 Context 准入；生成编码超预算撤回补充并记录最终未验证。

独立审查补充了真实离线反例：time 字段吞入 `but not in 2012`、after/before 条件被年份集合忽略、Paris or Rome/画中/小说地点、headed for 的方向含义、机构 location 被当作活动 held 关系。均保存 RED 后修复为支持明确年月日/年份范围、直接对象语法和物理地点尾语；最终支持仅限少量可核实的字面 location 问法，陌生举办/建筑/地址等请求仍未验证。

Frost A/B 离线新合同均能证明姓名与首跳，Q1 为 `Where is The Frost Place Advanced Seminar located?`，初始池确有第二跳，Mock BGE 可补入原文。A 的姓名链在最终普通 Context 内完整，B 缺完整姓名原句。两臂第二跳都是 **机构所在地线索**；`ENTITY_BRIDGE_VERIFIED=false`、`FINAL_ANSWER_SUPPORTED=false`。这是程序/Mock 合同验证，不是本轮真实 Cheap/BGE 成功，也没有生成最终答案。

RED：句子 ID 初始 13 failed；身份模块最初缺模块 collection error 单独标注，不能算功能失败；随后身份反例 6 failed/57 passed；关系审查补充 4+3+2 个失败反例。最终一次关联命令：

```powershell
python -B -m pytest -q -p no:reporter -p no:cacheprovider tests/weknora_port/test_s85_bridge_sentence_ids.py tests/weknora_port/test_s85_bridge_identity.py tests/weknora_port/test_s85_bridge_semantics.py tests/weknora_port/test_bridge_rescue.py tests/weknora_port/test_bridge_pipeline.py tests/weknora_port/test_s8_4_bridge_ecs.py
```

**271 passed，exit 0，1.90 秒**，`var/reports/s8-5/stage3/final-green.txt/.exit`。身份独立验证 63 passed，输出位于 `stage3-identity/green.*`；重复子集不累加。活动工程测试跟随新合同迁移，冻结 S8.4 输入/Gold/收据/真实结果未改。

### 阶段 4：首次离线对照与受限真实 STOP（历史记录）

本节保留最初 f5b0f6e 代码的结果；后续修复、真实多轮及 A/B 结果见“阶段 4 继续执行”。本节的 NOT RUN 不代表后续批次未执行。

一次跨阶段关联回归 **629 passed，exit 0，5.16 秒**，未把子集/重复运行累加。命令：

```powershell
python -B -m pytest -q -p no:reporter -p no:cacheprovider tests/weknora_port/test_s8_5_conversation.py tests/weknora_port/test_conversation.py tests/weknora_port/test_memory_storage.py tests/weknora_port/test_pipeline.py tests/weknora_port/test_p21_boundaries.py tests/weknora_port/test_m31_source_constraints.py tests/weknora_port/test_service.py tests/weknora_port/test_fallback_evidence_contract.py tests/weknora_port/test_s85_generation_history_boundary.py tests/weknora_port/test_generation.py tests/weknora_port/test_s85_bridge_sentence_ids.py tests/weknora_port/test_s85_bridge_identity.py tests/weknora_port/test_s85_bridge_semantics.py tests/weknora_port/test_bridge_rescue.py tests/weknora_port/test_bridge_pipeline.py tests/weknora_port/test_s8_4_bridge_ecs.py
```

输出与退出码：`var/reports/s8-5/stage4/affected-offline.txt/.exit`。额外缺池分支 **1 passed，exit 0**：`python -B -m pytest -q -p no:reporter -p no:cacheprovider tests/weknora_port/test_s85_bridge_missing_pool.py`，证明已验证 Q1 没有初始候选时停止，不运行第二次 Hybrid/Embedding。

可复现审计命令：

```powershell
python -B scripts/s8_5_offline_audit.py --output docs/evaluation/S8_5_IMPLEMENTATION_REPORT.results.json
```

**exit 0**，状态 `OFFLINE_CONTRACTS_PASS_FROST_FINAL_UNSUPPORTED`，产出 5 个多轮实参轨迹和 6 个冻结案例的独立 S8.5 轨迹。工具使用当前真实 Python 路径与 Mock 模型/存储，未调用数据库/云端/ECS，Mock usage 不计作真实 Token。4 个负控制均 0 Cheap/BGE/补充；Frost 两臂各 1 Mock Cheap/1 Mock BGE，均无最终活动地点支持。

| 离线多轮场景 | 实际验证 |
|---|---|
| 首轮 → 第二轮代词 → 第三轮代词 | 检查 Embedding/Hybrid 关键词/Rerank 的实际实参均为经过校验的独立问题 |
| 切换到逻辑回归后追问 | 使用新话题；不回扫越过新的独立问题 |
| 多实体比较、只在旧回答出现的指代 | 回退 q0；重新检索，不任意选择 |
| 格式转换/翻译、要求原文依据 | 前者严格免检索；事实与来源请求检索 |
| Tenant/user/Session、KB/document、完成/取消/当前轮 | 通过字段/SQL 录制与真实服务组合 Mock 检验隔离；真实 PG 历史仍 NOT RUN |
| 改写失败/空值/部分响应/条件改变 | q0 回退 + 固定原因/hash |
| 刷新/SSE/重启回放 | 同一 durable fixture 运行只调用一次改写/生成；浏览器与真实 Redis 仍 NOT RUN |

新运行器 `scripts/s8_5_bridge_ecs.py` 专用于本轮精确 Frost A 计划；复用正式 Registry/Gate/LocalExecution，不复用旧授权/fence/次数。58 个运行器安全测试通过，exit 0，1.47 秒：`python -B -m pytest -q -p no:reporter -p no:cacheprovider tests/weknora_port/test_s8_5_bridge_ecs.py`。独立审查发现“本地准入拒绝被算成实际调用/UNKNOWN”的报告缺陷，RED 8 failed 后修复；现在分别记录尝试、明确未发送、已结算和可能已发送未知，保留 STOP 与占用。证据 `runner-summary-{red,green}.*`。

真实计划见 [S8_5_REAL_EVALUATION_PLAN.md](S8_5_REAL_EVALUATION_PLAN.md)。精确范围批准原句为“批准此精确范围并执行”，对应 `call_77e140469d2c4edaa53239bf4adc979b:0`。源代码基线 `f5b0f6e732c7951cae5b993c45b3b92f5a0b785e`，在已有 ECS 容器 `rag-s8-api-1` 内使用新目录 `PYTHONPATH` 执行一次性诊断进程；没有部署 API/Worker。真实试验复用冻结 Search/首轮 Rerank 输入并回读当前原文，仅测 Bridge，不是完整线上 RAG 或多轮评测。

#### 4.3 实际真实调用与停止结果

Stage：`20261021-6c5bee3541044d649d70629ac0514b20`。请求公共前缀：`s85-20261021-6c5bee3541044d649d70629ac0514b20/frost-A/`。

| 请求后缀 | 模型 | 实际次数 | 输入 Token | 输出 Token | 合计 Token | 状态 |
|---|---|---:|---:|---:|---:|---|
| `bridge-extract` | SiliconFlow / `Qwen/Qwen3.5-4B` | 1 | 1103 | 134 | 1237 | HTTP 200；`finish_reason=stop`；`SETTLED`；字段校验失败 |
| `bridge-rerank` | SiliconFlow / `BAAI/bge-reranker-v2-m3` | 0 | NOT RUN | NOT RUN | 0 | 未准入 |

原始 Provider 响应、PG 收据与执行账本的 `(request_id, capability)` 唯一、键集合完全一致，wire hash 与 Token 完全相同。错误分支 `final.usage_by_capability` 原值为 null，以上用量直接由三份原始记录核对得出，没有补造聚合值。**实际总量仅 1,237 Token**；5,248 是门控预占，不能当作实际消耗。没有费用估算。

模型确实选中 `s-c9a81754f7673886cbb035bf`，程序映射到原始 Chunk `ec07a1fd-8a8e-4f23-a7d6-65d385d2343b` 的 Unicode 区间 `[155,311)`：

> She served as the Director of The Frost Place Advanced Seminar from 2007–2010, and serves on the faculty for the Stonecoast MFA Program in Creative Writing.

候选返回 subject=`She`、object=`The Frost Place Advanced Seminar`、time=`2007–2010`，但 **`subject_resolution=""`**。提示要求代词为 `UNVERIFIED`；`validate_extraction()` 在结构字段校验阶段抛出 `BRIDGE_JSON_FIELD_INVALID`，尚未运行关系/身份准入。原始输出未修改，也没有将“选中正确句子”报告为 `FIRST_HOP_VERIFIED`。

| 多跳检查层 | 真实结果 |
|---|---|
| 第一跳关系候选 | 选中了正确原文，但结构字段无效，验证未通过 |
| 身份指代验证 | 未执行；前驱原文链回读完整不等同于身份准入 |
| 程序 Q1 | 未构造 |
| 第二跳候选 | 本次执行未到候选筛选；冻池中存在来源由离线对照证明 |
| 第二跳 BGE | NOT RUN |
| 补充进入最终 Context | 否 |
| 生成答案及证据支持 | 未生成；`FINAL_ANSWER_SUPPORTED=false`、`generated_answer_verified=false` |

最终状态 `S85_REAL_STOP / S85_EXTRACTION_OR_RESPONSE_CONTRACT_STOP`。新 UNKNOWN=0，重试=0，Frost B/Embedding/最终生成/Expensive=0。账本和授权关闭记录均为 STOP；未用 1 次操作与 8,763 未占用 Token 额度已撤销，历史占用未释放或重置。原有收据、角色/模型配置、来源和容器版本的前后快照一致。

#### 命令、退出码与证据

| 实际命令 | 退出码 | 结果/证据 |
|---|---:|---|
| `python -B var/reports/s8-5/stage4/live_orchestrate.py stage-prepare` | 0 | 远端 prepare exit 0；205 文件哈希验证；10 句/1433 原文字符/3 前驱；0 次模型发送 |
| `python -B var/reports/s8-5/stage4/live_orchestrate.py run` | 1 | 远端 runner exit **2**，表示合同 STOP；`ecs-real-run.*` 和 `downloaded-final/stage/run.command.json` |
| `python -B var/reports/s8-5/stage4/live_orchestrate.py collect` | 0 | 22 个收据文件下载并通过逐文件清单验证 |
| `python -B var/reports/s8-5/stage4/independent_audit.py var/reports/s8-5/stage4/downloaded-final` | 0 | 独立原文/用量/账本审计通过；原响应重放仍抛出 `BRIDGE_JSON_FIELD_INVALID` |
| `python -X utf8 -B var/reports/s8-5/stage4/finalize_report.py` | 0 | 根据原始收据生成独立真实结果 JSON；再次核对 4 个冻结文件未改 |

远端实际 run 命令：

```sh
docker exec -i -e PYTHONPATH=/opt/rag-s8/state/s8-5-bridge-20261021-6c5bee3541044d649d70629ac0514b20/source:/app rag-s8-api-1 python -B /opt/rag-s8/state/s8-5-bridge-20261021-6c5bee3541044d649d70629ac0514b20/source/scripts/s8_5_bridge_ecs.py run --root /opt/rag-s8/state/s8-5-bridge-20261021-6c5bee3541044d649d70629ac0514b20
```

原始证据保留在本地 `var/reports/s8-5/stage4/downloaded-final/` 以及 ECS 新诊断目录；不保存请求头/密钥。源码/输入包 SHA-256 `1fb0d487aa3cc3b535559114fc88c3e3f71b6d19d14c4c348ebe628fb9a91a8a`。收据包 **58,013 字节**，SHA-256 `310b7ea37aa227ab2cb6a8e5a7805a0246605f731eab4a1ba764df5630e247a1`。请求 wire SHA-256 `769a6982ba0a4ae12885741dbc7c78a6fe497adad4501ac4b66b9e13b5107642`；原始响应 SHA-256 `17896bd212d20509eb68d27d435bd29a03f3a5103cb484cc25e3bf90a657c71c`。模型身份依据为注册表、实际请求和 Provider 响应的模型字符串，未独立验证模型权重。

### 阶段 4 继续执行：真实反馈修复与效果分层

负责人后续明确授权自行解决非权限问题并继续 S8.5。精确批次见 [继续执行计划](S8_5_CONTINUATION_PLAN.md)。实际云端调用在既有 ECS `rag-s8-api-1` 内，以独立目录和 `PYTHONPATH` 执行一次性进程；本机运行离线测试和原始记录重放。容器镜像保持 `sha256:80e2f2d84bf67d7d22117d98676049aa9cd3101e69b20f780d5fd9eeaf931a36`，API/Worker 未部署或重启。

#### 真实发现与对应修复

| 可复现问题 | 文件/函数与最小修复 | RED → GREEN / 结果 |
|---|---|---|
| Cheap subject_resolution 为空 | bridge_rescue 抽取提示显式枚举，不补写模型字段 | 2 RED → 86 GREEN；A1 第一跳/身份/Q1 真实通过 |
| 完整研讨会对象无法词面匹配机构 Chunk | bridge_rescue 有界字面前缀仅作搜索线索；不作为别名 | 4 RED → 151 GREEN；A2 真实补回机构来源，活动地点仍不验证 |
| B 对象误含 from 2007–2010 | 提示区分对象与相邻关系日期；程序关系边界不放宽 | 时间/普通机构提示组合 255 GREEN；B1 原响应继续拒绝，B3 新响应通过 |
| 普通 snapshot 反复重写 model 行 | PostgresModelStore.persist 比较解密值；相同模型不重新加密/更新 | 初始 4 RED → 22 GREEN，嵌套已加密输入 1 RED → 23 GREEN；旧模型/角色指纹保持 |
| 改写模型翻译/关键词化/丢标点 | query._REWRITE_GUARD_PROMPT 明确字面替换优先级，校验器不放宽 | 3 RED → 127 GREEN；C3 T2/T3 有效查询真实正确 |
| memory=False 导致失败落库误报所有者 | turns._accepted_owner 区分身份和策略位；保留真实 tenant/user/owner 拒绝 | 5 RED → 111 GREEN（包含诊断 fallback 输出上限修复）；C2 旧错误不改 |
| Provider 原始 Jean/[DONE] 无正常结束却标完成 | GenerationCoordinator 拒绝显式 provider_answer_done=False | 6 RED → 174 GREEN；实际原始字节 T1/T3 重放均 FAILED、不提交成功答案，真实 Token 结算不变 |
| 歧义 q0 未改写但 fallback 擅自选择实体 | query 保留 AMBIGUOUS_REFERENCE；RuntimeService.prepare_turn 在 Search/Rerank 后走固定澄清、清空事实和历史；保护其格式后续 | 8 RED → 174 关联 GREEN；实际 T5 记录重放固定澄清，catalog/生成调用均 0 |
| 诊断预检 JSON 元组变列表 | 整份规范 JSON 比较；restore_finalized_context 仅恢复声明的元组字段 | 6 RED → 33 GREEN；原 GA2 preclaim 拒绝、0 模型调用；新 GA3/GB3 使用修复版 |

当前源码一次完整受影响回归：**922 passed in 12.92s，exit 0**。原样 argv、源码版本和输出在 `var/reports/s8-5/continuation/final-verified.command.json/.stdout.txt`；执行入口 `python -X utf8 -B var/reports/s8-5/continuation/run_final_scoped.py`。测试只覆盖受影响模块，重复运行/子集没有累加为独立数量。四个 S8.4 冻结文件 SHA 均未变化。未实现的命令不得报告通过。

#### C3 七轮的实际效果

| 轮次 | 实际检索行为 | 回答/证据结论 |
|---|---|---|
| T1 Who is Jeanne Marie Beaumont? | q0 保持；当轮检索 | 原始流虽有 [DONE]，但缺 choice.finish_reason、provider_answer_done=false；不能计完整回答通过；当前原字节重放拒绝成功提交 |
| T2 What did she direct from 2007–2010? | What did Jeanne Marie Beaumont direct from 2007–2010?；Embedding/关键词/Rerank 均一致 | 回答 Director of The Frost Place Advanced Seminar，引用 ec07a1fd… 可回读；这一轮有原文支持 |
| T3 Which poetry collections has she written? | Which poetry collections has Jeanne Marie Beaumont written?；正确连续补全并重新检索 | 原始 Provider 仅输出 Jean，虽有 [DONE] 但缺 choice.finish_reason；检索改写通过，回答失败；当前重放已阻止成功落库 |
| T4 Compare a Leyden jar and Franklin bells. | 新话题原样检索，无跨题人物补全 | 有当前原文的设备说明，模型未输出引用；不是引用完整通过 |
| T5 What is it used for? | 歧义保留 q0，真实 Search/Rerank 后无 Context | 旧 fallback 错选 Franklin bells，FAIL；当前代码保留歧义并固定澄清，真实输出重放通过，非再次云端测评 |
| T6 Format the previous answer as a table. | follow_up，无 Search/Embedding/Rerank | 纯转换路径通过；旧运行把错误 T5 制成表格，不能当成事实成功；当前保护该歧义后续 |
| T7 Which original document… Leyden jar? | 明确出处要求，重新检索；q0 保持 | 引用 e8a751ff…，原文证明静电储存及早期 capacitor 用途；当前来源/版本/偏移回读通过 |

C3 共 26 次已结算响应、40965 个报告 Token；7 轮均有真实 PG 历史/异用户拒绝/空 Session 和 SSE 回放零新增模型操作检查。这些合同通过与答案事实/完整性分开记录。C2 的 19 次/27517 Token 失败诊断另存，不替换为 C3 的结果。

#### Frost 七层证据与新生成

A2/B3 都完成：真实 Cheap 第一跳 → 原文身份/时间/关系核验 → 程序 Q1 `Where is The Frost Place Advanced Seminar located?` → 初始拒绝池最多 8 候选的一次 BGE → 原文 `31417c7c-7abf-4723-956a-74bf7ee14683` 地点关系 → Context。第一轮阈值/RRF/TopK 未改，没有新增全库 Search/Embedding。机构地点被找到，但 `ENTITY_BRIDGE_VERIFIED` 和 `FINAL_ANSWER_SUPPORTED` 仍为未验证；B 最终 Context 还缺完整姓名锚点。

GA3/GB3 不重新执行前面的抽取/检索/Rerank；程序回放不改动的已结算真实输出，读取当前同版本原文，去掉生成的 Summary/描述，只保留已选来源或声明祖先，然后各做一次新 Cheap 生成。缓存 Token 不计入新账本。两次最终答案的原文逐项审查在继续结果 JSON 的 `post_real_fixes.generation_manual_reviews`；模型正常结束或引用能回读都不等于完整问题得到支持。

GA3 的审查为 **PARTIAL**：两句事实分别有原文，但没有说明研讨会地点仍未验证，且 `references=[]`。`institution_event_boundary_checked=true` 只表示已审查；边界表达状态为 INCOMPLETE、引用状态 MISSING、完整问题支持为 false。GB3 同为 **PARTIAL**：有 2 个可回读引用，机构所在地陈述受支持，但最终 Context 缺完整人物姓名锚点，且未明确说明活动地点仍不确定；两个有效引用不能证明完整问题的语义支持。两次都不得认定为完整多跳问答通过。原始流均观察到 stop 和 [DONE]，收据的迭代器 `complete=false` 另行原样保留，不改写为 true。

#### 调用与账本

继续批次实际尝试 **54** 次，其中 **53** 次已结算，**1** 次 UNKNOWN；已报告实际 Token **77200**。另有 UNKNOWN 占用 **6365**，不代表已测得实际消耗或费用；由于 UNKNOWN，完整实际总量为 null。原先首次受限 STOP 的 1237 Token 单列，包含它的已报告小计为 **78437**。

新旧继续批次的实际占用 **83565 / 200000**、操作 **54 / 56**。B2 UNKNOWN 的 6365 保留，旧收据/授权/历史不重置。GA2 的 JSON 预检失败发生于创建 Session、run.claim 和模型账本之前，0 模型发送；与本机仅构包未上传的 G1/GA 一同另记，不计成真实模型操作。

每条 request ID、模型、输入/输出 Token、HTTP/响应状态、wire/raw SHA、授权/原文/档案位置均可在继续结果 JSON 查询；原始文件保留本机 `var/reports/s8-5/continuation/` 与对应 ECS `/opt/rag-s8/state/s8-5-*` 目录。各批 `stage-prepare/run/collect` 原样命令及退出码见 `orchestrate-*.py`、`ecs-*.exit/stdout/stderr` 和下载的 `stage/*command.json`。初始首轮及 GA2 的非零退出码保留，不因后续修复改成 PASS。

#### 实际执行与记录重放的流程

```mermaid
flowchart TD
  A[C3: 新真实 Session / 7次正式准入] --> B[授权完成历史 / q0保留]
  B --> C[真实 Cheap Query Understand]
  C --> D[程序校验 / 有效查询]
  D --> E{纯格式?}
  E -- 否 --> F[真实 Embedding / PG Hybrid Search / BGE]
  E -- 是 --> G[历史文本格式转换]
  F --> H[当前原文 Context / Generation / 引用]
  G --> H
  H --> I[PG持久化 / SSE恢复零重复调用]
  H --> J[T1/T3未完成流 与 T5歧义错误原样保存]
  J --> K[当前代码原记录重放: 失败保护 / 固定澄清]
  K --> L[0新增云端调用 / 不冒充新鲜端到端]
```

```mermaid
flowchart TD
  A[S8.4冻结首轮Search/Rerank输入池] --> B[ECS当前原文与最多3前驱]
  B --> C[A2/B3真实Cheap选句 / 程序第一跳与身份验证]
  C --> D[已验证完整对象程序Q1]
  D --> E[原始候选池 / 各一次真实BGE]
  E --> F[机构地点原文 / 限定Context]
  F --> G[活动地点与实体桥接 UNVERIFIED]
  F --> H[GA3/GB3: 缓存真实输出重放 / 当前已选原文投影]
  H --> I[各一次新Cheap最终生成 / 独立新收据]
  I --> J[逐句原文与引用审查 / 不计为完整多跳正确]
```

### 阶段 5：仅评估

Frost 必需第二跳已在首轮 30 候选中，因此没有依据增加第二次全库 Search。当前主要缺口是事件与机构地点的关系，以及 B 的完整身份 Context。缺池反例已可执行记录 `SECOND_HOP_SOURCE_NOT_IN_POOL`。后续如在真实新样本中证明缺池，另行提出一次受限 Hybrid Search 的延迟/Embedding/Rerank 上限方案；本轮不实现。

## 初始实现流程（保留离线与首次 STOP 轨迹）

```mermaid
flowchart TD
  A[授权用户 + Session + client_request_id] --> B{已有运行?}
  B -- 是 --> R[SSE / 持久化事件回放]
  B -- 否 --> C[prepare_turn: 当前 Session 已完成历史]
  C --> D[understand_query: q0 + 有限历史]
  D --> E{改写程序校验}
  E -- 接受 --> F[有效查询 rewrite_query]
  E -- 拒绝或失败 --> G[有效查询 q0 + 回退原因]
  F --> H{原始问题意图保护}
  G --> H
  H -- 明确的历史格式转换 --> T[历史回答转换]
  H -- 事实或不确定 --> I[Embedding + Hybrid Search]
  I --> J[同一有效查询 Rerank]
  J --> K[Merge + TopK + 当前原文证据]
  K --> L[Context + Generation]
  T --> L
  L --> M[当前来源引用 + 持久化 + SSE]
```

```mermaid
flowchart TD
  A[当前 Search / 第一轮 Rerank / Merge] --> B{Bridge 显式启用?}
  B -- 否 --> N[普通 Context]
  B -- 是 --> C[当前版本原文回读 + 最多3前驱 + 2400字符]
  C --> D[程序切句 / sentence_id 映射]
  D --> E[一次 Cheap 选择句子与候选字段]
  E --> F[原文 / 完整对象 / 时间归属 / 身份链核验]
  F -- 不足 --> U[UNVERIFIED + 原因]
  F -- 通过 --> G[程序构造 Q1]
  G --> H{初始阈值拒绝池存在候选?}
  H -- 否 --> X[SECOND_HOP_SOURCE_NOT_IN_POOL]
  H -- 是 --> I[最多8候选 / 一次 BGE]
  I --> J[原文直接地点谓词校验]
  J --> K[最多1原文补充进入 Context]
  K --> L{实体桥接与最终问题均有原文支持?}
  L -- 否 --> U
  L -- 是 --> M[FINAL_ANSWER_SUPPORTED 的证据前提]
```

以上两图对应当前实际代码调用点与离线实参轨迹；首次受限批次真实执行的子链如下。`FINAL_ANSWER_SUPPORTED` 只描述证据前提，不能代替对实际生成答案的验证。

```mermaid
flowchart TD
  A[Frost A 冻结 Search / 首轮 Rerank 输入] --> B[ECS 当前原文与3前驱回读]
  B --> C[10个句子ID / 1433原文字符]
  C --> D[真实 Qwen3.5-4B 1次 / 1103+134 Token]
  D --> E[选中正确关系句 / subject_resolution为空]
  E --> F[BRIDGE_JSON_FIELD_INVALID]
  F --> G[STOP / 收据结算与授权关闭]
  G --> H[Q1与BGE未执行 / 无补充Context / 未生成答案]
```

## 未解决项与下一步最小建议

1. **Frost 原文缺研讨会举办地桥接**：机构地点证据不能回答完整问题；B 最终 Context 的完整身份锚点也不足。保持 UNVERIFIED，不扩检、不放宽全局阈值、不填 Gold。
2. **Cheap 引用/完整回答质量不稳定**：C3 T1/T3 原始流缺 choice.finish_reason，虽有 [DONE] 仍不能确认回答完成；T4、GA3 等模型漏引。已修复明确未完成流的成功落库错误，原始记录重放验证通过；不把读取到来源当成全部句子被引用或答案完整。
3. **最后两项安全修复的验证方式**：当前残缺流与歧义保护通过离线测试和不可变真实输出重放；没有在最后修复后再次执行完整 7 轮云端链。新 GA3/GB3 使用当前代码，但只验证生成及原文投影，不代替该全链回归。浏览器真实刷新未运行；真实 durable SSE 恢复已运行。
4. **UNKNOWN 未解决**：B2 实际用量未知，6365 占用保留；后续 B3 成功没有结算它。停止该旧请求，不重试，不伪造实际总消耗。
5. **下一步最小范围**：如继续效果工作，优先独立验证最后修复后的多轮整链、Cheap 引用/结束合同和语义边界表达；现有 Frost 第二跳就在原池，尚无依据自动实施阶段 5 的第二次全库检索。本轮不建立 Tag，也不代替负责人宣布里程碑验收。

## 分阶段版本

分支：`codex/s8-5-conversation-bridge-20261021`。本轮未推送、合并或打 Tag。

| 阶段 | Commit | 内容 |
|---|---|---|
| 参考依赖集成 | `7e5fb4a1c584a3fe672b9342cd5ff8e99dd74803` | 当前 main 合入已提交 S8.4 代码 |
| 0 | `f577963425f78ef90979446bec6ebcfac931a2b1` | 实际链路与缺陷基线 |
| 1 | `e55f39d` | 历史来源隔离、改写校验与事实追问检索 |
| 2 | `8ed00d6` | 关系/地点保护与双侧唯一收据 |
| 3 | `e4f011e17ca016c22b502b210749f98d12d7a34a` | 句子 ID、有界身份与第二跳证据 |
| 4 离线/运行器 | `f5b0f6e732c7951cae5b993c45b3b92f5a0b785e` | 离线对照、真实运行器与精确计划；真实执行源码基线 |

真实 STOP 结果及本报告作为后续独立文档提交保存；其最终 SHA 以交付时的 `git log -1` 为准，避免在提交正文内自引用尚不存在的 SHA。


继续实现的生产源码与最后诊断版本：`f9e8723b374e78de6a1a2ec9a44207035ad0d044`。关键补充提交：`600a379` 改写合同、`e9d9642` 身份/诊断输出、`0419030` 未完成流保护、`c3c7e6e` 歧义固定澄清、`f9e8723` 诊断 JSON 边界。报告提交 SHA 在交付消息中给出，避免正文自引用。

本轮发送已结束；未使用的 2 次操作及 116435 Token 会话上限空间已关闭。该关闭只撤销本轮未用授权，不修改月度预算，不释放 B2 UNKNOWN 的 6365 占用。关闭记录及 SHA 见继续结果 JSON 的 closeout。
