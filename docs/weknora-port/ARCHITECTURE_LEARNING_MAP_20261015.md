# WeKnora Python Port — 架构与数据流学习地图

> 面向新贡献者的导航文档。行号基于 git HEAD `e22e428`。所有路径均属 `backend/weknora_port/`。
> 只做导航，不作为验收证据；未列符号表示本次未核实，宁缺勿猜。

## 1. 运行入口与装配

| 文件 | 符号 / 行号 | 作用 |
|---|---|---|
| `entrypoint.py` | `create_app` L7；`main` L48 | 开发 API 工厂；懒启动 runtime，注册 `/healthz` L28、`/readyz` L30 |
| `worker.py` | `run_once` L5；`serve` L8；`main` L17 | 仅消费图像入库(`image_tasks.run_due`)与记忆抽取(`memory_enqueuer.run_due`) |
| `initialize.py` | `initialize` L5；`main` L31 | 仅对**空新库**建 schema，不在 API 启动时运行 |
| `development.py` | `DevelopmentSettings` L13；`own_storage` L53；`assemble` L66 | 显式装配。L84 校验密钥，L92 校验 schema，L98-121 注入权威，L151 构造 `ApplicationRuntime` |
| `authority.py` | `AuthoritySettings` L20；`load_authority_settings` L38；`ConfiguredTokenAuthority` L97；`ServerPermissionResolver` L138；`runtime_factory` L160 | 合成 Token 权威 + 校验，`runtime_factory` 包装 `assemble` |

要点：`assemble` 是唯一的真实装配点，可选依赖（model_factory/permissions/costs/principal/authority_settings）只作测试或服务端注入，绝不来自请求 JSON。

## 2. 模型传输 / 发送门 / Token 预算 / 用量记账

| 文件 | 关键符号 / 行号 | 一句话职责 |
|---|---|---|
| `models/transport.py` | `call_lifetime` L52；`DenySendGate` L43；`configured_endpoint` L104；`endpoint_urls` L143；`validate_endpoint` L155；`ResponseHandle` L162；`AsyncHTTPTransport.open` L216 | 统一 HTTP/SSE 传输；默认门拒发；发送前后走 gate `authorize`(L241)/`finish`(L348) |
| `models/provider.py` | `ProviderModel` L104；`chat` L148；`vision` L189；`chat_stream` L203；`embed` L388；`rerank` L434 | 协议配置可变、传输不变；`build_request_body` L117 校验输出预算 |
| `models/usage_facts.py` | `SIMULATED_USAGE` L8；`UsageValue` L12；`ProviderUsageFacts` L20；`capture_usage` L53；`aggregate_facts` L92 | 只保留原始出现证据，非估算/发票；`accounting` L36 定下界与结算 |
| `models/synthetic.py` | `transport_handler` L8 | 代码自有封闭夹具（`fixture.invalid`），非授权托管客户端 |
| `source_facts.py` | `OperationSlot` L14；`operation_slot` L28；`SourceSlice` L47；`SourceFacts` L58；`BoundCallFacts` L72；`bind_synthetic` L80 | 不可变输入证据，区别于授权与真实用量 |
| `storage/send_gate.py` | `ContentOrigin` L19；`SendAuthorization` L28；`P1SendGate` L91；`_permission` L144；`authorize` L195；`finish` L242 | 失败关闭的发送授权 + PG 月度账本(money) |
| `storage/token_budget.py` | `fingerprint` L13；`TokenBudgetStore` L22；`reserve` L27；`finish` L88 | Token 单位的月度预算/占位/结算（走 `port_call_slots`、`port_token_calls`） |
| `storage/token_migrate.py` | `apply_token_schema` L7 | 显式隔离迁移，校验源码 checksum |
| `storage/migrations/014_token_authority.sql` | 全文（`port_call_slots` L2、`port_token_budget_months` L7、`port_token_calls` L17） | Token 权威表；历史金额列不复用 |

## 3. RAG 主链数据流（按阶段顺序）

