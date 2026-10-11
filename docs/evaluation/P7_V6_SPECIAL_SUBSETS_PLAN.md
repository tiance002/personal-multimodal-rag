# P7 V6 六数据集与专项子集文件计划

任务：`RAG-V6-SPECIAL-SUBSETS-PLAN rev1 attempt1`。状态：`NEEDS_OWNER_REVIEW` / `MANUALLY_SUPERVISED_TRIAL`。这是文件身份、公开来源和确定性抽样计划，未批准下载大文件、未作质量验收。

Owner 最新指示已取代旧 v1.6 限制：OmniDocBench 使用当前官方可获取快照并固定 revision；停止追查旧 v1.6 冲突。固定截止 `2026-10-10T07:44:02Z`。三主集 ECS 工作在另一线程，本计划不修改其文件、服务或状态。

## 1. 目标与改动：先锁定需要哪些文件

本轮仅新增本文件及 `var/reports/v6-special-subsets-r1/` 内独立计划、选择结果、探测脚本和证据；公开元数据下载保存到 `D:\RAG-v6-special-subsets-r1`。未改业务代码、原 frozen Gold/479 holdout、原报告、ECS/DB、模型/Embedding/解析/主评测。原九文件逐个重新计算完整 SHA-256，9/9 与 P7 第 5 节一致。

| 数据集 | 本次需要的来源/内容文件 | 规模与可获取性 | 当前状态 |
|---|---|---|---|
| SciFact | 完整 `corpus.jsonl`、`manifest.json`；共享 dev24 中该集8题；不上传 holdout | 5,183 候选文档；已有本机文件，无新增网络下载 | 本机身份核验；8题 dev，不是300 holdout |
| MIRACL-ZH | 完整 `corpus.jsonl`、`manifest.json`；共享 dev24 中该集8题；不上传 holdout | 6,000 候选文档，含显式 grade-zero 与未判定背景；无新增网络下载 | 本机身份核验；8题 dev，不是99 holdout |
| LongBench-ZH | 完整 `corpus.jsonl`、`manifest.json`；共享 dev24 中该集8题；不上传 holdout | 400 候选文档；无新增网络下载 | 本机身份核验；8题 dev，不是80 holdout；检索 qrels 为诊断用途 |
| MMLongBench-Doc | 下列3个完整 PDF + 固定问题 parquet（可保留为来源证据）+ 派生8题原始 Gold 行 | 3 PDF 13,127,185 B；问题 parquet 82,073 B；8题文件 4,979 B；3 PDF HEAD 200 | 3PDF/8题身份已列；PDF内容未下载/未验hash |
| HotpotQA | `hotpot-selected8.jsonl`，完整原始问题/答案/supporting_facts/context；未来另派生 corpus/manifest/8题 adapter | 文件 53,481 B；已实测8次切片响应 60,784 B；73个上下文文档 | 八题内容下载并验派生文件hash；无需整27.5MB shard |
| OmniDocBench 当前官方快照 | 下列24张图片 + 同 revision 的 Gold；后续从 Gold 精确抽取24页记录 | 24图片 25,467,034 B；完整 Gold 42,208,096 B；当前已知获取方案合计 67,675,130 B | 图片/Gold对象元数据与HEAD确认；Gold页级映射未验；24页建议未由Owner冻结 |

八题/24页是开发专项小样本建议，不替代完整 benchmark 结果。未来派生 corpus、切块、索引、Embedding、Prompt、模型和 runner 仍需另行冻结。不得以答案文档/正例段落作为唯一建库材料。

### 1.1 原9文件：直接来自 P7_ECS_ENV_PREP 第5节，现已重算一致

