# Findings

- knowledge._read_chunks omits seq, although Go buildSearchResult sets Seq=ChunkIndex.
- hybrid_search hydrates hit score/body but omits unconditional source image_info enrichment, even skip=true.
- Existing merge.collect_image_info already implements two-level batch reads; project six fields on first insert to avoid importing child Owner extensions into new parents.
- Same-parent ID alone is insufficient when an allowed second document/version has a corrupt cross-parent binding. Pass known parent sources to the shared collector and validate KB/document/source_version on both levels; fail closed for permission/scope/cancellation/deadline.
- Source_version is chunk_metadata['source_version']; legacy absent/absent stays supported. Existing nonempty parent strings and child strings must remain literal.
- Source fusion tie order remains UNDEFINED_FUSION_TIE_ORDER. No rank changes.

Real PG caught an overbroad new version guard: model-generated summary children deliberately lack source_version and are ignored by fixed image collector. Only relevant text/image_ocr/image_caption children should undergo parent-version check; do not alter summary chunks. Minimal evidence pg-pump.domain-diagnostic.json. Diagnostic first ran from protected cwd and imported old backend: exit1 ModuleNotFoundError; corrected independent cwd read exit0.
