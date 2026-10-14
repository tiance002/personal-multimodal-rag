# Task 1 — 图片后台处理主链交接 Goal

Owner 最新分工：任务一由 Owner 在本机 Codex 派发，原线程继续其他剩余项。本任务不是整体 Goal 重启，也不是新生产模型选择授权。

## 任务与基线

从确认源码 SHA b961e7a7513a97b08b993c4aa0bbf892576af4d1 创建独立 E 盘 worktree/branch，建议 E:/RAG-Worktrees/weknora-image-worker-20261014，分支 codex/weknora-image-worker-20261014。原执行工作树 E:/RAG-Worktrees/weknora-python-port-20261013 不允许与本任务并发写。原项目 E:/RAG quention 及其 .serena/project.yml dirty 保持不变；不用旧 C 盘候选开发，不重置或覆盖历史代码。

先自行完整读取 docs/weknora-port/task/ 下的01、02、03三份 Markdown及 README；02_Codex完整执行Goal.md为全 Goal 执行指令。本文件仅把其中图片范围分配给本任务，不能取代原文。读取项目 AGENTS、相关 .agents/skills 与当前 progress/ADR；旧 QuickChain 常驻规范不覆盖最新批准的 WeKnora 主链重构。固定上游只读 E:/RAG-reference/WeKnora-3e8b0bfc，SHA 3e8b0bfc80b845b2d4b2ed683994748741450a97。

交接时图片业务改动未提交文件：NONE。已有 SourceImageProcessor、OCR sanitizer/table、源 OCR prompts 均已提交；最新图片规则实现16dd067包含在基线中。不需要图片 patch/cherry-pick。原线程只有新安装产物验证回执未提交，忽略目录中的安装 runner/checkpoint；不属图片 worker 实现。创建 worktree 前再核对准确 SHA/dirty。

## 明确实施范围

实现固定上游可配置的图片 OCR/Caption 后台流程：有效模型配置优先级、图片任务持久化、源失败重试、中断恢复、孤儿处理、pending计数/finalize及后续摘要调度，并正式进入 Python/FastAPI 上传/索引主链。仅新增必要实现，不重做已经通过的 Excel/Office/OCR HTML规则或全 frozen 数据集。

优先源码入口：
- internal/application/service/image_multimodal.go: Handle, resolveVLM, shouldDropOrphanedMultimodal, indexChunks, readImageBytes, checkAndFinalizeAllImages, enqueueKnowledgePostProcessTask, buildVLMOCRPrompt, buildVLMCaptionPrompt。
- internal/application/service/knowledge_process.go: enqueueImageMultimodalTasks 及未入队 slot 的释放/finalize流程。
- internal/application/service/knowledge_process_config.go: ResolveProcessConfig、resolveFileImportProcessConfig/验证；VLM覆盖替换后空 description_language/custom_instructions回填KB；nil与false不可混同。
- internal/types/knowledge.go: metadataKeyProcessOverrides为 process_overrides，ProcessOverrides/SetProcessOverrides；internal/types/knowledge_process.go、task.go: payload、队列与配置类型。
- internal/types/knowledgebase.go: VLMConfig.IsEnabled，新式 enabled+model_id 与旧式 model_name+base_url兼容；resolveVLM优先ModelID，否则legacy接口。
- knowledge_multimodal_slot_release_test.go、image_multimodal_orphan_test.go、image_multimodal_prompt_test.go 与相关源测试作为行为fixture候选，不仅照搬断言文字。

现有 Python 入口：backend/weknora_port/documents.py.upload_document 在 parse后同步调用 asset_processor.process_plan，随后批量存储/index/postprocess/finalize；runtime.py 装配 SourceImageProcessor；image_multimodal.py.process_plan 同步逐图 OCR/Caption，共用有效VLM，aux不走P5。当前 upload API尚不接受process_overrides。这不是完整持久后台 worker，需据源补齐而非宣称现有同步链等价。

数据入口：knowledges.metadata/source_version/parse_status、chunks父片段/图片类型/image_info、embeddings索引、Redis队列及计数；任何新队列表/迁移都先提交方案与串行整合范围。范围必须由服务端owner/tenant/KB/document/source_version确认；处理期间版本变化、删除、跨库/孤儿不能落错资料。

## 文件所有权与整合规则

本任务独占候选：backend/weknora_port/image_multimodal.py；新增图片专用模块例如 ingestion/image_tasks.py、storage/image_tasks.py（实际命名按代码结构）；新增 tests/weknora_port/test_image_worker* 与图片fixture；docs/weknora-port/task1-image-*专项证据。

共享文件仅本任务独立 worktree提出最小修改，在原线程串行审查/整合：documents.py、runtime.py、http_api.py、storage迁移/队列共用模块、model_registry.py、model配置/DTO、pyproject.toml和所有锁文件、native_fixture.py/test_runtime_pg.py。不得与另一线程在同一工作树写，不能直接覆盖其修改。提交列出共享修改与依赖，原线程在获知状态后串行cherry-pick/集成。先不要动OCR HTMLrenderer依赖实现或通用记忆UI。原线程写域为 frontend/src/weknora_port/记忆管理UI及非图片测试/恢复证据，其余共享后端同样串行协调。

## 模型和安全边界

真实 OCR/Caption provider/model选择待定。旧 PyMuPDF/Tesseract文字OCR与旧VLM图像描述是不同路径；当前新链闭合mock的siliconflow/Qwen/Qwen2.5-VL-72B-Instruct共用VLM不等于Owner批准生产模型合并。固定上游支持可配置VLM接口，并不强制DeepSeek；不得自行改真实模型配置，不读取/输出密钥，不发真实供应商probe（免费同样禁止）。所有实际模型验证仅标NOT RUN/待独立真实门授权。

