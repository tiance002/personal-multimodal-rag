# S2：模型路由、答案质量与响应稳定性

日期：2026-10-19。结论：**PASS，限本阶段验收范围**。不代表所有答案事实正确、Qwen 已完成普遍选型验证或已进入 S3。

## 1. 版本、范围与决定

- 工作树：`E:\RAG-Worktrees\weknora-python-port-20261013`。
- 分支：`codex/weknora-python-port-integrate-image-20261014`。
- 起点：`e9b89404317b92f65d627ab077dc32e09ce91d25`。
- 实现提交：`dcc61d91eeec53f107f0cd63b937a4975e8ae81a`。本报告和机器结果随后单独本地提交；交付提交完整 SHA 以该提交的 Git 记录和最终交付消息为准。
- 开始时的 18 个未提交路径全部按 SHA256 核对保持原样，未纳入本阶段提交。包括 `progress.md`、M4 分析工具、`scripts/s1_normal_api_check.py` 及其关联旧修改。

**主要决定：不采用新增的通用完整性指令。** 六组同 Context、同 Qwen 参数的 A/B 中，没有稳定增益，却出现一次输出截断和更严重的实体关系混淆。生产 `generation/context.py` 已恢复起点内容，查询理解、检索、路由阈值、模型默认角色均未改变。候选指令只保存在诊断脚本及冻结回执中。

保留的生产改动只解决终态可见性：区分运行完成、输出截断和空答案；不把 `stop` 当成语义正确，不新增语义裁判或自动补引用。

## 2. 授权、身份与真实资源

Owner 本轮明确批准六组 Qwen A/B、最多一次 tenant 1 正常 API 问答，所有调用合计不超过 24 次 Provider；每个执行文件继续受既有 12 次／100,000 Token 保护。没有新设阶段累计 Token 上限。新 UNKNOWN 即停止。

实际 **17 次 Provider，输入 29,985、输出 3,947、合计 33,932 Token；新增 UNKNOWN=0**。所有调用均有供应商 usage 和持久化记录。授权已 `CLOSED`，未使用的 7 次调用已撤回；旧 UNKNOWN 不结算、不释放、不重发。

| 项目 | 实际核验 |
|---|---|
| PostgreSQL | `127.0.0.1:56423`，`weknora_python_port_20261013`，system ID `7696088403504697382` |
| Redis | `127.0.0.1:56424` DB15，run ID `4c5d8c6990f72ba7ac4060d59a6e7cb7b5625ab6` |
| 日常配置 | 复用 `E:\WK\run\p81-retirement-20261018\local.json`；磁盘云开关仍关闭 |
| tenant 1 | 正常持久化 local principal；Cheap 和查询理解实际为 SiliconFlow `Qwen/Qwen3.5-4B` |
| tenant 332 | 复用 P25 已验证主体；默认 Cheap 仍为 Xing，未改角色配置 |
| 隔离 Qwen | 经批准，用 tenant332 已有 SiliconFlow 凭据通过现有 ModelStore 登记候选；未复制密文或改变默认角色 |
| 正常 API | 现有 `backend.weknora_port.entrypoint`，只监听本机 56425；无 Worker、无上传、无重建索引；验收结束已停止 PID 49960，PG/Redis 保留 |

Qwen ID：tenant1 `mdl96a368bfb0c176c6e38e325c53a030ae3e40b000254c17d19a854f4495846`；tenant332 隔离候选 `mdlf5350f8b80d8971c14ddaa8524f8a039d414e85c35a657e4271228473b25e`。两者真实回执均为准确模型名，不以请求路由标签推断身份。

发送继续使用原 Registry、Adapter、LeasedSendGate、LocalExecution 和 PG 调用账本；没有替换 Gate 对象。前后只读快照确认：**528 条历史调用、4 条历史 UNKNOWN、204 组模型角色配置、59 份来源和 496 个 Chunk 未变**。新增加的 17 条调用与本轮记录逐一相符。

## 3. 根因与实现

| 文件／函数 | 确认问题与改动 |
|---|---|
| `http_api.py::messages` | P5 存储以 `is_completed` 表示成功，`turn_status=pending` 本身并非数据库坏状态。公开历史接口现在将成功完成投影为 `completed`，保留 error/cancelled；不修改旧消息行。 |
| `runtime.py::list_messages` | 历史原先没有最终生成的 finish/quality 信息。通过现有 turn/run/attempt 表只读关联，取同 tenant、subject、session、run 和 assistant message 的最终已完成 attempt，返回 `finish_reason`、`generation_issues`。不误取被升级淘汰的 Cheap 状态。无 Schema 变更。 |
| `generation/coordinator.py::run` | SSE complete 增加最终选中 attempt 的 `generation_issues`，不汇总前次失败造成假截断。原 P5 生成和升级判断不变。 |
| `WeKnoraPythonApp.tsx`、`types.ts` | 实时与历史共用终态解释；明确显示“输出被截断”或“模型未返回可用答案”。续读返回时不把这些终态覆盖为 unknown。不增加提交、自动重试或再次计费。 |
| `scripts/s2_generation_quality.py` | 一次性固定 Context A/B 工具：校验历史文件 SHA、原始消息、候选身份、当前 wire；保留原始请求／响应、独立 request ID 和正常用量记录。空结果、未执行、只有 A 或存在 UNKNOWN 均不能结算为已完成的双臂。 |

