# S3：多模态文档处理与真实产品体验

日期：2026-10-19。最终结论：**S3 PASS（限定产品流程范围）**。

3 份真实上传资料完成处理及向量入库；真实 Edge 完成上传、问答、原件查看和刷新回读。6 道普通问答均完成，完整回答质量为 **2 PASS / 3 PARTIAL / 1 FAIL**；不能把流程通过解释成所有答案正确。另一次开启思考的正常 API 问答正确完成。本阶段停止，不进入 S4。

机器结果：[S3_MULTIMODAL_PRODUCT_E2E.results.json](S3_MULTIMODAL_PRODUCT_E2E.results.json)。原始请求、响应、阶段记录、截图和文件 SHA 清单：[S3_PRODUCT_E2E_20261019.zip](evidence/S3_PRODUCT_E2E_20261019.zip)。没有用 Mock 代替真实模型验收。

## 1. 版本及最新产品决定

- 起点：`5cfa6b8ffcbfb5e49307b497f6b2aee49fa7c44d`。
- 分支：`codex/weknora-python-port-integrate-image-20261014`。
- UI 恢复：`a0c68cf45cdda79bbf4b7f7da98fe80f519a0469`。
- 被动证据记录及来源句柄处理：`e6b3383782a8287e01443807a4108469d6f7b524`。
- 提示词 V1 / V2：`267d1f48a925700c7703250abc4bac8385caa9c7` / `6d99328e348d84d2fac922a4756353ade36b95a9`。二者的真实循环失败保留。
- 回答语言、普通问答关闭思考、EviRoute 身份：`4aee9f17e05f740c27dfa9ba4916f1c81c4fa90e`。
- 撤回新增的思考预算设置：`84fe6bf6633784b6c6a5287a1b4d89eab0c7f0e9`。临时预算实现没有发生真实发送，不作为实测结论。
- 在当前用户消息旁明确配置语言：`d4331eef50f03a504247776c78d932502576e2d1`。
- 只读汇总器区分 SSE 协议完成与传输 EOF：`1bb2720f5b545acb0ba0c411d36499d1e24b399d`。这是报告工具修复，不改变生成行为。

最终报告提交的完整 SHA 由 `git log -1 --format=%H -- docs/evaluation/S3_MULTIMODAL_PRODUCT_E2E.md` 及最终交付消息核实，避免将提交自身的 SHA 写进自身内容。Owner 最后明确要求完成 S3 后由 **6 Luna 推送 GitHub**；这覆盖本阶段早先的禁止自动 Push 要求，仍不部署、合并、打 Tag。

最终生效规则：

1. 系统开头为：**你是 EviRoute RAG，面向个人用户的知识库问答助手。请依据本次请求提供的资料回答用户问题；不要编造资料中没有的事实。**
2. 回答按设置中的语言，默认简体中文；不再按问题或来源语言自动切换。模型设置页提供简中、繁中、英文、日文、韩文，选择保存在本浏览器并随请求发送。
3. 普通 Quick 问答明确 `reasoning_effort="off"`，实际 Qwen wire 为 `enable_thinking=false`。
4. 开启思考的既有 API 路径保留；**应用不设置默认 thinking_budget，交由供应商处理**。没有新增预算平台或修改 DYNAMIC 评分。
5. 按 Owner 最新决定，**回答不附检索图片**；公开引用继续关闭，仅列真实对应文件名。OCR、Caption、图片及版本关系仍作为内部证据保存和回读。

## 2. 实际入口、资源身份与授权

主链为：真实 React → 正常 FastAPI → PostgreSQL 文档任务 → 标准独立 Worker → Parser / MinerU / Caption → Child 文本向量 → Hybrid Search → Rerank → Merge / Top-K → Context → DYNAMIC → Provider → SSE → PostgreSQL 历史。

