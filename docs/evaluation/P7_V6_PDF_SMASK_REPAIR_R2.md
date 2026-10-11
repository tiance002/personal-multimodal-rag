# RAG-V6-PDF-SMASK-PLACEMENT-REPAIR rev1 attempt1

## 1. 目标与改动

状态：`CHECKS_PASSED / NEEDS_OWNER_REVIEW`。这是 `MANUALLY_SUPERVISED_TRIAL`，不是里程碑验收或完整自动控制证明。

仅修改业务文件 `backend/app/adapters/parsers/__init__.py`，新增 `backend/tests/test_pdf_smask_placements.py`。完整差异：`var/reports/v6-special-subsets-r1/pdf-smask-placement-repair-r2/implementation.patch`。基线副本保留于该目录 `baseline/parsers_init.py`。

PDF 嵌入图像改为逐个真实 image block 处理，避免相同 base 像素但不同 SMask 被 `get_image_rects(xref)` 交叉匹配。可见 masked placement 使用原 PDF 对应区域渲染；全透明对象保持纯白，不能借用底层文本。xref 定位使用解码后的 base+mask 像素身份，记录候选及原字节 SHA；同一 base hash 不能充当 placement 身份。保留 page、placement index、bbox、transform、原 PDF SHA、base/mask SHA 和 OCR 引用回读信息。

仅新增 helper、修改 `PdfParser.parse` 并导入 BytesIO，parser version 为 `pdf/v5-image-mask-placement-pymupdf-1.26.4`。AST 检查确认 `_ocr`、初始化方法、ImageParser、Registry、页分类及非 PdfParser 行为未变。`prepared_ingestion.py` 与 `ports/ingestion.py` 均保持本轮入口 hash；并行线程修改的 repository 等文件未写入。

仅重解析 `11-21-16-Updated-Post-Election-Release.pdf` 第 **1–45页全部页面**，不从 Gold/答案挑页。原始 PDF 5,924,004 bytes，SHA256 `8f49f93f1665fd5ff604e66ffed9649d8b18ce7f114ab43463aeac46cd3d00cb`，固定作者源 revision `2ff6aa9237fc777b6627dc57a486e9225ac5fb86`。本轮未下载、未更改此 PDF。

## 2. 执行命令

cwd 为 `E:\RAG quention`。测试 TEMP/TMP 与 basetemp 均限定在新报告目录。最终实际运行命令：

```powershell
& '.venv/Scripts/python.exe' -B -m pytest backend/tests/test_pdf_smask_placements.py backend/tests/test_pdf_ocr.py backend/tests/test_pdf_table_evidence.py backend/tests/test_p2_pdf_offline_closure.py -q --disable-warnings --basetemp=var/reports/v6-special-subsets-r1/pdf-smask-placement-repair-r2/pytest-final --junitxml=var/reports/v6-special-subsets-r1/pdf-smask-placement-repair-r2/final.junit.xml
& '.venv/Scripts/python.exe' -B var/reports/v6-special-subsets-r1/pdf-smask-placement-repair-r2/run_bounded_parse.py
& '.venv/Scripts/python.exe' -B -X utf8 var/reports/v6-special-subsets-r1/pdf-smask-placement-repair-r2/validate_and_manifest.py
git rev-parse HEAD
git status --porcelain=v1 --untracked-files=no
git diff --stat -- backend/app/adapters/parsers/__init__.py
```

| 运行 | 退出码 | 真实输出 / 证据 |
|---|---:|---|
| 最终定向 pytest，4文件 | 0 | `final.log` / `final.junit.xml`：22 passed，6 warnings，2.06s |
| 单 PDF bounded parse | 0 | `bounded-parse-receipt.json` / `bounded-parse.log`：未超时，180s上限，实际20.904701s |
| frozen artifact / unchanged port validation | 0 | `validation.log` / `validation.json`：460组断言通过；字段/记录比较合计3596项，port 内部单项检查未另计数 |
| 上述3条 Git 读取 | 0 | HEAD `befd5478a680ffa779a1e8ede736260d9bac1060`；工作区已有多线程改动；parser diff 80 insertions / 34 deletions |

失败历史保留：首次定向测试 exit 1，2 failed / 20 passed，6.06s；反例画布导致旧页分类进入 scanned 分支，随后只修正反例尺寸。第二次 exit 1，2 failed / 20 passed，7.20s；实际 placements 已正确，但同一 mask 的两种 PNG 编码导致 xref 字节 hash 查找失败。完成 **1次实现修复**，改用解码像素身份，最终22项通过。原始 `initial.log/.junit.xml`、`repair.log/.junit.xml` 与每轮反例 PDF 均保留。它们是 `SIMULATED` 自包含来源 PDF，实际执行了真实本地 PyMuPDF/Tesseract OCR，不能视为真实数据集整体评测。

另一次只读结构检查因 Windows 默认 GBK 解码 UTF-8 JSON exit 1；指定 UTF-8 后 exit 0。它没有改变业务代码。完整请求中未新增依赖或网络访问。

**未实现的命令不得报告通过。** ECS、DB、正式入库、Embedding、模型/VLM、479主评测、六集最终封装：本轮均 `NOT RUN`，没有将它们记为通过。

## 3. 结果与新旧产物

| 指标 | 原 mml-00 freeze | 新独立 r2 |
|---|---:|---:|
| 来源 PDF / 页数 | 相同 SHA / 45 | 相同 SHA / 45 |
| source image assets | 311 | 57，与原 PDF 实际57个 image blocks 一一相符 |
| OCR ready / failed | 1 / 310 | 53 / 4 |
| normalized 字符 | 97,403 | 98,852 |
| Parent / Child 草稿 | 43 / 561 | 43 / 613 |
| 原生表 / row proofs | 41 / 51 | 41 / 51 |

