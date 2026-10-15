# 标准 API / worker 装配：实际 Token 口径实施设计

状态：**SOURCE_READ / DESIGN_ONLY**。基线 `c478b9f9165cb19886eec10a2db7c21370ddbd7b`，独立 E 盘工作树；本轮仅源码读取和文档，没有实现生产接线、创建授权、写账本、调用模型或启动服务。

本次最新依据：Owner `Sentinel_42a8526687e481919bee0193a7b3d1aa` 原文“评判是看token数，不要预估费用”；父级明确 DeepSeek 月限仍 **1,000,000 Token**，不授权真实请求或重置历史。本文已据此修订，价格、人民币/其他金额换算和费用估算**不是**本方案必需输入，也不是新评测指标。发送前必要的保守数量只称“预算预留”，绝不当作供应商实际消耗。

完整重读冻结 `task/02_Codex完整执行Goal.md`，核对已有 bootstrap assessment、独立 `E:/WK/go-linux/evidence/review-summary-c478b9f9-20261015/AUTHORITY_HANDOFF.md`、ADR-002、P1SendGate/005账本及输入/用量相关源码。身份与只读交付检查见 `evidence/AUTHORITY_IMPLEMENTATION_DESIGN_20261015/`。

## 1. 与冻结 Goal 的冲突及最小处理

| 原要求/实际实现 | 最新指令下的处理 |
|---|---|
| Goal G0.1/G2 按月费用上限；G6/G7 费用估计来源 | Owner 最新指令覆盖新评测的金额估算，DeepSeek 上限改按明确的实际 Token 与保守预留占用判断；不删除/改写冻结 Goal 原文 |
| P1 `CostAuthority.monthly/estimate/actual` 强制价格、金额、microunits；send_budget_months/send_calls 均为金额列 | **不能直接当 Token 账本**，不能填伪造零价/1Token=1microunit 绕过。需显式增加 Token 单位的合同及持久化路径，见第4节 |
| 旧 PostgresServiceTokenLedger 已有 reserve/finish、actual input/output、UNKNOWN、超界 suspend | 复用这些语义；它是旧 SQLAlchemy/policy 累计额度且依赖 link_cost，不是新链 UTC月账本，不能直接连旧数据库或当现成装配 |
| 旧金额账本与历史实际/UNKNOWN | 原样保留，不清零、不换单位、不重定价；只读批准 Token 基线不能从金额反推 |
| 默认拒绝、Scope/外发、幂等、并发和 UNKNOWN 防线 | 全部保留；这是指标/预算单位修订，不是外发授权或撤销保护 |

设计结论：仍用单一 Provider transport、同一个外层 P1 发送门和一份所选单位的账本。不同时扣金额和 Token，不复制 QuickChain、V6 grant、可变 permit 或多层审批。旧金额模式保持其原协议/历史；新 Token 模式须显式实现，默认无配置拒绝。当前“无新表即可直接接CostAuthority”的旧建议对本次 Token 月限已经不成立，不能继续照搬。

## 2. 已有、适配与真正缺失

| 范围 | 已有证据 | 最小工作类型 |
|---|---|---|
| 共用装配 | development.assemble 的 permissions/costs/model_factory 注入；API create_app 与 worker.serve 的 runtime_factory | 适配两个标准 main 共用可信 factory/config；并为新 Token authority 增加显式注入，不能往 costs 塞占位对象 |
| 主体/KB | LocalPrincipalStore、turns._resources、KB cloud_allowed 默认false | 适配为真实只读 permission resolver；全局许可仍缺可信配置 |
| 权限检查 | SendAuthorization、ContentOrigin、P1._permission，精确 wire/model/endpoint/scope 绑定 | 复用；缺具体 resolver 和真实来源覆盖 |
| 原子状态语义 | P1 authorize/finish，月锁、唯一call ID、保守UNKNOWN、终态幂等 | 抽取/复用到显式Token分支，避免复制另一套外层门 |
| Token记录 | TokenUsage/normalize_usage；旧 service_token_reservations 的实际/预留分列 | 借鉴合同和状态；缺新链月Token账本及生产 callback |
| Token真实性 | models/usage.py 已区分UNKNOWN，cache_status/cache_reported | 仍需适配原始字段存在性：当前会推导total，缺缓存字段多归0，不满足新的“实际字段缺失UNKNOWN”报告口径 |
| 输入上界 | P5 LocalTextTokenEstimator 明确只是proxy；旧ServiceCapacity有本地recipe | 新链可靠完整wire计量缺失；不能拿P5估算冒充实际usage或月限上界 |
| 来源/恢复 | messages scopes、memory source_message_id、port_turns/replay、任务operation_id | 接来源事实与稳定调用身份；未解决的来源/计量保持拒绝，不扩大RAG算法 |

