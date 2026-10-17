# P26 — Public Source-Handle Residue and MML Visual QA

- **Status:** `P26_COMPLETE_WITH_CAPABILITY_LIMITS`
- **Source baseline:** `9fe6f853d8dad30e97314b83984633f621940a90`
- **Branch:** `codex/weknora-python-port-integrate-image-20261014`

## 1. Public source-handle residue

P25 `row-0685` returned this unvalidated prose attribution after its answer:

> **Source:** *11-21-16-Updated-Post-Election-Release* (document d1, chunk c1)

P25's saved answer is in `E:\WK\run\p25-20261017\runtime\results\row-0685.json`; the captured answer-stage provider request is `E:\WK\run\p25-20261017\runtime\provider-requests\863aab4cd90b8612aab94e50618dcdbd55c4e59bb14aae37a0ba17d32fc66574.json`. The provider request context contains private `d1` and `c1` handles but not the generated attribution line. The response completed with `finish_reason=stop` and a parsed final response. P25's saved SSE and history answer match the same output. This identifies the source as model-generated prose echoing context-local handles, not citation readback or database leakage. The P25 evidence and stored history were not rewritten.

The citation-disabled output decoder previously removed transport tags such as `<ref>`, `<kb>`, and `<web>`, but did not recognize a free-form `Source:` line. The minimal fix now removes only a line explicitly containing the observed `(document dN, chunk cN)` form, only when public citations are disabled, and only outside Markdown code. The SSE decoder buffers only a possible `Source:` prefix until it can classify the complete line; ordinary answer text continues streaming. The history renderer applies the same narrow rule to old messages without changing the database.

Regression coverage preserves ordinary `c1`/`d1` text, ordinary source prose and URLs, inline/fenced code, images, links, citation-enabled text, and incremental SSE behavior. No global string replacement is used.

## 2. MML visual QA readiness

The current active configuration has no configured VLM role. Its Cheap model is configured for chat but is not cataloged as a general vision model and resolves with `supports_vision=false`; no model capability was inferred from its name or chat protocol. Current image-processing KB configurations have `enable_multimodal=false` and empty `vlm_config`. The relevant current documents have no `image_info` rows or image tasks. Existing retrieved-document Context enrichment can add saved OCR/caption metadata, but the current index has none for these targets. The final generation path can pass images supplied as direct attachments when configured for vision; it does not pass original bytes of retrieved indexed images through this path.

Consequently, none of the four frozen rows had an eligible image or sufficiently grounded derived visual evidence in the final model request. No Gold answer was placed in a request. All four rows are `NOT_RUN`; answer and citation scores are not applicable.

| Frozen QID | Existing source/index evidence | Status and reason |
|---|---|---|
| `row-0430` | Cat-vs-dog source SHA256 `40a74d3fc5e73c81465206a4043c19b850daf66fc5617756f5894d25ad054b4b`; 68 pages. Current 53 chunks contain no tiger/image-derived evidence; image metadata and image tasks are absent. | `NOT_RUN` — the requested whole-document visual count is unavailable in the current index, and no vision-capable final model is configured. |
| `row-0431` | Same Cat-vs-dog source. Frozen page labels are 29, 30, and 32 with page-index convention unknown. Read-only renders of physical pages 29, 30, and 32 are saved under `E:\WK\run\p26-20261017\cat-pages\`; they show the relevant diagrams, but the current 53-chunk index has no matching image metadata/diagram evidence. | `NOT_RUN` — physical pages were identified, but the diagram's required structure is not present as indexed evidence and no VLM is configured. |
| `row-0684` | Pew source SHA256 `8f49f93f1665fd5ff604e66ffed9649d8b18ce7f114ab43463aeac46cd3d00cb`; current 163 chunks contain occurrences of `81%` and `78%`, but they are unrelated chunks rather than the chart-specific comparison. Current KB multimodal processing is disabled. | `NOT_RUN` — current Context cannot establish the chart-specific values; no VLM is configured. The matching numbers elsewhere in the document are not treated as evidence. |
| `row-0958` | B3 source SHA256 `43be9592854c7da10bf011335faec017b726ff2e23bb4ccae7e8fe88d7801f22`; current 35 chunks contain “shiny surface” in two chunks, but no indexed mirror or shiny-object interpretation. Image metadata/tasks are absent. | `NOT_RUN` — current OCR text is insufficient for the diagram semantics and no VLM is configured. |

The evidence-readiness review reused P10's frozen manifest and visual-capability audit. It did not rerun OCR, reparse, reupload, reindex, or repeat rows `0434`/`0959`. The old standalone Tesseract run is not treated as current-KB evidence. P10 previously recorded that the Cat animal count, Cat face-network structure, Pew chart values, and B3 diagram semantics were not established by its OCR/parse path.

## 3. Calls, tokens, data changes, and validation

- P26 real Cheap answers: `0`; final-model requests: `0`; auxiliary provider calls: `0`.
- P26 Token usage: `0`; new `UNKNOWN`: `0`.
- No P26 token fence was opened. The P25-specific fence was closed and was not reused.
- No FastAPI or Worker process was started. No Redis operation, upload, index write, or database mutation was performed. PostgreSQL/KB status inspection was read-only; the identified database is `weknora_python_port_20261013` with system identifier `7696088403504697382`.
- P25 report/results, historical ledger rows and `UNKNOWN` records, frozen Gold, current source/index files, and inherited uncommitted files were preserved.

| Command | Exit | Result |
|---|---:|---|
| `& 'E:\RAG quention\.venv\Scripts\python.exe' -B -m pytest tests/weknora_port/test_references.py tests/weknora_port/test_generation.py tests/weknora_port/test_streaming.py tests/weknora_port/test_generation_protocol.py -q` | 0 | `105 passed` |
| `npm run test -- tests/weknora-port/adapter.spec.ts -g "old history strips internal reference markers"` (from `frontend`) | 0 | `1 passed`; mocked history UI test, not a real-app browser run. |
| `npm run build` (from `frontend`) | 0 | TypeScript and Vite production build passed; 274 modules transformed. |
| `git diff --check -- backend/weknora_port/references/decoder.py backend/weknora_port/references/registry.py frontend/src/weknora_port/WeKnoraPythonApp.tsx tests/weknora_port/test_references.py frontend/tests/weknora-port/adapter.spec.ts` | 0 | No whitespace errors; Git emitted only the existing LF-to-CRLF normalization warning for `decoder.py`. |

## 4. Changed files and limitations

P26 code changes are limited to:

- `backend/weknora_port/references/registry.py` — citation-disabled prose-handle filtering.
- `backend/weknora_port/references/decoder.py` — incremental SSE buffering for a possible private source line.
- `frontend/src/weknora_port/WeKnoraPythonApp.tsx` — safe display of old persisted answers.
- `tests/weknora_port/test_references.py` and `frontend/tests/weknora-port/adapter.spec.ts` — focused regressions.

This branch still contains pre-existing dirty/untracked P24/P25, owner/evidence and `.serena` files. They were retained. P26 did not modify P24/P25 result files, did not commit, push, deploy, merge, or tag.

The source-handle fix is verified offline; no live browser validation was requested or performed. All four visual questions remain unevaluated because current indexed visual evidence and a configured vision-capable model path are absent. A future live run would need the normal upload/index pipeline to persist image or validated derived evidence, a configured/authorized compatible vision model, and a distinct valid finite token fence before sending any request.
