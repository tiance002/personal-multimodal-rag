# RAG-M1 — 模型配置、Rerank 决策与动态路由

**状态：M1_COMPLETE。** 2026-10-17。按 Owner 最新补充范围收口：A 正常模型配置、B 冻结 Rerank 评分决策、C Cheap / Expensive / 受控升级。P31/P32 的上传、多模态、浏览器和六套首轮结果继续作为基线。

- 已完成：正常严格解密、4次冻结真实评分、原生过滤离线对照、真实 DYNAMIC Cheap / Expensive / 截断升级、87项定向测试、本地启动与停止验证。
- 正在处理：无。外部阻断：无。
- 已确认质量局限：0434 和 Hotpot 相关证据低分；Omni 精确 OCR 分数略低于阈值；model Fallback 可给出无当前 KB Context 支持的确定性答案。
- 暂缓技术债：SSE 逐 Token 展示、刷新后自动选中会话、更多负例上的 Rerank 配置验证。未启动 M2。

## 版本与修改

起点：`17a0b09772246fa26b5d835d12ad8d63a332c203`。固定上游：WeKnora v0.8.2 / `3e8b0bfc80b845b2d4b2ed683994748741450a97`。

实现与验收工具提交：`2a480a13036b91d426f892b2920438665f8fae94`；装配修复提交：`0b5162f141e00c10bbecc27719954984cc02249a`。真实调用从后者开始；后续只有一次性验收工具和报告变化，生产模块完全一致：

- `backend/weknora_port` Git tree：`e409473a8e0a70172bdb0f3a67aa39d1dab3c0b8`。
- `frontend` Git tree：`1a78c8940054960654f54d236a6306c74db2fe9a`。
- 修改 `scripts/p34_rerank_frozen.py`：同步显式依赖路径到当前进程的 sys.path；增加独立证据输出参数和实际源码 SHA。
- 新增 `scripts/rag_m1_acceptance.py`：复用现有 P32 控制器、正常 API、现有评分器，执行有限路由测试、结果回读和本地启动/停止。
- 隔离 tenant 332 的 Expensive 角色通过正常模型配置 PUT API 登记为已有 DeepSeek `deepseek-flash`，复用 Owner 原有配置凭据。未新增供应商或密钥。

本报告所在的最终文档提交可用 `git log -1 --format=%H -- docs/milestones/RAG_M1_REPORT.md` 精确定位；最终交付消息同时给出该完整 SHA。

## A. 配置阻断根因与修复

P32 在 Python 子进程启动前设置 PYTHONPATH，因此可导入既有 `cryptography==46.0.3`。P34 在解释器启动后仅修改环境变量，没有同步 sys.path。基础虚拟环境中该包缺失，`AESGCM` 导入报 ModuleNotFoundError，被 SecretCipher.decrypt 的统一异常包装成 `STORED_SECRET_DECRYPT_FAILED`。

修复只应用 P32 已声明的依赖路径。随后正常严格解密、模型身份、4份当前 Adapter wire 与冻结 Hash 校验全部通过。未猜测密钥或直接改写加密字段，也不需要 Owner 重配凭据。正常模型配置 API 保存 Expensive 时仍使用现有加密存储流程。

证据说明：首次成功复验使用默认路径，更新了旧 P34 目录的 `preflight.json`；原 P34 报告、results JSON 和 Git 历史仍保留。此后所有 M1 真实响应使用独立 `E:\WK\run\rag-m1-20261017`，不把新分数冒充 P32 的丢失历史响应。

## B. Rerank 真实结果与决策

四份 P32 原始 wire、Query、候选文本及顺序全部冻结，120/120 候选来源文本核对一致。每组恰好1次 SiliconFlow `BAAI/bge-reranker-v2-m3` 请求。完整响应与 index 映射保存在 `rerank/send-manifest.json` 和原始 response body。

