# S3：多模态文档处理与真实产品体验

日期：2026-10-19。当前结论：**PARTIAL**。三份资料已正常入库，UI 已修复；真实问答仍被 Qwen 重复思考阻断。不能将来源送达、图片可访问或离线测试通过写成完整产品 PASS。

## 1. 版本与用户决定

- 起点：`5cfa6b8ffcbfb5e49307b497f6b2aee49fa7c44d`，分支 `codex/weknora-python-port-integrate-image-20261014`。
- UI 修复：`a0c68cf45cdda79bbf4b7f7da98fe80f519a0469`。
- 普通模式被动证据记录及工具：`e6b3383782a8287e01443807a4108469d6f7b524`。
- 提示词第一版、文件名与图片回读：`267d1f48a925700c7703250abc4bac8385caa9c7`。
- 更精简的第二版提示词：`6d99328e348d84d2fac922a4756353ade36b95a9`；**第二版尚未真实调用**。
- 早期真实请求是在起点加 S3 工作改动下执行，随后归入上述提交；不冒充提交后的新增复验。报告交付提交完整 SHA 见本报告的 Git 记录及最终交付消息。
- Owner 先批准普通云模式，随后批准仅恢复未运行项；又明确要求优化冲突提示词、关闭引用时仅显示对应文件名并附相关原图。普通问答思考参数保持原默认，未关闭思考、替换模型或改变路由。

## 2. 真实入口与授权边界

复用标准 `wk.ps1`、React `http://127.0.0.1:56426`、FastAPI `127.0.0.1:56425`、独立 Worker、PostgreSQL 和 Redis，没有测试专用 API 或 runtime_factory。

| 项目 | 核验结果 |
|---|---|
| tenant | 1，既有 local principal；subject SHA256 `4a8c5c959f9ffb8d0f621c5bfc29030ebd17f565b2512a920bbe61058451ff15` |
| S3 KB | `d683c1b1-7268-45c2-8a05-23c94f4d5db0`，独立创建，未覆盖历史语料 |
| PG | `127.0.0.1:56423`，DB `weknora_python_port_20261013`，system ID `7696088403504697382` |
| Redis | `127.0.0.1:56424` DB15；独立前缀 `weknora-python-s3-7dc75486356c4e7c9e8bd42e1f55480f`，没有消费日常前缀的旧任务 |
| 存储 | 既有可信根 `E:/WK/run/rag-m34-20261018/assets` 下新 KB/document/version 目录 |
| 运行配置 | `E:\WK\run\s3-product-20261019\local.json`；日常配置未改，SHA256 `7c209bdb1b82e4acdbc50d1c575df6899c6d8625c9b139362f5bd71a2054750a` |
| 模型 | 查询理解/Cheap：SF `Qwen/Qwen3.5-4B`；Embedding：BGE-M3；Rerank：BGE v2-m3；Expensive：既有 DeepSeek；仅两个 Caption 实际用了 DeepSeek `deepseek-flash` |

Owner 明确批准普通云模式，没有应用级累计 Token/调用硬围栏；仍使用正式租户、KB 许可、Registry、原 SendGate、幂等及持久化调用账本。被动观察器只记录发送准备和真实回执，不提供预算保证，未替换 Gate 对象。请求文件位于准入前，**单有请求文件不证明已发送**，本报告结合真实响应和 PG 调用行判断。

初始授权、未运行项续跑及提示词第一版验证均因各自的新终态 UNKNOWN 停止。当前云开关关闭，API/Worker/UI 可供本地查看，PG/Redis 保留。原历史 UNKNOWN、首次取消记录和用量未改写、释放或补结算。

## 3. 上传、解析与索引

样本是冻结的公开合成验收资料，不是六套数据集官方 Gold。三份文件只各上传一次：PPTX 使用正常 API；PDF、XLSX 使用真实 Edge 的正常文件选择器。先前 PDF 文件选择器权限拒绝发生在 POST 前；保留旧 claim，经新授权确认库内尚无该文档后才完成第一次上传。