| 相对路径 | bytes | 完整文件 SHA-256 |
|---|---:|---|
| `scifact/manifest.json` | 1,827 | `0f9957b10413f4ed85bac62a112e416485e749f940637cdb4cd393f3e5e0982c` |
| `scifact/corpus.jsonl` | 8,020,839 | `8608aa67e02a307745506d92316ebd0d1b2079226a576446c2bb5943e064f59f` |
| `scifact/cases/locked_holdout.jsonl` | 52,830 | `c66586f3d8d5ea9a21b82bcc998bbc606f2e11698d9bc3d2e8338a8e3f3f9479` |
| `miracl-zh/manifest.json` | 3,144 | `e9dbbac0001321a27d16d89d8851596481b078445efd0d0c3e34bd102450fe79` |
| `miracl-zh/corpus.jsonl` | 3,420,932 | `105dde269ea99ddee8ad0ebf8649631e88c3710be9ad2b54b4ced59e4a17ad29` |
| `miracl-zh/cases/locked_holdout.jsonl` | 23,945 | `2fd74aa0d6fcb8854828220f8d9424216a9b5d78ad441a6a9958f3198ab09c91` |
| `longbench-zh/manifest.json` | 1,795 | `891029790b066b02a134f469a5fda2298b3a42b28b2b9c05aa3cc735df02301e` |
| `longbench-zh/corpus.jsonl` | 11,656,046 | `d5f18bf83d652664d6778d1fab2eba0c1d6ef7781cc4c106e96217eca1cac0c1` |
| `longbench-zh/cases/locked_holdout.jsonl` | 62,450 | `2f48fb242653c2ce3f883796e822b685a65f3b4f0be9b59bab5da6896fb894c8` |

合计 **23,243,808 B**（23.243808 MB，约22.17 MiB）。300+99+80=479 是历史 holdout 总数，不是本轮主评测题数。

### 1.2 当前10文件约40.86MB包：差额已解释

当前 `var/reports/v6-ecs-resume-r1/transfer-manifest.json` 与 `public-inputs.zip` 目录对应10/10。未解压/修改包。

| 组成 | 数量 | bytes |
|---|---:|---:|
| 三套完整 corpus + 三 manifest | 6 | 23,104,583 |
| `cohort_dev24.jsonl` + `source_manifest.json` | 2 | 18,352 |
| Linux `deepseek_recipe-0.1.1-cp310-abi3-manylinux_2_28_x86_64.whl` | 1 | 11,367,229 |
| `tokenizer-v41.json` | 1 | 6,367,247 |
| payload 合计 | 10 | 40,857,411 |

ZIP 是 **40,859,019 B**（目录/封装开销1,608 B），SHA-256 `31dafd309ebec3f9e60173372fb010fdd9b22f775ae62d3c0da4ebd863c2fa36`。相比原9文件，payload差额17,613,603 B = 去除139,225 B holdout + 加18,352 B dev24/source manifest + 加17,734,476 B runtime。不是 dev24 加额外负例文件；负例/背景原已在完整语料中。

`dev24` 各8，LongBench 4 dureader + 4 multifieldqa_zh；SciFact/MIRACL无答案文本 Gold 时不得宣称 QA 正确率。完整 Development case 文件和 holdout 均不在10文件包中。旧 handoff 中04:25:44截止已被最新Owner07:44:02覆盖，本轮不改旧文件。

## 2. MMLongBench-Doc：固定3PDF/8题

