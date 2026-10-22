# S8.6.4 real acceptance evidence archive

This is a purpose-selected archival copy for the 3-case HTTP + 4-image real acceptance. The latest actual query/model trace is retained as `actual-query-and-model-trace.json`; the private HTTP and image reviews are retained as named files. These are agent reviews, **not Owner approval**.

## Persistent original export

The successful 219-file raw export archive remains intact outside this tracked evidence directory at `E:\RAG quention\var\reports\s8-6-4\real-acceptance\final-export.tar`. Its SHA-256 is `442d172f2067f4acea1bb155281e68c32ee814921e2a36b814a0fad0d6a64a7e` and its size is 421529600 bytes (about 421 MB). The export manifest records persistent remote job root `/opt/rag-s8/diagnostics/s864-final-20261022t112810z-2bdb4629-originfix-235e37-job` and says the export was stable. The first-failure export is copied byte-for-byte into `raw/` and also remains at `E:\RAG quention\var\reports\s8-6-4\real-acceptance\first-failure-export.tar`.

The complete `manifest.json` lists every copied source path, original SHA-256 and size, stored SHA-256 and size, plus each omitted full strategy profile and `.stages.json` raw path/hash/size. Those full files remain unchanged in the persistent export and are not copied into Git. No environment dumps or request headers are included; provider request records report `headers_saved: false`.

## Contents and reconstruction

- `http-quality-review.json`, `image-quality-review.json`, `scoring-rubric.json`, `image-quality-rubric.json`, and `actual-query-and-model-trace.json` are exact copies of the private review artifacts captured for this archive.
- `post-run-summary.json` and `closure-receipt.json` are exact copies of their saved proof files. `usage-summary.json` is a compact derived summary from the frozen delivery manifest; it is operational accounting, not a quality score.
- `raw/000001.bin` and subsequent numbered files preserve selected evidence with source mapping in `manifest.json`. Files ending `.gz` are gzip-compressed byte streams of the original file bytes. Decompressing them reproduces the source byte-for-byte; `manifest.json` records the source and reconstruction SHA-256 and byte counts. Gzip metadata uses an empty filename and `mtime=0`.
- Archived HTTP case ledgers, result/source projections, complete captions, final Provider request/response bodies, response metadata, timing records, per-execution receipts, corrected controls/inputs, post-run evidence, and closure pre/post snapshots remain available through the manifest mapping.

The latest trace records that all 12 HTTP actual request-body hashes match SETTLED ledger entries; QU1/QU2 were `UNKNOWN`, QU3 attempted raw-dependent “eith”→“with” but protected fallback retained q0 “eith” and effective `UNKNOWN/PLAIN`. MML rerank best was raw BGE `0.02217947132885456` at index 3 for “Coding time”; the final neighbor sentence was delivered. Four vision calls were reported as `deepseek-flash` and stopped. The main report avoids claiming dependent-rule activation for this sample.