当前 tracked 源码 `git grep` 未找到确切 `SourceFacts/source_facts/InputUsage/input_usage` 同名合同（exit1，无匹配，见 contract-discovery.json）。下文名称是拟新增接口，不宣称已有类。实际已核对 ModelWindow、ServiceCapacity.measure、ServiceUsageGuard、service policy、P1 PreparedCall/TokenUsage；如父级另有冻结同名合同，落实前取实际路径/hash。

## 3. 实际用量与预算预留必须分列

拟增加不可变 `ProviderUsageFacts`（保留现有TokenUsage用于源协议内部兼容，不把默认值当实际报告）：

- provider/model/endpoint/capability、call_id、usage_semantics_version、raw_numeric_usage/hash；仅保留usage数值，不存完整响应/私有正文。
- `input_tokens / output_tokens / total_tokens / cache_read_tokens / cache_write_tokens / cache_miss_tokens` 均为 nullable，逐项记录 reported/present，缺失写 UNKNOWN/null，真实供应商明确返回0才记0。
- 原字段映射与 cache inclusion 语义：例如某协议 prompt/input 已含cache read时，不能再把 cache tokens 加进 total；不同供应商的input是否排除cached量必须按该固定协议映射，不能横向套用一个公式。DeepSeek prompt、completion、total及命中/未命中分别记录，按其响应合同校验，缓存只是input的细分，不另加一遍。
- 明确 `reported_total` 与内部 `derived_total` 分离。当前 normalize_usage 在 total缺失或0时可算prompt+completion，不能将该结果标作供应商返回的actual total；旧记录不回写。供应商明确0但与其他计数冲突时保留原始事实并标invalid，不偷偷改成和。
- 累计流usage有的帧是快照，不可逐帧相加；同一call的最终usage只记一次，独立物理batch/call才按可证明的互斥集合求和。任一调用缺usage，聚合actual total为UNKNOWN，同时可另报known subtotal与unknown call count，不能让“已知部分”充当完整消耗。

评测/可见报告字段只用实际supplier usage：input/output/cache/total及其语义/可用性，升级的每个attempt单列，辅助模型按真实角色单列。禁止金额换算，禁止把本地estimator、预留或未发生调用写为实际Token0。真实模型未调用时标NOT RUN，mock结果明确标SIMULATED。

拟 `TokenReservationBound` 分别存 positive input_upper_bound、output_upper_bound（embedding可明确0）、bound_source/recipe+asset hashes、wire SHA与支持模态；它只服务发送前严格月限，不是实际消费。P5 L估算保持独立，0.35/六特征/逐项max/升级开关完全不变。

`measure_final_input(config, body, wire)` 必须覆盖最终完整协议/模态及框架开销；可靠依据缺失则该能力拒绝发送，不补0、不自动用字符数。只可复用已核验纯计数/资产检查函数；旧ServiceCapacity整体绑定输出探针、旧专用参数/档位与不同JSON字节，不能直插或为适应计数改Prompt/输出上限。模型目录context/max output并不证明输入计数可靠。若供应商不保证实际输出受所选cap约束，需可靠保守总上界，否则相应真实能力仍拒绝。

## 4. 最小接口与持久化调整（均尚未实现）

建议两个新增实现文件 `authority.py`（只读配置、具体permissions/token authority、共享bootstrap）和 `source_facts.py`（来源/最终wire/上界绑定）；Token SQL适配放现有 storage 边界内。新类型和transport薄接线属于必需变化，不重构检索或模型业务。

拟具体函数/字段：

