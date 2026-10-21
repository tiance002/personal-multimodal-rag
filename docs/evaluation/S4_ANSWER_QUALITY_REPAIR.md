# S4 — 答案质量归因与最小修复

日期：2026-10-19。结论：**PASS（限定工程修复与验收范围）**。日期解析和已确认的来源句柄展示缺口已修复；新日期问题经过真实正常 API 回答正确。unit/site 关系错误与 Summary 过度具体化仍明确保留为 **UNRESOLVED_MODEL_QUALITY**，不表示所有生成质量问题已经解决。

- 起点：`2c21bbcc68384224b5d4a29d1300adf404c1f9b2`。
- 实现提交：`2e214051d7c00cac487fb1bd0382f18e8078c779`。
- 分支：`codex/weknora-python-port-integrate-image-20261014`。
- 最终交付提交完整 SHA 见最终消息，或执行 `git log -1 --format=%H -- docs/evaluation/S4_ANSWER_QUALITY_REPAIR.md`。
- 保留执行前 18 个未提交文件；未修改 Gold、S3 报告、旧原件/索引、历史回执或 UNKNOWN。没有 Push、部署、合并、Tag 或进入 S5。

机器结果：[S4_ANSWER_QUALITY_REPAIR.results.json](S4_ANSWER_QUALITY_REPAIR.results.json)。新增真实证据：[S4_REAL_DATE_REGRESSION_20261019.zip](evidence/S4_REAL_DATE_REGRESSION_20261019.zip)。日期本地重放：[S4_NATIVE_DATE_REPLAY_20261019.json](evidence/S4_NATIVE_DATE_REPLAY_20261019.json)。来源展示对照：[S4_SOURCE_HANDLE_DISPLAY_20261019.json](evidence/S4_SOURCE_HANDLE_DISPLAY_20261019.json)。旧 S2/S3 原始证据继续引用原归档，不作为新增模型调用。

## 1. 问题清单与决策

| 问题 | 根因分类与首次错误 | 本轮处理 |
|---|---|---|
| PDF 日期变为 `2026-1 0-1 2` | **已确认根因**：原生 PDF 布局重建把窄字符的字形留白误判为空格 | 修复原生字距计算；本地原件及真实新上传/问答通过 |
| Summary 写成 1 月 | **已确认根因**：摘要模型误读已损坏输入 | 上游修复后新 Summary 为 10 月；不宣称已实现通用摘要事实保证 |
| unit NP-204 → “204号机组” | **已确认首错环节；当前模型能力不足**：Summary 输出首先引入设备子类型 | 保留 UNRESOLVED；不按特定 unit 字符串改写任意答案 |
| Site West Yard 被称为 unit | **已确认首错环节；当前模型能力不足**：正确表格已进入最终请求，最终生成混淆关系 | 保留 UNRESOLVED；不新增通用事实裁判或完整性 Prompt |
| 简中设置却回答整段英文 | **已有修复，补齐回归**：当时 system 已正确传递，属于模型未遵循 | 沿用 S3 `d4331ee` 已有实现；新英文日期问题用中文回答，设置合同/显式 English 模拟回归通过 |
| 行内 `d1/c2` 来源注记 | **已确认代码缺口**：旧展示处理只覆盖部分来源模板；缺闭括号的 EOF 还可能漏出 | 后端批量/流式、前端历史/live 同步局部修复；保留普通正文及代码 |

不再把“存在 Summary”推断成“它必然进入最终模型请求”，也不把来源文档命中解释成必要事实送达。

## 2. 日期：逐层证据与真正根因

冻结文件为 S3 `north_pier_inspection.pdf`，SHA256 `9cf9b35209cac1162a0edafe4f56616446bdfd3e1dadda5ce8bae0931853e1e5`。历史文档 `48542dff-fd0d-4e63-8784-b0969ab5ab51`，版本 `4d91ea97-d566-4702-be33-f571ba58c0eb`。

