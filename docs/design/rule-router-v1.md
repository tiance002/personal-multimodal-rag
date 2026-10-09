# P5 RuleRouter V1 — draft-v0.4

This design implements the Owner's 2026-10-09 specification. It is an initial
rule policy, not calibrated model quality or evidence of actual savings.
Default mode is OFF. The P5 development scope is SIMULATED/offline only.

## Placement and unchanged contracts

The existing fixed Quick Runnable still owns prepare → generate → validate.
P4's Hybrid/weighted RRF, rerank, merge, Parent readback, TopK, Context and Child
Citation pipeline is unchanged. Smart still uses its existing agent. The
router runs only after Scope permission and the existing evidence gate succeed.
Empty/missing/partial evidence terminates without generation. Privacy/config
questions retain the existing exact-source presentation route.

`QuickSettings.router_policy=None` inherits the composition-root policy.
Explicit OFF retains the original Ollama LOCAL and explicit DeepSeek CLOUD
behavior. No message/API payload gets an arbitrary model/mode override.
CHEAP_ONLY/EXPENSIVE_ONLY are limited to injected SIMULATED roles and replay.

## Inspectable scoring

`router_feature_extraction.py` supplies printable task regexes, local anchors,
hashed public anchors, six statuses and integer hundredths for T/C/J/D/L/M.
`rule_router_v1.decide` sums them with Decimal; `score >= .35` selects Expensive.
Highest task category wins; explicit multiple categories remain visible.
Quotation/negated clauses are excluded from task detection.

Conditional frames are limited to explicit 若/如果/条件为/筛选条件/满足以下条件.
Recognized independent predicate clauses are deduplicated by normalized text.
This is not semantic synonym deduplication or a general logical parser.
Unparsed conditions remain UNKNOWN_NOT_INFERRED, not an asserted count of zero.
J uses one highest recognized structure, and exception scope must explicitly
refer to the identified conditions. D counts arithmetic AST dependency depth
or an explicit result-dependent template, not operation count. M recognizes
explicit output counts and a bounded cities × indicators template.

Only q0 or the application's already validated resolution is classified.
Evidence/Context conditions, RRF/relevance scores, parent counts, dates,
versions and domain keywords never increase the score. Hidden conditions and
implicit dependencies remain unresolved product limitations.

L uses an injected estimator over the complete actual generation prompt.
Default estimator is UNKNOWN; tokens are None and L adds zero. No tokenizer
was downloaded and no chat input capacity was invented. SIMULATED capacity
boundaries are test-only declarations. Real input capability needs review.

## Immutable generation and roles

GenerationEnvelope freezes q0, trusted resolution, exact prompt, template
version, Context hash, E labels, Child/version/quote/locator hashes, output cap,
timeout and optional estimated input tokens. Every attempt uses its same
identity. Cheap drafts are never appended to the Expensive prompt.

- Cheap: SiliconFlow `XingChenAGI/Xing4.0-29B`, through normal CloudChat.generate.
  The adapter owns its normal role usage guard; Quick does not reserve again.
- Expensive: DeepSeek `deepseek-flash`, through the existing
  `DeepSeekGateway.answer_with_product_scope`. Its closed gate and durable
  receipt requirement are preserved. One separate monthly role guard wraps
  the call; it releases only typed ProviderRequestNotSent, otherwise keeps UNKNOWN.
  A generic Factory CloudChat for DeepSeek cannot replace this boundary.

GenerationRole checks the selected role and exact provider/model. Real bindings
unconditionally fail before reserve/send in this P5 development implementation:
`P5_BLOCKED_REAL_AUTHORIZATION_AND_CAPACITY`. There is no environment switch to
turn a SIMULATED binding into a live grant. A subsequent reviewed change must
bind exact P5 attempts/prompts/output caps, registered DeepSeek gate and durable
receipt, input capability and current commercial/account/budget contracts.
This is a deliberate incomplete production boundary, not production readiness.

## Controlled strategy B and final commit

`HardeningPolicy.check_candidate` is side-effect-free and reuses the existing
Validator, citation checks, contradiction check and FinalAnswerCommitCheck.
The existing pure marker-spacing normalization is retained. This check runs
before local rejection audit, evidence-only fallback, display or business commit.
Only the final candidate reaches the old finalize path.

At most Cheap once + Expensive once; a directly selected Expensive never upgrades.
QUALITY_ESCALATION requires a completed deterministically rejected candidate.
ABSTENTION_ESCALATION and PROVIDER_FALLBACK are separate, default-off policies.
Whole-answer refusal uses an anchored grammar; quoted or partial refusal is
excluded. Truncation escalation is a separate default-off experiment. There are
no application retries, extra retrieval, query-model calls or final answer judges.

The existing RunEventStore atomic claim/finalize boundary remains authoritative
across process restarts. P5 requires a caller run/request identity. A locked,
in-process run claim also prevents changing role/threshold from replaying a
direct Quick invocation. Attempts use stable request_identity values. Cancellation
is checked before and after each attempt; no second call or successful result
is released after cancellation.

## Metrics and offline replay