增加对应的 `test_generation_visibility.py`、`test_s2_generation_quality.py`，以及既有 `test_http_api.py`、前端 `adapter.spec.ts` 的定向反例。

没有修改模型参数默认值、Prompt、P5 评分、Rerank/Context、OCR、取消、幂等和 UNKNOWN 结算逻辑。

## 4. 六组固定证据真实 A/B

这里是**固定 Context 生成诊断，不是自动检索验收**。两臂均为 Qwen、`enable_thinking=false`、`temperature=0.3`、`max_tokens=1024`；用户消息、历史、原文、顺序不变。B 只增加通用完整性要求。Gold 只在生成后核对，不进入请求。

| 样本 | A 原指令 | B 完整性指令 | 事实判定与决定 |
|---|---|---|---|
| MML-0959 | stop；3.22s；1,264 Token | stop；1.63s；1,347 Token | 两者均回答 radio waves、microwaves；电离层折射及卫星信号有来源支持。本轮 A 已不漏答，不能把历史 Xing → 当前 Qwen 的差异归功于 B。 |
| LongBench 美国节日 | stop；13.42s；7,483 Token | **length**；25.71s；7,572 Token | A 有主要节日名，但“感恩节非联邦”等依赖格式含混的星号标记，判 UNVERIFIED；“多数州法定”无当前来源支持。B 增长后在万圣节描述处截断，漏后续节日，拒绝采用。此样本不计官方生成准确率。 |
| Hotpot 5a8911 | stop；2.62s；1,771 Token | stop；6.54s；1,796 Token | 核心页名两者均为 Miss Universe 2010，但 A 把 Stefania 说成该届获加冕赢家；B 进一步混淆 Ximena/Karin、两场赛事的加冕关系。来源对此关系明确，属于生成错误。核心 Gold 词命中不等于整段事实正确。 |
| Hotpot 5a745a | stop；4.02s；1,556 Token | stop；1.76s；1,612 Token | 两者选 The Wind in the Willows。当前 Context 是其图书 1908 年与另一对象电影 1983 年，不能当成两本书的完整出版日期证据。A 说明缺少小说日期；B 的“早75年”比较跨类型，支持不充分。 |
| Omni Summary-only 缺证据 | stop；3.65s；1,075 Token | stop；34.53s；1,135 Token | 两者都说明缺少具体方程／数字，没有编造矩阵；不把主题 Summary 当数字事实。工程缺证据诊断，无官方 QA Gold。 |
| Omni MinerU 原文正例 | stop；7.52s；1,503 Token | stop；8.38s；1,599 Token | 两者矩阵的每个位置、负号、末列均与真实原文一致。无 B 独立增益。 |

双臂合计 12 次调用、29,713 Token。上述时间包含现有门禁、网络和本地流读取／记录，不能拆解为纯模型运算时间，也不能把单个 B 延迟直接归因为 Prompt。

**能力结论：** 当前 Qwen 能从完整结构提取正确矩阵，也能在这个缺证据反例中诚实说明不足；但非空 `stop` 仍可能存在实体关系错误、无依据补充及漏答。六个小样本不支持推广新的完整性 Prompt，也不支持宣布 Qwen 普遍优于 Xing。

## 5. 正常 FastAPI → SSE → PG 回归

本轮新提交一次，无人工选择 Chunk、无理想 Context 注入、无重传资料：

- 原问题：**请写出课件中线性方程组的系数矩阵，以及增广矩阵最后一列的三个数。**
- tenant1 KB：`9d3461a5-080c-4b5e-a367-c82b79d3013c`，`document_scope=[]`。
- client request：`0a199ee8-1ecb-4b6a-9455-048e62c755ff`。
- server run：`ccff9480-c184-428e-8e8b-212a6541458b`；session：`af64c76e-8955-40d5-9183-fab16375413f`。两种 ID 各按原合同使用，未再发生 API 身份错配。

