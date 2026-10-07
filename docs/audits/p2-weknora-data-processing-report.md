# P2 — WeKnora-style 多格式数据处理

日期：2026-10-07（Asia/Shanghai）。结论：**P2_PASS**（代码与离线合同范围）；外部能力状态：**READY_FOR_EXTERNAL_VALIDATION**。真实 PostgreSQL、DOC 转换器、Tesseract 中文/英文 OCR、真实 VLM 和供应商结算均未验收。本报告不授予发布、里程碑验收或删除权限。**未实现的命令不得报告通过。**

## 1. 授权与版本

用户最新授权是“先提交 P0，再做 P2”，并明确“weknora 的基线以 GitHub 固定基线为准”。因此执行 P2 后停止，不按总入口继续 P3–P7。附件的目标架构用于解释 P2，不作为提前实施后续阶段的授权。

| 项目 | 已核实事实 |
|---|---|
| 工作区 | `C:\Users\22088\.codex\worktrees\rag-retrieval-opt-round1\RAG quention` |
| 分支 | `codex/local-first-rag-v1-20260930` |
| P1 独立提交 | `4a46b02b1baa684575e019fed5d3cde2401f2f5e` |
| P0 独立提交 / P2 起点 | `510199b700dc6832ec80869623810b60f46b7992`，仅提交既有 P0 报告，未重写其历史审计基线 |
| P2 开始状态 | Git 干净；仓库 migration 包含 `0014_version_source_metadata` |
| 当前 HEAD 相对最初参考 dd4ca9e | DESCENDANT：P1、P0 提交在参考提交之后；没有 checkout/reset/fetch |
| Python | 本工作区 `.venv\Scripts\python.exe`，3.13.0；有 `Failed to find real location of D:\Drivers\python\python.exe` 提示，但成功测试命令实际退出 0 |
| GitHub 唯一语义基线 | Tencent/WeKnora v0.8.2，`3e8b0bfc80b845b2d4b2ed683994748741450a97` |
| 数据库 | 仓库最新 `0015_processing_manifest`；真实 DB 已应用版本 **UNKNOWN**，没有连接真实 DB |

读取了 AGENTS、progress、ADR 索引和任务设计；progress 中旧 main/0011/服务验证记录不作为本轮事实。旧动态路由附录的准备任务写入限制由本次 P2 源码/测试/迁移授权覆盖；没有改 AGENTS、全局配置或账本。主线程是唯一 writer。使用 efficient-goal-execution 技能复用基线与失败证据，没有建立新 Goal。

主协调请求 Sol high；一个限定只读 NORMAL 审查请求 `gpt-6.1-sol/medium`，最多主协调与审查两个执行者。审查初次发现 PDF 容错与 Caption guard 异常问题，父线程修复后进行一次定向复核，返回 CHECKS_PASSED；没有 FAST/HARD 或递归派发。请求参数不证明实际 serving identity；主/子线程上游实际模型及 effort、整体 token/费用/耗时 **UNKNOWN**。执行性质 **MANUALLY_SUPERVISED_TRIAL**。

## 2. 固定上游源码与采用方式

下列链接全部固定到上述 SHA，未使用 README 推测生产接线。

