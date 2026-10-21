# S8.6 Adaptive Retrieval V1

Owner scope dated 2026-10-21. Baseline `08ebd90b1c39965d1974de0717c5c6a66f36869a`, containing the S8.5.1 identity Context and ambiguity repairs. Dedicated branch `codex/s8-6-adaptive-retrieval-20261021`; no development from old main.

## Status at the A commit

A is implemented and validated offline. B is designed below and will be committed separately after integration. Complex planning, HyDE, universal semantic coverage, real classification quality and real latency/Token benefit are NOT_IMPLEMENTED / NOT_RUN as applicable. All new actual Provider attempts, Tokens and uncertain sends are zero.

## Configuration and one-call QU

`SessionCreate -> ApplicationRuntime.create_session -> context_config.conversation -> resolve_saved_conversation -> ApplicationRuntime._configuration -> PipelineState` carries `retrieval_strategy_mode=off|query_structure_v1`. Default is off. The public candidate selection must explicitly pair with `source_evidence_mode=bounded_source_v1`; invalid values/pairs fail before lease or model send. The new server-owned state fields and new dataclass options are keyword-only. No schema migration or model binding is added.

Mode off retains its configured prompts, evaluated three-field guard, 150-text/500-image output caps and `thinking=False`. Applicable candidate input uses one existing non-thinking QU call with a dedicated versioned wrapper, the unchanged literal rewrite guard body and the four-field contract `rewrite_query,intent,image_description,query_type`, output cap256. The dedicated candidate wrapper deliberately excludes old/custom three-field prompt text. Current q0/history/language remain delimited runtime input.

The existing rewrite validator runs before the type can drive retrieval. Rewrite fallback invalidates the type to UNKNOWN. A valid old three-field response retains its validated rewrite and uses UNKNOWN; an invalid type alone does not discard a valid rewrite. JSON truncation/invalidity follows the safe original-question path. Type labels never authorize a bypass of existing intent, source-request, ambiguity or pure-format rules.

Images, independent attachments/quoted content, Web and Graph combinations retain the legacy execution path with STRATEGY_NOT_APPLICABLE. They are not silently removed from the question or scope.

## Plans and retrieval units

`strategy.py` defines QueryType, immutable QueryPlan/SubQuery and a closed full-match comparison rule. The admitted CN form is `比较/对比 A 和/与 B 的 X`; the EN form is `Compare A and B in terms of X`. Two distinct object spans and one unchanged shared dimension span produce exactly two queries. Dates/digits, negation, source restrictions, conditions, OR, nested possession and unparsed tails conservatively reject the rule. This is a finite grammar, not a semantic-equivalence prover.

DIRECT/JOINT/UNKNOWN use one effective query. SIMPLE_COMPARE requires both the model suggestion and a valid rule. PARALLEL/MIXED and rejected comparison grammar use ordinary retrieval with explicit complex-planner-disabled / rule-not-applicable reasons. The root q0, validated rewrite, authorized history, targets and language remain unchanged.

`RetrievalPipeline.retrieve_once` contains Search -> Rerank -> optional memory affinity -> Merge -> per-round TopK -> scoped original-source projection. It contains no QU, bridge, Context persistence, answer generation or recursive `run()` call. `RoundResult` preserves each independent state's raw Search/Rerank/source outcomes and empty/error/budget reason. Candidate failures distinguish EMPTY from infrastructure ERROR; scope, permissions, cancellation, deadline, send denials and uncertain sends propagate.

Every child is isolated, closes expansion and has a unique `strategy/sq-N` request prefix. Candidate search serializes KB/model groups, scoped buckets and model-identity reads on the shared PG connection. Existing vector and keyword SQL operations were already serial. Rerank threshold-relaxation calls have distinct candidate operation IDs. Legacy execution retains its prior scheduling and expansion behavior.

At most three retrieval units may start per request. Existing execution reservation/12-call limits remain authoritative; no new allowance is manufactured. LOCAL_EXECUTION_LIMIT_DENIED before send marks the next branch omitted; UNKNOWN, authorization failure or uncertain sent failure stops the unsafe branch. Actual Provider counts may exceed unit counts because of model grouping, batching and auxiliary operations.

## Source aggregation, final budgets and delivery

