# ECS 首轮数据处理与评测执行方案

版本：r1；任务 RAG-V6-ECS-RESUME rev1 attempt1。本文件是执行方案，不是新运行成功记录。状态依据截至 2026-10-10 15:42Z 的已存证据及本轮只读源码检查；未 SSH/API、未运行测试、未改配置/DB。历史 P7_ECS_ENV_PREP、07:44 截止及早期 PREPARED_NOT_ACTIVE 记录保留，但不能覆盖最新 Owner 指令。

## 范围、时钟与责任

Owner 已取消16:00切回本机，继续 ECS；这不延长旧 SETUP lease：15:22:50Z–16:00Z 的 lease 到期后不得复用。协调者发新有限 lease、对应新 Run/许可/profile，累计账本不重置。先完成代表 raw＋prepared 的真实小闭环及断线恢复，再进入正式 BATCH。第一项正式批量操作开始前原子持久化 T0，截止 T0＋5h，包含摄取/索引、首轮40问＋24页解析评分、程序评分和结果落盘；不是从首条 Chat 才计时。后续每轮最多1h；既有 owner-lease-small 只支持40min，不能把其字段改成60；正式 owner-lease-40q-24page 已有60min轮/30min派发窗口。首轮后停止自动调优，协调者先云端分析/离线。

最新授权补充：Owner允许正常 Redis 有限租约到期后，在既定私有 Unix socket、权限、资源上限和预算控制内恢复新有限租约；不复用或修改已过期租约。Owner表达了开发阶段 ECS 删除授权；按本次协调包的执行边界，不可恢复删除仍需对具体对象当次确认，优先保留旧副本/新隔离目录等可恢复方案。不删除本机数据，不清空预算账本、UNKNOWN、历史失败证据或其他项目服务。本授权记录不触发本轮 SSH、服务重建、删除或真实请求。

唯一决策/验收 owner：父协调者。两阶段源码唯一 writer：线程01a1267d（本文不复制其未冻结改动）；ECS operator 是部署/数据消费/运行唯一 writer；其他作者只承担协调者明确分配的窄修复，不并行改相同文件。教学文档可并行。ECS epoch1 是现有预算 authority 唯一 writer，本机 fence 保持；用户本机 DB 准备任务独立，本文不安排同库迁移。总 DS 100万 input＋output；65569历史 tokens、UNKNOWN 和预占均保留，不因新 Run/进程/轮次退款。SF 报价为零不等于已核实免费结算。

状态词：**已验证**仅指所列实际命令/回执；**实现未验证**指接口存在但真实组合未跑；**缺失**指明确缺少实际消费/接线；**UNKNOWN**指当前证据不足。不得把缺证据一律判为缺代码。**未实现的命令不得报告通过。**

## 最关键差异与最小修复包

1. 正常两阶段 admission 在修；截至本方案证据截止点无真实三臂评分。旧构造要求先有精确 chat wire permit，再注册 native link/检索；不能用零 hash、可变 permit 或无门控调用破环。
2. 六包文件完整暂存、Linux 正常 prepared 校验已验证，但薄层没有实际 formal job 消费调度、逐批许可/claim、持久化 source→UUID 映射和整包索引结果。只补 operator 接线，复用正常 repository/port，不新建第二摄取器。
3. 已验证3READY只覆盖 MIRACL 小样本。整包 scope/index manifest、实际 candidate_pool 和完整 source ID 映射尚未冻结；已有全 corpus reader 不能证明全 corpus 已索引。748单元不等于5183＋6000＋400全语料。
4. **新增识别的调用合同缺口**：正常 `build_normal_paired_runtime` 对 `rerank_enabled` 报 `shared_retrieval_rerank_requires_separate_plan`；`PairedSharedRetrievalBinding.validate` 当前仅支持每QID一个 query_embedding plan。正常 reranker 已存在，但共享检索的独立 rerank 许可/批次/native claim 接线未验证。SETUP 若关 rerank 必须明示；完整 B 线要求 rerank 时由同一两阶段作者/指定窄作者补合同，不得静默泛化调用。
5. 评分消费缺口：Omni 正常解析结果→24页 `prediction.blocks` 协议；Hotpot source spans→sentence_indices；MML 原始页码约定→有效 locator。现有文档 qrels Recall 不能替代它们。
6. 现有 Langfuse wrapper 仅允许 answer_exact_match/answer_character_f1/citation_readback_rate；如果需要 retrieval/page 程序 Scores，仅补已算指标的 allowlist/映射和回读，不加 LLM judge。客户端实例/同步 I/O 限时/真实 flush-readback 尚需验证。
7. 新有限 Streams generation、后台任务入口、SSH断开后存活和 checkpoint 恢复尚无真实完整证据；普通 systemd 或 Python/Redis import 成功不能代替该验收。