| 项目 | 实际身份 |
|---|---|
| 日常入口 | `wk.ps1`，UI `http://127.0.0.1:56426`，API `127.0.0.1:56425`，没有第二套验收 API |
| 用户 | tenant 1，既有 local principal；subject SHA256 `4a8c5c959f9ffb8d0f621c5bfc29030ebd17f565b2512a920bbe61058451ff15` |
| 隔离 KB | `d683c1b1-7268-45c2-8a05-23c94f4d5db0`，未覆盖历史语料 |
| PG | `127.0.0.1:56423`，`weknora_python_port_20261013`，system ID `7696088403504697382` |
| Redis | `127.0.0.1:56424` DB15；前缀 `weknora-python-s3-7dc75486356c4e7c9e8bd42e1f55480f` |
| 存储 | `E:/WK/run/rag-m34-20261018/assets` 下新 KB / document / version 目录 |
| 查询理解 / Cheap | SiliconFlow `Qwen/Qwen3.5-4B` |
| Embedding / Rerank | SiliconFlow `BAAI/bge-m3` / `BAAI/bge-reranker-v2-m3` |
| Caption | 现有 DeepSeek `deepseek-flash` 的隔离绑定；2 次真实图片调用，不修改默认模型角色 |

Owner 批准本阶段普通云模式，明确接受该模式没有累计 Token / 次数硬围栏。仍使用正式 tenant、KB 外发许可、Registry、原 SendGate、幂等和持久化调用账本；未替换 Gate 对象。被动观察器的准入前请求文件不独立证明发送，本报告同时核对真实响应和 PG 调用行。

每次出现新的终态 UNKNOWN 均停止当时授权，后续只在 Owner 新许可下继续独立验证。当前所有临时授权已 STOP / CLOSED，`cloud_allowed=false`；API / Worker / UI 可本地查看，PG / Redis 保留。历史 UNKNOWN 没有释放、改写或补造结算。

配置为 `E:\WK\run\s3-product-20261019\local.json`。原日常配置 `E:\WK\run\p81-retirement-20261018\local.json` 未改，SHA256 为 `7c209bdb1b82e4acdbc50d1c575df6899c6d8625c9b139362f5bd71a2054750a`。执行前已有 18 个未提交文件内容保持不变，未混入本阶段提交。

## 3. 正常上传、解析、图片与索引

样本为来源明确的公开合成资料，不是六套数据集官方 Gold。三份各上传一次：PDF、XLSX 经真实 Edge 文件选择器；PPTX 经正常 API。最早 PDF 选择器权限拒绝在上传 POST 前，未将其计为上传或重复提交。

| 文件 | document / source_version | 实际处理与持久化 |
|---|---|---|
| `north_pier_inspection.pdf` | `48542dff-fd0d-4e63-8784-b0969ab5ab51` / `4d91ea97-d566-4702-be33-f571ba58c0eb` | 第 1 页原生文字，第 2 页扫描卡片；本地 MinerU OCR、真实 Caption；4 Chunk / 4 个非空 1024 维向量 |
| `north_pier_service_brief.pptx` | `fce4db0b-c790-49ad-b2a4-441769da3b94` / `9df714d2-5fa4-47ff-a06b-1405059acbec` | 正文、1 张图片、MinerU OCR / Caption / Summary；4 Chunk / 4 个非空 1024 维向量 |
| `site_readings.xlsx` | `3b2870ce-81d3-4cbf-b14f-24cc1088f96a` / `18ff1d62-d644-4376-b82c-76743fb1d0e7` | SiteReadings 表头和行文本、Summary；2 Chunk / 2 个非空 1024 维向量；没有额外 OCR / Caption |

三个文档任务 completed；两个持久化图片任务 completed；共 10 Chunk、10 个真实向量，索引 Chunk ID 与文档记录匹配。小文档未强造 Parent；保持已有 Parent 4096 / Child 384、Child-only 向量规则。原件均经授权 `/source` 回读，SHA 与冻结输入一致，完整 SHA 见 JSON。

