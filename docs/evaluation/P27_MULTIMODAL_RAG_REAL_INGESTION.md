# P27 — Multimodal RAG Real Ingestion and Visual Evidence Loop

**Status:** `P27_PARTIAL`

**Date:** 2026-10-17 (Asia/Shanghai)

**Repository:** `E:\RAG-Worktrees\weknora-python-port-20261013`

**Branch / starting HEAD:** `codex/weknora-python-port-integrate-image-20261014` / `78d277b12ed6ea5eb82a61a73160ef41a5c7027f`

## 最新真实执行结论：P27_PARTIAL

本轮基于 `77bd1914f895caacfc7407dfda4b0f75d9840d23` 继续；真实应用源码冻结为 **`14788b9ed37cfd82d15ac8ecbb62efcab4f14d35`**。两份冻结原PDF均通过正常HTTP上传、独立Worker完成限定页OCR/DeepSeek Caption、持久化及Child向量索引。Pew视觉派生内容进入真实最终Cheap请求；B3因现有Rerank过滤为固定拒答，未进入最终Cheap。因此为 **P27_PARTIAL**，不是P27_BLOCKED，也不将两个答案记为正确。

本章为当前状态；下面较早章节保留当时的阻断和恢复事实。旧Gold、P9–P26结果、UNKNOWN和原有未提交文件不变。

### 账户依据与有限围栏

Owner确认实际导出为 `usage_data_2026-10-11_2026-10-17.zip`，并明确“你就看这七天的，都是这个项目在用”。原先提及的9月18日起文件不存在，没有伪造为已读取。真实七天导出SHA为 `3397bad99e3f324d20551b593faa4e0047fc867db55eaa9da2cd2fa9f66176ac`，单一Key名称 `ccswitch-deepseek`，导出中的脱敏Key模式与当前项目密钥匹配。导出具有单一计费user_id，报告只保存其SHA；Owner先前提供的UUID与导出user_id不是同一字段，不冒充已匹配UID。依据是实际导出账户、Key匹配及Owner归属说明，账本继续沿用原 `deepseek-owner-main`，没有新建空账户。

七天观察用量：cache miss25,764 + cache hit10,112 + output16,920 = **52,796 Token /21次请求**。不把cache hit重复加到已含缓存的input字段；这里导出明确将hit/miss分列。账户不是技术独占，本机CCSwitch的claude/codex配置也引用相同密钥；本轮按Owner确认的七天项目使用范围核对，不宣称覆盖完整月份或技术上阻止外部调用者。原始账单zip保留在E盘，不提交含账户明细/价格的原件，也不从金额估算Token。

沿用现有月锁、P22不可变receipt/CAS及ApprovedTokenAuthority：本轮静态分配 **DeepSeek750,000 + SiliconFlow250,000 =1,000,000**。DeepSeek原本地占用273（UNKNOWN259及历史错桶SF14均原样保留），临时限750,273；连同整份导出52,796保守重叠计算为803,069，不超过原1,000,000上界，未相抵释放UNKNOWN。SF原占用1,693,086，临时限1,943,086。结束后两家均关闭到实际占用，headroom均0；本轮未用 **949,815 Token** 撤回，bundle入口禁用并保留已用配置副本。

### 本轮最小生产修复

实际装配原先一次只能使用一个provider的授权，无法让同一图片入库流程先调用DeepSeek视觉，再调用SiliconFlow Embedding。提交 `14788b9…` 仅组合现有单provider authority，按实际provider选择原账户、模型、meter和成员配置SHA；权限、P1SendGate、预留/结算、来源及UNKNOWN规则继续复用。未新增计费表、供应商、发送器或通用预算系统。

共享生产文件只有 `authority.py`、`development.py`、`entrypoint.py`。CLI可读取 `weknora-token-authority-bundle/v1` 的两个现有成员配置路径；成员必须同tenant/subject/month、approved且provider唯一。DeepSeek有限上限允许小于等于100万，100万以上仍拒绝；旧测试中999,999拒绝断言据此改为接受更低围栏，并保留零/超100万/类型错误/混用provider/合成外发反例。对应两个测试文件随提交记录。无迁移、依赖、普通Prompt、Rerank阈值、模型、路由或解析器改动。

### 两题实际链路与答案

| 项目 | row-0958 B3物理p9 | row-0684 Pew物理p10 |
|---|---|---|
| 原件 | 原SHA匹配，完整21页PDF经HTTP原文回读匹配 | 原SHA匹配，完整45页PDF经HTTP原文回读匹配 |
| 图片任务 | attempt1 completed，只有p9一个资产 | attempt1 completed，只有p10一个渲染资产 |
| OCR/Caption | 本地Tesseract + 1次真实DeepSeek，均completed | 本地Tesseract + 1次真实DeepSeek，均completed |
| Child与向量 | 1 OCR +1 Caption，有真实parent/version/page；合计9 chunk/vector | 1 OCR +1 Caption，有真实parent/version/page；合计165 chunk/vector |
| Search | 9候选，包含OCR、Caption及其parent | 30候选，包含OCR、Caption及其parent |
| Rerank | 4条实际输入，过滤后0 | 30条实际输入，过滤后4 |
| 最终Context | 0；无最终回答模型请求 | 2个parent文本Context；完整OCR/Caption经正常渲染进入实际Cheap请求 |
| 最终响应 | 固定证据不足提示 | 生成回答，错误声称男女均79% |
| 必要图像事实 | 原图可读“shiny object or mirror”，OCR损坏，Caption仅概述波现象，未提取两实例 | 原图可读男性81%、女性78%，OCR图表乱码，Caption仅约80%概述，未提取类别与数值对应 |
| 结果 | QUALITY_FAILURE；完整视觉→最终模型链路未达成 | 完整链路达成；视觉事实提取和回答质量失败 |
| 原件入库至完成 | 12.913s | 20.840s |
| HTTP问答墙钟 | 4.644s | 5.779s |
| 该题全部辅助及最终调用Token | 11,103 | 39,082 |

