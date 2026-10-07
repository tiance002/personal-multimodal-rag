# Local-first V1 Quality Hardening 最终报告

日期：2026-09-30。阶段：V1 Quality Hardening。候选：Local V1.1 Hardened Candidate。

## 结论与实验身份

**工程硬化与受控评估已完成；最终完整质量门为 19/32 = 59.375%，原 V1 为 20/32 = 62.5%。V1.1 尚未通过“已有高质量样例不能明显退化”的质量验收，不宣称整体质量提升或负责人已验收。**

原 V1 Baseline、评分、Reference、题目和资料保持原样；7 项不可变文件哈希检查 PASS。Frozen Reference SHA：`7b7468dcdde96fe17bfe35d4acdfd070b7ba299082af656d257c49716f856291`。32 题最终召回 ID 与 selected Context ID 均与原 V1 完全相同。没有检索参数变更、公开 benchmark、参数 sweep、Cloud 回答、新模型、reranker 或在线 Judge。

代码工作树：`C:\Users\22088\.codex\worktrees\rag-retrieval-opt-round1\RAG quention`，分支 `codex/local-first-rag-v1-20260930`，基底 HEAD `52d770e8418bb040c372bf8d9014381ed68bc160`。修改未提交、未合并到主工作区、未建 Tag。主工作区交付的是本报告和本地评估证据副本，不能把主工作区旧服务当作最新实现。

最终运行身份：`var/reports/local-v1.1-hardening/final-regression/manifest.json`；所有 backend/scripts Python 文件（含新增未跟踪文件）的执行前哈希保存在 `final-execution-source-manifest.json`，执行后核对 168 个文件均未变化。模型与资料版本见最终 manifest；现有 BGE-M3、Qwen3.5 4B，RTX 4060 Laptop 8GB，串行。

## 1. Human Review Overlay

独立 `human-review-overlay.json` 保留每题完整 `judge_original`、`human_override`、`override_reason` 和最终归因；没有改写 `SOURCE_GROUNDED_LLM_JUDGE` 原评分。

`human_review = PARTIAL`，`human_review_cases = 8 / 32`。这 8 题是负责人对 **旧 V1 答案** 的复核。**最终 V1.1 新答案没有再次经过负责人审核**，状态 `NOT_REVIEWED`，不能复用旧人工批准。

| Case | 原 Judge / 人工差异与最终记录 |
|---|---|
| real-001 | 保留高质量；受同一资料支持、属于答案范围的扩展不因比标题宽而扣分。 |
| real-004 | 原 Judge 责任不确定；人工确认资料可答且系统拒答：SYSTEM_OVER_REFUSAL CONFIRMED。旧 candidate 未保存，VALIDATOR_OVER_REJECTION NOT_PROVEN。 |
| real-008 | 保留 LOCAL_MODEL_FACTUAL_ERROR，新增 SELF_CONTRADICTION；明确指出无关 chunk 不单独算幻觉。 |
| real-009 | 与 004 相同：系统过拒已确认，Validator 误拒未证明。 |
| real-011 | 保留可观测 GENERATION_TRUNCATION，同时记录 MULTI_INTENT_COVERAGE_FAILURE、LOCAL_MODEL_OMISSION、CITATION_SELECTION_FAILURE，禁止只归因截断。 |
| real-016 | 原 Judge 为 Retrieval Failure；人工改为 SUFFICIENT、Correctness 2、Completeness 1、Faithfulness 2、LOCAL_MODEL_OMISSION。 |
| real-017 | Corpus Answerable YES；原 selected Context 缺必要字段，仍是 INSUFFICIENT / RETRIEVAL_FAILURE，次要 OVER_REFUSAL。 |
| real-023 | Context 充分；记录 CITATION_IDENTITY_INCONSISTENCY、CITATION_RELEVANCE_FAILURE、LOCAL_MODEL_REASONING_FAILURE、LOCAL_MODEL_OMISSION。 |

原 Judge retrieval sufficient 29/32 保持。旧答案加入 016 Overlay 后充分数可作独立归因视图的 30/32，不能把它冒充原 Judge 或重算原 62.5%。

## 2. Root Causes 与正式 Failure Taxonomy

### 系统层

