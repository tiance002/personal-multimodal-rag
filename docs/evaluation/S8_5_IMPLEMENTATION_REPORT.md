# S8.5 多轮对话检索与受控多跳检索

状态：阶段 0–4 的实现与离线对照完成；已取得精确授权并在 ECS 执行一次 Frost A 真实测评。Cheap 返回的 `subject_resolution` 为空，校验器按合同拒绝，真实分支以 **`S85_REAL_STOP`** 结束。没有重试或执行 BGE；不能宣称真实多跳链路通过。

| 能力 | 实现与离线验证 | 本轮真实验证 |
|---|---|---|
| Session 完成历史、指代改写、事实追问重新检索 | 已实现；检查实际 Embedding/关键词/Rerank 实参及隔离/回退/回放 | 真实多轮模型效果、PG 历史和浏览器恢复 NOT RUN |
| 句子 ID、原文对象/时间/身份保护、有界候选补救 | 已实现；Frost A/B 与 4 个负控制有独立轨迹 | 当前原文与三前驱读取通过；1 次 Cheap 收到正常响应，但字段合同失败 |
| 第二跳 BGE、Context 补充 | Mock 路径通过；缺池停止通过 | 因 Cheap 字段失败，NOT RUN |
| 事件地点与最终答案支持 | 能拒绝将机构所在地当作研讨会举办地 | 未生成答案；最终答案仍未验证/不具备充分证据 |

离线轨迹：[S8_5_IMPLEMENTATION_REPORT.results.json](S8_5_IMPLEMENTATION_REPORT.results.json)。独立真实结果：[S8_5_REAL_EVALUATION.results.json](S8_5_REAL_EVALUATION.results.json)。两类记录分开保存，旧离线结果及 S8.4 冻结结果未覆盖。

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

### 阶段 4：离线对照与真实测评（OFFLINE_PASS / REAL STOP）

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

### 阶段 5：仅评估

Frost 必需第二跳已在首轮 30 候选中，因此没有依据增加第二次全库 Search。当前主要缺口是事件与机构地点的关系，以及 B 的完整身份 Context。缺池反例已可执行记录 `SECOND_HOP_SOURCE_NOT_IN_POOL`。后续如在真实新样本中证明缺池，另行提出一次受限 Hybrid Search 的延迟/Embedding/Rerank 上限方案；本轮不实现。

## 执行流程（实际代码路径，离线模型为 Mock）

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

以上两图对应当前实际代码调用点与离线实参轨迹；本轮真实执行的子链如下。`FINAL_ANSWER_SUPPORTED` 只描述证据前提，不能代替对实际生成答案的验证。

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

1. **真实抽取合同未通过**：空 `subject_resolution` 已被安全拒绝，Bridge 默认仍 off，普通检索继续可用。建议下次只强化该枚举字段的明确输出示例并先做离线回归；不得修补此次原响应。重新真实调用需要新的一次性范围与授权，本轮额度已关闭。
2. **真实多轮效果仍未验证**：离线已证明参数传递、隔离、保守检索与 SSE 幂等；窄改写合同可能使真实模型的自由改写回退 q0。后续最小验证是固定三轮历史的独立评测，不与 Frost 重试混用。
3. **Frost 语义证据不足**：现有第二跳只证明机构所在地，不能证明 Advanced Seminar 的举办地；B 的最终普通 Context 也缺完整姓名锚点。这些限制在 Mock 成功路径仍成立，不能靠放宽阈值或追加模型调用消除。
4. **未验收范围**：真实 PG 多用户/跨 Session 历史测试、真实浏览器/Redis SSE 恢复、真实 BGE/最终生成均 NOT RUN。未批准延期或建立里程碑 Tag；本报告交付实现与可核查证据，不代表项目负责人已验收全部能力。

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