| 层次 | 已保存或本轮直接核验的内容 |
|---|---|
| 原 PDF 第 1 页 | 内容流的原始字节解码为 `Inspection date: 2026-10-12` |
| 实际解析分支 | 第 1 页是原生 text layer；第 2 页是扫描卡片。**日期不来自 MinerU**；MinerU 只处理第 2 页 |
| 原生提取 plain | 本轮只读离线复现仍得到完整 `2026-10-12` |
| 旧布局输出 | `samples/parse_results.json` 已是 `2026-1 0-1 2`；基线代码离线复现同样错误 |
| ParsedDocument / Chunk | native PDF 路径直接使用 ParsedDocument；没有该页的 NormalizedDocument 工件。历史 text Chunk `a5f3dbd0-b587-451f-a06a-16e9b25b9e32` 原样保留坏串 |
| Summary 输入 / 输出 | `eb65f8ad595e4f0a95b37fdcf11fd7a1` 请求收到坏串；响应首次写 `2026年1月12日`，存入 Summary 和 description |
| Search / Rerank / Merge | 都保留上述 text Chunk 和坏串；Summary 也被 Search 召回，但未进入该次最终 Merge |
| 中间 Context 模板 | `a72d8e...stages.json` 同时含错误 description 和坏原文 |
| **最终实际 Provider 请求** | `b8012b694bad48fca442102933c027bc.request.json` **只有坏原文，没有 Summary/description** |
| 原始 SSE / 持久化答案 | 都给出 `2026年1月12日`，stop/DONE，二者一致；不是 Decoder 或历史显示改坏 |

这更正了 S3 对该题的较粗归因：错误 Summary 确实存在，但不能称它是该次最终请求中的直接致错证据。S3 历史文件不改，S4 保留此更正及精确回执定位。

### 最小修复

`ingestion/upstream_pdf_parser.py` 的 `_page_chars()` 增加有效 font cell 横坐标，`_join_line_glyphs()` 据此判断词间空隙；tight bbox 继续用于原有布局、分栏和过滤。font cell 缺失、异常或无效时保留字符并回退原几何逻辑。

实测旧算法在字符“1”后看到约 2.700 的 ink gap，就插入空格；真实 font cell 彼此连续，间隔为 0。修复不匹配日期、不硬编码月份、不删除任意数字间空格，也不改变 MinerU。

同一冻结原件正常离线解析/切块后，全文与旧文本相比**仅减少这两处错误空格**；扫描卡片资产 SHA 不变，Chunk 原文切片与偏移一致。新增测试还覆盖不同日期、真实间隔、显式空格、无效 font cell、普通正文和来源身份。

## 3. unit/site 与过度具体化：不伪装成程序修复

**q4：**PPTX 原件 slide XML、解析正文、OCR 和 Caption 都只有 `unit NP-204`。真实 Summary 请求 `7b33910dee444abdb1dc182f15806db7.request.json` 已要求不添加事实，但响应首先生成“204号机组”。正确原文与错误 Summary 都进入 q4 实际请求 `c5feb6afbe4a43349e96617f6e6ec7c4`；最终日期正确，设备子类型无依据。

**q6：**XLSX 表头明确 Site / Unit / Reading / CheckedOn；原行是 West Yard / WY-309 / 51 / 2026-10-13。解析、Summary 和最终请求 `3e47e2424d34458e86dd64451b1bd9cd` 都保留正确关系，回答才首次把 West Yard 称为 unit。检查日期有原文支持，但若“数据记录于”表示独立 RecordedOn 时间，则原文件没有该字段，不能认证这层含义。

两处都是可复核的真实模型语义错误。没有发现强制上述错误翻译的 Prompt 冲突；本轮不堆新指令、不全局排除 Summary、不建立特殊实体替换或语义裁判。没有安全通用的局部程序办法保证修正任意设备类型与关系，故明确保留未解决。没有新增这两题的模型请求，也不捏造“修复后正确答案”。

## 4. 语言与内部来源展示

语言旧失败 q3 的原问题为英文，实际 system 已要求简中；没有历史消息，Query Understanding 也没有覆盖 language 字段。最终回答整段英文，确为设置不遵循，不是仅保留 NORTH PIER 等专名。

S3 `d4331eef50f03a504247776c78d932502576e2d1` 已把当前设置的本地语言说明放在问题旁。当前代码保留该实现，不再新增全局 Prompt。S3 后续中文样本及本轮英文日期问题均提供简中实测证据，专名 North Pier 和文件名正确保留。显式 English 的选择、刷新持久化、请求头及生成准备合同通过离线/模拟 API 浏览器验证；**没有新真实 English 模型调用，不宣称五语言均已真实验收**。

句柄新增覆盖的是实际 S2 来源注记：

- ``根据提供的课件内容（文档 `d1`）`` → 保留句子、去除私有来源括号；
- ``提供的文档片段（`d1`）`` → 同上；
- `(Source: d1, c1)` → 去除该私有来源注记；
- 已可确认的上述模板在 EOF 缺闭括号时也隐藏，流式过程中暂存尚未可判定的前缀。

没有全局删除 `d1/c2`；普通正文、独立括号、行内代码及 fenced code 均保留。底层 document/chunk/version 不变，引用开启的 Registry 映射合同保留；当前公开引用 UI 仍关闭，不编造文件名、页码或链接。历史使用同样展示规则，**不重写历史数据库**。

