# 0434：MinerU 图片内部文字与页面标题传递修复

## 结论

**已修复图片内部文字遗漏及可信同页标题传递；跨页章节归属仍为 UNVERIFIED。**

这里有两个真实缺口：MinerU Basic 的版面路径跳过了 image 区域内部的文字识别；适配层又丢失了已识别标题的类型/层级，后续按纯文本切块把多个页面混成一个片段。不能因此给 Kaggle 片段手工补上 `Coding time!`。新增云调用0，旧数据库、Chunk、索引、Gold和UNKNOWN均未改写。

先更正上一报告：0434 的目标原件是 `catvsdogdlpycon15se-150512122612-lva1-app6891_95.pdf`，不是B3。上一份报告文字误写B3，现已明确更正；原始A/B inputs、Provider回执与分数未修改。

## 真实来源与根因

- 原PDF SHA256：`40a74d3fc5e73c81465206a4043c19b850daf66fc5617756f5894d25ad054b4b`。
- 历史doc：`4019d2aa-c21d-42c2-8e8f-4d5aee4039ce`；version：`03f5a072-4021-4aca-9233-2dca910614f2`；KB：`7f1d720d-9561-446f-a8cc-6a562c339d23`。原件68页、无PDF outline，第33页原生文本为空。
- 旧SDK第33页 `/pages/32/blocks/0/content/0` 是空 `image_body`；标题在图片像素里，SDK middle/structured/model_output中没有该字符串。第34页确有 `doc_title level1` 的Kaggle标题，第35页有竞赛文字。历史Tesseract回执不作为本次MinerU结果。
- 安装版本的 `mineru.backend.analysis.pdf.ocr._collect_ocr_det_crops` 只处理指定版面类型，其他类型被跳过。单独使用**同款 MinerU PP-OCRv6 ONNX** 检测识别，能读出该标题。因此本例存在版面覆盖缺口，不能认定为识别器不认识这些字。
- 旧目标chunk `085643dc-5ebb-4b8a-b7f1-75427da40015` 的范围覆盖24、25、26、27、28、30、33、34、35页，且header为空。直接给整个旧chunk挂一个标题会错误扩大标题作用域。

## 实际实现

1. `ingestion/mineru_image_ocr.py`：仅对尚无文字的SDK `image_body`，复用已有MinerU本地ONNX文字模型。已有文字不重复OCR；不调用Caption/VLM，不启用Tesseract，不新增模型或依赖。保存逐行文字、置信分、crop像素多边形及文本偏移；单次最多256个image body、每图最多4000万像素，遗漏项记录原因。
2. `mineru_runner.py`：在已有网络全拒绝的隔离进程中接入该步骤。保留SDK `middle_json.json` 原始字节，追加 `image_ocr.json`。适配层产生明确标记的 `rag_middle_json.json` 派生投影；原SDK文件及SHA始终可回查，不把应用补充伪称SDK原始输出。
3. `mineru.py`：将**SDK实际标记**的顶层doc_title/paragraph_title及其level、位置、source/version传给现成sections结构。OCR图片文字只保留为图片派生正文，不猜成章节标题。原文不插入伪Markdown；位置与派生正文hash匹配，公开来源回读标明 `local_mineru_image_text` 及sidecar身份。
4. `ingestion/sections.py`、`core.py`：按已证明的页面/标题边界复用原split和Parent/Child流程。同页标题传给context_header，禁止跨页继承；无标题内容仍保留。校验源SHA、版本、标题原文、block路径、bbox和middle引用，避免一个chunk横跨多个标题后错误绑定。存储层原本已支持header，本轮不改Schema。

所有改动用于新上传/新解析版本；历史冻结版本没有重切、重嵌入或覆盖。没有修改Rerank阈值、Prompt、模型或路由，也没有实现桥接、实体匹配、LLM跨片段关系验证或二次检索。

## 验证结果

