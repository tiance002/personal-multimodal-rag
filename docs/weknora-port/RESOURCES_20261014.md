# Resources and entry points — 2026-10-14

This inventory distinguishes current Python-port isolation from older deployments. No container rename, restart, volume rebuild or old data move was performed. Docker inventory was read with `docker ps -a` and mount/port-only `docker inspect` (no secret environment output).

| Resource | Location / identity | Purpose / rule |
|---|---|---|
| Implementation | E:/RAG-Worktrees/weknora-python-port-20261013; codex/weknora-python-port-resume-20261013 | Sole source writer for this port; original checkout untouched |
| Original source | E:/RAG quention; 55372f7; existing .serena/project.yml dirty | Read-only original; do not reset |
| Upstream | E:/RAG-reference/WeKnora-3e8b0bfc; exact3e8b0bfc | Fixed read-only source and Go oracle input |
| Frozen downloads | D:/RAG-Public-Bench; D:/RAG-v6-final-ingestion-pack-r1/attempt2; D:/RAG-v6-special-subsets-r1 | Preserve original paths/hashes; never generate replacement Gold |
| Earlier fixture originals | E:/RAG-Tests/runtime-source-* | Retained and referenced by saved evidence/DB; no move/delete |
| New fixture originals | E:/WK/run/<12-hex> | Short storage root for newly-created native/mock browser fixtures only |
| Evidence | docs/weknora-port/evidence and existing var/weknora-port/P7 batch paths | Immutable earlier success/failure evidence; short new artifact directory preferred |
| New candidate artifacts | E:/WK/pkg/<short-commit> | New artifacts only; no rename/rebuild of existing data |
| Isolated build environment | var/weknora-port/p8-build/venv | setuptools83.0.0; readonly original dependency reuse, no standalone dependency claim |

| Container | Host port / volume | Role / current state |
|---|---|---|
| weknora-python-resume-pg-20261013 | 127.0.0.1:56423; named volume same name; physical7696088403504697382 | Current Python-port isolated ParadeDB0.22.6-pg17; running |
| weknora-python-resume-redis-20261013 | 127.0.0.1:56424; named volume same name | Current Python-port isolated event/queue storage; running; run-id changes on restart |
| weknora-python-port-pg-20261013 | 56413; named volume same name | Previous C-candidate isolation; stopped; retain history |
| weknora-python-port-redis-20261013 | 56414; named volume same name | Previous C-candidate isolation; stopped; retain history |
| weknora-cpp-toolcheck-20261014 | none; no volume; network none/read-only | Exited compiler inventory helper; never an app service |
| WeKnora-redis | no host binding; existing anonymous volume7f9bdd... | Old upstream deployment, auto-started by existing policy; not owned by this port; untouched |
| raglocalfirst0930-* | Existing volumes/config | Old application deployment, stopped; untouched |
| p55a-*, ragv1final0927-*, v1review*, rag-eval-trust*, deploy-* and other WeKnora-* | Existing retained volumes | Historical deployment/evaluation resources; not current port; do not reuse/start/delete |

Readable naming proposal, report only: current PG could be `wk-port-db`, Redis `wk-port-events`; old candidate names may be prefixed `archive-`. A Docker rename preserves volumes but changes network/DNS references and breaks hardcoded identity checks/runbooks. Therefore keep names stable now; resolve exact affected callers and get runtime-change decision first. This inventory and `wk.ps1 status` supply role labels without touching services.

From the worktree, PowerShell: `./wk.ps1 status` inventories; `./wk.ps1 api` runs the foreground closed-mock native HTTP fixture on56425 with a new synthetic owner; `./wk.ps1 ui` in a second terminal runs native React on56426; `./wk.ps1 test` runs the bounded actual memory API/PG mock integration. Ctrl+C ends the foreground helper. The API stop-marker is also recorded in var/weknora-port/browser-fixture/ready.json; do not use an old PID/marker to stop another process. Port conflicts fail rather than replacing a service. No action starts Docker, changes credentials, sends real suppliers or runs full dataset ingestion.

Long-path root cause: verbose worktree/batch names plus deeply nested immutable version/asset paths exceeded Windows260. Native asset I/O already uses checked extended paths (78dda16); new fixture roots are shortened now. Existing originals remain untouched. Historical path lengths stay in evidence; no claim that old paths were renamed.

New retained restore-only logical database: wk_port_restore_20261014_8e7a3fa inside current isolatedPG/volume,fixture95 only; original/asset snapshot E:/WK/restore/ocr-8e7a3fa. It is not an applicationservice or oldbusinessDB; no newcontainer/port/volume. Latestcandidate E:/WK/pkg/8e7a3fa (see P8_SHORT_ARTIFACT_20261014.md). Parserlocation/availability caveats: PARSER_STATUS_20261014.md.