两份DeepSeek原响应的Caption与任务持久化内容完全一致，usage与结算行一致。两个目标图像的SHA与实际DeepSeek wire内JPEG逐字节一致，原件SHA/任务snapshot/document/version/页码关联通过。实际wire分别304,741和410,465字节，均`max_tokens=5000`，没有显式thinking参数；input预留分别305,765、411,489。两次真实output840/1,161，含reasoning分项744/1,013，均在cap内；reasoning已包含在output中，没有再次相加。

B3 Rerank观察器中OCR/Caption/parent分数分别0.00783876、0.01072710、0.01442671；Pew分别0.85367054、0.87352788、0.90333790。这里是既有model.rerank观察器返回值，管线还保留其既有转换/过滤；不是额外抓取的原始Rerank HTTP响应，不据此调整阈值。

Pew原生段落说总体79%及男女“无显著差异”，不足以推出两个群体均79%。真实最终请求包含这段原文和有来源身份的图像派生文字，但没有可读的81%/78%类别对应。故同时记录 **CHART_VALUES_NOT_EXTRACTED** 与 **UNSUPPORTED_GROUP_PERCENTAGE_IN_ANSWER**；不能把来源可回读或Context送达当作事实充分。B3另记录 **VISUAL_DETAILS_NOT_EXTRACTED / RERANK_FILTERS_ALL_CANDIDATES**，固定拒答符合现有保护。

Gold只在调用结束后参与离线评分，未进入Caption、索引或回答输入。两题项目诊断normalized EM均0；character F1分别0和0.0122449；人工目标事实正确0/2。只有1次最终Cheap生成，另1题为固定拒答。官方judge NOT_RUN，不冒充官方MML全量准确率，也不并入旧P9版本成绩。

公开引用继续关闭，没有自动补引用。4个image_ocr/image_caption通过正常`GET /api/v1/chunks/{id}`回读，KB、document、source_version、物理页及文本一致；完整原PDF通过正常source API回读匹配。来源合法与答案正确分开记分。只读回查前后模型账本完全相同，新增调用0。

### 真实计量和收口

| 实际模型调用 | 次数 | Input | Output | Total |
|---|---:|---:|---:|---:|
| DeepSeek视觉 | 2 | 2,050 | 2,001 | 4,051 |
| SiliconFlow Embedding | 15 | 24,814 | 0 | 24,814 |
| SiliconFlow Rerank | 2 | 7,801 | 0 | 7,801 |
| SiliconFlow Cheap聊天 | 5 | 12,885 | 634 | 13,519 |
| **合计** | **24** | **47,550** | **2,635** | **50,185** |

5次Cheap聊天是2次正常入库摘要、2次正常Query Rewrite、1次最终回答。2次Rerank均真实结算，未为计量额外请求。所有24条均settled、simulated=false，新增UNKNOWN0；原DeepSeek UNKNOWN1/259 held、SF UNKNOWN61/823,355 held不变，全部原2/726调用行逐行相同。单独供应商网络耗时未采集，标NOT_CAPTURED；上述耗时为实际入库/HTTP问答，不冒充单模型时延。

环境：PG56423 / `weknora_python_port_20261013` / system7696088403504697382；Redis56424/DB15/run_id `dcff9d65a25725574ba0cfbc743d409fac211ef0`，独立前缀 `weknora-python-p27:2b810c56a34b491fa3330ef1e2b5cc92`；新tenant344、两个独立KB，所有问答`document_scope=[]`，候选全部属于对应KB/doc。最终本地月占用/限额为DeepSeek4,324、SF1,739,220（均headroom0，不代表完整供应商账单总量）。只停止本任务API/worker及只读回查API，56425/56426无监听；真实只读closeout复核两个账户无reserved、历史UNKNOWN分别1/61且关闭后的账本未再变化；原PG/Redis及其他服务保留。未Push、部署、合并、打Tag或进入P28。

下一步最值得单独验证的是入库视觉证据的细粒度提取要求：能否保留图表类别—数值和图示对象标签，而非仅概述整页。该方向需要另行受控验证，本轮不修改Caption/普通Prompt或生产策略。B3低分Rerank及Cheap无依据套用总体数字分别保留为质量问题，不扩大修复。

### 本轮命令与证据

工作目录均为本仓库。解释器为 `E:/RAG quention/.venv/Scripts/python.exe`，全部带`-B`。完整129项离线命令保存在证据的`offline-tests/command.txt`，退出0；该组装配测试为doubles，不冒充真实服务。

