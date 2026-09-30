# Local-first RAG V1 layers

Date: 2026-09-30. Goal: mature-reference adaptation, a frozen retrieval baseline, and the real-material product workflow. Source audit: `docs/reviews/rag-current-state.md` at `0f73521`.

## Shared path

Quick Runnable and Smart knowledge tools continue to use KnowledgeGateway. The shared path is server Scope → deterministic RetrievalRouter → candidate recall → fusion → optional ranking/diversity → ContextBuilder → evidence coverage and quality signals → execution selection → answer validation and citation persistence. Scope, active version and embedding profile filtering remain inside recall queries.

Semantic queries use vector recall. Explicit identifiers, quoted phrases, filenames, snake_case, internal CamelCase, code tokens use the fixed hybrid profile. Acronyms and numbers require an explicit lookup cue before routing to Hybrid. Single title-cased words alone do not classify a semantic question as an identifier query. A vector failure or empty result permits bounded keyword fallback with an explicit reason. Router decisions are deterministic and do not call a model. Explicit static vector/keyword/hybrid modes remain available for troubleshooting.

## Layer ownership

- Recall owns bounded source candidates and their original raw scores/ranks.
- Fusion joins and deduplicates candidates. RRF values are rank votes, not comparable semantic confidence. Candidate rankings survive for metrics.
- Ranking and diversity are optional injected ports. Disabled ports preserve the fused ordering; an adapter may only reorder/filter authorized candidates, never inject a new chunk. No heavy reranker or MMR model is installed for V1.
- Context selection owns the final complete chunks and character budget. It preserves source IDs/order and citation readback. It does not call a model.
- Evidence quality reports retrieval signals and HIGH/MEDIUM/LOW heuristics separately from the existing deterministic target coverage and final answer validation. Unknown conflicts/confidence stay unavailable; these labels do not claim answer correctness.
- ExecutionRouter exposes LOCAL/CLOUD choices. V1 selects local by default; explicit cloud selection must satisfy global and all-KB egress plus provider and budget checks. Automatic evidence-based cloud fallback is reserved for a separately validated policy. A cloud adapter is not currently implemented.

## Optional features and resources

Local query expansion remains opt-in after empty recall, preserving q0 on failure. General rewrite, heavy rerank and MMR are unavailable unless a concrete adapter is added and explicitly enabled; no flag may pretend to make an absent adapter operational. Local answer generation can be disabled for cited deterministic evidence output. Cloud fallback stays disabled without an actual cloud provider.

On the 8 GB GPU, validate serial BGE-M3 and Qwen 4B stages and record cold starts. Do not make a resident reranker a V1 prerequisite. Resource settings and CPU/failure behavior are documented in `local-first-resource-routing.md`.

## Metrics and validation

One local run metrics record per turn includes route/reason, source counts, retrieved/selected IDs, stage and total latency, genuine model usage, provider/model, cloud-called state, citations, errors and retries. Missing provider usage or context tokenization is `NOT_AVAILABLE`; characters are not tokens. Store the record in the existing run event stream, also return it in trace. It contains no prompt/context/answer text or credentials. Smart model calls are recorded individually.

First reuse Round 1 ranks and qrels offline. Report measured ranking/observed historical latency separately from inferred counterfactual latency and missing context metrics. No parameter grid or duplicated corpus index. The retrieval decision closes public parameter tuning; new validation then uses the user's PDF/DOCX materials and project Markdown in an isolated V1 database/storage. Freeze a 30–50 case source-grounded set using the existing required JSONL fields. Check upload through follow-up and citation/session readback; do not claim semantic correctness from string matching alone.

## Future system profiles

`local_first`: local recall/routing/simple processing/local 4B; optional future cloud fallback behind egress and actual budget reservation.

`cloud_heavy`: future explicit cloud query understanding/reasoning/generation baseline. It remains NOT_IMPLEMENTED/NOT_EVALUATED until a real cloud provider and fixed policy are authorized. Compare answer correctness/relevance/faithfulness/citation support, cloud call/tokens/cost, p50/p95 latency and local GPU/RAM. No quality/cost improvement is claimed from retrieval-only results.

## Post-validation answer safety

Privacy/egress configuration questions use the shared deterministic evidence-only path, including Smart requests. It quotes authorized source chunks, marks PRIVACY_CONFIG_EVIDENCE_ONLY, and makes no generation or expansion call. Source documentation is not proof of the current process environment. This narrow protection was added after a real answer incorrectly described optional content capture as mandatory; the original result remains preserved.

Ollama done_reason=length is recorded with genuine usage and rejected as MODEL_OUTPUT_TRUNCATED. Quick falls back to readable original evidence; Smart stops without persisting an incomplete assistant answer. The generation cap remains unchanged, no hidden retry occurs. This handles observed incomplete tails without claiming that all semantic errors are solved.