| 拟接口 | 必需输入/输出及职责 |
|---|---|
| load_authority_settings(path) | 返回不可变服务器配置；schema/hash、主体、有效期、global outbound、允许provider/model/endpoint/capabilities、Token账户/月限/carry-in/可靠recipe；无价格/rate字段 |
| SourceFacts / SourceSlice | source identity/version、原值hash、发送值hash、kind、origin KB、transform recipe及wire值定位；不是许可对象 |
| bind_wire_facts(config,context,body,wire) | 唯一序列化后验证完整私有字段来源与精确wire，产生BoundCallFacts；未知字段/来源/模态拒绝 |
| CallContext.source_facts / PreparedCall.bound_facts | 可选不可变字段，默认None；生产resolver必须拒绝缺失；child调用重建自己的输入事实，不能继承父wire证明 |
| ServerPermissionResolver.resolve(call) | 校验批准来源/有效期/global、模型、真实origins；读取全部origin/retrieval/required KB的tenant及cloud_allowed，产生原SendAuthorization |
| TokenBudgetAuthority.monthly(call) | TokenMonthlyAuthority(account_id,provider,UTCmonth,limit_tokens,carry_in_tokens,status,source,approval_reference)；DeepSeek limit=1_000_000，不臆定其他provider上限 |
| TokenBudgetAuthority.reserve_bound(call) | TokenReservationBound(input/output upper bounds,source,wire identity)；无金额估算 |
| TokenBudgetAuthority.actual_usage(call,usage_facts) | 返回真实且语义完整的input/output/total/缓存事实与budget_count；不请求模型，不推导成供应商actual；不完整返回UNKNOWN |
| P1SendGate.authorize/finish | 外层接口保持；显式新增token_authority及unit选择，复用权限/身份检查；为Token分支调用原子Token存储，不要求CostAuthority/zero price来放行 |

不能把 tokens 写到 reserved_microunits，不能通过known_zero_price=True、假price_source或Token→金额映射满足旧检查。最小安全schema建议**新增两张显式单位表** `port_token_budget_months`、`port_token_calls`，仅隔离新库版本化migration；它们是旧金额二表的单位明确替代分支，不是第二个同时计费的门：

- month表：`account_id, provider, month` 主键，`limit_tokens`、`carry_in_tokens/status/source`、`approval_source/config_hash`、`blocked_reason`。统一Token单位，不按进程/角色另开额度；同账户DeepSeek Cheap/Expensive及辅助调用共用对应月额。
- call表：全局唯一call/request_id、tenant/subject/session/operation、account/provider/month、model_id/model/endpoint/capability、wire hash/bytes、retrieval及origin KB、permission source、bound source，`reserved_input_tokens/reserved_output_tokens`、nullable actual input/output/total/cache及presence/语义、`state/outcome`、timestamps。计数与state有CHECK，月键FK。
- 不修改原send_*金额列或旧service_*历史，不连旧engine。一个新调用只能选择一种预算单位/账本；禁止由请求体决定unit，缺配置默认拒绝。旧金额模式源码保留为历史/兼容，不能因新Token模式而伪造其授权。
- Token持久化可复用P1月锁+事务算法、旧PostgresServiceTokenLedger的实际/预留和超界suspend语义；旧ledger没有UTC月/carry-in且强制cost link，不能直接复用其运行对象。新表是本次单位修订的必要最小迁移；若已有新链Token表，落实前核查并优先复用，当前005不含。

生产配置与真实密钥不是本轮产物。既有八项WEKNORA_PORT_数据库/Redis/存储/32字节加密主密钥配置沿用，provider key仍在加密角色存储，不增加第二套key。拟仅增可信 `--authority-config PATH`，API/worker共享loader；文件路径不是授权，示例只能disabled/synthetic。禁止任意module import或HTTP body创建authority。

## 5. API / worker 共用调用顺序

1. **启动**：同一 `authority_runtime` asynccontextmanager在验证leases/principal后构造真实只读callbacks，委托assemble。两个标准main明确使用同一factory/配置SHA；避免resolver递归组装runtime。默认无authority可保留配置页面，ready503/拒绝发送；配置损坏/过期不能静默启用。ready改为本地合同校验，不是供应商探针，non-None不能代表可发送。
2. **组装事实**：question/history/evidence/memory/document/image及静态模板从现有服务端来源收集；所有模型能力均覆盖，含rewrite、rerank、summary、memory extraction/embed、vision。原始source与派生文本双hash绑定；私有内容插入system后不能把整个system当public。
3. **唯一wire**：transport现有canonical JSON序列化后调用bind+measure，附入PreparedCall；来源完整性和可靠bound在写任何账本前检查。请求字节、模型选择与业务算法不变。
4. **许可**：复用P1._permission的身份/Scope/外发语义，resolver只读实际KB并构造SendAuthorization；来源KB并集不增宽检索范围。local/remote识别不能猜免费，不借缺金额模式绕过权限。
5. **原子Token admission**：权限→monthly/bound→新库事务读UTCmonth→account/provider/month advisory lock→call_id去重→month snapshot插入/核对→总占用比较→许可有效期及必要consent复核→call insert/commit。必须跨API/worker同锁，不能本地各算额度。月末跨界拒绝/重新取事实，不能错入另一个月；重启不刷新quota。
6. **实际发送**：既有cancel/deadline再次检查、安全endpoint/transport、sent保守判定不变；为等待月锁后撤销同意的用例，在相同事务提交前重读global/KB事实，不新增审批层。每个batch/升级独立call_id、重新permission+预算。
7. **finish**：同一P1 finish按Token单位分派；只known NOT_SENT释放reserved，可能发送/缺usage保留UNKNOWN；完整reported且符合协议实际总量≤预留才用实际量settle。终态相同finish幂等、冲突拒绝。不将取消/finally失败当未发。

