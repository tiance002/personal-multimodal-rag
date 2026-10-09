# P4 Rerank 正文回显兼容修复报告

日期：2026-10-09（Asia/Shanghai）。任务/attempt：P4-RERANK-ECHO/r1；单协调者、单写入者，MANUALLY_SUPERVISED_TRIAL；实际服务模型/effort 无控制面证据，UNKNOWN。

## 1. 结果与版本

**正文回显修复：CHECKS_PASSED；两次获准真实调用各 1 次，均成功。完整阶段仍为 P4_BLOCKED / NEEDS_OWNER_REVIEW，没有提交。**

开始及结束 HEAD 均为 `7dcf273e9dab66dc95632253245c2b5a56dfcb0f`，分支 `codex/local-first-rag-v1-20260930`。保留开始时全部 10 个未提交文件及历史证据。本轮修改其中 7 个文件、另修改 3 个已追踪测试，并新增本报告；结束工作树共 14 个 dirty 路径，暂存区为空。

不能标完整 P4_PASS 的具体缺口：现有 `scripts/verify_p4_rerank_once.py:live_container` 固定 `retrieval_mode='keyword'`，PG-only 验证实际为真实 Keyword→RRF→真实 Rerank→合并→Parent→Top-K→Context/Citation。真实 PG/pgvector 的 Keyword、Vector、Hybrid 另有 5 项回归通过，但这些用例的 Embedding 和 Rerank HTTP 明确为 SIMULATED，不能拼接成“同次 Dense＋Keyword／weighted RRF＋真实 Rerank 联合验收”。本轮没有更改该入口的检索模式，没有增加第三次真实请求。原 P4 完整验收条件尚未全部满足，因此不行使条件提交授权。

读取了适用 AGENTS、progress、ADR/当前 P4 报告并沿用交接；原 Temp 任务包文件当前缺失，未臆造其内容。最新 Owner 本轮明确授权为具体执行范围。**未实现的命令不得报告通过。**

## 2. 实际请求合同与代码

只读核对结果：冻结请求与生产 `SiliconFlowRerank.rank` 原先均显式 **return_documents=false**，不是省略。旧请求原始字节保存在 `var/reports/p4-rerank-echo-prep-r1/legacy-request.json`，仍为 227 字节、SHA-256 `5b3c083dc324871e201ff270013015a99a3f08974ad43a68aeba9f0caf8fdbd0`。

相同问题和两段 cost/gardening 合成正文，新请求显式 **return_documents=true**，`top_n=2`，226 字节、SHA-256 `5d8d6e14e7da5719799d993d46e67a2b4c3e9997a0642a002b59fbc606a74378`。新身份 `p4-rerank-echo-r1`；PG-only 独立身份 `p4-rerank-echo-pg-r1`。这不是重放任何旧请求。

