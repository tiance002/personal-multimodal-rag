# Local-first RAG V1 收敛与真实资料验证报告

日期：2026-09-30。范围：成熟检索参考适配、有限检索验证、冻结 V1 路线、真实资料完整流程；不是 V1 发布验收。私人问题、答案全文、源文件、数据库及截图没有进入本报告或 Git。

## 1. 目标与实际改动

| Goal 任务 | 实际交付与状态 |
|---|---|
| Current State | [源码审计与后续差量](rag-current-state.md)，保留0f73521基线；逐项IMPLEMENTED/PARTIAL/NOT_IMPLEMENTED |
| WeKnora/RAGFlow | [版本固定对照](rag-reference-adaptation.md)：WeKnora v0.8.2的加权RRF与RAGFlow v0.27.2的token/cosine融合分开；不混用数字 |
| Reference profile | backend/app/retrieval_profiles/v1_reference.json；每个参数注明参考/项目适配/原有值 |
| Recall/Fusion/Rank/Context | 原有授权范围召回；加权RRF融合；独立可选Ranker/Diversity接口；共享完整Chunk上下文；接口禁止注入越权候选 |
| RetrievalRouter | 确定性Adaptive，语义Vector；精确词/标识符条件Hybrid；数字/缩写需要lookup cue；无路由LLM |
| EvidenceQuality | HIGH/MEDIUM/LOW启发式信号接口；冲突/置信度未知保留不可用，不代表答案正确 |
| ExecutionRouter | LOCAL/CLOUD接口与外发/provider/budget拒绝路径；实际云provider/自动策略NOT_IMPLEMENTED |
| Resource design | [8GB资源报告](../design/local-first-resource-routing.md)，区分实测与估计；num_ctx8192，未加常驻重排器 |
| Bounded validation | 固定24 Development QID，一次48检索调用；历史Old Hybrid复用，不跑网格；[完整脱敏报告](v1-reference-retrieval-sanity.md) |
| Retrieval freeze | [决策](../decisions/v1-retrieval-decision.md)：Vector默认+条件Hybrid；公开参数优化结束 |
| Metrics | Quick/Smart共享本地run metrics，真实usage/阶段延迟/路由/候选/引用/错误/云调用；缺失context_tokens=NOT_AVAILABLE |
| Real materials | 私有32题schema与来源冻结；真实PDF/DOCX/Markdown解析、索引、问答、引用、历史、同范围追问和失败恢复完成 |
| Future profiles | local_first/cloud_heavy设计预留；没有声称未测的质量或成本改善 |

重排/MMR/通用rewrite/cloud fallback默认关闭，缺少adapter时明确拒绝启用。Query expansion保持原有可选q0回退；未新增云端、MCP、沙箱或Skills运行代码。新工作在独立分支和隔离数据库/存储，未覆盖原实验源码版本。

## 2. 检索结论（MEASURED与局限）

24题，每数据集8题。Adaptive的Recall@5分别为SciFact .625、MIRACL 1.000、LongBench .750；MRR/nDCG/Context Recall与Vector一致，没有额外lost-hit QID。静态Reference Hybrid有负迁移，采用条件路由。Adaptive仅1题走Hybrid，不能据此证明所有标识符类别已充分覆盖。Adaptive延迟是已执行arm离线选取，排除router开销，不宣称生产因果优势。

Gate：**PASS，仅针对这次有限检索迁移检查**。源码0b68385f237e9560ef6929297d459973a4def89d。0语料Embedding/DB write/LLM/Locked/云调用；24次query embedding、48次检索。原索引指纹与model digests前后不变，原容器保持停止。无需继续公开benchmark。

## 3. 真实产品验证（MEASURED）

冻结Gold目标集5文档46Chunk；UI失败恢复额外加入第6文档1Chunk，实际计分前语料6文档47Chunk。第6文档不在原Gold manifest，因此不能声称完整实际语料预注册。

| 项目 | 实测 |
|---|---:|
| 固定题数/已尝试 | 32/32 |
| 返回完成答案/被验证器拒绝 | 30/2 UNSUPPORTED_ANSWER |
| 首批生成调用 | 33（31 Quick单调用、1 Smart双调用） |
| 首批query embedding | 32 |
| 首批生成input/output/total tokens | 58,238 / 6,068 / 64,306 |
| Embedding API报告input tokens | 788（单独计量） |
| 首批云调用/自动重试 | 0/0 |
| 首批elapsed | 146.209秒 |
| 请求总延迟p50/p95 | 3,932.634 / 10,056.963 ms（nearest-rank） |
| 引用身份/版本/原文回读 | 46/46一致 |
| 记录的检索/上下文/引用唯一Chunk同库映射 | 39/39，无观察到跨库Chunk |
| 引用在题目Gold目标文档以外 | 9（同库，但不能等同语义支持） |
| 同范围追问 | 2/2记录context used |
| 一秒采样GPU峰值 | 4,816 MiB（主机总量） |
| RAM峰值/最低可用 | 27,032.49 / 5,459.38 MiB（主机总量） |

机器汇总：[real-material-verification-20260930.json](real-material-verification-20260930.json)。原始记录没有直接序列化effective request scope；同库结论来自Chunk-ID映射，不冒充逐请求scope验证。原文回读成功也不等于引用内容支持答案。**正式语义准确率与引用语义支持NOT_EVALUATED，人审NOT_RUN。**

独立UI流程另有2次真实生成（total tokens分别1757、2128），不混入32题汇总。真实浏览器完成上传、失败版本保留、同名新版本恢复、问答、引用抽屉、关闭抽屉后追问及刷新历史；浏览器pageerror=0。脚本前两次UI尝试因定位/抽屉状态停止，使用检查点继续，没有重跑成功回答。

