# S5 模型事实可靠性专项

## 1. 结论与范围

**`COMPLETE_WITH_UNRESOLVED`：本轮限定诊断与真实对照完成；表格关系混淆仍为 `UNRESOLVED`。生产代码、默认 Prompt、模型、DYNAMIC、检索与历史索引均未修改。**

- Summary 的编号/类型约束候选在本轮 4 类材料中没有再增加设备子类型，保留了编号、读数和日期。可以保留为隔离候选，尚不足以修改所有文档的默认 Summary。
- 逐行绑定真实表头后，原表格问题仍把 **West Yard** 称为“单元”，同时给出 WY-309。该候选没有解决主要错误，不接入生产作为事实可靠性修复。
- 缺 Unit 的候选回答只说“West Yard 的读数为51”，事实有依据，但未说明 Unit 未知，也未回答所问单位，判 **PARTIAL**，不算正确拒绝猜测。
- 本轮新增 **12 次真实 SiliconFlow Qwen3.5-4B 生成，输入8,234／输出1,045／合计9,279 Token，全部结算，UNKNOWN 0**。授权和临时围栏已经 CLOSED，日常云开关仍 false。

基线：`ab969e5185ef1ae2e23083d48a60dd454d847377`；分支 `codex/weknora-python-port-integrate-image-20261014`。实验实现提交：`b704bbad47318809e30784d93701585053b7c125`。报告最终提交用 `git log -1 --format=%H -- docs/evaluation/S5_MODEL_FACT_RELIABILITY.md` 读取，完整 SHA 同时在最终交付中提供。

## 2. 原始证据及首次错误

| 案例 | 可核对来源 | 已确认的首次错误 |
|---|---|---|
| A：Summary 类型扩展 | PPTX SHA `382670af50e0e5c7247efaad9ba23dcd0f42db5aec195bbadb74aa766fb62419`；document `fce4db0b-c790-49ad-b2a4-441769da3b94`；version `9df714d2-5fa4-47ff-a06b-1405059acbec` | 原句 `The maintenance team checked unit NP-204.`、OCR/Caption 的 UNIT 均未定义设备子类型。真实 Summary/gist/typical_question 首次增加“204号机组”，后续答案继承了它。 |
| B：字段关系混淆 | XLSX SHA `8791281e99941b5624acf3f40ef26fad81851cbaee3f2391daa9d834a4b7da04`；document `3b2870ce-81d3-4cbf-b14f-24cc1088f96a`；version `18ff1d62-d644-4376-b82c-76743fb1d0e7` | A4=West Yard、B4=WY-309、C4=51、D4=2026-10-13，表头 Site/Unit/Reading/CheckedOn 正确。最终真实请求只有正确原表，无 Summary；错误首次出现在最终模型回答。 |

两者均属于**已定位生成阶段的语义错误**；内部注意力、翻译选择等成因没有证据，不声称已经证明。未发现解析代码把 unit 确定性转换为“机组”，也没有发现表格字段在送达前被交换。

四份历史对照的原始请求/响应标识：

| 对照 | S3 Provider 文件 stem | 历史 input/output/total |
|---|---|---:|
| A 失败 Summary | `7b33910dee444abdb1dc182f15806db7` | 770 / 141 / 911 |
| A 正确字段 Summary | `6aa2d4aa61d94116a6ee9081b0adb524` | 679 / 139 / 818 |
| B 失败答案 | `3e47e2424d34458e86dd64451b1bd9cd` | 510 / 64 / 574 |
| B 正确 East Gate 读数答案 | `d16836c522df49fcb14caf1fa3c20049` | 653 / 73 / 726 |

以上均 stop、Qwen3.5-4B、temperature0.3、max_tokens1024、enable_thinking=false。它们只读复用，不计本轮消耗。原始文件保存在 S3 ZIP 的 `provider/` 下，16 个请求/响应/完成/时间文件与本地原件逐字核对一致。

