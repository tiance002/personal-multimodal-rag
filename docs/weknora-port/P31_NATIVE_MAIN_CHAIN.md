# P31：恢复固定 WeKnora 主链

基线：`84bd4bb09f29fdf96fd239fc68898b34d41eb990`。上游始终为 `3e8b0bfc80b845b2d4b2ed683994748741450a97`，未使用最新分支。

## Owner 决策与行为变化

本轮 Owner 明确撤销 Python Port 的最终严格原文证明、账户 Token Authority 发送阻断及 image_pages 测评专用生产路径。该决定取代本 Port 先前相冲突的预算/证据前置要求；不修改旧 ADR、Gold、结果、账本或 UNKNOWN。

| 范围 | 修改位置 | 回退后行为 | 固定上游依据 |
|---|---|---|---|
| 证据 | `runtime.py::_pipeline`；删除 `_source_merge_input/_project_source_evidence`；`retrieval/pipeline.py::run` 删除专用回调 | Search/Rerank→Merge→Parent/邻块→TopK→Context。无需额外 source_version/offset/quote/SHA 证明。Summary 保留其类型与来源进入普通路径，不宣称逐字原文。 | `knowledgebase_search_results.go` Summary 可检索；`chat_pipeline/merge.go` 无强制 Summary 排除；`into_chat_message.go` 使用合并内容；`references.go/types/chunk.go` 保留类型及身份。 |
| 模型 | `development.py::assemble`、`runtime.py::LeasedSendGate`、`entrypoint.py`、`worker.py` | 正常注册表及凭据→Provider 请求→真实 usage。生产不装配 ApprovedTokenAuthority、SourceFacts Meter、预算预留、月账本校验或结算阻断。 | 固定上游 `models/chat`、`models/embedding`、`models/rerank`、`models/vlm` 的正常 Provider 请求/usage 接口；Python 仅保留必要隔离与持久化适配。 |
| 图片 | `http_api.py`、`documents.py`、`image_configuration.py`、`image_tasks.py`、`ingestion/core.py`、`ingestion/upstream_pdf_parser.py` | 完整 PDF 正常逐页文本/扫描页/嵌图处理；不筛物理页、不额外渲染指定文字页。OCR/Caption 独立配置、任务版本和文档关联仍保留。 | `docreader/parser/pdf_parser.py`；`image_multimodal.go`；`knowledge_process.go`。相关8个 PDF 函数 AST 与固定上游一致，详见 image-notes。 |

没有修改 Query Rewrite/Expansion、Hybrid/Weighted RRF、Rerank 默认和阈值、MMR、Merge 算法、ContextHeader、P28 model/fixed Fallback、动态路由或升级规则、前端及输出标签清理。

## 保留的正常边界

- Tenant/KB/Document 权限、注册模型所有者与启用状态；原有索引、文档版本和图片来源关联。
- 全局外发开关与 KB `cloud_allowed`；本地模型不因云外发关闭而被误拒绝。网络、数据库及权限错误保持真实错误。
- 单次输出上限、Provider 参数映射、超时、取消、安全凭据、受控升级和 SSE/历史持久化。
- 新请求的持久化操作身份先写 `UNKNOWN`，回执后记录实际 outcome/usage；中断不自动重发同一操作。历史同操作的 `port_call_slots` 仍拒绝重放，**其他历史 UNKNOWN 或账户余额不阻断新操作**。
- Usage 写入失败会记录错误并保留 UNKNOWN，不再推翻成功回答或触发重新发送。统计缺失时如实报告，不虚构结算。

**撤销后不再提供原先的账户级硬 Token 预算保证。** 不设替代月锁、围栏、信用额度或新计费系统。

## 配置与迁移

普通入口继续为：

```powershell
python -B -m backend.weknora_port.entrypoint --host 127.0.0.1 --port 8000
python -B -m backend.weknora_port.worker
```

沿用已有显式隔离数据库、Redis、存储、密钥文件、模型目录配置。允许云端时设置 `WEKNORA_PORT_CLOUD_ALLOWED=true`，并通过正常 API 打开该 KB 的外发许可；这只是基本外发开关，不是额度证明。默认仍为 false。

新增一个无账户/预算字段的使用量表 `port_provider_calls`（迁移015），仅保存操作身份、Provider、模型、wire hash、outcome 和 usage；不记录 API Key、Prompt 或图像正文。旧计费表不能在不伪造预算预留的情况下充当此记录，故不复用或改写其历史行。

在已有正确环境变量下显式执行：

```powershell
python -B -m backend.weknora_port.storage.provider_calls_migrate
```

启动只检查迁移，不自动 DDL。旧 `--authority-config` 不再支持；显式注入旧 authority_settings 返回 `LEGACY_AUTHORITY_CONFIG_NOT_SUPPORTED`。历史研究工具与其材料保留，其旧围栏启动流程不代表当前产品入口。

上传或 KB 配置传入 `image_pages` 明确返回422；历史配置/任务 JSON 不破坏性迁移，处理时不再应用页筛选。图片模型、Tesseract、DeepSeek 与 SiliconFlow 的已有配置能力均保留。

## 验收证据