| 固定源码位置 | 核实行为 | 本项目采用与保留 |
|---|---|---|
| [pdf_parser.py:76–81、167–199、1503–1507](https://github.com/Tencent/WeKnora/blob/3e8b0bfc80b845b2d4b2ed683994748741450a97/docreader/parser/pdf_parser.py#L167) | 图像面积比是各 image object bbox 面积之和 / 页面面积，重叠可超过 1；先判断 ratio ≥0.5，再判断文字 <10 且 ratio ≥0.1，否则 text | 相同默认阈值和顺序；使用已有 PyMuPDF 对应图像 bbox API，不引入 pdfium 栈；不发明参数搜索。空白页不栅格化 |
| [PDFParser:1416、1549、1564、1605](https://github.com/Tencent/WeKnora/blob/3e8b0bfc80b845b2d4b2ed683994748741450a97/docreader/parser/pdf_parser.py#L1416) | text 页面提取正文/布局/嵌图，scanned 页面渲染，混合文档包含不同页类型 | 保留本项目 native table/geometry；逐页/逐图隔离失败。上游全局异常回退整文为图的路径没有复制，因为附件要求混合文档尽量保留可用页 |
| [registry.py:151](https://github.com/Tencent/WeKnora/blob/3e8b0bfc80b845b2d4b2ed683994748741450a97/docreader/parser/registry.py#L151)、[Go engines.go](https://github.com/Tencent/WeKnora/blob/3e8b0bfc80b845b2d4b2ed683994748741450a97/internal/infrastructure/docparser/engines.go) | Python registry 之外还有 Go engine 选择；不能把注册当整个生产链 | 本项目使用自己的唯一 ParserRegistry，组合根显式注入；不复制 Go/Redis/Asynq |
| [doc_parser.py:178–188](https://github.com/Tencent/WeKnora/blob/3e8b0bfc80b845b2d4b2ed683994748741450a97/docreader/parser/doc_parser.py#L178)、[docx_parser.py](https://github.com/Tencent/WeKnora/blob/3e8b0bfc80b845b2d4b2ed683994748741450a97/docreader/parser/docx_parser.py) | DOC→DOCX 转换、解析器 fallback；textract 路径因风险禁用 | 复用已有受安全门控制的 DOC converter→DOCX adapter。没有复制 antiword/textract/MarkItDown 多重依赖；DOCX 原始 tblHeader、XML origin 与错主体/错表头保护继续存在 |
| [excel_parser.py:68、78、137、157](https://github.com/Tencent/WeKnora/blob/3e8b0bfc80b845b2d4b2ed683994748741450a97/docreader/parser/excel_parser.py#L68)、[xlsx_merge.py](https://github.com/Tencent/WeKnora/blob/3e8b0bfc80b845b2d4b2ed683994748741450a97/docreader/parser/xlsx_merge.py) | 默认首行数据；显式首行表头；sheet→row 的列名:value；合并区域填 anchor 值；data_only 读取缓存 | 迁移检索语义；保留 sheet 身份。XLSX 同时读取原 formula 与 cache，不在原文件 unmerge/save；仅检索文本填值，独立 proof 保留原始合并/公式状态 |
| [image_parser.py](https://github.com/Tencent/WeKnora/blob/3e8b0bfc80b845b2d4b2ed683994748741450a97/docreader/parser/image_parser.py)、[image_multimodal.go:274–332](https://github.com/Tencent/WeKnora/blob/3e8b0bfc80b845b2d4b2ed683994748741450a97/internal/application/service/image_multimodal.go#L274) | image parser 输出图像引用；服务层分别产生 OCR、caption 子资料；上游 OCR 也可能通过 VLM Predict | 本项目明确区分原始 OCR 文字与未验证语义 caption，后者不能升级为数值原文；使用 provider port，不复制上游把视觉模型输出直接视作 OCR 的全部策略 |

本机 `E:\WeKnora` 是印证材料：VERSION 为 0.8.2，无 `.git`；检查的 23 个限定文件中 14 个与固定源码字节一致、8 个不同、1 个本地新增。差异涉及 source_blocks/source_wire、XLSX 修复、图像 action、MinerU 等，均未作为迁移基线复制。

运行镜像标签声明 version v0.8.2、revision 固定 SHA；运行 docreader 中 registry/PDF/Excel/xlsx_repair/document model/main 六个抽查文件与固定源码一致，source_wire 不存在。镜像 ID：app `sha256:bc8a534799fcb54045100daf4bc5db86270b38ae45c5f761c410c50b4dcd6cdc`；docreader `sha256:3c36e7e738ba515315523876d8041f9bb02809145a4e3b331e2550e4bff98b7a`。标签和六个文件哈希不能证明整个运行镜像完全一致；没有向部署发解析/模型请求，没有读取 `.env`、凭据或数据库。

## 3. 当前→新调用链与实现状态

原链：`Upload → immutable VersionSource → ParserRegistry 类型分支 → NormalizedDocument → 原有 chunk_document → 索引/版本激活`。PDF 主要按是否有 text 判断，XLSX 猜首个多文本行为表头，Caption 原实现未接生产组合根，XLS 没有 adapter。

新链：

```text
Upload → CAS source SHA-256 → VersionSource（版本自己的文件名/MIME）
→ ParserRegistry.parsers[canonical MIME / extension]
→ specific inert parser / bounded native subprocess / explicit DOC converter
→ NormalizedDocument(content, sections, tables, assets, engine/version/status/warnings)
→ independent TableRowProof(source SHA, row/header/cells, conversion lineage)
→ optional CaptionEnricher(provider, explicit cloud guard)
→ 原有 chunk_document（参数与算法未修改）
→ assets + normalized CAS + processing manifest CAS
→ 现有租约/claim 栅栏事务：索引 ready + manifest reference + active version
```

关键项目证据（路径均相对于仓库根）：

| 路径 / 符号 | 实现状态、证据与边界 |
|---|---|
| `backend/app/adapters/parsers/__init__.py:PdfParser.parse/classify_pdf_page` | 真实生成 PDF 解码验证；单页 OCR、分类 metadata、枚举、render 失败隔离验证。扫描页保留原页 source raster、OCR derivative、unsupported table diagnostic；native table mapper 未替换 |
| `.../__init__.py:ParserRegistry`、`bootstrap.py:build_container` | 映射集中于 registry，production store 明确使用。默认 native Python 为项目当前 interpreter，可显式覆盖；不动态下载运行时；网络/进程动作在既有 native worker 审计钩子中禁止 |
| `.../xlsx.py:XlsxParser.parse`、`domain/table_evidence.py:key_value_row` | 新索引输出逐 sheet/row 的轻量 k:v；默认 first-row-as-data；显式配置 `Settings.xlsx_first_row_as_header` / `RAG_XLSX_FIRST_ROW_AS_HEADER`；未改变 max chunk/overlap/strategy |
| `.../xls.py:XlsParser`、`native_worker.py:xls` | xlrd 真实 BIFF 解码通过；stdin-only、8MiB input、4MiB output、32 sheets、1000 rows/200 cols、50000 grid/20000 cells、timeout 等既有边界。XLS 全部标 partial + XLS_FORMULA_CACHE_UNVERIFIED，不能用于严格数值见证 |
| `domain/models.py:TableRowProof/NormalizedDocument/DocumentTable` | additive 独立 proof 与 diagnostics；新表 row_representation，旧 JSON 默认 legacy。proof 包含 document/version/source hash/table/row/range/header policy/cells/status/conversion lineage |
| `application/structured_evidence.py:_table_rows` | 仅为新 explicit_first_row k:v 进行内容精确复核；旧 inferred legacy render 仍读取。same entity/month/value/unit、同源表头、formula/cache/merge 拒绝条件未减弱；新真实 XLSX/DOCX 正例及反例通过 |
| `application/caption.py:CaptionEnricher`、`ports/providers.py:CaptionProvider/CaptionUsageGuard` | 通用 port、显式注入。cloud 在 preflight/调用前重复检查 literal True 权限，再 reserve→call→settle；unknown usage 传 None，保守占用责任属于可信 guard。拒绝/guard 异常不外发且保留原文；fake 验证通过 |
| `application/local_caption.py:LocalCaptionEnricher` | 复用既有 derivative 身份/hash、4 图/串行/45s call/90s doc、取消、未验证证据标记；默认仍关闭；没有改本地 Chat、Embedding、Query Expansion 路由 |
| `adapters/postgres/knowledge_repository.py:process_job` | 成功版本新 CAS manifest 保存 diagnostics/proof/lineage；最终更新 version manifest key/hash 与 active switch 使用既有同一事务。SQL recording + CAS readback 验证是 SIMULATED，不是实际 DB 并发验证 |
| `alembic/versions/0015_processing_manifest.py` | 只增两个 nullable Text 字段；旧 migration 未改、无回填/删除；downgrade 明确拒绝丢失历史 proof；offline SQL exit 0 |

没有修改 chunking.py、Hybrid/RRF、Parent-Child、Rerank、Context Builder、Cheap/Expensive Router、Local Chat 或 Citation/History 服务源码。原先已有的应用层导入 adapter 三项 layering violation 没有在本阶段顺带重构。

## 4. 格式行为与错误矩阵

| 格式 | engine / version | 原文、资产与 proof | 失败/实际验证 |
|---|---|---|---|
| TXT / MD | TextParser / text/v1 | UTF-8 原文、换行标准化；MD heading 保留、start/end locator | 真实生成文件通过；无模型 |
| HTML | beautifulsoup4/4.14.3 / html/native-v1 | bounded native 标准化文本/表；移除 script；原始表头策略保留 | 真实生成 HTML 通过；无抓取外链 |
| DOCX | python-docx/1.2.0 / docx/native-v1 | 段落/表、declared tblHeader、XML origin、同源 hash，独立 row proof | 真实生成 DOCX 及严格 row_facts 通过 |
| DOC | DOC converter→上述 DOCX / doc/lo-docx-v1 | original SHA 与 converted SHA 分开；converted coordinates，不伪装原分页；partial layout | fake converter+真实 DOCX adapter 通过；真实 DOC conversion NOT RUN，安全门继续阻止未经隔离运行时 |
| XLSX | openpyxl / xlsx/openpyxl-3.1.5/row-v2 | 多 sheet；首行行为明确；merged 值只填检索文本；formula/cache/raw_number 原 proof 保留 | 真实生成 workbook+既有 frozen-sales 样本通过；缺 cache 不编造数字，不向检索输出 UNKNOWN 当事实 |
| XLS | xlrd/2.0.2 / xls/native-v1 | genuine BIFF decoding、sheet row k:v、span origin proof；公式/cache UNKNOWN | 官方 ragged.xls 真实解码通过；不属于 P2_PARTIAL_XLS_RUNTIME；严格数值用途仍不支持 |
| PDF text | pymupdf/weknora-page-router-v1 / pdf/v4-weknora-page-router-pymupdf-1.26.4 | native text、既有 table geometry/raw evidence、嵌图/OCR资产 | 真实生成 PDF 和既有 vertical-merge table chain 通过；7 项需要专用 frozen 目录的旧 PDF 测试 SKIP |
| PDF scan/mixed | 同上 | 每页 raster→OCR；source_asset→derivative；混合原文保留；无全文一刀切 | 真实 PDF routing + SIMULATED OCR 通过；真实 Tesseract NOT RUN |
| image/* | pymupdf/tesseract / image/v2-ocr | source bytes/hash + OCR original text + optional caption derivative，分别失败 | 真实 PNG + SIMULATED OCR/VLM 通过；纯 caption 可成为定性可检索文本，不能成为原始数值事实 |

ParserError.category 兼容旧内部错误字符串并提供 UNSUPPORTED_FORMAT / PARSER_UNAVAILABLE / SOURCE_CORRUPT / PASSWORD_PROTECTED / OCR_UNAVAILABLE / OCR_EMPTY / VLM_UNAVAILABLE / PARTIAL_PARSE / NO_SEARCHABLE_CONTENT 分类。诊断不输出任意 provider 异常文本或输入。资产失败记录自己的 code/status；整文有可检索内容可 partial，无 content/chunk 继续 failed，不能激活空版本。新 PDF blank page 无 OCR asset 是按上游 classifier 的有意合同变化。

cloud VLM 只有可注入 port/guard 合同，没有具体云 SDK/模型/Key/运行开关；应用默认不会调用。已有费用总账未改，不能声称真实 settlement、所有模型调用统计或实际费用已验证。guard 的生产实现必须同时核对 global/KB 外发和月预算；供应商 usage 不等于最终账单，None 必须保守占用。该外部能力明确为 READY_FOR_EXTERNAL_VALIDATION。

## 5. SourceLocator、历史与持久化边界

没有给 SourceLocator 新增 cells/proof/parser-state 字段；新 proof 在 NormalizedDocument.table_row_proofs 和 version manifest。现有 chunk/citation 消费者继续使用历史 locator cells 的兼容投影，尚未用独立 proof 替换 FinalAnswerCommitCheck 的读取接口；这项替换属于后续阶段，不能现在删除旧字段。

历史 locator JSON、新 normalized JSON、legacy row renderer 与严格旧 row_facts 均有读取测试。只影响以后新版本/重索引内容，未重写历史 chunk、snapshot、citation 或原始文件。新 XLSX 默认不猜表头，因此过去依赖 inferred header 的新上传需要明确打开 header config 才能形成精确数值见证；没有擅自以模型猜测填补。

Manifest 的引用保存在 document_versions 的两个新增字段；CAS hash 复核通过。此轮 manifest 覆盖成功完成索引的版本；失败版本继续由已有 job.error_code 和已保存资产诊断记录，不承诺失败时完整逐页 manifest。没有开放新的 proof API。上线应用代码前需在新授权下应用 additive migration；真实 DB 的既有 schema 不推定已一致。

前端文件选择器仍沿用原有 accept 列表，未加入 DOC/XLS 扩展；后端 Registry/API 支持与前端选择器展示是不同状态，本阶段没有修改前端交互。Image Caption 默认关闭，只有可信组合根显式注入才接通；不是宣称默认生产已开启。

## 6. 依赖、测试与因果对照

新增并精确锁定：python-docx 1.2.0、beautifulsoup4 4.14.3、xlrd 2.0.2；安装解析到的 lxml 6.1.3、soupsieve 2.10 也锁定。此前 openpyxl 3.1.5/PyMuPDF 1.26.4/Pillow 12.3.0 不变。包名/发布版本经官方 PyPI 验证；成功安装及 import/version 冒烟 exit 0。第一次 pip 因默认 Temp 权限失败，改用工作区 var/p2-temp 后安装成功，没有关闭保护或修复无关环境。

真实 XLS fixture 来自 python-excel/xlrd 2.0.2，原样保存 BSD license 和出处；SHA-256 `a144c284163641c2cb7dfc17d0116006aeffc3dff2b4878f039d4dbab6e2b07e`。测试运行不下载文件。新增 test_p2_document_processing.py 含 33 个实际收集用例；真实格式文件与 SIMULATED 模型/SQL/merge 场景在文件中明示。

| 命令/证据 | 实际结果 |
|---|---|
| 首轮 affected，p2-first.xml | exit 0，107 passed / 7 skipped |
| 新测试 + XLSX 首轮，p2-new.xml | exit 1，47 passed / 1 failed：旧断言要求 missing-cache 列进入检索文本；有意迁移为不编造值，同时保留 formula/cache proof，调整对应检索 query，未放宽数值保护 |
| 新测试及提交合同定向，p2-targeted.xml | exit 0，208 passed |
| 最终定向，p2-final.xml | exit 0，358 passed / 7 skipped；包括 P1 全部定向文件、格式/proof/caption/边界模块 |
| 最终广泛离线，p2-regression.xml | exit 1，1033 passed / 134 skipped / 9 failed / 20 errors；不是全绿 |
| P2 起点快照复现上述 29 项，p2-baseline-failures.xml | exit 1，9 failed / 20 errors；与当前逐 testcase identity、error type/message 相同，新增失败 0 |
| p2-causal-comparison.json | exit 0；current=29、baseline=29、new=[]、same_type_and_message=true |
| Alembic 0014→0015 offline SQL | exit 0；仅 ADD 两个 nullable Text；没有连接 DB |
| Git diff --check / AST / 基线内容哈希 | exit 0；详见附录 |

广泛测试的 29 项既存失败：layering 1，legacy DOC 缺外部 fixture 1，multimodal OCR 环境断言 1，product_gap native runtime 显式未配置 6 fail+20 error。失败已在 P2 开始前的源码、相同已安装依赖与环境重现，而不是仅引用旧 P1 报告。134 skip 是显式 OCR/native/frozen fixture/人工场景缺条件，不能算通过。

快照通过 `git archive HEAD` 在 ignored `var/p2-baseline-source` 建立，没有 checkout/reset；293 个初始相关源码/测试/migration/scripts 内容哈希在 var/reports/p2-baseline-manifest.json。测试只执行受筛选的 96 个离线旧文件 + 新模块；未运行需要真实 DB/模型/ledger HTTP 服务的模块。真实服务、付费模型、全量端到端、真实账单、真实 DB restore：**NOT RUN**。没有把模拟或旧测试结果当真实生产验收。

## 7. 风险、验收边界与停止

P2 的实现与离线回归边界满足附件：采用固定上游页级/row 语义，保留版本、原文、证明、引用历史与数值保护；没有新增回归。按全局合同，既存失败不单独阻止本阶段 PASS。没有自行批准遗留缺陷延期，也没有宣称已满足 release gate。

后续外部验证的最小事项是：隔离 DB 应用 0015 并验证 lease/activation/manifest 事务；可信隔离 DOC converter；有授权的 Tesseract/VLM 正反例；cloud guard 与真实账单对账。禁止本轮直接调用这些外部服务。P3 之前可复用此稳定 normalized/proof 接口，但本轮停止，不继续实现 P3，不 push/tag。

## 附录 A：实际命令

测试命令均使用工作区绝对 TEMP/TMP、cloud/langfuse=false，`-B -p no:cacheprovider`。最终 PowerShell 脚本显式 `exit $LASTEXITCODE`，避免包装脚本退出 0 掩盖 pytest exit 1；早期包装曾显示 0，但失败统计始终标 FAIL，最终重新执行记录原生 exit 1。


### 最终定向（exit 0）

```powershell
$env:TEMP=(Join-Path (Get-Location) 'var/p2-temp'); $env:TMP=$env:TEMP; New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null; $env:RAG_CLOUD_ENABLED='false'; $env:RAG_LANGFUSE_ENABLED='false'; $env:RAG_STORAGE_ROOT='var/p2-test-storage'; & .\.venv\Scripts\python.exe -B -m pytest -q --tb=short -p no:cacheprovider backend/tests/test_version_source_contract.py backend/tests/test_final_answer_commit.py backend/tests/test_version_activation.py backend/tests/test_ingestion_state_machine.py backend/tests/test_evidence.py backend/tests/test_evidence_accumulator.py backend/tests/test_citation_resolution.py backend/tests/test_answer_validation.py backend/tests/test_answer_hardening.py backend/tests/test_structured_evidence.py backend/tests/test_caption_fact_guard.py backend/tests/test_cancellation_boundaries.py backend/tests/test_answer_service.py backend/tests/test_quick_evidence_flow.py backend/tests/test_langchain_agent.py backend/tests/test_preview_contract.py backend/tests/test_p2_document_processing.py backend/tests/test_parsers.py backend/tests/test_pdf_table_evidence.py backend/tests/test_local_caption_enricher.py backend/tests/test_xlsx_table_evidence.py backend/tests/test_xlsx_resource_limits.py backend/tests/test_xlsx_storage_paths.py --basetemp=var/p2-pytest-final --junitxml=var/reports/p2-final.xml; exit $LASTEXITCODE
```

### 广泛离线（exit 1）

```powershell
$env:TEMP=(Join-Path (Get-Location) 'var/p2-temp'); $env:TMP=$env:TEMP; $env:RAG_CLOUD_ENABLED='false'; $env:RAG_LANGFUSE_ENABLED='false'; $env:RAG_STORAGE_ROOT='var/p2-test-storage'; $env:RAG_NATIVE_TEST_PYTHON=''; $env:RAG_NATIVE_TEST_FIXTURES=''; $env:RAG_NATIVE_TEST_TMP=''; $env:RAG_OFFLINE_HINT_ARTIFACTS=''; $env:RAG_OFFLINE_SMART_HINT_ARTIFACTS=''; & .\.venv\Scripts\python.exe -B -m pytest -q --tb=short -p no:cacheprovider backend/tests/test_agent_limits.py backend/tests/test_agent_tool_allowlist.py backend/tests/test_agent_trace_sse.py backend/tests/test_answer_hardening.py backend/tests/test_answer_service.py backend/tests/test_answer_validation.py backend/tests/test_backup_manifest.py backend/tests/test_bootstrap.py backend/tests/test_bounded_history.py backend/tests/test_budget_gate.py backend/tests/test_cancel_route.py backend/tests/test_cancellation_boundaries.py backend/tests/test_caption_fact_guard.py backend/tests/test_chunk_strategy.py backend/tests/test_chunking.py backend/tests/test_citation_group_span.py backend/tests/test_citation_resolution.py backend/tests/test_clarification_persistence.py backend/tests/test_config.py backend/tests/test_container_injection.py backend/tests/test_context_and_locators.py backend/tests/test_context_expansion.py backend/tests/test_context_neighbor_repository.py backend/tests/test_context_pool_policy.py backend/tests/test_conversation_scope.py backend/tests/test_egress_matrix.py backend/tests/test_embedding_profile_isolation.py backend/tests/test_evidence.py backend/tests/test_evidence_accumulator.py backend/tests/test_follow_up.py backend/tests/test_fusion.py backend/tests/test_graph_evidence.py backend/tests/test_graph_failure_isolation.py backend/tests/test_graph_scope_sql.py backend/tests/test_health.py backend/tests/test_hybrid_retrieval.py backend/tests/test_ingestion_claiming.py backend/tests/test_ingestion_lease_config.py backend/tests/test_ingestion_retry_route.py backend/tests/test_ingestion_state_machine.py backend/tests/test_inline_citation_group.py backend/tests/test_knowledge_scope_boundaries.py backend/tests/test_knowledge_tools.py backend/tests/test_langchain_agent.py backend/tests/test_langchain_quick_chain.py backend/tests/test_layering_preview.py backend/tests/test_legacy_doc.py backend/tests/test_llm_rerank.py backend/tests/test_local_caption_enricher.py backend/tests/test_model_policy.py backend/tests/test_multimodal_ingestion.py backend/tests/test_native_release.py backend/tests/test_native_release_review_regressions.py backend/tests/test_native_table_evidence.py backend/tests/test_original_header_boundary.py backend/tests/test_parsers.py backend/tests/test_pdf_ocr.py backend/tests/test_pdf_table_evidence.py backend/tests/test_preview_contract.py backend/tests/test_privacy_configuration_answer.py backend/tests/test_product_gap_contract.py backend/tests/test_quality_eval_schema.py backend/tests/test_quality_gate.py backend/tests/test_query_coverage.py backend/tests/test_question_checklist.py backend/tests/test_question_checklist_prompt_replay.py backend/tests/test_quick_answer_language_prompt.py backend/tests/test_quick_chain_budget.py backend/tests/test_quick_chain_quality.py backend/tests/test_quick_citation_prompt.py backend/tests/test_quick_evidence_flow.py backend/tests/test_reference_profile.py backend/tests/test_release_preflight.py backend/tests/test_restore_invariants.py backend/tests/test_retrieval_contract.py backend/tests/test_retrieval_execution_record.py backend/tests/test_retrieval_provenance.py backend/tests/test_retrieval_routing.py backend/tests/test_run_metrics.py backend/tests/test_schema_check.py backend/tests/test_scope.py backend/tests/test_scope_and_graph_routes.py backend/tests/test_smart_citation_replay.py backend/tests/test_smart_table_header_hint.py backend/tests/test_storage.py backend/tests/test_structured_answer_spacing.py backend/tests/test_structured_evidence.py backend/tests/test_table_header_hint.py backend/tests/test_targeted_merge_bound.py backend/tests/test_text_normalization.py backend/tests/test_version_activation.py backend/tests/test_xlsx_resource_limits.py backend/tests/test_xlsx_storage_paths.py backend/tests/test_xlsx_table_evidence.py backend/tests/test_version_source_contract.py backend/tests/test_final_answer_commit.py backend/tests/test_p2_document_processing.py --basetemp=var/p2-pytest-regression --junitxml=var/reports/p2-regression.xml; exit $LASTEXITCODE
```

### P2 起点失败对照（cwd=var/p2-baseline-source，exit 1）

```powershell
$env:TEMP='C:\Users\22088\.codex\worktrees\rag-retrieval-opt-round1\RAG quention\var\p2-temp'; $env:TMP=$env:TEMP; $env:RAG_CLOUD_ENABLED='false'; $env:RAG_LANGFUSE_ENABLED='false'; $env:RAG_NATIVE_TEST_PYTHON=''; $env:RAG_NATIVE_TEST_FIXTURES=''; & 'C:\Users\22088\.codex\worktrees\rag-retrieval-opt-round1\RAG quention\.venv\Scripts\python.exe' -B -m pytest -q --tb=short -p no:cacheprovider 'backend/tests/test_layering_preview.py::test_declared_layering_has_no_violations' 'backend/tests/test_legacy_doc.py::test_simulated_doc_conversion_reuses_real_table_citation_chain' 'backend/tests/test_multimodal_ingestion.py::test_pdf_and_image_keep_source_locators_and_image_failure_is_recoverable' 'backend/tests/test_product_gap_contract.py::test_source_declared_docx_header_qualifies_complete_and_preserves_provenance' 'backend/tests/test_product_gap_contract.py::test_declared_docx_complete_pipeline_accepts_correct_row_unit_and_original_citation' 'backend/tests/test_product_gap_contract.py::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[header-role]' 'backend/tests/test_product_gap_contract.py::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[header-origin]' 'backend/tests/test_product_gap_contract.py::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[row-origin]' 'backend/tests/test_product_gap_contract.py::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[foreign-table]' 'backend/tests/test_product_gap_contract.py::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[missing-unit]' 'backend/tests/test_product_gap_contract.py::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[wrong-column]' 'backend/tests/test_product_gap_contract.py::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[wrong-policy]' 'backend/tests/test_product_gap_contract.py::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[partial]' 'backend/tests/test_product_gap_contract.py::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[conflict]' 'backend/tests/test_product_gap_contract.py::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[header-source]' 'backend/tests/test_product_gap_contract.py::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[header-version]' 'backend/tests/test_product_gap_contract.py::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[header-document]' 'backend/tests/test_product_gap_contract.py::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[chunk-document]' 'backend/tests/test_product_gap_contract.py::test_declared_docx_positive_fixture_rejects_invalid_provenance_or_unit[chunk-version]' 'backend/tests/test_product_gap_contract.py::test_declared_docx_validator_rejects_wrong_generated_claim[\u7d2b\u6e7e\u95e8\u5e97 2027-04 \u7684\u8425\u4e1a\u989d\u4e3a 741 \u4e07\u5143 [E1]\u3002]' 'backend/tests/test_product_gap_contract.py::test_declared_docx_validator_rejects_wrong_generated_claim[\u5176\u4ed6\u95e8\u5e97 2027-04 \u7684\u8425\u4e1a\u989d\u4e3a 741 \u5343\u5143 [E1]\u3002]' 'backend/tests/test_product_gap_contract.py::test_declared_docx_validator_rejects_wrong_generated_claim[\u7d2b\u6e7e\u95e8\u5e97 2027-05 \u7684\u8425\u4e1a\u989d\u4e3a 741 \u5343\u5143 [E1]\u3002]' 'backend/tests/test_product_gap_contract.py::test_declared_docx_validator_rejects_wrong_generated_claim[\u7d2b\u6e7e\u95e8\u5e97 2027-04 \u7684\u8425\u4e1a\u989d\u4e3a 742 \u5343\u5143 [E1]\u3002]' 'backend/tests/test_product_gap_contract.py::test_docx_ambiguous_source_declarations_keep_unknown_and_partial[no-declaration]' 'backend/tests/test_product_gap_contract.py::test_docx_ambiguous_source_declarations_keep_unknown_and_partial[false-declaration]' 'backend/tests/test_product_gap_contract.py::test_docx_ambiguous_source_declarations_keep_unknown_and_partial[non-leading]' 'backend/tests/test_product_gap_contract.py::test_docx_ambiguous_source_declarations_keep_unknown_and_partial[multiple-header]' 'backend/tests/test_product_gap_contract.py::test_docx_ambiguous_source_declarations_keep_unknown_and_partial[duplicate-label]' 'backend/tests/test_product_gap_contract.py::test_docx_ambiguous_source_declarations_keep_unknown_and_partial[merged-header]' --basetemp='C:\Users\22088\.codex\worktrees\rag-retrieval-opt-round1\RAG quention\var\p2-pytest-baseline' --junitxml='C:\Users\22088\.codex\worktrees\rag-retrieval-opt-round1\RAG quention\var\reports\p2-baseline-failures.xml'; exit $LASTEXITCODE
```

### Additive migration offline SQL（exit 0）

```powershell
$env:RAG_DATABASE_URL='postgresql+psycopg://offline:offline@127.0.0.1:1/offline'; .\.venv\Scripts\python.exe -B -m alembic upgrade 0014_version_source_metadata:0015_processing_manifest --sql
```

这是明确的虚拟占位 URL，仅生成 SQL，没有连接数据库。最终 git diff --check exit 0。

## 附录 B：最终内容身份

初始 293 个路径中 16 个为本阶段有意修改、277 个未变化；另有新文件。所有 19 个改动 Python 文件 AST 读取成功（不是执行测试）。P0 报告原始字节哈希未变化；未检测到授权范围外并行源码变动。下表 SHA 是 staging 前工作树原始字节；Git autocrlf 可能规范化行尾。

| 改动路径 | SHA-256 |
|---|---|
| `alembic/versions/0015_processing_manifest.py` | `65033a3345388bf28d1f69899a4854724878a00b752876148390d556ee89f399` |
| `backend/app/adapters/parsers/__init__.py` | `70577bb49f4df6024c47d4c71a59aeabfe2162819bb9dc46b54354f1560dafb5` |
| `backend/app/adapters/parsers/native.py` | `a0ef820d6c008e40883c1fbc32f8bbd0da8a23f25eea3f4707a904181cda58cb` |
| `backend/app/adapters/parsers/native_worker.py` | `15827b4453b8e18fe33ac8afb69c71440e55cce4c202fdfd3345c4e0209ca48e` |
| `backend/app/adapters/parsers/xls.py` | `4c8b1c6ceac7a9c6348438379880e5376680360c5209919b6e5a3052ef383ca5` |
| `backend/app/adapters/parsers/xlsx.py` | `8639db470378c5fdf690acaa23e9b23342b4c9d1b53e2f51b95992951ac0aaea` |
| `backend/app/adapters/postgres/knowledge_repository.py` | `bca9ca556793a94ba445ba74b0328f3d6c677dcaf6aa104c5dd157d9c5f44df7` |
| `backend/app/application/caption.py` | `63183f63acfe70c9c9322a5d92dbf0e320898cf167d9d3795f2a02154bc3b694` |
| `backend/app/application/local_caption.py` | `8c4d0a9632565656d457e9b4010420c9024a32698119a8ed036b7af21a42539e` |
| `backend/app/application/structured_evidence.py` | `20884963dcf09105865ea6b2d9da1c900848d15903cb7c3207d4791054b2c25e` |
| `backend/app/bootstrap.py` | `e61c1f0ab7cfdb90058b36333a66de465b24d9951048e2427fbaf30aa7f1f2a9` |
| `backend/app/config.py` | `37d9c53a7f804548aaba6e029f0ec2d52ce90b283ac699bc527a26d65966b622` |
| `backend/app/domain/models.py` | `3d4500c243d688d92ebc2ea7ab421cdbf5f68637aadbe76578ac3d1407a05309` |
| `backend/app/domain/parsers.py` | `ec1c906776347c5a0db237eb7e90945e87c0fa80868947de37bad584804e9872` |
| `backend/app/domain/table_evidence.py` | `e75e28de665086bfbd11fdb3294d7efd31d9f846404f022867ba11513ee73e0d` |
| `backend/app/ports/providers.py` | `8f8bcc5bba05311a31ef594e5a9480ed90b4866ae7906f7278bb2fb3de8c790b` |
| `backend/tests/fixtures/p2_weknora/README.md` | `7159ef66490ec0582f9173215af329d7ab1bd5d0f1b07208392ccd9a0bb13480` |
| `backend/tests/fixtures/p2_weknora/ragged.xls` | `a144c284163641c2cb7dfc17d0116006aeffc3dff2b4878f039d4dbab6e2b07e` |
| `backend/tests/fixtures/p2_weknora/xlrd-LICENSE` | `b5a5dbce60265e305a815a6cb83ed07f24519d8ba644f2a307994488bced8815` |
| `backend/tests/test_p2_document_processing.py` | `5959fb3a018f0245dcf7b116f5731d3d9d7a49a0e503d907634d468e7be786e4` |
| `backend/tests/test_pdf_table_evidence.py` | `f5b93a3479708107e1ee9fa2a3940d631f166076a0353a543527bad44d9b547e` |
| `backend/tests/test_xlsx_table_evidence.py` | `43cb341a7470010a7d094b8e86ac8905bac1485c284dd35ce644509ea921ca12` |
| `pyproject.toml` | `66f64e0311d794b081e355e8a731db22a542a2b7dc544c4a085f14654f5e0887` |

### 本地测试证据哈希

| 文件（var/reports/） | SHA-256 |
|---|---|
| `p2-first.xml` | `5f621879023290b4e9a8ced9b47a44cf2ee3415524dd3e3f0684811a433a6607` |
| `p2-new.xml` | `770723fa28cd50e503cfccf2b4008dbd1dc00f37f0b918032ae21ef17fe35f9d` |
| `p2-targeted.xml` | `e010eab10faec1f0f8c81efcc48175619e75bacdd1803022b8f1ce9f47ee6659` |
| `p2-final.xml` | `100df3d2a27c2cb32d6db1444c99d760fcd62258019766ad9b5e172f8ff4ec94` |
| `p2-regression.xml` | `a7db3bb7f7bf1b8f321e7c62448bd6a7ac06af534ea6a25f4d37f4917d315a74` |
| `p2-baseline-failures.xml` | `f7bba82195df9a3f9c5461508bebd7ce26ace4db411ffb4715adf8a7a036249a` |
| `p2-causal-comparison.json` | `5cdd12436162b4e1b25876ecb836b17e7c40a5d9735b25621f0852fb73e726f5` |
| `p2-baseline-manifest.json` | `66e8eca14262b8625192dd6767d22f8feaa246f4ef969272280860da477332fd` |
| `p2-changed-source-manifest.json` | `c0aca818e67d33cc810d0566c583383c065bd0956e33cf306f41e3fbca3446c7` |
