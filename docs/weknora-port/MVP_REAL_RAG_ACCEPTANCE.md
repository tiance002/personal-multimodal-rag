# MVP 真实 RAG 验收 — 2026-10-16

## 1. 结论与实际启动

**`MVP_REAL_RAG_PASS`（限定公开文本和下列三类样本）；`CHEAP_REAL_RAG_PASS / EXPENSIVE_BLOCKED`。**

真实文本→解析/切块→真实 Embedding→PostgreSQL/pgvector→现有检索与真实 Rerank→真实 Cheap 回答→逐原文引用回读已跑通。三类最终样本中，无证据项复用现有固定拒答，未调用回答模型。**不代表本轮所有尝试通过**：最初的四个 TLD 问题仍有已保留的错误答案/缺引用；不宣称质量验收、六数据集评测或发布通过。

- 工作树 `E:\RAG-Worktrees\weknora-python-port-20261013`，分支 `codex/weknora-python-port-integrate-image-20261014`；开始 HEAD `e20e06c7d2662831c1e1d754ac70329f8259bf1e`。Recipe 修复 `561e3bc8a5a6d13a7199c48f35daf40be3cd04e1` 已存在，未重做。
- 恢复原 `eviroute-rag-postgres` / `eviroute-rag-redis`；PG `56423` / `weknora_python_port_20261013` / physical **7696088403504697382**；Redis `56424` / DB15 / run_id `87b4c2a9f76f9f94b34b5f21fe31a967926f343a`。未重建环境、清库或 flush。
- 真正标准入口 `python -B -m backend.weknora_port.entrypoint --port 56425 --authority-config E:\WK\run\mvp-20261016\authority.json`；approved authority `/readyz` **200**，没有注入 mock 或关闭授权保护。
- React/Vite `56426`，代理到上述 API。用户现有 Edge 扩展会话实际读取最终 Q2、点击引用并显示版本/原文。上传和提问由正常 HTTP API 驱动；**不声称本轮在浏览器里执行上传和发送问题**。
- 文本摄取在 API 内同步完成，无需图片/记忆 worker；独立 worker **NOT_RUN**。不为非必要 CLI/解析入口展开修复；前端、app 和解析服务按 Owner 指示暂不容器化。
- tenant **331**，KB `761cc140-5657-42f3-9bb5-608e701225d6`，document `79a20804-ae02-4036-b952-ba3e060a07b3`，版本 `9683c549-a4a3-4a67-8e24-9ef6a0fd67ce`。独立资产目录和 Redis 前缀记录在 E 盘 `state.json`。

## 2. 最小闭环实际结果