OCR系统prompt不接custom_instructions；Caption才接description_language与业务指令。Chat Cheap/Expensive路由不能替换Embedding/Rerank/VLM/OCR/Caption。保持P5阈值0.35、T/C/J/D/L/M、q0与Rewrite逐项max以及原升级开关；不得新增GraphRAG、Agent工具、代码沙箱、旧Validator/TaskFence/输出探针。

Python业务规则不等于所有底层代码Python。现有OCR HTML渲染依赖 E:/WK/deps/htmlcodec/htmlcodec.exe 是Go构建的第三方html-to-markdown/v2 v2.5.1+x/net v0.56.0 primitive；hash绑定，测试Go源码oracle另存、不用于runtime。该边界保持透明，不把它写成纯Python/完整平台无关wheel。PNG/PDF原始字节与历史版本、有效可回读引用、隐私/权限/费用/幂等/UNKNOWN安全规则保留，不释放UNKNOWN，不删除旧证据。

## 隔离资源与运行

现有新隔离PG weknora-python-resume-pg-20261013，127.0.0.1:56423，paradedb/paradedb:v0.22.6-pg17，physical7696088403504697382；Redis weknora-python-resume-redis-20261013，127.0.0.1:56424，redis7.4.2。同名卷保留。二者刚因Docker退出后由原线程恢复；Redis run_id会变化，必须核验容器ID/volume/endpoint再更新夹具身份，不移除断言。旧业务DB25438、旧WeKnora-*、其他历史容器一律不使用/修改。可新建本任务独立PG/Redis/存储用于隔离测试；如复用现有测试服务，必须唯一synthetic owner/tenant、队列前缀和E:/WK/run子目录，迁移/全局资源变更串行执行，禁止flushdb或清理历史租户。

只读参考及新依赖：E:/WK/deps/reader、E工作树var/weknora-port/runtime-deps；E:/RAG quention/.venv只读可执行Python；GoSDK E:/RAG-reference/toolchains/go1.26.0/go/bin/go.exe，官方已验证模块D:/WK/go/modules，新buildcache E:/WK/go/cache。新增依赖先核实版本与安装/import，只在新E环境构建，下载D。不改系统ACL、不绕权限/安全拒绝。

## 必需验收

1. 固定Go同fixture与Python有效配置对照：nil、显式false、VLM replacement、空字段继承、upload>KB、现代/legacy优先级、语言/Caption指令仅影响正确prompt；实际HTTP上传证明生效及持久化，辅助模型不被P5替换。
2. 实际上传返回/状态、图片父chunk、OCR/Caption独立失败分支、索引与原始资料readback；不能仅helper单测。按源比较可观察事件/状态/调度。
3. seed所有图片pending slots，部分无法入队释放、全部未入队、siblings先完成、context取消；最后一个slot触发后处理，避免重复finalize/摘要调度。
4. 任务重复消费、崩溃/进程中断与重启后恢复、retry/backoff/最大尝试、Redislease/ack/archive语义；失败证据保留。当前90Go backoff fixture仅公式证据，不代表完整Asynq等价。
5. 文档删除、版本替换、孤儿、暂时数据库错误、跨库/跨租户、隐私拒绝/预算/UNKNOWN/取消；拒绝不导致额外供应商调用、不释放不明费用，不把模型生成OCR假称原文文字。
6. 正式native入口隔离PG/Redis/closed MockTransport验证，并与固定上游同fixture比较；未实现命令不得报告通过。重用已通过规则测试，只测新增影响与必要不变量，不重跑全六数据集。

每批报告实际文件/上游函数/配置优先级/原样命令/退出码/证据路径/本地commit。分开SOURCE_READ、IMPLEMENTED、OFFLINE_PASS、ISOLATED_INTEGRATION_PASS；REAL_PROVIDER_PASS/CUTOVER_PASS保持NOT RUN。重大SOURCE_PARITY/安全冲突给最小证据与方案，不私改上游断言/阈值。持续完成离线隔离部分，不因真实模型门未批而停所有编码。禁止push/tag/release、替换现有本机/ECS服务或配置、旧业务DB变更、UNKNOWN清除、历史证据删除。

交付可回滚本地提交和共享文件列表，由父级独立审查、串行整合。不要自称全Goal完成或已验收。


## Superseding Owner correction after initial handoff

Independent local OCR text recognition plus separate configurable VLM Caption is now Owner-approved source adaptation. Read OCR_CAPTION_OWNER_ADAPTATION_20261014.md; its latest decision replaces the shared-VLM assumption above. Real provider/model choice and calls remain unapproved. Owner task1 worktree E:/WK/image from b961e7a, own DB/Redis queue prefix; no shared writable DB/queue with original thread. Existing RedisMemoryExtractionQueue is memory-specific, ExtractionPayload and max_retry2; only source_retry_delay is a general reusable formula. Do not repurpose it as a complete image Asynq protocol; new common interfaces require explicit compatible contract and serial integration.


Latest Owner refinement supersedes absolute independent model selection: configurable OCR engine/model abstraction; explicit Caption/VLM config first, otherwise reuse OCR model only with established image-description capability. Tesseract cannot be VLM; OCR continues and Caption explicitly unavailable. Explicit Caption failure does not fallback; shared model does not merge requests. Capability registration policy still being finalized by parent, do not expand it autonomously. See latest section of OCR_CAPTION_OWNER_ADAPTATION_20261014.md.
