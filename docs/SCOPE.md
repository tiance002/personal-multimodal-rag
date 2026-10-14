# Public learning scope / 公开学习范围

This is a selected source snapshot, not a new production release. The original three operational Goal documents were inspected but omitted: they mix historical resource boundaries, authorization and internal execution context with the engineering goal. This page summarizes the public engineering boundary rather than publishing task chat/history.

- Fixed reference: Tencent/WeKnora 3e8b0bfc80b845b2d4b2ed683994748741450a97; Python/FastAPI and React.
- Ordinary QA: admission, history/memory recall, query understanding, source retrieval/rerank/merge/context, unified provider, citations, SSE and persistent turns.
- Upload: immutable source, local format parsing, parent/child chunks, indexing, profile; separate durable OCR/caption image tasks.
- Accepted adaptations: P5 six-feature routing at 0.35, feature-wise max(original,rewrite); one controlled Cheap-to-Expensive escalation for truncation/empty/invalid references. Provider timeout does not upgrade. UNKNOWN reservations remain occupied.
- OCR/Caption adaptation: explicit Tesseract or vision OCR; explicit caption vision model or inheritance from valid vision OCR. User-declared vision is untested.
- Safety requires trusted scope, isolated identity, egress permission, idempotency and monthly budget. Importing modules does not start services; absent authorities deny sends.
- No GraphRAG, Agent/tool executor or sandbox work is added by this snapshot. Production migration, real provider quality and cutover are not accepted.