| 样本 / 目标 index | 目标分数 | 排名 | Top1分数 | 原生0.3/0.15保留数 / 目标 | 判断 |
|---|---:|---:|---:|---|---|
| 0434 / 14 | 0.000780089 | 6 | 0.002043959 | 0 / 否 | 整池低分；文本含 Kaggle，但可靠 Coding time 章节关联仍缺失。降低全局阈值缺乏依据 |
| Hotpot / 14 | 0.002800834 | 5 | 0.970044911 | 2 / 否 | 地点证据与人物问题的多跳关联评分很低；不是略低于绝对阈值 |
| Omni page007 / 4 | 0.271101624 | 2 | 0.769410014 | 1 / 否 | Summary 高分，包含矩阵和0、8、-9的精确 OCR 被阈值排除 |
| 0959 / 1 | 0.522476017 | 2 | 0.606361985 | 2 / 是 | 正常成功对照，模型仍保留相关无线电波/微波文本 |

使用产品 `retrieval.rerank.rerank` 和生产 default_segmenter 对新响应离线回放：阈值0.25时只有 Omni 从1条变2条并纳入目标 OCR，另外三组保留集合不变。首次 Omni 辅助回放漏传 segmenter，报 TokenizerUnavailable；补上产品原有分词器后重放成功，没有额外云请求。

**决策：保留现有模型及全局0.3/0.15。** 单个 Omni 冻结池支持后续有限的原生阈值配置实验，但没有证明跨语料收益和负例风险，本轮不部署配置调整。0434 优先归为输入关联不足/低相关性，Hotpot 归为多跳相关性局限，不能靠全局降阈值统一修复。没有新增 Rerank 补救规则。

## C. 动态路由真实验收

均走正常 FastAPI、PG/pgvector、Redis、现有 Provider Adapter、持久化与 SSE。Cheap 正常题复用了本轮 Edge 首次0959问答，不再发送一遍。另两题使用独立新会话、正确 KB Scope、document_scope=[]。

| 场景 | 实际路由与回执 | 最终生成Token | 全链Token / 调用 | 延迟与限制 |
|---|---|---:|---|---|
| 正常 Cheap | DYNAMIC score=0.10 → SiliconFlow Xing4.0-29B，finish=stop | 1,170入 + 286出 = 1,456 | 8,371 / 4次 | 最终模型4.651s；浏览器POST总时长未采集。Context含电离层与微波/卫星，答案核心正确 |
| 正常 Expensive | DYNAMIC score=0.38 → DeepSeek deepseek-flash，finish=stop | 917入 + 565出 = 1,482 | 9,405 / 4次 | POST41.923s，最终模型3.965s；Rerank无保留，使用原生model Fallback，不能计为有KB证据支持的问答正确 |
| 受控升级 | DYNAMIC先Cheap；真实length → TRUNCATION → DeepSeek Expensive | Cheap1,181 + Expensive1,094 = 2,275 | 8,838 / 5次 | POST6.272s；两次真实wire均max_tokens=8。升级=true、最终truncated=true，不计作完整答案质量成功 |

受控升级使用**新隔离会话已有 summary_parameters** 的8-token上限，未改生产默认值或伪造 finish_reason。DeepSeek wire 为 `thinking.type=disabled`。两级都受同一测试上限，所以最终仍截断，这是配置约束的实际结果。网络结果未知或 Provider 连接错误不被当成自动升级信号，沿用原有受控合同。

Expensive 的 Fallback 回答对卫星/频率作了明确判断，但没有对应当前 KB Context；虽部分说明资料不足，仍存在一般知识与库内依据区分不充分的质量风险。本轮记录该限制，不将路由成功解释成答案有证据支持，也不扩大修改 Fallback。

## 实际调用与必要验证

核心目标：**17次调用，66,494输入 + 987输出 = 67,481 Token**，含4次冻结 Rerank 和三种路由的正常辅助调用。

补充缩小范围前，旧指令下已启动的11题批次及一次Edge追问已经执行完毕，未撤销或重跑。12个冻结单元（包括上述Cheap）均完成SSE与持久化对齐；额外部分实际用量一并计入。全轮合计 **65次调用，223,605输入 + 3,946输出 = 227,551 Token**；DeepSeek仅2次生成、2,576 Token，未调用VLM或重新OCR。所有65次都有usage，新增UNKNOWN=0；历史UNKNOWN与五张旧预算/调用表保持原状。

额外小样本只在现有子集索引上运行，不是官方全语料成绩。原始答案及评分在 `scored.json`：SciFact/MIRACL无答案Gold；LongBench按任务分别评分；Omni无官方QA Gold且本轮未重解析。按补充范围，本轮不再继续六套测试或为错误答案重试。