12 份真实 S2 固定 Context 回答离线重放：基线有 **5/12** 残留模板，当前 **12/12** 展示符合预期；8,523 个切分点及原始 Token 分块通过。这里只评展示，不重算它们的事实质量或将旧调用计为本轮调用。任意未观察的自然语言句柄重述没有通用保证。

## 5. 新授权下的一次真实日期回归

Owner 单独批准 tenant1 新隔离 KB、同一 PDF 上传 1 次、日期问题 1 次、最多 12 Provider；Caption 关闭，本地 MinerU，正常 DYNAMIC。使用正式普通云模式和原 Gate/账本；该模式无累计 Token/次数硬围栏，未虚报为有硬预算模式。

沿用 `wk.ps1` 的同一 API/UI 端口，没有临时验收 API 或 runtime_factory。实际 tenant 1、PG system ID `7696088403504697382`、DB `weknora_python_port_20261013`、Redis DB15 身份均核实；S4 独立 Redis 前缀，未消费旧前缀任务。日常 principal、默认模型及配置记录未改。

| 项目 | 新增真实结果 |
|---|---|
| KB | `349e2703-2d87-41cb-b740-7c2689dbf3f4` |
| document / source_version | `433a9ffc-4b3e-4f1e-8b0b-88bcdf8e1de1` / `390db60f-f6d3-49d9-b71b-1bc459ae634d` |
| 上传 | 仅 1 次，31.446 秒至 completed；OCR 本地 MinerU，无 Caption |
| 原文 / Summary | 新原文 `2026-10-12`；新 Summary 为 `2026年10月12日` |
| 索引 | 3 Chunk，PG 实查 3 个非空 1024 维向量，分别对应原文、OCR、Summary |
| 问题 / Scope | 原问题 `What was the inspection date?`；新 KB 范围，`document_scope=[]`，无历史、无指定 Chunk |
| run | `014465de-c3d3-4e18-8392-4739aa1e0524` |
| Query Understanding | Qwen3.5-4B，thinking=false；原问题保持，intent=kb_search |
| Search → Rerank → Merge → Context | **3 → 1 → 1 → 1**；最终为原文 Chunk `942f6770-2423-47ac-959b-74f2fd2f21be` |
| 原始 Rerank 分数 | 原文 0.2611276805、Summary 0.0265415162、OCR 0.000053422；原生 0.3 无通过后由已有 0.15 Top1 保底保留原文，配置未改 |
| 实际生成输入 | 包含完整新原文日期；没有损坏的空格串，没有手工 Gold 或理想证据替换 |
| 回答 | `根据提供的资料，北码头（North Pier）的检查日期是 **2026-10-12**。`，文件名 `north_pier_inspection.pdf` |
| 生成状态 | SiliconFlow Qwen3.5-4B，thinking=false，无应用预算；stop、无升级、无 fallback、无内部句柄/图片 |
| 耗时 | 整次客户端 6.983 秒；最终生成 1.251 秒，二者口径不同 |
| SSE / PG / 回读 | 协议完成，持久化 MATCH；原件 HTTP200/SHA 一致；关闭云后 GET 历史一致，调用数仍为 9 |

新 KB 仅有该 PDF，Caption 也按授权关闭，故这不是纯单变量的最终生成 A/B；原件离线前后全文比较才直接隔离了字距修复。真实回归证明修复后的原文能通过正常索引、检索和生成得到正确答案，不外推其他问题已全部正确。

**本轮新增 9 次 Provider，全部已结算：input 3,993 / output 200 / total 4,193 Token。** 上传侧 3 次 / 1,020 Token；问答侧 6 次 / 3,173 Token。最终生成单次 input 523 / output 34 / total 557。实际只用 Qwen3.5-4B、BGE-M3、BGE Rerank；DeepSeek、Caption 调用均为 0。新增 UNKNOWN 0。623 条既有 Provider 行指纹、S3 三文档/十 Chunk/图片任务指纹全部不变；旧 UNKNOWN 不结算、不释放、不重发。当前授权 CLOSED，云开关 false，PG/Redis 保留。

## 6. 实际测试及执行命令

以下工作目录为本工作树；精确命令、失败与退出码也记录在 JSON。测试批次互有重叠，不相加伪装覆盖量。

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest -q tests/weknora_port/test_s4_pdf_spacing.py tests/weknora_port/test_references.py tests/weknora_port/test_generation_protocol.py tests/weknora_port/test_generation.py tests/weknora_port/test_s3_answer_prompt.py tests/weknora_port/test_conversation_configuration.py --tb=short
# exit0: 149 passed in4.26s

