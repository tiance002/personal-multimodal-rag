# Phase 01 — bounded original source context (experimental)

## Observations and decision

Summary is a generated document-level representation, with a persisted first-text link. Current merge handles text parents but not this relationship. It can preserve a Summary alone even when a short current document has all needed facts. The current LongBench wire has the training count but not the source year; 2005 is only in the question. The source year is two linked predecessor chunks away in a 10,341-character document, so full-document expansion would introduce excessive noise. The old report's model-only attribution is not reused.

Compare only two mechanism families: (1) lower absolute retention thresholds, already shown by saved M3.2 experiments to increase unrelated candidates and still miss very-low-score text; (2) bounded, authenticated source context. Implement (2), leaving threshold0.3/model/MMR unchanged. Historical missing Qwen Rerank scores stay unavailable; synthetic Summary-only companions are explicitly synthetic.

An accepted Summary can resolve to a **complete short document** only if the current source link, version and enabled state verify and all text fits <=2048 Unicode characters / <=6 chunks. A selected text hit may recover up to two reciprocally linked preceding text chunks in the same immutable source, within the same unit bound. This second operation is ordinary bounded source-window context, **not proof of a section relationship or that every sentence is relevant**. In particular it cannot establish MML Coding time association or arbitrary multi-hop facts. Preserve negative/window-noise results; do not add query year/entity classifiers.

Perform expansion after native Merge/Top-K so newly read raw source members do not disappear in overlap scoring. Already selected results cannot be evicted. At most3 expanded document/version groups, <=4096 added raw characters, and <=16000 final rendered/encoded context characters. Character caps are not Token caps; normal send Gate still accounts full actual wire and output upper bound. Never slice a structure to fit. Oversized selected context is explicitly refused; optional expansion exceeding added budget is recorded and skipped. Long documents are never cut into a fake complete short source.

All source members retain independent IDs, source text, version, offsets and locators. A freshly loaded member's score remains0; Summary's score is recorded as selector provenance, not attributed as a new OCR/Rerank score. Generation labels Summary as generated and original text as source. No inference of factual support from doc_id equality.

## Boundaries and rollout

Default `ConversationSettings.source_evidence_mode='off'`; opt-in `'bounded_source_v1'` uses the existing saved/server conversation configuration and standard runtime. No public test API, Schema, role or tenant change. All source reads use the verified repository and original principal scope. Missing/broken version or reciprocal locator fails closed. No cloud sends in development replay.

Rollback: retain `source_evidence_mode='off'`; no data migration or rewritten old chunks. Adoption requires review after negative controls and normal API results. Remaining generation errors are separate from source delivery; no semantic judge or regex answer validator.

## References read 2026-10-18

- LlamaIndex `recursive_retriever.py`, `_retrieve_rec` and `_query_retrieved_nodes`: retrieval representation can resolve to original nodes. Official source SHA256 `bbd8e729e79f4f6d4661170810068f16242d4b51360594db2d46f9576fc20ccc`.
- Haystack `sentence_window_retriever.py`, source identity fields and `merge_documents_text`: bounded source windows, explicit source/position mapping. SHA256 `f87379be332253ffec789d78886d98e6957db031666418852eaeea218387849b`.
- RAGFlow `retrieval_testing.md`: test source completeness and distinguish experiment settings from actual application settings. SHA256 `566c45c26e342f6d1e0d6155481c8f105ab067c6e2d6f1ce911e49b6d17d32bc`.

Official URLs and full files are saved locally under `E:\WK\run\s1-evidence-20261018\references.json`. No framework code/dependencies copied. These references were supplementary research before the corrected package and remain applicable engineering references, not active scope requirements from the withdrawn package.
