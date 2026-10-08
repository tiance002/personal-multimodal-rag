# P4-Preflight — Embedding 输入完整性与 P3 真实索引验证

## P4_PRE-R4 当前结果（2026-10-08；下方历史报告保留）

**P4_PRE_PASS — 有界合成输入的工程链路检查通过，等待 Owner 复核。**
本轮入场/测试基线 `92b660bd3690b51e67350a62120a9f911df58344`，分支
`codex/local-first-rag-v1-20260930`。独立本地提交 SHA 见本轮 commit-receipt.json；
此 PASS 不表示任意长度服务端不截断、P7 质量达标或已获发布批准。
执行方式 MANUALLY_SUPERVISED_TRIAL；实际 serving model/effort UNKNOWN，未派发 Worker。
没有进入正式 P4。入场四项已知 Secret 修改保留并纳入本轮验收。

### 实际改动及输入证据

- `.gitignore`、`.dockerignore` 排除根 `.env.local`；`scripts/with_clean_slate.py`
  只将两个白名单 Provider Key 装入当前项目子进程，不更改 Windows 环境。
  DB、Storage、外发开关不能被该文件覆盖；`models.json` 仍只存变量名。
  `test_clean_slate_secret_config.py` 用 SIMULATED Key 验证日志、错误和 Git/Docker 排除。
- 新增 `backend/app/adapters/models/bge_tokenizer.py`，固定 BAAI/bge-m3 提交
  `5617a9f61b028005a4858fdac845db406aefb181` 的完整 tokenizer.json；
  17,098,108 字节、SHA-256
  `21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08`，
  与官方 HF revision API 的 LFS SHA/大小一致。无需权重或额外 tokenizer 文件。
  `pyproject.toml` 固定 `tokenizers==0.23.2`，已安装/import，实际 Python 3.13.0。
- 依 Owner 最新要求，tokenizer 和下载的上游元数据均从 C 盘移至
  `D:\RAG-ModelAssets\bge-m3\5617a9f61b028005a4858fdac845db406aefb181`。
  转移前后 SHA 相同；测试从 D 盘直接读取，C 盘下载副本已移除。
  hf-verified-metadata.json 是有效来源记录；hf-revision-metadata.json 的 null
  是旧失败尝试，不作为来源证据。config 下载 EOF 记录保留，不以之替代自包含 tokenizer。
- `EmbeddingAdmission.from_bge_m3_file` 是明确的有界准入，默认构造继续关闭。
  完整 ContextHeader + 原文、XLM-R `<s>`/`</s>` 均精确计数；关闭 tokenizer
  truncation/padding，原文不改写。官方 8192 上限，客户端上限 7680，预留
  512（6.25%）作为本地保守余量；该余量不是实测供应商漂移上界。
  非匹配哈希、未知计数器、超限、缺 Key/权限/预算在传输前拒绝。
  `non_truncating_verified` 对此路径仍为 False，供应商内部行为 UNKNOWN。
  真实短输入计数一致是有限证据，不能证明供应商任意输入均不截断。