修复包按依赖冻结：P1正常两阶段（唯一writer01a1267d）；P2 operator正式消费/ID映射/许可调度/固定组件入口；P3必要评分来源映射与Omni预测导出、程序Scores窄接线；P4独立后台包装＋checkpoint恢复。每包保留源 manifest/hash/import证据和旧失败，不上传全 dirty 仓库，不重跑已通过大套测试。现有代码能满足的节点只补实际证据，不新架构。

## 11节点闭环

### 1. 固定环境与唯一 authority

**输入→函数/CLI→输出**：冻结 release/依赖 lock、DB目标、模型/tokenizer和authority manifest → `build_container`、`V6LedgerAuthority.from_manifest/revalidate`、`V6OwnerActivityGate`、官方 `DeepseekCapacityProfile.from_manifest` / `XingCapacityProfile.from_manifest`、`require_read_only_connection` → 同一环境清单、CONFIGURED/MISSING状态、有限lease与真实 typed counters。锁定 rag_p7_r1/role rag-eval、OID19805、system_identifier7687882228252794550、schema0022_memory_vector_index；保护 study_platform 和旧8787服务。

**持久化/恢复/验收**：release source SHA、依赖版本、DB profile、模型完整身份、counter profile SHA和lease SHA为幂等身份；源漂移/身份不同/lease到期即停，不改旧Run。证据 E1/E4/E5：156文件编译/import、实际DB只读身份、官方DS/Xing各3正2负验证均已验证；hosted revision和实际上游 serving identity仍 UNKNOWN。**缺口/归属**：两阶段新包须重新冻结并只检必要delta；ECS operator签发新lease/profile、正常加载凭据且不上传.env；冻结 dependency inventory 与模型resolved_revision，UNKNOWN可显式保留，不能冒称已知权重。

### 2. 六包加载与 Gold 隔离

**输入→函数→输出**：`incoming/final-six-r2/<dataset>/manifest.json`＋原始对象＋prepared artifacts → 固定 loader SHA `336cc1c384d752b6d884b727eef05d073b0ae56a3b8d919c5634adc4d7c88b98` 的 `load_original_source/load_prepared_input`，operator `load_package_input` → exact bytes / `PreparedIngestionInput`。真实kind是 `original_sources`648（200/400/48）＋`prepared_input`100（73/24/3），总748；470是单独 artifact records。

**持久化/恢复/验收**：package manifest SHA＋dataset/unit_id＋source SHA为唯一输入；安全相对路径、bytes/hash、非alias校验失败即停该单元，复用已存在相同对象，不重传全库。Gold/answers/qrels/supporting facts/evidence_pages留在独立评测输入，不进 CAS/embedding/rerank/context；仅 question进入查询。E2：748原始对象hash暂存已验证；E3：六包各1真实样本、3prepared Linux正常校验已验证。**缺口/归属**：operator将同一loader接入正式调度，核验Gold隔离/背景池与正式40Q＋24页输入引用；MML3PDF partial标签保留。不宣称样本验证等于全748已消费。

### 3. 正常 document/version/job 与解析

**输入→函数→输出**：exact original bytes→`ContentAddressedStorage.put_stream`、`PostgresKnowledgeRepository.create_upload`、`VersionSource.from_row`；raw走 `process_job` 正常 ParserRegistry，prepared走 `validate_prepared_input` 后同一 `process_job(prepared_input=...)` → 正式ID、原文CAS、normalized/chunks/assets、quote/hash/locator、parser/chunker处理manifest。正常port已把 producer draft IDs重绑定正式IDs，无需新DTO。