## 6. 来源与持久化复用，不能只接启动器

复用messages/sessions scopes、immutable source/chunk/version、memory source_message_id、port_turns及现有task数据，不存raw prompts到新账本。SourceFacts进程内不可变，由现有可信记录在每次调用前重建；不创建可变permit/manifest注册平台。

历史：StoredMessage/HistoryRound本身不带KB origin，可按实际加载轮次request_id在同tenant/subject/session下读取原始messages scope/content，绑定去think/格式化后的值。message_origin_scopes可提供保守KB并集，但不能证明正文逐段完整覆盖。跨库历史必须检查来源库，不把它们改成当前KB。

memory：已有source_message_id/source_session_id能回查的派生内容沿用真实源scope；RuntimeMemoryModels当前全subject sessions KB并集只是保守范围，不足以证明手动/缺源memory。来源缺失保留数据并拒绝相应云端调用，不自动删除或默默赋KB。模板静态部分可public_static，私有插值必须显式覆盖；图片绑定实际发送bytes/hash而非仅URL。

稳定身份：extraction已有identity:extract:0/1，保留上游length第二调用条件。RuntimeMemoryModels.complete无operation_id时随机UUID、embed_query UUID、maintenance缺operation_id是接线必须收窄的实际位置：从持久任务/消息/版本/阶段/模型/批次派生稳定ID；无父identity时不准自动恢复发送。不能用新UUID绕过未知旧调用。新的用户主动请求仍是新操作。

## 7. 月限、UNKNOWN、缓存、cancel与恢复

Token占用定义：`carry_in + Σ(reserved/unknown 的 input_bound+output_bound) + Σ(settled 的实际 budget_count)`，released为0。DeepSeek月上限1,000,000，预算预留单独显示，不计作actual；当已知实际超过预留时，保留至少已知实际占用并block该月，复用旧token ledger suspend思想，不靠下次估算掩盖破界。所有更新与月锁同事务；异常抛出不能回滚掉block/UNKNOWN。

未知usage/缺total不补0。供应商有一致、完整、固定语义的total时计该值；缓存read/write/miss按provider语义单列，不重复加到已包含的input/total里。若只报部分字段，只展示该字段actual，完整actual total保持UNKNOWN；严格月限继续占预留。对于total与input/output关系不同的协议，需独立明确mapping，未实现则拒绝/UNKNOWN，不套DeepSeek公式。

carry-in必须来自可核查既有Token记录/账户usage及UNKNOWN的可靠保守上界。不能从金额换算，不因为新表空写0，不能因跨日/重启新分支重领100万。旧UNKNOWN无Token可靠上界或同账户旧服务仍在消费且不能保守覆盖时，真实启用BLOCKED；可以继续离线实现。其他provider的真实额度尚未明确，不能默认为无限或照搬DeepSeek额度。

幂等：port_turns同client请求replay_only，SSE GET只续事件；任何已用call_id（包括released）不允许authorize重发。合法Cheap→Expensive保持不同attempt且重新扣Token预算，最多原规则次数；取消/权限/额度拒绝及默认provider_fallback=false均不能改成重试理由。

恢复：预留commit后崩溃，无论是否实际发出，只要无可靠NOT_SENT证据就保留reserved/unknown占用、拒绝同ID重发；不能因没有sent标志就退款。image unknown/orphan复用；memory队列重投必须撞同稳定ID，不以新UUID重新发送。重启仅回读/重建事实，不重置账本或释放UNKNOWN。

cancel/deadline：复用call_lifetime已有admission/provider/settlement生命周期，model timeout与run deadline区分，cancel/run deadline不升级。resolver/PG锁等待服从剩余deadline；如实现bounded shield，只用于必要终结持久化并继续传播cancel，失败仍保留占用。不能引入新任意短超时替换上游。恢复/对账不要求真实请求。

## 8. 有限验证集合（建议，尚未运行）

只实现下列12组受影响合同/隔离入口，不重复检索/图片审查：

