# V1 真实资料 Source-Grounded Quality Baseline（2026-09-30）

## 1. 评估身份

| 项目 | 固定值 |
|---|---|
| 原始运行 Git SHA | `90af812e0cdb3d3124df9f39e9e78beb42da195f` |
| 本次报告执行 Git SHA | `52d770e8418bb040c372bf8d9014381ed68bc160` |
| 32 题数据集 SHA-256 | `d0fc5f80371a0710d1618cf8a660fa8a70fb4fab96fb9b982b9e51a36804719c` |
| 原始 `results.json` SHA-256 | `1c74ce53b9f1caa94857e38323291fbd903d9448ad65d996d116f4a45926a5df` |
| 材料 manifest SHA-256 | `b73cba8d888175035237f3cbd30603955dfee1b7068b496d9a0b8657175ad567` |
| 原始运行 manifest SHA-256 | `a50ad5234d22f20d57162a38b5457282dbf6ad2d712fb2ada35820440eaaa9ec` |
| Frozen Reference SHA-256 | `7b7468dcdde96fe17bfe35d4acdfd070b7ba299082af656d257c49716f856291` |
| 样本范围 | 32 道固定问答；原 Gold manifest 为 5 份资料、46 个 chunk |

本评估在隔离的 `codex/local-first-rag-v1-20260930` 工作树中完成。主工作区未改动；本报告只保存脱敏汇总，本地逐题分数与受控证据留在忽略目录 `var/`。未提交代码、未提交数据、未创建 Git tag。

## 2. 评估方法

采用 `SOURCE_GROUNDED_LLM_JUDGE`。Phase A 只读取问题、冻结资料、原 Gold 范围/要点及元数据；没有打开答案。32 题 Reference 完成后写入 `var/reports/real-material-quality-baseline/reference-set.json`，校验其 SHA-256 与 sidecar 一致，然后才进入 Phase B。所有保留的原 answer point 均按来源核验；原始数据未记录要点作者，因此按 `SOURCE_VERIFIED` 标记，没有假设其为用户手写 Gold。

Phase B 对现存输出、真实 selected context 与被引用 chunk 做语义检查。引用回读成功、词面命中和检索器的 `expected_context_recall` 都没有被当成语义正确性的替代指标。没有重跑检索或生成，没有调用 Cloud 模型，没有修改 case、结果、参数、prompt、validator 或检索策略。

`human_review = NOT_RUN`。两条返回 `UNSUPPORTED_ANSWER` 的原始拒绝答案没有保存在结果文件里，所以无法判断它们是 Validator 误拒还是正确拒绝了不受支持的生成内容；这两题的 faithfulness 均记为不可评估，不将其写成已确认的 Validator 缺陷。

## 3. 质量结果

### 总体答案质量

| 指标 | 得分 |
|---|---:|
| 正确性 | 1.69 / 2（32 题） |
| 完整性 | 1.56 / 2（32 题） |
| 忠实性 | 1.80 / 2（30 题；2 条被拒绝答案无原文） |
| 完整质量门 | 20 / 32（62.5%） |

“完整质量门”定义为：正确性、完整性和忠实性均为 2；引用语义支持或不适用；检索充分；输出完整；拒答符合预期。

| 结果分类 | 数量 | 定义 |
|---|---:|---|
| 基本正确 | 20 | 满足上述完整质量门 |
| 部分正确 | 9 | 没有已评 0 分，但有重要遗漏、矛盾或证据问题 |
| 错误/无可用答案 | 3 | 正确性为 0 或忠实性为 0；两条 Validator 拒绝按用户可见结果计为无答案 |

### Citation Support

| 语义支持 | 数量 |
|---|---:|
| `SUPPORTED` | 24 |
| `PARTIAL` | 4 |
| `UNSUPPORTED` | 2 |
| `NO_CITATION` | 2 |
| `NOT_APPLICABLE` | 0 |

有可评引用的 30 题中，语义支持率为 **24/30 = 80.0%**；按全部 32 题计算，答案带有受支持引用的比例为 **24/32 = 75.0%**。引用的可回读状态不等同于语义支持。本次两个 `UNSUPPORTED` 是引用错配；另有两题被 Validator 拒绝，未产生可评引用。

### Retrieval 与拒答

| 维度 | 结果 |
|---|---|
| Retrieval Sufficiency | `SUFFICIENT` 29；`PARTIAL` 2；`INSUFFICIENT` 1 |
| Refusal Quality | 正确拒答 3；过度拒答 2；不足拒答 0；不适用 27 |
| Output Integrity | `COMPLETE` 28；`TRUNCATED` 2；`VALIDATOR_REJECTED` 2；`MALFORMED` 0 |