| 验证 | 实际结果 |
|---|---|
| 原PNG经未修复的完整MinerU路径 | 0字符，只有image/image_body；保留首次证据 |
| 同款本地ONNX识别原PNG | `Coding time!`，置信分0.999909；不是云模型结果 |
| 修复后的正常隔离runner处理该PNG | 图片文字进入派生正文，SDK原字节保留；不是HTTP上传验收 |
| 原始PDF物理33–35页本地有界解析 | exit0，使用原PDF字节/物理page_idx32–34；标题实际置信分 **0.9999607205** |
| 当前适配器/Chunk/来源回读 | 5个新诊断chunk；Kaggle片段只属物理第34页，header为真实Kaggle标题；逐字quote/绝对Unicode偏移及Parent/Child引用校验通过 |
| 旧完整68页SDK回执经新适配器离线重放 | 78个真实SDK页面标题形成120个chunk；目标原文仅第34页。不执行新OCR，不假称真实索引已更新 |
| `Coding time! → Kaggle` 跨页章节关系 | **UNVERIFIED**。PDF无大纲，保存结构无父章节指针；相邻页和相同画风不足以证明章节归属。没有将该标题塞入Kaggle ContextHeader |
| HTTP/向量索引/Rerank新分数/最终答案 | **NOT RUN**。原有限云许可已关闭，本轮没有新增发送授权 |

详细文字与坐标见同名results.json。新OCR也包含图内识别噪声，例如`000`等；保留原始识别结果，不将其解释成完整图示语义。正确识别标题不等于已经恢复语义章节树。

## 执行命令与退出码

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest --noconftest -p no:cacheprovider -q --tb=short tests/weknora_port/test_mineru_image_ocr.py tests/weknora_port/test_mineru_sections.py tests/weknora_port/test_mineru.py tests/weknora_port/test_chunk_storage.py tests/weknora_port/test_m34_mineru_routing.py tests/weknora_port/test_rerank_model_passage.py
```

**exit0，74 passed，5个既有PyMuPDF弃用警告。** 覆盖重复OCR、篡改sidecar、来源回读、Unicode偏移、跨页/跨标题/缺页、caption非标题、Parent/Child、原生文字PDF路由、header索引与正文分离及Rerank输入合同。

实际本地有界PDF解析的子进程命令如下；所用env由原`local_environment`装配，仅本地模型和D/E缓存，socket连接全部拒绝：

```powershell
& 'E:\WK\mineru-env\Scripts\python.exe' -I -B 'E:\WK\run\s1-0434-section-20261019\pdf_page_probe.py' 'E:\WK\run\p11-real-20261016\B\assets\332\7f1d720d-9561-446f-a8cc-6a562c339d23\4019d2aa-c21d-42c2-8e8f-4d5aee4039ce\03f5a072-4021-4aca-9233-2dca910614f2\original\catvsdogdlpycon15se-150512122612-lva1-app6891_95.pdf' 'E:\WK\run\s1-0434-section-20261019\pdf-pages-33-35-final' 'E:\RAG-Worktrees\weknora-python-port-20261013\backend\weknora_port\ingestion\mineru_image_ocr.py'
& 'E:\RAG quention\.venv\Scripts\python.exe' -B 'E:\WK\run\s1-0434-section-20261019\finalize_evidence.py'
```

两条均 **exit0**。第一条是原PDF的3页本地解析诊断，不是正式上传的页筛选生产路径；没有恢复已撤销的image_pages机制。第二条只读回放解析产物并核对来源，不发送模型、不访问数据库。

首次工具错误保留于证据ZIP：cv2假设不存在、OCR接口实际需bytes/array、-I排除脚本目录以及局部import遮蔽、Windows诊断脚本缺multiprocessing guard、审计器误要求所有小child必有parent。已针对原因修正，未增加依赖；最终结果只使用成功版本。尝试单独收集旧test_ingest.py时缺python-pptx，标BLOCKED，不扩大Office任务。

## 范围与剩余事项

- 本轮 **0云Provider、0新增云Token、0数据库/索引写入**。没有新增UNKNOWN，也没有修改、释放或重发旧UNKNOWN。未启动API、Worker或容器。
- 已修复功能需要通过正常新版本解析入库后，才能改变当前用户的向量检索结果。旧KB中已有52个chunk仍保留；不能把本地5个诊断chunk当成已入库。
- 真实跨页“Coding time章节”仍缺明确结构证据。本轮不以相邻页自动继承来制造关联。需要区分后续的章节结构能力与一次合法的新版本入库/检索验证；不能先认定回答已正确。
- 本地提交本轮相关文件；保留原有未提交路径。不Push、部署、合并或打Tag。

完整证据：`docs/evaluation/evidence/s1-0434-section-20261019/evidence.zip`，逐项SHA见manifest.json；实际运行目录 `E:\WK\run\s1-0434-section-20261019`。
