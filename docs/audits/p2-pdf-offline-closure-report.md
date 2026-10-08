# P2-PDF-OFFLINE-CLOSURE 补证报告

日期：2026-10-08。结论：**P2_PDF_CLOSURE_PASS**，提交协调者复核，未授予里程碑验收。

**1. 目标、版本与实际改动**

开始及结束 HEAD 均为 `44e84831944f8c9da3c30e48d45a9cfb30c3eeb2`；branch 为 `codex/local-first-rag-v1-20260930`。开始 `git status --short --untracked-files=all` 为空。相对本轮开始记录的 477 个已跟踪文件，结束仅 `backend/tests/test_pdf_table_evidence.py` 内容变化：fixture 缺省路径改为仓库内 frozen 目录，保留显式环境变量覆盖。AST 比较确认排除 `frozen()` 后，该模块的 imports、chain helper、全部测试函数及断言完全相同。生产代码、配置、依赖、migration、原 P2 报告和 P1 测试均未改。

新增：`backend/tests/test_p2_pdf_offline_closure.py`、`backend/tests/pdf_closure_fixtures.py`、`backend/tests/fixtures/p2_pdf_closure/` 中三份 frozen 样本及 README/provenance，以及本报告。忽略目录 `var/` 中保留本轮 JUnit、基线与哈希清单。没有 commit/amend、P3、真实 DB、OCR 引擎、VLM、模型/API、服务启动或部署。

任务包：`P2-PDF-OFFLINE-CLOSURE / rev1 / 初始执行 + 一次证据驱动的测试纠正`。主协调为唯一写入者，本轮未派发 Worker。requested route 沿用主协调 Sol high；工具没有提供 app-server-confirmed 或 upstream actual model/effort，实际身份、effort、token 和费用均 UNKNOWN。执行性质为 **MANUALLY_SUPERVISED_TRIAL**。

**2. Fixture 来源与真实链路**

项目 `docs/pdf-native-tables.md` 明确引用的历史轮次为 `D:/RAG-M5-PDF-TABLE-01-rev1attempt1`。读取其 `harness/prepare.py`，确认三份 fixture 来自 `D:/RAG-PDF-TABLE-POC-01-rev1attempt1/fixture`。逐个比对旧轮次文件、原 PoC 文件和旧 `output/artifact-manifest.json` 的字节数与 SHA-256，均一致，然后原样复制入仓库。本轮不下载资料、不重写 gold。旧 manifest SHA-256：`4b8c0aed8d7012e5bc712b9d598f893d14859b53dd9515412d9429988bd42d04`。

| Frozen 文件 | 字节数 | SHA-256 |
|---|---:|---|
| tables.pdf | 273256 | `327630979b4097b3f78732d46aea86888c78ffae4298fdad3c6519e86afad08f` |
| gold.json | 18157 | `d44ff365b77800fab83b4604b26a53f9152fa8d810851e52f8cf93aae8836f79` |
| scan_table.png | 5348 | `1a02f380dd87cd422e8ed0f58773b2d3578a8ee30165b19784416824a3ed67f2` |

五页分别为有边框、横向合并有边框、两页无边框、扫描页。gold 中设计几何独立于本轮 extractor 输出。它们是 **SIMULATED 合成输入**，真实 PDF 解码、几何提取和证据生成，不代表真实语料准确率。

新增 generator 固定坐标、文本、缺线位置，使用 `no_new_id=True`；重复生成复杂跨度 PDF 字节一致，已实际断言。生成后写入 pytest 临时目录并重新打开。复杂跨度候选由真实 `Page.find_tables(strategy='lines')` 返回，未 mock 表格或 bbox。一次前置构造筛选中的开放边框仍被 PyMuPDF 补成矩形，未满足“未确认跨度”前提，因此没有把它当负例或实现缺陷；最终负例是两个内部缺线造成重叠 origin 的非矩形网格。

执行链：`ParserRegistry/PdfParser → page_tables/map_table → NormalizedDocument → chunk_document → scoped HybridRetriever → ContextBuilder → CitationService.freeze/resolve`。retrieval 与 citation store 是明确的内存替身；其范围、context、quote、locator 等实际应用逻辑运行。无内容测试另外走真实 CAS 源文件及生产 `PostgresKnowledgeRepository.process_job`，SQLRecorder 仅代替数据库响应/记录 SQL；不声称验证真实事务、并发、DB migration 或 DB 已应用版本。

