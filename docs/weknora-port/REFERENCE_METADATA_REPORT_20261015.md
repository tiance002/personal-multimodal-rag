# Source reference DTO metadata correction, 2026-10-15

Separate scope after P8 proof8b9f96e. IMPLEMENTED; scoped OFFLINE_PASS and ISOLATED_INTEGRATION_PASS. Full paired Go/Python differential rerun NOT RUN here; independent review pending. REAL_PROVIDER/CUTOVER NOT RUN.

## Problem and minimal production change

Read E:/WK/go-linux/PYTHON_GO_DIFF_REVIEW_20261015.md, field-differences.json and reference-key-audit.json. Fixed upstream3e8b0bfc types/search.go SearchResult and types/chunk.go offsets are non-null integers with zero retained. chat_pipeline merge_overlap.go checks Unicode rune counts. Python offsets remain Unicode code-point half-open intervals [start_at,end_at), never UTF8 byte/UTF16 unit indices, and are not recomputed for edited/enriched text.

Evidence.from_result discarded source coordinates and metadata; coordinator._references then omitted them in FinalCommit. Corrected generation/context.py and coordinator.py. SearchResult adds seq/knowledge_channel at the end, preserving existing positional constructors. Evidence appends compatible defaults for older direct constructions, but from_result always copies actual row.start_at/end_at; null/non-integer coordinates are rejected, not replaced with0. JSONB schema/repositories need no changes: PostgresTurnRepository.commit_final -> PostgresHistoryRepository.commit_final serialize exact references, runtime.list_messages and PostgresMemoryRepository._message return them unchanged.

Other audited fields now copied: seq, source numeric MatchType enum, sub_chunk_id, metadata, parent_chunk_id, image_info, knowledge_channel; populated chunk_metadata, matched_content, knowledge_description and knowledge_custom_metadata preserve upstream omitempty behavior. Optional empty sub_chunk_ids use source nil JSON null. Retrieval currently represents match types as embedding/keywords/history/etc.; the new boundary maps these to the fixed types/embedding.go enum without changing internal routing/retrieval. Dict metadata is copied before generation. Existing knowledge source/title/file/content/scope/score remain included. SearchResult's new seq/channel values are preserved when supplied; no new DB channel ingestion feature is claimed.

## Exact counterexamples and actual entry proof

New test_reference_metadata.py runs actual GenerationCoordinator finalization on source rows: start0/end3 with Chinese+supplementary-plane characters; start5/end8 on same3-code-point content; explicit0/0; edited/replaced text with source5/88 preserved; overlapping Chinese/emoji chunks merged0/5 with child ID retained. Additional null/False/string/float offset negatives and pinned numeric match enum cases. Metadata values and copy isolation checked. These are exact assertion counterexamples, not response-only decoration.

New test_reference_metadata_pg.py uses actual loopback TCP FastAPI -> upload/parse/index -> real PG retrieval -> generation(MockTransport) -> PostgresTurnRepository/PostgresHistoryRepository JSONB -> HTTP and recent_messages history. Two distinct RequestIDs preserve source0/27 for Blue widgets use W-17. plus Chinese/emoji; encoded byte length differs. Nonempty description and matched_content persist. Matched content is compared exactly to real embeddings.content, including source title prefix. Previous first-turn references survive second-turn readback. A separate manually constructed legacy history row verifies old missing keys remain missing in both actual HTTP-model listing and memory history, without changing historic records. It does not replace the full-entry two-turn proof.

## Commands and exits

Evidence: docs/weknora-port/evidence/REFERENCE_METADATA_20261015/.

- `python -B -m pytest --noconftest tests/weknora_port/test_reference_metadata.py tests/weknora_port/test_generation.py tests/weknora_port/test_pipeline.py -q -p no:cacheprovider --tb=short`: exit0,168 passed in1.54s; offline.stdout/exitcode.txt.
- `python -B -m pytest --noconftest tests/weknora_port/test_reference_metadata_pg.py -q -p no:cacheprovider --tb=short`: final exit0,1 actual integration passed in5.42s; http.fourth.stdout/exitcode.txt and http.<id>.json. Explicit approved isolated PG/Redis identities; real_provider_calls0.
- Earlier exit1 attempts are retained: first test erroneously counted the generated summary as a second original text chunk; second tried disabling summary_model_id and exposed POSTPROCESSOR_PENDING; third incorrectly equated indexed matched_content with raw body (actual title prefix is preserved). Final fixture restores normal summary configuration, selects chunk_type=text explicitly and checks the actual index content; source-coordinate/metadata assertions remain exact.

source-and-test.sha256.json binds actual tested files. No repeated253/27/8 whole integration run, no provider request or old DB writes. P8 immutable wheel9567c88 remains historical proof for that source; this reference fix is not included in it, and no newer wheel claim is made.

## Risk and next review

Independent paired Go/Python reference metadata review required; do not infer full DTO equivalence from the prior158 core-field projection. Upstream nil/empty metadata/optional key distinctions and channel/seq producers remain visible review boundaries. New reference JSON adds fields intentionally, without a migration or rewriting legacy JSON.

Additional concrete observation, not deferred/accepted: on a fresh synthetic KB, setting summary_model_id='' caused upload to return pending/POSTPROCESSOR_PENDING. SourceProfileProcessor.process returns False without a summary model while document lifecycle treats False as pending. This is outside the current metadata fix; Owner/root should review the source semantics and minimal handling separately. No algorithm/threshold/assertion was changed to hide it. Normal configured summary path passes above.

No service replacement, existing configuration/key edits, UNKNOWN release, history deletion, legacy deletion, push/tag/release. Next step is root's independent review of this isolated commit and byte-binding addendum. No Owner milestone acceptance claimed.