| 层 | 实测 |
|---|---|
| Query Understanding | Qwen，intent=`kb_search`；Rewrite 为“课件中线性方程组的系数矩阵及增广矩阵最后一列的三个数”。 |
| Search | 2 条：真实 MinerU 原文 `96ad695f-bb32-4df9-9cfb-1e8281f2fc31` 和 Summary `a06e7826-577a-53ca-bd68-f53d8315a160`。 |
| Rerank | 真实 BGE 原始分数：Summary `0.9610400199890137`；原文 `0.8417180776596069`；0.3 原阈值下两条均通过，MMR 保留2条。后续综合 score 与模型原始 score 分别记录，不混用。 |
| Merge／Context | 两条保留；解码真实生成请求的 `<content>`，与 PG 中对应 Chunk 正文逐字相同。 |
| 来源身份 | document `996fda57-6cc2-4045-a3ff-7416f037459a`；version `91f260da-03e3-422e-ae08-b6f818ee3b7b`；原件 SHA `95a3151c2c9f5f3d9e36392065ecd1e7ab6cf49ae9c591b698cff7196ab78601`。 |
| 路由与生成 | DYNAMIC 分数0.1、阈值0.35 → Cheap Qwen；non-thinking、max_tokens1024；stop，无 fallback、无升级、`generation_issues=[]`。 |
| 事实 | 系数矩阵 `[1,-2,1; 0,2,-8; -4,5,9]`，末列 `[0,8,-9]`，逐项与实际原文一致。 |
| 完成与回读 | SSE 答案=PG 答案=两次 GET 历史答案；公开状态 completed，历史 finish=stop。GET 没有新增 Provider 调用。 |

用量：查询理解 2,385；Embedding 两次 26+39；Rerank 366；最终生成 1,403；共 **5 次／4,219 Token**。整次 HTTP/SSE **6.515s**；首次页面答案事件 **6.512s**。DYNAMIC Cheap 缓冲输出，因此后者不等同供应商首 Token。最终生成 transport open 至完整读取约1.759s，至响应头约0.322s；现有记录不能独立分开 DNS/TCP/TLS/供应商排队。

公开引用 UI 仍关闭。真实回答末尾出现了有效的 `d1/c2` 自然语言来源句柄；与实际原文对应，没有虚构来源，但展示清理仍不完整。**本报告不把这项展示限制写成已解决。** 不为重新获得干净答案追加请求。

## 6. DYNAMIC／Expensive／UNKNOWN 的验证边界

- 新增真实验证：上述 Qwen Cheap、DYNAMIC 选择及正常完成；12次固定 Context 均直接使用授权候选，不冒充动态路由运行。
- **复用而非新增：** M2 `53efc436-1464-4f02-bdf3-4cb32747a4a3`，DeepSeek Expensive 正常 stop；M2 `86b39352-e93e-42a7-9478-fa25e63cd019`，Cheap 48输出Token length → Expensive 34输出Token stop。原回执单独归档，未计入17次／33,932Token。
- 升级判断保持 P5：Cheap DYNAMIC 仅因 TRUNCATED、EMPTY、INVALID_HANDLES 等已有客观合同受控升级一次；同 Context、原有授权。没有增加语义正确性检测，也不把一段非空错误答案称作 QualityGate 已能识别。
- 历史美国节日：Xing max_tokens1024 length 后**已经升级**；DeepSeek 同样1024输出预算全部记为 reasoning_tokens，正文零、finish=length。问题不是“没有触发升级”。本轮不提高全局上限或再次外发 DeepSeek，以免把参数、模型、Prompt 多因素混作一次改进。
- 失败／取消／UNKNOWN 定向合同继续通过；UNKNOWN 不当普通错误自动重试，不换模型重复未知操作。`cost_status=UNKNOWN` 的现金价格字段与 Provider outcome UNKNOWN 区分；本轮实际 Token 均为 REPORTED，不估算费用。
- 实际 PG+ASGI 只读历史回查也验证了一个正常 stop 消息及一个历史 length/EMPTY 消息的投影，新增 Provider=0。

## 7. 实际命令与退出码

工作目录均为上述工作树；Python=`E:\RAG quention\.venv\Scripts\python.exe`。测试禁用插件自动装配，使用仓库现有工具。

```powershell
# 生成、协议、Provider和升级原合同；exit 0，130 passed
& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest --noconftest -p no:cacheprovider -q --tb=short tests/weknora_port/test_generation.py tests/weknora_port/test_generation_wire.py tests/weknora_port/test_generation_protocol.py tests/weknora_port/test_provider_stream.py tests/weknora_port/test_provider_request.py tests/weknora_port/test_generation_visibility.py

# 撤销候选Prompt后最终定向检查；exit 0，16 passed
& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest --noconftest -p no:cacheprovider -q --tb=short tests/weknora_port/test_generation_visibility.py tests/weknora_port/test_http_api.py::test_history_api_projects_completed_status_and_preserves_failure_outcomes tests/weknora_port/test_s2_generation_quality.py

# frontend目录；真实浏览器、业务API/SSE为mock；exit 0，19 passed
$env:PLAYWRIGHT_CHROME_PATH='C:\Program Files\Google\Chrome\Application\chrome.exe'
npx playwright test --config playwright.weknora.config.ts tests/weknora-port/adapter.spec.ts --reporter=line --output E:/WK/run/s2-output-status-20261019/playwright-results
npm run build # exit 0，274 modules

git diff --check # exit 0，已有文件LF/CRLF警告不算失败
git diff --cached --check # 实现提交前exit 0
```