98条新 locator quote、656条 Parent/Child 回读均通过。45页原生文本、quote、layout evidence、41张表（仅整体文本偏移改变）、51条 proof（仅新版本 ID 改变）保持一致。所有 source asset 字节与 SHA、mask/page/bbox/provenance 一致；prepared_input 使用真实原 PDF 及新 producer pins 验证成功，并正确重绑内部形式 UUID。这是离线接口验证，没有写 CAS/DB，也没有创建正式索引。

剩余4个 `OCR_EMPTY` 已逐图查看：page1/xref47 与 page3/xref137 仅含横线；page1/xref55、59 为完全透明对象，输出为纯白。它们维持 failed，未补造文字。封面可见 `Low Marks for Major` 图像已恢复并进入真实 OCR ready。既有表格/图片语义 unsupported 警告保留，文档状态仍为 `partial`。

冻结接入清单：`var/reports/v6-special-subsets-r1/pdf-smask-placement-repair-r2/ingestion-delta-manifest.json`，SHA256 **`01c86da9f75a3944547f73b2303fc27160d41f32fe1838d0939785bd975d10a1`**。

新 mml-00 的 payload 为 **61文件，14,043,653 bytes**：normalized、asset manifest、chunks、result 各1文件，加57个 PNG。此数字不含3个交接元数据文件，也不含保持原处的原 PDF。清单给出每个文件 path、精确 bytes、SHA 和建议包内路径；并列出旧 mml-00 四件产物、旧 mml-01/02 和原 source identity。元数据另附 `producer-pins.json` 与 `validation.json`。

| 新文件 / 当前 producer | SHA256 |
|---|---|
| `evidence/normalized/mml-00.json`，5,227,841 bytes | `478d62e31d8be9621162f36fe131a0b2dce9b2b5f55150ff022e09f84bfd814b` |
| `evidence/asset-manifests/mml-00.json`，224,287 bytes | `b9ad3339dae5d828407908875fe8dfbd083db3712b89187c41d80adca910f8cc` |
| `evidence/chunk-drafts/mml-00.json`，8,175,416 bytes | `aa5ca56936824063577c59e591d7a1861602fc33bbc8dc93c9d9c4cbd3381934` |
| `producer-pins.json`，2,221 bytes | `d8d40464eb77ade2d50fcf54ef8bf1b305952215590c30ec59a9c1c0cc01a8c2` |
| `validation.json`，61,299 bytes | `b94356055798a2bdd12e1bd7de833d3ab031bfdb80d46f80c424114f697ae2bd` |
| 当前 parser module | `9136ee6d328acbf38c01e5e44680470fc435379cf34f11033f63317aa0c18dfb` |
| 历史 parser module | `70577bb49f4df6024c47d4c71a59aeabfe2162819bb9dc46b54354f1560dafb5` |
| 完整旧100单元 handoff，未改 | `fd18b7d4a1a5c5ed5605602047fd15fb9a272bc85c3052425aa63c8e4afefdd5` |

## 4. 风险与遗留

**实际接入限制：旧100份 prepared bundle 的 parser 文件 pin 均是历史 hash。** 未改动的 prepared_input 会严格比较整个 producer module，所以新版本下均会触发 `PREPARED_MODULE_MISMATCH`，包括 AST 行为未变的 TextParser/ImageParser 旧缓存。替换 mml-00 后仍有99份旧缓存。不能将历史 pins 改成新值并称其已重新验证，也不能声称其他2份 MML/Hotpot/Omni 旧缓存已被当前接口接受。

原始来源字节与其他五集 raw-source 封装不受此 hash 变化影响，不需重封装五集。协调方需明确消费策略：原始来源走正式解析、精确旧 producer runtime，或另行授权兼容/再验证任务。本轮没有改 passed 接口、没有重解析另99单元、没有自行批准延期。新 chunks 从561增至613，之后使用时必须从实际新草稿计算 Embedding/budget reservation，不能复用旧561的数量。

未发现另一独立新根因；2个纯横线和2个透明对象不足以要求再扩张修复。原生表格/语义 warning 的完整性未在本轮扩张处理，保留原状。本轮验证不替代 benchmark 答案语义验收。控制面未证明自动单写者/取消强制，故只报 `MANUALLY_SUPERVISED_TRIAL`。

## 5. 版本与下一步

HEAD `befd5478a680ffa779a1e8ede736260d9bac1060`，没有提交或 Tag，没有负责人验收结论。协调方负责 `D:\RAG-v6-final-ingestion-pack-r1`；本任务未写该目录。可按独立新清单加入 mml-00 的完整61文件及3个交接元数据，保留 raw PDF 与历史产物可回读，再处理严格 pins 对另99缓存的消费限制。mml-01/02 仍为旧 freeze，明确 `NOT_REPARSED_NOT_REPINNED`。

请求模型/effort：继承的执行路由，精确值 `UNKNOWN`；configured/app-server/upstream actual identity 均 `UNKNOWN`，没有以自报或磁盘配置充当路由证明。可核查时间仅为 receipt 和验证 UTC；总会话耗时、tokens、成本、剩余额度 `UNKNOWN`。RAG provider/model调用、云端 VLM、DB、上传均为0；DeepSeek/Langfuse 未请求，既有7 calls / 855 tokens 及 disabled entry 保持未触碰。
