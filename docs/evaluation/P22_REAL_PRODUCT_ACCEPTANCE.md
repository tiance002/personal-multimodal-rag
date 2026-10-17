# P22：最新 Python RAG 最小真实产品验收

日期：2026-10-17。结论：**MVP_READY**。P21 生产源码在本轮四个核心场景中可实际使用，可作为当前可展示 MVP 的冻结候选；不是全面质量认证或 Owner Tag 批准。

执行源码 SHA：`1bd7d993bc56e2b9182b86476e75f3fe30742ae7`。真实 Edge（用户原有配置）、真实 React、普通 FastAPI API、现有 PG/pgvector／Redis 和真实 SiliconFlow；无应用 API 或模型 Mock。没有重新入库或重建索引。

## 实测结果

| 场景 | 结果 | Search→Rerank→Context | 真实模型调用 | 全链路 Token | API 耗时 |
|---|---|---:|---:|---:|---:|
| 知识库打印盒清洁步骤 | PASS | 30→8→8 | 4，含最终 Cheap 1次 | 15,535 | 15.47秒 |
| 随机飞船验证码／无证据 | PASS，固定证据不足 | 6→0→0 | 4，最终回答0次 | 3,358 | 4.55秒 |
| 同会话追问喷嘴金属板擦拭材料 | PASS | 0→0→0，合法 follow_up 使用历史 | 2，含最终 Cheap 1次 | 4,248 | 9.32秒 |
| 刷新后打开已保存会话 | PASS | 不重新检索／生成 | 0 | 0 | 页面恢复观察 |

两次最终回答都由 `XingChenAGI/Xing4.0-29B` 真实返回 `finish_reason=stop`，没有选优或额外重试。正常答案含179个非空 API answer delta，追问26个；SSE 拼接文本、最终数据库答案及刷新前后页面文本逐字相同。

页面没有引用按钮、切块卡片、内部引用标签或永久加载。恢复保留同一 session、message、run 身份；前后账本完全相同，新增模型请求／Token均为0。**未测试进行中的 SSE 断线重连、进程崩溃恢复；不以本次刷新验证替代这些场景。**

## 原文、Scope 与事实核对

- 正常问答使用原有 `P8 longbench-zh` KB，问答请求／会话 document_scope 均为空，实际走向量／混合检索，没有直接加载文档旁路。Rewrite 真实返回 kb_search。
- 8个最终证据片段均通过正常 source API 回读，内容、KB、document、source_version与实际 Context 一致。实际发给 Cheap 的请求包含真实手册原文和“无尘干布”等证据；不是只核对检索命中。
- 手工逐项核对实际原文：关闭扫描仪、取出打印盒、无尘干布／不用纸巾、吸水干布／抹布、F1清洁剂湿布均有来源支持。首答第4步把“打印盒座”写作“打印盒座底部”，名称区分不够精确，操作内容有对应原文；记录为质量问题，未改 Prompt 或模型。
- 追问 Rewrite 真实返回 follow_up；实际 Provider messages 包含上一轮完整问答，无另一无证据会话的随机代号或其他 KB 内容。回答“无尘干布，且不要使用纸巾”与原文及历史相符。
- 无证据样本在原有三页 Omni B KB中检索6候选，真实 Rerank全部排除，最终固定回复。无最终自由生成模型调用。

详细引用原句、真实字符位置和版本见 `E:/WK/run/p22-20261017/manual-fact-check.json`、`source-readbacks.json`。这是本次来源核对，不是新增官方 QA Gold。

## 调用、Token 与权限

真实调用共10次，全部 settled，**23,141 Token（输入21,877／输出1,264）**：

| 用途 | 次数 | Token |
|---|---:|---:|
| Query Rewrite（同一 Cheap） | 3 | 7,923 |
| Embedding BAAI/bge-m3 | 3 | 71 |
| Rerank BAAI/bge-reranker-v2-m3 | 2 | 9,284 |
| 最终 Cheap 回答 | 2 | 5,863 |

所有请求经过原有 SourceFacts、ApprovedTokenAuthority、P1SendGate 和结算。真实 HTTP200、原始 JSON／SSE回执与模型请求体均保留，未保存请求头或密钥。最终 chat 原始回执的 model 字段与请求身份一致；未报告 model 字段的响应保留 NOT_REPORTED。