来源：[官方 tokenizer 固定文件](https://huggingface.co/BAAI/bge-m3/resolve/5617a9f61b028005a4858fdac845db406aefb181/tokenizer.json)、
[固定版本 metadata](https://huggingface.co/api/models/BAAI/bge-m3/revision/5617a9f61b028005a4858fdac845db406aefb181?blobs=true)、
[SiliconFlow Embedding 合同](https://api-docs.siliconflow.cn/docs/api/embeddings-post)、
[官方价格页](https://siliconflow.cn/pricing)。本轮核对标准 BAAI/bge-m3 免费，未使用 Pro 型号。
这些公开页面不等于账户结算凭证。

### 真实 SQL / 模型 / 清理

`scripts/verify_p4_cloud_embedding.py` 是显式 opt-in 的一次性合成入口。
先检查固定 Clean-slate 身份（库名、OID 21278、cluster 7691227493754040358、
端口 25438、0017_embedding_profile_identity、Storage 和 P3 identity），
24 表入场均 0、无其他客户端。真实 worker `run_once`、租约/heartbeat、Parser、
P3 prepare_document、Repository 写入和 pgvector cosine SQL 均执行，没有用 Fake 向量。
唯一临时允许角色为 embedding，其他角色关闭；产品全局开关保持 false。
先实际验证 adapter 禁用和 KB.cloud_allowed=false 均 0 请求、失败版本不激活，
再以短 embedding 探针确认账户模型可用，未访问其他带认证 API。

| 阶段 | 真实请求 | 本地/供应商 Token | 模型 HTTP 延迟 |
|---|---:|---:|---:|
| 短文本账户/模型探针 | 1 | 12 / 12 | 3577.82 ms |
| 合成文档摄取 | 1 | 1704 / 1704 | 3681.73 ms |
| Query Embedding | 1 | 12 / 12 | 1427.55 ms |
| 合计 | **3 / 10** | **1728 / 20000；供应商 1728** | 无自动重试 |

三次响应均成功；供应商 completion_tokens=0。DeepSeek/Chat/Rerank/Vision 请求均 0。
实际结算 **UNKNOWN**，未声称已核实零费用。

合成 Markdown 有中文标题层级、多段长正文、短表格行及独立短节。
1 Parent、8 Child、8 个真实 1024 维 Embedding；7 Child 关联同版本 Parent，
独立短 Child 合法 NULL parent_id。Parent 无向量、无关键词项。
8 个 Child 的真实 pgvector 命中集合精确一致；错误 Profile 和另一 KB 查询为空。
当前 Version 成功激活；index_identity、Provider/model/revision/fingerprint、
完整 source/normalized SHA、所有块 offset/quote/content SHA 均检查。
两条 Profile 为真实索引身份和独立的错误输入语义隔离反例，清理前均记录完整六列和 ID。
worker 总耗时 4027.07 ms，pgvector 查询 4.88 ms；无峰值资源或质量指标推断。

模型调用经真实 PostgresBudgetGate / BudgetUsageGuard：公开免费模型使用每请求
正数最小占用 1、此受控实例预算 10 个既有 micro-unit；这是审计占用，不是人民币
实扣估计或产品月度预算变更。10 请求、20000 Token 的独立上限在 adapter 和
发送前持久化 receipt 共同保留；live-attempt.lock 不允许重启入口获得新额度。

本次允许为真实 worker 可见性提交专属合成 KB/Document/Version 等，随后按登记
ID、名称、完整 Profile 身份及 FK 顺序精确清理，非测试业务对象删除 0。
最终 23 张业务表仍 0；model_calls **3** 条真实调用记录以 unknown 保留，各占用 1，
不因清理合成数据而重置费用占用。清理 PASS；两条 Profile、两份上传/KB 和全部
Child/Parent/terms 已清理。历史库没有连接或读写，旧库前后计数 NOT RUN；
未将此表述为独立排除外部消费者写入。公开数据集未读写。测试 CAS 在仓库 ignored
var/reports/p4-pre-r4/synthetic-storage，未改变配置的 D 盘业务 Storage。

### 实际验证及失败保留

| 命令/轮次 | 退出码 | 实际结果 |
|---|---:|---|
| `python -B var/reports/p4-pre-r4/run_checks.py admission-first` | 1 | 88 PASS / 3 FAIL；边界生成器尾部空格额外产生 Token |
| `python -B var/reports/p4-pre-r4/run_checks.py targeted-final` | 0 | **213 PASS / 0 FAIL / 0 SKIP**；REAL tokenizer，模型/传输 SIMULATED |
| `python -B var/reports/p4-pre-r4/run_checks.py postgres-final` | 0 | 31 PASS；REAL PG，模型 SIMULATED |
| `python -B var/reports/p4-pre-r4/run_checks.py postgres-final2` | 1 | 31 PASS / 1 FAIL；入口模拟演练的合成短节未成为独立一级节 |
| `python -B var/reports/p4-pre-r4/run_checks.py postgres-final3` | 0 | **32 PASS / 0 FAIL / 0 SKIP / 2 DESELECTED**；REAL PG / SIMULATED HTTP |
| `python -B scripts/verify_p4_cloud_embedding.py --tokenizer <D盘固定文件>` | 2 | 未给显式 opt-in，拒绝外发；非测试失败 |
| 同一入口加 `--allow-real-siliconflow` | 0 | **REAL SiliconFlow / PG PASS**；3 请求、精确清理 PASS |
| `python -B scripts/with_clean_slate.py --mode check`（前后） | 0 | REAL PG 只读身份检查 PASS；输出 model_calls=0 指此次检查调用数，不是表行数 |
| `git diff --check`；提交前默认 `git diff --cached --check` | 见 git-checks.json | 实际退出码与 diff 单独存档 |

所有 Python 命令真实可执行文件为项目 `.venv/Scripts/python.exe`，完整命令、
JUnit、日志及对应全源码 SHA 快照见本轮证据。未实现的命令不得报告通过。
第一轮失败只修正合成边界生成方式；第二轮只调整 synthetic_source 的标题层级。
均未放宽数值/引用断言、P2 原生 table proof 或 P3 参数/算法；所有原失败 XML 保留。
新增 PG 401/403/429、数量/维度错误、NaN/Inf 七反例均验证失败版本不激活、
无向量残留、仅一次请求且无 Ollama fallback。超限真实 tokenizer 反例在传输前拒绝，
没有向供应商发送超限内容。

2 项 DESELECTED 是未改动的 R3 已提交并发 Profile 用例；它们会写固定的 R3 证据路径，
本轮复用原同哈希代码的真实成功/精确清理证据，不覆盖旧轮次、不算本轮 PASS。
其余真实并发事务回滚用例仍实际执行。原四项 Caption 历史目录对照保持 NOT RUN。
无无关广泛回归；原 29 项历史失败及因果对照原文件保留并附 hash，不声称本轮重跑全绿。
历史 Migration 0001–0017、P3 算法、Repository、frozen PDF 等保护哈希与入场相同；
gold.json 仍 18157 字节、SHA-256 d44ff365b77800fab83b4604b26a53f9152fa8d810851e52f8cf93aae8836f79。

**依赖升级影响未独立验证**。UNKNOWN 云模型 revision 的漂移、供应商内部任意输入
截断行为、账户实际结算和普遍检索质量仍未证明。P4_PRE_PASS 仅适用于本轮有界工程链路。
完整证据同步到 `D:\RAG-CleanSlate\cs0_20261008t072656z_352f705b\reports\p4-pre-r4`，
索引为 docs/audits/p4-pre-r4-evidence.json；final-manifest.json、git-checks.json、
full.diff、commit-receipt.json 分别记录源码/资产 SHA、范围检查、实际差异和独立提交回执。
本轮允许本地独立 commit，不 Push、Merge、Tag；完成后停止，等待复核。

---

## 以下为历史阶段报告（当时状态及限制，不作为本轮结果）

**状态：`P4_PRE_PARTIAL`**
**基线：** `a41952f8dbbcaa568ed57974f7d896b0d64b5ac2` (`codex/local-first-rag-v1-20260930`)
**当前范围：** P4 开发前验证；未实现 Rerank、Parent 回捞、Merge 或 Router。

## 结果

已为 Ollama Embedding 请求显式设置 `truncate=false`，并在向量进入持久化前检查返回条目数、1024 维长度、数值类型、有限性及 PostgreSQL `float4` 可表示范围。输入正文与 ContextHeader 原样发送；Provider 拒绝后不做短路、缩短或重试。Chat `/api/chat`、Embedding Profile、fingerprint、调用统计和 P3 参数没有改动。

离线 Provider 合同和所选 P3/P1/P2 回归通过。当前机器的 Ollama 0.34.4 服务没有 `bge-m3:latest` 等已登记模型：loopback-only `/api/tags` 返回空清单；没有实际生成 Embedding。因此没有创建 P4 测试 KB，也没有执行真实摄取、向量写入或 pgvector 查询。当前判定为 `P4_PRE_PARTIAL`，不能据此批准 P4_PRE_PASS。

## Provider 与容量证据

项目设置的 Provider 是 `ollama`，配置模型标签为 `bge-m3:latest`，应用合同要求 1024 维。实际模型 digest、服务器返回的运行时模型、运行时 tokenizer、运行时输入上限和实际生成维度均为 **UNKNOWN**；记录 `MODEL_INPUT_LIMIT_UNKNOWN`。没有从 Chat 的 `num_ctx=8192` 或 P3 字符长度参数推导 Embedding 容量。

Ollama 官方 `/api/embed` 文档说明 `truncate` 默认为 `true`，设为 `false` 时超出 context window 应返回错误。BAAI 的上游 BGE-M3 model card 列出 1024 维和 8192 sequence length，tokenizer config 标注 XLM-Roberta、model_max_length 8192；这些是上游模型资料，不证明本机已安装的 Ollama artifact 与之相同或实际运行限制相同：

- Ollama API: <https://docs.ollama.com/api/embed>
- 上游 BGE-M3: <https://huggingface.co/BAAI/bge-m3>
- 上游 tokenizer config: <https://huggingface.co/BAAI/bge-m3/blob/main/tokenizer_config.json>

Ollama CLI 显示版本 `0.34.4`。普通启动失败并报告系统拒绝访问。经授权提升权限后启动的本轮实例先被发现监听通配 IPv6；在发出任何模型请求前停止该 PID，再以 `OLLAMA_HOST=127.0.0.1:11434` 启动。确认 listener 只绑定 loopback、`/api/tags` 为空后停止了本轮 PID `26712`。当前未遗留 Ollama listener。没有下载/更新模型，也没有发出 Chat 或 Embedding 请求。

## 代码与合同测试

修改位于 `backend/app/adapters/models/ollama.py` 和 `backend/tests/test_ollama_usage.py`。新增测试通过本地模拟 HTTP transport（SIMULATED），核对 `truncate=false`、多条输入的向量数、1024 维、ContextHeader 与原文完整传递、拒绝错误向量及 Provider 4xx 不会以缩短输入重试。模拟响应不作为真实模型证据。

| 检查 | 实际结果 |
|---|---|
| `pytest backend/tests/test_ollama_usage.py ...` 首轮 | FAIL，退出码 1：13 passed、1 failed。测试构造把两条向量用于一条请求输入，实际触发预期的 count mismatch；修正测试数据后重跑。 |
| Provider 合同最终轮（命令见下文） | PASS，退出码 0：16 passed，5 warnings；JUnit `provider-contract.xml`。 |
| P3/P1/P2 离线结构与证据回归（命令见下文） | PASS，退出码 0：83 passed，5 warnings；JUnit `offline-regression3.xml`。覆盖 Adaptive Chunking、长表格保护、Profile 隔离、P2 PDF 离线合同、摄取状态机及 Version/Source 合同；不代表真实模型或数据库向量验证。 |
| P3 PostgreSQL chunk strategy 回归 | PASS，退出码 0：1 passed，5 warnings；使用 profile 验证并强制指向 Clean-slate DB。该回归未注入 Embedding Provider，不是 P4 真实向量验证。 |

pytest 的首次临时目录失败和随后 `TEMP/TMP` 权限修复均保留于外部证据目录。定向回归最终通过时，`--basetemp`、`TEMP`、`TMP` 都位于本仓库忽略的 P4 临时目录。运行时会显示 `Failed to find real location of D:\Drivers\python\python.exe`，但上述成功命令均实际退出 0。

## Clean-slate 数据库与测试数据

必须的只读命令 `.venv\Scripts\python.exe -B scripts\with_clean_slate.py` 退出码 0，输出 `PASS`；数据库为 `rag_clean_dev_20261008t072656z_352f705b`，OID `21278`，cluster system identifier `7691227493754040358`，端口 `25438`，迁移 `0016_parent_child_chunks`。只读身份核对使用既有 profile/helper，没有自行拼装 DSN。

P3 PostgreSQL 回归前，检查到所核表均为 0；该测试创建并软删除了一个临时测试知识库。其对象 ID 已记录并经 FK/归属核对后，仅对这个测试对象按依赖顺序精确清理：

- Knowledge Base `133eee20-0c47-45e4-b31e-d5a02773bcfa`（`chunk-strategy-test_postgres_records_actual_p0`）
- Document `440cf844-40e3-4606-8cd5-836025cf144b`
- Version `07506115-360a-433d-948e-0ebd0c317f2b`
- Chunk `fdcbd571-875e-41f0-8031-02783fb389ab`
- Ingestion Job `1d155955-695e-4166-9870-19be773a631f`
- 2 Sections、6 Terms、0 Embeddings

第一次删除 KB 的尝试因真实 FK `NO ACTION` 被拒，事务回滚、数据未变；之后按已核实的单个测试 ID 删除其 Terms/Chunk/Sections/Job/Version/Document/KB，清理命令退出码 0。复查 23 张业务表全部为 0，迁移记录保留。P4 专用测试没有创建 KB、Document、Version、Chunk 或 Job。旧业务库和公开数据没有连接或写入；模型 API 调用数为 0。

## 未完成的真实验证

| 必需项 | 状态与影响 |
|---|---|
| 当前实际 Embedding provider/tag/digest/维度 | Provider 设置和标签可从配置确认；服务端模型清单为空，digest/实际维度 UNKNOWN。 |
| 中文、ContextHeader、代表性长内容及合法表格行实际请求 | NOT RUN；没有可用本地模型。不能证明本机服务支持该请求参数，也不能证明实际上限。 |
| 超限时本机 Provider 明确拒绝 | 离线 400 合同 PASS；真实超限拒绝 NOT RUN。 |
| 真实 Parser → Adaptive Chunking → 上传/摄取 | NOT RUN；没有创建 P4 KB。 |
| Parent/Child、SourceLocator、Version 和 Embedding Profile SQL 对照 | 仅 P3 结构回归及先前 CS0 migration/schema 证据可用；本轮未产生可核验的 P4 真实文档。 |
| Child-only Embedding 与 Parent 无向量 | NOT RUN。 |
| 用户查询 Embedding + 实际 pgvector scope/profile 查询 | NOT RUN。 |
| 广泛后端回归及历史 29 项集合对照 | NOT RUN；本轮只运行上述定向回归，不声称旧失败集合已复核。 |
| OCR/VLM、Chat Generation、云 API、正式 Benchmark | NOT RUN，且不在本任务授权范围。 |

没有模型或 GPU 资源使用；耗时仅记录在 pytest 日志，峰值内存/GPU 指标 **NOT MEASURED**。真实向量摄取和 pgvector smoke test 是进入 `P4_PRE_PASS` 的阻断项。服务恢复并确认已有模型后，最小续验是重新读取 Ollama tags/show 并保存 digest，再运行受控 P4 fixture 的真实 Embedding、摄取、SQL 对照和 pgvector 查询；不下载模型。

## 实际命令

```powershell
.venv\Scripts\python.exe -B scripts\with_clean_slate.py
.venv\Scripts\python.exe -B -m pytest backend/tests/test_ollama_usage.py -q --tb=short -p no:cacheprovider --junitxml=var/reports/p4-preflight/provider-contract.xml
.venv\Scripts\python.exe -B -m pytest backend/tests/test_p3_adaptive_chunking.py backend/tests/test_p3_chunking_review_fixes.py backend/tests/test_embedding_profile_isolation.py backend/tests/test_p2_pdf_offline_closure.py backend/tests/test_pdf_table_evidence.py backend/tests/test_ingestion_state_machine.py backend/tests/test_version_source_contract.py -q --tb=short -p no:cacheprovider --basetemp=var/reports/p4-preflight/pytest-offline-temp2 --junitxml=var/reports/p4-preflight/offline-regression3.xml
# P3 PostgreSQL strategy 测试由包装器先调用 resolve_environment/verify_database，再启动 pytest：
.venv\Scripts\python.exe -B var/reports/p4-preflight/run_postgres_regression.py
# 对该测试所建软删除记录按精确 ID 核验后清理：
.venv\Scripts\python.exe -B var/reports/p4-preflight/cleanup-postgres-regression.py
```

离线回归实际将 `TEMP`、`TMP` 指向 `var/reports/p4-preflight/os-temp`，并指定仓库内 `--basetemp`；数据库回归包装器将其进程环境中的 `RAG_DATABASE_URL` 解析为 Clean-slate profile，并在启动 pytest 前验证身份。

provider 首轮试跑 14 个用例时产生 1 个测试数据形状错误；定向回归在修正临时目录变量前分别有 41 个 fixture setup ERROR、以及 12 个临时文件权限导致的失败。保留日志以区分这些失败试跑与最终 `16/83/1` 通过结果。全量测试未运行。

## Git 与停止状态

只修改 Provider 和测试并新增本报告；`0014–0016`、P3 参数、Clean-slate profile 及其它业务实现未改。当前没有 P4 完整通过条件，按任务合同不创建提交；工作区改动等待审阅，本轮在此停止，不进入 P4。

---

## P4_PRE-R3 续验记录（2026-10-08，最新状态）

**最终结论：P4_PRE_BLOCKED。** 初步必要合同检查曾暂标 P4_PRE_R3_CODE_PASS / P4_PRE_PARTIAL；最终 Schema 复核发现新身份不能并存的唯一约束冲突，真实失败反例已确认，因此撤回 CODE_PASS，不执行提交。保留上文整个 Preflight 历史及原失败证据。本轮不 Push、Merge、Tag，不进入 P4。

### 入场、范围与执行身份

入场 HEAD `a41952f8dbbcaa568ed57974f7d896b0d64b5ac2`，分支 `codex/local-first-rag-v1-20260930`。原未提交三文件与旧 final-manifest 相符：Ollama Adapter、其测试原字节保持；报告仅在旧内容后追加。没有 reset、stash、checkout 或覆盖用户改动。

任务 id `P4_PRE-R3`，职责为主执行与证据汇总，单写入者，无子代理；请求/实际 serving model 和 effort 的可核实控制面证据为 UNKNOWN，不能用角色名或自报证明。执行性质 MANUALLY_SUPERVISED_TRIAL。可读写范围为本节所列 Provider/Ports、组合根、配置、Repository、最小调用边界、相关测试、模型配置示例与报告，证据写入 `var/reports/p4-pre-r3/`；未读 .env、auth、Key 值或私人语料，没有改全局设置/账本。使用 efficient-goal-execution 技能记录检查点并复用未失效证据。

### 实现与接线事实

| 角色 | Provider / 模型 | 状态与证据 |
|---|---|---|
| chat_cheap | siliconflow / XingChenAGI/Xing4.0-29B | CloudChat + ChatResult，Factory 可切换；离线请求、finish、usage 合同；未替换 Quick/Smart 产品回答链 |
| chat_expensive | deepseek / deepseek-flash | 同一 Chat port，独立 model_key/Guard/统计；原产品 DeepSeek AttemptGate 保留，不新增额度 |
| embedding | siliconflow / BAAI/bge-m3 | SiliconFlowEmbedding，摄取/查询共享配置实例；Profile provider 硬编码解除；默认拒绝外发 |
| rerank | siliconflow / BAAI/bge-reranker-v2-m3 | CandidateRanker.rank 兼容；RerankResult 保存分数、顺序与 usage；未接线生产 RRF/Context |
| vision | deepseek / deepseek-flash | CaptionProvider 合同，显式注入 CaptionEnricher；CaptionUsageGuard 保留；未真实调用 |

源文件与关键符号：`domain/model_registry.py::ModelSpec,ModelRegistry`；`adapters/models/factory.py::ProviderFactory`；`adapters/models/cloud.py::CloudChat,SiliconFlowEmbedding,SiliconFlowRerank,DeepSeekVision,EmbeddingAdmission`；`ports/providers.py::ChatResult,EmbeddingResult,CaptionResult`；`ports/ranking.py::RerankResult`；`application/provider_usage.py::BudgetUsageGuard`；`ports/model_access.py::model_access`；`bootstrap.py::build_container`。详细配置和生产/预留边界见 `docs/model-provider-registry.md` 与 `deploy/clean-slate/models.json`。

没有建立第二套重复 Port。ChatResult 是 AnswerProvider 的结构化扩展，Embedding/Caption/CandidateRanker 保持既有调用签名。无 Key 时不发请求；注册表启动前校验角色、维度、默认项、固定可信端点与 Key 环境变量名。一个角色可登记多个候选，同一模型可登记不同角色；模型实例独立统计。只做静态配置，不做动态路由或模型发现。

Query 的云 Embedding 在 `application/retrieval.py::HybridRetriever.retrieve` 中通过 Repository 的服务端 Scope 许可再发送；摄取在 `knowledge_repository.py::process_job` 中查实际 KB.cloud_allowed。仍保留 q0 keyword fallback。不会把缺向量或失败 Embedding 伪装为向量成功，失败候选不激活。

### 输入与身份合同

P3 General 512/80、Parent 4096、Child 384 及 index identity `p3:391208cb6fb98bbcd68552703cfa80d158973c46902eb9f321a1c1f02d88c041` 未变。P3 原子表格、行保护、offset、quote、SHA 与冻结 PDF 样本未改。完整 Header + Child 输入不缩短；SF 不发送 Ollama truncate=false。

官方 [Classic Embeddings API](https://docs.siliconflow.cn/docs/api/embeddings-post) 明确列出 BGE-M3 8192 tokens；[BAAI 模型卡](https://huggingface.co/BAAI/bge-m3) 列出 1024 维、8192 长度。当前未安装 tokenizers/transformers，未下载模型或 tokenizer；供应商默认不截断保证、实际批量总 token/条数上限仍 UNKNOWN。因此默认 admission 对全部输入（包括短文本）拒绝，错误为 EMBEDDING_CAPACITY_GUARANTEE_UNKNOWN。32 条 batch 是本地保护策略，不冒充供应商上限。离线边界 8192/8193 使用明确 SIMULATED token counter，不能作为真实容量证据。

七项身份由 `domain/embedding_identity.py::EmbeddingIdentity` 冻结：provider、model_id、resolved_revision_or_unknown、dimension、distance_metric、chunking_index_identity、embedding_input_semantics_version。Profile revision 写 UNKNOWN；完整身份生成 fingerprint。`get_embedding_profile_id` 只能解析当前有效模型；`vector_candidates` 同时校验 profile ID/fingerprint/provider/model、scope、active version、child role、P3 identity。旧四项 Ollama fingerprint 被隔离，不自动读取为新向量空间，不重建历史索引。

最小旧测试合同变更有明确依据：P3 模型 Double 补 model/dimensions；revision 断言由伪 local/P3 版本改为 UNKNOWN，原来源/行/表格/child-only 全部断言保留。PostgreSQL scope 测试改为每个 Repository 只查询自己的模型；增加跨模型 Profile 返回空断言，保留各自可检索断言。没有弱化数值、引用或旧版本保护。

UNKNOWN revision 无法主动识别同名模型的供应商漂移；本轮未制造版本号，也未声称解决该问题。后续须用经核实 revision、冻结漂移样本/结果或经批准的新输入语义身份建立新 Profile。既有 Migration（包括 0014–0016）全未改；现有字段能保存身份，但现有唯一约束不足以允许这些 Profile 并存，详见末尾 Schema 阻断；未实际执行迁移。

### 权限、用量与官方核对

职责开关独立，默认全部 false。Embedding 独立许可不会打开 Chat/Rerank/Vision；后三类还要求全局 cloud。KB 外发拒绝保持有效；没有关闭原云边界。URL 只允许两个官方固定 base URL，无系统代理/redirect、自动重试、缩短输入或 fallback；Key 仅运行时内存读取，正文和 Key 不进入 receipts。

预算使用既有 BudgetGate，按职责/model_key 独立 reserve；未知费用即使有供应商 token 也 mark_unknown 保留占用。UsageCapture 与 adapter receipts 区分 supplier tokens、计划 tokens、耗时、错误和 UNKNOWN 结算。Rerank meta.tokens 未映射为统一 usage，明确 UNKNOWN；不会宣称覆盖完整计费。现有 Quick 产品链仍使用原预算/AttemptGate，工厂 Chat 尚未替换该链，不能用它绕过原 DeepSeek 账本。

官方核对日期 2026-10-08：[SF Chat](https://docs.siliconflow.cn/docs/api/chat-completions-post)、[SF Rerank](https://docs.siliconflow.cn/docs/api/rerank-post)、[DeepSeek Vision](https://api-docs.deepseek.com/zh-cn/guides/vision/)、[DeepSeek 模型价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)、[SF 价格](https://siliconflow.cn/pricing)。SF 价格页当前列出 Xing4.0-29B、BAAI/bge-m3、bge-reranker-v2-m3 免费；账户权限未核实。DS Flash 支持图像，使用 user 图文块、内联 data URL、原字节图片 SHA；模型响应未报告 model 时记录 UNKNOWN，不伪造供应商报告身份。Caption 继续为 UNVERIFIED/model_generated_caption，不成为 OCR 或表格单元格证明。

本机运行时 SILICONFLOW_API_KEY=无、DEEPSEEK_API_KEY=无；只核实 presence，未读取值。真实 Embedding/Chat/Rerank/Vision/DeepSeek 请求全部 0，供应商 token/真实扣费 NOT RUN，无付费调用。真实 Embedding 还缺明确本轮有限外发批准、可核实 tokenizer/非截断合同和相应 Guard；不能只提供 Key 就解除输入拒绝。

### 真实数据库与模拟模型边界

`scripts/with_clean_slate.py --mode check` 退出 0：数据库 `rag_clean_dev_20261008t072656z_352f705b`，127.0.0.1:25438，OID 21278，system_identifier 7691227493754040358，migration 0016_parent_child_chunks。

`test_clean_slate_model_profiles.py` 在该固定实例运行 4 项真实 PostgreSQL/pgvector 检查，模型/HTTP 明确 SIMULATED。实际 Parser→P3→Repository 写入→pgvector cosine 查询经过真实 SQL；验证两模型 Profile 隔离、SF provider/revision/fingerprint、Child 向量/Parent 无向量、完整 Header 输入、source quote、KB 范围与禁止外发的失败候选不激活。P3 对无需细分的完整 Child 允许 NULL parent_id，其余 parent 链必须属于同版本实际 Parent。

这些数据在外层事务中全部 rollback；每项对 24 业务表前后计数相等。没有删除数据库/业务对象，不写旧业务库；测试 Storage 在仓库 var 临时目录，不写 D 盘 Clean-slate storage。真实 SF 模型摄取、query embedding、真实模型 pgvector 结果仍 NOT RUN，不能用上述模拟向量替代验收。

### 实际测试结果及失败保留

| 检查 | 实际结果 | 退出码 / 证据 |
|---|---|---|
| 首轮 Provider / Ollama / bootstrap | 78 PASS | 0；providers-first.xml/log |
| 第一轮扩展定向 | 372 PASS / 5 FAIL | 1；targeted-first.xml/log |
| 最终定向（当前源码） | 342 PASS / 0 FAIL / 0 SKIP | 0；targeted-final3.xml/log |
| Caption 独立来源合同 | 35 PASS / 4 DESELECTED | 0；caption-final.xml/log；4 项 NOT RUN，不能算 PASS |
| PG 首轮与第二轮 | 各 3 PASS / 1 FAIL | 1；postgres-first.xml、postgres-final.xml；测试假设错误原记录保留 |
| PG 最终 | 4 PASS | 0；postgres-final2.xml/log；REAL PG / SIMULATED model |
| 广泛最终离线 | 1224 PASS / 9 FAIL / 20 ERROR / 127 SKIP | 1；broad-final.xml/log |
| 历史失败对照 | 原 29 状态及原因全部一致，新增失败 0 | failure-baseline-comparison.json；只归一化 .py 行号 |

第一轮定向的 1 项失败来自 P3 Double 缺少模型元数据；补齐真实 port 合同后通过。另外 4 项 Caption 旧版对照用例在读取 RAG_CAPTION_A_BEFORE 时立即 KeyError，此环境前提本轮未提供；它们不是此前 29 项集合成员，没有改测试去伪造该历史目录，也不把 DESELECTED 算通过。其余 Caption 35 项真实运行通过。

PG 首轮将所有 Child 都要求有 Parent，和冻结 P3 的“无需细分则不造 Parent”实现冲突；按源码调整为所有 Child 有向量、实际 parent 链完整且范围内。第二轮 UUID 与字符串直接比较失败；只规范化比较类型，不更改 SQL 或生产返回合同。保留所有失败 XML/日志。最终 4 项通过，无生产缺陷被测试修改掩盖。

广泛回归复用 P3 有界文件列表，追加本轮 Provider/Ollama/DeepSeek safety 合同；四类外发/Tracing 禁用，移除 Provider Key，数据库 URL 为不可用的本机端口 1，避免触碰旧业务库。原 29 个失败原因逐项比较完整 JUnit message，仅归一化 .py 源码行号，29/29 相同。DOCX/native fixture 等未修复；127 SKIP 不算通过。广泛命令整体仍 FAIL，CODE_PASS 只表示本轮必要合同通过且无新增相关失败。

精确命令、退出码与源码/JUnit SHA-256 见 `docs/audits/p4-pre-r3-offline-evidence.json` 和 `var/reports/p4-pre-r3/`。主要命令：

```powershell
.venv/Scripts/python.exe -B scripts/with_clean_slate.py --mode check
.venv/Scripts/python.exe -B var/reports/p4-pre-r3/run_offline.py
# 最终定向：同 evidence.json 所列 15 个明确文件，不扫描其它测试。
.venv/Scripts/python.exe -B -m pytest backend/tests/test_clean_slate_model_profiles.py -q --tb=short -p no:cacheprovider --basetemp=var/reports/p4-pre-r3/postgres-final2-temp --junitxml=var/reports/p4-pre-r3/postgres-final2.xml
git diff --check
git diff --cached --check
```

所有 pytest 设置 TEMP/TMP 为 `var/reports/p4-pre-r3/os-temp`，使用独立 basetemp。真实 PG 必须显式 RAG_R3_CLEAN_SLATE_TEST=1，fixture 先核对 profile 和真实实例身份，再进入回滚事务。Python shim 的 D:\Drivers\python\python.exe 位置警告仍存在，实际 Python 3.13.0 与 pytest 命令可运行；不为此修复环境。

### 遗留、提交与停止

输入容量/非截断合同、Key/账户与本轮有限外发批准、真实云摄取/查询仍为 P4_PRE_PASS 的阻断项；不属于本轮付费 Chat、Vision 或 P4 Rerank 接线许可。历史 29 项与 4 项缺历史 Caption 目录的对照验证保持原始失败/NOT RUN 状态。**依赖升级影响未独立验证**；同名云模型漂移也未完成独立实测。峰值 GPU/内存与供应商实际费用 NOT MEASURED / NOT RUN。

原计划在 CODE_PASS 后独立本地提交 `refactor(providers): configure chat embedding rerank and vision`；最终 Schema 阻断使条件不成立，**未执行 commit**。HEAD 保持入场 SHA，暂存及工作区改动均保留供 Owner 审核。没有 Push、Merge、Tag，停止在 P4_PRE_BLOCKED，不进入 P4。

### 最终 Schema 阻断与失败反例

`alembic/versions/0002_m1_rag.py:81` 的 embedding_profiles_identity_ux 只唯一约束 `(provider,model_name,model_revision,dimension,distance)`，没有 fingerprint；0014–0016 没有解除它。完整七项身份虽产生不同 fingerprint，同 Provider/model/revision/dimension/distance 仍不能建立第二个 Profile。这影响输入语义变化、P3 chunking identity 变化及同模型重建身份，不能声称身份隔离功能已经完整通过。

补充真实 PG 测试 `test_clean_slate_model_profiles.py::test_distinct_input_semantics_profiles_can_coexist`，两个诚实 UNKNOWN revision 的身份只改变 input semantics，fingerprint 不同；第二次 INSERT 实际触发 psycopg.errors.UniqueViolation / embedding_profiles_identity_ux。反例采用可选 Ollama 的本地身份，不做模型调用；约束定义没有 Provider 特例，SiliconFlow 同样受限。

最终数据库集合 **4 PASS / 1 FAIL，退出 1**，证据 `var/reports/p4-pre-r3/schema-conflict.xml` 与 `.log`。这是新增必要合同的真实失败，不属于历史 29 项，也不是 SKIP；前面的 4 PASS 仅涵盖单一输入语义与不同模型身份，不能覆盖该冲突。所有反例事务已 rollback，24 业务表仍为 0；没有旧业务库写入或删除。

最小修订提案见 `docs/audits/p4-pre-r3-schema-blocker.md`：新增经 Owner 批准的后续 Migration，把 fingerprint 纳入唯一约束，保留现有 Profile ID/历史引用；随后针对新约束实现原子 get-or-create 并复验。**仅提出方案，未创建、修改或应用 Migration，未改生产实现绕过它。** 不用 local/P3、输入语义或随机串冒充 provider revision，不覆盖旧 Profile。

按附件第四节“若实际发现新 Schema 不足，先提交证据和最小修订方案，由 Owner 决定是否批准新增 Migration”停止。下一步需要 Owner 对该新增 Migration 与相应最小 Repository 修订作决定；不是重新批准整轮任务，也不需要通过付费模型补证。


---

## P4_PRE-R3-FIX 当前修复结果（以上 R3 阻断和失败记录保留）

代码状态 **P4_PRE_R3_CODE_PASS**。整体 **P4_PRE_BLOCKED**，不是 P4_PRE_PASS。
本节基线 HEAD `a41952f8dbbcaa568ed57974f7d896b0d64b5ac2`；执行方式 MANUALLY_SUPERVISED_TRIAL，实际 serving model/effort UNKNOWN。Owner 已授权最小 Schema 修订，并另行明确授权本次标记合成 Profile 的短暂提交及精确清理。

### Schema 与生产接线

- 新增 `alembic/versions/0017_embedding_profiles_fingerprint_identity.py`，真实 revision 为 `0017_embedding_profile_identity`（符合现有 Alembic version_num 长度），down_revision 保持 `0016_parent_child_chunks`。upgrade 先核验原五列约束、fingerprint NOT NULL 及空/NULL 指纹数据，再使用 Alembic 标准 drop/create unique constraint 增加 fingerprint；不改 ID、指纹、模型 revision、外键或 Embedding 行。downgrade 如发现旧五列重复则明确拒绝，不删除数据。
- 唯一真实迁移目标为 `rag_clean_dev_20261008t072656z_352f705b`，127.0.0.1:25438，OID 21278，system identifier 7691227493754040358。检查时无其他 client backend，24 业务表迁移前后及测试后均为 0。真实约束为 `UNIQUE (provider, model_name, model_revision, dimension, distance, fingerprint)`；fingerprint 仍 NOT NULL，chunk_embeddings Profile FK 及 P3 Parent-Child FK 定义前后相同。
- `scripts/migrate_clean_slate.py` 是专用执行环境：核验 profile 身份、真实 OID/cluster、迁移版本、空库及消费者后，通过明确覆盖的 RAG_DATABASE_URL 调用标准 Alembic；不启动服务，不复用旧库目标。仅成功验证后更新 `deploy/clean-slate/profile.json` 的 migration_revision；其余字段与 HEAD 完全相同。`scripts/with_clean_slate.py::resolve_environment` 进一步固定获准库名/OID/cluster，原 Storage、端口、P3 identity 与真实迁移版本保护保留。
- `PostgresKnowledgeRepository::_get_or_create_embedding_profile` 采用六列 `INSERT ... ON CONFLICT ... DO NOTHING RETURNING id`；仅在 READ COMMITTED 中允许执行。冲突后只重新 SELECT 一次完整六列身份，行仍不可见则明确失败，无无限重试、revision 伪造或 fingerprint 覆盖。非法/不完整身份在 SQL 前拒绝；非预期 PK 冲突保留错误。process_job 使用该方法；get_embedding_profile_id/vector_candidates 同时过滤 Provider/model/revision/dimension/distance/fingerprint，原 Scope、active-version、child-only 与 P3 identity 过滤和排序算法保留。

### 实际命令与结果

命令完整参数、白名单环境和退出码分别在 `var/reports/p4-pre-r3-fix/*-command.json`；汇总 `check-summary.json`。未实现的命令不得报告通过。

| 实际执行 | 退出码 | 实际结果与证据 |
|---|---:|---|
| `.venv/Scripts/python.exe -B scripts/migrate_clean_slate.py` | 0 | 真实 0016→0017；migration-before.json / migration-after.json / migration.log |
| `.venv/Scripts/python.exe -B scripts/migrate_clean_slate.py --reapply` | 0 | 标准 Alembic 已在 0017 时无重复 DDL/数据；migration-reapply-*.json |
| `run_checks.py` 中 targeted-final 的 pytest 命令 | 0 | 351 PASS / 0 FAIL / 0 SKIP；targeted-final.xml |
| `run_checks.py` 中 db-final2 的 pytest 命令（两个显式 DB opt-in=1） | 0 | 26 PASS / 0 FAIL / 0 SKIP；db-final2.xml，真实 PostgreSQL/pgvector，模型 SIMULATED |
| `run_checks.py` 中 broad-final 的 pytest 命令 | 1 | 1233 PASS / 9 FAIL / 20 ERROR / 127 SKIP；broad-final.xml；29 项原失败逐项状态/原因相同，仅归一化源码行号，没有新增失败 |
| `.venv/Scripts/python.exe -B var/reports/p4-pre-r3-fix/run_checks.py` | 0 | 验证上述退出码、29 项对照及测试期间源码哈希无漂移；不把 broad exit 1 改成全绿 |

真实 DB 测试包含原 `test_distinct_input_semantics_profiles_can_coexist`、相同身份 ID 复用、不同语义/不同 P3 identity/不同 Provider/模型并存、错误 Profile 不返回其他向量空间、完整输入/来源 quote/child-only、现存合成 Profile ID/FK/Embedding 在真实 0017 DDL 前后保持、非法身份、非 READ COMMITTED 拒绝及非预期 PK 冲突不重试。

并发测试使用独立真实连接和不同 pg_backend_pid。相同身份观察到 pg_blocking_pids 冲突等待，获胜提交后第二连接返回同一 ID，最终已提交 Profile 数为 1；不同身份的两次 INSERT 均在任一提交前完成，最终保留两个不同 ID。仅这两项按 Owner 追加授权短暂提交，随后按本次唯一 SIMULATED model marker、ID 与六列身份精确删除合成 Profile：最终轮共清理 3 行，非测试行删除 0，库最终为空。其他测试全部 rollback。具体 PID、ID、fingerprint、提交计数、清理 ID 和最终 0 见 committed-concurrency-same.json / committed-concurrency-distinct.json；早轮同类证据保留于 round1-*，不得把授权清理描述为全回滚。

### 失败记录、保护及限制

原始 Schema RED（4 PASS / 1 FAIL）仍在 `var/reports/p4-pre-r3/schema-conflict.xml`。本轮首轮 350 PASS / 1 FAIL 因 SIMULATED SQLRecorder 未实现新的连接隔离级别及 INSERT RETURNING 响应，保存 targeted-first.xml/log/command 与 first-tested-source-snapshot.json；修复仅补 Double 协议并新增 ON CONFLICT 断言，原 P3 来源、完整内容、数值、引用、父子和 child-only 断言均未删除。未修改 P3 Chunking 生产算法。

原四项 Caption 历史目录比较保持 **NOT RUN**。未改 Caption 实现，复用此前同哈希源码的 35 PASS / 4 DESELECTED 证据；不把 DESELECTED 算 PASS。历史 Migration 0001–0016 的 raw SHA 与入场一致；三份 frozen PDF 二进制 fixture 与 HEAD 字节完全一致，其余 fixture 与前轮 raw SHA 一致、与 HEAD 仅允许既有 EOL 差异（provenance.json 的 CRLF 已存在，并非本轮漂移）。gold.json 仍 18157 字节、SHA-256 d44ff365b77800fab83b4604b26a53f9152fa8d810851e52f8cf93aae8836f79。原 27 个暂存文件入场 raw/index 哈希全部一致，原内容和失败证据保留；本轮仅追加获准 Schema/Repository/测试/专用 helper/配置与报告变更。

旧业务数据库未连接、未读取、未写入，故本轮其前后行数 **NOT RUN**，不能声称已独立排除其他消费者写入。公开语料未读写，无新增全量目录盘点；仓库历史资产逐字节保护结果在 protected_hashes。Clean-slate Storage 配置保持不变，测试 CAS 只写仓库临时目录。未调用真实模型/API，未下载模型/tokenizer，未执行 P4 正式检索链或重建业务索引。

**依赖升级影响未独立验证**。真实 SiliconFlow Embedding、模型对应 tokenizer/不截断输入保障、实际模型 pgvector 端到端验证仍 NOT RUN；UNKNOWN revision 的静默模型漂移、真实结算及原 R3 限制继续保留。代码通过不解除这些上线前阻断项。

最终 whitelist、工作树/暂存 blob SHA-256、完整 diff、默认 diff --cached --check 实际结果和独立本地 commit 回执，见本轮 final-manifest.json、git-checks.json、full.diff、repair.diff、commit-receipt.json。只有上述检查实际通过后才执行用户指定提交；不 Push、Merge、Tag，不进入 P4。