依据：[SiliconFlow 官方 Rerank 合同](https://docs.siliconflow.cn/docs/api/rerank-post) 明确 false/default 不含文档文本，true 返回输入文本。本轮实际读到该字段说明和 document.text 响应示例；null 的接受语义不据此推断。实现继续拒绝 null。

| 本轮修改文件 / 符号 | 实际变化 |
| --- | --- |
| backend/app/adapters/models/cloud.py / SiliconFlowRerank.rank.validate | 显式请求 true；每行必须具有对象 document，text 必须为字符串，且与该合法 index 的原候选完全一致。缺失、null、非字符串、串位正文均拒绝；固定诊断枚举，无正文回显到报告。 |
| scripts/verify_p4_rag.py / AuthorizedTransport | 合成允许集增加 return_documents 必须精确为 True；旧 combined 入口仍无条件禁用，PG-only 不隐式发 probe。 |
| scripts/verify_p4_rerank_once.py | 新请求身份/长度/hash/授权 schema；三份旧收据按历史 hash 核验；PG-only 同步 true 合同并绑定本次成功合成收据。 |
| backend/tests/test_p4_rerank_diagnostics.py | 缺失/null/非字符串/串位拒绝；显式 true；含原始空格、换行和中文的精确匹配；乱序结果按 index 保留原 hit。 |
| backend/tests/test_p4_rerank_once.py | 新正文身份、缺失/false 授权零发送；三个历史 UNKNOWN 加新请求仍单次，401/403/超时/结构/结算失败不重发。 |
| backend/tests/test_p4_rerank_integrated.py | HTTP→wrapper→adapter→持久收据；三份历史收据不可改；同一模拟账本连续 probe/PG；持久化错误及首错保留。 |
| backend/tests/test_model_provider_contracts.py | 模拟正常响应提供精确正文，不删除原映射/异常断言。 |
| backend/tests/test_p4_rag_pipeline.py | 模拟正常响应同步 echo；新增缺失/false/数字 1 均零发送的传输门反例。 |
| backend/tests/test_p4_rag_postgres.py | 既有模拟 Rerank 响应逐 index 回显输入，原 Scope/Profile/Parent/Citation 断言保留。 |
| scripts/verify_p4_integrated_offline.py | checks-3 覆盖上述相关路径；三份历史收据保护；保持真实网络、DB、凭据文件、subprocess 护栏。 |
| docs/audits/p4-rerank-echo-compatibility-report.md | 本报告；旧报告不覆盖。 |

保留 index 唯一性/越界/布尔值拒绝、评分有限数值、结果数量、Scope/Citation、正常 ProviderFactory/BudgetUsageGuard/PostgresBudgetGate/AuthorizedTransport、30秒超时、无重试、排他目录及收据 fsync/replace 语义。未修改 P3、模型配置、Profile、Migration、代理、ACL、凭据或旧账本。未读取或输出真实 Key；仅获准真实入口由项目加载器在内存使用已有凭据。

## 3. 测试及真实结果

| 实际轮次 | PASS | FAIL | ERROR | SKIP | 退出码 / 证据 |
| --- | ---: | ---: | ---: | ---: | --- |
| 集成离线 checks-3（SIMULATED HTTP/DB） | 322 | 0 | 0 | 0 | 0；var/reports/p4-rerank-integrated-offline-r1/checks-3/report.json、junit.xml、pytest.log；23.295s |
| 原 P4 关联离线 targeted-echo-final | 403 | 0 | 0 | 0 | 0；var/reports/p4-r1/targeted-echo-final.xml、-command.json、.log |
| 原 P4 真实 PostgreSQL/pgvector；模型 SIMULATED | 5 | 0 | 0 | 0 | 0；var/reports/p4-r1/postgres-echo-final.xml、-command.json、.log |
| 原 P4 广泛离线 broad-echo-final | 1285 | 11 | 20 | 132 | 1；var/reports/p4-r1/broad-echo-final.xml、-command.json、.log |
| 真实合成 Rerank / p4-rerank-echo-r1 | 1 次调用成功 | 0 | 0 | 0 | 0；HTTP 200，453 响应字节，结果 2/2，document object，诊断 complete/NONE |
| 真实 PG-only / p4-rerank-echo-pg-r1 | 1 次调用成功 | 0 | 0 | 0 | 0；HTTP 200，2193 响应字节，结果 5/5，document object，诊断 complete/NONE |

集成 checks-3 实际收集 322，JUnit 322；网络/secret_file/subprocess/database 拦截计数均 0，真实请求 0。源码和三份旧收据测试前后哈希一致；结束再次对齐被测源码。旧 checks-1 的140 PASS/70 setup ERROR、checks-2 的225 PASS及先前127项离线证据均保留，不当作本次源码通过证据。

本轮测试命令原样（相对本项目根目录）：

```text
.venv/Scripts/python.exe -B scripts/verify_p4_integrated_offline.py checks-3                         # exit 0
.venv/Scripts/python.exe -B var/reports/p4-r1/run_checks.py targeted-echo-final                    # exit 0
.venv/Scripts/python.exe -B var/reports/p4-r1/run_checks.py broad-echo-final                       # exit 1
.venv/Scripts/python.exe -B var/reports/p4-r1/run_checks.py postgres-echo-final                    # exit 0
.venv/Scripts/python.exe -B var/reports/p4-rerank-echo-prep-r1/preflight_readonly.py                # exit 0
.venv/Scripts/python.exe -B var/reports/p4-rerank-echo-prep-r1/postflight_readonly.py               # exit 0
.venv/Scripts/python.exe -B var/reports/p4-rerank-echo-prep-r1/pg_preflight_readonly.py             # exit 0
.venv/Scripts/python.exe -B var/reports/p4-rerank-echo-prep-r1/pg_postflight_readonly.py            # exit 0
.venv/Scripts/python.exe -B var/reports/p4-rerank-echo-prep-r1/final_database_readonly.py           # exit 0
```

必要测试和 DB 命令通过正常 require_escalated 审批运行，依据 Owner 已允许提权；没有修改系统 ACL、关闭保护或读取全机进程信息。Python launcher 的既有 location 警告另保留，实际进程/pytest 退出码由命令记录独立确认。各回归展开的完整 pytest argv 与基线范围见对应 -command.json；集成入口 argv 见 report.json。

真实命令及退出码完整记录在 `var/reports/p4-rerank-echo-prep-r1/synthetic-command.json` 与 `pg-command.json`：

```text
.venv/Scripts/python.exe -B scripts/verify_p4_rerank_once.py --execute-owner-authorized-once --authorization-file "C:\Users\22088\.codex\worktrees\rag-retrieval-opt-round1\RAG quention\var\reports\p4-rerank-echo-prep-r1\synthetic-authorization.json"   # exit 0
.venv/Scripts/python.exe -B scripts/verify_p4_rag.py --pg-only --execute-owner-authorized-once --authorization-file "C:\Users\22088\.codex\worktrees\rag-retrieval-opt-round1\RAG quention\var\reports\p4-rerank-echo-prep-r1\pg-authorization.json"   # exit 0
```

合成排序为 probe-0 cost 第一，评分 `[0.9952083230018616, 0.0016504302620887756]`；供应商观测 prompt/completion/total=`50/0/50`。PG-only 评分为 `[0.9855782389640808, 0.9722939729690552, 0.9522215127944946, 0.9510282278060913, 0.9482484459877014]`，供应商观测=`550/0/550`。全行正文精确校验和合法索引映射通过；诊断不保存响应正文或请求头。响应未返回 model 字段，该缺失明确记录，不虚构供应商身份回显。

PG-only 请求实际 1734 字节，SHA-256 `33e7dc203804123fdb117e66faf6575aeb2d50b1f19ebb406f7ccdedd49f4f4d`，5 个 Child 候选；合并后1个上下文组、1个 Parent、5个独立原始证据/E标签，context 2994 字符。源语料 SHA-256 `7e2773faf0ef01fafefb488d37a7cfffce35ca66dbd8aae9b45db348753ba330`，P3 prepare_document 参数不变，全部业务 SQL 回滚，不触碰旧库、不调用最终回答模型。answer correctness 仍 UNKNOWN。

## 4. 基线因果对照与限制

对照真实 `var/reports/p4-r1/baseline.xml`（HEAD `4ffdaeafc4ffbdd6d506d46f17a5f90872e04d6e`，1234 PASS/25 FAIL/20 ERROR/127 SKIP）及此前 failure-baseline-comparison.json。新结果没有新增失败 ID；14个基线失败现通过，不能把执行提权后的变化归功于本轮生产修复。原29项历史失败仍全部失败。30个持续失败/错误的原始 JUnit 详情与基线逐字相同；另1项 OCR 实际原因改变，单列：

- 20 ERROR＋6 FAIL：product_gap_contract 缺 RAG_NATIVE_TEST_PYTHON；setup失败不能宣称数值/引用断言已执行。
- 2 FAIL：legacy_doc 缺 RAG_NATIVE_TEST_PYTHON；1 FAIL：legacy_doc 缺 RAG_NATIVE_TEST_FIXTURES。
- 1 FAIL：既有 layering violation，原始错误详情相同。
- 1 FAIL：multimodal_ingestion 原断言 OCR_EMPTY；基线 PermissionError，当前 OCR_UNAVAILABLE。实际已越过权限失败，仍缺可用OCR环境；未改该测试/生产OCR代码，未把它当成同因已通过。

逐项 ID、原/现错误全文、历史29关联和当前原因在 `failure-baseline-comparison.json`、`failure-baseline-causal-review.json`。广泛回归 **不是全绿**。132 SKIP逐项原因保存在JUnit；其中5个PG用例已另行真实PG执行通过，不能把broad里的SKIP改成PASS。原4项Caption因历史目录缺失仍 **NOT RUN**。**依赖升级影响未独立验证**。本轮没有配置 native runtime、OCR/VLM、下载依赖或扩大修复范围。

## 5. 历史保全、预算与数据库

三次旧失败分别1次/227字节，累计 **3次/681字节**，原 UNKNOWN 收据/hash/账本不变。新两次各1次，累计 Rerank **5次/2641字节**；其中历史3次失败，本轮2次成功。没有重试、旧身份重发、占用归零或退款。

[官方价格页](https://siliconflow.cn/pricing)精确非 Pro 模型标免费，Pro另列收费。直接页刷新两次超时；采用本会话此前成功核对的官方直读证据（2026-10-09T02:30:52.491697Z，仍在入口24小时内），且官方索引页再次确认免费，未伪造新的直读时间。授权文件明确引用 Owner 本轮第5项，免费/Owner/UNKNOWN初态/本次验证许可分别记录。公开免费不等于供应商账单证明。

预算估算：本地每次仍保守占用1 microunit，不声称按token推算实际费用。新两次供应商返回共600 tokens（ACTUAL usage）；实际费用 **UNKNOWN**、settlement **UNKNOWN**，没有0元已结算证据。三次旧失败的usage与费用继续UNKNOWN。

唯一 DB：127.0.0.1:25438 / rag_clean_dev_20261008t072656z_352f705b / OID21278 / system identifier7691227493754040358 / migration0017_embedding_profile_identity；Storage `D:\RAG-CleanSlate\cs0_20261008t072656z_352f705b\storage`。现场身份核对通过，未迁移/清空/重建。业务表前后均0，model_calls 6→7→8，原6行逐列不变。新UNKNOWN行：合成 `fadf0e8c-7b76-41fb-ae0a-752dc027eb57`，PG `4fdd3a9b-69e9-4bf9-811b-39828bd87e30`。预算cap10、used8、remaining2 microunits。最终回归后8行账本和空库状态再次核对一致。

旧业务库本轮未访问，不把未读取写成旧库计数实测。27个已知受保护资产（Migration、P3切块、Clean-slate模型/配置、PDF闭包及生成器）相对既有基线原始SHA全部一致，见 protected-assets.json。gold.json原始18157字节和历史hash保持。三份失败收据最终重新hash一致；冻结请求保持原始字节。旧报告未修改、无文件/数据库删除。

## 6. 证据、回滚与停止

证据目录 `var/reports/p4-rerank-echo-prep-r1/`：开始 Git/源码/收据哈希和原始源码拷贝、repair.diff（仅本轮差异）、tracked-head.diff、full-review-final.diff（包含未追踪P4文件）、最终 manifest/Git检查、离线被测源码核对、两个真实授权/命令/收据引用、预算及DB前后对照、广泛失败因果对照。最终 `git diff --check` 和 `git diff --cached --check` 均0；最终源码哈希见 final-manifest.json。源码在322项集成测试之后未修改。

回滚建议仅供后续审核：先保护已有dirty和本轮证据，再按repair.diff及start-snapshot逐文件审阅逆向恢复本轮10文件修改；不能直接 reset/restore 全工作树。业务数据已经事务回滚，正常账本与真实请求收据必须保留，不退款或删除UNKNOWN。未实际执行任何代码回滚。

剩余最小工作：协调者核对本报告与原始证据，明确真实 Hybrid 联合验收所需的query embedding/受控向量输入及独立新身份/请求授权，再单独完成该缺口；不得把本轮已消费的合成/PG身份复用。真实模型调用本轮限额已用尽，已停止。不自动提交、push、部署、进入P5。没有新commit SHA；当前SHA仍为7dcf273e9dab66dc95632253245c2b5a56dfcb0f。
