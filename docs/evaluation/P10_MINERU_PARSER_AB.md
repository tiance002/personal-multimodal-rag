# P10 — MinerU 4.0 实际解析 A/B 与最小接入

日期：2026-10-16。结论：**A：显式可选 Basic 最小接入完成，限定离线验收 PASS。**

MinerU 4.0.12 已独立安装并实际运行。扫描 PDF 的原默认路径只有图片占位，Basic 能恢复可引用文字；Omni 相同 3 页有效全文 NED 均值从 **0.4367953 降至 0.1063907**。收益足以支持扫描件/图片的可选接入。普通文字 PDF 上发现碎裂及图表数值未恢复，故不扩大替换范围。

**未执行的命令不得报告通过。** HTTP 上传、数据库持久化、向量索引、Embedding、Rerank、Cheap、DeepSeek、真实 RAG A/B 均 **NOT_RUN**。本轮供应商请求、账本修改和数据库调用为 0；本地 Standard VLM 推理单列。没有 Push、部署、合并或 Tag。

## 1. 源码、样本及证据身份

- 工作树：`E:/RAG-Worktrees/weknora-python-port-20261013`。
- 分支：`codex/weknora-python-port-integrate-image-20261014`。
- 接手 SHA：`5e2b4fc83d2b816a70b53e6bf6f560890d8bf2ec`。
- 实现 SHA：`24e9782e445617f005fa8180a71a3a51ca33c09e`。本报告的提交位于其后，不改变实现。
- 机器结果：[P10_MINERU_PARSER_AB.results.json](P10_MINERU_PARSER_AB.results.json)。包含实际源文件 SHA、每案时间/页号/资产/类型、全部退出码索引和 8 QID 审计。
- 原始证据根：`E:/WK/run/p10-mineru`；`evidence-manifest.json` 对 1,412 个本地证据文件记录 SHA/大小。原始模型文件的 SHA 单列于 `analysis-all-runs.json`。
- P8/P8.1/P9 冻结结果、Gold、历史证据、接手时 `.serena/project.yml` 与六个未跟踪文件：`protected-before.json` → `protected-after.json` **376/376 字节哈希不变**。没有实时查询或修改供应商账本，不能把这项文件审计称为实时数据库账本审计。

在读取 MinerU 输出之前冻结原件与关键页：

| 原件 | 页数 | SHA-256 | 首轮 Basic 实际物理页（1 起） |
|---|---:|---|---|
| Pew `11-21-16-Updated-Post-Election-Release.pdf` | 45 | `8f49f93f1665fd5ff604e66ffed9649d8b18ce7f114ab43463aeac46cd3d00cb` | 1,10,11,28,29,30 |
| cat `catvsdogdlpycon15se-150512122612-lva1-app6891_95.pdf` | 68 | `40a74d3fc5e73c81465206a4043c19b850daf66fc5617756f5894d25ad054b4b` | 1,29–35 |
| b3 `b3m5kaeqm2w8n4bwcesw-140602121350-phpapp02_95.pdf` | 21 | `43be9592854c7da10bf011335faec017b726ff2e23bb4ccae7e8fe88d7801f22` | 1,9,10,17–19 |

三原件分别为 D 盘已核验来源目录中的 object-00/01/02.pdf；Pew 同时是普通文字 PDF 对照。Omni 使用原有 24 张图片及 P9 Gold，原件哈希与来源身份 24/24 对应。没有下载新数据集、改题或把 Gold 传给 MinerU。

清单：`frozen/mml-pdfs-and-qid-pages.json`、`omni24-original-pages-and-gold.json`、`normal-text-pdf-control.json`、`basic-case-plan.json`、`metric-protocol.json`。MML 原标注页码基数未知，因此关键页同时覆盖标注值和 +1；报告的 page_idx 均是实际输入的零起物理页，不改 Gold 页码含义。

原 Omni 冻结清单将 package manifest 哈希误写为 ZIP 哈希。保留原文件，新增 `frozen/provenance-correction.json`：ZIP 内 manifest.json 的正确 SHA 为 `369c1b56f83fbf7f444207f61c23477c94ec82ee16b31e2cb9aa8a11225f4cee`，122,431 字节；ZIP 哈希仍为 `111d1e8eff5fa7e743fec9adf72b67538981fadd72fac303a43c344d3f2d1c06`。该更正没有改变选样。

## 2. 独立环境、模型与许可证