真实命令尾部：`E:/WK/run/p27-live-20261017/account_and_inputs.py`；`fence.py open`；`run.py configs`；`run.py start`；`run.py configure`；`run.py case row-0958`；`run.py case row-0684`；`run.py snapshot final`；`fence.py close`；`readback.py`；`analyze.py`。除下述保留的离线/本地工具失败外，最终命令均exit0。进程实际命令、操作intent、HTTP回执及源码文件hash均保存，不只依据HTTP200判断结果。

保留失败：围栏脚本首轮因Windows默认GBK读UTF-8失败，发生于任何DB变更/外发前，显式UTF-8后成功；两次离线provenance断言因Context的blockquote及HTML转义而失败，修正分析器按真实渲染顺序反转这两项后成功，原失败log和断言保留。没有因此重复上传、Rerank或最终回答。首次`git diff --cached --check`对原始SSE末尾协议空行返回2；以证据目录局部`.gitattributes`保留原始字节并标注SSE为原始数据，未裁剪/改写SSE，原检查失败证据保留。随后识别原始CRLF为合法行尾，最终`git diff --cached --check`退出0；未改写原始数据。

本轮仓库证据：`docs/evaluation/evidence/p27-live-20261017/`（实际wire、DeepSeek原响应、阶段、最终SSE、当前新数据快照、chunk API回读、分析与测试）；完整E盘运行目录 `E:/WK/run/p27-live-20261017/`（含原始账单zip、图片资产和不可变完整围栏收据）。manifest对保存文件绑定SHA。新报告提交只包含本任务证据，不暂存既有.serena/progress/P6等文件。

## Previous continuation — historical cap incident recovered

Starting source: `9d4788a6b7f47f31459fbebdf5873577a7599dac`. **P27 remains BLOCKED only on unverified account association / consumer coverage before live activation. The historical DeepSeek month block has now been explicitly recovered.** The earlier sections below retain their earlier stop state; they do not describe the current month block.

### Exact historical request and root cause

The old renderer at `04f7b0d78f0b539f0d70a2db79359487168e940d` and the original synthetic messages reconstruct a 195-byte canonical wire. Its SHA-256 is `59e5ce47d546fd2f03b671204fc576a733d5949d6e71a165006b87592b271f4f`, exactly matching the original durable call. This is a hash-confirmed reconstruction, not a claim that a packet capture or full response body was retained.

The wire contains `max_completion_tokens:64`, **no `max_tokens`**, no `thinking`, and no `reasoning_effort`. The test constructed `ModelConfig` directly, bypassing catalog resolution. Its default ChatSettings selected the wrong field. DeepSeek declares `max_tokens` as its completion cap. The cap-contract repair already exists in `561e3bc8a5a6d13a7199c48f35daf40be3cd04e1`; this continuation does not redo it. Current code rejects the old wire and renders the correct field through normal catalog configuration.

The real retained usage is input44/output177/total221. A full response body and reasoning breakdown were not retained, so the 113-token difference cannot be assigned to thinking. Current vendor documentation says unspecified thinking defaults to enabled; historical actual reasoning participation remains unverified. The old report's claim that the vendor exceeded a correctly sent cap is superseded, without editing that report.

### Explicit recovery decision and real PostgreSQL execution

Under the Owner's latest recovery instruction, `scripts/weknora_port_p27_recover.py` uses the existing month advisory lock and immutable P22 receipt helpers. It pins the old ledger digest and incident wire, checks the repaired contract, snapshots call/slot/month identities, and **changes only `blocked_reason` from `USAGE_EXCEEDS_RESERVATION` to empty**. Before, precommit and postcommit receipts support crash reconciliation; a completed recovery cannot clear a subsequently changed state. It creates no account, permission, spending allocation or send path.

Executed against PG `127.0.0.1:56423`, database `weknora_python_port_20261013`, system identifier `7696088403504697382`. Commit and repeat-invocation checks passed. UNKNOWN remains UNKNOWN; held remains259; actual_total remainsnull; raw usage remains44/177/221. Month limit1,000,000, carry, approval and every other month field remain unchanged. Original DeepSeek-bucket2 and SiliconFlow726 full call rows retain their prior SHA-256 values; call slots are unchanged within the recovery. SF's closed P25 fence and 61 UNKNOWN /823,355 held remain untouched. No Redis writes, API/worker startup, index changes or model-configuration changes occurred.

### Actual image request size — no recipe change

Only the two target pages were rendered with the existing product renderer and its normal settings to measure bytes. No OCR experiment, full reparse, upload, Gold injection or model request occurred. The proposed normal Caption body uses the existing Chinese caption prompt, default temperature and `max_tokens=5000`; this is a local size measurement, not a saved sent request.

| Target | JPEG bytes | Image dimensions | Wire bytes | Input upper bound | Output cap | Total reservation |
|---|---:|---|---:|---:|---:|---:|
| B3 row-0958 p9 | 228,312 | 2000×1125 | 304,728 | 305,752 | 5,000 | 310,752 |
| Pew row-0684 p10 | 307,605 | 1546×2000 | 410,452 | 411,476 | 5,000 | 416,476 |