拒绝 candidate 原先缺少审计，004/009 因而无法追责；固定 512 输出上限导致 010/011 可见截断；显式多问缺同次生成清单；重复证据身份/引用 occurrence 未完整观测。第一轮硬化回归另暴露通用降级过窄、空格引用误拒和数值问题错误原文降级，均在最终代码中修复并保留诊断证据。

### Model 层

最终 Context 充分但仍存在说反关系、步骤遗漏、无关 Citation。在线词面规则不具备完整语义裁判能力，不能因为 chunk 真实存在就认为支持结论。

### Retrieval 层（只读诊断）

- **real-015**：selected Context 含 GRU/一般注意力，缺必要 AUGRU 目标 chunk `11992bff-831e-4e5e-baf5-c210537729e8`。
- **real-017**：selected Context 没有日期/三张表目标 chunks `340dc4df-928e-4a3f-acf0-956843d2d158` 与 `8c1717de-b9ae-40f7-81c5-00873a7b9ef4`，而含不相关视觉资料。
- **real-013**：有数据表收益率名，缺把它明确关联为时间敏感因素的摘要。新旧 Context 相同，当前判 INSUFFICIENT 是离线充分性重新判断，不能称检索器被本轮改坏。

运行记录可确认：冻结召回/Context 选择未将这些目标证据带入当前五个片段。未保存完整 32 个候选排序的逐项分数，故无法进一步证明是候选未入池还是池内排名裁剪；不编造分数，不调参。

### 正式归因规则

| 类型 | 证据要求 |
|---|---|
| MULTI_INTENT_COVERAGE_FAILURE | 明确多个意图，至少一个必要意图未被回答；词面 MENTIONED 不等于语义覆盖。 |
| SELF_CONTRADICTION | 同一答案的明确否定与随后有证据支持的肯定矛盾。 |
| SYSTEM_OVER_REFUSAL | 本应能获得资料支持的答案，系统最终没有返回；不自动推断 Validator 责任。 |
| VALIDATOR_OVER_REJECTION | 已保存 candidate 足够正确且有来源支持，却被 Validator 错误阻止。无 candidate 不可确证。 |
| VALIDATOR_CORRECT_REJECTION | 已保存 candidate 错误、不受支持或引用无效，Validator 正确阻止。 |
| CITATION_IDENTITY_INCONSISTENCY | 同一 run 的 chunk_id + version_id 被映为冲突来源实体，或 label 指向不同身份。 |
| CITATION_RELEVANCE_FAILURE | Citation 绑定结论不受所指来源合理支持；在线未证实时标 UNKNOWN，离线 Judge 决定语义结论。 |

## 3. Changes 与通用产品价值

| 修改 / 文件 | 机制与边界 |
|---|---|
| `adapters/answer_audit.py`、`bootstrap.py` | 拒绝时在现有本地 storage_root/answer-audit 保存独立审计：run/request hash、rule/version、原因、candidate 是否存在/hash/text、Context ID、Citation 映射、时间。正文仅本地 ignored var，不进 Git/公开报告/Cloud。文件独占创建；写入失败保守拒答。 |
| `application/answer_hardening.py`、`knowledge_gateway.py` | Quick/Smart 共享生成后审核；拒绝原答案不直接放行。对各意图均有直接、主题匹配的声明/操作原文时做仅摘录 fallback；数字目标需已有明确支持事实，含糊定量问题拒绝降级。无可靠直接证据安全拒答。 |
| `ports/providers.py`、`adapters/models/ollama.py` | done_reason=length 保留候选用于审计；SIMPLE/NORMAL/COMPLEX 为有限 512/896/1280。按独立意图数、Context 大小与问题形态选择，不是全量提高到 2048；不续写、不增加模型调用。 |
| `quick_chain.py`、`langchain_agent.py` | 同一次生成注入保守 deterministic checklist，简洁输出及逐事实引用要求；不调用 Planner。独立问句、显式分别解释、独立 interrogative clauses 优先，不把任意“和/逗号”强拆。追问 server wrapper 只检测当前问题意图。 |
| `citations.py`、`context_builder.py` | 同 run 相同 chunk/version 复用冻结 identity/label；冲突映射拒绝，重复 context 不作为两个来源实体。不同 run 的 E1 与 E4 是各自局部 label，不要求跨回答 label 相同。 |
| `run_metrics.py`、hardening mapping | occurrence → display_label → chunk/document/version/locator，含 answer_span/marker_span。原文 locator quote 不泄露到 metrics。仅明确原文匹配 SOURCE_TEXT_MATCH；其他均 CITATION_RELEVANCE_UNKNOWN，未知标签 CITATION_UNSUPPORTED。 |
| 明显矛盾窄规则 | 仅“没有提及/没有提到/未提及/不存在”与后续相同主题、有 Citation 的肯定匹配。复杂否定不宣称已解决；没有新增 NLP engine 或模型调用。 |
| `scripts/quality_hardening_evaluation.py`、`replay_answer_hardening.py` | 不可变输入账本、8 题 Overlay；诊断用原 candidate 零模型调用重放，重放与真正最终实时回归分开存储。 |