OCR 失败隔离采用不存在的 tessdata 目录，`PdfParser._ocr` 在检查 `eng/chi_sim.traineddata` 时真实返回 `OCR_UNAVAILABLE`，不会进入 OCR 引擎。原复杂跨度测试的 mock 保留为单元拒绝分支证据；新增真实 PDF 用例承担几何闭环证据。

**3. 原 7 项缺口逐项对照**

下表原节点均位于 `backend/tests/test_pdf_table_evidence.py`。它们在 P2 最终报告中 SKIP，本轮全部实际 PASS；没有把 SKIP 改写成 PASS。原合同中的“空白页资产”按获准 P2 合同解释，详见下一段。

| 原合同 / 原测试节点 | P2 原有替代覆盖及缺口 | 本轮执行与补充 | 实际结果 |
|---|---|---|---|
| 有边框逐格、空格、空单元格、引用 bbox；`test_frozen_bordered_geometry_rawtext_context_and_page_bbox[1]` | 已有 vertical-merge 生成 PDF 可验证纵合并 origin；不能替代 frozen 5x3 全几何及原始双空格 | 原节点直接读取核验 frozen gold：整个 table/cell bbox、raw API rows 与原始 raw_text、`Two  spaces`、JSON roundtrip、Beta `-7.25`、空格单元与 context/citation bbox | PASS |
| 横向合并及逐格/bbox；同上 `[2]` | vertical-merge 不覆盖横合并/缺省占位 | 原节点验证 `Inventory` origin、column_span=2、唯一 origin、`R1C1:R1C2`、全部 row_cells、Beta 第4行引用 | PASS |
| 无边框只能为候选、扫描不可产生确认表；`test_borderless_scan_status_and_raw_candidates_are_not_complete` | `test_real_pdf_page_routing_with_simulated_ocr` 只证明页路由/资产，不覆盖无边框几何候选状态 | 原节点验证 p3/p4 的真实 text-strategy 候选和 API 9 行保留、raw quote、partial，以及 p3/p4/p5 无 table chunks；p5 unsupported/failed OCR 可见 | PASS |
| 多页身份隔离、正文去重、页码及精确引用；`test_cross_page_mixed_text_table_dedup_and_citations` | `test_pdf_page_failures_keep_other_native_page` 证明有用页保留，不证明 table ID/几何去重 | 原节点真实复制两页，验证2个唯一 table ID、4段正文、表内数值不重复为正文、chunk 拼接等于 normalized content、p2 Beta 引用 `[50,152,410,188]` | PASS |
| 空白页诊断与无内容失败；`test_empty_and_scan_keep_failed_ocr_visible[empty]` | fixed classifier 阈值用例包含 `(0,0)→text`，但没有生产空文失败闭环 | 原节点：无 table/chunk，partial+unsupported，无 OCR 资产。新增 `test_page_failure_retains_native_content_and_exact_page_citation[empty]` 保留p2数值/引用；`test_production_job_rejects_no_content_preserves_failed_assets[empty-EMPTY_TEXT]` 验证两个 failed 更新、无 chunks/ready/activation | PASS，3节点 |
| 扫描失败资产、可用内容及无内容失败；同上 `[scan]` | 原路由/异常隔离用例有 SIMULATED OCR；`test_empty_scanned_pdf_cannot_activate` 是内存入库 + OCR_EMPTY，不等于生产缺 OCR failure SQL | 原节点验证真实扫描 PDF failed OCR；新增 `[scan]` 验证 `scanned_page` ready、OCR failed、派生链接、页码和p2原文引用；新增 `[scan-OCR_UNAVAILABLE]` 用真实解码/生产 job 观测到资产插入记录、失败更新且无空版本激活；未验证 SQL 调用先后顺序 | PASS，3节点 |
| 未确认复杂跨度不能生成有效表；`test_unconfirmed_complex_span_does_not_fake_success` | 原节点本身注入 malformed API table，不能证明 PDF 解码/几何正确性 | 原节点仍 PASS，另增 `test_real_pdf_unconfirmed_overlap_cannot_become_table_evidence`：真实网格候选中 `[50,116,290,188]` 与 `[50,152,170,188]` origin 重叠；SPAN_UNCONFIRMED、无 tables/proofs/table chunks、raw candidate/text 保留、文本引用仍可回读、源文件 hash 不变 | PASS，2节点 |

