# 任务三专项审查：真实工具与公开语料评测（只读盘点 + 无外发分层执行）

- 审查者：本轮执行者。基线 HEAD：`ee5919f`（任务二提交）。
- **更正（2026-10-15，Owner 指令）**：本报告早期版本把**测试夹具**角色（`siliconflow Qwen/Qwen3-VL-32B-Instruct`）当作正式 VLM 选择，**这是错的**。正式五角色以 Owner 方案为准：**vision 与 expensive 同为 `deepseek` / `deepseek-flash`**（来源：`deploy/service-policy.example.json` 策略 `owner-20261011-deepseek-normal-v1`（roles=[chat_expensive,vision]，limit=1000000）、`docs/model-provider-registry.md`、`backend/weknora_port/generation/router.py:6`、`deploy/clean-slate/models.json`）。模型 ID 来源全表见 `docs/weknora-port/COMPLETION_STATUS_CORRECTION_20261015.md`。
- 结论（更正后）：**层 1（真实 Office 转换）PASS；层 2（真实本地 Ollama）PASS（本地、零外发）；层 3 云端真实模型、层 4–6（真实最小问答闭环／三模式对照／准确性指标）BLOCKED 且不作任何准确率声明。本任务只在隔离/模拟层完成，真实问答与评测仍未完成。**
- 全程`real_provider_calls=0`；未下载任何大模型、未重建环境、未上传新私有数据。

## 1. 层 0：只读盘点（事实与出处）

**Office／转换工具**
- 端口摄取实现：`backend/weknora_port/ingestion/core.py`（PDF 路径 L292-330；DOCX 优先 `upstream_docx_parser`，失败回退 MarkItDown，L381-429）、`upstream_doc_parser.py`（`.doc`→`soffice`/`antiword`，L241-294）、`upstream_ppt_convert.py`（`.ppt`→`soffice` L38-94）、`upstream_excel_parser.py`（`.ods`→`soffice` L193-220）。
- 本机外部转换器**均未安装**：`soffice` / `libreoffice` / `pandoc` / `antiword` 均 NOT FOUND；`C:\Program Files\LibreOffice\program\soffice.exe` 不存在。→ 依赖 soffice/antiword 的 `.doc/.ppt/.ods` 路径不可用。
- OOXML 由 `E:\WK\deps\reader` 捆绑 Python 库处理（manifest 见 `E:\WK\deps\office-manifest.json`：mammoth/markitdown/python-pptx/pandas…），无需外部二进制。

**本地 Ollama**（`E:\ollama\ollama.exe`）
- 版本 `0.34.4`；API `http://127.0.0.1:11434`；`/api/version` 200。
- 已安装模型：`qwen3.5:4b`（3.4 GB）、`bge-m3:latest`（1.2 GB）、`ornith-1.5:9b`（6.6 GB）。
- `ornith-1.5:9b`：qwen35 / 9.0B / ctx 262144 / 量化 Q4_K_M / 能力 tools,thinking,completion,vision。
- 端口本地适配器：`backend/weknora_port/models/local_ollama.py`（`LocalChatService`、`local_connection_config()` 改写为 `api="ollama"`）。
- 授权依据：`docs/adr/ADR-001-model-capabilities.md`（L1 Chat=`ornith-1.5:9b`，Embedding=`bge-m3:latest`/1024，`OLLAMA_NO_CLOUD=1`）；`ADR-004`（默认本地 Chat=`qwen3.5:4b`）。属本地、无外发。

**模型配置**
- 角色存储：`storage/configuration.py` L49-107（`port_model_configuration`：embedding/rerank/cheap/expensive/vlm）；解析：`model_registry.py` L36-66（`SourceCatalogue.resolve`，支持 local/remote source）。
- **正式角色**（来源：`docs/model-provider-registry.md`、`deploy/clean-slate/models.json`、`deploy/service-policy.example.json`、`generation/router.py:6`）：embedding siliconflow `BAAI/bge-m3`；rerank siliconflow `BAAI/bge-reranker-v2-m3`；cheap siliconflow `XingChenAGI/Xing4.0-29B`；expensive **deepseek `deepseek-flash`**；vision **deepseek `deepseek-flash`**（与 expensive 共用 1,000,000/月）。`docs/RUNNING.md` L13 为历史 Go 服务记录且一致；测试夹具 `test_authority_cli_pg.py` 的 vlm 值为**夹具专用，非正式选择**。
- **本机无本地 rerank 模型**，故配置的 rerank/cheap/expensive 角色只能走云端。

