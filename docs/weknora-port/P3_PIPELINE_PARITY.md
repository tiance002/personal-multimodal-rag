# P3 query/search/rerank/merge/context implementation evidence

## Scope and result

Python source mechanisms are implemented through `RetrievalPipeline.run`: query understanding → parallel chunk/entity search → rerank → merge → shared TopK filter → `into_chat_message`. The result is immutable `FinalizedContext(user_content, rendered_contexts, result_ids, system_prompt_override, images)` for the later P5 coordinator. P5 model role selection, Cheap retry/upgrade rules, completion, API/SSE, and live RAG acceptance are outside this worker change. There is no custom retrieval router, semantic answer validator, extra expansion LLM, output-tier classifier, or runtime Go dependency.

Source is Tencent/WeKnora fixed at `3e8b0bfc80b845b2d4b2ed683994748741450a97` (MIT, see [upstream license](UPSTREAM_LICENSE.md)). CPPJieba adaptations and resources retain their complete MIT licenses under `tokenizer_assets`. Go Unicode tables retain the complete BSD license there. These are source ports and offline fixtures, not a claim of complete hosted/DB acceptance. 未实现的命令不得报告通过。

Worker paths:

- `backend/weknora_port/retrieval/query.py`, `search.py`, `rerank.py`, `merge.py`, `context.py`, `pipeline.py`.
- Authorized append-only `retrieval/types.py`: source document metadata, image/chunk metadata, merged child IDs, content revision/rewritten status, internal enabled status, matched index content. Existing positional fields and defaults are preserved. `matched_content` is not promoted to source evidence.
- Authorized `retrieval/tokenizer_assets/` dictionary/HMM/user dictionary, full upstream licenses, manifest; `requirements-tokenizer.txt` documents the discarded Python Jieba investigation and has no runtime dependency.
- `tests/weknora_port/test_pipeline.py`, `test_pipeline_tokenizer.py`, this report.

Root-owned storage, fusion, final TopK, conversation, provider, ingestion and oracle files were preserved. No worker commit, push, tag, database migration execution, old service invocation, credentials inspection or actual model request occurred.

## Source/function mapping

| Python function/type | Fixed source mechanism |
|---|---|
| `understand_query`, `parse_understanding` | `query_understand.go` OnEvent/selectModel/buildPrompts/parseStructuredQueryOutputJSON/mergeImageDescAndOCR/applyIntentPromptOverride |
| `expand_queries`, `tokenize`, `simple_tokens` | `query_expansion.go` expandQueries/extractKeywords/extractPhrases/splitByDelimiters/removeQuestionWords/tokenize; `searchutil/textutil.go` TokenizeSimple |
| `SearchTarget`, `SearchParams`, `search` | `search.go` searchByTargets/searchSingleTarget/targetReportsEmbedFailure; `search_parallel.go` independent entity/chunk tasks, partial-error handling and final dedup |
| `rerank`, `clean_passage`, `enriched_passage`, `mmr` | `rerank.go` OnEvent/rerankCandidates/rerankFallbackMinScore/compositeScore/applyMMR/cleanPassageForRerank/getEnrichedPassage |
| `sequential`, `join_content`, `append_overlap` | `merge_overlap.go` classifyMerge/mergeSequentialChunks/appendTrustedContent; `searchutil/chunkmerge.go` JoinChunkContent/ContainsChunkContent/AppendWithOverlap/AppendWithExactOverlap |
| `resolve_parents`, `collect_image_info` | `merge.go` resolveParentChunks/collectScopedTextChildIDs; `searchutil/imageinfo.go` CollectImageInfoByChunkIDsOnly/collectImageInfoByChunkIDs |
| `inject_history`, `populate_faq`, `expand_neighbors` | `merge_history.go` filterHistoryResults; `merge.go` populateFAQAnswers; `merge_expand.go` expandShortContextWithNeighbors/mergeOrderedContent |
| `into_chat_message`, `enriched_chat_passage`, `document_header` | `into_chat_message.go` OnEvent/buildDocumentHeader; `imageinfo.go` EnrichContentWithImageInfoForChat/buildImageInfoMarkdownMetadata |
| `render_placeholders`, `validate_input` | `types/placeholder.go` RenderPromptPlaceholders; `utils/security.go` ValidateInput (input security only, no semantic validator) |
| `RetrievalPipeline`, embedded prompt defaults | `session_knowledge_qa.go` registered stage order; `config/config.yaml` default_rewrite/default_context resolution |