资料预览直接解析7份支持的PDF/DOCX，3份XLSX不支持而跳过；PDF直接文本可读，图像OCR语言资源缺失。不能声称论文图表文字全部抽取。

## 4. 答案质量诊断与窄范围修复

Agent原文核对发现：2条回答可见尾部不完整（各512 output tokens；原run未存finish reason，所以“触发上限”仅为INFERRED）；部分解释/操作问题遗漏或过度拒答；1题Gold存在但Context没有目标细节；1条隐私配置回答错误描述正文采集为必需。以上失败保留，30完成不等于30正确，也没有把词面要点检查当语义分数。

修复源码 **25974ce**：隐私/外发配置问题（包括Smart）直接展示可回读原文，0生成/0扩写，并说明实际运行环境仍需核查；Ollama done_reason=length记录真实usage并拒绝不完整输出，Quick返回原文证据，Smart正常停止，不添加隐含重试或提高生成上限。

单独真实Smart隐私护栏验证：exit0，execution_mode=evidence_only，model_calls=0，input/output/total tokens=0，cloud_called=false，5条引用回读。私有证据hash `5525e66bad0fe477229441962e1dba9c45408b348fdedb6c76f6da32c269b970`。它不是原32题的替换评分。截断护栏为fake-provider定向测试；**修复后真实截断场景NOT RUN**，不能声称32题质量问题全部解决。

## 5. 命令、退出码与证据

所有路径相对本分支工作树，Python使用已有venv。未实现的命令不得报告通过。

| 原样执行命令 | 退出码/输出 | 证据 |
|---|---|---|
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -u -m scripts.validate_reference_retrieval --output var/reports/reference-sanity-20260930` | 0 COMPLETED | var/reports/reference-sanity-20260930 |
| `docker compose -p raglocalfirst0930 -f deploy/compose.yml up -d db`（POSTGRES_PORT=25438） | 0 healthy | 新project、新volume |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -m alembic -c alembic.ini upgrade head`（仅隔离DSN） | 0 | 0013_message_run_link |
| `npm.cmd run build`（frontend） | 0 | TypeScript+Vite构建；既有bundle警告 |
| `node var/local-first/ui_workflow.cjs` | 最终0 | var/local-first/ui-workflow.json、截图 |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -u -m scripts.verify_real_materials --cases var/local-first/real-material-eval.jsonl --materials var/local-first/materials.json --output var/reports/real-materials-20260930` | 0 COMPLETED | 32独立检查点、manifest、results、资源采样 |
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest backend/tests/test_ollama_usage.py backend/tests/test_privacy_configuration_answer.py backend/tests/test_run_metrics.py backend/tests/test_langchain_agent.py backend/tests/test_quick_chain_budget.py backend/tests/test_answer_service.py backend/tests/test_scope.py backend/tests/test_knowledge_scope_boundaries.py backend/tests/test_cancellation_boundaries.py -q --tb=short -p no:cacheprovider` | 0；31 passed、6 warnings | 模拟/内存定向测试，无真实Judge |
| `git diff --check` | 0 | 仅LF/CRLF提示 |
| verify-m0…verify-m4 / verify-release | NOT RUN | 本轮不称里程碑全量或发布门禁通过 |

隐私验证真实请求与断言代码保存在本地运行记录；没有发布请求全文。只在确认active_runs=active_jobs=0后重启自建18088 API，未停止旧Docker、数据库或Ollama。首次材料脚本误把running当结束而退出1，后改为轮询并从receipt继续，没有重复上传/索引。读取两个猜测路径曾失败，未当作存在的文件或通过的命令。未安装缺失psutil，资源采样使用标准库ctypes。

## 6. 身份与复用

首批执行SHA `90af812e0cdb3d3124df9f39e9e78beb42da195f`；tree `6347a283e28f9404035eb6b1bc038df812fa9567`；patch SHA256为空补丁的 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`。

题集hash `d0fc5f80371a0710d1618cf8a660fa8a70fb4fab96fb9b982b9e51a36804719c`；结果hash `1c74ce53b9f1caa94857e38323291fbd903d9448ad65d996d116f4a45926a5df`；完整model digests、材料/Chunk身份留本地manifest。后续安全修复与报告提交不冒充首批执行版本。历史M3/M3.5/Round1全部保留。

## 7. 风险遗留与下一步

| 建议级别 | 已观察状态/措施 |
|---|---|
| P0 | 本轮没有观察到数据损坏、实际云外发或跨库Chunk；这不是所有风险已穷尽的声明 |
| P1 | 错误隐私开关建议用原文路径护栏处理；截断不再直接返回不完整生成。源码/定向测试与独立真实护栏有证据；其余语义支持可靠性仍需真实人工审核，不能给发布验收结论 |
| P2 | 部分概念混淆、缺要点/过度拒答、Context未覆盖Gold、Smart验证拒绝；OCR资源不足；RAM余量在串行时最低约5.3GiB，并发/冷启动/CPU offload未测 |
| P3 | 既有前端bundle大小警告；metrics没有单独Context tokenizer，明确NOT_AVAILABLE |

不自行批准延期。上述质量问题留作负责人真实使用后的优先级决策；不阻挡打开隔离UI查看已有资料与引用。完整启动及用户操作见[运行说明](local-first-v1-runbook.md)。**公开RAG参数优化已结束；本轮不自动新增评测、云调用、重排器或部署，也不打发布Tag。**