首轮新增 UI 断言曾有2项失败，定位为流读取结束把 truncated/empty 覆盖成 unknown；最小修复终态白名单后19项通过。首次失败 DOM 和最终日志同时保留。

真实实验命令使用以下固定参数，prepare一次、六个case各run一次，均 exit 0。完整命令及输出保留在 `E:\WK\run\s2-generation-20261019\*-send.log` 和原终端记录；manifest 列出精确 case、输入 SHA 与历史路径：

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -B scripts/s2_generation_quality.py prepare --config 'E:\WK\run\p81-retirement-20261018\local.json' --principal-state 'E:\WK\run\p25-20261017\runtime\state.json' --manifest 'E:\WK\run\s2-generation-20261019\cases.json' --authorization 'E:\WK\run\s2-generation-20261019\owner-authorization.json' --output 'E:\WK\run\s2-generation-20261019'
# 历史实际执行：同参数，prepare改为run，并分别追加以下case；授权现已关闭，禁止重发
# --case mml0959-radio-waves
# --case longbench-us-holidays
# --case hotpot-5a8911-unsupported-hosted
# --case hotpot-5a745a-success
# --case m32-omni-summary-only-no-evidence
# --case s1-omni-matrix-positive

& 'E:\RAG quention\.venv\Scripts\python.exe' -B scripts/s1_normal_api_check.py --config 'E:\WK\run\p81-retirement-20261018\local.json' --authorization 'E:\WK\run\s2-generation-20261019\api-owner-authorization.json' --output 'E:\WK\run\s2-generation-20261019\normal-api' --helpers 'E:\WK\run\phase01-evidence-20261018\run_case.py' --case-id s2-daily-api-20261019 --max-calls 12 --max-tokens 100000 --send
# exit 0，COMPLETE；以上是已结束的历史执行命令，不是再次发送许可
```

未运行：新的 Expensive／升级请求、完整 Edge UI 问答、全量六套测评、OCR／上传／索引重建。没有使用模拟回执替代上述真实模型结果。前端 mock 测试与正常真实 API 验收分别报告。

## 8. 证据、限制与后续最小建议

- [机器结果](S2_MODEL_ROUTING_ANSWER_STABILITY.results.json)：逐臂答案、receipt、input/output Token、时间、身份和事后事实分类。
- [证据包](S2_MODEL_ROUTING_ANSWER_STABILITY.evidence.zip) 与 [逐文件 SHA 清单](S2_MODEL_ROUTING_ANSWER_STABILITY.evidence.json)：包括新请求、原始供应商SSE、时间、正常API、PG/历史读回、测试与首次UI失败；历史复用证据放在独立目录。请求观察器不保存敏感头，归档前凭据模式检查通过；无密钥或数据库连接配置。
- 正常 API 检查复用了起点已修改但未提交的 S1 执行工具和 M4 结果合同。它们未混入 S2 代码提交；证据包 `execution-source/` 单独保存实际工具版本及 SHA，绑定本次运行，避免把仅干净 checkout 描述成相同执行工具。
- 本地完整记录：`E:\WK\run\s2-generation-20261019`，测试记录另在 `E:\WK\run\s2-output-status-20261019`。

仍需 Owner 决定后续范围：

1. **生成事实关系与完整性限制：** 现有非空 stop 可能读反主体／事件。优先以当前小样本和负例设计可比较的模型／输出约束实验；不声称已经具备自动事实审计，不继续堆通用 Prompt。
2. **长回答与 reasoning 预算：** 已明确旧问题发生在输出上限及推理消耗；如需下一阶段处理，应同时验证约束任务范围、模型输出合同与末次 Expensive 截断展示。本轮只修展示，不擅自扩大默认上限。
3. **自然语言内部句柄展示：** 模型可能在普通来源说明里写 d1/c2，现有清理没有覆盖全部表达。建议单列小范围展示修复，禁止全局删除这些普通字符串或编造替代来源。

这些限制不被隐藏，也不将已有真实正常样本外推为全局事实质量合格。S2已完成批准的对照、必要实现、真实API和历史升级复核；到此停止，未 Push、未部署、未进入 S3。