| 项目 | 实测 |
|---|---|
| 系统/CPU | Windows 11 10.0.26200；i7-13650HX，14 核 |
| 内存 | 33,271,672 KiB 物理内存，预检空闲 17,248,580 KiB |
| 显卡 | RTX 4060 Laptop，8,188 MiB；driver 581.80，预检空闲 7,760 MiB |
| 预检空闲盘 | D 128,389,984,256；E 59,045,511,168 字节 |
| 独立环境 | `E:/WK/mineru-env`，Python 3.12.7 |
| 实际包 | mineru 4.0.12；docvortex 0.5.16；onnxruntime 1.31.0；mineru-llama-cpp 0.1.3；mineru-vl-utils 2.0.7 |
| 缓存/输出 | 全部新下载、模型、缓存、临时文件与测试日志位于 D/E；配置 `E:/WK/run/p10-mineru/env.ps1`、`config.yaml` |
| RAG 环境 | Python 3.13.0；PyMuPDF 1.26.4、Pillow 12.3.0；未安装 MinerU，未改依赖锁或增加项目运行依赖 |

Basic 使用本地 ONNX CPU；Standard 使用本地 llama-cpp GGUF，实际加载 Vulkan DLL，在 NVIDIA 上观察到推理。官方本地模型选择机制对应：

- `opendatalab/MinerU-4_models_onnx`，revision `358310b4f64b95f9fefc372ad899356e4111f376`。
- `jinzhenj/MinerU2.5-Pro-2605-1.2B-GGUF`，revision `9185688a0495e1577d521a757c7c0b62dd38ca48`；这是 4.0.12 注册表默认指向的第三方量化仓库，不冒称全部权重由官方直接发布。
- 本地模型目录实际文件合计 **2,098,683,085 字节**；全量版本见 `installed.lock.txt`，模型逐文件哈希见 `analysis-all-runs.json`。下载与 verify 均 exit 0。

安装包 LICENSE 实测为 **Apache 2.0 加附加条款**，不是无限定 Apache 许可：集团月活超过 1 亿或月收入超过 2,000 万美元时要求单独商业许可；向第三方提供在线服务时须在 UI 或公开文档显著标识 MinerU。原文保留 `installed-MinerU-LICENSE.md`，来自安装包 dist-info/licenses。权重仓库抓取元数据没有明确 license 声明，未据此推断权重可无限商业使用或再分发。