Both reservations sum to727,228, leaving272,772 of the authorized1,000,000 for other P27 reservations if simultaneously held. Actual model usage is not inferred from these byte upper bounds. Neither single image exhausts the fence. Keep the current recipe; verify the final configured wire at admission. An additional image call is not assumed to fit while UNKNOWN occupies its reservation.

### Actual credential / account checks and remaining external fact

Two read-only official requests used the existing credential in process, without recording its plaintext: `GET https://api.deepseek.com/models` and `GET https://api.deepseek.com/user/balance`, both200. The actual returned Flash entry declares `DeepSeek-V4.1-Flash`, `deepseek-flash`, image input and maximum accepted output393,216. Balance reports available. The API does not expose a billing UID in those responses; `owned_by=deepseek` describes the model publisher and is not the user's billing account. No amount was recorded or converted to Tokens.

Edge opened `https://platform.deepseek.com/usage`, but page inspection repeatedly returned `Debugger unattached`. The tab is preserved for the Owner to check. Local files provide no independently verified UID↔current-key mapping or proof/bound of other consumers. A focused pending question requests a redacted UID/Key association, usage export, and whether/from when the account is exclusive. This is a missing account fact, not another numeric authorization request. No additional metering system was built.

**Generation calls this continuation:0; generation Tokens:0; new UNKNOWN:0.** Read-only provider metadata/balance HTTP calls:2. Both P27 visual RAG cases remain NOT_RUN, with generation latency NOT_APPLICABLE. The combined1,000,000 ceiling is approved but no live temporary allocation was opened; there is none to revoke. Once account evidence is available, reuse existing authority/fence machinery and the recovered month; do not replay the old uncertain logical request.

### Commands and evidence

All commands ran with product Python `E:\RAG quention\.venv\Scripts\python.exe`, `-B`, in the stated repository. Short run root: `E:/WK/run/p27-gate-20261017` (abbreviated `<run>` here). Every command below exited0.

| Exact command tail after the interpreter | Result |
|---|---|
| `-B E:/WK/run/p27-gate-20261017/old_wire_audit.py` | Real read-only PG; 195-byte reconstructed wire hash matches stored call. |
| `-B -m pytest tests/weknora_port/test_p27_incident_recovery.py tests/weknora_port/test_output_budget_contract.py -q -p no:cacheprovider --basetemp=E:/RAG-Tests/p27incident` | 25 offline tests passed; no provider sends. |
| `-B -m scripts.weknora_port_p27_recover --approval-file E:/WK/run/p27-gate-20261017/recovery-owner-decision.txt` | Executed once, repeated once; RECOVERED both times; durable receipt. |
| `-B E:/WK/run/p27-gate-20261017/verify_recovery.py` | Real read-only PG; both historical call sets unchanged, only intended month field changed. |
| `-B E:/WK/run/p27-gate-20261017/image_request_size.py` | Existing renderer and builder; target-image request bounds measured, no network. |
| `-B E:/WK/run/p27-gate-20261017/account_endpoint_audit.py` | Two real official read-only HTTP200 responses; zero generation. |

Committed evidence directory: `docs/evaluation/evidence/p27-incident-recovery-20261017/`, including scripts, wire reconstruction, size JSON, account metadata, immutable recovery receipts, tests/logs/exitcodes and preservation hashes. Actual image bytes stay at `<run>/row-0958-p9.jpg` and `<run>/row-0684-p10.jpg`, linked by source and image SHA; no extra benchmark image copy is committed. Historical reports and raw evidence remain unchanged. Current new files are the recovery script, focused test, this report/result update and the dedicated evidence directory. No backend/Prompt/threshold/router/parser/migration/dependency change in this continuation.

## Prior gate continuation (superseded by the recovery below)

**Current outcome: `P27_BLOCKED` at the existing DeepSeek account/month safety block.** This gate-focused continuation starts at `c145ef6659857318919b20a8d16552c3894dcef5`. No real model calls, uploads, worker processing, index writes, or live model/KB configuration changes were made.

The previous P27 diagnosis that current approved mode raises `APPROVED_METER_UNIMPLEMENTED` was incorrect: that literal no longer exists in executable source. `ApprovedTokenAuthority`, actual transport, per-call production `build_call_sources`/`bind_wire_facts`, durable pre-send reservations, actual-usage settlement and UNKNOWN handling already exist and were used by P22/P25. The old assembly report described an earlier revision. This continuation corrects the diagnosis against actual code and the live isolated ledger, without modifying that historical report.

The actual missing image capability was the intentional `MODEL_INPUT_BOUND_NOT_APPLICABLE` refusal in the text-only meter. A minimal opt-in recipe, `deepseek-inline-image-1024/v1`, now bounds official DeepSeek Flash inline static JPEG/PNG requests as all wire bytes plus 1024 tokens per image, and reuses the existing full-completion output contract. **116 focused offline tests passed, exit 0.** The new recipe was not installed as a live grant or used to send a request.

Real external execution stopped after the read-only ledger audit: the existing `deepseek-owner-main` October month has `blocked_reason=USAGE_EXCEEDS_RESERVATION`. Its historical call reserved 64 output tokens but reported 177; its UNKNOWN state and 259-token occupancy remain intact. The current local SiliconFlow fence is closed, and provider-account-wide shared-consumer/history coverage has not been independently established. The owner's combined 1,000,000-token maximum is authorized but unopened; no usable credit was invented from that number.