**持久化/恢复/验收**：保存(dataset,unit_id,source_document_id)→(KB,document,version,job,sourceSHA)映射；已有同hash READY仅在独立identity证明后复用；禁止同filename/不同内容被静默当重试。由正常 `claim_job/renew_job` fence处理queued任务；prepared模块/库/配置/source不符即拒绝，不偷偷raw重解析；失败job和旧version保留。E3本地/Linux prepared重绑定使用明确SIMULATED IDs；E4实际raw3job成功已验证。**缺口/归属**：operator薄层 `bind_package_job/process_bound_package_job`是草稿/实现未验证，需真实raw＋prepared各1 formal绑定和正常解析消费、映射落盘；无须重写 parser/backend。

### 4. 分批真实 Embedding 与预算

**输入→函数→输出**：正常 child `embedding_content`＋formal version/job/确定chunkIDs → `PostgresKnowledgeRepository._embed_index_chunks`、`SiliconFlowEmbedding.embed`、`ProviderBatchPlan`/`FreeModelRequestContext`/`SiliconFlowFreeBinding`、`BudgetUsageGuard`/`PostgresBudgetGate.reserve` → 向量、native succeeded/UNKNOWN receipt、真实usage/占用。当前正常索引上限 `min(32,admission.max_batch_items)`，逐项token/总容量也必须满足真实 BGE admission；不是按字符比估算。当前固定1024维，不能把item上限当任意总tokens可用。

**持久化/恢复/验收**：batch_id=`version:job:number:total`，正常 `_planned_index_chunk_ids`、输入逐项SHA、wire/profile/plan SHA、native request ID、call ordinal/lease固定；原子预占先于transport。每批真实vectors/结果CAS/claim状态保存后继续；UNKNOWN先查原结果/原receipt，不能自动新ID重放；not_sent只有正常明确证据才释放，未定费用不清零。E4真实3Embedding/3READY已验证；整包逐批调用实现未验证。**缺口/归属**：operator从正常raw解析/实际prepared children生成精确 batch plans/permits，绑定同一writer；初始摄取串行、模型调用并发1，不新增并行model sender；纯文件hash可并行。不得由无发送transport脚本启动模型。

### 5. Child索引、READY与整包 manifest

**输入→函数→输出**：正式chunks/vector/profile→`process_job`正常最终事务、`_get_or_create_embedding_profile`、`keyword_candidates/vector_candidates`、`validate_index_identity` → 当前version READY、child-only `chunk_embeddings/chunk_terms`、独立整包indexmanifest。父块用于回读扩展，不入向量/BM25投票。

**持久化/恢复/验收**：version/source/parser/chunker/config/modelprofile/dimension/inputsemantics及实际chunk/vector数量/hash是index identity；READY/version切换与索引同一正常事务。中断不把partial发布ready；相同READY以原receipt复用，未知claims不重发。E5只读复核现有3READY＋identity冻结已验证，整包发布未验证。**缺口/归属**：operator核验所有纳入pool的单元状态，保留失败分母和不足说明，生成正式scope/source映射、counts/fingerprint及独立expected/actual；取真实candidatepool和合理干扰语料，不只索引答案文档。现有数据reader全corpus与748实际入库pool是不同层，必须分别记录；BM25已有正常SQL实现，无须另建全文服务。

### 6. Query、Hybrid/RRF、rerank、parent与Context

**输入→函数→输出**：question/q0＋服务端Scope＋READYidentity＋每stage许可 → `KnowledgeGateway.retrieve_question`、`HybridRetriever.retrieve`、`PostgresKnowledgeRepository.keyword_candidates/vector_candidates`、`rrf_fuse`、正常ranker、`read_parent_contexts/merge_passages`、`EvidenceService.bundle/with_context` → 原始排名、融合/rerank序、parent展开/去重与固定证据Context。