S3 历史 ZIP SHA：`014fa7c26bb3a7e80e036368b069ec2d43d9688e71ffc79c02df11cd95d10780`。本轮冻结包、结果 JSON 给出文件/wire SHA，来源身份和各次新 request_id。

## 3. 已有能力与候选方案

### Summary

现有 `generate_summary.yaml` 已明确 `Base every field entirely on the provided content; add nothing that is not there`，要求保留技术术语和编号。当前模板替换语言后与历史两份 Summary system 相同。因此，不能诊断为“缺少勿编造指令”。`document_profile.py` 的 JSON 解析/长度清理不会校验“编号正确但类型被补写”的语义。

| 方案 | 唯一变化、成本 | 本轮结果与决定 |
|---|---|---|
| 现有生成逻辑 | 无改动；原输入/输出合同 | 保留真实失败及成功对照；不是稳定不出错的基线。 |
| 编号绑定事实约束 | 隔离 system 后追加一段：原文未定义实体类型时保留原短语/标签，不按编号、领域常识或更具体的译法扩展，适用全部 profile 字段。每次多93个实际输入Token，不增加额外必经模型。 | 4个候选响应符合本轮编号/类型/关系判据；有限改善，**不修改默认模板**。 |
| 带定位的原文摘录 | 本地保留完整输入行/字段及 start/end、SHA；OCR 单独标识，排除生成 Caption；不翻译类型、不补值。 | 4类材料均可逐段精确回查。但这不是完整 Summary/profile，失去综合表达及部分图像语义，**不作为生产 Summary 替代品**。未做该表达的生成测试。 |

没有实施“看到 unit 就替换为设备”“删除机组关键词”或在线事实正则。这些方案会误伤原文确实定义类型的资料，且不能验证一般事实。

### 表格

已有 `ExcelParser(..., xlsx_first_row_as_header=True)` 能直接输出：

```text
Site: West Yard,Unit: WY-309,Reading: 51,CheckedOn: 2026-10-13
```

历史默认路径为单独表头 `A: Site,B: Unit,C: Reading,D: CheckedOn` 加 A/B/C/D 数据行。已有 `_parse_excel()` 记录 sheet/row 和解析文本偏移，但正常上传没有传入该 header override；`TableRowProof` DTO 存在也不代表历史 XLSX 已产生该证明。

本轮复用该内部选项进行离线表达转换，没有新建表格框架或公开 API。原文170字符→派生190字符，两个历史问题的新实际输入 Token 各减少1；字符数不能替代 Token。保留所有行、数值和原字段，不注入问题或答案。

实验工具要求显式第一行表头，拒绝空/重复表头、合并、公式/图片函数、SHA不符或无法精确映射的内容；不从任意首行自动猜语义。派生偏移仅对应新文本，并回查真实 header/value 单元格；不冒充历史 Chunk 偏移或正式 TableRowProof。

## 4. 固定证据真实结果

Owner 新批准12次／65,000 Token，仅 tenant1 原 S3 KB、SiliconFlow Qwen3.5-4B。4份历史控制不重发；8个候选各一次，4个构造反例的原版各一次。每对只改 Summary system 附加约束或表格表达，模型、问题、其余 Context、输出参数均冻结。新 request_id，不使用旧 UNKNOWN。