参考实际核对的官方说明：[模型来源](https://opendatalab.github.io/MinerU/usage/model_source/)、[快速开始](https://opendatalab.github.io/MinerU/quick_start/)、[许可证](https://raw.githubusercontent.com/opendatalab/MinerU/master/LICENSE.md)、[输出文件](https://github.com/opendatalab/MinerU/blob/master/docs/en/reference/output_files.md)。命令以已安装版本 `--help` 为准。

## 3. 实际解析范围和对照

| 路径/模式 | 执行范围 | 结果 |
|---|---|---|
| 原 PDF ingestion | 同一三份完整 PDF：45/68/21 页 | 全部完成；Pew 70,220 字符/162 chunks；cat 9,092 字符/23 chunks、b3 2,668 字符/6 chunks，后两者是图片占位，不能当真实 OCR 文字 |
| 独立旧 Tesseract | 相同 20 个关键 PDF 页面，150 dpi，eng+chi_sim | 20/20 无错误，16,668 字符；独立 OCR 也能找回有关文字，不能称 MinerU 独有识字能力；原默认 PDF 不会自动走此路径 |
| Flash | Pew 全 45 页，txt | 成功，保留完整原始 zip |
| Basic 首轮 | 三 PDF 20 个关键页 + Omni24 张，共 27 案/44 次页面 | 27/27 schema/producer 成功 |
| Basic 扩展 | 首轮耗时可接受后，仅将既有 cat68、b3 21 扫描页完整解析 | 89 页完成；没有升级成全量数据集或长时间 VLM 测试 |
| Standard | Pew 物理10/11页 auto/txt；b3物理9页；Omni公式页；Pew相同两页强制ocr复核 | 4 案/6 次页面，4 个不同页面；全部成功 |
| Advanced | 未运行 | NOT_RUN |

Basic 合计覆盖 **119 个不同页面**（89 扫描 +6 原生文字 +24 图片），133 次页面执行；关键扫描页与完整扫描重复，不能把 133 当不同页面。适配器另有一次真实本地 Omni 冒烟，未混入 A/B 分母。

真实 `ParseResult.to_dict()` 和官方 zip 中 `middle_json.json` 均核验 `schema=docvortex.middle`、`schema_version=2.0`、producer mineru/4.0.12。保存 SDK JSON、portable JSON、Markdown、structured_content、model_output 和图片资产；未使用旧 pdf_info/_backend 协议，也未拿 CLI 响应充当中间 JSON。

首轮 Basic 顶层类型实测：paragraph_title46、text408、image43、chart9、header40、doc_title18、table6、footer20、page_number19、equation13。这只证明结构实际存在，不证明分类/版面正确。保留 HTML 表格、公式字符串、标题、bbox 与原数组顺序；没有充分 Gold 对齐的行列/单元格、公式语义、阅读顺序及图表数值不报官方分数。图表 body 为空时仍为空，仅保存真实裁图；不生成假 Caption。

Standard 的第一份 Pew auto 实际走 txt，没有 VLM Predict，不能将它称为视觉推理成功。随后强制 OCR 的同页重跑有真实 VLM Predict；b3 和公式也实际执行本地 VLM。Standard 没有恢复目标图表数值、反射图所需全部证据，公式 literal NED 还差于 Basic，因此不接入 Standard。cat full 在 page_idx52 有一次规范化丢弃 block 警告，日志保留，不称无损完整识别。

## 4. Omni 质量：保持原 3/24 分母

使用 P9 原 `_ned` 和同一 Gold，原始 block 数组顺序递归提取 content，行间 LF；不清洗、重排、剥离公式/表格、挑选更优结果。规则已在输出前冻结。

| 有效页 | P9 Tesseract NED | Basic NED | 观察 |
|---|---:|---:|---|
| Omni06 yanbao PPT4185 | 0.6021505 | 0.0537634 | 改善 |
| Omni10 Changes English | 0.1200000 | 0.1333333 | 退化，保留 |
| Omni14 lecture1公式 | 0.5882353 | 0.1320755 | 改善 |
| **均值，3 页** | **0.4367953** | **0.1063907** | **绝对下降0.3304045，相对约75.64%** |

Standard 仅额外评了公式这一页：NED **0.2515723**，差于 Basic 0.1320755；不能与三页均值混比。其余 21 页没有可靠全文 Gold 顺序，仍 NOT_RUN，不补造标注。此结果不是官方 Omni 总分、版面总分或 QA 成绩。

核对 P9 原始图片任务后发现：24 页任务均收口，但真实 OCR 为 **21 页成功且非空、3 页 OCR 失败**，不能写成 24 页 OCR 全成功。三页是 Omni05/09/13，错误 `OCR_HTML_SOURCE_CONVERTER_REQUIRED`；其 Tesseract layout 保留，但旧转换边界没生成 ocr_result。P10 24 页 Basic 都有真实结果，改善中这部分属于避免原转换阻断，不能全算 OCR 算法提升。补充审计见 `native/omni-p9-native-ocr-sidecar.json`、`omni-p9-existing-reference.v2.json`，原版保留，P9 冻结文件不改。

## 5. MML：证据恢复与能力限制分开

| QID | Basic 证据结论 | Standard | 本轮 QA |
|---|---|---|---|
| row-0430 | 动物计数仍未建立；保存图片不等于正确数虎 | 未跑该题页 | NOT_RUN |
| row-0431 | 神经网络人脸图层数仍需视觉解释 | 未跑该题页 | NOT_RUN |
| **row-0434** | **足够文本证据：cat page_idx33/block0，`Kaggle's Cat vs Dog dataset (25k dog/cat pictures)`** | 未跑该题页 | NOT_RUN |
| row-0684 | 男女比例所需图表数值仍未恢复；“无显著差异”的叙述不足 | 未解决 | NOT_RUN |
| row-0685 | 缺图表字段不能据此证明不可回答，Gold不可回答尚未被证据审计确认 | 未解决 | NOT_RUN |
| row-0687 | 原文字 PDF 证据早已可用；P9 的均值/中位数回答问题未处理 | 未跑该题页 | NOT_RUN |
| row-0958 | 提取到 shiny surface 仍不足以完整支持 shiny object/mirror；图像未被可靠解释 | 反射页实际推理，仍未解决 | NOT_RUN |
| **row-0959** | **足够文本证据：b3 page_idx16/block3，ionosphere refracts radiowaves；microwaves…satellites** | 未跑该题页 | NOT_RUN |

七个此前受限 QID 中，**2/7 现在具备足够解析证据**；这是“可开展下一轮 RAG 评价”，不是模型回答 PASS。完整逐题 quote、bbox、来源 SHA 和中间 JSON SHA 见 `mml-evidence-audit.json` 及机器结果。独立旧 Tesseract 也可恢复这些文字，MinerU 的收益包含将扫描 PDF 接到统一可引用结构中，而非声称旧引擎完全无法识字。

## 6. 资源、耗时与退化

| 执行组 | 实测时间 | 最大 SDK 主进程峰值工作集 |
|---|---:|---:|
| Flash45页 | 日志 inference8.170385秒；未单独测墙钟 | 未采集 |
| Basic27案/44页次 | 案例墙钟之和558.51秒 | 2,662,858,752 bytes，约2.48GiB |
| Basic cat完整68页 | 82.76秒 | 4,407,590,912 bytes，约4.10GiB |
| Basic b3完整21页 | 46.77秒 | 3,189,342,208 bytes，约2.97GiB |
| Standard4案/6页次 | 案例墙钟之和91.52秒 | 1,840,705,536 bytes，约1.71GiB |

时间为每案实测，不是并行时系统总墙钟；首次失败与安装/下载不在成功解析耗时分母内。Standard GPU 抽样峰值2,104MiB、利用率80%，见 `standard-gpu-samples.csv`；抽样不保证捕获瞬时峰值。SDK 进程内存不是所有渲染子进程/整机 RAM 合计。模型增加约2.10GB十进制磁盘占用，另有独立环境/缓存；未测电费，不估算人民币。

普通 PDF：Basic 对 Pew 的自然文字存在碎裂，chart body 空，不能全面替换。**原默认 PDF 路径实际回归 PASS**：45页/70,220字符/162chunks，Unicode正文与接入前逐字相等，约0.95秒。首次检查误把 Windows CRLF 文件哈希与 LF 内存哈希比较，exit1 已留存；v2 分别验证文件字节 SHA 和完全相同 Unicode 文本，exit0。没有通过改原基线或放松文字相等来掩盖问题。

## 7. 最小接入与来源合同

直接复用 `backend.app.domain.models.NormalizedDocument` 纯 DTO；WeKnora 主链实际接口为 DocumentRequest→ParsedDocument→ChunkRecord，新适配器桥接两者，不调用旧 app 服务或旧 chunker。

| 文件 | 最小职责 |
|---|---|
| `backend/weknora_port/ingestion/mineru.py` | 独立配置/子进程调用、扫描/单帧预检、zip/schema/版本/页覆盖/资产校验、NormalizedDocument桥接、locator校验 |
| `backend/weknora_port/ingestion/mineru_runner.py` | 独立Python本地Basic/ocr、关闭图片语义描述、最小环境、不继承凭证、拒绝socket连接 |
| `backend/weknora_port/ingestion/core.py` | 注册显式parser；固定splitter完成后附加locator交集；显式失败不回退 |
| `backend/weknora_port/documents.py` | 上传parser选择、资产持久化接线、parser_evidence不重复送旧OCR/Caption、原文回读附带校验后的定位 |
| `backend/weknora_port/development.py` | 正常应用装配显式配置，不更改默认parser |
| `pyproject.toml` | 打包既有纯domain DTO，不加MinerU依赖；旧app服务仍不入wheel |
| `scripts/weknora_port_mineru_offline.py` | 保存输出重放/显式真实本地冒烟、切块和引用核验、资产独立落盘 |
| `tests/weknora_port/test_mineru.py`、`test_development_entry.py` | 来源/篡改/异常/分流/默认路径反例及打包契约 |

必要来源字段保留：原件 SHA、document_id/source_version、producer/版本、实际 page_idx、block 类型/index/JSON pointer/bbox、原始结构化 JSON 的 SHA 和图片资产。NormalizedDocument.content_sha256 为**派生文本 UTF-8 SHA**，与原件 SHA 分开保存。offset 是**派生 Unicode codepoints**，不是 PDF 原始字节偏移。SourceLocator 和 chunk metadata 分别保存页/块/引用交集。

原件、sidecar、裁图均走既有资产 manifest；引用回读核对 document/version、原件 SHA、派生文本范围/quote、受信解析映射与资产哈希。此校验不是重新 OCR 或证明 PDF 字节偏移；不伪造空图表文字。保留 tenant/KB/document/version 既有范围校验。空正文资产可保留，但文档以 DOCUMENT_CONTENT_UNREADABLE 失败收口，不能报成功。

配置默认关闭。建议图片先用以下显式配置：

```text
WEKNORA_PORT_MINERU_ENABLED=1
WEKNORA_PORT_MINERU_EXTENSIONS=.png,.jpg,.jpeg
WEKNORA_PORT_MINERU_PYTHON=E:/WK/mineru-env/Scripts/python.exe
WEKNORA_PORT_MINERU_MODEL_ROOT=E:/WK/run/p10-mineru/models
WEKNORA_PORT_MINERU_TEMP_ROOT=E:/WK/run/p10-mineru/tmp/runtime
```

仅在明确处理扫描 PDF 的隔离配置中追加 `.pdf`。本实现拒绝原生/混合文字 PDF（MINERU_NATIVE_PDF_UNSUPPORTED），也拒绝多帧图片。扩展名选择对该应用实例上传生效，没有新增前端选择器或自动检测路由；将 `.pdf` 全局交给新parser会令文字PDF明确失败，故不推荐默认加入。显式配置失效会清楚报错，不静默回退。

本地parser完成的资产跳过旧云OCR/Caption，避免重复请求；本地OCR不要求打开云多模态开关。模型调用权限/预算、检索、Prompt、回答动态路由均未改。没有 DB 迁移、依赖锁或模型注册系统变更。

共享文件为 core.py/documents.py/development.py/pyproject.toml 及原测试打包断言；与专属模块同一实现提交交付，未合并到其他分支。协调顺序：确认独立环境/模型 → 整合专属adapter及DTO打包 → 整合core/documents来源映射 → development显式装配 → 定向测试。此说明不是部署指令。

## 8. 实际命令、退出码及证据

以下路径 `E:/WK/run/p10-mineru` 简记 `$P`；实际命令通过同目录 env.ps1 把缓存/TEMP 指向 E，项目命令 cwd 为上述工作树。完整逐案 argv 在 basic-fixed-batch-checkpoint.json；退出码逐一列在机器结果 command_exit_receipts。

| 实际执行命令/组 | exit | 证据 |
|---|---:|---|
| `D:/Drivers/anaconda/Scripts/uv.exe venv --python D:/Drivers/anaconda/python.exe E:/WK/mineru-env` | 0 | venv.log |
| `uv.exe pip install --python E:/WK/mineru-env/Scripts/python.exe mineru==4.0.12` | 0 | install.log、installed.lock.txt |
| `E:/WK/mineru-env/Scripts/mineru.exe version --json` | 0 | version.log |
| `mineru-kit models download --tier basic --small-backend onnx --source huggingface`，同参数verify（去source） | 各0 | models-basic-download/verify.log |
| `mineru-kit models download --tier standard --small-backend onnx --vlm-engine llama-cpp --source huggingface`，同参数verify（去source） | 各0 | models-standard-download/verify.log |
| `mineru-kit parse D:/RAG-v6-special-subsets-r1/download-verification-attempt1/source-files/object-00.pdf --output E:/WK/run/p10-mineru/outputs/flash/pew.zip --format zip --tier flash --ocr-mode txt` | 0 | flash-pew.log |
| `E:/WK/mineru-env/Scripts/python.exe -B E:/WK/run/p10-mineru/run_batch.py basic basic-fixed` | 0，27案各0 | basic-fixed-batch.log、逐案log/receipt |
| `python.exe -B E:/WK/run/p10-mineru/parse_case.py basic full-cat-key-pages basic-full`；同命令full-b3-key-pages | 各0 | basic-full-*.log、outputs/basic-full |
| `python.exe -B E:/WK/run/p10-mineru/parse_case.py standard pew-chart-one standard`，同命令 b3-reflection-one、omni-formula、pew-chart-forced-ocr | 各0 | standard-*.log、outputs/standard |
| RAG Python `-B E:/WK/run/p10-mineru/native_compare.py` | 0 | native/native-compare.log |
| MinerU Python `-B E:/WK/run/p10-mineru/analyze_all_runs.py` | 0 | analysis-all-runs.log/.json |
| RAG Python `-B E:/WK/run/p10-mineru/native_after_regression_v2.py` | 0 | native-after-regression-v2.log/.json；首次exit1保留 |
| MinerU Python `-B E:/WK/run/p10-mineru/finalize_report.py` | 0 | finalization.log、protected-after.json、evidence-manifest.json |

实际最终定向测试（RAG Python 为 `E:/RAG quention/.venv/Scripts/python.exe`，PYTHONPATH 包含本工作树、`E:/WK/deps/reader`、本树 var/weknora-port/runtime-deps；独立 native_compare.py 使用 `weknora_port` 导入，另以本树 `backend` 目录作为 PYTHONPATH）：

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m pytest tests/weknora_port/test_mineru.py tests/weknora_port/test_documents.py tests/weknora_port/test_development_entry.py -q --tb=short -p no:cacheprovider --basetemp 'E:/WK/run/p10-mineru/adapter-tests/pytest-closure'
```

**exit0，49 passed**，adapter-tests/pytest-closure.stdout.txt。之前包含旧 test_ingest.py 的组 **44 passed，exit0**；有重叠不相加。包含隔离反例、错误SHA/schema/版本/bbox/页覆盖/zip路径、派生文本hash、原生PDF拒绝、默认parser与空输出、旧云图片任务不被误触发等最小检查。

实际本地子进程冒烟：

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B scripts/weknora_port_mineru_offline.py --source 'D:/RAG-v6-special-subsets-r1/download-verification-attempt1/source-files/object-10.jpg' --live-local-basic --python 'E:/WK/mineru-env/Scripts/python.exe' --model-root 'E:/WK/run/p10-mineru/models' --temp-root 'E:/WK/run/p10-mineru/tmp' --output 'E:/WK/run/p10-mineru/adapter-tests/omni06-live.json'
```

**exit0**，1 chunk/90字符/2locators/5资产。该实时冒烟早于最终 NormalizedDocument.content_sha256 修正；最终源码使用同一真实保存输出和独立哈希断言完成 recheck，未为此重复模型推理。最终四样本 `--archive .../result.zip --receipt .../receipt.json --save-assets` 重放各 **exit0**：

| 最终源码重放真实保存输出 | 页 | 字符 | chunks | 资产 |
|---|---:|---:|---:|---:|
| cat完整扫描 | 68 | 21,530 | 52 | 73 |
| b3完整扫描 | 21 | 15,684 | 34 | 39 |
| Omni00，含实际表格结构 | 1 | 1,818 | 4 | 6 |
| Omni06 | 1 | 90 | 1 | 5 |

命令格式见脚本 --help；实际输入/receipt/结果/独立资产保留 adapter-tests/*-recheck.json 及同名.assets。每块与派生原文区间逐字比较，再经过正常 public_chunk_locators 校验。重放属于真实解析产物的离线适配验证，不冒称真实HTTP/PG/向量索引验收。

wheel 构建、内容检查、E盘外部cwd下 `-I` 隔离导入均 **exit0**：wheel SHA `8fe0f4b9288ef7cd6c506148dcc50fff8c6d5965ef46f1be159222c7d6e0c952`，old_app_modules=[]。仅打包现有纯domain，backend.app作为namespace，没有旧bootstrap/services/adapters运行代码。见 adapter-tests/wheel-content.json、wheel-import.stdout.txt。

## 9. 首次失败与收口

- 首轮 SDK helper 缺 Windows spawn main guard，子进程重复 mkdir，9次首轮失败观察均保存 outputs/basic 和 basic-*.log；仅修复测试驱动并在 basic-fixed 新目录运行，未擦除失败或算作质量成功。
- 首轮定向测试 reader路径缺失、fixture参数签名错误，以及 RAG venv 缺setuptools构建失败均保留。补已有reader路径/修正fixture，使用独立环境已有构建工具，不向RAG环境安装新依赖。
- 适配中发现 NormalizedDocument.content_sha256 曾误放原件SHA；改为派生文本SHA，新增独立反例并对四份真实输出复核。原件SHA仍独立保留。旧报告/首个失败不覆盖。
- 未运行整项目回归或六套LLM评测。所有自启解析进程已经结束，未停止共享服务、未改变Docker/PG/Redis。无待运行后台模型任务。
- 最终 Git 仅保留接手时 `.serena/project.yml` 与六个既有未跟踪文件；P10代码及报告独立本地提交。报告提交SHA以 `git log -1 -- docs/evaluation/P10_MINERU_PARSER_AB.md` 为准，避免文件自引用SHA。

## 10. 决策与停止

建议 **保留现有默认解析器，部分引入 Basic**：图片可显式选择；扫描PDF限定实例/配置使用，普通文字PDF保留旧路径。Standard暂不进入运行适配，Flash不替换稳定路径，Advanced不运行。

后续值得进行一次独立、有限的真实 RAG A/B：优先 row-0434/0959 与 Omni 的3个有效页，区分解析改善、检索是否找回、引用是否生成、答案是否正确。当前不运行，不扩展全语料，不处理路由、漏引用Prompt或模型推理质量问题。本轮限定实现与离线验收完成后停止。