P22 有限围栏：原关闭上限1,646,586 → 开放1,746,586 → 关闭1,669,727；未使用的76,859 Token撤回，headroom=0。原账户 `siliconflow-owner-main`、2026-10月、carry14和原 approval_source保持不变。705条历史调用、61条历史UNKNOWN和DeepSeek账本未改；新增UNKNOWN=0。没有人民币费用估算。

## 修改与证据

生产 backend/frontend源码均与P21基线一致，**没有产品缺陷修复**。新增两份验收脚本：

- `scripts/weknora_port_p22_observed_api.py`：围绕正常 entrypoint／业务 API 被动记录阶段、实际请求、原始响应、SSE；只允许冻结三条问题、对应KB与合法会话关系，最终回答最多2次，持久化消费意图以防重发。没有提供模拟结果或改写模型请求。
- `scripts/weknora_port_p22_fence.py`：复用已验证围栏实现，仅变更P22身份、范围及当前限额，保留原账本身份、CAS、advisory lock和历史保护。

主要实际命令（Python为 `E:/RAG quention/.venv/Scripts/python.exe -B`；准确 argv／PID见 `processes.json`）：

1. `python -B E:/WK/run/p22-20261017/preflight.py`：退出0，实际核对HEAD、PG／Redis、现有KB索引、模型及账本。
2. `python -B -m scripts.weknora_port_p22_fence open --approval-file E:/WK/run/p22-20261017/owner-approval.txt`：最终退出0，headroom100000。
3. `python -B E:/WK/run/p22-20261017/start.py`：退出0，API与React启动；`/readyz`真实返回 configured／tokens。正常运行模块为 `scripts.weknora_port_p22_observed_api --port 56429 --authority-config ... --evidence-dir ...`，内部使用正式 `entrypoint.create_app`。
4. `npm run dev -- --host 127.0.0.1 --port 56430 --strictPort`：React正常启动；既有 Edge 标签中通过UI提交3次，未通过脚本直接调用问答替代浏览器。
5. `python -B E:/WK/run/p22-20261017/audit_v2.py`：退出0，SSE／页面／持久化／Scope／原文／账本核验。
6. `python -B E:/WK/run/p22-20261017/receipt_summary.py`：退出0，10份真实供应商原始回执可回查。
7. 停止已核验的8个本任务API／前端进程，再 `python -B -m scripts.weknora_port_p22_fence close`：退出0。
8. `python -B E:/WK/run/p22-20261017/final_account.py`：退出0，最终账本与P21生产源码核验。

原始证据根：`E:/WK/run/p22-20261017`。核心文件：`scenario-results.json`、`audit-result.json`、`provider-receipt-summary.json`、`provider-requests/`、`raw/`、`http/`、`captures/`、`stages/`、`rerank/`、`new-ledger.json`、`fence-100000/`及所有截图。失败初稿（围栏approval_source预期错误、审计目录顺序错误、回执工具usage状态假设错误）保留，修正仅限验收工具，未因此新增模型调用。未实现的命令不得报告通过。

## 可控遗留与停止

- 长历史中的长标题使按钮宽度1106px超过248px侧栏，横向溢出，浏览器扩展中心点击首次未激活；使用可见历史项键盘Enter后正常恢复。`history-layout.json`与首次／最终截图均保留。可点击可见部分或用键盘；本轮不扩展UI整理。
- 原UI仍将Markdown强调符作为文本展示，空态文案还提到引用；没有实际引用控件。属于展示技术债。
- P21已有可选 matched_content 契约问题保留，本轮默认关闭引用的正常产品链路和8个原文回读通过。
- 不把两次回答验收外推为全量质量分数；不测试新入库／解析／worker处理，因为本轮禁止重入库与重建索引。

PG：127.0.0.1:56423，weknora_python_port_20261013，system_identifier7696088403504697382；Redis：127.0.0.1:56424 DB15，run_id03f4db094e157950da440540acf3726cef2287f3，P22独立UUID前缀。两库11个既有文档与287条向量等源记录指纹保持一致。

API／React已停止，56429／56430无监听。原先停止的EviRoute PG／Redis已恢复停止，卷保留；未操作其他容器。初始.serena修改及六个未跟踪文件保持原样。完成后停止，不Push、部署、合并或Tag，不进入下一阶段。
