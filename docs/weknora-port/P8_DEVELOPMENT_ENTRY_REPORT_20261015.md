# P8 development entry and installed-wheel proof, 2026-10-15

Status: IMPLEMENTED / scoped OFFLINE_PASS / scoped ISOLATED_INTEGRATION_PASS. NOT REAL_PROVIDER_PASS; NOT CUTOVER_PASS; no whole-stage parity or Owner acceptance claimed. Original accepted source baseline53f4b87; unaccepted checkpointec98695; functional correction9567c8817e1fab81b88710af6063342838ffa423. This report/evidence is a separate local commit.

## Actual changes and defaults

- backend/weknora_port/development.py: standalone assembly of identity-verified PostgreSQL, Redis, owned storage, local principal, document service and actual ApplicationRuntime. Explicit WEKNORA_PORT_* environment only; no old RAG_* fallback. API factory and worker use the same assembly.
- backend/weknora_port/entrypoint.py / worker.py / initialize.py: new API, actual persisted image-ingest and memory-extraction consumers, explicit empty-new-database initializer. API does text ingestion inline and leaves image/memory tasks persisted. Worker normal mode polls both real queues continuously, even after idle; --once handles up to one due task of each type. Initialization was NOT RUN.
- backend/weknora_port/storage/memory_queue.py: optional autostart_on_enqueue flag defaults True for existing callers. New assembly explicitly sets False. Existing lease/retry/grace/UNKNOWN semantics and thresholds are unchanged.
- documents.py and http_api.py retain accepted lifecycle defaults; new assembly disables API consumers. React main.tsx defaults to weknora_python; legacy remains an explicit selector. pyproject scripts select new entries and wheel excludes backend.app/backend.tests. deploy API/worker Dockerfiles and new development compose select new entries; old configs and README remain under deploy/legacy and docs/history. No legacy source deleted.

Configuration priority: required explicit WEKNORA_PORT_* environment -> validated physical resource identities/ownership -> persisted server model-role/settings resolution through accepted runtime. Missing permission/cost authorities deny model sends; readiness is503 DENY_UNCONFIGURED. Synthetic authorities were injected only in isolated tests. Default router DYNAMIC, fixed0.35 and existing switch semantics unchanged; auxiliary models still use their configured roles. Optional explicit Ollama endpoint has allow_pull=False. No provider credential fabricated. Model-ready production authority binding remains outstanding.

Upstream implementation contracts are the existing fixed3e8b0bfc native document ingestion/ImageTaskQueue and ExtractionWorker/RedisMemoryExtractionQueue paths, not the legacy placeholder worker. This batch changes assembly/consumer ownership, not upstream RAG algorithms, estimator, citations, route scoring or summary semantics. Reference-persistence parity investigation is separately owned by the root reviewer; no 158-row or full differential PASS asserted here.

## Failure root cause and repair

http.first exit1 is retained. The HTTP generation successfully committed and enqueued memory under its configured tenant/scope/prefix; enqueue called start(resume=False), creating a hidden memory pump despite API lifespan consumers being disabled. drain_background waited for that pump to consume the delayed job, so pending jobs were0. This was a lifecycle bug, not evidence for weakening the expected1 assertion. New assembly disables enqueue autostart; assertion remains1. The invalid subsequent _pump field assertion was replaced with the queue's actual tasks set. A second independently assembled consumer now reads the same physically verified persistence and performs image processing + memory extraction. An idle continuous worker later consumes another HTTP-uploaded image without restarting.

## Commands, exits and evidence

Evidence directory: docs/weknora-port/evidence/P8_DEVELOPMENT_ENTRY_20261015/. Python test commands use E:/RAG quention/.venv/Scripts/python.exe -B with explicit isolated DSN and Redis identity; models use MockTransport only.