P2 空白页行为具有依据：固定 GitHub 基线为 WeKnora v0.8.2 `3e8b0bfc80b845b2d4b2ed683994748741450a97`。沿用已批准 P2 报告对 `docreader/parser/pdf_parser.py` 分类顺序的源码核验：先 image bbox 面积和 / 页面面积 ≥0.5，再 text_len<10 且 ratio≥0.1，否则 text。真正空白页 ratio=0、text_len=0 走 text，不栅格化、不创建虚假 OCR 资产。本项目 `classify_pdf_page` 与 `PdfParser.parse` 实现此行为，P2 报告已明确记录有意合同变化。本轮未请求网络或以本机 WeKnora 差异覆盖固定基线；保留 blank partial/unsupported 和生产 EMPTY_TEXT 失败保护，不恢复旧“空白即 OCR”协议。

数值/引用保护没有削弱：原 test/chain AST 不变，原 raw/gold/bbox/quote 断言实际执行；P1 `test_final_answer_commit.py`、`test_version_source_contract.py` 和相关 structured/citation/answer/caption 回归也实际执行。几何与精确字符串保护不等于语义表头确认，PDF inferred header 仍 UNKNOWN，partial 状态保留。

**4. 执行命令、退出码与失败记录**

运行目录为本报告所在仓库根目录。pytest 前设置：

```powershell
$env:TEMP=(Join-Path (Get-Location) 'var/p2-pdf-closure-temp'); $env:TMP=$env:TEMP
$env:RAG_CLOUD_ENABLED='false'; $env:RAG_LANGFUSE_ENABLED='false'
$env:RAG_PDF_TEST_FIXTURES=''; $env:RAG_PDF_EVIDENCE_DIR=''
```

