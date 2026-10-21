# P16 — Owner补充要求验收

## 结论

**P16_PARTIAL_SAFE_STOP**。已有计量修复具备真实响应离线验证；4次漏引用已按三类候选原因分别记录，但因缺供应商解码前正文，无法安全确认根因，停止推测修复。0434直接复用既有OCR结论，章节关联登记为技术债，本轮不再研究。

补充核对基线 `978e2ba67556a566dacde64ae44568cb9165ef70`。本补充没有生产代码修改，不重跑OCR、不追加Rerank/模型请求、不重复104项已通过回归，不更改旧结果或UNKNOWN。

## 1. Rerank字段、单位与边界

核对[SiliconFlow官方Rerank API响应示例](https://docs.siliconflow.cn/docs/api/rerank-post)，结构将meta.tokens与meta.billed_units分开。官方页面字节快照及SHA256保存在 `E:/WK/run/p16-common-20261016/acceptance-supplement/official-source.json` 和同目录HTML。本轮只读取公开文档，没有供应商模型请求。

P13/P14的10份真实响应均有meta.tokens对象，字段为input_tokens、output_tokens、image_tokens，值为非负整数；本批output/image均为0。meta.billed_units同时含计费相关字段。样本中的同名数值相等是**观测事实，不是恒等合同、不是供应商账单核对**。实际Token读数只取tokens；search_units/classifications不换算为Token，不把billed_units作为缺失tokens的替代。没有独立验证供应商tokenizer或发票计费单位。

已有修复只在明确文本计量条件下，对报告的input/output求和并标为derived_total；不伪称供应商报告total。其他provider、缺项、非法数值、图像计量歧义保持原规则或UNKNOWN。修改只影响新调用经正常预算门的计量，历史UNKNOWN没有自动结算、重放finish或释放。

本补充对10份真实响应逐一再做窄范围反例：原件/夹具哈希一致；任意改变billed_units不改变Token读数；移除tokens但留下billed_units时保持UNKNOWN。**10/10通过，exit0**。这是离线字段兼容与安全边界检查，不是新模型调用。历史响应73957输入Token不是本轮消耗。

## 2. 四次漏引用的逐类归档

| QID | 请求协议 | 模型未生成引用 | 模型输出协议错误 | 解码丢失 | 解码后存储丢失 |
|---|---|---|---|---|---|
| MIRACL 692002#0 | 配置正确 | 无法确认 | 无法确认 | 无法确认 | 未观察到 |
| MIRACL 704170#0 | 配置正确 | 无法确认 | 无法确认 | 无法确认 | 未观察到 |
| LongBench dureader:5726f5fc6feedda99cafb275a27a8fd67483185b6fb8efa7 | 配置正确 | 无法确认 | 无法确认 | 无法确认 | 未观察到 |
| LongBench multifieldqa_zh:1a6c19fed6361cbfbfd0df3fb7ca1e01f2a2ffa0da9903bc | 配置正确 | 无法确认 | 无法确认 | 无法确认 | 未观察到 |

“请求协议配置正确”只指实际请求有启用的引用规范与Context handles，不能推断模型遵循了规范。“未观察到存储丢失”只指应用SSE、持久化和评测答案一致。供应商原始response body/predecoder delta未保存，无法判断模型是否没生成、生成了错误格式、或正确格式被decoder丢弃。不能将应用raw_answer当成供应商原始输出。

四题根因分类均为 **EVIDENCE_GAP_NOT_CONFIRMED_CAUSE**，不是宣称已经确认模型质量或解码Bug。现有100个合成分片边界检查没有复现规范引用丢失，但不能代替历史供应商重放。无已确认原因，不做生产引用修复，不按Context命中/相似度自动添加引用，不为不受支持的答案补引。四题完整事实和null原始响应路径见P16_COMMON_FIXES.results.json/citation_diagnosis。

## 3. 已授权记录的技术债

**TD-P16-0434-SECTION**：现有本地Tesseract已经真实识别第33页Coding time!，来源与位置见 `P16_TITLE_OCR_ASSOCIATION.md/.results.json`。缺少到34/35页证据的可靠机器跨页章节关联。按Owner本次补充明确记录为技术债、排除于本轮研究范围；新增OCR0、章节实验0、Rerank0。不替换引擎、不调参、不继续寻找关联。

## 4. 命令、账本与停止

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p16-common-20261016/acceptance-supplement/verify.py
```

exit **0**，输出 `PASS10_REAL_RESPONSE_STRUCTURES_AND_UNITS_BOUNDARY; FOUR_CAUSAL_CLASSES_UNRESOLVED_EVIDENCE_GAP; OCR_REUSED_NO_EXPERIMENT`。日志同目录verify.log/verify.exitcode.txt。

必要代码修复仍为 `c2f7197abbf9336bea7d19e047f7a5f7c7b713ae`，已执行104项定向回归exit0，原日志 `E:/WK/run/p16-common-20261016/unit-final.log`，本补充复用结果，不声称重新运行。原只读账本核对618条记录/61条UNKNOWN/月预算/DeepSeek逐值未改的证据保留。此次无写库、无旧UNKNOWN结算/释放。

新增真实模型请求 **NOT_RUN**、新增Token0、新增UNKNOWN0。未扩大开发，无Push/部署/合并/Tag。不能满足历史根因确定性所需的原始证据已明确缺失，按“无法安全修复则记录并停止”结束。**未实现的命令不得报告通过。**