| 阶段 | 文件 · 符号 / 行号 |
|---|---|
| 上传 | `documents.py` · `DocumentLifecycleService.upload_document` L255 → `_process_document` L311 |
| 解析/入库 | `ingestion/core.py` · `ingest_document` L575、`parse_document` L534（`_process_document` 于 L322 调用） |
| 切块 | `ingestion/core.py` · `split` L1266、`split_parent_child` L1298；装配为落库计划 `storage/chunks.py` · `prepare_chunks` L67（documents.py L338/L368 调用） |
| 嵌入 | `documents.py` L377 `embedder_for_kb`；`storage/indexing.py` · `HybridIndexer.batch_index` L75、`batch_embed_with_backoff` L54 |
| 建索引 | `documents.py` L389 `create_chunks`(`storage/knowledge.py` L167)、L402 `HybridIndexer.batch_index` |
| 混合检索 | `retrieval/search.py` · `search` L120；`storage/hybrid.py` · `PostgresKnowledgeRepository.hybrid_search` L34 |
| 融合/去重 | `retrieval/fusion.py` · `fuse_or_deduplicate` L88 |
| 重排 | `retrieval/rerank.py` · `rerank` L81（MMR L66） |
| 合并/扩邻 | `retrieval/merge.py` · `merge` L371 |
| TopK/亲和 | `retrieval/filter_top_k.py` · `filter_top_k` L25；`retrieval/memory_affinity.py` · `apply_memory_affinity` L11 |
| 上下文构建 | `retrieval/context.py` · `into_chat_message` L88；生成侧 `generation/context.py` · `prepare_generation` L140 |
| 编排 | `retrieval/pipeline.py` · `RetrievalPipeline.run` L78（understand→search→rerank→merge→filter→context） |
| 生成/协调 | `generation/coordinator.py` · `GenerationCoordinator` L53、`run` L344、`_attempt` L86 |
| 路由（阈值 0.35） | `generation/router.py` · `RouterPolicy` L9（默认 `Decimal('0.35')` L11）、`decide` L44（role 判定 L55） |
| 升级策略 | coordinator `run` L380：DYNAMIC 下 cheap 首答存在 objective 才升级到 expensive；`generation/router.py` · `MODELS` L6（cheap/expensive 角色模型） |
| 引用/参考 | `references/registry.py` · `SourceRegistry` L109；`references/decoder.py` · `CitationStreamDecoder` L7；coordinator `_references` L294 |
| 流式/SSE | `streaming/manager.py` · `RedisListStreamStore` L27；`streaming/events.py` · `to_sse` L73；`streaming/replay.py` · `finalize_turn` L39、`ReplayService` L48 |

## 4. 会话/记忆队列/文档版本/图像任务

| 主题 | 文件 · 符号 / 行号 |
|---|---|
| 会话 & 运行 | `service.py` · `RuntimeService` L116、`accept_turn` L146、`run_turn` L339、`replay` L361 |
| 运行时装配 | `runtime.py` · `ApplicationRuntime` L199、`_configuration` L369、`_pipeline` L473、`_generation` L481 |
| 会话/记忆服务 | `conversation/__init__.py` 导出 L6-14：`OrdinaryHistoryService`(history.py)、`MemoryService`(memory.py)、`MemoryManager`(management.py)、`ExtractionWorker`(extraction.py)、`TopicService`(topics.py)、`MemoryMaintenance`(maintenance.py) |
| 历史会话存储 | `runtime.py` L220 `PostgresTurnRepository`；`storage/turns.py` |
| 流存储 | `streaming/manager.py` · `RedisListStreamStore` L27 |
| 记忆抽取队列 | `storage/memory_queue.py` · `RedisMemoryExtractionQueue` L103、`run_due` L152；runtime 装配 L240-245、`memory_worker` L243 |
| 文档版本 | `documents.py` · `_process_document` L311（`source_version`）、`_finalize` L595、`_document_row` L629 |
| 图像任务 | `image_tasks.py` · `ImageTaskQueue` L41、`create` L49、`run_due` L180、`cancel` L124；装配于 runtime L255-257 |

## 5. HTTP/SSE 表面（`http_api.py`）

| 路由装饰器 / 行号 | 说明 |
|---|---|
| `create_app` L262 | 组装 FastAPI；`_response_stream` 生成 SSE L237 |
| `POST /api/v1/sessions/{session_id}/stop` L367 | 停止生成 |
| `GET /api/v1/runtime` L470 | 运行身份 |
| `GET`/`POST /api/v1/knowledge-bases` L478 / L482 | 列/建知识库 |
| `GET`/`POST /api/v1/knowledge-bases/{kb}/documents` L492 / L496 | 文档列表 / 上传 |
| `POST /api/v1/settings/vision-models` L519 | 注册视觉模型 |
| `GET .../documents/{doc}/image-task` L532 / `.../cancel` L537 | 图像任务状态/取消 |
| `GET`/`PUT /api/v1/settings/model-configuration` L542 / L547 | 读/写模型配置 |
| `GET`/`POST /api/v1/sessions` L562 / L566 | 会话列表 / 创建 |
| `GET`/`POST /api/v1/sessions/{id}/messages` L570 / L574 | 消息列表 / 发起(SSE) |
| `GET /api/v1/sessions/continue-stream/{id}` L589 | 续流(SSE) |
| `GET /api/v1/chunks/{chunk_id}` L598 | 片段回读 |

## 6. 建议阅读顺序

1. `entrypoint.py` — 入口与懒启动
2. `development.py` — `assemble` 装配全貌
3. `runtime.py` — `ApplicationRuntime` 核心门面
4. `service.py` — 一次 turn 的编排(`run_turn` L339)
5. `retrieval/pipeline.py` — RAG 阶段顺序
6. `generation/coordinator.py` — 生成与升级
7. `generation/router.py` — 路由(阈值 0.35)
8. `models/transport.py` — 传输与发送门
9. `storage/send_gate.py` — 授权/账本
10. `http_api.py` — HTTP/SSE 线协议

---
导航辅助文档，非验收证据；基于 git HEAD `e22e428` 阅读时点编写。