`DEFAULT_REWRITE_SYSTEM`, `DEFAULT_REWRITE_USER`, and `DEFAULT_CONTEXT_TEMPLATE` embed exact default literal blocks from the fixed prompt YAML, including final clipped newline. Session/request settings remain injectable. Generation system/completion prompt remains a P5 responsibility.

## Implemented behavior

Query understanding starts with original q0 and falls through dedicated understanding model → chat model; images first select a usable vision model. Rewrite-disabled/no-image skips the model. Existing history is reused; an injected scoped history loader is used only with positive MaxRounds. Structured output accepts source aliases, wrapped JSON, empty object and image-description/OCR combination. The call uses temperature .3, thinking false, max completion 150 text / 500 image. Provider/model failure retains q0. Intent override preserves source precedence. Caption/rendered-message writes are optional injected sinks with nonfatal errors; this port awaits the supplied sink rather than spawning unmanaged background database work. A production sink may enqueue the source asynchronous write. Actual persistence acceptance belongs to P4/P5 integration.

Expansion is local string processing and pinned segmentation, never another model call. The low-recall gate uses chunk/web recall before entity results: count < max(1, EmbeddingTopK). Expansion preserves target scope, doubles max search/rerank TopK, scales keyword threshold by .8, skips context enrichment and limits active jobs to 16. Length gates use UTF-8 bytes where Go `len(string)` does. Go Unicode15 Han/letter/digit/space/punctuation/symbol tables and rune lowercase semantics avoid Python Unicode/whitespace approximations in local token logic.

Search groups server-resolved physical embedding identity keys and computes the group embedding once when an embedding resolver is supplied. Untagged full KB targets are batched; partial-document and tag targets remain separate. Missing identity/resolver leaves embedding to the repository service as the upstream empty-key fallback does. Embedding failure disables vector search and retains real keyword-index targets; a vector-only failure yields SEARCH_FAILED when no independent search succeeds. Partial failures with usable independent results are retained. Repository exceptions never widen scope. Expansion errors preserve prior recall. Global cancellation/deadlines have distinct failure classes and do not manufacture model-call timeouts.

Rerank skips absent IDs/candidates/passages, treats model lookup as a hard error, and preserves retrieval candidates for API failure. It implements inclusive threshold, source .15 top-1 floor (0 for explicit target override), one threshold retry at max(.3,.7×original) with no configuration mutation, composite .6 model + .3 base + .1 source weight, FAQ boost/metadata, and greedy MMR lambda .7. Markdown cleanup preserves code/math/link text and adds caption/OCR/generated questions, including older generated-question revisions as source does. The final P5 provider algorithm is not implemented here.

Merge retains current edited bodies, uses coordinates only when source invariants hold, verifies exact overlap before slicing, and otherwise performs bounded text matching. It injects relevant history (Jaccard .15, discount .6, max3), expands text/image hits into parent context with matched-child image scope, preserves recognized OCR/caption body, merges enabled image children across two levels by URL, refreshes FAQ current answers, expands short text to at least350 runes or available neighbors and caps at850, then re-merges/deduplicates before shared TopK. The neighbor walker detects a repeated ID to avoid a corrupt cyclic graph hanging the request; valid acyclic source behavior is unchanged.

Final rendering includes escaped document metadata, source FAQ/DOC IDs, first FAQ exact marker when any FAQ is high confidence, original safe q0 on retrieval path, rewritten-query fallback on no-search path, source date/weekday placeholders, image-description fallback, quoted context and injected attachment prompt. Image metadata is spliced against the original Markdown/HTML content so injected metadata is never rescanned. No artificial character/word/output ceiling is introduced.

## Scope boundary and explicit seams

`SearchTarget` is server metadata; `CallContext.scope` is the trusted owner/KB/document boundary. `RetrievalRepository.hybrid_search(targets, params, context, query_embedding=None)`, `get_chunks(ids, context)`, `get_faq_answers(ids, context)` and `get_chunk_children(parent_ids, context)` are scoped interfaces. Calls and returned rows are checked against target KB/document scope. Children must be enabled and related to requested parent IDs; returned child scope is checked again. Parent/neighbor loaders intentionally retain source raw-body semantics; primary search hydration separately controls searchable status. Permission/scope violations are hard failures, never soft fallbacks.