- PDF 卡片资产为物理第 2 页，路径 `images/north_pier_inspection_page_2.jpg`；OCR、Caption、原图及当前 source_version 可关联回读。
- PPTX 原件和 ZIP 关系能证明图片位于第 1 张幻灯片；图片记录 `page_number=null`，**不宣称已持久化结构化页码**。
- MinerU 为本地 4.0.12 路径，没有 Tesseract 或云 OCR 回退；Caption 与 OCR 独立。
- XLSX 保留行列文字和原始单元格，可支持数值问答；该路径未输出类型化 `TableRowProof`，标记 **NOT IMPLEMENTED**，不假称已验证此证明对象。
- 内联 OCR layout 已保存；完整 MinerU `middle_json` 侧车未进入文档资产库，记录为留项。

## 4. 正常真实问答与分层结果

各题新 session / request_id；相同合法 KB Scope，`document_scope=[]`，没有手工指定证据或直接文档加载。实际 Query Rewrite、完整候选、分数、筛选、最终 wire 和回答见 ZIP。以下耗时是**最终生成 Provider 调用**，不是整次问答总耗时。

| 题目 | Search → Rerank → Merge → Context | 最终生成秒 | 调用数 / 实际 Token | 事实与回答判定 |
|---|---:|---:|---:|---|
| q3 卡片的站点及读数 | 9 → 1 → 1 → 1 | 1.101 | 5 / 4,155 | North Pier、37 有原文支持；回答英文违反简中设置，PARTIAL |
| q1 检查日期 | 9 → 1 → 1 → 1 | 0.936 | 6 / 4,046 | 源日期 2026-10-12，正文拆成 `2026-1 0-1 2`，Summary 错成 1 月；答案 1 月 12 日错误，FAIL |
| q2 扫描卡片读数 | 9 → 2 → 2 → 2 | 0.891 | 7 / 4,213 | 实际 wire 含卡片 OCR / Caption 的 37、正确 PDF 版本，中文回答正确，PASS |
| q4 下次复查日期 | 9 → 2 → 2 → 2 | 1.152 | 5 / 4,194 | 2026-10-20 正确；将 unit NP-204 过度具体化为“204号机组”，PARTIAL |
| q5 East Gate 读数 | 9 → 2 → 2 → 2 | 1.251 | 5 / 4,108 | 表格原行支持 24，中文答案和 XLSX 文件名正确，PASS |
| q6 读数 51 的 unit | 9 → 1 → 1 → 1 | 2.054 | 6 / 4,042 | wire 含 Site=West Yard、Unit=WY-309；答案含正确 ID 但把站点称为 unit，PARTIAL |

普通 6 题：关键目标值命中 **5/6**；完整回答质量 **2/6 PASS、3/6 PARTIAL、1/6 FAIL**。两者口径不同。q5 同时带入无关 PDF Summary，但该题没有据此答错；这不是无噪声检索通过。

q3 普通题使用 `84fe6bf`；其余 5 题在 `d4331ee` 的语言说明增强后执行，均输出中文。没有为改善 q3 普通模式分数重复提交同一条件，也不把不同提交合为同版本 A/B。

### 开启思考的单次真实回归

- 正常 API、同一 PPTX 卡片问题、`reasoning_effort="auto"`，run `809e1768-8467-4b8a-82ef-7de7a05b2394`，代码 `d4331ee`。
- 实际 Qwen wire `enable_thinking=true`；没有应用 `thinking_budget`，由供应商默认处理。
- 正确中文回答 North Pier / 37，真实 PPTX 文件名，不附图；`finish_reason=stop`，SSE 与持久化匹配。
- 最终生成 **42.606 秒**，input 611 / output 1,074 / total 1,685；其中 reasoning 1,036 已包含在 output 中，不能重复相加。
- 整次客户端执行含创建会话 **48.226 秒**；8 次 Provider、5,189 Token。没有 Expensive 升级或 fallback。
- 这是 **PASS_SINGLE_THINKING_PATH**，不是 Smart / Agent UI 验收；Smart 按钮仍不可用，也不证明任意思考问题均稳定。