## P24–P26 state and ownership

The inherited dirty files were preserved and not staged by the P27 work:

- Modified: `.serena/project.yml`, `progress.md`.
- Untracked: `docs/weknora-port/AUTHORITY_STOP_STATE_20261015.json`, `docs/weknora-port/OWNER_DEEPSEEK_AUTHORITY_HANDOFF_20261015.md`, `docs/weknora-port/OWNER_DEEPSEEK_HANDOFF_20261014.md`, and the three P6 ordinary Go build evidence files under `docs/weknora-port/evidence/`.
- There were no staged changes at the start of the P27 report work.

P24–P26 were already organized into separate local commits before P27 files were added:

| Phase | Commit | Scope |
|---|---|---|
| P24 | `cb07fb50874386ade91413e059f096e328c0e47b` | P24 report and result JSON only |
| P25 | `5f949a97bd31b71b33cf51d2e9a2af3253261abc` | P25 report and result JSON only |
| P26 | `78d277b12ed6ea5eb82a61a73160ef41a5c7027f` | Narrow public source-handle fix, tests, and P26 report/results |

The P25 token fence remains historical/closed. This run did not reopen, extend, reset, or consume it. No historical `UNKNOWN`, Gold, result, or index was changed.

## Existing implementation audit

The fixed upstream reference remains WeKnora commit `3e8b0bfc80b845b2d4b2ed683994748741450a97`; no newer upstream revision was used for this audit.

| Stage | Existing implementation reviewed | P27 finding |
|---|---|---|
| Configuration selection | `backend/weknora_port/image_configuration.py`: `effective_image_config`, `_vision_binding`, `resolve_image_selection` | KB defaults and non-empty upload overrides are merged; OCR and Caption can select separate adapters/models, and Caption can follow a validated VLM OCR selection. This reuses the model store/catalogue and existing vision declarations; it does not infer capability from a model name or probe a provider. |
| PDF asset extraction | `backend/weknora_port/ingestion/upstream_pdf_parser.py`: `PDFParser.parse_into_text`, `_route_locked` | Per-page parser emits scanned-page assets and can emit embedded/vector image assets. It emits no target chart asset for the Pew PDF under the tested default configuration. |
| Durable task and image processing | `backend/weknora_port/documents.py`: `upload_document`, `_process_document`, `_persist_assets`; `backend/weknora_port/image_tasks.py`; `backend/weknora_port/worker.py` | P27 continuation adds `image_pages` as a one-based physical-page selection. Upload configuration wins over KB defaults; null does not clear a valid KB default. The durable `selected_config` snapshot stores selected pages plus tenant, subject, KB, document, source version, and source SHA-256. The worker validates the snapshot and current document hash before image requests, then filters pending image assets to selected pages. |
| OCR/Caption result persistence | `backend/weknora_port/image_multimodal.py`: `SourceImageProcessor.process_plan` | OCR and Caption are processed separately per asset when both use a model. Results retain page/source metadata, separate OCR/Caption status and errors, and become `image_ocr`/`image_caption` child chunks linked to the source parent/version. Empty output is rejected rather than stored as success. |
| Embedding and retrieval | `backend/weknora_port/documents.py` (`HybridIndexer.batch_index`); `backend/weknora_port/retrieval/rerank.py`; `backend/weknora_port/retrieval/context.py` | Persisted derived children use the existing text indexing path. Rerank and Context can enrich passages with persisted `caption` and `ocr_text`. This code path was not exercised against a database in P27. |
| Final model request | `backend/weknora_port/generation/context.py` | Final generation can include directly attached images when supported; retrieved image bytes are not forwarded as images through this path. For this task, the intended RAG route therefore depends on persisted OCR/Caption text entering the final Context. |

The task queue is image-specific and preserves the fixed upstream retry/claim semantics already documented in its source; the memory-only extraction queue was not reused. The existing P26 tests cover offline configuration and image processing, but do not prove live model quality or the full upload-to-answer path.

## Frozen source and parser preflight

The frozen source identities matched the P10/P24 manifests. The inventory evidence is `E:\WK\run\p27-20261017\offline_parser_asset_inventory.json`; it contains asset paths/page identities and counts, not image bytes.

| QID | Source SHA-256 | Target page | Offline parser output | P27 disposition |
|---|---|---:|---|---|
| `row-0684` | `8f49f93f1665fd5ff604e66ffed9649d8b18ce7f114ab43463aeac46cd3d00cb` | 10 | Default parsing: 45 text pages, no image assets. P27 page-scoped parsing adds exactly one native-page render for page 10 while retaining the PDF text. | `NOT_RUN` — offline target image is ready; real Caption/RAG is blocked by the trusted authority. |
| `row-0958` | `43be9592854c7da10bf011335faec017b726ff2e23bb4ccae7e8fe88d7801f22` | 9 | 21 scanned pages and 21 full-page image assets; physical page 9 maps to `images/b3m5kaeqm2w8n4bwcesw-140602121350-phpapp02_95_page_9.jpg`. P27 page filter selects only page 9 for image processing. | `NOT_RUN` — target image is ready and its parent reference is fixed; real Caption/RAG is blocked by the trusted authority. |