| 组 | 必要断言 |
|---|---|
| 1 共用装配 | 两标准main相同factory/config SHA/Token账本；无配置ready503且0 mock send/0预留 |
| 2 配置/单位 | 100万月限、缺carry/过期/错身份/未知模式拒绝；Token不写microunits，不依赖价格或假零价；原金额/历史hash不变 |
| 3 来源 | 七类来源正例及遗漏/跨tenant/KB/version/system插值/manual memory未知负例，预留前拒绝 |
| 4 Wire | 最终字节与原provider合同一致，任一body/hash/model/endpoint变动拒绝；无registry/可变permit |
| 5 上界 | text/embedding/rerank/vision独立可靠recipe或明确拒绝；P5 proxy不当预算依据，预留不冒充actual |
| 6 实际usage | 输入/输出/total/cache逐项0与缺失区别；cached已包含不重复加；raw total缺失不伪报derived；流快照/物理batch去重 |
| 7 并发 | 两隔离PG连接模拟API/worker抢同DeepSeek月额，恰一成功；同账户多角色不拆额，call ID只有一次预留/最多一次mock send |
| 8 撤销/时间 | 月锁等待时撤销consent/global、授权过期、跨UTC月、cancel/deadline，解锁后拒绝且无HTTP |
| 9 finish | NOT_SENT release、缺usage UNKNOWN、实际量超预留持续block、同finish幂等/冲突拒绝、未知不退款 |
| 10 重放/升级 | API重复POST/GET恢复不增加send；合法一次升级重检权限和Token；默认provider_fallback不改 |
| 11 崩溃 | commit前/commit后未发/可能发/finish中断故障注入，重启额度和call ID保留，不盲发退款 |
| 12 worker | 来源PG重建、稳定operation跨重投、过期配置拒绝；明确length第二调用与可能已发送的任务分开 |

计划测试（当前NOT IMPLEMENTED/NOT RUN）：新增authority/token合同 +现有test_send_gate/test_provider_usage/test_provider_transport/test_transport_occupancy受影响部分；隔离新库migration及标准API/worker两个进程用闭合MockTransport验证，并记录源码/配置/模式/单位/actual与reservation分列。**未实现的命令不得报告通过。** 无真实模型、浏览器或全仓回归前置要求。

## 9. 执行者短计划与必要未决问题

1. 冻结上述来源/usage/Token单位合同，写默认拒绝、实际字段缺失/cache重复、单位误用RED；先实现无网络纯adapter，保留P5和旧证据。
2. 给P1增加显式Token authority/存储分支与新库最小migration；复用月锁、幂等、UNKNOWN公式，不直插旧SQLAlchemy或复制业务编排。实现可靠计量支持范围，缺者拒绝。
3. 在现有调用组装和唯一transport处接来源/上界/原始usage事实；两main共用可信factory，稳定worker调用身份。完成上述12组定向/隔离入口证据，分阶段本地提交。
4. 只准备disabled/synthetic配置模板及回滚步骤。当前只读方案任务到此为止；后续获准编码可离线/隔离完成，无需为真实调用许可停掉默认拒绝和接口实现。

真实启用时才需Owner明确：实际可外发KB/历史/memory范围和来源、global outbound/有效期；真实五角色provider/model/endpoint及辅助模型、可靠计量资产/协议范围；DeepSeek账户的UTC月100万Token基线、历史已消耗及UNKNOWN保守占用、同账户其他服务的并发消费覆盖。其他供应商的额度事实缺失时只阻断对应真实能力。已有加密凭据复用仍需身份范围明确，本轮不读明文、不生成key。**不再要求价格、人民币金额、汇率或费用预算作为新方案必需输入。**

未决仅：①另有冻结SourceFacts/输入合同则需实际路径/hash；②哪些provider/模态有可靠输入/输出预算上界，缺者先拒绝；③DeepSeek当前月真实Token carry-in/UNKNOWN上界和共享账户消费来源，缺者阻止真实启用。月限100万和不估金额已是明确决定，不重复请示。真实请求/切换/改旧库/释放UNKNOWN/push/tag/release仍不在本轮授权内。

## 10. 本轮检查与交付

实际执行只读git身份/status、UTF-8 Path.read_text与contract discovery（git grep exit1=无同名合同）。初次默认编码乱码和一次路径搜索语法错误已修正重读，无副作用。正确ADR文件是ADR-002-budget-and-public-contracts.md。本轮没有执行新测试、DDL/DML、模型或服务命令；全部NOT RUN。

源码读取hash与既有.serena/四受保护文件交付前复核；旧方案assessment保留作为当时事实，本报告按最新Owner指令取代其生产金额接线建议。新增设计/证据不宣称IMPLEMENTED、OFFLINE_PASS、REAL_PROVIDER_PASS或CUTOVER_PASS；summary修复既已接受，不重审。