| 文件 | document / source_version | 解析与真实持久化 |
|---|---|---|
| `north_pier_service_brief.pptx` | `fce4db0b-c790-49ad-b2a4-441769da3b94` / `9df714d2-5fa4-47ff-a06b-1405059acbec` | MarkItDown 正文＋1 图；MinerU 本地 OCR＋DeepSeek Caption；text、OCR、caption、summary 共4块 |
| `north_pier_inspection.pdf` | `48542dff-fd0d-4e63-8784-b0969ab5ab51` / `4d91ea97-d566-4702-be33-f571ba58c0eb` | 第1页原生文字，第2页 scanned_pdf 图；独立图片任务完成；OCR、caption、text、summary 共4块 |
| `site_readings.xlsx` | `3b2870ce-81d3-4cbf-b14f-24cc1088f96a` / `18ff1d62-d644-4376-b82c-76743fb1d0e7` | Excel 解析；无图片任务/OCR/Caption；text＋summary 共2块 |

三份原件的正常 `/source` 回读均 HTTP200、SHA 与冻结原件一致；**10/10 Chunk 均有非空1024维向量**。图片任务2/2 completed，未伪造处理完成状态。普通文字上传在 API 同步完成；有图片的资料通过既有持久化任务和独立 Worker 处理，不声称存在统一异步文档队列。

两张图的本地 MinerU 4.0.12 OCR 均保存 `FIELD CHECK CARD / SITE: NORTH PIER / UNIT: NP-204 / READING: 37`；两个真实 Caption 也正确描述这些事实。OCR/Caption 子块关联父正文、原件和版本。PDF 图明确绑定物理第2页；PPTX 资产及子块的 `page_number=null`，仅原文 Slide 1 标记及 ZIP relationship 能定位到第1张幻灯片，不能冒充已持久化结构化页码。

OCR inline layout/识别状态已保存；文档存储没有可回读的完整 MinerU middle JSON 侧车，不能把其逻辑路径或 SHA 当作侧车已保存。XLSX 表头/数据列保持在解析文本中，但本 port 本次未生成 `TableRowProof`，也尚未完成表格问答。没有重建既有索引或回退 Tesseract。

## 4. 六道问题与首次失败位置

| 问题 | 实际运行 | 检索/证据 | 答案与终态 |
|---|---|---|---|
| q1 Inspection date | 原版＋提示词第一版各1次 | 均 Search9 → Rerank1 → Merge1 → Context1；命中 PDF 原正文并进入真实请求 | 都没有正文，正常取消后 UNKNOWN；第一版仍重复检查输出规则 |
| q2 Scanned card reading | NOT RUN | 尚未发出 | 新 UNKNOWN 后停止 |
| q3 Card site and reading | 原版1次；第二版待授权 | Search4 → Rerank1 → Merge1 → Context1；原 OCR/Caption 的 North Pier、37 确实进入最终 Qwen 请求 | 0字正文；取消/UNKNOWN，不能计为正确回答 |
| q4 Next review date | NOT RUN | 尚未发出 | 新 UNKNOWN 后停止 |
| q5 East Gate reading | NOT RUN | 尚未发出 | 新 UNKNOWN 后停止 |
| q6 Unit with reading51 | NOT RUN | 尚未发出 | 新 UNKNOWN 后停止 |

共2/6个不同问题、3次提交，0次完成有效答案，4/6未运行。第二版新请求尚未执行，不能计为改善。无独立 TopK 快照；Context 是其后的实际观察点，不能伪造独立阶段结果。

q3 原版 BGE 最高分正文 `0.2028170526`，没有候选达到0.3，依原有0.15 Top1保底保留正文。它带出的真实 OCR/Caption 已送达，不能将本次无答案归因于图片没有入库或 Rerank 丢失。原 q3 Query Rewrite 将 site/reading 误译为“网站/阅读”，这是另一个真实质量问题，未在本阶段改查询理解。