RunMetrics.rule_router stores feature/status/decision fields, prompt and Context
hashes, source IDs, parent count, envelope identity and attempt records. No new
raw question/prompt/Context/error response/key/header is logged by the router.
Known supplier-format tokens are separate from billing and labeled SIMULATED
in development. Unknown tokens/costs remain UNKNOWN/None; no billing receipt
means net saving NOT_AVAILABLE. The old integer cost field is compatibility
metadata, not a billing claim. Answer-stage capture includes both attempts.

`replay_p5_router.py` uses 36 synthetic development cases and four cached arms:
Cheap Only / Dynamic / Expensive Only / All Cheap + same B. Cache identities
bind model, template, prompt, Context, generation parameters and actual
postprocessing source hashes. Changes invalidate old replies. The script never
constructs a provider, credential loader or DB. Cache latency/tokens are scenario
data; quality is NOT_REVIEWED. These are not frozen quality/trust Gold or P7 results.

## Configuration and remaining review

`RAG_RULE_ROUTER_ENABLED=false`; quality escalation true only within an enabled
router; abstention/provider fallback false. All are trusted process configuration.
The original egress, DeepSeek ledger, budget and Scope checks remain mandatory.
Turning the router OFF is the immediate application behavior rollback; no DB
migration or reindex is involved.

Before any production enabling: resolve the two existing product-receipt
scope-close regression failures, review the P5 grant/receipt identities and
capacity/price/account contract, authorize bounded real generation separately,
and independently assess semantic quality and refusal. No ≥10% savings claim.


## Owner Review Fix R1 (2026-10-09)

The numeric score contributions and 0.35 threshold are unchanged. Conditional
exception boundaries no longer depend on comma versus semicolon. Recognized
predicates survive unresolved clauses, with an explicit LOWER_BOUND count and
OBSERVED_LOWER_BOUND_UNKNOWN_REMAINDER status. Unscoped exceptions stay unknown.
Dates and tagged numeric identities are masked before arithmetic recognition;
real formulas remain AST dependency observations. 能不能 and bounded explicit
entity/indicator lists are recognized; vague counts, negations and quotes do
not invent output counts. L stays UNKNOWN without a valid full-prompt estimator.

P5.5-A supersedes the earlier process tombstones after real isolated PG/Redis
verification. `rag_run_leases` binds request content, mode, Session and Scope to
an execution owner and expiring lease; `rag_model_attempts` uniquely binds each
role/ordinal and Envelope to the same Run. Redis uses SET NX and exact Run/owner
CAS for LiveRun. It never stores the only duplicate, budget or final-answer fact.
Router generation without an active durable lifecycle fails closed. Default
OFF and the real-role authorization/capacity block remain in force.

Attempt generation_status, send_status, candidate result_validation and
settlement_operation are separate. COMPLETED can have a rejected candidate;
TRUNCATED has length/NOT_RUN, NOT_SENT has no candidate validation, local
validator exceptions are VALIDATION_ERROR rather than provider failure.
Settlements stay UNKNOWN unless explicitly not sent and the local release did
not fail. Settlement failure preserves its first outcome using fixed enums;
known not-sent plus failed release remains UNKNOWN budget occupancy. No raw
exception message or invented ACTUAL cost is recorded.

The R1 report and evidence are under var/reports/p5-owner-review-fix-r1/.
Original 104 plus 47 new P5 cases pass. Associated regression is 382 PASS and
the same two excluded historical receipt FAIL; no real API/DB checks occurred.
Default OFF and BLOCKED_REAL are retained. No P5 acceptance or commit is granted.


## Owner Review R1.1 (2026-10-09)

Slash dates YYYY/MM/DD and annual YYYY/YYYY identities no longer create
arithmetic dependencies. Explicit calculation/formula/parenthesized year-pair
division without an annual label remains arithmetic. The score contributions,
0.35 threshold and L=UNKNOWN contract are unchanged; rules remain bounded
lexical observations with ambiguity, not a semantic calibration claim.

Score capture no longer requires --junitxml. Without XML it uses pytest tmp_path;
with XML it writes alongside the exclusive runner output. Exclusive file creation
refuses to overwrite historical scores. Both XML and non-XML pytest paths passed
under offline guards; no unsafe plain pytest execution is claimed.

A successful provider return followed by an internal candidate-check exception
returns CANDIDATE_VALIDATION_INTERNAL_ERROR, empty answer, and VALIDATION_ERROR
metrics. It terminates before escalation or finalize can rewrite the error or
release a successful evidence fallback. Normal validator/final commit checks
remain unchanged, provider failure stays distinct, and raw exception messages
are never returned or recorded. Costs and budget occupancy remain UNKNOWN.

PRODUCTION_ENABLE_BLOCKED: before formal enabling, trusted durable duplicate
prevention AND a sustainable memory lifecycle must be present and verified.
The earlier 4096/8192 limits and process history sets were replaced in P5.5-A
after the real isolated integration gate. This is local durable anti-duplicate
evidence with SIMULATED model transport, not provider Exactly Once or permission
to enable real generation. Real-model authorization/capacity, production Redis
configuration and migration deployment still need Owner approval.

R1.1 P5 cases: 170 PASS (151 prior +19 new). Associated offline regression:
401 PASS /2 unchanged historical Gate FAIL /0 ERROR /0 SKIP. Both Gate failures
remain separately owned. Default OFF/BLOCKED_REAL, no real API/DB calls, no commit,
publication or deployment. Evidence: var/reports/p5-owner-review-r1-1/.
