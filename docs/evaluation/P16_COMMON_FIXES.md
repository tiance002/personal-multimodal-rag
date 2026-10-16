# P16 — 参考开源实现修复共性问题

## 1. 结果与范围

**P16_PARTIAL：计量修复通过离线验收；0434识字/关联结论明确；四次历史漏引用的生成或解码原因未决。** 不把缺失证据解释为生成质量问题，也不推测修复decoder。

接手HEAD `3055bf5d8f41d42ee5fb13d3f4355292e45dc937` 已含独立P16 OCR验证。原 `.serena/project.yml` 及六个未跟踪文件保留。本轮实现提交 `c2f7197abbf9336bea7d19e047f7a5f7c7b713ae`；只修改三个生产文件：models/provider.py、usage.py、usage_facts.py，添加一个定向测试及十份真实响应夹具/哈希清单，无迁移或依赖变更。剩余564个既有生产文件哈希不变。

## 2. SiliconFlow Rerank计量修复 — PASS（真实响应离线重放）

确认Bug：原ProviderModel.rerank只读顶层usage，P13/P14真实响应则在meta.tokens报告Token，导致应用未识别。LiteLLM参考采用固定提交 `7c49197f29d136bdde0b997aead2a8486d79d72d` 的 [litellm/types/rerank.py](https://github.com/BerriAI/litellm/blob/7c49197f29d136bdde0b997aead2a8486d79d72d/litellm/types/rerank.py)，它将Token与计费单位分开建模；只参考边界，不引入LiteLLM依赖，不照搬其他provider计费方式。下载字节及SHA256保存在references/，GitHub API限流403与另一路径下载失败不影响已取得的类型源码；不把未取得文件称为已审查。

最小适配：

- 仅SiliconFlow rerank路径在顶层usage缺失/为null时读取meta.tokens；已有有效顶层usage保持优先，不混合两个来源。
- 明确input_tokens/output_tokens为Token。**billed_units的search_units、classifications或同名Token字段均不作为Token来源或补缺值**；即使真实样本二者数值相等也不互相替代。不计算人民币。
- 明确报告input/output且image_tokens=0时，允许本适配语义按两者求和；facts保留原total字段“不存在”，求和记为derived_total，不伪造供应商total字段。普通OpenAI缺total规则不变。
- 计量缺项、bool/负数/字符串/浮点、矛盾total、图像Token非零或缺失时保持UNKNOWN，不将缺失补为零，也不猜图像Token是否已包含于input。
- 前序已观测占用下界不能被后续较小计量覆盖；多物理请求逐个门控，汇总只加一次，混合旧usage与新meta结构兼容。
- 只影响未来实际请求的正常结算路径。本轮未调用真实模型，未重放历史finish/结算，不迁移或修正任何旧账本行。

十份原始响应= P13六份 + P14四份，夹具逐字匹配原response.body的SHA256；通过httpx.MockTransport走实际ProviderModel/transport/Gate离线回放，124+186=310个排名/分数不变，10/10 Token计量被识别并传给finish Gate。样本报告input合计73957/output0，**这是历史响应的离线验证值，不是本轮消耗**。真实持久化PG结算新调用及真实供应商验证均NOT_RUN。

## 3. Cheap漏引用 — 历史原因UNRESOLVED，无生产修复

| 数据集/QID | Context数 | 应用答案标记/引用 |
|---|---:|---:|
| MIRACL-ZH 692002#0 | 1 | 0/0 |
| MIRACL-ZH 704170#0 | 1 | 0/0 |
| LongBench-ZH dureader:5726f5fc6feedda99cafb275a27a8fd67483185b6fb8efa7 | 9 | 0/0 |
| LongBench-ZH multifieldqa_zh:1a6c19fed6361cbfbfd0df3fb7ca1e01f2a2ffa0da9903bc | 8 | 0/0 |

四个实际请求含启用的引用协议、具名Context handles、finish_reason=stop。已解码应用SSE、持久化答案、P9报告raw_answer逐字一致，没有应用解码后再存储丢失引用的证据。**但P9 raw_answer并非供应商原始输出**：观察器保存请求body、响应状态及解析后的usage/finish，却未保存响应body/predecoder SSE delta；178个provider记录的字段库存与源码一致。原始响应路径与哈希在逐题JSON中明确为null/NOT_CAPTURED，因此无法可靠区分生成遗漏与解码丢弃。

规范ref、无标记、虚构handle、伪造public tag四类合成decoder检查共100个双片边界通过。规范引用在这些反例中不会丢失，未知/未授权引用不会被补入；这不能冒称历史供应商replay。未确认decoder Bug，不修改Prompt，不添加推测性补引。

另直接查看RAGFlow [v0.27.2 rag/nlp/search.py Dealer.insert_citations](https://github.com/infiniflow/ragflow/blob/v0.27.2/rag/nlp/search.py#L316)：其生成后相似度选择的引用插入是候选方案，不能单凭相似度证明当前答案逐主张得到证据支持。保存tag/path及下载内容SHA256，不声称tag是不可变commit。未照搬其阈值/降阈值循环，也未执行embedding。当前无冻结样本语义支持验证通过，故不接入补引功能。本地已有架构对照不是citation源码，二者明确区分。

缺失历史供应商原始内容是本轮剩余阻断。以后如要对新请求定位，需同时保存解码前内容与应用输出、Context映射；新的请求不能冒充这四次历史原因。本轮不再调用模型以猜测旧原因，不擅自批准延期或宣称漏引用已修复。逐题完整路径、SHA、输出和映射见results.json/citation_diagnosis与E:/WK/run/p16-common-20261016/citations/。

## 4. 0434 — 复用已有真实本地OCR，停止章节实验

当前基线已包含 `P16_TITLE_OCR_ASSOCIATION.md/.results.json`：原有TesseractOCR.recognize真实运行一次，9行原始结果中识别Coding time!，区间[5,17)，带图片bbox、原件/图片/middle SHA和document/version/page32指针。未人工补字。原报告/日志/失败证据哈希保持不变，本轮不重复OCR、不换OCR引擎、不下载模型。

识字结果无标题层级/章节终点/parent关联；原PDF也没有TOC/Outlines/StructTreeRoot补足。到第34/35页数据集证据仍无可靠跨页关联，不用邻页或Gold补关联。**Rerank NOT_RUN，不构建增强payload、不打开围栏、不接入生产。** 现有Docling/PaddleOCR/LlamaIndex参考不构成当前已保存文档存在章节关联的证据，故不扩张到这些框架的接入。

## 5. 实际命令与测试

工作树E:/RAG-Worktrees/weknora-python-port-20261013；Python原venv，PYTHONPATH沿用仓库、E:/WK/deps/reader、var/weknora-port/runtime-deps，日志/缓存E盘。

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B -m pytest tests/weknora_port/test_siliconflow_rerank_usage.py tests/weknora_port/test_provider_usage.py tests/weknora_port/test_authority_usage.py tests/weknora_port/test_output_budget_contract.py tests/weknora_port/test_provider_transport.py tests/weknora_port/test_provider_stream.py -q --tb=short -p no:cacheprovider --basetemp E:/WK/run/p16-common-20261016/unit-final
```

exit **0**：104 passed in1.35s；日志 `E:\WK\run\p16-common-20261016\unit-final.log`。

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p16-common-20261016/citations/diagnose.py
```

exit **0**：4 application evidence reconciliations;100 synthetic split positions；日志 `E:\WK\run\p16-common-20261016\citations\diagnose.log`。

```powershell
& 'E:/RAG quention/.venv/Scripts/python.exe' -B E:/WK/run/p16-common-20261016/ledger-audit.py
```

exit **0**：original account/month/calls and UNKNOWN unchanged；日志 `E:\WK\run\p16-common-20261016\ledger-audit.log`。

首轮计量测试67pass，补充已观测下界反例并加入受影响传输/流式契约后最终104pass（1.35s）。引用诊断首轮合成fixture将public doc误期望为knowledge_id而失败，仅修正实验预期为真实document_title并保留首失败日志，最终exit0。没有修改旧断言使生产结果变成功。报告生成首轮因Windows路径分隔符与允许清单比较失败exit1，未写报告；仅规范化审计路径后重试，原日志保留。**未实现的命令不得报告通过。** 未运行全量回归、正式浏览器或真实模型问答。

## 6. Token、历史保护、结束

新增真实模型请求 **NOT_RUN/0**；新增供应商Token0、新UNKNOWN0、未建立围栏。公开GitHub源码读取是参考资料HTTP，不是供应商调用。只读PG system_identifier7696088403504697382，weknora_python_port_20261013；618条SiliconFlow调用/61条UNKNOWN/reserved0、月账本及DeepSeek记录与P14关闭快照逐值相同。旧UNKNOWN未修改或释放。P11—P15既有保护证据、P16独立OCR结果和输入、P13/P14原始响应哈希不变。原工作区改动保留。

交付状态：计量兼容PASS；引用四题现象及证据边界明确，但生成/解码根因仍UNRESOLVED；0434 OCR/无可靠关联结论PASS。综合不宣称全部通过，状态P16_PARTIAL。必要下一步仅建议取得未来解码前后成对证据；本轮不自动采集、不加生成后补引，不继续Rerank或框架改造。无Push、部署、合并、Tag，完成限定工作后停止。


## Owner补充验收

见[P16_ACCEPTANCE_SUPPLEMENT.md](P16_ACCEPTANCE_SUPPLEMENT.md)：补充官方SiliconFlow结构证据、十份真实响应Token/billed_units反例和四题三类原因的证据缺失分类；0434关联记录为TD-P16-0434-SECTION，按Owner排除本轮研究，无新增OCR/Rerank。状态保持PARTIAL_SAFE_STOP，不以无法确认的历史原因宣称修复。
