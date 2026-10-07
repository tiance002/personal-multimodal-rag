# 本机优化与验证记录

基线工作树：`C:\Users\22088\.codex\worktrees\rag-retrieval-opt-round1\RAG quention`。分支 `codex/local-first-rag-v1-20260930`，HEAD `52d770e8418bb040c372bf8d9014381ed68bc160`；保留此前 10 个 tracked 修改、8 个 untracked 文件。此文档仅记录本机实际执行，不复用云端结果。

## 2026-09-30 配置与离线安全入口

| 轮次 | 输入 / 变更 | 本机实际结果 | API 次数 |
|---|---|---|---:|
| C0 | 原 dirty 工作树可恢复快照 | PASS：358 文件逐项 SHA-256、bundle verify、42 冻结引用哈希；Git status 未变化 | 0 |
| B0 | 既有加固、egress、预算、config 定向测试 | 首轮插件收尾 PermissionError，退出 1；禁用全局插件重跑 PASS，30 passed，退出 0，0.53s | 0 |
| D1 red | 新门禁 / dotenv / adapter 14 个合成测试 | 14 failed，退出 1；缺失模块的预期失败 | 0 |
| D1 green | 原子先占调用额度、无自动重试、后端配置加载、DeepSeek adapter | PASS：14 passed，退出 0，1.55s | 0 |
| D2 red | 合成 QuickChain 本地默认 / private 拒绝 / explicit cloud | FAIL：缺 cloud_answer_gateway 接口；局部编辑第一次因 CRLF 匹配安全停止，未改文件 | 0 |
| D2 green | 独立 cloud gateway，保留现有加固 | PASS：45 passed，退出 0，2.12s | 0 |
| D3 red | usage 为非对象且 choices 为空的异常响应 | FAIL：finally 的 AttributeError 阻断 finish；无真实密钥或响应参与 | 0 |
| D3 green | 异常 usage 归一化，保守结算 | PASS：47 passed，退出 0，2.23s，6 项既有依赖弃用警告 | 0 |
| B1 broad | `pytest backend/tests` | BLOCKED / INCOMPLETE：进入 PostgreSQL 依赖等待/skip，未取得完整汇总；已中止。不是全量 PASS | 0 |