## 4. Failure Taxonomy

12 个未通过完整质量门的案例，其 Primary Failure 分布如下。两个 Validator 拒绝因原答案缺失归为 `REFERENCE_UNCERTAIN`，不算作已证实的误拒。

| Primary Failure | 数量 | 说明 |
|---|---:|---|
| `RETRIEVAL_FAILURE` | 3 | 两题仅部分覆盖必需证据，一题未检索到目标资料块 |
| `GENERATION_TRUNCATION` | 2 | 两个输出均在 512 个输出 token 处结束 |
| `LOCAL_MODEL_FACTUAL_ERROR` | 2 | 与上下文中明确事实矛盾或错误解释配置项 |
| `LOCAL_MODEL_OMISSION` | 2 | 上下文充分，但答案漏掉必需点 |
| `LOCAL_MODEL_REASONING_FAILURE` | 1 | 上下文充分，答案仍声称无法确定并遗漏事实 |
| `REFERENCE_UNCERTAIN` | 2 | Validator 拒绝且原始生成内容未留存 |

次要失败有重叠：`LOCAL_MODEL_OMISSION` 另出现 6 次，`CITATION_SELECTION_FAILURE` 另出现 1 次。案例 11 同时有输出截断与引用错配。

## 5. Retrieval 与 Generation 归因

**Retrieval 不是本轮占比最高的已确认瓶颈。** 29/32 题的实际上下文被判为充分，3 题存在证据覆盖缺口，并对应 3 个 Primary `RETRIEVAL_FAILURE`。

上下文已充分但回答仍未达到完整质量门的有 **7 题**：`real-008`、`real-010`、`real-011`、`real-023`、`real-024`、`real-026`、`real-027`。其中包含事实矛盾、推理失败、要点遗漏、引用错配和截断。另有 `real-004` 与 `real-009` 的上下文也充分，但原始答案缺失，只能确认用户没有得到可用答案，不能确认问题发生在模型还是 Validator。

因此，按用户可见结果，9 题发生在充分上下文下；其中 **7 题已确认答案/引用输出失败，2 题仍无法归因**。另有 3 题先受检索覆盖影响。运行记录的有效 KB/document request scope 未完整序列化；所有已记录 chunk 均映射到同一个 KB，但无法从 trace 完整复核每题的请求范围。后置语料比 Gold manifest 多 1 份资料，`real-017` 曾选中 Gold manifest 外的 chunk；本评估没有把它当作有效 Gold 证据。

## 6. Query Type Breakdown 与未来 Cloud Fallback 候选

均值为 0–2 分；样本量很小，只用于本组案例的定性比较。

| Query Type | 题数 | 正确性均值 | 完整性均值 | Retrieval 充分 | 基本正确 / 部分 / 错误 | Cloud 候选（LOCAL_SAFE / CLOUD_RECOMMENDED / AMBIGUOUS） |
|---|---:|---:|---:|---:|---:|---|
| `CONCEPT_EXPLANATION` | 8 | 1.38 | 1.38 | 8/8 | 4 / 2 / 2 | 4 / 2 / 2 |
| `CONFIGURATION` | 5 | 1.60 | 1.60 | 5/5 | 3 / 2 / 0 | 3 / 2 / 0 |
| `EXACT_IDENTIFIER` | 5 | 1.80 | 1.60 | 4/5 | 3 / 2 / 0 | 3 / 1 / 1 |
| `FACT_LOOKUP` | 3 | 2.00 | 2.00 | 3/3 | 3 / 0 / 0 | 3 / 0 / 0 |
| `FOLLOW_UP` | 2 | 2.00 | 2.00 | 2/2 | 2 / 0 / 0 | 2 / 0 / 0 |
| `MULTI_CHUNK` | 5 | 1.60 | 1.20 | 3/5 | 2 / 2 / 1 | 2 / 1 / 2 |
| `MULTI_DOCUMENT` | 1 | 2.00 | 1.00 | 1/1 | 0 / 1 / 0 | 0 / 1 / 0 |
| `UNANSWERABLE` | 3 | 2.00 | 2.00 | 3/3 | 3 / 0 / 0 | 3 / 0 / 0 |