**Initial runtime qualification:** the first inventory used `E:\WK\mineru-env\Scripts\python.exe` (Python 3.12.7, `pypdfium2` 5.14.0). At that point the product Python 3.13.0 environment lacked its already-declared optional `pypdfium2==5.8.0`. In this continuation, the exact pinned wheel was installed into the project virtual environment only; the product interpreter then parsed both frozen PDFs and created the selected page assets. No global Python installation, package pin, or lock file was changed. The initial alternate-interpreter inventory's PDFium bitmap-close warnings remain historical evidence; the product-interpreter targeted run completed without that warning.

## P27 continuation: offline wiring and frozen-page proof

The configured product-runtime dependency is the existing project pin `pypdfium2==5.8.0`. It is installed only under `E:\RAG quention\.venv`; parser tests run with that interpreter. The focused full-source `test_ingest.py` module could not be collected because the same environment lacks the separately declared optional `python-pptx==1.0.2` import (`ModuleNotFoundError: No module named 'pptx'`). A dedicated parser test module without Office imports covers the requested PDF behavior and passed; the missing `python-pptx` dependency was not installed because it is outside this task's parser runtime fix.

DeepSeek model resolution was checked offline through `SourceCatalogue` for a `vlm` role with provider `deepseek` and API model ID `deepseek-flash`. The pinned registry declares `input: ["text", "image"]`; the resolved config has `supports_vision=true`, OpenAI-compatible chat, and multi-content support. The request renderer emits the configured prompt and `data:image/jpeg;base64,...` image as separate user-content parts. The provider's vision path is explicit and does not silently retry after stripping the image. The repository's catalog/request contract and adapter tests passed. This is a configuration and wire-shape check, not a live provider verification. DeepSeek's official [Vision guide](https://api-docs.deepseek.com/guides/vision/) documents image_url content on the chat-completions API, and the [V4.1 Flash release](https://api-docs.deepseek.com/news/news260910/) identifies the API model ID.

Local OCR was checked using the product interpreter, existing `E:\WK\deps\tessdata`, and a generated image containing `LOCAL OCR PAGE 9`. `TesseractOCR.recognize` returned that text with `image-ocr-layout-v1`; this is a local synthetic smoke only, not OCR evidence for either benchmark PDF. The frozen dataset's actual PDF page content was not sent to OCR or a provider.

The upload/worker page selector accepts one-based physical pages (`image_pages`, 1–1000), validates positive unique integers, applies upload override before KB default, preserves the full original PDF, and passes selected pages to the existing PDF parser. Native text pages are rendered only when selected; existing scanned-page asset identities are retained. Only selected page assets are sent to the image processor. The page list and source identity are stored in the task's existing `selected_config` JSON snapshot; no schema migration or new model registry was added. Worker source version and file hash are checked before processing. A parser path mismatch for requested scanned pages was fixed: Markdown now references the existing `_page_N.jpg` asset rather than a nonexistent `_requested_render.jpg` asset.

Using the frozen source manifest and verifying both source SHA-256 values, the product Python 3.13.0 parser produced:

| Sample | Requested physical page | Full-document assets emitted by parser | Selected page asset | Parent Markdown reference | Offline result |
|---|---:|---:|---|---|---|
| `row-0958` B3 PDF | 9 | 21 scanned-page assets | `images/b3m5kaeqm2w8n4bwcesw-140602121350-phpapp02_95_page_9.jpg` | Present; scanned asset identity preserved | `PASS / parser only` |
| `row-0684` Pew PDF | 10 | 1 selected native-page render | `images/11-21-16-Updated-Post-Election-Release_p10_requested_render.jpg` | Present | `PASS / parser only` |

The B3 parser still emits its 21 scanned-page assets for the full document; the normal image-task filter selects only page 9 for VLM processing. The Pew parser retains all native document text and renders page 10 as an additional sourced asset. These are parser/selection results, not persistence, indexing, retrieval, or answer results.

## Current gate audit and minimal repair (2026-10-17)

Repository AGENTS.md and progress.md were read. No ADR index file exists under docs/adr; the relevant existing ADR-001 and ADR-002 documents were read directly.

The isolated PostgreSQL identity was verified before any query: `127.0.0.1:56423`, database `weknora_python_port_20261013`, system identifier `7696088403504697382`. All account inspection transactions used `SET TRANSACTION READ ONLY`. Redis and application processes were not started or changed.

| Account | Observed original state | P27 action |
|---|---|---|
| DeepSeek `deepseek-owner-main` | October limit 1,000,000; stored carry 0/KNOWN with source `LEDGER:port_token_calls:<2026-10-01`; month blocked by `USAGE_EXCEEDS_RESERVATION`. One actual DeepSeek UNKNOWN holds 259, reserved input/output 195/64, provider-reported input/output/total 44/177/221. The same historical bucket also contains an incorrectly assigned settled SiliconFlow embedding call of 14 tokens. | Kept all rows, carry values, misassignment and month block unchanged. The stored carry value is not treated as independent proof of complete provider-account consumption. No new account/alias or live authority file was created. |
| SiliconFlow `siliconflow-owner-main` | October limit 1,693,086; settled actual total 869,717; 61 historical UNKNOWN hold 823,355; preserved carry 14; occupied including carry 1,693,086, headroom 0. | P25 remains closed. Its CAS/receipt/month-lock mechanism is reusable for SiliconFlow, but it does not unblock DeepSeek. No new increase was opened. |