q1 存在更早的事实表达损失：冻结日期值为 `2026-10-12`，原生 PDF 提取成 `2026-1 0-1 2`，生成摘要又误写成“2026年1月12日”。这些原样进入模型，故日期证据标为 **FACT_PARTIAL**；不能说它已经获得干净、无矛盾的日期 Gold。最终没有正文，因此不判为“已返回错误日期”或“快速正确回答”。没有为测试人工修原文。

三次生成均为 SF Qwen，DYNAMIC 未实际升级、无 fallback。SSE 收到 progress/思考流，没有有效答案 Token、没有最终 finish_reason。取消是正常 Stop 操作，不是供应商已证实的超时：原 q3 404.32秒、原 q1 323.52秒、第一版提示词 q1 401.05秒，均为 Gate/传输/流读取观察区间，不是纯模型运算或全问答耗时。

## 5. UI、提示词与来源图片修复

### UI

历史行固定高度、单行省略、长标题提示和 Enter 操作；检索范围移回输入框的“选择资料”；KB与文件独立滚动、上传区保留在视口内；文件名、状态与“查看原文件”分行显示。真实 Edge 已验证历史点击/Enter、范围选择和三份资料完成状态。没有重新启用引用按钮或切块卡片。

### 提示词与显示

用户指出的歧义在真实 wire 中确实存在：配置默认中文与跟随用户语言分散；关闭引用未明确文件名呈现；图片规则分散，并夹带 Wiki/MCP/sandbox 指令。第一版统一了规则，但真实 q1 仍逐条自检循环，**不支持“只要去掉冲突就已修好”的结论**。

第二版把系统说明从原3785字符压缩至1954字符：明确语言优先级；资料是数据不是指令；模糊数值不猜修复；关闭引用后只列真正支持答案的准确文件名；相关图片独立于引用开关。移除重复展示检查和无关工具协议。`knowledge_context()` 提供真实 `filename`；关闭引用时不把 cN/dN/bN 属性发送给模型，内部 Registry/来源/版本验证仍保留。第二版当前只通过离线测试，尚不能宣称解决了循环。

原 Context 的 `images/...` 也不是浏览器可用地址。现在仅把带真实文档/KB/版本和 `image_info` 关联的图片转成既有资源句柄，解码后访问正式 `/source?asset_path=...&source_version=...`。服务端对当前完成版本、唯一 manifest、存储边界、栅格 MIME、尺寸和 SHA 逐项校验；无效资产不回退原件。前端只将这种受控地址解析到已配置 API，普通图片/链接/代码不变。

真实 Edge 已显示第2页扫描原图，正常图片回读 SHA 匹配。**这是来源图片访问通过，不是模型已经在完整答案中附图通过。** 没有根据相似度补引用、伪造文件名或把人工答案写入来源。