Each branch applies its own Rerank/threshold before aggregation. No final q0 rerank/threshold runs over the union. Deterministic round-robin selects one admissible source per available branch before filling the existing global TopK. Cross-query scores are not compared. Same immutable Chunk IDs combine coverage labels; conflicting ID/version/body/locator tuples fail closed. Numeric/date differences remain separate facts and no approximate0.85 text dedup is applied across branches.

Coverage distinguishes source_validity, semantic_support and delivery. Rule comparison binding is NOT_CHECKED semantic support; a valid hit is not a supported answer. Actual retrieval and generation Context encoders check the same final evidence budget, conservatively including citation wrappers. Tail pruning records BUDGET_OMITTED; a fully empty result takes SearchNothing instead of ungrounded generation.

The server binds an optional Context observer on GenerationRequest; the actual GenerationCoordinator invokes it after constructing its final PreparedGeneration. It checks current source handle identity and exact encoded original bodies in the current system/user messages, excluding historical dialogue. Only that path records DELIVERED. Previews never invoke the observer. P5 inputs, decisions, final generation prompts, policies and model bindings are unchanged.

## B design: one additional evidence-driven recall

The explicit candidate DEPENDENT path will reuse sentence-id/v2, current-source reads, first-hop extraction validation, bounded identity proof and identity admission. A typed BridgePlan and BridgeAssessment replace boolean/trace-based sufficient-evidence decisions. COMPLETE, RELATED_ONLY, NO_CANDIDATE, UNVERIFIED and CONFLICT remain distinct. Institution address clues are RELATED_ONLY.

Once a reliable first hop exists, reuse the old pool first. COMPLETE stops; RELATED_ONLY/NO_CANDIDATE may use one additional original-scope Hybrid unit with program Q1. UNKNOWN/identity ambiguity/conflict stops. Cheap extraction is at most once and the original-pool BGE is at most once. New recall never reenters QU or the complete Pipeline and never generates an intermediate answer.

Q1 uses the full verified object; explicit event-held wording selects the held relation, while located wording remains located. First-hop employment dates are not transformed into event occurrence dates. The final direct relation validator is deliberately closed and conservative about exact subject, predicate, negation, modal/history, multiple entities and same-window source identity. Complex unknown expressions stay UNVERIFIED. Original first hop/name/normal evidence must survive any accepted supplement and the final Context budget.

## C: future connection only, no runtime implementation

PARALLEL/MIXED or genuinely unsafe-to-rule comparison may later use one separately authorized planner call, at most three DAG nodes, unknown variables for unobserved entities and original-constraint mapping. Plan validation must establish acyclicity, scope and entity/constraint preservation before reusing this executor. Thinking choice belongs to that planner experiment; it does not alter QU or final generation.

HyDE is a separate future experiment for qualified semantic-recall candidates. A bounded hypothetical passage would produce only an embedding vector. Keyword Search and Rerank retain the effective original query; the hypothesis cannot become a Chunk, source or citation. Existing sources remain. No HyDE runtime, enabling flag, model call or placeholder PASS is supplied here.

## References and borrowing boundary

[LlamaIndex SubQuestionQueryEngine](https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/query_engine/sub_question_query_engine.py) is a reference for separating a plan from execution. This implementation only retrieves originals for each branch and performs no per-branch answer generation.

[FlashRAG pipeline](https://github.com/RUC-NLPIR/FlashRAG/blob/main/flashrag/pipeline/pipeline.py) illustrates separating retrieval types from execution paths; no classifier, runtime dependency or research quality result is imported. [Haystack query decomposition](https://haystack.deepset.ai/blog/query-decomposition) motivates preserving the subquery/document pairing; its intermediate-answer process is not used.

## Verification and later real acceptance

Use affected offline contracts and frozen-record replays, preserving all failures. New type accuracy, retrieval benefit, latency and Token effects remain NOT_RUN. Compare legacy, candidate with expansion disabled, fixed-QU/first-hop strategy experiments and formal API acceptance separately; do not attribute simultaneous prompt/recall changes to one factor. Gold belongs to the evaluator only. Fresh real sends require the S8.5.1 formal-profile/citation/budget/second-user preconditions and a new independent Owner grant.