**持久化/恢复/验收**：保存query SHA、scope/indexSHA、每stage候选ID/order/score、模式/参数、源映射、quote/locator、Context及hash、真实providerreceipt；Scope/current-version/profile/child过滤每次成立。正常代码遇vector/ranker故障可降级，评测必须标 `VECTOR_UNAVAILABLE/RANKER_UNAVAILABLE`，不能当完整Hybrid＋rerank已验证。**当前**：正常算法实现；真实新query/retrieval组合未验证。**缺口/归属**：operator共享检索组件/观测计数/source mapper；两阶段作者或指定唯一窄作者补共享rerank独立许可/native合同（当前preflight明确拒绝无计划rerank）。A线所有三臂共用一次冻结结果；B线记录真实排名及降级，允许按已冻结参数评测，首轮不自动调参。

### 7. 两阶段准备与实际 wire admission

**输入→正常入口→输出**：合法查询任务/预算/有限lease → 正常native RETRIEVAL生命周期及共享query receipt → 冻结Context → 正常prompt/messages renderer、thinking disabled/output512 → 精确wire SHA/profile/token bounds与immutable answer permits → 正常Factory/CloudAdapter发送前guard。入口现为 `normal_setup_entry.run_normal_prepared_setup`、`build_normal_paired_runtime`、`run_public_paired_cohort`；最终ABI以01a1267d的新冻结包为准，不猜新CLI。

**持久化/恢复/验收**：query/context/renderer/wire/permit/Run-link/authority/lease组成准备checkpoint；prepared无send≠succeeded；只可从可验证结果封装，不填零hash、不改旧permit bool、不直接provider probe。E6已证实旧顺序循环，精确源码片段保存；**状态：实现修复中，未验证**。**归属**：01a1267d唯一writer；operator消费新冻结ABI，先无发送构造校验，随后代表raw＋prepared真实SETUP。旧07:44或16:00lease不复用。

### 8. 三臂正常答案与引用

**输入→函数→输出**：同QID/同源pool/同retrieval/同Context＋三arm独立native/Product Run → `run_public_paired_generation_case/invoke_paired_generation_arm`、正常 `LangChainQuickChain`/`GenerationRole`、`EvidenceService.validate_answer`、`_readback_citations`、`NormalPairedProductBridge` → CHEAP_ONLY/DYNAMIC/EXPENSIVE_ONLY答案、route、prompt/context/envelope hash、citation回读、native/Product状态和usage。

**持久化/恢复/验收**：armRun uuid5(cohortRun:qid:arm)、正常request_identity、nativeAttempt、providercall、case_result固定。Memory关闭、阈值0.35、output512、统一timeout、fallback/escalation关闭；DYNAMIC真实选中哪个模型如实记录，不硬称全部便宜。真实3arm皆succeeded且无error、pairing hashes一致、引用指向当前有效原文才是完整单QID闭环。checkpoint恢复跳过已完成case，partial/UNKNOWN保留且不自动重试。C1 native状态修复窄验证已通过（E1）；真实完整三臂 **NOT RUN**。**缺口/归属**：operator真实SETUP及对桥接/terminal/stream的必要验证；代码异常交唯一作者，不把测试fixture通过当真实验收。

### 9. 两条验收线与数据集评分

**A 路由公平线**：固定同QID、同Context与证据，只比较三臂有效答案、route、usage/延迟/成本审计、citation及validation。**B 解析/检索质量线**：以数据集独立Gold检验原始解析与排名/Context覆盖；低质量Context下的公平三臂不能证明检索好，好的检索也不能证明回答好。Gold只在identity/scoring侧读取，不参与检索/排序/输入选择。

**输入→函数→输出**：冻结40Q（原24＋Hotpot8＋MML8）与Omni24页、实际ranked source/annotations/解析prediction/答案 → `prepare_full_cohort`（或CLI `--phase paired-prepare-special`）、`score_answer`、`score_retrieval_annotations`、qrels Recall/MRR/nDCG/context评分、`omni_contract.page_input_contract/selected_page_gold/score_page_parsing` → 每dataset分子分母、适用性、缺Gold/失败/降级统计，独立A/B报表。