这些机制即使没有 32 题，也能让普通拒答可追责、直接证据不丢失、复合问题更完整、引用来源稳定。生产代码不读取 Frozen Reference、不按 case_id 写分支、不注入 reference points。

## 4. V1 vs V1.1

最终答案采用同一 Frozen Reference、同一质量门和离线 Source-Grounded LLM Judge：Codex 对真实输出、selected Context 和引用原文做语义复核。项目模型/provider 未用于 Judge，不把词面命中当正确率，也不冒充负责人人工复核。逐题分数保存在 `final-quality-scores.json`。

| 指标 | V1 Baseline 原值 | V1.1 最终真实回归 |
|---|---:|---:|
| Full Quality Gate | 20/32，62.5% | **19/32，59.375%** |
| 正确性 | 1.6875/2，32题 | 1.8125/2，32题 |
| 完整性 | 1.5625/2，32题 | 1.71875/2，32题 |
| 忠实性 | 1.80/2，30题 | 1.87097/2，31题 |
| Citation Support | 24/30 cited，80% | 25/31 cited，80.645% |
| Citation 分类 | 24支持/4部分/2不支持/2无 | 25支持/6部分/0不支持/1无 |
| Retrieval sufficient | 原 Judge 29/32 | 当前 Judge 29/32；另1部分、2不足 |
| System over-refusal | 人工确认004/009共2；017 retrieval相关拒答另列 | Context充分的系统过拒0；013/017 retrieval相关未满足答案2 |
| Validator rejected | 2，旧candidate缺失 | 2：007正确拒绝后安全降级；017无效引用拒绝 |
| 用户最终收到错误/空输出 | Judge错误/无答案3 | 1：017；不等于另外12部分答案通过 |
| Truncated | 2 | **0**，length0，continuation0 |
| 多意图失败 | 人工至少011；另遗漏见旧Judge，不做未有证据的精确全量计数 | 12题路由多意图；027仍有明确遗漏，011必要点已覆盖；不能将清单视作完整性保证 |
| Citation mismatch/relevance | 原至少011；人工023另确认 | 010/011/014/016/018/025 共6题 PARTIAL |
| Generation input tokens | 58,238 | 56,436（-1,802，-3.09%） |
| Generation output tokens | 6,068 | 3,165（-2,903，-47.84%） |
| Generation total tokens | 64,306 | 59,601（-4,705，-7.32%） |
| 总延迟 p50 | 3.933s | 2.453s（-1.480s） |
| 总延迟 p95 | 10.057s | 9.371s（-0.686s） |
| 单题最大延迟 | 10.633s | 13.661s（+3.028s；首题冷启动） |
| Generation calls | 33 | 31（Smart原有工具循环计入；清单/降级无额外调用） |
| Query embedding calls | 32 | 32 |
| Cloud Calls | 0 | 0 |
| GPU采样峰值 | 4816MiB | 4816MiB |
| Runner elapsed | 146.209s | 116.883s |

Input/output 表为 generation usage，与原 Baseline 相同口径；Embedding 单列次数，不把它混入比较。资源每秒采样，GPU数值是全机采样峰，非单模型常驻显存。一次运行没有冷暖启动控制或因果消融，不能把所有延迟差归因某项代码。

### 得失与归因

跨过完整门的新增案例：004/008/009/024；退出完整门：012/013/014/018/025；净少1题。