官方作者 [GitHub](https://github.com/mayubo2333/MMLongBench-Doc) 链接到 [作者HF](https://huggingface.co/datasets/yubo2333/MMLongBench-Doc)。固定 revision `2ff6aa9237fc777b6627dc57a486e9225ac5fb86`；当前HF main及服务器 X-Revision 均为同值。

选择规则在首次题目内容读取前写入 `selection-rules-predeclared.json`：全部135个 doc_id 按 `SHA256(revision + NUL + RAG-V6-SPECIAL-SUBSETS-R1 + NUL + doc_id)` 升序取3；各文档内部以 doc_id 和官方零基 row_idx 哈希排序，按文档顺序取3/3/2题。没有使用答案、可答性、难度或模型表现筛选。1091行按原索引完整读取作小元数据；所选每题的 doc_id 与该 revision PDF 文件名唯一精确对应。

问题文件 [data/train-00000-of-00001.parquet](https://huggingface.co/datasets/yubo2333/MMLongBench-Doc/resolve/2ff6aa9237fc777b6627dc57a486e9225ac5fb86/data/train-00000-of-00001.parquet)：82,073 B，下载后的完整 SHA-256 与官方LFS一致：`bcdac3c96669634c34184814cede4fe57cf7ac0f98dde0e85936394f6a56a02d`。

| PDF精确来源URL | bytes（官方LFS及HEAD一致） | 官方预期 SHA-256（未下载验内容） |
|---|---:|---|
| [11-21-16-Updated-Post-Election-Release.pdf](https://huggingface.co/datasets/yubo2333/MMLongBench-Doc/resolve/2ff6aa9237fc777b6627dc57a486e9225ac5fb86/documents/11-21-16-Updated-Post-Election-Release.pdf) | 5,924,004 | `8f49f93f1665fd5ff604e66ffed9649d8b18ce7f114ab43463aeac46cd3d00cb` |
| [catvsdogdlpycon15se-150512122612-lva1-app6891_95.pdf](https://huggingface.co/datasets/yubo2333/MMLongBench-Doc/resolve/2ff6aa9237fc777b6627dc57a486e9225ac5fb86/documents/catvsdogdlpycon15se-150512122612-lva1-app6891_95.pdf) | 5,296,218 | `40a74d3fc5e73c81465206a4043c19b850daf66fc5617756f5894d25ad054b4b` |
| [b3m5kaeqm2w8n4bwcesw-140602121350-phpapp02_95.pdf](https://huggingface.co/datasets/yubo2333/MMLongBench-Doc/resolve/2ff6aa9237fc777b6627dc57a486e9225ac5fb86/documents/b3m5kaeqm2w8n4bwcesw-140602121350-phpapp02_95.pdf) | 1,906,963 | `43be9592854c7da10bf011335faec017b726ff2e23bb4ccae7e8fe88d7801f22` |

| 官方原始行号（零基） | 本计划QID末尾 | document |
|---:|---|---|
| 684 | `row-0684` | `11-21-16-Updated-Post-Election-Release.pdf` |
| 687 | `row-0687` | `11-21-16-Updated-Post-Election-Release.pdf` |
| 685 | `row-0685` | `11-21-16-Updated-Post-Election-Release.pdf` |
| 431 | `row-0431` | `catvsdogdlpycon15se-150512122612-lva1-app6891_95.pdf` |
| 434 | `row-0434` | `catvsdogdlpycon15se-150512122612-lva1-app6891_95.pdf` |
| 430 | `row-0430` | `catvsdogdlpycon15se-150512122612-lva1-app6891_95.pdf` |
| 959 | `row-0959` | `b3m5kaeqm2w8n4bwcesw-140602121350-phpapp02_95.pdf` |
| 958 | `row-0958` | `b3m5kaeqm2w8n4bwcesw-140602121350-phpapp02_95.pdf` |

固定官方 schema 没有原生 QID。完整派生ID为 `mmlongbench-doc:2ff6aa9237fc777b6627dc57a486e9225ac5fb86:row-NNNN`，明确是本计划合成ID，不能称作者原生QID。原题目、答案、evidence_pages、evidence_sources、answer_format 原样保存在 `mml-selected8.jsonl`；完整ID/题目映射见 `mml-locked-plan.json`。

资料范围为3份完整PDF的全部页面，共享检索范围中保留另外2PDF和非证据页作为干扰来源；不按 evidence_pages 裁成答案页，也不承诺这些干扰等同官方跨文档检索负例。没有新增第4份PDF。作者README数据条款为CC BY-NC 4.0、研究用途；代码LICENSE标签与数据条款分开记录。

未来首次来源文件获取量为 **13,209,258 B** = 82,073 B问题parquet + 13,127,185 B PDF，不含HTTP开销/目录元数据；本轮parquet已缓存，后续只需PDF 13,127,185 B。PDF内容下载、解析、页码边界核查均 `NOT RUN`。

## 3. HotpotQA：八题实际大小及版本关系

[官方作者HF](https://huggingface.co/datasets/hotpotqa/hotpot_qa) 的 distractor/validation共7,405行。预声明规则：按旧固定完整revision `d3686a50a60ca0066a85478a192ded607f77be36`、固定seed与0..7404行号的SHA-256升序取8；不筛type/level/答案。

| QID | 固定行号 | 原始context文档 | 原始句子 |
|---|---:|---:|---:|
| `5a8ad6805542992d82986f93` | 3157 | 10 | 36 |
| `5a745a6055429974ef308be4` | 6040 | 10 | 42 |
| `5a729fe55542991f9a20c53b` | 3151 | 10 | 37 |
| `5ae3992d5542994393b9e722` | 3791 | 10 | 62 |
| `5ab672165542995eadeeffe0` | 4040 | 3 | 11 |
| `5a8911ad554299515336129c` | 4636 | 10 | 55 |
| `5a7b68a75542997c3ec97153` | 4317 | 10 | 106 |
| `5a7631cd5542994ccc91870c` | 896 | 10 | 40 |

**Owner所问八题大小：UTF-8紧凑JSONL含全部原始字段和上下文是 53,481 B（约52.23 KiB）。文件 SHA-256 `82e9d8f85ddf4e064e0454055ddd56a96a43b92938c35bd8836f1ba5e1caa042`。** 原始完整字段没有摘要、正例裁剪或truncated_cells；8题共73个context（73个不同标题）。一题原始context只有3个，保留原状，不补造为10个，也不按这点换题。

已证明的小下载路径是官方 [datasets-server /rows](https://huggingface.co/docs/dataset-viewer/rows)：8个 `length=1`、固定offset请求，实际HTTP响应体合计 **60,784 B**，每次含重复schema等封装。单请求/8QID `/filter` 方案有[官方文档](https://huggingface.co/docs/dataset-viewer/filter)，但本次8ID请求25.444秒超时，未重试，不能声称它已成功或编造该路径bytes。精确八条URL/各响应bytes/hash/X-Revision见 `probe-subsets-results.json`。

版本区别：八响应 `X-Revision=1908d6afbbead072334abe2965f91bd2709910ab`（现main），不是旧d3686a5。固定转换revision为 `14f0ace3c3fac7bd86149c616b5b05d8282e5c6a`。旧revision、现main与该转换revision的 validation 分片 LFS SHA-256、size、blobId均相同：`c20b638ca82b21d04fe12e14ff417ad05153d4d215a65de54497fca4e972f7c6`，27,452,575 B。此为官方对象身份等价证据，不是本轮完整shard内容下载hash。

未来复现优先使用本轮已保存8题文件及其hash；重新请求服务器时检查X-Revision/分片对象身份，变化即停止，不滚动替换样本。已下载八题可直接离线保留，后续网络0 B；重新从已验证小路径获取需本次实测约60.8 KB响应体，加HTTP开销。历史官方JSON超时不再重试；未发现作者单独8题固定文件。完整镜像27.5MB只是备用获取方案，**本轮未下载，也无需为了八题先下载整包**。许可CC BY-SA 4.0来自固定作者dataset card。

后续adapter应从全部73文档派生共享 corpus，保留所有句子、来源QID和标题；supporting_facts仅作Gold，不可用来裁剪建库。该adapter、本次评测与解析/索引 `NOT RUN`。

## 4. OmniDocBench：当前官方快照、24图片与Gold获取边界

按最新Owner要求停止旧v1.6冲突调查。官方 [GitHub README](https://github.com/opendatalab/OmniDocBench) 声称2026-04-30更新至v1.7；其链接指向的[官方HF](https://huggingface.co/datasets/opendatalab/OmniDocBench)当前快照固定为 **`aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec`**（latest commit 2026-06-26），没有HF tag，refs仅main/v1_5/v1_0。此快照README仍描述1651页April更新，历史提交名为add v1.6。**能确认的是当前官方可下载数据对象，不能据代码页的v1.7名称断言HF Gold内容已是独立v1.7数据发布。** 不把版本名替代文件身份，不混其他仓库/旧快照图像。

同revision完整 [OmniDocBench.json](https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec/OmniDocBench.json)：**42,208,096 B**，官方LFS预期SHA-256 **`a45cd84b04ad8b793e775089640e6b681209abea33ead54c1828ddca35fae496`**；HEAD200且Content-Length一致。仅元数据确认，完整Gold未下载/未hash。

沿用P7明确规模24页。最终建议按预声明filename-hash规则，从同revision `images/` 1651个图像中以 `SHA256(revision + NUL + seed + NUL + image_path)` 升序取24，不过滤大小、难度、内容或模型分数。下列为确定性建议清单，**不是Owner冻结或已证明分层代表性样本**。

| 图片精确来源URL | bytes | 官方预期LFS SHA-256（未验内容） |
|---|---:|---|
| [yanbaopptmerge_cf41c3f5ddc7509959ef643ca6308d5ae90f3e87708855d6770df564b749fe7b.pdf_3.jpg](https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec/images/yanbaopptmerge_cf41c3f5ddc7509959ef643ca6308d5ae90f3e87708855d6770df564b749fe7b.pdf_3.jpg) | 501,658 | `b96bbf617b52a7626ceccf1bc15d4fc74c2500df30a90882154daf990e8fce00` |
| [jiaocaineedrop_chap5.pdf_4.jpg](https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec/images/jiaocaineedrop_chap5.pdf_4.jpg) | 272,445 | `ed01f2d5e8157c375502a926e24606751104abe70a1f95520a9fdf252310091c` |
| [yanbaopptmerge_yanbaoPPT_4125.jpg](https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec/images/yanbaopptmerge_yanbaoPPT_4125.jpg) | 752,737 | `f2e079f35e9813d1054656805e2ad8c5b70b1f5fbbd367073b193c0da8cde394` |
| [newspaper_The Times UK_0801@magazinesclubnew_page_083.png](https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec/images/newspaper_The%20Times%20UK_0801%40magazinesclubnew_page_083.png) | 2,359,136 | `bce4e102948824826f61dde270bb92fdb9fd5e32bdf8250b38702d7b28e12da8` |
| [jiaocaineedrop_jiaocai_needrop_en_2604.jpg](https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec/images/jiaocaineedrop_jiaocai_needrop_en_2604.jpg) | 316,262 | `d0f87c71999e2e9439d8d66bda2886955800bb3adfcbe5bdd575a575c4651d6e` |
| [docstructbench_dianzishu_zhongwenzaixian-o.O-61510863.pdf_73.jpg](https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec/images/docstructbench_dianzishu_zhongwenzaixian-o.O-61510863.pdf_73.jpg) | 334,178 | `cee5dfe5832d25aaf999a07ccee36b931340ae088152a0dee79ad05e33aedc54` |
| [yanbaopptmerge_yanbaoPPT_4185.jpg](https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec/images/yanbaopptmerge_yanbaoPPT_4185.jpg) | 293,713 | `3471a533f957c8aed9d8732878a763c039017f454c70eecf1cc832038d76e956` |
| [docstructbench_dianzishu_zhongwenzaixian-o.O-61465568.pdf_186.jpg](https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec/images/docstructbench_dianzishu_zhongwenzaixian-o.O-61465568.pdf_186.jpg) | 120,923 | `477ea3f2303b379dfb273b8e55bb6c4cc6564802e18ff38aeccb9a02f8f996ed` |
| [color_textbook_zhonggaokao_小学_KET听说读写逐项突破_KET听说读写逐项突破之轻松搞定KET写作25分 【10讲 褚连一】_第09讲第九课写作Part9小作文详解_【8054】第九课写作Part9小作文详解_page_001.png](https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec/images/color_textbook_zhonggaokao_%E5%B0%8F%E5%AD%A6_KET%E5%90%AC%E8%AF%B4%E8%AF%BB%E5%86%99%E9%80%90%E9%A1%B9%E7%AA%81%E7%A0%B4_KET%E5%90%AC%E8%AF%B4%E8%AF%BB%E5%86%99%E9%80%90%E9%A1%B9%E7%AA%81%E7%A0%B4%E4%B9%8B%E8%BD%BB%E6%9D%BE%E6%90%9E%E5%AE%9AKET%E5%86%99%E4%BD%9C25%E5%88%86%20%E3%80%9010%E8%AE%B2%20%E8%A4%9A%E8%BF%9E%E4%B8%80%E3%80%91_%E7%AC%AC09%E8%AE%B2%E7%AC%AC%E4%B9%9D%E8%AF%BE%E5%86%99%E4%BD%9CPart9%E5%B0%8F%E4%BD%9C%E6%96%87%E8%AF%A6%E8%A7%A3_%E3%80%908054%E3%80%91%E7%AC%AC%E4%B9%9D%E8%AF%BE%E5%86%99%E4%BD%9CPart9%E5%B0%8F%E4%BD%9C%E6%96%87%E8%AF%A6%E8%A7%A3_page_001.png) | 926,377 | `e50bedfce6755d6987307c35bf2b6f08207af5ec9088bb5a36e6cc005a9972aa` |
| [exam_paper_en-file-putnam-archive_2013_Problems_2013_page_002.png](https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec/images/exam_paper_en-file-putnam-archive_2013_Problems_2013_page_002.png) | 173,261 | `5bf98977353643999616d974a9fe3168b56dc770a60181bc2aa8d849b464d91d` |
| [PPT_EnglishtoAmericanTransition_page_012.png](https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec/images/PPT_EnglishtoAmericanTransition_page_012.png) | 88,095 | `428cca795816237571181f0b2f491ecb81d895359a142c07aa93d160ab58dd9d` |
| [docstructbench_llm-raw-scihub-o.O-s00348-003-0689-7.pdf_3.jpg](https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec/images/docstructbench_llm-raw-scihub-o.O-s00348-003-0689-7.pdf_3.jpg) | 733,097 | `2d5dd15d51beb6e5a74c1c48208f3464c10ef9893f251956e3e8ea6ac44f700e` |
| [newspaper_2a6b4fa088699701a6fa9ccecfb5c25d_15.jpg](https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec/images/newspaper_2a6b4fa088699701a6fa9ccecfb5c25d_15.jpg) | 911,571 | `7da5ed976b27d3c24f6b269e7aaa46b9ab35bc1f3c7997e25119d8180366e860` |
| [page-a6d06208-475c-4c6e-9d84-858f7e2ecf72.png](https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec/images/page-a6d06208-475c-4c6e-9d84-858f7e2ecf72.png) | 326,721 | `f035c220a167a19439dd3ce1245d633db01382b609bb18aa6feea7156dc3126a` |
| [PPT_lecture1_page_007.png](https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec/images/PPT_lecture1_page_007.png) | 115,771 | `95a3151c2c9f5f3d9e36392065ecd1e7ab6cf49ae9c591b698cff7196ab78601` |
| [page-b145da5c-182f-4d4e-813a-28b8d6971b0f.png](https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec/images/page-b145da5c-182f-4d4e-813a-28b8d6971b0f.png) | 334,685 | `8e7278d62f6a1af8675cd9bb1babfabe945c73998bd96d3d4604bef976b9bd86` |
| [magazine_TheEconomist.2023.12.16_page_019.png](https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec/images/magazine_TheEconomist.2023.12.16_page_019.png) | 10,274,994 | `1cf6737ad4bca84fc82cb93d446aaef001357d7dd08e46a509875f2ed7e6b436` |
| [newspaper_d1716a12000a38e1e4c0bf66309558db_1.jpg](https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec/images/newspaper_d1716a12000a38e1e4c0bf66309558db_1.jpg) | 2,283,057 | `643f1b7e4c0f1db2890f0f452cea6ce7fc8fb81abd3e2ea45e94c41d3de0b16e` |
| [notes_f7f010b78016aeebd76e56d9283eb67f_68.jpg](https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec/images/notes_f7f010b78016aeebd76e56d9283eb67f_68.jpg) | 214,420 | `55204e21c7d46913df3736befad467caa7ba39b2f177cf91e4a54c1b1e2cdbd3` |
| [PPT_ch7_page_136.png](https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec/images/PPT_ch7_page_136.png) | 224,971 | `9dcbf54a42f97ec64a804ed454d5dc480c395b78cc7b106646cbc4d1d1730027` |
| [page-4009495f-1a7c-46dc-9f52-7f80f5f0cc5c.png](https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec/images/page-4009495f-1a7c-46dc-9f52-7f80f5f0cc5c.png) | 277,809 | `e83b311d1208ee6e95f5adbf143846a25475b51d310576ff280a336bcca083c2` |
| [newspaper_The Globe and Mail - 2025-1-8@magazinesclubnew_page_027.png](https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec/images/newspaper_The%20Globe%20and%20Mail%20-%202025-1-8%40magazinesclubnew_page_027.png) | 2,540,149 | `854a9981be969d8373367d40d21f7c64778148c2fe1b6558e581cb962e29a5b2` |
| [page-d5f79be0-5d57-4849-9897-6106dd32117a.png](https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec/images/page-d5f79be0-5d57-4849-9897-6106dd32117a.png) | 867,742 | `dc35626bf3405e7829e35c36265b06be82c95d5d03318a6eab17662afc4f6601` |
| [docstructbench_dianzishu_zhongwenzaixian-o.O-63688043.pdf_110.jpg](https://huggingface.co/datasets/opendatalab/OmniDocBench/resolve/aa1ee96d106dbe53d0ae59474d75c6e6d9b53fec/images/docstructbench_dianzishu_zhongwenzaixian-o.O-63688043.pdf_110.jpg) | 223,262 | `c99c8655ef4070c55307afd220e4d29931cbe2fbc92e11cd4e607efb310323fa` |

24个图片各HEAD200且bytes一致，合计 **25,467,034 B**。没有图片内容GET，未计算图片实际内容hash。抽到的一张magazine图像10,274,994 B，按已声明规则保留，没有为了减少bytes事后换页。精确URL、revision、期望hash及HEAD值另见 `omni-locked-plan.json`。

Gold/图片关系分层：官方固定README明确Gold与images目录配套，同commit对象身份唯一；24个文件均存在该images目录。这证明来源版本一致。**逐页Gold的 image_name 对应、页级属性/标注内容仍 NOT VERIFIED**，不能把配套说明当成本轮对24页原Gold的实际回读。

官方 `/rows` 的真实结果只有image URL/尺寸，viewer总1657行包含6个根目录资产；没有Gold列。24次预声明行号能力探测仅保留为原始证据，不用这些viewer行冒充Gold或最后样本。发现这点后作一次证据驱动修正，回到先前声明的images目录filename规则，未追加大包读取或重复探测。

目前可证实的获取方案：只下载上述24图片（25,467,034 B），加固定完整Gold（42,208,096 B）后在本地抽取同名24条，合计 **67,675,130 B**（约67.68MB，不含HTTP开销）。最终只保留24条Gold的bytes为 **UNKNOWN**。这不是网络只下载24页Gold；官方Gold切片能力未获证明。**该完整Gold下载尚未执行，须由Owner/协调父线程明确批准此具体大文件范围后再做。**

官方README可确认研究用途、非商业限制；HF没有返回机器可读license card。本轮未独立证明旧P7所写CC BY-NC 4.0对应当前对象，许可标识保持来源限制/UNKNOWN，不补写“已验”。

## 5. 执行命令、结果与证据

**未实现的命令不得报告通过。** 下列均实际执行；记录到的HTTP成功与离线一致性检查不同，均不是模型/服务评测。原始请求packet/results记录完整URL、方法、限额、HTTP状态、bytes、下载后hash和elapsed。

| 原样执行命令（PowerShell） | exit | 实际结果/证据 |
|---|---:|---|
| `& '.venv/Scripts/python.exe' -B -X utf8 'var/reports/v6-special-subsets-r1/public_probe.py' 'probe-initial.json'` | 0 | 6请求，3 HTTP成功；GitHub API 3次403 rate limit，未重试/换路；`probe-initial-results.json` |
| `& '.venv/Scripts/python.exe' -B -X utf8 'var/reports/v6-special-subsets-r1/public_probe.py' 'probe-subsets.json'` | 0 | 13/13 HTTP成功；八题响应、MML parquet完整hash、Omni固定元数据；`probe-subsets-results.json` |
| `& '.venv/Scripts/python.exe' -B -X utf8 'var/reports/v6-special-subsets-r1/public_probe.py' 'probe-server-metadata.json'` | 0 | 5/5 HTTP成功；固定转换对象/revisions/官方目录；`probe-server-metadata-results.json` |
| `& '.venv/Scripts/python.exe' -B -X utf8 'var/reports/v6-special-subsets-r1/public_probe.py' 'probe-question-pages.json'` | 0 | 35/35 HTTP成功，11批MML题目元数据与24次Omni图片URL能力探测；`probe-question-pages-results.json` |
| `& '.venv/Scripts/python.exe' -B -X utf8 'var/reports/v6-special-subsets-r1/baseline_audit.py'` | 0 | 原9文件9/9匹配；10文件ZIP目录/bytes/hash匹配；dev24行数24；`baseline-audit.json` |
| `& '.venv/Scripts/python.exe' -B -X utf8 'var/reports/v6-special-subsets-r1/select_subsets.py'` | 0 | 3PDF/8题唯一映射，24图片确定性清单；`mml-selection.json`, `omni-selection.json` |
| `& '.venv/Scripts/python.exe' -B -X utf8 'var/reports/v6-special-subsets-r1/public_probe.py' 'probe-heads-filter.json'` | 0 | 28/28 HEAD成功，1 filter超时；脚本exit0不代表全部HTTP成功；`probe-heads-filter-results.json` |
| `& '.venv/Scripts/python.exe' -B -X utf8 'var/reports/v6-special-subsets-r1/finalize_plan.py'` | 0 | 独立计划/六集manifest/网络摘要生成；`six-dataset-plan.json`, `network-summary.json` |
| `& '.venv/Scripts/python.exe' -B -X utf8 'var/reports/v6-special-subsets-r1/validate_plan.py'` | 0 | 22/22真实离线身份/范围检查通过；15个受保护输入hash未变；`validation.json`；不是模型或RAG质量评测 |
| RAG业务测试/模型调用/ECS/DB/主评测/verify-mX | NOT RUN | 本轮禁止范围；没有调用这些命令 |

记录到的Python探测响应体合计 **1,081,680 B**，共88请求、84 HTTP成功，其中28 HEAD内容体0 B。这不包含HTTP/TLS头、web工具流量与失败请求未知线上传输量，不能称整个任务网络流量精确值。大PDF/图片/完整Gold GET数 **0**；metadata/content-download hash证据分开标注。

Windows `rg`不存在（第一次会话命令exit1），随后使用Get-ChildItem/Select-String，仅必要目录。两个Codex app工具调用（依赖定位/初步回传）未返回结果后停止等待，结果UNKNOWN；未以它们证明路由、依赖或消息送达，也未重发消息。本轮公开网络读取正常通过sandbox自动审批，无自动审批拒绝。GitHub API限流与filter超时是网络结果，不用其他镜像绕过。

## 6. 风险与遗留、版本与下一步

1. `NEEDS_OWNER_REVIEW`：三个专项选择均为确定性建议，未经Owner冻结；小样本不能宣称benchmark代表性或总体QA质量。
2. `NOT RUN`：3PDF/24图片内容hash、解析/页码边界及模型评测。先按清单批准必要对象后下载验证，不能现在宣称完整下载成功。
3. `BLOCKED`（仅Omni后续Gold适配）：当前范围不含完整42,208,096 B Gold GET；官方viewer不能提供Gold页切片，24页Gold对应未回读。范围需Owner明确决定，不自行延期或排除该数据集。
4. Omni代码v1.7名称与当前HF数据说明不同；复现使用具体commit+对象hash，禁止按版本名滚动取main或混文件。许可标签未确定的部分如实标UNKNOWN。
5. Hotpot filter超时为局部可绕过问题：已成功的8次rows切片保留原context，故不阻塞八题获取；无须大包下载或新增探测。
6. HEAD `befd5478a680ffa779a1e8ede736260d9bac1060`；本轮不commit、不Tag、不批准里程碑。原9文件/受保护输入前后hash检查见最终validation；其他线程dirty工作区不覆盖。执行控制未由控制平面完整证明，按MANUALLY_SUPERVISED_TRIAL记录；不自称ACCEPTED或完全自动化。

下一个最小任务：Owner/协调父线程评审固定3PDF/8QID、Hotpot8QID/73候选文档与Omni24图片清单，决定Omni完整Gold的具体下载范围；批准后独立下载必要对象并验内容hash、构建派生adapter。三主集现有dev24/ECS准备继续独立执行，不等待专项Gold。