客户端最初漏正常 Origin，创建会话被现有中间件 403 拒绝，问题 POST / Provider 均为 0；修正客户端 Origin 后执行上述唯一模型请求，没有放宽正式 API 校验。

## 5. SSE、来源查看、刷新与故障处理

最新 7 次问答均完整生成，`finish_reason=stop`，真实 SSE `[DONE]` 与 PG `RESPONSE_RECEIVED`、finished 状态对应，最终答案与历史 MATCH。旧工具曾将 adapter 收到 DONE 后不继续读 EOF 的 `capture_complete=false` 错解为“回执不完整”；`1bb2720` 只修正汇总判定，保留原始 EOF 标记和回执。UNKNOWN 即使包含 DONE 也不能自动结算。

真实浏览器恢复 q2、q6 同一会话；从资料页查看 q2 PDF 原件，下载 SHA 与冻结 SHA 相同；页面、历史顺序、答案及 completed 一致。刷新 / 查看来源前后 Provider 调用数均为 **78**，没有再次生成。最新公开回答没有 `<ref>`、内部来源句柄或检索图片，仍保留真实文件名及受权原件回读。

已修复的局部问题：

- 历史行被压缩断字：固定最小行高、收缩约束、单行截断；鼠标及 Enter 保留。
- 检索范围移回输入区域，资料页上传 / 滚动布局恢复；未重设计整个产品。
- 原始图片回读增加严格版本与资产身份校验：当前合法文档版本、资产清单、SHA / MIME / 存储边界；原件下载路径不变。
- 关闭公开引用时，对可确认的内部来源协议做局部处理，不全局删除普通 `c1/d1` 字符串，不补造来源。
- 取消、截断、空答案、失败上传 / SSE 状态使用既有离线回执与模拟 Provider 验证，不为验收制造新 UNKNOWN。

## 6. 实际调用与 UNKNOWN

| 范围 | 调用 / 用量 |
|---|---|
| 最新设置阶段 | 42 次，全部 RESPONSE_RECEIVED；**29,947 Token**，新增 UNKNOWN 0 |
| 其中 6 次普通完整问答 | 34 次 Provider；24,758 Token |
| 其中 1 次开启思考的完整问答 | 8 次 Provider；5,189 Token |
| 整个 S3 | **78 次 Provider**，其中 74 次已结算、4 次旧终态 UNKNOWN |
| 已结算实际用量 | input 46,177 / output 3,068 / total **49,245** |
| 4 次 UNKNOWN 已观察下界 | 11,213 + 10,686 + 10,212 + 15,827 = **47,938**，不是最终结算值 |
| 总已观察下界 | **至少 97,183 Token**；最终总量仍未知，不估算现金费用 |

4 次较早失败均是重复思考、没有正文后正常取消，不将其说成已证明的供应商超时。分别持续约 404.32、323.52、401.05、182.93 秒，全部首次失败和授权停止记录保留。最新改动没有修改旧 UNKNOWN；更早项目历史 UNKNOWN 另行保留，未计作本 S3 新增值。

## 7. 实际测试、命令与退出码

只运行受影响测试，完整命令和历史失败见 results JSON；未运行全仓、六套数据集或阈值重放。

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest -q tests/weknora_port/test_s3_answer_prompt.py tests/weknora_port/test_generation_protocol.py tests/weknora_port/test_references.py tests/weknora_port/test_generation.py tests/weknora_port/test_conversation_configuration.py --tb=short
# exit 0：133 passed（撤回应用思考预算后）

& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest -q tests/weknora_port/test_s3_answer_prompt.py tests/weknora_port/test_generation_protocol.py tests/weknora_port/test_references.py tests/weknora_port/test_generation.py --tb=short
# exit 0：115 passed（当前语言说明增强后）

& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest -q tests/weknora_port/test_s3_evidence_summary.py --tb=short
# exit 0：3 passed

npm --prefix frontend test -- adapter.spec.ts -g "POST once and recover|answer language defaults" --output=E:/WK/run/s3-product-20261019/ui-answer-language-test
# exit 0：2 passed（模拟 API 的真实测试浏览器；不是云模型证明）

npm --prefix frontend run build
# exit 0：TypeScript / Vite build passed
```

先前相关验证：后端 109 项、来源 / 文档 / HTTP / 生成 154 项、原 UI 20 项分别通过。新资料页测试首次定位器匹配两个标题，退出 1，修正后单测退出 0；当前语言后缀首次使旧测试断言失效，修正断言后上述 115 项退出 0。没有把首次失败抹掉或把不同测试批次相加为独立覆盖总数。

标准运行命令如下，check / start / status / stop 已实际执行，退出 0；结束时已恢复 cloud=false。启动不发送模型请求。

```powershell
.\wk.ps1 check -Config 'E:\WK\run\s3-product-20261019\local.json'
.\wk.ps1 start -Config 'E:\WK\run\s3-product-20261019\local.json'
.\wk.ps1 status -Config 'E:\WK\run\s3-product-20261019\local.json'
.\wk.ps1 stop -Config 'E:\WK\run\s3-product-20261019\local.json'
```

最终报告生成命令：`& 'E:\RAG quention\.venv\Scripts\python.exe' -B 'E:\WK\run\s3-product-20261019\prepare_delivery_settings.py'`，退出 0，输出 78 调用、74 已结算、4 UNKNOWN、413 个归档文件。JSON 分母 / 调用重算及 ZIP 中 412 项 SHA 清单校验退出 0；原有 18 文件 SHA 校验退出 0，变化数 0。`git diff --check`、`git diff --cached --check` 均实际执行，退出 0。**未实现的命令不得报告通过。**

## 8. 修改范围、留项与收口

生产文件集中在 `frontend/src/weknora_port/{WeKnoraPythonApp.tsx,styles.css,api.ts,sse.ts}`、`backend/weknora_port/{conversation_configuration.py,conversation_assets/,generation/context.py,documents.py,http_api.py,references/,evidence_capture.py,development.py,runtime.py,execution.py}`。增加 / 调整对应定向测试及 `scripts/s3_{product_check,sample_documents,evidence_summary}.py`。没有改默认模型、Rerank 阈值、排序、DYNAMIC 评分、数据库 Schema 或旧索引。

尚未解决，且没有作为 PASS 的能力：

1. PDF 日期空格和错误 Summary 引起错误月份；这是解析 / 派生信息及生成质量缺口，建议下一阶段先修日期结构与原文一致性。
2. unit / site 中文措辞及无依据的设备类型具体化；保留真实生成失败，不通过自动补引或语义裁判掩盖。
3. PPTX 图片结构化页码、原始 MinerU middle 侧车持久化、类型化 TableRowProof 尚不完整。
4. 未新增同名重传版本、处理中重启 Worker 的真实实验，复用既有离线测试；不宣称这些真实异常全覆盖。
5. 五种语言的选项、持久化、请求头通过测试；本轮新增真实问答只验证简中，且较早 q3 英文失败保留。
6. Smart / Agent UI 未启用；一次正常 API 思考成功只解决本次受控回归，不代表长期思考稳定性或全部多模态精度。

阶段要求的上传 → 持久化 Worker → OCR / Caption → 向量索引 → 正常检索 / Context → 回答 → 有效来源查看 → 刷新历史闭环已取得真实证据。无新引入的租户、来源版本或账本完整性缺陷。S3 在此限定范围收口；后续质量问题不扩展为本阶段第二轮全量评测。