**持久化/恢复/验收**：cohort/source/special-freeze/Gold/prediction/评估器SHA冻结；case/页评分落盘，聚合只读已保存结果。SciFact/MIRACL16题只有qrels，不造QA答案/准确率；LongBench8有answerGold。Hotpot8官方answer EM/tokenF1实现；supporting facts需实际sentence级来源映射，official joint QA仍NOT_EVALUATED。MML8保留原typed答案/Not answerable/格式，现为PROJECT诊断EM/字符F1，official GPT judge NOT_RUN；当前页索引约定UNKNOWN时不计页准确率，`score_retrieval_annotations`尚无MML实际页指标分支。Omni24是官方原始解析Gold，正常parser输出需转为page_id/imageSHA/blocks(poly/category/text/html/latex/order)；现评分为PROJECT layoutF1/NED/order，不能称官方完整Omni分。官方Omni QA不存在，不新增LLM judge/事后编QA；若无独立预模型QA overlay，正确标QA不可用，仍完成24页解析评分。

**状态/缺口/归属**：评分函数已实现，真实40Q＋24页消费未验证；operator冻结Linux40Q/Gold引用并提供source mapper/解析输出；指定评分窄作者只补必要来源/页适用性和Omni导出。先确认证据与输入是否已存在，不重写scorer。完整结果应有120arm cells，24页prediction和评分/明确失败；QA的实际有Gold分母最多24（LongBench8＋Hotpot8＋MML8），不是40或64。

### 10. Trace＋程序Scores

**输入→函数→输出**：已保存Run/QID/arm、hash、真实usage及已算numeric scores → 正常审批/egress下构造Langfuse SDK client，`public_langfuse.write_and_readback_public_event`（start observation/create_score/flush/observations.get_many/scores_v3.get_many_v3）、`replay_public_paired_telemetry` → trace_id/score_id及独立回读receipt。

**持久化/恢复/验收**：trace/score稳定uuid5(run:qid:arm[:metric])；不发送正文/keys，实际费用UNKNOWN不写成核实零。写入不确定只保留unknown，不能重跑生成；恢复先查稳定ID，可用明确telemetry-only replay补已存评分，须避免无条件重复写入。E7仅CONFIGURED与接口存在；实际trace/score/flush/readback未验证。**缺口/归属**：operator客户端注入、限时I/O、真实回读；评分作者如有需要仅补retrieval/page已算数值allowlist。Trace是独立验收：失败不销毁答案，但整体Trace节点不能报PASS；无新LLM judge。

### 11. 脱离SSH、checkpoint恢复与最终落盘

**输入→正常入口/已有设施→输出**：冻结operator入口/参数/新lease/单writer/run-state＋private Redis prefix → 既有隔离目录的有限systemd任务（不是shell前台SSH），普通native/Product lifecycle/Redis Streams/SQLite state → 后台运行、可回读checkpoint/日志、结束receipt和报表。具体后台wrapper/CLI在冻结入口确定后由operator填写；当前不能把 `systemd-run <占位命令>`当已实现命令。

**持久化/恢复/验收**：保存T0/deadline、source/index/data/profile/authoritySHA、Run/lease、输入映射、job/providerclaim、resultCAS、Context/permit/case/page/Trace状态。原unit Restart=no；Redis Unix socket/AOF保持隔离，旧失败unit/日志保留，无业务端口/旧服务改动。SETUP在安全已落盘边界断开SSH：从新连接确认PID/checkpoint前进；安全结束/重启任务（非UNKNOWN强杀）后从同一有效lease与状态读取，已succeeded调用数量不增、UNKNOWN不重放。lease已过期只读旧checkpoint，另发新受控Run/恢复决策，不改老截止；真实未完成状态如实保留。

**状态/缺口/归属**：已有PG/Streams/lifecycle及native幂等接口实现；旧有限Redisgeneration已到期；新后台wrapper、真实断线/恢复/收费无重复证据缺失。ECS operator独占执行。正式截止前停止新派发并保存checkpoint；不得把评分聚合无限延后5h。最终本地/远端manifest、状态/分母/命令exit、费用/UNKNOWN、失败及报告hash齐全，协调者审查后才能判断完成；本文不授予验收或Tag。

## 最短执行路径与并行边界

**G0（无发送）**：父协调者收01a1267d最小修复包及必要反例，冻结新source/依赖 → operator新lease/profile、当前authority/DB/stream/凭据状态核验 → 固定raw＋prepared各1输入、映射、精确batch计划。不要再跑全仓审计/旧大套测试。缺失项仅做对应窄修复。