# 使用 wk 实际已有的依赖路径；没有安装或全局升级依赖，结束后恢复子进程环境。
$env:PYTHONPATH='E:/WK/deps/reader;E:\RAG-Worktrees\weknora-python-port-20261013\var\weknora-port\runtime-deps'
& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest -q tests/weknora_port/test_ingest.py -k 'pdf' --tb=short
# exit0: 4 passed,7 deselected

npm --prefix frontend test -- adapter.spec.ts -g 'answer language defaults' --output=E:/WK/run/s4-quality-20261019/language-ui-test
# exit0: 1 passed，真实测试浏览器+Mock API
```

前端目录下实际执行：

```powershell
$env:PLAYWRIGHT_CHROME_PATH='C:/Program Files/Google/Chrome/Application/chrome.exe'
& ./node_modules/.bin/playwright.cmd test --config E:/WK/run/s4-handle-display-20261019/playwright.focused.config.ts tests/weknora-port/adapter.spec.ts --grep 'observed S2 inline source handles' --reporter=line --output E:/WK/run/s4-handle-display-20261019/playwright-eof-results
# exit0: 2 passed in7.3s；同时覆盖完整模板、EOF、正常正文/代码、历史/live
npm run build
# exit0: TypeScript/Vite，274 modules
```

本轮本地原件重放和最终真实回归分别执行：

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -B scripts/s4_date_replay.py --source 'E:\WK\run\s3-product-20261019\samples\north_pier_inspection.pdf' --source-sha256 9cf9b35209cac1162a0edafe4f56616446bdfd3e1dadda5ce8bae0931853e1e5 --output 'E:\WK\run\s4-quality-20261019\date-native-replay.json'
# exit0；无 API/Provider/索引写入
& 'E:\RAG quention\.venv\Scripts\python.exe' -B 'E:\WK\run\s4-quality-20261019\real.py' configure
& 'E:\RAG quention\.venv\Scripts\python.exe' -B 'E:\WK\run\s4-quality-20261019\real.py' upload
& 'E:\RAG quention\.venv\Scripts\python.exe' -B 'E:\WK\run\s4-quality-20261019\real.py' question
# 各exit0；当前授权已关闭、一次性claim已占用，不可直接重复发送
```

标准 `wk.ps1 stop/check/start -Config E:\WK\run\s4-quality-20261019\local.json` 实际退出0；结束时恢复 cloud=false。新原件与历史只读回查、623 行历史指纹及旧 S3 来源指纹检查均退出0。

首次失败保留：新日期测试 RED 为4失败/4通过；来源新模板 RED 为3失败/17通过、EOF RED 为2失败/20通过。裸 venv 收集旧 PDF 测试时缺 pptx，退出1；复用已存在的正式 PYTHONPATH 后4项通过。只读索引诊断曾错误假设 embeddings 有 tenant_id，退出1；按实际 Schema 通过 knowledge_bases 的 tenant JOIN 修正后退出0，没有 Schema 变更或模型补发。

`git diff --check`、`git diff --cached --check` 实际退出0。不执行全仓、六套数据集、完整 S1/S2/S3 或新阈值扫描。**未实现的命令不得报告通过。**

## 7. 修改文件、未运行项与后续优先级

生产代码仅修改：

- `backend/weknora_port/ingestion/upstream_pdf_parser.py`：font cell 字距修复。
- `backend/weknora_port/references/{registry.py,decoder.py}`：明确来源注记的批量/流式展示清理。
- `frontend/src/weknora_port/WeKnoraPythonApp.tsx`：旧历史与 live 展示一致。

测试/工具：`tests/weknora_port/{test_s4_pdf_spacing.py,test_references.py}`、`frontend/tests/weknora-port/adapter.spec.ts`、`scripts/s4_date_replay.py`。没有修改默认 Prompt、语言优先级、模型、DYNAMIC、全局阈值、MinerU、数据库 Schema 或历史索引。

保留项：

1. **优先级1：Summary 过度具体化、最终生成关系混淆。** 根因已确认但没有可信通用小补丁，本轮不通过更多 Prompt 或特例制造 PASS；后续若比较方案，先冻结原始上下文与干扰反例，另行授权有限实验。
2. **优先级2：历史坏版本。** 旧 S3 日期 Chunk/Summary 不变；本次修复对新解析生效。若用户需要修正日常既有资料，应通过正常新版本处理，不能静默改写历史。
3. **优先级2：其他显式语言的真实模型遵循。** English 设置合同已验证，但本轮真实 English 模型回答 NOT_RUN，不机械翻译历史答案。

unit/site、过度具体化的新模型复验、任意新句柄自然语言形式、全量格式/语言矩阵均 NOT_RUN 或未覆盖；未声称解决。没有新引入来源错配、越权、重复发送或 UNKNOWN 处理缺陷的证据。S4 在已批准范围内收口，不自动 Push 或进入 S5。