The direct cause of the DeepSeek month block is an observed output component overrun (177 > 64), even though reported total 221 is below the original combined reservation 259. Current P1 correctly refuses such a month with `TOKEN_MONTH_BLOCKED`. This audit did not establish why that old request exceeded its output reservation, reconcile its original wire contract, or approve clearing the incident. A new input meter cannot clear it.

The code change is confined to `source_facts.py` and its focused tests. The new opt-in recipe verifies provider `deepseek`, model `deepseek-flash`, official HTTPS deployment, OpenAI Chat Completions and `vision` capability; it requires inline valid static JPEG/PNG in user messages and an exact body/wire match. Remote URLs, files, GIF/animated media, other deployments, different models/protocols and unsupported media remain denied. It keeps all serialized bytes in the input bound, including base64, then adds the documented image allowance. This is deliberately conservative and is never provider actual usage. Existing text recipes still reject image input.

The upper bound is based on DeepSeek's [Vision Token Usage documentation](https://api-docs.deepseek.com/guides/vision/#token-usage), which bounds each image at 1024 input tokens. Output continues through the existing `resolve_output_contract`: the wire must carry the provider-declared `max_tokens`, and the bound covers full completion including reasoning; no visible-answer-only allowance is used. See the official [Chat Completions schema](https://api-docs.deepseek.com/api/create-chat-completion/). The recipe remains behind the existing hash-pinned meter configuration, ApprovedTokenAuthority, SendGate and ledger; it does not create permission or modify actual-usage/UNKNOWN settlement.

Missing facts that cannot be filled by this code change are: authoritative review/reconciliation and a trusted recovery decision for the existing DeepSeek overrun block; current key-to-billing-account association and complete consumption coverage for that account; and coverage or a conservative bound for other consumers of both shared provider accounts. No provider-account export or exclusive-consumer evidence was supplied or independently established in this pass. Local ledger emptiness or a stored zero is not proof of those facts.

The combined P27 limit remains **1,000,000 tokens authorized, 0 live headroom opened, 0 consumed**. No temporary allocation was created, so there is no issued unused allocation to revoke. Two-provider run admission/configuration was not activated while these prerequisites were false. Stage 2 Model Registry/KB updates and both Stage 3 RAG cases are `NOT_RUN`; this is an account/authorization blocker, not a Caption or answer-quality failure.

Read-only before/after SHA-256 comparisons confirm both original account call sets (DeepSeek bucket 2 rows; SiliconFlow 726 rows) and their month rows are unchanged. Evidence: `E:\WK\run\p27-gate-20261017\account-audit.json`, `deepseek-block.json` and `history-preservation.json`.

## Prior meter-continuation acceptance results

| Acceptance item | Status | Evidence |
|---|---|---|
| Image extraction | `PASS / offline product-runtime parser only` | Verified frozen source hashes; B3 page 9 and Pew page 10 assets and matching Markdown references in `E:\WK\run\p27-resume-20261017\actual-pdf-page-scope.json`. |
| OCR/VLM | `PARTIAL / local smoke; DeepSeek NOT_RUN` | Local synthetic OCR returned `LOCAL OCR PAGE 9`; DeepSeek image requests: 0. No benchmark Caption was generated. |
| Persistence | `NOT_RUN` | Uploads: 0; image tasks: 0; OCR/Caption child chunks: 0. |
| Vector retrieval | `NOT_RUN` | Embeddings/search/rerank requests: 0; only read-only account/ledger PostgreSQL queries ran in this continuation. |
| Context to Cheap | `NOT_RUN` | Cheap final answers: 0; no model Context was constructed or sent. |
| Answer/Gold comparison | `NOT_APPLICABLE` | No answer was generated. Gold remained evaluation-only. |
| Token / latency | `0 new provider tokens`; latency `N/A` | Provider requests: 0; new `UNKNOWN`: 0. |
| Focused offline regression | `PASS (offline contract tests only)` | Current meter change: 116 passed in 1.78s, exit 0; the prior 492-test parser/image run belongs to the earlier P27 source. Log: `E:\WK\run\p27-gate-20261017\meter-tests-verified.log`. |

No claim is made that P27 validated real HTTP, Worker, PostgreSQL/pgvector, Redis, indexing, or model quality.

## Commands and exit codes