| 实际命令 | 退出码 / 结果 |
|---|---|
| `python -B scripts/p34_rerank_frozen.py --output E:/WK/run/rag-m1-20261017/rerank --send` | 0；4真实响应，40,867 Token |
| `python -B scripts/rag_m1_acceptance.py replay-rerank` | 0；四组原生筛选离线回放，0云调用 |
| `python -B scripts/rag_m1_acceptance.py route expensive` / `route upgrade` | 各0；真实角色、usage、SSE和持久化已保存 |
| `python -B -m pytest tests/weknora_port/test_crypto.py tests/weknora_port/test_generation.py -q -p no:cacheprovider --basetemp E:/WK/run/rag-m1-20261017/pytest-tmp` | 0；87 passed |
| 两个M1脚本用Python compile读取源码；`git diff --cached --check` | 各0 |
| `python -B scripts/rag_m1_acceptance.py start` / `stop`，停止后再启动 | 各0；readyz正常，未新增供应商调用 |

上述 `python` 实际为 `E:\RAG quention\.venv\Scripts\python.exe`；pytest使用与服务相同的显式依赖路径。初次来源核验辅助SQL误读不存在的 source_version 列，改用已有 metadata字段后82份文档版本核验通过，未写业务数据。未执行全项目测试。

## 当前架构与数据流

React/TypeScript → FastAPI normal sessions/messages → 服务端tenant/KB/document Scope → Query Understand/Rewrite及Expansion → 独立Embedding + PG/pgvector与关键词检索 → Weighted RRF → 原生Rerank → Parent/Child、邻块Merge与TopK → Context → DYNAMIC(.35)选Cheap或Expensive → 允许条件下最多一次Cheap→Expensive升级 → SSE/Redis流与PG历史；每次真实发送/usage写入 `port_provider_calls`。

文档旁路：原文件/版本 → 现有PDF/Office/MinerU显式解析 → 资产及图片任务Worker → 独立OCR(Tesseract优先)/Caption(已配置DeepSeek视觉接口) → 有来源的Child文本 → Embedding索引 → 上述同一检索主链。图像链路沿用P31基线，本轮未重新入库或启动图片Worker。公开引用保持关闭，内部来源/版本及权限保留。

未恢复 ApprovedTokenAuthority、月度/账户围栏、SourceFacts、Summary强制排除或image_pages路径；没有添加Agent、MCP、沙箱、GraphRAG。

## Windows 本地启动和复现

当前机器已具备项目虚拟环境、既有隔离运行配置、加密密钥文件及锁定依赖目录。应用服务仍为本地进程，Docker只提供现有PG和Redis。

```powershell
Set-Location 'E:\RAG-Worktrees\weknora-python-port-20261013'
docker start eviroute-rag-postgres eviroute-rag-redis
& 'E:\RAG quention\.venv\Scripts\python.exe' -B scripts/rag_m1_acceptance.py start
# Edge 打开 http://127.0.0.1:56426
& 'E:\RAG quention\.venv\Scripts\python.exe' -B scripts/rag_m1_acceptance.py stop
```

主API56425、辅助隔离API56427、React56426；PG56423、Redis56424/DB15。启动器复用已有显式配置，不重新prepare或生成密钥。图片Worker仍用已有 `-B -m backend.weknora_port.worker` 入口及相应tenant/namespace/storage配置，本轮没有把历史队列当作可任意消费的任务。独立安装/打包仍是后续候选方向。

收口时只停止本轮启动的3个应用进程，56425/56426/56427无监听；原PG/Redis继续运行。未Push、部署、合并或打Tag；原有 `.serena/project.yml`、`progress.md` 和历史未跟踪文件未纳入本轮提交。

## 证据与后续候选

机器摘要：`RAG_M1_REPORT.results.json`。真实wire、响应、SSE、模型回执、全部答案、浏览器截图和关闭检查：`E:\WK\run\rag-m1-20261017`。其中 `rerank/send-manifest.json`、`rerank-native-offline.json`、`route-expensive/result.json`、`route-upgrade/result.json`、`snapshot.json` 和 `closeout.json` 为核心复核入口。

后续可由Owner选择：更广负例上的原生Rerank配置/模型对照；原生model Fallback的来源表达；普通用户启动/打包。均未自动实施。本轮按修订后的三个核心目标完成并停止。
