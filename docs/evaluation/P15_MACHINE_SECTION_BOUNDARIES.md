# P15 — 真实章节边界与标题关联验证

## 1. 目标与结论

**P15_COMPLETE_OFFLINE_STOP：离线结论明确，按Owner停止条件结束，Rerank NOT RUN。**

当前保存的MinerU 4.0.12 Basic输出不足以可靠恢复row-0434问题要求的章节标题与正确证据的关联。不代表MinerU完全没有标题识别能力，也不代表所有文档都不可恢复章节；本结论限定于本次保存产物与指定关联。

基线 `064f1c459f8a77160ba624dfd2812308176b3a60`。只新增离线检查脚本、本报告/同名JSON和progress记录；无生产Bug确认，无生产修复、阈值/Prompt/路由/解析器/计量变更。使用已保存数据，不重新OCR、解析、上传或索引，不读取Gold、不人工补标题、不构造邻居章节。

## 2. 逐项机器证据

核查两份原始PDF及六份已保存结构化文件：两文档各middle_json、structured_content、model_output。文档/版本/原件SHA与P14冻结来源一致，全部路径与SHA256见JSON input_hashes；原件仅哈希校验，未转录图片标题。

| 检查 | 结果 |
|---|---|
| 0434所需Coding time机器标题 | 三格式均未命中；独立复核忽略大小写/空白仍未找到 |
| 第33页实体 | middle_json#/pages/32/blocks/0为image；content/0为image_body，content为空，image_path=images/page_32_image_0.jpg |
| 第33页可提取文字 | 只有image_footnote `(picture by Dato)`，不能作为章节标题 |
| 第34页数据集标题 | #/pages/33/blocks/0：doc_title、level1，内容为 `Kaggle's Cat vs Dog dataset (25k dog/cat pictures)` |
| 第35页数据集证据 | #/pages/34/blocks/6 为数据集任务标题；blocks/12竞赛说明、blocks/16 Data Files、blocks/24–29文件/格式信息、blocks/31 Kaggle URL |
| 章节归属/边界 | 未见section/parent/outline关系字段，也没有第33页图片标题到第34/35页证据的机器归属；页相邻不构成章节关系 |

0434 middle_json/structured_content有9个doc_title(level1)、69个paragraph_title(level2)。标题类型、level与bbox可以定位已有标题，但不是已验证的真实章节树；多张投影片被分成同级标题。model_output中的唯一title键为`/metadata/document/title=null`，没有额外章节树可供补救。原始sidecars本身就缺少所需文字/关系，不能归因于现有应用适配时丢失了一个已存在的Coding time章节。

**正确证据本身仍存在。** 0434 anchor来自chunk17，Unicode[7276,7744)，覆盖多页与多个不同标题；并非一段已标定的真实章节。把“DL? How?”、数据集标题、前后页或相邻Chunk拼起来，再称为“Coding time章节”，会引入未经机器证据支持的关联，本轮不这样构造。

### 0959与负例对照

- 0959：第16页`#/pages/15/blocks/0`识别`P1g: Wireless Communication`为paragraph_title/level2；第17页`#/pages/16/blocks/3`保存长距离通信的电离层/微波证据，blocks/4有同名标题文本，但类型是普通text。页级位置与文字可回查，不能仅凭前一页标题就认定已有跨页章节树。该文档12个识别标题均为level2，没有显式父子关系；并非全无有用局部标题信号。
- 负例slot6：第49页`#/pages/48/blocks/0`有`Pylearn2: Multilayer Perceptron (MLP) on top of extracted features`，doc_title/level1；它有真实标题，但不是0434数据集答案。
- 负例slot24：第6页Caffe/Why Caffe?为paragraph_title/level2。
- 负例slot28：第67页`Wanna Play ? NLP`为paragraph_title/level2。

这些反例表明“有标题/有level”不足以证明与目标问题的章节关联；不能为了关联正确证据，将所有材料挂到邻近标题下。五个冻结候选的全部locator均按derived_block_start/结束位置回读原middle block的对应substring，包括0959跨Chunk截断的首片段；完整映射保存在results.json controls。

## 3. 真实模型与账本

**真实Rerank调用0；新增供应商Token0；新增UNKNOWN0；A/B分数/排名/过滤结果NOT RUN。** 未开新围栏、未调用Embedding/Chat/DeepSeek、未运行RAG服务。已有P14分数不当作本轮实验成绩。

只读PG核对：127.0.0.1:56423，数据库weknora_python_port_20261013，system_identifier7696088403504697382。当前SiliconFlow618条调用记录、61条UNKNOWN、reserved0；调用记录/月度预算与P14关闭快照逐值相同，DeepSeek账本亦逐值相同。全部历史UNKNOWN保留，不修meta.tokens兼容、不释放占用。无写库、无Redis访问、无新服务/容器。

P11—P14既有保护证据及P14结果引用的证据哈希复核不变；567个生产文件哈希不变。原.serena改动及六个既有未跟踪文件保留。没有迁移、依赖或共享生产接口变更。

## 4. 实际命令与状态

在工作树中执行：

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B scripts/weknora_port_p15_section_audit.py --output E:/WK/run/p15-20261016/section-audit.json
```

最终 **exit0**：`OFFLINE_AUDIT_PASS documents2 outputs6 mapped_candidates5 STOP_NO_RELIABLE_0434_SECTION RERANK_NOT_RUN`。日志`E:/WK/run/p15-20261016/section-audit-v3.log`及同名exitcode。第一次检查器Counter统计写法exit1；第二次将截断locator误当完整block比较而exit1；只修离线检查器，保存两版源码及失败日志，未修改数据/生产模块，也未产生模型请求。最终按真实原文substring严格核对，不放松来源校验。

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p15-20261016/ledger-audit.py
```

**exit0**，只读账本比较PASS；日志`ledger-audit.log`、机器结果`ledger-preservation.json`。PYTHONPATH沿用仓库、E:/WK/deps/reader和var/weknora-port/runtime-deps。

不运行无关回归；本轮新增的是离线检查器，其实际原始产物审查通过，不能称生产章节恢复测试通过。**未实现的命令不得报告通过。**

## 5. 风险、建议与停止

当前缺失的是所需图片标题的机器文本及可靠章节归属，不能靠纯标题level/相邻页自动补齐。没有充分依据启动本轮Rerank，更没有生产接入依据。若Owner以后决定继续，需单独验证标题识别/结构恢复能否提供可回查信号；本轮不扩大解析方案、不补OCR、不手工标章。

这不是需要延期批准的未完成调用：Owner明确要求无可靠机器关联即停止，本轮已满足该分支的完成标准。全部结论限定于当前产物，未取得章节增强的质量收益数据。完成后停止，无Push、部署、合并、Tag或自动优化。