资料：完整公开 [RFC 2606](https://www.rfc-editor.org/rfc/rfc2606.txt)，8008 字节，保留原版权文字；SHA-256 `b6869c8984701701bc2e6973b6ffc750d497f845cc1a65a106e9301590a13ab0`。本轮 KB 采用既有 chunk 配置 `1000/100`，没有改切块算法。

| 步骤 / 样本 | 结果 | 实际依据 |
|---|---|---|
| 正常上传、保存、解析、切块 | PASS | `upload.json` completed；10 个原文 text chunk，另有 1 个生成摘要 |
| 真实 Embedding / 向量索引 | PASS | 11 行向量，均 1024 维；包含既有摘要索引流程，摘要不作最终证据 |
| 数据库和原文回读 | PASS | chunks / vector / 文档版本读回；正常 source API 返回原字节，SHA 与上传相同 |
| Q4：第3节三个示例二级域名（直接事实） | PASS | 真实 Xing 正确回答；1 条原文引用 `4d895032-c33f-4a18-b514-433c0c8336b1` |
| Q2：DNS 测试顶级域＋三个示例二级域（结合两段） | PASS | 正确回答；分别引用第4/5段 `9414fe6b-320f-440c-9d0b-0b51497c125a` / `4d895032-c33f-4a18-b514-433c0c8336b1` |
| Q3：文档未给出的 example.com 年注册价格 | PASS（现有固定拒答） | 真实检索之后空证据，零回答调用、零伪造引用，不输出价格 |
| 最初 Q1 四个 TLD 的重复尝试 | FAIL，保留 | 原答案依赖生成摘要；修复后一轮输出耗尽；关闭可选思考后另一轮答错且无引用。不将任一轮改成 PASS |
| DeepSeek / Dynamic / Expensive Only | BLOCKED / NOT_RUN | 原月 `USAGE_EXCEEDS_RESERVATION` 及 UNKNOWN 不解除 |
| 六套数据集整批评测、GraphRAG/Agent/Sandbox | NOT_RUN | 不属于本轮 |

最终三类样本使用 `--round verified` 的 Q4/Q2/Q3；原 initial、final-Q1、verified-Q1 全部保留。Q2/Q4 的 reference 与 `GET /chunks/{id}` 的 ID、正文、start/end、KB、document、source_version **逐项相同**，正文也确实出现在下载的原文中。三类样本是有限基础检查，不是“3/3 总质量准确率”，更不能排除已观察到的 Q1 失败。

## 3. 真实模型、账目与 Token

- 真实模型均为 SiliconFlow：`BAAI/bge-m3`、`BAAI/bge-reranker-v2-m3`、`XingChenAGI/Xing4.0-29B`；普通 ProviderModel/真实 HTTPS，**Mock=0、DeepSeek 新调用=0**。requested/reported 身份和 numeric usage 保存在 ledger/SSE，模型请求名不冒充供应商内部物理实例证明。
- `CHEAP_ONLY` 使用既有模式，不改打分或升级规则。最终问题使用 API 已有 `reasoning_effort=off`，处理已观察到的 1024 Token 全思考/空可见输出；不扩大 cap Recipe。默认开启思考时的截断仍保留，未谎称默认设置全部通过。
- Owner 最新直接授权：除 DeepSeek 累计100万消耗 Token 外，其它模型可直接用、不逐次询问。SF 账目固定到 **siliconflow-owner-main / siliconflow**；额外自限100万围栏只约束这次 MVP 执行，不声称 Owner 给 SF 设了100万额度，也没有从 DeepSeek 池挪用额度。
- 所有尝试合计 **62 次物理模型请求**：embedding38、rerank8、chat16（包括入库摘要、查询改写和回答）；全部 RESPONSE_RECEIVED。**54 settled / 8 unknown**。这62次包含初轮失败和修复验证，不是62题，也不是全部成功回答。
- 供应商已报告 input **39,983**、output **2,654**、total **42,637 Token**；8次 Rerank 未提供可结算 usage，保守占用 **74,497 Token** 原样 UNKNOWN，不当作免费或实际消耗。总真实用量因此不能声称恰为42,637；它仅是已报告部分。
- `deepseek-before.json` 与最终快照全字段一致（含被旧脚本错记在 DS 的 SF embedding14）；没有移动/改写旧行。新 SF 围栏另外保守携带该14，未释放 DS 占用。DeepSeek 原 blocked_reason 和 UNKNOWN 均保留。
- 凭据仅进程内从既有本地配置取出，经正常配置 API 保存为 `enc:v1:`。3条 SF 模型记录已检查为加密，不输出密钥或写进报告；证据扫描明文 provider key 匹配0。Langfuse 在该端口尚无接线，本轮 **NOT_RUN**，没有新建外发通道。

## 4. 本轮修复的 BLOCKER

1. **approved 只能装配 DeepSeek，SF 被错误归到 DS 池**：`authority.py` 为 SF 接受明确有限执行围栏；loader/构造器/调用校验强制 provider 与 allowed_models、账本供应商一致。DeepSeek 仍严格100万。保留混供应商拒绝反例。
2. **生成摘要进入最终证据；合并正文与单片段引用回读不一致**：只在生产最终证据边界投影。原检索/Rerank/merge 保留，摘要仍可参与初始召回；剔除最终 summary 后按原 topk 取合并组，再加载和验证每个原始 text，使用正常 source 回读的正文和位置；缺失/跨库/跨文档/跨版本失败，不退回旧拼接文本。统一渲染 Context 和 Evidence。
3. **空证据 model fallback 补无依据价格**：通过可信 `WEKNORA_PORT_FALLBACK_STRATEGY=fixed` 选择早已有的固定拒答，不新增策略或改 prompt。默认 model 保持上游兼容；普通问答真实调用与固定无证据拒答分开计数。

非产品故障也保留：Docker 起初未启动；runner 首次配置缺 Origin 被403拒绝（未外发），已补正确 Origin；只读审计脚本首次 `%` 占位符写法错误，以及随后把 missing usage 错当必须存在的断言错误，都保留对应失败日志。没有因此重发模型请求。

## 5. CONTROLLED_DEBT（按本次 Owner 范围记录，不继续修）

- Xing 原四 TLD 问题仍观察到答错/缺引用；默认思考可能耗尽1024输出。当前证明有限两题事实路径可用，**没有证明可靠回答任意问题**；保留原答案供下一阶段质量统计，不重跑寻优。若用于对外可靠回答，需另行处理无引用输出，不能把这次样本 PASS 当通用保障。
- 默认 `fallback_strategy=model` 会添加知识库外信息；本次明确以 fixed 运行。多历史/记忆/图像的逐元素来源、其它未启用输入类型边界不纳入本次纯文本单库证明。
- Rerank 不返回 usage，账本保持 UNKNOWN 有界占用；不得伪造 Token 或准确费用。模型流错误、取消和旧历史兼容未在本轮重新全测。
- 独立 worker、图片/PDF/Office、Langfuse、正常日常启动和容器封装不作为本轮文本 MVP 必要条件。

## 6. OPTIMIZATION（未开展）

召回/Weighted RRF、Rerank TopK、分段参数、Prompt 调优、路由阈值、并发性能、Token 预留精度和大范围重构均未开展。没有把先前审查五项问题自动列为必修任务。

## 7. 实际命令、退出码与证据

Python 前缀 `E:\RAG quention\.venv\Scripts\python.exe -B`，cwd 当前工作树；PYTHONPATH 使用当前树、`E:\WK\deps\reader` 和本树 runtime-deps。所有新产物在 **E:\WK\run\mvp-20261016**，无新增依赖/迁移。未实现的命令不得报告通过。

| 实际命令（接上述 Python 前缀，除另注） | 退出码 / 结果 |
|---|---|
| `scripts/weknora_port_mvp_real.py prepare` / `start` | 0 / 0，启动标准 API；后台仍运行，未采集进程最终退出码 |
| `scripts/weknora_port_mvp_real.py configure` | 首次1：ORIGIN_NOT_ALLOWED；修正请求头后0，无 provider 调用 |
| `scripts/weknora_port_mvp_real.py upload` | 0，真实入库 completed |
| `scripts/weknora_port_mvp_real.py q1` / `q2` / `q3` | 各0是收集器完成，**不代表功能全PASS**；初轮引用/无答案检查FAIL见上 |
| `scripts/weknora_port_mvp_real.py q1 --round final` / `q1 --round verified` | 各1：缺可用引用；失败日志原样保留 |
| `scripts/weknora_port_mvp_real.py q2 --round verified` / `q3 --round verified` / `q4 --round verified` | 各0，分别2引用/固定拒答/1引用；后续内容核对PASS |
| `-m pytest tests/weknora_port/test_authority_approved.py -q -p no:cacheprovider --basetemp E:\WK\run\mvp-20261016\pytest-authority-approved-sf` | 0，26 passed，离线 |
| `-m pytest tests/weknora_port/test_development_entry.py -q -p no:cacheprovider --basetemp E:\WK\run\mvp-20261016\pytest-development-fallback` | 0，15 passed，离线 |
| `-m pytest tests/weknora_port/test_mvp_source_evidence.py tests/weknora_port/test_pipeline.py -q -p no:cacheprovider --basetemp E:\WK\run\mvp-20261016\pytest-source-evidence` | 0，77 passed，离线 |
| `E:\WK\run\mvp-20261016\verify-real.py` | 首两次审计脚本退出1；第三次0，`verify-real-3.log`，不发模型请求 |
| `node frontend/node_modules/vite/bin/vite.js --host 127.0.0.1 --port 56426 --strictPort`（实际argv见进程路径，cwd frontend） | 实际页面成功；后台仍运行，无最终退出码 |
| `git diff --check` | 0 |

仓库内精简凭证：`evidence/MVP_REAL_RAG_20261016/acceptance-summary.json`、`verified-runtime-source-hashes.json`、`credential-scan.json`。E盘保留全部原始 SSE、messages、sources、账本、原始文件回读、API日志及失败日志。Edge截图 `real-two-evidence-citation.jpg`；上传/提问是 HTTP，浏览器仅结果/引用回读，不夸大为本轮完整浏览器流程。

逻辑提交：供应商账户修复 `e1288551c1d139637da7305a50344bfbd8d2dcf1`；已有拒答配置 `6141656c15e2d675289ec6f64b4ed7baaaed2eea`；原文投影 `eabf2d6add84fed40b531a035f6666a4bcaf13da`；真实 runner `2999323ffd8a0a5e136d8c516d39557649ce4890`。被验代码与提交文件字节相同，哈希单列；报告提交随后单独记录。

## 8. 下一步与结束状态

**可以开始六套数据集的接线与少量样本检查；本轮到此停止新增模型请求和开发。** 已有748单元的 Gold/数据检查沿用，未重设计、未正式评测。模型质量失败必须进入下一阶段原始分母；不能只挑本轮成功样本出准确率。DeepSeek恢复前仅可进行不依赖它的接线，三模式真实对照仍阻断；解除原月阻断需独立 Owner 决定，不自动对账或换账户标签。

当前保留本轮标准API **10876**、Vite **3432** 供回读：`http://127.0.0.1:56426/`。本轮 approved 配置于 **2026-10-16 13:38:56（Asia/Shanghai）** 到期；这是隔离临时运行，不是日常服务部署。依赖继续在原 Docker 分组；六个原未跟踪文件和 `.serena/project.yml` 原有改动保留，未 Push/部署/打 Tag。