- 检索调参贡献 **0**：检索文件和配置未改，32题 retrieved/selected IDs 全同。
- 007 的确定性安全降级和 030 的空格引用/数值保护由保存 candidate 重放提供直接系统修复证据；最终 007/030 均正确。
- 010/011不再截断，但说反“让 agent 自行 grep 的效率”、附无关引用仍使完整门失败。
- 008同次多意图提示后本次覆盖两问，029当前问题路由后本次引用说明恢复。没有随机种子重复实验/消融，不给出“系统贡献X个百分点”的伪精确值。
- 020/024已有隐私原文路径在旧 Baseline 之后、本 Goal 之前就存在；024提升不能全部归功本轮。

### 高质量类别回归

FACT_LOOKUP 的 001–006/019/021继续高质量，但018答案数值正确却附无关视觉来源，质量门退化。FOLLOW_UP 028/029最终均通过；UNANSWERABLE 030–032最终均安全拒答；EXACT_IDENTIFIER 类主要事实保留，026仍有冻结Reference必需点遗漏。不能宣布“所有高质量类别完全无退化”。

## 5. Remaining Local Model Limits

Context 足够、没有截断、没有身份冲突，但仍存在生成事实/推理/遗漏或错误引用选择的 **8题**：010、011、012、014、016、018、025、027。

这是基于最终单次运行的模型失败候选，不等于通过消融严格证明的8个纯模型原因。在线 semantic relevance 未解决，仍需人工区分通用提示影响、模型能力与产品 Validator 的可判定边界。

023/026涉及冻结必需点与问题范围对应关系疑问，记 AMBIGUOUS，不偷偷删除 Reference 或把它们直接归为 Cloud 必需。新答案未全部人工审核。

## 6. Future Cloud Candidates（仅候选，不实现）

- `LOCAL_SAFE`：001、002、003、004、005、006、007、008、009、019、020、021、022、024、028、029、030、031、032（19题）。
- `CLOUD_RECOMMENDED`：010、011、012、014、016、018、025、027（8题）。
- `RETRIEVAL_FIRST`：013、015、017（3题）。
- `AMBIGUOUS`：023、026（2题）。

编号均带 `real-` 前缀。候选分类不是生产 Cloud Router；没有新增 provider、调用 Cloud 或自动进入下一阶段。

## 7. Retrieval-first Cases

013/015/017 的关键 evidence 未进入 selected Context。更大的生成模型不能可靠补出未提供的资料事实；先诊断证据进入链，随后才能比较模型能力。016保持充分，不再错误归因 Retrieval Failure。

## 8. 风险、停止条件与验收边界

- 新V1.1没有负责人再次人工审查；旧 Overlay 只有8/32，报告 PARTIAL。
- 单次实验、Judge也可能错误；014/016引用附加绑定和023/026Reference范围需要复核。旧Reference保持不变。
- 原Gold5文档46chunks，实际同一库6文档47chunks；第六文档可影响无关引用，旧新均保留，不能为提高分数删除。
- 031法定名称的负面检查未覆盖所有图像OCR；沿用原限制。
- fallback是狭窄直接原文规则，不是通用语义证据充分性证明；写审计失败、没有可靠证据时不降级。
- occurrence的字符范围可追踪；SOURCE_TEXT_MATCH只证明文字匹配，103个UNKNOWN不能称语义通过。本轮 identity 已收敛，**relevance仍未完全收敛**。
- 截断此次为0，有强制有界失败处理及实测测试，不保证未来任何长问题永远不截断。
- 质量门较旧Baseline下降，已记录事实、保留结果；不以继续调Prompt或无限debug追分，不自行批准退化延期，也不声明发布验收。

8项工程停止条件证据：拒绝审计（最终007/017）；充分直接原文降级（007）；bounded预算/length候选审计（单元集成+本轮length0）；零额外模型意图清单（12题metrics）；111 occurrence完整身份映射；重复身份检测测试/本轮冲突0及relevance UNKNOWN；独立8题Overlay；同32/同Ref最终真实回归。按Goal停止。**工程交付完成、质量验收未通过，不自动开启Cloud。**

## 9. 执行命令与结果