首次原模块运行还执行了 `New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null`。最终关联回归另设置 `$env:RAG_STORAGE_ROOT='var/p2-pdf-closure-storage'`。以下均使用当前 `.venv`，Python/PyMuPDF/Pillow 依赖未安装、升级或修复。

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q --tb=short -p no:cacheprovider backend/tests/test_pdf_table_evidence.py --basetemp=var/p2-pdf-closure-original --junitxml=var/reports/p2-pdf-closure-original.xml
.\.venv\Scripts\python.exe -B -m pytest -q --tb=short -p no:cacheprovider backend/tests/test_p2_pdf_offline_closure.py --basetemp=var/p2-pdf-closure-new --junitxml=var/reports/p2-pdf-closure-new.xml; exit $LASTEXITCODE
.\.venv\Scripts\python.exe -B -m pytest -q --tb=short -p no:cacheprovider backend/tests/test_p2_pdf_offline_closure.py --basetemp=var/p2-pdf-closure-new-repair --junitxml=var/reports/p2-pdf-closure-new-repair.xml; exit $LASTEXITCODE
.\.venv\Scripts\python.exe -B -m pytest -q --tb=short -p no:cacheprovider backend/tests/test_version_source_contract.py backend/tests/test_final_answer_commit.py backend/tests/test_version_activation.py backend/tests/test_ingestion_state_machine.py backend/tests/test_evidence.py backend/tests/test_evidence_accumulator.py backend/tests/test_citation_resolution.py backend/tests/test_answer_validation.py backend/tests/test_answer_hardening.py backend/tests/test_structured_evidence.py backend/tests/test_caption_fact_guard.py backend/tests/test_cancellation_boundaries.py backend/tests/test_answer_service.py backend/tests/test_quick_evidence_flow.py backend/tests/test_langchain_agent.py backend/tests/test_preview_contract.py backend/tests/test_p2_document_processing.py backend/tests/test_parsers.py backend/tests/test_pdf_table_evidence.py backend/tests/test_local_caption_enricher.py backend/tests/test_xlsx_table_evidence.py backend/tests/test_xlsx_resource_limits.py backend/tests/test_xlsx_storage_paths.py backend/tests/test_context_and_locators.py backend/tests/test_p2_pdf_offline_closure.py --basetemp=var/p2-pdf-closure-regression --junitxml=var/reports/p2-pdf-closure-regression.xml; exit $LASTEXITCODE
```

| 实际运行 | 退出码 | PASS | FAIL/ERROR | SKIP | JUnit SHA-256 |
|---|---:|---:|---:|---:|---|
| 原 PDF 模块，p2-pdf-closure-original.xml | 0 | 10 | 0 | 0 | `577ae79521f7ba2ce798e8688785ef5b0bf5c4538b1f27594188086283d91505` |
| 新增首轮，p2-pdf-closure-new.xml | 1 | 4 | 2 | 0 | `efb7481cb8c08d2bdca326bbbde9a51f0258080ff1265e755b9ecf3b761f87d2` |
| 一次纠正后，p2-pdf-closure-new-repair.xml | 0 | 6 | 0 | 0 | `d58ca07771e42d6d6841b77198653382affa5dd244520758771e690f9bcad1ed` |
| 最终25模块回归，p2-pdf-closure-regression.xml | 0 | 377 | 0 | 0 | `49deb5562797e651565416aca9552c42ac3dd01e89ada437211e4c777b8c45f2` |

首轮失败节点：`test_page_failure_retains_native_content_and_exact_page_citation[scan]` 与 `test_production_job_rejects_no_content_preserves_failed_assets[scan-OCR_UNAVAILABLE]`，均为 StopIteration：新增测试误用 `source_image`，而既有模型枚举、PDF parser 和 P2 已有路由测试明确使用 `scanned_page`。仅纠正这两处测试资产类型，未改生产实现，未删除状态/派生/页码/失败断言。首轮失败 XML 保留。最终回归后没有再改任何测试、generator 或 frozen 文件。

每次 pytest 有5项 PyMuPDF SWIG deprecation warnings。Python 启动还输出 `Failed to find real location of D:\Drivers\python\python.exe`，命令仍执行完成，上述退出码和 JUnit 是实际结果；不声称环境警告消失。

`git rev-parse HEAD`、`git status --short --untracked-files=all`、`git diff --check` 实际退出0；diff 无空白错误，仅提示既有测试文件后续 Git 操作可能 LF→CRLF。基线 hash/AST/JUnit 交叉核验 Python 命令实际退出0；477个已跟踪文件仅 fixture helper 改动，其余476个原始字节 hash 一致。新文件另作 whitespace 检查与最终 SHA 清单，见 `var/reports/p2-pdf-closure-final-manifest.json`。

**未实现的命令不得报告通过。** 本轮没有运行 verify-mX、真实服务、模型或全仓广泛回归；这些结果为 NOT RUN。本轮377通过只覆盖列出的相关模块，不把旧 P2 全仓29项失败/错误改判通过。旧报告、`p2-regression.xml`、`p2-baseline-failures.xml` 和因果对照均保留。

**5. 最终文件 SHA-256、风险与下一步**

| 新增/修改文件 | SHA-256 |
|---|---|
| backend/tests/test_pdf_table_evidence.py | `654699f76e338990f2a595242062332707fb93f1652bc84ff81be30616ce0a02` |
| backend/tests/pdf_closure_fixtures.py | `4945c934f90a0b44892c57164b33b1f20d6b2b2d6b952d42e90bf820aabf3491` |
| backend/tests/test_p2_pdf_offline_closure.py | `46f9b5d2456fb88f6599b02f8ba55ab2df1971bfbc7dc82d825b82db97f15c73` |
| backend/tests/fixtures/p2_pdf_closure/provenance.json | `f31a0e78dd006ebd95ea4e5a1e5aeb613aaaa36e2dafcb76761be436b5506c65` |
| backend/tests/fixtures/p2_pdf_closure/README.md | `e091c5f5ad31b7cf28ed33e2740c1ca972982a2165badb258a58ee5c3b6ff5a0` |

三份 frozen hash 见第2项；本报告自 hash 记录在外部 final-manifest，避免自引用。`var/reports/p2-pdf-closure-evidence.json` 记录 baseline identity、全部运行计数/失败节点、上述源码与关键未改生产文件 hash；final-manifest 再记录本报告、JUnit、证据 JSON、最终 status 与 diff 检查。最终工作树包含本轮上述未提交内容，不能称为干净；开始时干净，没有覆盖用户改动，未观察到其他并行已跟踪文件变化。

边界仍在：**依赖升级影响未独立验证**。离线 SIMULATED DB/输入/部分回归 OCR 替身不能替代真实 DB/OCR/VLM 验证，Owner 已指定它们为上线前阻断项，本轮没有申请延期或宣称解除。未覆盖真实语料复杂版式、CJK、旋转/裁切、性能压力、真实 OCR 质量或表头语义确认。未发现本轮合同范围内需要修改生产代码的失败。

当前状态 **P2_PDF_CLOSURE_PASS / NEEDS_OWNER_REVIEW**，仅表示7项缺口已有可复核离线证据。本轮停止，不 commit、不 amend P2、不进入 P3。由协调者复核后再决定独立提交这次补证。

**6. 2026-10-08 提交前属性与证据措辞修正**

本节追加本次结果，不覆盖上文历史运行。本轮 HEAD 仍为 `44e84831944f8c9da3c30e48d45a9cfb30c3eeb2`，开始 index 为空；保留原有补证工作树。此前 `.gitattributes` 不存在，本次只新增以下三个精确根路径规则，没有修改全局 Git 配置：

```gitattributes
/backend/tests/fixtures/p2_pdf_closure/tables.pdf -text
/backend/tests/fixtures/p2_pdf_closure/gold.json -text
/backend/tests/fixtures/p2_pdf_closure/scan_table.png -text
```

三份 fixture 和 provenance 工作树原始字节未改，测试、生成器、生产代码均未改。第3项扫描失败行的“资产插入先于 failed”已更正为“观测到资产插入记录、失败更新且无空版本激活；未验证 SQL 调用先后顺序”。未增加 SQL 时序断言，不声称现有测试证明调用顺序。

保留的旧报告：`var/reports/p2-pdf-precommit-20261008-133959/report-before.md`，SHA-256 `4b5c3954ce8db7c09c029f7033e82e6c56f62709da46bd86563481aba754b305`。本次报告差异保存为同目录 `report-before-after.diff`；旧 review ZIP、旧 closure-evidence/final-manifest/baseline、四份 JUnit（包括原首轮2失败）均保持原文件和原 hash。

实际暂存命令如下，首次因 sandbox 无法创建 index.lock 退出1；经工具授权审查后原命令成功退出0。没有改路径绕过权限，没有提交：

```powershell
git add -- .gitattributes backend/tests/test_pdf_table_evidence.py backend/tests/test_p2_pdf_offline_closure.py backend/tests/pdf_closure_fixtures.py backend/tests/fixtures/p2_pdf_closure/README.md backend/tests/fixtures/p2_pdf_closure/gold.json backend/tests/fixtures/p2_pdf_closure/provenance.json backend/tests/fixtures/p2_pdf_closure/scan_table.png backend/tests/fixtures/p2_pdf_closure/tables.pdf docs/audits/p2-pdf-offline-closure-report.md
```

使用 `git ls-files --stage -z` 保存测试所用 index 清单，未创建 commit；通过下述命令在全新独立目录检出完整暂存快照，退出0，未覆盖当前工作树：

```powershell
git checkout-index --all --prefix="C:/Users/22088/.codex/worktrees/rag-retrieval-opt-round1/RAG quention/var/reports/p2-pdf-precommit-20261008-133959/checkout/"
git check-attr --cached text -- backend/tests/fixtures/p2_pdf_closure/tables.pdf backend/tests/fixtures/p2_pdf_closure/gold.json backend/tests/fixtures/p2_pdf_closure/scan_table.png
```

属性检查退出0，三项均为 `text: unset`。分别使用 `git show :backend/tests/fixtures/p2_pdf_closure/<name>` 获取真实暂存 blob，和工作树、独立检出字节逐个比较历史 provenance 中的 bytes/SHA-256，结果如下；没有归一化三份历史内容或弱化测试哈希断言：

| 文件 | 工作树/暂存 blob/独立检出字节数 | 三处 SHA-256（完全相同） |
|---|---:|---|
| tables.pdf | 273256 / 273256 / 273256 | `327630979b4097b3f78732d46aea86888c78ffae4298fdad3c6519e86afad08f` |
| gold.json | 18157 / 18157 / 18157 | `d44ff365b77800fab83b4604b26a53f9152fa8d810851e52f8cf93aae8836f79` |
| scan_table.png | 5348 / 5348 / 5348 | `1a02f380dd87cd422e8ed0f58773b2d3578a8ee30165b19784416824a3ed67f2` |

初始辅助核验脚本额外要求 provenance 的暂存 blob 也与工作树字节相同，因此退出1：provenance 作为普通文本暂存为 LF（1362字节、SHA `64f9bc2715e6dd5943823242cf2693f60ff70ee5ef05d332602f4f1fd1d24879`）；工作树及独立检出均为原 CRLF（1395字节、SHA `f31a0e78dd006ebd95ea4e5a1e5aeb613aaaa36e2dafcb76761be436b5506c65`）。JSON 内容完全相同。此额外比较并非 pytest 或三份 frozen 文件断言，后者在该脚本停止前已全部通过。保留 `initial-fixture-check-failure.json`，后续核验如实分别记录 provenance 的 blob/检出状态，未改 provenance 或 tests。限定新增 `-text` 的三个用户指定路径，没有扩大属性范围。

实际 PDF 测试命令如下；`p2-pdf-precommit-current.json` 仅保存上述独立路径。使用项目现有 Python，切换到独立检出目录，并在同一测试进程断言 parser/test imports 源自独立目录：

```powershell
$p2Precommit = Get-Content -Raw var/reports/p2-pdf-precommit-current.json | ConvertFrom-Json; $p2Python = Join-Path (Get-Location) '.venv/Scripts/python.exe'; $env:TEMP = Join-Path $p2Precommit.evidence_dir 'temp'; $env:TMP=$env:TEMP; New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null; $env:RAG_CLOUD_ENABLED='false'; $env:RAG_LANGFUSE_ENABLED='false'; $env:RAG_PDF_TEST_FIXTURES=''; $env:RAG_PDF_EVIDENCE_DIR=''; Set-Location -LiteralPath $p2Precommit.checkout_dir; & $p2Python -B -c "from pathlib import Path; import pytest; import backend.app.adapters.parsers as parsers; import backend.tests.test_p2_pdf_offline_closure as closure; root=Path.cwd().resolve(); assert Path(parsers.__file__).resolve().is_relative_to(root); assert Path(closure.__file__).resolve().is_relative_to(root); print('INDEPENDENT_CHECKOUT_IMPORTS:', parsers.__file__, closure.__file__); raise SystemExit(pytest.main(['-q','--tb=short','-p','no:cacheprovider','backend/tests/test_pdf_table_evidence.py','backend/tests/test_p2_pdf_offline_closure.py','--basetemp=$($p2Precommit.evidence_dir.Replace('\','/'))/pytest-temp','--junitxml=$($p2Precommit.evidence_dir.Replace('\','/'))/pdf-16.xml']))"; exit $LASTEXITCODE
```

结果：**16 passed / 0 failed / 0 skipped，exit 0**。含原10项、新增6项及其中 fixture 哈希检查。JUnit 为 `var/reports/p2-pdf-precommit-20261008-133959/pdf-16.xml`，SHA-256 `0ed136b6a261a27aea59d08882bdbb25b9a890d22681f101e2200e6aa57d31ed`。没有真实 DB/OCR/VLM/API；“依赖升级影响未独立验证”限制不变。Python 路径警告和 SWIG deprecation 提示仍存在。

测试后只追加本节报告，再暂存报告并仅更新独立检出目录的报告副本；测试输入（属性、三份 fixture、provenance、测试、生成器、生产源文件）不变。最终命令 `git diff --cached --check`、`git diff --check`、HEAD/index/工作树白名单和暂存 fixture 再核验结果记录在同目录 `final-check.json`，最终 index identity 另记，不把测试前报告 hash 当作最终报告 hash。

本次白名单共10文件：`.gitattributes`、原 PDF 测试、两个新增测试/生成器、fixture目录五文件及本报告。该10文件是完整补证待提交集合；本轮新增内容修改仅 `.gitattributes` 和本报告。

最终空白检查不能合并为“全绿”：

| 实际检查命令 | 退出码 | 观测 |
|---|---:|---|
| `git diff --cached --check` | 2 | 1135条诊断仅涉及 frozen gold.json / tables.pdf；gold 的历史CRLF被判行尾空白，PDF也有历史格式空白 |
| `git diff --check` | 0 | 无未暂存差异 |
| `git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --cached --check --no-textconv --no-ext-diff` | 2 | 命令级识别CRLF后仅剩 tables.pdf 的93条历史行尾空白；未改任何 Git 配置文件 |
| `git diff --cached --check --no-textconv --no-ext-diff -- .gitattributes backend/tests/test_pdf_table_evidence.py backend/tests/test_p2_pdf_offline_closure.py backend/tests/pdf_closure_fixtures.py backend/tests/fixtures/p2_pdf_closure/README.md backend/tests/fixtures/p2_pdf_closure/provenance.json docs/audits/p2-pdf-offline-closure-report.md` | 0 | 三份 frozen 文件以外的全部待提交文件无空白错误 |

完整stdout/stderr和命令退出码保留在同目录 `diff-check-results.json` 及 `diff-check-1..4.stdout.txt/stderr.txt`。保留历史字节优先，没有为消除提示修改 gold/PDF，也没有增加用户要求之外的属性。初始最终核验脚本把默认 cached check 当作必须0的断言，因此停止退出1；之后仅修正证据汇总以接受并保留真实退出2，不改变检查结果。

属性、暂存/检出历史字节、fixture哈希、16项PDF测试均 **PASS**；严格默认全量暂存空白检查 **FAIL**，本次提交前收口为 **BLOCKED / NEEDS_OWNER_REVIEW**，不得据此批准 commit。若协调者要求该默认检查也退出0，最小补充决策是明确 immutable frozen PDF/CRLF 的 diff/whitespace 属性策略，不能改历史文件字节。本轮不 commit、不 amend、不进入 P3。

**7. 2026-10-08 Owner 批准的二进制差异策略与独立提交闸门**

第6项保留为上轮历史检查，不代表本节处理后的最终状态。Owner 已明确决定，仅将三条既定精确路径从 `-text` 改为 `-text -diff`：tables.pdf、gold.json、scan_table.png 作为不可变二进制资产显示差异，历史字节、长度和 SHA-256 断言继续保留。测试代码、本报告及其他 JSON 的空白检查保持原策略；未设置全局 Git 配置，也没有增加通配属性。

本次只修改 `.gitattributes` 三条属性和追加本节。三份 fixture、provenance、全部测试、生成器和生产代码不变，不改变测试输入。沿用第6项独立检出执行的16项PDF测试和此前377项关联回归证据，本次不重跑测试。旧报告、属性版本、失败输出及前后差异分别保存在本机 `var/reports/p2-pdf-independent-commit-*/` 和之前的补证目录，未覆盖旧证据。依赖升级影响未独立验证，真实 DB/OCR/VLM/API 上线前阻断项不变。

Owner 条件批准独立提交 `test: close P2 PDF offline evidence gaps`，条件为 HEAD=`44e84831944f8c9da3c30e48d45a9cfb30c3eeb2`、暂存范围仍仅第6项10文件、三份 fixture 暂存 bytes/SHA 保持历史值、默认 `git diff --cached --check` 退出0。任一不符立即停止；全部满足才执行新 commit，不 amend P2、不 push、不 tag、不进入 P3。

提交前实际命令、退出码、fixture 暂存哈希、属性值与白名单记录在本机本次目录的 `precommit-gates.json`。独立提交的新 SHA、父提交和提交后 Git 状态记录在同目录 `commit-receipt.json`；目录索引为 `var/reports/p2-pdf-commit-current.json`。本报告不自引用尚未生成的 commit SHA；以实际提交回执为准。上述二进制差异策略只改变展示/空白检查适用对象，不改变 frozen 内容或 PDF 解析测试合同。
