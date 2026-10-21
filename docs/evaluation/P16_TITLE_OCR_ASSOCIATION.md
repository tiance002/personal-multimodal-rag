# P16 — 第33页图片标题真实OCR与章节关联验证

## 1. 结论与范围

**P16_COMPLETE_STOP_NO_RELIABLE_SECTION_LINK。** 现有本地OCR真实识别出`Coding time!`，但没有取得该标题与数据集证据的可靠章节归属，按Owner条件停止，**Rerank NOT RUN**。

源码基线/执行 `5bc48ba149f7212c7e89cb9621bbd1103dac2b2a`。复用生产`TesseractOCR.recognize`，不重建OCR实现、不修改生产模块。不人工补标题、不读取Gold、不凭邻页推断章节，不修改阈值/Prompt/路由，不接入生产。只新增限定验证脚本、报告与progress记录。

## 2. 一次真实本地OCR

- 入口：`backend.weknora_port.tesseract_ocr.TesseractOCR.recognize`，与`image_multimodal`实际调用同一适配器。
- 实际依赖：PyMuPDF1.26.4、Pillow12.3.0。不是外部tesseract.exe命令，而是PyMuPDF内置Tesseract接口`page.get_textpage_ocr`。
- 原有语言数据：`E:/WK/deps/tessdata/eng.traineddata`及`chi_sim.traineddata`，SHA256见results.json；不下载/安装依赖。显式复用该路径，没有修改全局环境配置。
- 原有参数：`eng+chi_sim`、dpi150、full=True；输入原始JPEG字节1753×1277，不调OCR参数、不放大/二值化/旋转、不重新裁图、不注入文字。
- 实际调用1次，耗时约0.455秒。error为空，输出9行。供应商调用0。

完整原始识别结果（噪声原样保留）：

```text
a
Gj
Coding time!
ies
Syt,
REx
ab
our ingredients...
ad
```

`Coding time!`为上述实际OCR输出的第3行，字符区间`[5,17)`；不是从P13人工标题或问题中复制进结果。现有适配器没有输出置信度、标题类别或章节树，因此不编造置信度或宣称整页OCR质量通过。

## 3. 来源定位与精度边界

- KB：`7f1d720d-9561-446f-a8cc-6a562c339d23`。
- 文档：`4019d2aa-c21d-42c2-8e8f-4d5aee4039ce`；版本：`03f5a072-4021-4aca-9233-2dca910614f2`。
- 原PDF SHA256：`40a74d3fc5e73c81465206a4043c19b850daf66fc5617756f5894d25ad054b4b`。
- 物理第33页（page_idx32），原middle指针：`/pages/32/blocks/0/content/0`；逻辑资产`images/page_32_image_0.jpg`。
- 原图片SHA256：`3a48bfaa8438f3b680ab11200f15b027a430552fcf9d96136d4d258c61cc17a0`。
- 原middle SHA256：`968553ee60df473d352a53b993d3e9a40a65f09a0f1eecb12df008cee1969085`。
- 标题在原图片像素坐标（左上原点）：约`[1095.305,20.473,1737.133,240.778]`。
- 使用原有MinerU图片bbox `[.113,.070,.934,.868]`投影到768×576点页面后，约为`[480.749,47.689,711.605,126.986]`点。

图片内坐标来自本次OCR TextPage；整页投影依赖原MinerU已舍入的bbox，**仅为近似定位，不能称PDF像素级精确坐标**。9行全部通过文字区间逐字回读与图片范围校验。完整映射、原始小数、输入路径、参数/依赖/训练数据哈希见results.json及`E:/WK/run/p16-20261016/ocr-page33.json`。

新OCR结果只保存在本任务证据，未修改原MinerU结果、原版本资产、索引或文档状态，未把派生OCR冒称原MinerU已有文字。该验证直接调用生产OCR适配器，不等于正式上传/后台任务/RAG闭环验收。

## 4. 为什么不进入Rerank

取得可回查的标题文字，只满足识字条件。现有OCR仅返回文字和行位置，不提供标题层级、所属section、章节结束位置或与后续证据的parent关系。P15已保存输出的完整核查仍适用：数据集文字位于第34/35页，缺少第33页标题到该证据的可靠机器章节关联。

同PDF、同版本、页码相邻、物理位置在上方，都不能替代章节归属证据。禁止用这些线索将标题直接拼接给数据集Chunk。本轮没有构造Rerank payload、没有打开Token围栏、没有给出新Rerank分数。

独立只读复核还检查了原PDF显式结构：68页、TOC为空、page labels为空，catalog仅包含Type/Pages，没有Outlines或StructTreeRoot，不能由书签/结构树补足关联。该检查exit0；执行的Python stdin原文和stdout分别记录在`E:/WK/run/p16-20261016/pdf-review-executed-stdin.py`、`pdf-review.stdout.txt`，环境/调用方式及两个只读import配置失败见`association-review.json`。Root没有重复执行该检查。

这说明原本地OCR可补出MinerU图片中遗漏的一行文字；**尚未证明它能够恢复章节关系或改善检索**。不自动启用生产OCR补强/章节增强。

## 5. 实际命令与保护

- `& 'E:/RAG quention/.venv/Scripts/python.exe' -B scripts/weknora_port_p16_title_ocr.py --output E:/WK/run/p16-20261016/ocr-page33.json` — exit **0**，日志`E:\WK\run\p16-20261016\ocr-page33.log`。
- `& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p16-20261016/verify.py` — exit **0**，日志`E:\WK\run\p16-20261016\verify.log`。
- `& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p16-20261016/ledger-audit.py` — exit **0**，日志`E:\WK\run\p16-20261016\ledger-audit.log`。

运行使用原Python、仓库/E:/WK/deps/reader/runtime-deps PYTHONPATH；TEMP/TMP位于E:/WK/run/p16-20261016/tmp。依赖/图片哈希预检exit0；实际OCR、定位/历史文件复核、只读账本核对均exit0。未运行无关全量回归。**未实现的命令不得报告通过。**

只读PG身份：weknora_python_port_20261013，127.0.0.1:56423，system_identifier7696088403504697382。SiliconFlow618条记录/61条UNKNOWN/reserved0、月度预算和DeepSeek记录均与P14关闭快照逐值相同。**本轮新增云端Token0、新增UNKNOWN0**，没有释放历史UNKNOWN，没有修改meta.tokens兼容。

567个生产文件、378个继承保护文件、P14证据、P15原件与结构化输入哈希不变。未启动API/Worker/浏览器/容器，未写数据库、未访问Redis。原.serena改动和六个未跟踪文件保留；无迁移/依赖/共享接口变更。

## 6. 结束

真实OCR验证完成；章节关联条件未满足，因此按Owner停止条件结束，不为获得理想结果继续OCR/Rerank或扩大开发。未确认生产代码Bug，不作生产修复。不Push、不部署、不合并、不打Tag。后续若再推进，需要独立、可回查的章节关联证据，本轮不自动实施。