The Go untagged full-KB batching branch does not pass `ScopeTagIDs`. This wrapper keeps a nonempty server scope-tag target separate instead of dropping that restriction. This is an explicit scope boundary protection, not a recall strategy. Full untagged target thresholds use configured global values as source does; per-target/expansion paths honor disable-recall thresholds. Go unordered map/group/URL ordering is not represented as a byte-for-byte serialization promise; equal/unspecified ordering is tested through content/identity sets and final source TopK canonical ordering.

Optional web and graph/entity implementations are injected. Enabling them without the adapter fails `WEB_SEARCH_UNIMPLEMENTED` / `ENTITY_SEARCH_UNIMPLEMENTED`; it is not reported as a PASS. Full web provider/fetch and graph repository algorithms are not implemented by these six files. History, attachments, advisory memory and persistence connect to P4; final completion connects to P5. Live index/DB/model capability and end-to-end upload→answer acceptance remain NOT RUN for this worker.

## Tokenizer dependency/resource evidence

The verified PyPI [jieba0.42.1 release metadata](https://pypi.org/pypi/jieba/0.42.1/json) advertised the source archive SHA256 `055ca12f62674fafed09427f176506079bc135638a14e23e25be909131928db2`. It was installed reversibly into ignored `var/deps/weknora-port/query`, not the shared venv:

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -m pip install --require-hashes --no-deps --target var/deps/weknora-port/query -r backend/weknora_port/retrieval/requirements-tokenizer.txt
```

At execution that requirements file contained the verified package pin/hash. Exit0, installed0.42.1, isolated import0.42.1 PASS. This was an investigation; the current requirements file records the discarded dependency and does not install it. Python's dictionary hash `7197c3211ddd98962b036cdf40324d1ea2bfaa12bd028e68faa70111a88e12a8` (5,071,852bytes) differed from the fixed CPPJieba dictionary. No package-name equivalence claim was made.

The final runtime uses pure Python `CppJiebaSegmenter`, source MP DAG dynamic programming, Mix singleton buffering, HMM Viterbi (B/E/M/S state order, strict predecessor tie choice and E≥S final selection), source ASCII handling, 2/3-gram search expansion, special separators, static user dictionary median weight, and one cached global instance. No Python Jieba or Go runtime is required. No reference-directory path is accessed at runtime.

Fixed go.mod version is `github.com/yanyiwu/gojieba v1.4.7`; fixed go.sum is `h1:2YkXELcYLTE0SJetq6xv4MjpEikWga6VpFn4jIFFQ/k=` (go.mod `h1:JUq4DddFVGdHXJHxxepxRmhrKlDpaBxR8O28v6fKYLY=`). The manifest records exact SHA256/size:

- `jieba.dict.utf8`: `3043b77068e09c9904f27cad82f12b6ebe9dbdb5aeff3b25e45ab7f9c1122b55`, 5,071,204bytes.
- `hmm_model.utf8`: `f17790586ac86dd048c8adffed052c4bd2b28ed0682972c1275e59040c0589a7`, 519,739bytes.
- `user.dict.utf8`: `495bbf49270408a1234690e1e6a97328f30a482a7a72aa769e8a12e8714b0c62`, 49bytes.

The long Chinese search fixture is copied from the pinned `jieba_test.go` literal expected output; it passes the Python port. Other fixtures exercise MP/search subwords, unknown HMM, ASCII/numeric rules, source user dictionary/resource integrity, and cached construction. These are **source-derived fixtures PASS**, not direct GoJieba execution. Direct Go/CPPJieba runtime comparison: **NOT RUN**. `CppJiebaSegmenter.parity` remains `SOURCE_PORT_ORACLE_UNVERIFIED`; source implementation and dictionary identity do not prove all possible segmentation outputs. Direct reference compilation/comparison can be added without changing the Python runtime.

## Commands and results

All pytest commands were run from `C:\Users\22088\.codex\worktrees\weknora-python-port\RAG quention` with the existing interpreter and `--noconftest`.

Initial focused command:

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest -q tests/weknora_port/test_pipeline.py --tb=short -p no:cacheprovider
```

Exit1: 4FAIL/50PASS (fixture helper duplicated enable_rewrite keyword; expansion fixture assumed multiword delimiter segments whereas source splits whitespace; a gate fixture produced no distinct variants). After correcting fixture assumptions: exit1,1FAIL/53PASS (same whitespace-split fixture at max-five boundary). Corrected to distinct sufficiently long delimiter words. These failures were recorded, not hidden as passes. First source-mechanism suite then passed60 tests; later contracts grew to69 pipeline/tokenizer checks in the final associated suite.

Final associated suite:

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest -q tests/weknora_port/test_pipeline.py tests/weknora_port/test_pipeline_tokenizer.py tests/weknora_port/test_fusion.py tests/weknora_port/test_filter_top_k.py tests/weknora_port/test_storage.py tests/weknora_port/test_knowledge_storage.py tests/weknora_port/test_conversation.py --tb=short -p no:cacheprovider
```

Exit0: **128PASS in1.10s**. All synthetic mocks/source fixtures; no model network or real database. Root-owned tests are included only as associated compatibility checks. No Go oracle test is in this command.

```powershell
git diff --check -- backend/weknora_port/retrieval/types.py
```

Exit0. AST parsed all7 edited retrieval Python files successfully. Initial compileall exit1 exposed Windows default file encoding for Chinese source text; rewritten explicitly as UTF-8 and compileall exit0. No artifact was called accepted based on compilation alone.

## Version and remaining acceptance

No worker commit; parent reviews/commits the shared phase. Main P3 mechanisms and offline scope/error/render fixtures are ready for integration. Formal P5 orchestration and live provider/index/end-to-end acceptance are not approved or completed by this report. Optional adapter omissions and direct-tokenizer oracle limits are stated above; no postponement or milestone acceptance is self-approved.

## Fixed source blob identities

- `internal/application/service/chat_pipeline/query_understand.go`: `c8c29777229f2d4af90b89b136d628db4b2e894a`.
- `internal/application/service/chat_pipeline/query_expansion.go`: `3324a9853e11a94c393ac4d682380c1c8aa02782`.
- `internal/application/service/chat_pipeline/search.go`: `a3b2423cea4f47306877bca2280ed8111b82d0a5`.
- `internal/application/service/chat_pipeline/search_parallel.go`: `fb8ba98200d3b37b90de50fda3235711b84de53a`.
- `internal/application/service/chat_pipeline/rerank.go`: `659a0c6590b3d7bac0c71597a9ee4169b9b332df`.
- `internal/application/service/chat_pipeline/merge.go`: `21263c8a1ec4b0332ab95bc18c44b3c4abafc459`.
- `internal/application/service/chat_pipeline/merge_overlap.go`: `025e6c69292811dcda942c8fa75f9b3e032a37cc`.
- `internal/application/service/chat_pipeline/merge_expand.go`: `e459e96c5874361a49f9081fb606810f2ead6741`.
- `internal/application/service/chat_pipeline/merge_history.go`: `33d81b00a3a1db4416338299aea4fc206035e9a4`.
- `internal/application/service/chat_pipeline/into_chat_message.go`: `e8f923ce10642ca806eef59d129bce6833e1ea61`.
- `internal/searchutil/chunkmerge.go`: `a8149577b7325a87fd0f558e4904a5eae71409c8`.
- `internal/searchutil/textutil.go`: `eb927aa007c2fc508d8e87269106e938d77e8733`.
- `internal/searchutil/imageinfo.go`: `3cae5442d89c0ff158cfda083015961fdcbfcfc5`.
- `internal/searchutil/imageinfo_match.go`: `b0f54990518f02ed266c6c22599d17e6ac3d2401`.
- `internal/types/placeholder.go`: `cc829b3bcf2f9f1409c71547664894152936d5c2`.
- `config/prompt_templates/rewrite.yaml`: `b5f79dd75922bd2eea8a2950b983f5ab8bb08b70`.
- `config/prompt_templates/context_template.yaml`: `4e9ed770d5bec34d6b11ea58d75461b154852523`.