**公开语料与 Gold**
- 仓库内冻结小集：`evaluations/core.jsonl`（20 题）、`evaluations/quality_v1.jsonl`（9 题）、`evaluations/incremental.jsonl`（2）、`evaluations/trust_v1/locked.jsonl`。字段含 `question`/`expected_chunk_ids`/`answer_points`/`kb_scope`/`expected_sources`/`dataset_version`（与 AGENTS.md 冻结 Schema 一致）。
- 公共基准适配器：`eval_center/public_data.py`（`scifact`/`miracl-zh`/`longbench-zh`，数据根 `D:\RAG-Public-Bench`，实测存在）。
- 六数据集冻结队列（历史“40 题＋24 页”来源）：`docs/weknora-port/P7_FROZEN_COHORT_20261014.md` 指明 `source_manifest_40.json` SHA `05a11e2d…`，含 SciFact 5183 + MIRACL-ZH 6000 + LongBench 400 + Hotpot 73 + 3 MML PDF + **24 Omni 原始页**，QA 共 **40**；其模型质量当时明确 `NOT_RUN_SIMULATED_MODELS`。
- 该冻结 manifest（SHA `05a11e2d…`）在本次可访问根目录（`E:\WK`、`E:\RAG quention`、`E:\RAG-Tests`、`D:\RAG-Public-Bench`）内**未定位到**（`E:\RAG quention\var\reports\…` 下同名文件哈希均不等于 `05a11e2d…`）→ 只读队列复验 **NOT RUN**，属既有产物路径缺口，非本轮新失败。

## 2. 层 1：真实 Office 转换与正式摄取路径 — PASS

命令（cwd=工作树）：
`python -B docs/weknora-port/evidence/AUTHORITY_TOOLS_EVAL_20261015/run-accept.py task3-office-real --pg-env tests/weknora_port/test_office_entry_pg.py tests/weknora_port/test_excel_source_pg.py`
**退出码 0，2 passed（28.3s）**。

- `test_office_entry_pg.py`：用真实 python-docx/python-pptx 合成 `office.docx`/`slides.pptx` + `table.csv`，经真实摄取入库；证据重定向到 `…/task3-office-real.redirects/P2_OFFICE_NATIVE_ENTRY_20261014.json`（含 mammoth/markitdown/python-pptx/pandas 版本）。
- `test_excel_source_pg.py`：真实 XLSX（含 sharedStrings 边界）经 ExcelParser + 原生上传/索引；证据 `…/P2_EXCEL_SOURCE_ENTRY_20261014.json`。
- **限制**：`.doc/.ppt/.ods`（需 soffice）与 `.doc`（需 antiword）本轮 **NOT RUN**（本机无该二进制）。

## 3. 层 2：真实本地 Ollama — PASS（本地、零外发）

命令：`python -B scripts/model_probe.py --chat-model qwen3.5:4b --embedding-model bge-m3:latest --report …/task3-local-ollama-probe.json`
**退出码 0**，报告 `status=PASS`、`cloud_enabled=false`、`local_only=true`（`…/task3-local-ollama-probe.json`）：

| 检查 | 结果 |
|---|---|
| `ollama list` | exit 0；三模型均在位 |
| `ollama show qwen3.5:4b` | qwen35 / 4.7B / ctx 262144；能力 completion,vision,tools,thinking |
| `ollama show bge-m3:latest` | bert / 566.70M；embedding length **1024** |
| `/api/version` | 200（0.34.4），35.1 ms |
| structured chat（think=false, format=json, num_predict=64） | 200，**9395.5 ms**，内容 `{"answer":1,"ok":true}`，`done_reason=stop` |
| embedding（bge-m3） | 200，**3413.7 ms**，维度 **1024** |

`ornith-1.5:9b` 元数据另存 `…/task3-ollama-show-ornith.txt`。**未发起任何云端调用。**

## 4. 层 3：云端真实模型 — BLOCKED（待批准清单）

本交接**不新增付费调用授权**，故云端真实调用 **NOT RUN**。继续执行了无外发工作（层 1/2 与 Gold 校验）。待批准的模型／数据／上限：

| 角色 | provider / model | 用途 | 待批准最大 Token |
|---|---|---|---|
| embedding | siliconflow `BAAI/bge-m3` | 入库/检索向量 | 与月限共用 |
| rerank | siliconflow `BAAI/bge-reranker-v2-m3` | 混合检索重排 | 同上 |
| cheap | siliconflow `XingChenAGI/Xing4.0-29B` | 动态路由低档 | 同上 |
| expensive（chat_expensive） | deepseek `deepseek-flash` | 动态路由高档 | **1,000,000/月（与 vision 共用）** |
| vision（vlm） | **deepseek `deepseek-flash`** | 图像 OCR/Caption | **与 expensive 共用 1,000,000/月** |