| Command | Exit / observation | Evidence |
|---|---|---|
| python -B -m pytest --noconftest tests/weknora_port/test_development_entry.py tests/weknora_port/test_http_api.py tests/weknora_port/test_documents.py -q -p no:cacheprovider --tb=short |0;23 passed (checkpoint check, not repeated)|offline.stdout/exitcode.txt|
| npm.cmd run build (frontend) |0;Vite build (checkpoint, unchanged frontend)|react.stdout/exitcode.txt|
| python -B -m pytest --noconftest tests/weknora_port/test_development_entry_pg.py -q -p no:cacheprovider --tb=short |first1;after repair0;final0,1 passed12.40s|http.first/second/final.stdout/exitcode.txt|
| python -B -m pytest --noconftest tests/weknora_port/test_development_entry.py tests/weknora_port/test_memory_queue.py -q -p no:cacheprovider --tb=short |0;11 passed,9 config checks+2 actual Redis checks|queue-scoped.stdout/exitcode.txt|
| docker compose -f deploy/compose.yml config --quiet; docker compose -f deploy/compose.data.yml config --quiet |both0; synthetic validation env only; no up/build/start|compose*.stdout/exitcode.txt|
| python -B scripts/weknora_port_package.py --ref 9567c8817e1fab81b88710af6063342838ffa423 --output E:/WK/pkg/p8-dev-entry-20261015 |0;161 native wheel members byte-match raw Git|wheel-build.stdout/exitcode.txt;wheel.manifest.json|
| python -B -m pip --python E:/WK/pkg/p8-dev-entry-20261015/installed/Scripts/python.exe install --no-index --no-deps E:/WK/pkg/p8-dev-entry-20261015/dist/personal_rag-0.1.0-py3-none-any.whl |0; fresh venv; explicit read-only third-party paths reused|wheel-install.stdout.txt;installed-entry.receipt.json|
| installed/Scripts/weknora-python-{api,worker,init}.exe --help (three commands) |all0|api/worker/init.help.txt;installed-entry.receipt.json|
| E:/WK/pkg/p8-dev-entry-20261015/installed/Scripts/python.exe -I -B docs/weknora-port/evidence/P8_DEVELOPMENT_ENTRY_20261015/installed_verify.py |0;1 actual loopback HTTP/PG/Redis/worker test12.49s;97 backend modules from installed site-packages,0 source backend imports|installed-http.stdout/exitcode.txt;installed-http.receipt.json|

Wheel SHA256:5fb5215b3b4da0f12dc7af4c8bcb13815ba4d2413405c2103b632911fe784459;2832592bytes. New E:/WK/pkg artifact and installed venv only. No download this resumed batch; no C-drive development/cache. Full fresh dependency installation/resolution is NOT CLAIMED; installed-code verification reused three explicit read-only third-party dependency paths recorded in receipt. Separate worker assembly was verified in the same test process; OS service deployment was NOT RUN. No Docker image build, real Ollama, actual provider or cutover test.

## Protection, provenance and outstanding gates

Four pre-existing untracked files retain their exact hashes and remain untracked; accepted SERIAL_INTEGRATION evidence unchanged. final-protection.json records checks. estimator-origin.json shows no P8 modification: raw Git SHA2563db5a02df3d6e7a235f1e219a5d204d5b921148d0c6799b50403d7733030b291 at53f4b87/ec98695/9567c88; disk SHA25631d5156f6b117eb065d3410e3947f16717036b0a7b973a07b0992797dda88fef differs by one CR and normalizes exactly. Last modifying commit1a045b979a52f6ebae4dc2b611c34ec556ca5023.

Prior visible pause text is preserved, but its actual message ID/time/sender provenance are unavailable: UNKNOWN, not authenticated Owner identity. No restricted logs accessed. Root's latest delegation reports Owner denial and explicitly authorizes resume; development followed that latest instruction. Earlier pause document remains historical, superseded by this report.

Remaining gates: separately reviewed real server-side permission/cost authority binding; real-provider/Local acceptance under fresh explicit approval; approved new schema initialization and shadow startup; image build and process deployment validation; real cutover/recovery and Owner acceptance. No existing service/config/key changes, old business DB writes, UNKNOWN release, old code/history deletion, push/tag/release. Old code deletion remains a later separate review after functional/cutover acceptance. Existing253/27/8 accepted integration suites were not repeated.