**最终状态：P31_COMPLETE。** 运行证据：`E:/WK/run/p31-20261017`；机器结果见本报告同名 `.results.json`。新原始证据打包在 `docs/weknora-port/evidence/p31-native-main-chain-20261017/`，真实结果与离线测试分开。

- 定向离线：`native-tests.stdout.txt`，229 passed，退出0；包含正常混合检索/生成/历史及新的 Summary/图片文本、权限、usage、未知发送不重发反例。
- 图片离线：`image-tests/run-03.log`，39 passed，退出0；原生混合 PDF 路由、旧字段拒绝、图片任务正常校验。尝试收集较宽 ingestion 测试时缺少独立 `pptx` 依赖，退出2，保留 run-02，不为本轮安装或扩大处理。
- 首次诊断脚本路径/事件循环及迁移 guard 配置问题保留；修正工具装配后继续，无重复云请求。

### 最小真实闭环

| 用例 | 真实执行结果 | 判定 |
|---|---|---|
| A：row-0959 | 新会话、正确 KB、document_scope=[]；Search30→Rerank2→Merge2→Context2→Cheap。实际请求包含 ionosphere/radiowaves/microwaves 原文；正常回答。SSE、持久化及重新 GET 历史逐字一致，finish_reason=stop，无内部引用残留。9.174秒。 | PASS |
| B：权限 | tenant344 请求 tenant332 KB 返回403；调用记录增量0。合法租户普通 text 及图片派生 Chunk 来源均可正常回读。 | PASS |
| C：单页扫描 PDF | 正常上传完整51318字节 PDF，无 image_pages；独立 Worker 完成本地 Tesseract OCR + 1次 DeepSeek Caption。正文、image_ocr、image_caption、summary 共4个 Chunk/4个1024维向量入库。Search4→Rerank4→Merge3→Context3，包含 OCR、Caption、Summary；实际 Cheap 请求含原图文字，回答“琥珀色镜片”。SSE/历史/重新GET一致，stop，问答5.618秒。 | PASS |

合成原件只有一页图像，文字为 `EviRoute lantern uses amber lenses`；不是冻结测评 Gold。扫描页图片资产保留物理页1、原件 SHA、版本、父子关联；正常源文件 API 回读 SHA 完全一致。Caption 是生成的解释，Summary 也是生成内容，不将二者认证为原 PDF 的逐字引文。

实际16次 Provider/辅助请求：SiliconFlow15次（5 chat，其中2最终回答、2 Rewrite、1文档Summary；8 Embedding；2 Rerank），DeepSeek图片Caption1次。输入13,592、输出674，合计 **14,266 Token**；SiliconFlow12,879，DeepSeek1,387。全部REPORTED/RESPONSE_RECEIVED，新增UNKNOWN=0。Rerank使用既有meta.tokens解析，不将billed_units当Token。没有新预算围栏或“结算”操作。

旧5张计费/操作表的行数与内容摘要全部不变；已有91条 Token UNKNOWN、保守占用827,414保持原样。新的使用量记录与旧账本分开，既不释放旧占用，也不以旧占用拒绝新请求。问答只各执行一次，没有重试选优。历史账本不再提供账户级上限控制。

实际命令（均使用 `E:\RAG quention\.venv\Scripts\python.exe -B`，完整前置环境见工具及证据）：

```powershell
python -B scripts/p31_native_acceptance.py prepare
python -B scripts/p31_native_acceptance.py start
python -B scripts/p31_native_acceptance.py query-text
python -B scripts/p31_native_acceptance.py upload-image
python -B scripts/p31_native_acceptance.py query-image
python -B scripts/p31_native_acceptance.py snapshot
python -B scripts/p31_native_acceptance.py stop
```

以上7个命令退出码均0。普通API与独立Worker复用真实PG/Redis，未使用模型替身。跨租户检查及回读均是真实HTTP。浏览器UI操作、SSE中途断线演练、全量数据集回归为NOT_RUN，本轮未修改前端；历史SSE相关定向离线回归已通过。

资源身份：PG `127.0.0.1:56423/weknora_python_port_20261013`、system identifier `7696088403504697382`；Redis `127.0.0.1:56424/15`、run_id `dcff9d65a25725574ba0cfbc743d409fac211ef0`；独立P31前缀和tenant344资产目录。复用332/344已有配置主体，没有修改默认tenant1主体或资料所有权。Worker启动前确认没有会被领取的旧344图片任务。

本任务API PID31556/31980、Worker PID36084已停止；56425/56427关闭，既有PG/Redis端口仍可连接，Docker及其他服务未停止。

## 遗留范围

旧 Authority/预算代码及历史测试保留为档案，没有要求本轮删除所有旧文件。公开引用继续关闭；正常 Chunk 来源元数据保留，但不再作额外逐字原文认证。OCR/Caption 质量、低 Rerank 分数和历史章节关联问题不在本轮优化范围。

共享文件与整合：先应用新增015迁移，再采用本提交的runtime/development/API/worker代码与配置说明；没有依赖锁或破坏性迁移。原有`.serena/project.yml`、`progress.md`及6个未跟踪交接/证据文件未暂存。仅本地提交，不Push、部署、合并、Tag或进入下一阶段。