正式角色来源：`deploy/service-policy.example.json`（`owner-20261011-deepseek-normal-v1`: deepseek/`deepseek-flash`, roles=[chat_expensive,vision], limit=1000000）、`docs/model-provider-registry.md`、`deploy/clean-slate/models.json`、`generation/router.py:6`、`models/catalog_resources/models.generated.json`（`deepseek-flash` = "DeepSeek V4.1 Flash"）。**测试夹具与隔离库中的 Qwen VLM 不是正式选择。**

- 月度上限：`limit_tokens = 1,000,000`（任务一 authority 配置固定值）；单调用上界：`input_limit=200000`、`output_limit=8192`（合成配置值，**真实供应商语义未核实**）。
- 待批准数据：冻结公开语料（SciFact 5183 / MIRACL-ZH 6000 / LongBench 400 / Hotpot 73 / MML 3 / Omni 24 页）与 40 题 Gold；仓库内 `evaluations/core.jsonl`（20）+ `quality_v1.jsonl`（9）。
- 批准入口约束：`Mode=approved` 在本实现中仍抛 `APPROVED_METER_UNIMPLEMENTED`（沿用任务一、二结论），真实启用保持拒绝。

## 5. 层 4–6：真实最小问答闭环／三模式对照／准确性指标 — BLOCKED

- **真实最小问答闭环**：需要配置的 embedding/rerank/chat 角色。embedding 可走本地 `bge-m3`，L1 chat 可为本地候选，但 **本机无本地 rerank 模型**、且廉价/昂贵 chat 角色为云端 → 无授权时无法组成端到端真实 QA 闭环。本轮仅完成本地 L1 chat/embedding 能力验证（层 2）。
- **Cheap Only／Dynamic／Expensive Only 对照**：三档依赖云端 `Xing4.0-29B` / `deepseek-flash` → **NOT RUN**。
- **解析/检索/回答/引用分 Gold 指标**：无真实模型运行 → **不做任何准确率或路由质量声明**。
- Gold 类型（只读核对，可支撑何种指标）：
  - 检索 Gold：`expected_chunk_ids` / `expected_sources`（可算解析/检索指标）。
  - 回答 Gold：`answer_points`（可算回答要点覆盖）；quality_v1 另有 `expected_outcome∈{full,partial,refuse}` + `expected_target_count`（可算拒答/目标数）。
  - 无答案 Gold 的样本不得外推为“官方问答准确率”。
- 离线 Gold 模式校验（无模型调用）PASS：
  `python -B scripts/validate_eval.py --path evaluations/core.jsonl --schema core-v1` → exit 0，20 记录 PASS；
  `… --path evaluations/quality_v1.jsonl --schema quality-v1` → exit 0，9 记录 PASS。
  证据：`…/task3-gold-core.schema.json`、`…/task3-gold-quality.schema.json`。

## 6. 保留的 NOT RUN / 缺口（不得当作通过）

1. `.doc/.ppt/.ods` 经 soffice、`.doc` 经 antiword 的真实转换：NOT RUN（本机无二进制）。
2. 六数据集只读队列复验（`scripts/weknora_port_six_cohort_verify.py`）：NOT RUN（未定位冻结 `source_manifest_40.json` SHA `05a11e2d…`）。
3. 云端真实模型、Cheap/Dynamic/Expensive 对照、准确性指标：NOT RUN / BLOCKED（无授权）。
4. `test_office_legacy_pg.py`（`redis_port=56425`）在当前环境无法连接（无该端口实例），未纳入。
5. 未采集真实供应商延迟／计费；本地延迟仅一次冷/热样例，不构成 SLO。

## 7. 被测/证据哈希

| 项 | 值 |
|---|---|
| `scripts/model_probe.py`、`scripts/validate_eval.py`、`tests/weknora_port/test_office_entry_pg.py`、`tests/weknora_port/test_excel_source_pg.py` | 见 `evidence/AUTHORITY_TOOLS_EVAL_20261015/task3-office-real.command.json` 与各 `.stdout.txt` |
| 层 2 原始报告 | `evidence/AUTHORITY_TOOLS_EVAL_20261015/task3-local-ollama-probe.json` |
| 上游对照 | `3e8b0bfc80b845b2d4b2ed683994748741450a97`（未改） |

## 8. 交付与下一步

- 本项只提交：`docs/weknora-port/evidence/AUTHORITY_TOOLS_EVAL_20261015/` 与本报告；未 Push、未合并、未打 Tag。
- 下一步（需 Owner 授权后）：批准上表云端模型/数据/Token 上限 → 复现真实最小问答闭环 → 冻结 Gold 三模式对照 → 分 Gold 报告解析/检索/回答/引用指标。