离线标签总数为 `LOCAL_SAFE=20`、`CLOUD_RECOMMENDED=7`、`AMBIGUOUS=5`。更值得未来对照 Cloud 的是：**有充分上下文但本地回答仍出错的概念解释与配置问题**，以及少数多文档/标识符问题。对缺证据的多块问题，应先判断检索覆盖；直接换更大的生成模型不保证解决。Validator 原答案未留存的两题保持 `AMBIGUOUS`。这些标签不实现 Router，也不触发 Cloud 调用。

## 7. 当前 Cost/Performance Baseline

以下全部复用原始运行记录，不是本次评估重新测量。

| 指标 | 原始记录 |
|---|---:|
| Cloud 调用 | 0 |
| 生成调用 / 返回答案 | 33 / 30 |
| 运行耗时 | 146.209 秒 |
| 生成阶段 token | 输入 58,238；输出 6,068；合计 64,306 |
| 含 Embedding 的记录 token | 输入 59,026；输出 6,068；合计 65,094 |
| 总延迟 | p50 3,932.634 ms；p95 10,056.963 ms；最大 10,633.275 ms（32 题） |
| GPU 采样峰值 | 4,816 MiB |
| RAM 采样峰值 / 最低可用 | 27,032.49 MiB / 5,459.38 MiB |
| 模型 | 回答 `qwen3.5:4b`；Embedding `bge-m3:latest` |
| 金额成本 | 未记录；不能由 token 或本地资源数据推定 |

## 8. Judge 自检、局限与下一步候选

建议项目负责人之后抽查下列 8 题，不需要审核全部 32 题。chunk ID 可在本地 frozen reference 中回读；报告不包含问题、答案或资料原文。

| Case | 抽查原因 | 本次判断 | Source chunk ID |
|---|---|---|---|
| `real-001` | 高置信度正确样例，用于检查 Judge 是否宽松 | 完整质量通过 | `b90d76eb-38e8-4500-8a98-29c8e4acbfc0` |
| `real-004` | Validator 拒绝，检查是否误拒 | `REFERENCE_UNCERTAIN` | `b90d76eb-38e8-4500-8a98-29c8e4acbfc0` |
| `real-008` | 答案自相矛盾 | 部分正确；事实错误 | `b90d76eb-38e8-4500-8a98-29c8e4acbfc0` |
| `real-009` | Smart 路径 Validator 拒绝，检查是否误拒 | `REFERENCE_UNCERTAIN` | `6a9ce0b2-8065-4b10-905a-fbc30323e5c1`、`a3058375-df1a-4da4-a5d6-3529f6d27ec6` |
| `real-011` | 输出截断且引用错配 | 截断；引用不支持 | `6a9ce0b2-8065-4b10-905a-fbc30323e5c1`、`a3058375-df1a-4da4-a5d6-3529f6d27ec6` |
| `real-016` | 多块检索部分覆盖；中置信度 | 检索部分充分；答案遗漏 | `ae559aad-f817-4a0e-a1ec-cd8eccd9306c`、`340dc4df-928e-4a3f-acf0-956843d2d158` |
| `real-017` | 未检索到目标字段块，且发生拒答 | Retrieval failure | `340dc4df-928e-4a3f-acf0-956843d2d158`、`8c1717de-b9ae-40f7-81c5-00873a7b9ef4` |
| `real-023` | 上下文充分，答案仍称无法确定 | 推理/遗漏失败 | `a495f9da-2e55-4ff1-8ed0-20689655523e` |

需要将本次结果视为可追溯的基线，不是绝对真值：`LLM Judge != Human Ground Truth`；32 题不代表一般用户问题分布；语料是个人私有资料；Reference 构建可能有误；人工复核尚未完成。`real-031` 的 PDF 文字提取未覆盖嵌入图像 OCR，因此该题 Reference 与评分置信度为 MEDIUM。两个 Validator 拒绝结果未保存原始生成内容。原始 trace 中的错误/拒答布尔标记与文本并非始终一致，本次按答案语义判读。

**下一步候选（本次不实施、不批准延期）：**

1. `P1 LOCAL_ANSWER_GENERATION`：先评估充分上下文下的矛盾、遗漏、推理和 512-token 截断。
2. `P1 VALIDATOR_REJECTION_AUDIT`：先保留并审计被拒答案，再判断是否存在误拒。
3. `P2 RETRIEVAL_COVERAGE`：单独检查三道覆盖不足的查询；由项目负责人决定是否新开优化目标。

本报告只描述现有固定结果。未自动修复任何问题，也未进入下一轮优化。