| 类别 | 原版结果 | 候选结果 | 具体证据 |
|---|---|---|---|
| Summary 失败例 | FAIL（历史） | PASS（限定判据） | “204号机组”→“单位 NP-204”；37、2026-10-20 保留。 |
| Summary 正确例 | PASS（历史） | PASS | 三站点及 Unit/Reading/CheckedOn 关系类别未被改成设备子类型。 |
| Summary 缺编号 | PASS（新） | PASS | 删除原句、Caption、OCR 中所有含 NP-204 的整行，保留删除映射；双方未补编号/类型。 |
| Summary 多记录干扰 | PARTIAL（新） | PASS | 原版将西院也归入“同一天”10月12日；候选保留12日至13日范围。拼接来源边界和原文均保存。 |
| 表格历史失败题 | FAIL（历史） | **FAIL** | 原问题 `Which unit has a reading of 51?`；候选仍答“单元是 West Yard (WY-309)”。 |
| 表格正确题 | PASS（历史） | PASS | `What is the reading for East Gate?` 仍答24。 |
| 表格缺 Unit 列 | FAIL（新） | **PARTIAL** | 原版把 West Yard 当单位；候选只说 West Yard 读数51，未说明 Unit 不足。不能算完成问题。 |
| 表格列次序干扰 | PASS（新） | PASS | 仅将真实列重排为 Unit/Site/CheckedOn/Reading；双方答 WY-309。 |

人工按原文/单元格审查，判据在发送前冻结。构造缺证据/干扰材料不是官方 Gold，也没有写数据库。表格候选为 **2 PASS / 1 PARTIAL / 1 FAIL**，不能宣称解决关系混淆。

上述4个历史控制为非同期回执；temperature0.3 仍有随机性。列重排结果提示模型可能受表达位置影响，但与历史失败的比较也有时间/随机性差异，**没有证明位置偏置的因果关系**。未重复采样，不声明统计显著或总体最佳方案。

Summary 的 PASS 仅针对本轮已冻结的编号、子类型、字段关系和日期判据；候选仍把 unit 译成“单位/单元”，没有逐字保留所有英文标签，也未证明领域术语翻译最优。不能把它描述为一般事实可靠性或全部指令遵循已通过。

本轮是**人工冻结 Context 的生成诊断**，不是 Search/Rerank/API 闭环验收，也没有证明新 Summary 能改善后续最终回答。

## 5. 门禁、用量、耗时与历史保护

- 正常 `assemble` 读取已有 tenant1 principal；subject hash 与原请求一致，两个文档版本实际核对。PG system ID `7696088403504697382`、数据库 `weknora_python_port_20261013`、Redis DB15/已有实例身份通过现有核对。
- Registry 读取现有 Cheap 凭据，实际 model_id 与历史一致；不登记新模型，不复制凭据或改角色。正常 Adapter 重建的12份 wire 与冻结包及 PG 回执完全一致。
- 原 `SendGate` 对象和现有 LocalExecution 使用同一正式装配；发送前逐次预留。所有冻结请求的保守预留上界合计60,069，低于批准65,000，不靠事后补账控制。
- 新增 Summary 6次/5,979 Token，表格6次/3,300 Token；合计输入8,234、输出1,045、总9,279。全部 REPORTED/SETTLED、stop，reasoning_tokens=0；无升级、额外模型、重试或 UNKNOWN。
- 12次调用的 Adapter等待/证据落盘/PG回执间隔合计95.567秒，单次0.683～20.189秒；不是完整RAG耗时，也不是纯模型推理时间。原始时间不能独立区分DNS/TCP/TLS/排队，不据此声称性能提高。
- 授权、execution均CLOSED，未用55,721 Token临时空间撤回。S4/S3配置cloud=false；没有启动Worker、上传、Embedding、Rerank或重建索引。
- 所有旧 Provider 行指纹和默认角色配置指纹前后相同；原18个未提交文件SHA全部相同。旧UNKNOWN未结算、释放或重发。

新证据 ZIP：`evidence/S5_FACT_RELIABILITY_REAL_20261019.zip`，SHA `4d924bff47ba262f22198714c301e649a93536f0d104273d20928eae57f9528b`；保存12次原始请求、响应、完成和时间记录及本轮CLOSED账本。无密钥或请求头。冻结包保留其冻结时的 NOT_RUN 标记，新增结果以本报告/results/新ZIP为准，不覆盖历史快照。

## 6. 修改与实际命令

仅新增实验代码和交付：