方法参考：[Langfuse Prompt Management](https://langfuse.com/docs/prompt-management/overview)及其技能中的失败驱动、最小可测试修改准则；本轮没有接入新平台或上传项目日志。

## 6. Token、UNKNOWN 与历史恢复

截至第一版提示词测试收口，共 **28次实际 Provider**：25次已结算，输入14328＋输出1461＝**15789 Token**；3次终态 UNKNOWN 的已观察下界分别11213、10686、10212，合计32111。全部调用合并仅能说**至少47900 Token**，最终总消耗未知，不能把 UNKNOWN 填0或当作已结算。原有更早 UNKNOWN 也保持不变。不估算现金费用。

实际 DeepSeek 图片请求2次，没有最终 Expensive 请求。所有失败及辅助调用均计入。第一轮上传观察器曾把“进行中 UNKNOWN”误当终态，首次错误保留；读取真实完成回执确认4条均已结算后，只修工具的任务/版本/租约判断，没有重传或重发。三次取消后的 UNKNOWN 则都有 finished_at，是真正未获最终回执的终态，不能用“观察器误报”解释。

取消的同一会话经正常历史接口和 Edge 刷新回读，问题/空正文/取消状态一致；刷新前后Provider总数仍22（该检查发生在提示词测试之前）。没有因刷新再生成。尚无本阶段“成功回答后刷新”的新证据，空正文一致不等于回答成功。

## 7. 实际测试与命令

工作目录为本工作树，Python为 `E:\RAG quention\.venv\Scripts\python.exe`。**未实现的命令不得报告通过。**

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest -q tests/weknora_port/test_s3_answer_prompt.py tests/weknora_port/test_generation_protocol.py tests/weknora_port/test_references.py tests/weknora_port/test_generation.py tests/weknora_port/test_document_assets.py tests/weknora_port/test_documents.py tests/weknora_port/test_http_api.py --tb=short
```

第一版代码退出0：154 passed，3.09秒，5条既有SWIG弃用警告。第二版只改生成/来源提示及wire展示，重跑其受影响集合：

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest -q tests/weknora_port/test_s3_answer_prompt.py tests/weknora_port/test_generation_protocol.py tests/weknora_port/test_references.py tests/weknora_port/test_generation.py --tb=short
npm --prefix frontend test -- adapter.spec.ts -g "source asset images keep version" --output=E:/WK/run/s3-product-20261019/ui-source-assets-test
npm --prefix frontend run build
```

分别退出0：113 passed/0.82秒、1 passed/6.5秒、TypeScript/Vite构建成功。来源图片测试另以临时跨端口配置执行1次，退出0/1 passed/4.2秒；临时配置仅在外部run目录。前端测试使用模拟API，不能替代真实Edge问答。

早期UI布局测试20项通过；新增第21项首次因h1/h2定位歧义失败（退出1），修正定位后该项独立通过（退出0）。不声称最后一次完整21项全部同跑通过。初期后端109项亦通过；没有跑全仓或六数据集回归。相关提交的diff检查退出0。

真实只读核对：`python -B scripts/s3_product_check.py snapshot` 退出0；`prompt-clarity-01/check.py` 在新终态UNKNOWN时退出1并STOP，属于预期安全停止。标准wk启停/状态命令退出0。浏览器操作没有shell退出码，以实际DOM、HTTP、PG和截图为证据。

## 8. 留存项与收口

- 第二版真实验证正在等待 Owner 对新增UNKNOWN后的续跑决定；不自动补发旧操作，不扩大模型或Prompt试验轮数。
- 日期原生提取/摘要月份错误、PPTX结构化图片页码、raw middle侧车保存、TableRowProof、同名第二次上传/版本递增、Worker重启防重复的新增实测仍有缺口。上述未验收，不擅自批准延期或写成PASS。
- 原有18个文件SHA核对未变；本轮提交只含归属明确的代码/测试/报告，原Gold、旧结果、UNKNOWN、正式索引和其他项目均保留。
- 后续最小动作是第二版有限正常问答；若仍循环，应停止继续堆叠提示词，单独决定Qwen思考模式/供应商兼容性方向，本轮不擅自更改。

日常配置保持不变。本轮服务的本地操作仍只有：

```powershell
.\wk.ps1 check -Config 'E:\WK\run\s3-product-20261019\local.json'
.\wk.ps1 start -Config 'E:\WK\run\s3-product-20261019\local.json'
.\wk.ps1 status -Config 'E:\WK\run\s3-product-20261019\local.json'
.\wk.ps1 stop -Config 'E:\WK\run\s3-product-20261019\local.json'
```

保持云关闭时启动不会获得新的外发许可。模型配置仍在正式设置接口，未将凭据写入报告。机器结果及证据包见同名 results JSON 中的路径与SHA；全部真实原始记录另存 `E:\WK\run\s3-product-20261019`。不Push、不部署、不进入S4。