**G1（SETUP）**：实际raw＋prepared正常入库/Embedding/READY → 正常query及必要rerank → 两阶段wire绑定 → 同QID三臂含适用评分/真实引用回读 → trace程序Scores回读 → 安全断线/恢复证明不重复发送。保留旧3READY；最小2个代表输入/适用QID即可，不扩大成全748 preflight。若representative无对应Gold，另用冻结有Gold QID完成评分，不能自编。

**G2（BATCH）**：仅G1关键闭环通过且协调者确认实质结果后，第一项正式批量操作前原子写T0/5hdeadline → 748单元正常摄取/索引（已READY独立核验复用）→ 整包manifest/Scope固定 → 40Q×3arm＋Omni24页评分 → Trace与只读聚合/结果落盘。每轮原生有限lease≤1h且不得超过BATCH截止；不足95s不派发新三臂QID。首轮结束不自动调优、不补发UNKNOWN。

允许并行：01a1267d代码修复、评分来源/输出协议只读整理、无模型文件hash/Gold映射检查、教学文档；各自独占文件。正式消费、authority/预算账本、DB写、模型发送、索引发布仅ECS operator一条链；解析CPU独立只读任务须确实不碰同job/CAS发布，ECS2vCPU约3.5GiB先串行测峰值再决定是否有必要并行。禁止并行本机备用调用或第二预算writer。

## 最终完成标准

G1真实raw＋prepared消费、三臂含评分和断线恢复均有命令/exit/receipt；G2每个单元/每QID/每页状态有明确分母，完整成功与partial/BLOCKED区分；A线同源同Context公平性独立通过，B线各dataset适用Gold的检索/解析/答案指标独立呈现；Trace写入/flush/回读与numeric Scores证据齐全。所有artifact/model/索引/代码/迁移/lease/authority身份可回读；唯一账本累计占用无重置、无重复收费，历史失败/dirty/UNKNOWN保留；无私有正文/秘密外发、无旧业务服务/库改动。由协调者/Owner审阅原始问题/参考/Context/答案作语义验收，自动PASS不代替批准。

## 证据索引与本次交付

E1 `var/reports/v6-ecs-resume-r1/native-status-release-prepare-r6-1.json`：156文件no-send compile/import，exit0；source manifest SHA8fe94b26d7e7569ab5613d641d22d5e4a9c922ebdae96fedb751d0b7b6901d07。新两阶段源包尚不在该证据内。

E2 `six-data-completion-packet-r3-1.json`：748原始对象完整暂存，prepared consumption NOT_RUN。E3 `package-binding-linux-r6-2.json`及`operator-link-increment-r3-1/offline-package-binding-r6-2/report.json`：Linux/本机六样本＋三prepared正常校验，exit0；IDs明确SIMULATED，DB/model0；远端结果SHA06a4582479cbf8c87e1b6300cb32940bab6f41854e959dddd44d9cd46541a2a5。

E4 `first-normal-index-r3-1-repair1.json`、`index-current-inspection-r6-1-repair1.json`：真实3SF Embedding及3READY；独立readonly复核exit0，历史53/65569及14UNKNOWN/10microunits占用保留。E5 `freeze-existing-index-r6-1-repair1.json`、`runtime-data-view-r6-1.json`：仅3READYidentity及正常全corpus reader，exit0；不要扩大为六包索引成功。

E6 `operator-link-increment-r3-1/wire-stage-assessment-r6-2.md`、`answer-wire-preparation-constraint-r6.json`：旧接口构造顺序和2个明确SIMULATED API输入校验；真实检索/Chat/三臂NOT_RUN。E7 `paired-profiles-r6-1-repair1.json`：官方本地计数DS/Xing各3正2负、CONFIGURED状态，exit0，hosted probes0；旧lease16:00到期。

本次仅写本MD和同名JSON；执行的是本地文件读取/接口检查/JSON完整性检查，未运行真实服务/测试/SSH/API/模型、未部署/变更DB或lease。HEAD befd5478a680ffa779a1e8ede736260d9bac1060，未commit/push。方案完成可供父审，真实闭环状态仍BLOCKED；源代码、离线合同和真实服务状态分别报告。