以下从代码工作树执行；Python绝对路径为 `E:\RAG quention\.venv\Scripts\python.exe`。未实现的命令不得报告通过。

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest backend/tests/test_answer_hardening.py backend/tests/test_ollama_usage.py backend/tests/test_citation_resolution.py backend/tests/test_quick_chain_quality.py backend/tests/test_langchain_agent.py backend/tests/test_run_metrics.py backend/tests/test_quick_chain_budget.py backend/tests/test_langchain_quick_chain.py backend/tests/test_egress_matrix.py backend/tests/test_knowledge_scope_boundaries.py backend/tests/test_scope.py backend/tests/test_privacy_configuration_answer.py backend/tests/test_context_and_locators.py backend/tests/test_agent_trace_sse.py -q
```

退出0：59 passed，6条既有依赖弃用warning，11.27秒。覆盖audit、fallback、length、清单/追问/coverage、重复引用/occurrence、范围/隐私外发、Agent及SSE。模型边界单元使用fake；下列32题runner为真实PG/BGE/Qwen服务，不混称。

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' scripts/quality_hardening_evaluation.py initialize
& 'E:\RAG quention\.venv\Scripts\python.exe' scripts/quality_hardening_evaluation.py verify
& 'E:\RAG quention\.venv\Scripts\python.exe' scripts/replay_answer_hardening.py
& 'E:\RAG quention\.venv\Scripts\python.exe' scripts/verify_real_materials.py --endpoint http://127.0.0.1:18088 --cases var/local-first/real-material-eval.jsonl --materials var/local-first/materials.json --output var/reports/local-v1.1-hardening/final-regression
& 'E:\RAG quention\.venv\Scripts\python.exe' var/reports/local-v1.1-hardening/build_final_scores.py
git diff --check
```

Initialize/verify退出0：7不可变输入、8Overlay。重放最终退出0：32题，0新增模型/检索/Cloud；诊断用途，不算最终真实回归。最终runner退出0：32题、31回答、31生成、0Cloud、52引用回读、116.883秒。离线评分退出0：19完整/12部分/1无答案。diff检查退出0（换行格式提醒，无patch错误）。源哈希核验退出0：168文件执行后未改。

首轮真实runner保存在 `regression/`，退出0，但暴露降级P1；其诊断Judge为15/32。随后原candidate重放为17/32，保存 `deterministic-replay/`；用户明确“允许一次最终真实回归”后才执行 `final-regression/`。不覆盖、不挑选三者最高分；最终报告只用最后真实运行19/32。

过程中的RED测试按预期失败后修复；一次离线检查因Windows GBK无法输出不间断空格退出1，设置PYTHONIOENCODING=utf-8后读取成功；重放初次导入旧主工作区模块失败，脚本根目录导入修复后退出0。它们均不是模型回归重试。全量发布、verify-release、Cloud对照、所有新答案人审：**NOT RUN**。

## 10. 最终十二问

1. 原62.5%是否保持：是，原7项输入与结果哈希未变。
2. V1.1 Gate：19/32，59.375%，较原少3.125个百分点。
3. 系统vs检索贡献：检索调参为0；没有净Gate提升；系统修复个案可观测，无消融不拆分精确百分点。
4. 004/009能否证明误拒：不能，旧candidate缺失；新答案正确不补足旧责任证据。
5. 截断是否解决：本轮2→0，bounded处理已实现；010/011内容错误仍在。
6. 多意图是否改善：008两问和011必要点本轮覆盖，027仍遗漏；机制改善不等于普遍完整性保证。
7. Citation是否改善：identity冲突0、111 occurrence可追踪；语义支持80%→80.645%，仍6题部分支持，relevance未完全解决。
8. 真正Local能力候选：8题；2题归因/Reference疑问；不宣称因果隔离已证明。
9. Cloud候选：010/011/012/014/016/018/025/027。
10. Retrieval先行：013/015/017。
11. 额外成本：没有总量额外Token；generation总量减少4705，p50减少1.480s、p95减少0.686s；冷启动max增加3.028s，无因果成本结论。
12. 是否值得下一阶段：目前不建议作为已通过质量验收的基线直接开启正式对照。先由负责人复核退化与引用/Reference边界；可以据本报告决定后续，当前不自动实现Cloud。
