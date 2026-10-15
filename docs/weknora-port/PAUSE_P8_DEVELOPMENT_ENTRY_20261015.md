# Owner-requested pause: P8 development entry, 2026-10-15

Status: PAUSED / IMPLEMENTED_PARTIAL / NOT ACCEPTED. Owner explicitly requested wrapping up and waiting. No further fixes or checks were started after that request.

## Workspace and scope

Independent worktree E:/RAG-Worktrees/weknora-python-port-20261013; branch codex/weknora-python-port-integrate-image-20261014. Accepted starting HEAD 53f4b87c474736f7faa3ae822bfc13ff72eea0f8. This checkpoint contains unaccepted P8 development-entry source/config/documentation changes only. Original checkout and four protected prior untracked files were not included. Their hashes are verified in evidence/P8_DEVELOPMENT_ENTRY_20261015/pause-protection.json.

## Saved implementation

New development.py assembles PostgreSQL/Redis/storage/runtime under explicit identity and ownership guards. entrypoint.py provides the API console entry and readiness; worker.py invokes image-ingest and memory queues; initialize.py requires an explicit empty new database. http_api.py and documents.py add backward-compatible consumer lifecycle flags. pyproject.toml selects the new chain for packaging and adds console scripts. React defaults to weknora_python. Development Dockerfiles/compose select the new entries; old compose/Dockerfiles and README are retained under explicit legacy/history paths. See docs/WEKNORA_DEVELOPMENT.md and docs/LEGACY_ENTRYPOINTS.md. No schema initializer, compose up, real model request or deployment was executed.

Default model sends remain denied until separately approved server-side permission/cost authorities are bound; /readyz returns 503 DENY_UNCONFIGURED without them. This is not a usable-provider or cutover claim. Worker end-to-end behavior is not yet proven.

## Executed checks and actual results

- `python -B -m pytest --noconftest tests/weknora_port/test_development_entry.py tests/weknora_port/test_http_api.py tests/weknora_port/test_documents.py -q -p no:cacheprovider --tb=short`: exit 0, 23 passed in 1.24s. Evidence: offline.stdout.txt and offline.exitcode.txt.
- `C:/Program Files/nodejs/npm.cmd run build` in frontend: exit 0; Vite production build completed. Evidence: react.stdout.txt and react.exitcode.txt.
- `python -B -m pytest --noconftest tests/weknora_port/test_development_entry_pg.py -q -p no:cacheprovider --tb=short`: exit 1, 1 failed in 13.13s. Failure at line 61: expected Redis memory pending hash count 1, observed 0. Evidence: http.first.stdout.txt and http.first.exitcode.txt. Local mock models only; real provider calls 0. Test did not reach worker/image assertions.

## Exact continuation point

1. Inspect why the memory task is absent after the HTTP request; verify RedisMemoryExtractionQueue enqueue/automatic consumption and the API/worker lifecycle boundary. Do not assume this is only a test bug.
2. The next assertion currently references memory_enqueuer._pump, which is not a field of RedisMemoryExtractionQueue (it uses tasks/pump_lock). Correct the assertion only after checking actual queue lifecycle semantics. Preserve default behavior for accepted old fixtures.
3. Complete real isolated image ingestion + memory extraction through the new worker, replay and duplicate-send checks using mock model transport. Current test has not proven these.
4. Installed-wheel build/import/console/HTTP proof: NOT RUN. Compose config validation: NOT RUN. No additional broad regression needed unless changed paths warrant it.
5. Update final P8 report and stage evidence only after actual checks; current work is a reversible, unaccepted checkpoint. Do not delete legacy code or deploy.

## Preserved boundaries and blockers

No REAL_PROVIDER_PASS or CUTOVER_PASS. No old database/volume changes, UNKNOWN release, history deletion, push/tag/release or service replacement. Production authority binding and Owner real-provider/deployment approval remain separate gates. Earlier accepted serial integration evidence remains applicable only to its unchanged inputs. Existing synthetic isolated test data is retained; no cleanup/flush performed.