| Command | Exit | Result |
|---|---:|---|
| `& 'E:\WK\mineru-env\Scripts\python.exe' 'E:\WK\run\p27-20261017\offline_parser_asset_inventory.py'` | 0 | Offline parser feasibility inventory; PDFium bitmap-close warnings were emitted. Saved JSON listed above. Alternate Python/dependency versions apply. |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest tests/weknora_port/test_image_configuration.py tests/weknora_port/test_image_multimodal.py tests/weknora_port/test_image_processing.py tests/weknora_port/test_image_json_boundary.py tests/weknora_port/test_search_image_completion.py -q` | 0 | `76 passed in 1.25s`; offline/unit coverage only. |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -B -c "import sys, importlib.util; print(sys.version.split()[0]); print('pypdfium2_spec=', importlib.util.find_spec('pypdfium2'))"` | 0 | `3.13.0`; `pypdfium2_spec=None`. |
| `pip download --no-cache-dir --only-binary=:all: --no-deps --dest E:\WK\run\p27-resume-20261017\wheels pypdfium2==5.8.0` | 0 | Existing pinned Windows wheel downloaded to the E-drive run directory. |
| `pip install --no-index --no-deps --find-links E:\WK\run\p27-resume-20261017\wheels pypdfium2==5.8.0` | 0 | Installed into `E:\RAG quention\.venv`; no global environment or dependency manifest changed. |
| Product interpreter import/version check for `pypdfium2` | 0 | Python `3.13.0`; package metadata `5.8.0`; module loaded from the project `.venv`. |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest tests/weknora_port/test_p27_page_scoped_vision.py tests/weknora_port/test_image_configuration.py tests/weknora_port/test_image_multimodal.py tests/weknora_port/test_image_processing.py tests/weknora_port/test_image_json_boundary.py tests/weknora_port/test_search_image_completion.py tests/weknora_port/test_provider_request.py tests/weknora_port/test_provider_transport.py tests/weknora_port/test_catalog.py tests/weknora_port/test_documents.py tests/weknora_port/test_http_api.py -q -p no:cacheprovider --basetemp=E:/RAG-Tests/p27r3` | 0 | `492 passed in 3.61s`; log and exit-code file in `E:\WK\run\p27-resume-20261017\`. |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest tests/weknora_port/test_ingest.py -q -p no:cacheprovider --basetemp=E:/RAG-Tests/p27-ingest-blocked` | 2 | Collection blocked by missing `pptx` module. Evidence: `E:\WK\run\p27-resume-20261017\test-ingest-collection-failure.log`; independent P27 parser tests passed in the historical 492-test suite. |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -B -c "import io; from PIL import Image, ImageDraw, ImageFont; from backend.weknora_port.tesseract_ocr import TesseractOCR; image=Image.new('RGB',(1200,300),'white'); draw=ImageDraw.Draw(image); draw.text((40,90),'LOCAL OCR PAGE 9',fill='black',font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',72)); out=io.BytesIO(); image.save(out,format='PNG'); result=TesseractOCR(r'E:/WK/deps/tessdata').recognize(out.getvalue(),'image/png'); print('error='+result.error); print('text='+repr(result.text)); print('layout='+result.layout.get('schema',''))"` | 0 | Text `LOCAL OCR PAGE 9`, `image-ocr-layout-v1`; log `E:\WK\run\p27-resume-20261017\local-tesseract-smoke.log`. |
| Product-interpreter page-scoped parse for frozen `row-0958` p9 and `row-0684` p10 | 0 | Both original SHA values matched; both target assets and parent Markdown references were found. JSON/log: `E:\WK\run\p27-resume-20261017\actual-pdf-page-scope.json` and `actual-pdf-page-scope-final.log`. |
| Real upload / API / Worker / Redis / DeepSeek / Cheap RAG | `NOT RUN` | Existing DeepSeek month block and unverified whole-account coverage prevent activation. Approved assembly already exists. Only read-only PostgreSQL ledger audits ran. |

## Prior meter-continuation commands and source changes

| Command | Exit | Result |
|---|---:|---|
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -B 'E:\WK\run\p27-gate-20261017\audit_accounts.py'` | 0 | Real isolated PG identity and both existing provider-account ledgers; read-only. |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -B 'E:\WK\run\p27-gate-20261017\audit_deepseek_block.py'` | 0 | Original numeric usage confirms output 177 > reserved 64. No answer/key content read. |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest tests/weknora_port/test_p27_vision_meter.py tests/weknora_port/test_authority_approved.py tests/weknora_port/test_output_budget_contract.py tests/weknora_port/test_authority_usage.py tests/weknora_port/test_send_gate.py tests/weknora_port/test_provider_usage.py -q -p no:cacheprovider --basetemp=E:/RAG-Tests/p27meter-verified` | 0 | 116 passed in 1.78s. No provider calls or real-account fixture writes. |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -B 'E:\WK\run\p27-gate-20261017\verify_history.py'` | 0 | Both account call sets and month rows unchanged. |

Changed this continuation: `backend/weknora_port/source_facts.py`, `tests/weknora_port/test_p27_vision_meter.py`, this report and its result JSON. No parser, image-task, model registry, ordinary Prompt, retrieval threshold, routing, schema/migration or dependency changes were made. Existing dirty files remain outside the local delivery commit. No real key was read, copied or printed. Earlier P27/P9–P26 results and raw evidence remain preserved.

## Prior stop state before incident recovery

`P27_BLOCKED`: both target questions remain unexecuted because the original DeepSeek month is blocked and required whole-account facts are unverified. Obtain authoritative incident/account/shared-consumer facts and use a trusted recovery procedure that preserves UNKNOWN and original limits before opening any combined P27 fence. The owner has already authorized this task's numeric ceiling; another numeric allowance alone does not resolve the missing evidence. This pass stops here without enabling live roles, pushing, deploying, merging, tagging or opening another stage. The final local commit SHA is provided in the delivery message.