- `scripts/s5_fact_reliability.py`：从真实输入冻结8类对照，验证SHA、复用ExcelParser、记录派生来源。
- `scripts/s5_fact_validation.py`：新授权校验、正常Registry/Adapter/Gate、一次性batch claim、持久化结算及关闭。不会续额、自动重发或重置旧记录。
- `tests/weknora_port/test_s5_fact_reliability.py`：来源映射、缺字段、歧义拒绝、单变量及限额计划合同。
- 本报告、results、冻结JSON与真实回执ZIP。**`backend/`、前端、生产模板和Schema改动0。**

以下均在本工作树实际执行：

```powershell
$env:PYTHONPATH='E:/WK/deps/reader;E:\RAG-Worktrees\weknora-python-port-20261013\var\weknora-port\runtime-deps'
& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest -q tests/weknora_port/test_s5_fact_reliability.py tests/weknora_port/test_document_profile.py --tb=short
# exit0: 30 passed in2.22s，离线真实解析/工具测试，不模拟模型质量

& 'E:\RAG quention\.venv\Scripts\python.exe' -B scripts/s5_fact_reliability.py --source-root 'E:\WK\run\s3-product-20261019' --output 'docs/evaluation/evidence/S5_FACT_RELIABILITY_CASES_20261019.json'
# exit0: 8 frozen cases, Provider0

& 'E:\RAG quention\.venv\Scripts\python.exe' -B scripts/s5_fact_validation.py prepare --config 'E:\WK\run\s4-quality-20261019\local.json' --authorization 'E:\WK\run\s5-facts-20261019\owner-authorization.json' --manifest 'docs/evaluation/evidence/S5_FACT_RELIABILITY_CASES_20261019.json' --output 'E:\WK\run\s5-facts-20261019'
# exit0: ZERO_SEND_PREPARED; 12 requests / upper60069
& 'E:\RAG quention\.venv\Scripts\python.exe' -B scripts/s5_fact_validation.py run --config 'E:\WK\run\s4-quality-20261019\local.json' --authorization 'E:\WK\run\s5-facts-20261019\owner-authorization.json' --manifest 'docs/evaluation/evidence/S5_FACT_RELIABILITY_CASES_20261019.json' --output 'E:\WK\run\s5-facts-20261019'
# exit0: 12 SETTLED, CLOSED。授权已关闭且batch claim存在，不可重跑。

git diff --check
git diff --cached --check
# 最终均exit0
```

首次工具失败保留：裸venv缺pandas（exit1），复用已有产品PYTHONPATH后通过，没有安装依赖；冻结器首次canonical哈希遗漏sort_keys，发送前SHA核对拒绝（exit1），已修复；来源SHA抄写错误在发送前校正。交付汇总首次误假设LocalExecution原始capture含wire_sha256（exit1），改按body重算并对照PG回执，没有补发模型。首次暂存检查发现测试EOF空行（exit2），只清理新增测试文件后通过。无关旧文件CRLF警告保留。

**未实现的命令不得报告通过。** 全量测试、六套重评、普通API/RAG验收、原文摘录生成、重生成Summary后的最终回答验证均 **NOT_RUN**。

## 7. 决策与剩余风险

1. **优先级1：Unit/Site生成绑定仍未解决。** 原文已正确送达，单靠重复表头不足；本轮不为单题继续加规则、Prompt或阈值，也不自动把答案替换成选定单元格。字段缺失时仍可能绕答。
2. **优先级2：Summary类型约束仅有限支持。** 可保留本轮隔离附加约束与四类反例供后续决策；不宣称一般事实校验已经实现，不推广S2被否决的完整性Prompt，不修改默认Summary。
3. **优先级2：表头配置产品化不是已完成修复。** 内部能力存在，但正常上传尚未暴露可靠的显式选择；如后续确需接通，应保留首行确认/来源映射，并独立验收。当前主失败并未被它修好，没有为该候选扩张实现。

本阶段选择保留生产行为，提交可复核的成功与失败，不把模型局限伪装成代码修复。只本地提交，停止；不Push、部署或进入下一阶段。