最终定向命令（设置 `PYTHONDONTWRITEBYTECODE=1`、`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`）：

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest backend/tests/test_deepseek_safety.py backend/tests/test_answer_hardening.py backend/tests/test_egress_matrix.py backend/tests/test_quick_chain_budget.py backend/tests/test_config.py backend/tests/test_bootstrap.py -q -p no:cacheprovider --basetemp 'E:\codex_workspace\2026-10-01\task-2\test-tmp-final-offline'
```

所有 DeepSeek 测试仅使用合成凭据、临时独立测试账本和替代 HTTP transport；未消费正式账本。正式唯一账本仍是 `E:\codex_workspace\2026-10-01\task-2\deepseek-call-ledger.json`，consumed=0，reserved=0，limit=50，calls_allowed_in_this_task=false。备份不包含正式账本内容，不得恢复账本以重置额度。

## 配置事实与启动入口

最终 `.env` 位于本工作树根目录，Git 忽略，用户已填写 DeepSeek / Langfuse public+secret key；仅程序检查字段存在性，没有输出、复制、备份密钥。US `LANGFUSE_BASE_URL` 匹配，`RAG_LANGFUSE_CAPTURE_CONTENT=false`。保留用户所有非空值；只补 `DEEPSEEK_MODEL=deepseek-flash`。该 provider 模型来自当前官方 API 文档，不改变 Codex 模型。

`POSTGRES_PASSWORD` 为空，`RAG_DATABASE_URL` 仍为公开模板默认值；独立实例历史 runbook 使用 25438，而模板使用 55432。Docker API/config 和监听状态查询受当前权限阻塞。未改变密码、身份、URL、端口映射或数据库。认证连接未验证，不能宣称完整后端就绪。

新后端启动入口：在本工作树运行 `& 'E:\RAG quention\.venv\Scripts\python.exe' scripts/start_backend.py`。它仅加载该工作树 `.env`，已有进程环境优先，忽略 VITE 字段，不执行配置插值；配置变更需重启该后端。此启动命令本轮 NOT RUN。现有产品 API 仍使用本地回答策略，没有自动改为 DeepSeek 或云端降级。

## 下一次合成真实冒烟条件（本轮 NOT RUN）

计划最多 1 次请求，串行，无重试；固定合成证据例如“测试盒颜色为蓝色”，无私人语料、个人标识、题集或历史答案。输出上限 512 token，超时 <=30s；只记录调用 ID、状态、实际 usage 与延迟，不启动 Langfuse exporter。

父会话须先协调正式账本的单一执行所有者与启用状态；真实 runner 必须使用固定正式账本、显式全局+资料范围外发授权，并满足项目月度费用预占条件。用户密钥只在本机进程中从 `.env` 加载。已发送错误/超时/未知结果永远计数，不退款，重试必须另行预占。无账本、禁用、格式不一致、锁不可用或已达 50 次均在网络调用前失败。

## 外部阻塞

Library 文件 `personal-multimodal-rag-minimal-fixes-20260930.zip` 两个本机目标的当前 helper 均失败：Windows Python 无 `os.setxattr`。未成功物化，未比较或应用云端四份候选补丁，不绕过 helper。云端 apply-check 与历史评测不计为本机 PASS。

原 32 题质量成绩不重算、不改写；历史硬化报告的 19/32 vs 原 20/32 仍属此前结果，本轮没有真实语义质量提升证据。私有资料外发授权、完整数据库闭环、真实 DeepSeek/Langfuse 网络验证、前端/契约修复比对、全量发布均 NOT RUN。

参考：[DeepSeek 当前 Chat Completions 契约](https://api-docs.deepseek.com/api/create-chat-completion/)，[Langfuse US 地区与 BASE_URL](https://langfuse.com/security/data-regions)。


## Local offline stage after 18:33 (2026-09-30)

| Check | Result | Actual DeepSeek calls |
| --- | --- | --- |
| Durable attempt deduplication, process restart, cap <=50, scoped backend regression | PASS: 50 tests, exit 0, 2.47s | 0 |
| Adapter layering repair and metrics regression | PASS: 22 tests, exit 0, 1.96s | 0 |
| New citation stale-response cases, valid RED before source fix | FAIL as expected: 2 cases | 0 |
| Citation stale-response cases after source fix | PASS: 2 tests, exit 0, 5.0s | 0 |
| Citation + existing history/message-scope browser group | FAIL: 3 passed, 6 failed; existing failures need independent diagnosis | 0 |
| TypeScript and Vite production build | PASS: exit 0; existing bundle-size warning | 0 |
| Frozen 32-case score arithmetic and reference hash | PASS: 8 checks; historical quality bands 20/9/3 | 0 |
| Contract after our adapter layering repair | FAIL: 2 pre-existing layering violations and OpenAPI preview/source snapshot drift; SSE sample has no errors | 0 |
| Configured loopback PostgreSQL read-only probe (3s connection timeout) | FAIL: ConnectionTimeout; no authentication conclusion | 0 |
| GET health on localhost:8000 / :18088 | PASS HTTP200 / FAIL URLError; health does not establish DB access | 0 |
| Full backend suite | INCOMPLETE: interrupted database-dependent wait; no full-suite PASS | 0 |
| Real synthetic API smoke | NOT RUN: dollar cap pending, formal ledger remains disabled at 0/50 | 0 |

The App citation change invalidates pending fetches when page, conversation or
message scope changes and accepts only the latest citation selection response.
All browser fixtures are synthetic and mocked. No private corpus or Langfuse raw
content trace was uploaded. No user modifications were committed or replaced.

Docker diagnostic command was `docker ps --format '{{.Names}}\t{{.Ports}}\t{{.Status}}'`.
It exited 1 with config-file Access is denied and docker_engine named-pipe
permission denied. Docker Desktop/backend processes exist; daemon health and
whether the restriction is host-side or execution-environment-side remain unproven.
No permission settings or Docker credentials were read or altered. A user-run
PowerShell `docker ps --format "{{.Names}} {{.Status}}"` is the minimum diagnostic
action; if it succeeds, report that outcome before considering tool access.

ADR-002 requires durable cost reservation before any cloud provider call.
Monthly microunit fields exist but currency conversion is not defined in the
inspected project evidence. A session USD cap must not overwrite monthly limits
or existing spend. Current official numeric prices were not fully verified.
No bounded live runner is enabled and no actual attempt ID or token usage exists.

Remaining independent work: diagnose the existing browser failures against the
saved baseline, repair the two pre-existing layering violations, reconcile the
OpenAPI preview/source snapshot, and audit bounded second-pass retrieval merging.
Frozen arithmetic report: task workspace `offline-frozen-recompute.json`.
Service probe report: task workspace `read-only-service-probe.json`.
Library patch transfer remains failed (Windows helper os.setxattr); no retries,
cloud patch comparison, or patch application were performed.


## Stored evaluation recomputation (zero API calls)
Historical immutable rankings were independently recomputed from ranked IDs and qrels, with duplicate IDs consuming rank slots. Saved per-case scores, macro aggregates, manifest hashes and query-set identities all match. This is not a new retrieval or generation run.
| Locked dataset | Cases | Hybrid doc Recall@5 | Hybrid doc nDCG@5 | Context Recall@5 | Verification |
| --- | ---: | ---: | ---: | ---: | --- |
| longbench-zh | 80 | 0.887500 | 0.790078 | 0.875000 | PASS |
| miracl-zh | 99 | 0.756253 | 0.593845 | 0.756253 | PASS |
| scifact | 300 | 0.671889 | 0.544581 | 0.664389 | PASS |

Saved hybrid failure classifications: {"longbench-zh": {"CONTEXT_SELECTION_LOST_TOP5_HIT": 1, "DOCUMENT_AND_CONTEXT_TOP5_MISS": 9, "FULL_CONTEXT_RECALL": 70}, "miracl-zh": {"DOCUMENT_AND_CONTEXT_TOP5_MISS": 16, "FULL_CONTEXT_RECALL": 66, "PARTIAL_CONTEXT_RECALL": 17}, "scifact": {"CONTEXT_SELECTION_LOST_TOP5_HIT": 2, "DOCUMENT_AND_CONTEXT_TOP5_MISS": 90, "FULL_CONTEXT_RECALL": 192, "PARTIAL_CONTEXT_RECALL": 16}}
All three public runs retain their saved configuration and code SHA 241d2c7; change since these saved runs is zero by identity, not a measured improvement. MIRACL candidate-pool and LongBench paired-source qrels are dataset-specific; no equivalent global-recall claim is made.
Real32 historical baseline versus final human-overlay result: same 32 case IDs and reference hash; fully/mostly correct 20 -> 19 (62.5% -> 59.375%, -3.125 percentage points); incorrect 3 -> 1; partial 9 -> 12. Four cases gain the top quality band, five lose it. Supported citations 24 -> 25. Historical final primary failures: factual error 3, omission 4, retrieval failure 3, citation relevance 3. Changes include historical judging/human overlay; no new model-quality claim.
Detailed aggregate-only evidence and all keyword/vector/hybrid modes: task workspace `stored-evaluation-analysis.json`; no raw private questions, answers or excerpts exported.


## Offline P1 follow-up: UI fixtures, layering, file contract, targeted merge

| Validation | Result | Real calls |
| --- | --- | --- |
| UI safety scenarios after actual interaction alignment | PASS: 9 tests, exit 0, 11.1s | 0 |
| Layering RED | Expected FAIL: 2 failed / 1 passed | 0 |
| Layering/provider/usage/bootstrap GREEN | PASS: 26 tests, exit 0, 2.33s | 0 |
| File-response contract RED | Expected FAIL: 1 failed / 1 passed | 0 |
| File-response contract GREEN | PASS: 9 tests, exit 0, 0.77s | 0 |
| Bounded targeted merge RED | Expected FAIL: 2 cases | 0 |
| Final affected backend regression | PASS: 64 tests, exit 0, 3.53s | 0 |
| scripts.contract_test | PASS: zero layering violations, no snapshot drift, no SSE sample errors | 0 |
| git diff --check | PASS (CRLF warnings only) | 0 |

The six prior UI failures came from obsolete fixture assumptions: auto-opening
history, creating a conversation before first send, the removed native kb-select
control, and a text locator matching both transcript and sidebar title. No test
was deleted and no product behavior was changed to satisfy these fixtures.
All unspecified API routes are mocked. The old-message race now waits for the
old request before switching; draft cases explicitly assert zero history reloads.

Shared capture moved to ports/model_usage.py, with application and adapter
compatibility exports sharing exactly the same context variables. Local Office
preview uses a port wired at bootstrap; the API no longer imports the adapter.
The original local conversion functions, exception identity, cleanup and file
response behavior remain accessible through that adapter.

OpenAPI drift audit found exactly two missing approved paths: document source
and preview. Every shared path and the components were identical. Those two
existing paths now declare binary file responses and 404 / preview 503 statuses,
then only those paths were added to the approved contract. No route was hidden
and no existing operation/component was rewritten.

One targeted retrieval is retained. Its merge now enforces the existing two-pass
2*top_k bound (reference profile: 10 items), removes duplicate IDs from both
passes, preserves stable order and does not mutate inputs. Compound-question
tests retain the targeted evidence at top_k=1. The merge still drops multi-pass
ranking/configuration metadata: this is a separate provenance defect, not claimed
fixed. No rank profile, frozen query, gold or corpus was changed.

Final evidence: task workspace p1-stage-results.json and contract-current-fixed.json.
Commands: node node_modules/@playwright/test/cli.js test tests/stale-citation.spec.ts
tests/history-citation.spec.ts tests/stale-message-scope.spec.ts (task local config);
python -m pytest (11 affected backend test modules, no plugin autoload/cache);
python -m scripts.contract_test --report (task evidence path).
All validations are offline/mock; actual Office conversion, DB and model APIs NOT RUN.
Luna historical report received/read; independent corroboration is the next phase.


## Historical evidence corroboration: trailing citation audit

Read Luna's local report, then independently re-read original real-014/018
result, citation_details and frozen reference. Both current plans have zero
structured fact targets; validator acceptance does not prove semantic support.
For real-018 the AUC/MRR/NDCG@5 statement is followed by a separate [E1][E2]
paragraph. Those literal identifiers appear in E2's quote and not E1's quote.
This corroborates the label/source mismatch signal, not a complete semantic verdict.

Two synthetic finite regressions isolate audit parsing: adjacent trailing labels
must bind to the same preceding statement, and may not jump past a newer paragraph.
RED: 1 failed / 1 passed. First fix: 1 failed / 25 passed, revealing trailing
whitespace in claim text. Final scoped fix: 26 passed, exit 0, 0.50s. The first
label could retain its statement start, while a later adjacent label lost it;
surrounding whitespace also prevented an exact quotation match. Both are fixed.

Actual historical real-018 replay now assigns claim_start=0 to both labels.
Both semantic relevance states remain CITATION_RELEVANCE_UNKNOWN. Synthetic E2
exact quotation yields SOURCE_TEXT_MATCH; that is explicitly textual, not semantic.
No gold or saved score was rewritten, no private content exported, no API called.
Evidence: task historical-citation-corroboration.json and citation-audit-stage-results.json.
real-004/009 refusal causes, real-023 P3 scope and recall-versus-fusion causes remain
unproven. No case-specific keyword rejection rules were added to force scoring.


## Final provenance and offline acceptance

RED: 6 missing-provenance failures. First focused GREEN: 11 passed. Added actual
call linking and empty/config-conflict cases: 13 passed. Complete affected group
then exposed one pre-existing unavailable-as-zero timing defect: 121 passed,
1 failed. Initial snapshot confirms it predates this session. Unexecuted stages
now use None, and empty scope retains measured query timing. Final affected group:
123 passed / exit0 / 4.05s. Metrics aggregation consumer: 7 passed / exit0 / 0.13s.
UI mock group: 9 passed / exit0 / 11.4s. Contract PASS, build PASS, diff-check PASS.

Provenance stores two independent raw rank/config/timing records, canonical config
fingerprints, all duplicate/cap-excluded candidate origins, and stable bounded
merged output positions. No new fusion rank/config is invented. Raw calls retain
actual global call indices; merge records link only matching pass metadata and do
not add retrieval calls or double-count latency. Missing metadata/fingerprints or
links are unavailable, not inferred. Text-free local metadata only; no exporter.
Legacy fields/counters remain compatible. Performance comparisons require
matching config/query identity, complete original rankings and measured timings;
semantic/quality comparisons require separate authorized evidence and evaluation.

Full handoff and original-dirty delta: task offline-handoff.md,
session-change-inventory.json, session-only.patch and final-offline-acceptance.json.
The original 372-file checkpoint and 42 frozen references are retained. Credentials
and the canonical ledger are excluded from recovery archives. Real API0/50 and
budget pending; live DB, complete live flow, actual Office renderer and provider
integration remain NOT RUN/BLOCKED. These tests do not imply a quality increase;
no historical score was rejudged. Stop scope expansion after this offline handoff.


## Independent review P2 follow-up

Confirmed P2-01: Quick previously omitted request_id. RED synthetic repeated run
sent twice, six concurrent executions sent six times, and restart submitted again.
Stable request_identity now hashes canonical [version, run, purpose, attempt].
Quick uses purpose quick.answer / explicit attempt1; no generated UUID is used
for logical identity, and no automatic attempt increment/retry is added. Distinct
legitimate purposes and explicit attempts are separate; repeating any identity
after unknown settlement is denied across gate instances. The cap remains50.

Confirmed P2-02: on one multi-sentence line, the second adjacent marker previously
expanded its claim to the entire line. Adjacent citation groups now reuse the
previous marker's resolved sentence start, including whitespace/newline separation.
This affects audit spans only; answer acceptance is unchanged. Exact quotation
is SOURCE_TEXT_MATCH only, with no lexical-overlap semantic-support rule.

RED: 8 failed / 1 passed, 3.49s. GREEN affected group: 65 passed / exit0 / 4.50s,
6 existing deprecation warnings; includes repeated/concurrent/restarted same run,
same run distinct purposes/attempts, delimiter safety, three citation separators
and a similar-word negative control. Contract PASS; diff-check PASS. UI/build
not rerun in this follow-up because frontend/build inputs did not change; previous
final offline results remain separately recorded. All transports mocked, synthetic
ledgers only; formal ledger remains disabled at0/50, budget pending. No DB/Docker.
Evidence: review-p2.junit.xml, contract-review-p2.json, review-p2-changes.json.


## Pretransport budget settlement P2 follow-up

Confirmed: a duplicate request was rejected by the attempt ledger before transport,
but Quick marked its newly reserved expense unknown. Added the explicit port
exception ProviderRequestNotSent; DeepSeek raises it only for pretransport
authorization/credential/request-limit/model checks and attempt reserve denial.
Quick releases only that invocation's reservation for this typed exception.
Transport/response/settlement uncertainty and legacy untyped errors remain
unknown, including errors containing misleading denial text. No attempt refund,
second attempt, automatic retry, identity change, cap change or budget increase.
BudgetGate/PostgresBudgetGate existing reserved-only release guards are unchanged.

RED: 7 failed / 2 passed, exit1, 1.98s. GREEN: 45 passed, exit0, 5.43s across
pretransport-budget, stable-run, DeepSeek-safety, budget, Quick-budget and two
citation groups. Nine new regressions: sequential/concurrent duplicate budget,
disabled ledger/cap/disabled provider denial, transport timeout/misleading denial
text followed by duplicate, normal settlement, and legacy untyped failure.
Existing distinct purpose/explicit attempt and restart identity tests pass.
Duplicate transport=1 / consumed=1 / used=1; first unknown remains occupied when
only the second rejected reservation is released. All transports are mocks and
ledgers temporary synthetic files. No actual currency conversion is validated.
Contract PASS and diff-check PASS. No schema/frontend inputs changed; UI/build
not rerun, and prior results remain historical. No real API/DB/Docker performed;
formal ledger remains disabled0/50 with USD cap pending. No quality re-scoring.
Evidence: budget-red.junit.xml, budget-green.junit.xml, contract-budget-p2.json,
review-budget-p2-changes.json and budget-p2-only.patch. Stop scope for review.


## Independent review closure and paused handoff

The independent read-only reviewer closed the two original P2 findings (stable
Quick request identity and adjacent citation audit span) and the directly related
pretransport budget settlement P2 within the reviewed scope. Independent affected
regression execution: 45 passed in 5.29s, exit0; JUnit tests45, failures0, errors0,
skipped0. This is the latest 45-test run, not 75 tests summed with the earlier30.
Synthetic reproduction confirmed transport1 / attempt1 / budget-used1 with states
settled + released. Previously sent unknown costs remain occupied; no attempt
refund or additional transport. Five changed-file hashes and the minimal patch
hash matched at review time; the prior helper/citation regression hashes also
matched. This document-only appendix subsequently changes the metrics hash;
review-budget-p2-changes.json and budget-p2-only.patch remain immutable historical
review artifacts. No code or tests changed during closure documentation.

Independent evidence:
E:\codex_workspace\2026-10-01\task-4\independent-rag-review.md
E:\codex_workspace\2026-10-01\task-4\budget-p2-review-targeted.junit.xml

This closes the specified defects in synthetic offline validation, not full live
acceptance. Real DeepSeek/Langfuse, persistent PostgreSQL execution, DB/Docker,
live UI/Office/full E2E and actual-currency settlement remain NOT RUN in this
review. Contract/diff-check were inspected as implementer evidence, not independently
rerun. No quality improvement or historical score reassessment is claimed.

Task PAUSED awaiting the user's total USD cost ceiling and database diagnosis.
Real API0/50, disabled; existing budget constraints remain unchanged. Latest
recoverable checkpoint20260930T204639Z (377 files,42 frozen references) and original
dirty-change inventory are preserved. The checkpoint predates this document-only
appendix; no backup restore, commit, new checkpoint, code change, test execution,
network, API, DB or Docker action is performed for this final documentation step.
No expansion into remaining time; next work requires parent coordination.


## Resumed validation readiness (2026-10-01)

User revised authorization: only50 cumulative requests AND1,000,000 cumulative
input+output tokens stop this batch; USD0.50 cap withdrawn. New explicit USD
pricing audit uses scale1,000,000, exact rational ceiling and conservative peak
rates. Partial earlier USD implementation was retained in a local review ZIP;
its15 RED and14 PASS/1 fixture FAIL are historical, not the latest acceptance.
Current gate atomically reserves count+tokens in the original single ledger,
settles validated usage, retains unknown/truncated bounds, rejects private prompts
and blocks further sends after observed overrun. Product zero monthly policy and
old currency-unknown database costs remain unchanged. See ADR-002's new explicit
synthetic scope. Generic gate cannot bypass token policy; no cross-day reset.

Latest affected offline group:63 PASS / exit0 /9.18s; current validation module24
cases. Overrun RED1 FAIL/1 PASS repaired. Contract PASS, diff-check PASS. Safe
client preflight PASS: deepseek-flash, official endpoint, key existence only,
input bound337 + max output512 =849 tokens, total timeout30s, no retries.
Actual DeepSeek smoke still NOT RUN when this readiness checkpoint is made.

Formally approved docker ps PASS. Labels: DB25438 belongs to selected worktree
Compose; running API18086 belongs to old E:\RAG quention Compose, created before
this round. API18086/UI14186 HTTP200, not proof of current code or DB quality.
Existing authentication reused internally with only in-memory host/port25438;
read-only PostgreSQL probe PASS, migration0013_message_run_link matches local
alembic heads, relevant tables/model_calls columns present, writes0. No port/env/
password edit, rebuild, shutdown or migration. Existing data/profile quality is
not newly re-evaluated. Synthetic model smoke has no database-write dependency.
Evidence: current-service-probe-20261001.json, validation-final-green.junit.xml,
contract-validation-token.json. No frontend/schema changes in this step; prior
UI/build remain historical. Real smoke receipt will be recorded separately.


## First authorized live synthetic smoke (2026-10-01)

PASS: attempt6c6b3834c30448b9a91977d526441730, deepseek-flash; exact synthetic
response check SYNTHETIC_OK passed. Provider usage input22/output6/total28.
Session cumulative1/50 requests and28/1,000,000 input+output tokens. Reservation
337 input+512 output=849 was atomically recorded before transport; validated
usage then settled tokens to28. No retry, private source or raw Langfuse trace.
Runner total timeout30s, official endpoint, no redirect. Entry disabled afterwards.

USD audit: reservation peak upper bound716 microUSD (USD0.000716); observed-usage
peak upper bound14 microUSD (USD0.000014), currencyUSD/scale1,000,000.
Actual debit NOT VERIFIED and is not claimed as14 microUSD. USD0.50 stop ceiling
was withdrawn by the user; only request/token limits stop this batch.

Receipt: synthetic-deepseek-smoke-result.json. This proves one synthetic provider
boundary, not retrieval/answer quality, private-corpus egress permission, Langfuse
integration, current container code, DB ingestion/write acceptance or full E2E.
Existing product cloud/monthly defaults and real data remain untouched. Formal
ledger history MUST NOT be reset or restored from any checkpoint. Old0/50 entries
in this document describe earlier stages only; current tally is1/50 and28 tokens.

Next limited acceptance proposal: use current-worktree code and non-sensitive
generated fixtures in a newly named isolated database rag_acceptance_20261001_task2
on the verified25438 cluster, with separate temporary storage/API port. Before
creating resources, verify test fixture database target and account privileges;
never run migrations/cleanup against the existing rag database. No such database,
process, ingestion or real API follow-up has been created/executed yet. Cleanup
must target only recorded new resource identities, retain reports, and never
restore/reduce the formal global count/token ledger. No production container
rebuild/stop/replacement is part of this proposal; no additional live calls queued.


## Isolated synthetic API/browser acceptance (2026-10-01)

Resource plan saved before creation: test DB rag_acceptance_task2_20261001_021151 on verified
127.0.0.1:25438, API18192, UI14192, task-local storage/logs. Existing identity
created an empty uniquely named DB; target/current_database and empty schema were
double-checked before migrations. Only test DB migrated to0013_message_run_link.
No credential reset/new privilege, container change, product default change or
real database DML/migration. Selected real DB before/after table counts identical:
knowledge_bases1/documents6/document_versions7/rag_runs99/model_calls0. Cardinality
is a limited read-only check, not a whole-dataset hash guarantee. Existing18086/
14186 remained HTTP200; their code is still not identified with current worktree.
Current worktree HEAD52d770e8418bb040c372bf8d9014381ed68bc160, starting dirty
fingerprint 3763c9d53a7667a0808ee781415da36b9422dd294fad71365c8e8f2ddbf77238 from380-file checkpoint020621Z.

API35 assertions PASS /5 mock run IDs; browser11 assertions PASS /3 mock run IDs,
page errors0; DB boundary regression9 PASS/0 skip/exit0/7.50s. Flows cover creating
and selecting synthetic KBs, TXT/Markdown/text-PDF uploads and ingestion, real SQL
retrieval with deterministic mock1024-dimensional hashed embeddings, mock
extractive answers, citation/locator/SSE readback, second-KB and document-scope
isolation, stale-scope rejection, v2 activation with v1 frozen citations intact,
PDF preview, UI upload/document scope, refresh/history/multiturn and KB switching.
Synthetic KB cloud_allowed=true is explicit; global cloud remainsfalse and no
cloud model is wired into these product requests. Graph/Memory were not added.

Initial harness failures are preserved: citation DTO does not contain KB ID, so
version ownership was checked against uploaded API paths; rerunning a retained
fixture used a duplicate unique KB name, fixed with distinct synthetic names.
Neither was a product defect. Initial DB regression7 PASS/1 FAIL exposed an OCR
environment-dependent fixture that assumed OCR_EMPTY when the backend was absent.
Only test_document_version_boundaries.py changed: explicitly exercise OCR_EMPTY
and OCR_UNAVAILABLE, preserve exact error codes and nonactivation assertions.
No production behavior or acceptance criteria were weakened. Actual OCR backend
remains NOT RUN; text-PDF parsing/preview was exercised.

Independent run metrics JSON retains all8 run IDs, selected/retrieved chunk IDs,
configuration fingerprints, actual local/application/HTTP timings and per-run
cache flags. Earlier partial queries warmed Orion cache; the final API first run
must not be advertised as cold. UIBeacon first/second query flags provide actual
cache conditions. Mock model token usage remains unavailable, not fabricated0;
no model-quality or latency-improvement claim is made from passing assertions.

Additional real DeepSeek calls0; cumulative1/50 requests and28/1,000,000 tokens,
formal entry disabled. Fixed-prompt live smoke remains the only real model result.
Real RAG cloud generation, real semantic embedding quality, OCR/Office backend,
Langfuse content traces and private historical quality re-evaluation are NOT RUN.
Existing strict validation gate cannot accept arbitrary RAG prompts; any later
live RAG step requires an explicitly registered synthetic-only scope and the
same global ledger, with at most6 extra calls for this phase, not a new quota.

Resources retained for review: API PID27252, UI PID25112;
http://127.0.0.1:14192 displays only isolated synthetic fixtures with mock models.
Before stopping, verify these PIDs still execute this task's isolated_e2e_api.py
and isolated-vite.config.mjs; stop only matching task processes. DB/storage remain
for readback. Cleanup requires the exact registered test database name and
current_database check; no cleanup is performed now. Never restore or reduce the
canonical count/token ledger. Original372-file checkpoint remains preserved.
Evidence: isolated-e2e/acceptance-summary.json, api-e2e-results.json,
ui-e2e-results.json, run-metrics.json and db-regression.junit.xml. This is isolated
mock-model E2E acceptance, not full live provider/real-corpus release acceptance.


## Frozen synthetic real RAG generation closure (2026-10-01)

Before any answers, six questions/expected facts/version references and exact Core
prompts were frozen: SHA256 94b1e292aadd46f532dc2fc5355f9f42877d1522040c20a0140cb48c2799fcdb. All six registered calls were sent once
through latest shared KnowledgeGateway/Core -> ContextBuilder -> DeepSeek -> Quick
validation/evidence persistence in the isolated database. Five collector receipts
PASS; followup collector FAIL is retained unaltered. Read-only persisted run/message/
evidence recovery confirms followup completed, no error, 08:00-12:00 UTC with E2
maintenance version. Functional checks:6 PASS, including fact, two-document combine,
missing-price refusal, similar KB isolation, active-v2 and frozen-question followup.
Raw response/timing/metrics for followup unavailable; not reconstructed. A synthetic
Windows subprocess roundtrip reproduces default-encoding UTF-8 decode failure
(FAIL_UTF8_DECODE); explicit UTF-8 passes. Workspace collector fixed for future use;
no live retry was performed. Automatic citation semantic relevance remains UNKNOWN;
manual review matches each explicit answer fact to its identified synthetic quote.
This is not a semantic entailment benchmark or private/historical score reassessment.

Phase6 real requests; cumulative7/50 and855/1,000,000 input+output tokens (including
initial28-token smoke), all usage settled, no pending reservation, entry disabled.
USD fees are audited upper bounds, actual debit unverified. No private content,
raw Langfuse traces, product monthly-budget changes, real-index changes or installs.
RAG generation used synthetic hash embeddings with actual PostgreSQL retrieval.
The scoped gate/regressions were70 PASS/9.63s and contract PASS before sending;
new frozen scope admission, six-call phase cap and durable USD reservations are
additional guards under the same global atomic request/token ledger.

Existing local BGE-M3 latest digest 7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab verified from allowed
loopback Ollama model list. Separate1024/cosine profile 4bb91d91-3937-4147-bb56-e9c940d2c110 indexes
only3 current synthetic chunks in isolated DB; mock/other profiles untouched.
Six local retrieval checks PASS scope isolation and expected-version recall@5,
12.11s embedding/index/query total. Tiny corpus2/1 candidates makes recall trivial;
followup maintenance ranked2, not evidence of ranking/quality improvement. BGE
retrieval and real generation were separately validated, their combined live
pipeline NOT RUN. No new cloud calls for local embedding.

Evidence: real-rag-results/acceptance-summary.json, six original case receipts,
followup-persisted-readback.json, local-embedding-readiness.json and
local-bge-retrieval-results.json. Original frozen scope and all42 historical frozen
references preserved. Original372-file checkpoint retained; final checkpoint
recorded separately. No commit/push/merge/tag or production-container replacement.
Full private-corpus quality, OCR/Office, Langfuse and combined BGE/cloud E2E NOT RUN.


## RAG-M1-GATEWAY-01 rev1 attempt1 — mock/offline gateway milestone

This is the actual M1 implementation handoff, distinct from the earlier 45-test
P2 closure. A context drift caused one intervening response to describe that old
closure; it did not revert or redo M1. Requested route: gpt-6.1-sol/medium;
actual serving model/effort UNKNOWN. No subordinate code writer was dispatched.

Baseline: HEAD 52d770e8418bb040c372bf8d9014381ed68bc160, checkpoint20261001T044903Z
(382 files /42 frozen references). Seven source files changed: backend/app/config.py,
bootstrap.py, application/answer_service.py, application/quick_chain.py, api/routes.py,
adapters/postgres/knowledge_repository.py and frontend/src/api/client.ts. One new
test module backend/tests/test_product_gateway.py and only the MessageIn component
in contracts/openapi.json were added/updated. Original user dirty edits retained.

Behavior: default local generation remains; explicit cloud preference and opt-in
single fallback after local provider failure use the existing scope/cloud_allowed,
budget reservation and provider-attempt guards. Stable UUID request identity is
claimed once through existing rag_runs primary-key ON CONFLICT; duplicate/conflicting
payloads are rejected before generation. Legacy local requests remain supported;
configured cloud routes require request_id. The client accepts a reusable caller
request ID; no automatic retry. No schema migration or additional retrieval round.
Route metrics preserve local/cloud path, reason, provider and reservation estimate;
local compute cost is NOT MEASURED, cloud estimate is NOT a verified currency debit.

Checks: original RED15 failures/exit1; initial GREEN30/exit0; original final affected
group82/exit0/9.501s. These runs overlap and are not additive. The final bound-source
run contains22 new product checks within82 total: PASS/exit0/7.885s,
zero failures/errors/skips. Source identity before/after the bound run:
5425e140a487cbf6cde542883fffeb2ef0e4853a20971f9e2dc493381377e682. Deterministic replay of the prior implementation
and refinement into a temporary mirror matches all9 M1 code/test/contract files;
all other382-baseline files unchanged before this documentation appendix.
Contract report PASS, drift/layering/SSE errors0; only optional nullable UUID
MessageIn.request_id changed. TypeScript --noEmit exit0. Original Vite session60474
completion recovered: exit0 /1.34s, existing >500kB bundle warning; no second build.
An earlier workspace build harness failed on Windows E: ESM import; one pathToFileURL
repair produced this success without changing project build configuration.

Coverage: local/off defaults, explicit cloud, private and sensitive requests,
one permitted fallback, exhausted/missing estimate rejection, duplicate/concurrent
requests, unknown-cost retention, stale scope, invalid/missing/conflicting identity,
cloud-disabled factory, fail-closed default gate and mock SQL conflict/recreation.
API tests use fake transport, synthetic credentials, temporary ledgers and an
in-memory budget. SQL persistence/concurrency against actual PostgreSQL is NOT RUN;
the SQL protocol/repository recreation assertion is a mock, not DB acceptance.
Live gateway, combined BGE/cloud, actual costs, quality/latency comparisons, new
UI browser E2E, real-corpus/Office ingestion and semantic citation benchmarks are
NOT RUN. No retrieval/answer quality improvement or historical score change claimed.

No real API calls, DB/service/container operations, credential/.env/config.toml edits,
installs, parser changes or ledger writes in M1. Cumulative7 requests /855 tokens
retained, entry disabled. Monthly budget0 and cloud flags default-off unchanged.
The default product gate rejects the tagged validation ledger: registered authorized
scope/gate injection is still required for any future real validation, alongside
permissions, stable ID, positive justified cost estimate and compatible budget.
Passing mocks does not authorize paid product requests or constitute live readiness.

Recovery: preserve original372-file checkpoint,42 frozen reference hashes and
20261001T044903Z pre-M1 checkpoint. m1-gateway/final-manifest.json and m1-only.patch
compare against that dirty baseline; per-file .before copies preserve all7 sources
and contract. Extract checkpoints into a separate review directory and reconcile
only selected files against current edits; never reset/clean or restore .env/ledger.
Final checkpoint and packet paths are recorded in m1-gateway/Evidence-Packet.json.
Stop scope expansion and return CHECKS_PASSED / NEEDS_OWNER_REVIEW, not ACCEPTED.


## RAG-M2-TABLE-01 rev1 attempt1 — bounded XLSX evidence, offline

Baseline HEAD52d770e8418bb040c372bf8d9014381ed68bc160,
checkpoint20261001T052826Z:383 files/42 frozen references, all matching final M1.
Requested Sol6.1/medium; actual serving identity/effort UNKNOWN. Sole assigned code
writer. M1 seven sources plus test/contract hashes unchanged at final check.

Dependency probe used the actual existing E:\RAG quention\.venv interpreter:
openpyxl/et-xmlfile/defusedxml absent before. After explicit user approval, installed
only openpyxl3.1.5 (MIT), et-xmlfile2.0.0 (MIT), defusedxml0.7.1 (PSF) into this venv.
Official PyPI release metadata confirmed stable/non-yanked and Python3.13 compatible;
wheel license files and SHA256 verified, --no-index --no-deps --require-hashes used.
Only these3 packages added; all pre-existing freeze entries unchanged. pip check
exit1 before and after, identical existing cn-mail-agent/langgraph and
langchain-openai/langchain-core conflicts; no new conflict and no upgrades.
Dependencies/install.log, requirements.lock and verified-artifacts.json are retained
in task2/m2-table/dependencies. Installed wheel SHA256:
openpyxl 5282c12b107bffeef825f4617dc029afaf41d0ea60823bbb665ef3079dc79de2
et-xmlfile 7a91720bc756843502c3b7504c77b8fe44217c85c537d85037f0f536151b2caa
defusedxml a352e7e428770286cc899e2542b6cdaedb2b4953ff269a210103ec58f6198a61
No automatic uninstall. Only these newly added exact distributions are candidates
for a later owner-reviewed uninstall after checking subsequent dependents; never
restore the whole shared environment. No system Python/global variables changed.
Three wheels were already downloaded to E before new D preference; subsequent
pytest temp, build cache/output and process TEMP/TMP use D:/codex_workspace/2026-10-01/rag-m2-table.

Product edits: fixed dependencies in pyproject.toml; additive TableCell,
DocumentTable/DocumentBlock and optional table locator fields in domain/models;
new mature adapters/parsers/xlsx.py; registry imports it instead of legacy ZIP/XML
flattening; new domain/table_evidence.py and table-row chunking; App.tsx displays
sheet/range. No repository/schema/API route changes required: existing chunk
locator JSON, retrieval and citation API preserve the new row+header cell metadata.
PDF/text/Markdown/OCR paths remain unchanged when no tables are present. Mixed
table+other content is explicitly rejected until a future adapter supplies complete
coverage; no silent text/image discard and no claim that mixed-format parsing is done.

Frozen two-sheet fixture hash17f0961b9bbc47e9bd726ebe879f27ab6108b2c0a96c5a316d0891df991c6441.
Same bytes old->new structural comparison: tables0->2, explicit cell records0->36,
named-sheet locators0->2, formula records0->2, missing-cache flags0->1, chunks1->8.
Content136->2423 characters: repeated header/provenance increases context size,
not evidence of lower tokens/cost. Worksheet relationships preserve true names
under workbook/XML reordering. Sparse/blank rows and columns, merge anchors/ranges,
raw negative decimal, number formats, percentages and ISO dates are retained.
Header detection is explicitly inferred (first multi-text row), not guaranteed
semantic header identification for arbitrary complex workbooks. Raw numeric lexical
values supplement openpyxl values via workbook relationships; formula text and
pre-existing cache are distinct. Cache absence is UNKNOWN_CACHE_MISSING, never0;
no formulas/macros/external links are executed and no arbitrary spreadsheet calculation.

RED15 failed/exit1/1.35s. Initial affected GREEN54 passed/exit0/6.02s.
Extended checks17 passed/1 failed: mixed unhandled text silently omitted. One permitted
evidence-driven repair added exact coverage rejection and bounded repeated-header
render. Final affected group110 passed/exit0/11.124s, including19 new
table tests. Counts overlap; M1 prior82 and old P2 45 are not additive M2 output.
Seven frozen cases assert exact row/header/value, sheet/range and original source
substring through NormalizedDocument JSON, row chunks, fake persisted locator JSON,
existing scoped retrieval/ContextBuilder, frozen CitationService and real API route
with FakeStore. Private KB and noncurrent-version chunks remain excluded. No model
generation used. Production PostgreSQL ingestion/persistence is NOT RUN.
Bound tested source snapshot: a34bccee28b59fa1d23127934da65d632acced9171ca76a1291a423cfb83ecef; all8 files identical after checks.

Actual entity/DTD fixture rejected XLSX_UNSAFE_XML; openpyxl DEFUSEDXML true/LXML false.
Inert preflight checks input8MiB, expanded archive64MiB, member8MiB, ratio100,
entry count2000, rows10000/cols200/grid50000 per worksheet, max32 sheets; global
cell grid100000 checked after pair loading. Macro/external workbook entries rejected.
Repeated headers max8 rows/512 chars; XML cell/formula text4096 chars; rendered table
text16Mi chars; oversized complete row rejected rather than cut away headers.
Defusedxml is not complete file/resource safety. Multi-sheet pre-load aggregate
allocation and worst-case peak RAM/time NOT BENCHMARKED; aggregate check currently
occurs after openpyxl loads. Large adversarial workbooks need independent review;
do not advertise unrestricted spreadsheet support or universal safe-file acceptance.

OpenAPI/layering/SSE contract PASS/exit0, no snapshot drift or schema masking.
TypeScript --noEmit exit0; Vite build exit0/1.00s, existing >500kB warning.
git diff --check exit0 (existing CRLF warnings). The optional offline UI formatter
harness FAIL: TypeScript7 lacks legacy compiler API; one extraction repair then hit
CRLF end detection. Stopped without another retry; no formatter assertion PASS,
browser UI/E2E NOT RUN. Static sheet/range display compiled, not browser acceptance.

Semantic QA/retrieval-quality/latency/cost NOT_MEASURED. Other format gaps remain:
HTML/DOCX mature structured parsers, PDF table/layout/scans, legacy DOC conversion,
image semantics/caption. These are later adapters and no Docling/LibreOffice/weights
were installed or run here. No further package install needed for bounded XLSX;
python-docx/BeautifulSoup/lxml remain absent and are not installed speculatively.

No new real API requests; cumulative7/855 retained, entry disabled. No DB/service,
config/.env/credential, model weights, commit/push/deploy, or ledger write. Original
dirty modifications and372-file checkpoint/42 frozen references preserved. Phase
patch/manifest and final recoverable checkpoint recorded in m2-table/Evidence-Packet.json.
Extract backups only separately and reconcile selected files; never reset/clean or
restore credentials/ledger. Status NEEDS_OWNER_REVIEW, not live ACCEPTED. Stop here:
the one product repair and the one UI harness repair have been used.


## RAG-M2-RESOURCE-FIX-01 rev1attempt1 (2026-10-01)

Scope: bounded XLSX preflight and direct regression tests; standalone D-drive
formatter harness. Mature openpyxl 3.1.5 remains the parser. No gateway/API/schema,
App.tsx, dependency, global environment, ledger, service or deployment change.

Trust boundary: all XML is defused and ZIP metadata limits remain in force before
any openpyxl load. Explicit and implicit cell coordinates, actual worksheet count,
merge/hyperlink endpoints and maximum sparse extents are checked without expanding
ranges. Duplicate/overlapping merges and unsupported hyperlink layouts are rejected.
Workbook grid <=100000 slots, each sheet <=50000, rows <=10000, columns <=200.
Merge plus hyperlink expansion work <=100000 per load; the fixed two loads have
<=200000 combined work slots. Range records <=1024 per sheet bound pairwise merge
comparisons. These are conservative admission budgets, not measured peak RAM.
Joined shared/inline rich text <=4096 characters; total serialized text plus shared
reference lengths <=1Mi characters, shared items <=50000, style XML nodes <=10000
per stylesheet, format-code length <=4096. Unused shared strings are checked too.
Shared strings and styles still have ZIP/XML byte bounds; full-tree defused XML
preflight has byte-bounded allocation rather than streaming or measured peak memory.
A non-cooperating file replacement between preflight and load is outside this
path-based adapter contract; callers must supply a stable local file.

New actual offline evidence: RED15 failed/5 passed (includes one bad positive
fixture); initial GREEN38 passed/1 failed (test-budget interference); one evidence
repair isolated test budgets and corrected formatter fallback expectation.
Final39 passed =20 new resource cases +19 existing table cases. Eighteen rejection
cases record loader_calls=0; legal small hyperlink/rich text remains readable.
First pytest collection exited4 on unauthorized parent-directory discovery,
then confcutdir bounded discovery without retrying the rejected target. This was
an environment failure and is retained. Explicit D log-file avoids Windows NUL.
Formatter20 assertions PASS using installed rolldown AST selection/transformation
of the actual App.tsx function for CRLF and LF, including Chinese and empty fields.
TypeScript noEmit, Vite build and offline contract PASS; browser/PG/live API/model
quality/cost and adversarial peak RAM/time NOT_RUN. Build warns on >500kB chunks;
pytest retains6 dependency deprecation warnings.

Historical evidence is carried, not reset: original M282/110 results remain
historical; review7 security probes completed but review pytest ran0 tests and
exit3 due NUL guard. Original UI2 failures remain: first compiler API mismatch
is supported statically with original stderr missing; second LF/CRLF delimiter
failure is confirmed. Neither establishes a product UI bug. No final project or
M2 acceptance is granted. New live API calls0; cumulative7/50 and855/1M unchanged.
Evidence, precise rollback backups, patch, hashes and raw commands/logs:
D:/codex_workspace/2026-10-01/rag-m2-resource-fix/RAG-M2-RESOURCE-FIX-01-rev1attempt1/.


## RAG-M3-NATIVE-TABLE-01 rev1attempt1 (2026-10-01)

Scope: explicit local HTML/DOCX mature-library stdin/neutral-JSON adapter, ordered
mixed text/table blocks and backward-compatible table/cell/citation provenance.
No XLSX adapter/test, App.tsx, config, dependency installation, project LICENSE,
services/DB, credentials, cloud/API, commit/push/deploy or ledger changes.

Native runtime is selected explicitly with RAG_NATIVE_TABLE_PYTHON or constructor
injection; no D path is hardcoded in business defaults. The primary E runtime was
not extended. D's already-installed python-docx1.2.0/BeautifulSoup4.15.0 and
existing defusedxml0.7.1 are reused. Docling aggregation is still BLOCKED and unused.
Details, limits and schema: docs/native-table-adapter.md.

Actual verification: initial targeted pytest exit1,61 passed/3 failed (two test
interval-membership mistakes and Windows LF fixture generation). One source/test
repair corrected those and preserved partial warnings in persisted chunk locators.
First guarded post-repair harness exit3,0 tests: pytest logging's Windows NUL write
was rejected. Explicit D logging avoided that rejected target without relaxing
write guards. Next harness exit1,64 passed/16 failed due Windows audit command-line
representation and _fallback_socketpair misclassification; this run is INVALID as
native/API integration acceptance (generic rejection assertions could be masked).
Only D verification-helper corrections followed; no additional product repair.
Final actual guarded run exit0,80 passed:30 native/safety/schema tests +39 unchanged
XLSX compatibility tests +11 parser/chunk/layering tests;6 dependency warnings.
This new writer-run39 is separate from reviewer history, where7 TestClient cases
were BLOCKED by its internal socketpair guard. That historical status is unchanged.

Final verification disables plugin autoload before collection, restricts writes to
this D task directory and permits only the exact approved local parser command and
stdlib _fallback_socketpair loopback operations. No external network/service/DB
is permitted. Earlier initial pytest omitted plugin disable; project output.json /
archive side-effect attribution has insufficient baseline and stays UNKNOWN.
No existing report/archive was restored, deleted or cleaned. Final before/after
report/archive SHA256 maps match; this proves the final stage only.

Frozen SIMULATED HTML/DOCX fixtures each retain4x3 grids and10 unique cells,
exact original text/blank/zero/-12.5/25%, both merged ranges, source context,
ordered chunks, locator JSON, scoped in-memory retrieval, ContextBuilder and
CitationService roundtrip. HTML header gold matches documented source-policy;
DOCX's original unmarked w:tblHeader semantics remain UNKNOWN, page=null.
Real model answer quality, browser, PostgreSQL/live ingestion, old.doc, native PDF
tables/scans and full visual parsing NOT_RUN/NOT_IMPLEMENTED as applicable.
No mock generation is presented as real QA. RAGpaid7/855 unchanged.

Evidence, raw commands/logs/actual chain JSON, A/B, source manifests, before/after
file hashes, precise original backups and rollback instructions:
D:/codex-rag-tools/native-table-m3-rev1attempt1/.
Requested route NORMALsolmedium; actual model/effort UNKNOWN (no serving metadata).
Status CHECKS_PASSED / NEEDS_OWNER_REVIEW, MANUALLY_SUPERVISED_TRIAL; no acceptance,
milestone approval or publication claimed. Source repair budget used once.


### Review-01 P1 targeted repair (same RAG-M3-NATIVE-TABLE-01 rev1attempt1)

Independent review packet SHA256
fc2e4003452576c29e0830cfc3044f0ad4182fb19ca8e298139339ef41bc1401
confirmed two P1 counterexamples despite the original80 baseline passing.
The owner explicitly authorized this one additional review-driven repair cycle;
the original attempt, first implementation repair and all harness/FAIL history
remain preserved. This cycle has one RED and one GREEN execution, one source
repair, no subsequent retry or extra source fix.

P1-1: native row evidence previously included only origins beginning in that row,
losing Widget on Q2 and omitting rows fully covered by earlier vertical spans.
Native row projection now references each existing origin in every covered row;
raw unique cells are unchanged, source row/cell origin and DOCX continuations are
retained, and locator.cell_range identifies the projected row. Native admission
adds <=1000 rows, <=200 columns, <=50000 projected grid slots before expansion;
rows hold references to original cells rather than fabricated independent cells.
XLSX retains its original row rendering, chunk contract and resource defenses.
This supersedes the earlier docs/native-table-adapter.md origin-row-only note.

P1-2: legacy w:hMerge was ignored by the bounded gridSpan/vMerge adapter and could
be labeled complete. It now fails closed with NATIVE_HMERGE_UNSUPPORTED, preserving
the original file. This repair does not attempt a separate Word merge parser;
existing gridSpan/vMerge support stays available and unmarked headers/page-null
behavior is unchanged.

Actual RED:6 failed/30 deselected,exit1,8.00s. Four HTML/DOCX vertical-span cases
confirmed missing semantic label or missing fully-covered-row chunks; two hMerge
cases (declared/unknown headers) incorrectly did not raise.
Actual GREEN:90 passed,exit0,18.79s,6 dependency warnings: original80 baseline
+4 vertical-context/citation cases +2 exact hMerge rejection cases +3 projection
budgets +1 byte-exact pre-repair XLSX rendering/shared chunk contract. Commands:
E Python -B D:/codex-rag-tools/native-table-m3-rev1attempt1/repair-01/verify-red.py
and verify-green.py. Both disable plugin autoload before collection, constrain all
writes/TEMP to repair-01, allow only the exact configured local worker and stdlib
TestClient internal socketpair, and prohibit external network/services.
All69 old report/archive hashes unchanged in RED/GREEN. No project cleanup.

Repair code scope is exactly table_evidence.py, chunking.py, native_worker.py,
its targeted native test module, and this metrics appendix. No XLSX source/tests,
registry/native host/schema/models/App.tsx/dependency/config/API/DB/service/model,
publication, commit/push/deploy or ledger change. Main E dependencies unchanged.
Browser/PG/live ingestion/real generated-answer quality remain NOT_RUN;
hMerge/old.doc/native PDF table/scans/full visual parsing remain unsupported.
Requested route NORMALsolmedium; actual model/effort UNKNOWN; paid7/855 unchanged.
CHECKS_PASSED / NEEDS_OWNER_REVIEW, MANUALLY_SUPERVISED_TRIAL, not owner acceptance.

Exact current hashes, before backups, repair-only patch, RED/GREEN failure matrix,
raw synthetic context/citation artifacts and rollback:
D:/codex-rag-tools/native-table-m3-rev1attempt1/repair-01/.


## RAG-XLSX-STORAGE-FIX-01 rev1attempt1 (2026-10-01)

CHECKS_PASSED / NEEDS_OWNER_REVIEW, MANUALLY_SUPERVISED_TRIAL. Real ingestion
found that openpyxl rejects extensionless content-addressed filenames even when
the bytes are valid. After the existing complete preflight, each of the two eager
formula/cache loads now receives a separately scoped binary file object. No blob
rename, storage workaround copy, BytesIO duplication, dependency change or guard
relaxation. Handles close on success and second-load failure; books retain their
existing finally-close lifecycle. Registry MIME selection remains unchanged.

RED: 4 failed, 8 passed, exit1 (two actual/synthetic hashpath InvalidFileException
reproductions and two binary-input/lifecycle assertions). GREEN: 65 passed,
6 dependency deprecation warnings, exit0: original39 XLSX +12 new storage cases
+14 shared parser/chunk/citation regressions. Real retained hashpath bytes match
original.xlsx, independent BytesIO formula/cache oracle, normalized tables,
locators, chunks and in-memory citations. Synthetic fixture tests are SIMULATED;
reading the retained blob is actual offline parsing, not ingestion acceptance.
Commands (E Python unchanged, openpyxl3.1.5):
E:/RAG quention/.venv/Scripts/python.exe -B D:/RAG-XLSX-STORAGE-FIX-01-rev1attempt1/verify.py red
E:/RAG quention/.venv/Scripts/python.exe -B D:/RAG-XLSX-STORAGE-FIX-01-rev1attempt1/verify.py green
Full pytest arguments, cwd/rootdir, plugin-autoload disable, D log/basetemp/TEMP,
JUnit results, source hashes, exact before backups, task-only patch and rollback
are retained under D:/RAG-XLSX-STORAGE-FIX-01-rev1attempt1/.

Wrong MIME and corrupt ZIP fail closed; new extensionless merge/text/macro limits
reject before loader (0 calls). Original XML/zip/aggregate/sparse/link guards pass.
TestClient internal Windows socketpair is narrowly allowed. urllib3 import-time
IPv6 capability bind was denied and caught by that library; no external transport.
Old dirty files outside the three task targets and all protected old reports,
archive and integration outputs retain their recorded byte hashes.
No DB/service/model/external calls, live ingestion retry, job resume, ledger change,
commit/push/deploy or old report writes. Local carried usage8 BGE requests/8 strings,
Qwen1 request217in/81out/512reserve; global paid11calls/1536tokens are not reset.
Full suite/live ingestion/answer quality NOT_RUN per scoped authorization.
Requested NORMALsolmedium; actual serving model/effort UNKNOWN. Initial single
source implementation; no evidence-driven source repair used. Owner acceptance
and coordination of a later new integration run remain outside this task.

### RAG-CLOUD12-PAIR-01 (2026-10-01)

Parent review (Tian Ce): both local and DeepSeek answers had all 12/12 key facts correct in this synthetic sample. Q09/Q10 correctly state the requested facts are absent from the authorized context and cite supporting evidence; Q11 gives the E1 date, but fixed context does not test follow-up parsing; Q12 gives E-14 within the West District constraint. Q09-Q12 add no unsupported claims. Q04's extra gold fact about taking over the check was not required by the question and was not present in the cloud answer. Q06's cloud comparison claim had clearer independent citation marking. Parent-attributed manual evaluation only; no evaluator model, general accuracy, refusal, or multi-turn capability claim.

The 12 cloud generations used contexts captured after actual retrieval and then frozen; DeepSeek received those frozen inputs and did not perform retrieval in these calls. The earlier four-case static-gold-context smoke is a separate sample. The current source includes a later follow-up-context reduction, while these paired inputs remain from an older snapshot; this is not a current-source E2E or before/after optimization result.

Usage: local tokenizer 4,615 input / 578 output = 5,193; DeepSeek provider 4,334 input / 406 output = 4,740. The tokenizers differ, so do not infer a compression ratio. Per-model-call wall median: local 0.9908 s, cloud 1.0891 s; nearest-rank P95: local 9.3125 s, cloud 1.3008 s (n=12, rank 12). Local Q01 recorded 8.0799 s Ollama load duration; cold/warm state was not independently controlled. These are model-call walls, not equivalent end-to-end API timings; local API total includes retrieval, while cloud used frozen contexts.

Cloud usage-price estimate: USD 0.00089370 at official DeepSeek Flash off-peak rates (0 cache-hit, 4,334 cache-miss input tokens, 406 output tokens); actual account charge is UNKNOWN. No local energy or amortization data, so no 10% total-cost claim. Cumulative ledger: 23/50 requests and 6,276/1,000,000 tokens; global and batch gates disabled. Pricing source: https://api-docs.deepseek.com/quick_start/pricing/.


## 2026-10-02 bounded-history controlled candidate merge

This entry appends evidence; it does not rewrite earlier scores, 22 follow-up
failures, raw semantic failures, or unknown109 records. Parent-reviewed real
resolver-only cases with frozen prompt SHA256
9c82a77ba55b55d8f44eae23f27e112cf64bedec5c0c446d72cfe140dff3ed5e:
U03 independent new object, U02 explicit clarification, U04 single-object
reference and old S05 explicit clarification had correct raw decisions. U01
still expanded two objects: raw semantic FAIL; its non-verbatim quote was
rejected by source/schema validation and the application clarified safely.
Thus raw decision correctness was 4 of these 5 directed cases; the final path
reached the expected decision in all five with one schema-rejection fallback.
This is a directed sample, not general accuracy or a passed semantic quality
gate. Source: parent task handoff; this writer did not make or repeat those calls.

Merge scope: pronoun boundary, original-source quote contract, frozen Chinese
few-shot prompt, resolver256-output/20-second adapter and service envelope.
Default follow_up_enabled remains false. No diagnostic guard, paid admission
exception, cloud gate, KB authorization or DB schema was merged. No claim of
general reliability, latency/cost compliance, or end-to-end API acceptance.
Old paid source pins are stale after this merge and must be rebound by the
owner; old captures and completed local results remain historical evidence.
This merge task makes zero real model/embedding/API/DB/network/service calls,
and does not read .env, credentials or canonical ledgers.
Offline C-loaded results and the actual route enablement gap are recorded in
D:\RAG-BOUNDED-HISTORY-IMPLEMENT-01\controlled-merge-01\REPORT.md.
