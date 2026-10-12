# Ordinary DeepSeek connection-state repair, 2026-10-12

## Scope and proved defect

After explicit owner approval, two fresh probes were executed within the unchanged7probe/3question/75000Token limits. Xing4096 reached its cap and settled; DeepSeek again returned UNKNOWN, so all further generation stopped immediately. No webpage POST was sent, and no old identity or UNKNOWN was reset.

Read-only configured-gateway inspection confirmed both roles use the actual CloudChat and original `_send` in service Python3.11.16. The live pre-repair cloud source SHA was `5ec3b65fd0de16f4b6a15b3218b30fd4706f5d67e15f7a10c681ab8902ddcc1c`.

A full in-memory HTTP request reproduction proved a local defect in ordinary TLS reconnection: `HTTPConnection.close()` after the first pre-HTTP SSL EOF resets the in-flight request state from Request-sent to Idle. If the second connection succeeds, the original request is written once, then `getresponse()` raises `ResponseNotReady: Idle`; the existing diagnostic becomes generic TRANSPORT_FAILED. Previous tests invoked `connect()` without a full request/response and missed this transition.

This is a proved offline defect, **not proof of the cause of any historical live UNKNOWN**. Historical usage/results remain unavailable and occupied. The initial broader response-read diagnostic hypothesis was not expanded into this patch.

## Actual per-file changes

- `backend/app/adapters/models/cloud.py`: first pre-HTTP EOF retry now closes only the failed socket and sets it to None, preserving the in-flight HTTP state. Terminal failures still close fully and return typed NOT_SENT. Two connection attempts maximum; one HTTP request; same TLS context, hostname/certificate checks and repeated egress/budget/lease fences. Frozen transport remains on its original path.
- `backend/tests/test_ordinary_deepseek_tls.py`: complete request/getresponse regression, exact HTTP/body send counts, terminal EOF, certificate, revoked-fence and late-failure boundaries.
- `scripts/smoke_ordinary_deepseek_tls.py`: dependency-free standard-library smoke using the actual cloud module, memory sockets and simulated connect outcomes. No network, real credentials or database activity.
- `scripts/probe_ordinary_output.py`: exact new UNKNOWN acknowledgment and `owner-followup-2` label for a future fresh seventh probe. The old receipts/Run identities cannot be resent or overwritten; max7 and75000 remain unchanged. This opt-in does not constitute owner approval.
- `backend/tests/test_output_probe_failure.py`: earlier acknowledgments cannot authorize the latest UNKNOWN or any future one.
- This report and `progress.md`: actual call/ledger outcomes and next approval boundary.

The repaired cloud SHA is `24ca85b5c828274bf35dc85635a70ef8847687af02e9bca07d49baa872add7be`. Scoring, attempt limits, model policies/ledger, final validation and atomic citation transaction were not changed.

## Actual new probes / PG readback

| Run | Outcome | Actual usage / occupancy |
|---|---|---|
| `8b61ffc6-f251-46a7-a357-9f4431552ebe` | Xing4096 VERIFIED; length; token state settled | input128+output4096=4224 |
| `33a016bc-41d1-4340-a072-5dd50b594af1` | DeepSeek4096 UNKNOWN; transport/TRANSPORT_FAILED | input708+output4096=4804 retained |

Six probes total, zero webpage questions. Actual known usage6576 plus UNKNOWN10671 = **17247 occupied Token**. Full original bounds across all six probes = **19755 Token**. The three UNKNOWN reservations remain intact. Actual PG readback confirms all six public probe Runs failed deliberately without conversation messages; no factual answer/citations were committed.

Ignored local evidence: `var/reports/weknora-ordinary-20261012/probe-batch-six-readback.json`, individual public receipts, `gateway-identity-readonly.py` output and `xing4096-binding-preview.json`. Config preview bound exact pinned Xing2048+4096 receipts: cheap READY_CONFIG, observed2048/4096, inferred1024, supported512/1024/2048/4096. This was an internal preview only, **not a live service configuration switch**; DeepSeek remained NOT_READY.

New public proof file `var/model-assets/ordinary-output-capability/receipts-v3-xing4096.json` SHA256 `9dda9cd7b19fa42571297490b1ff437082c24fbf8342f09b3eb8356e67fe3b30`. No secrets are stored in the proof.

## Commands and exits

Actual live Xing probe (exit0, VERIFIED):

```powershell
docker exec -e PYTHONPATH=/app raglocalfirst0930-api-1 python /tmp/probe_ordinary_output.py --role chat_cheap --cap 4096 --root /data/storage/weknora-probes-20261012 --acknowledge-unknown 046c3327-5dd9-4d44-a6e6-876897da41ab --acknowledge-followup-unknown 72917fff-bd37-4607-b3db-7c7e402d3fbf
```

Actual live DeepSeek probe (exit1, UNKNOWN4804; batch stopped):

```powershell
docker exec -e PYTHONPATH=/app raglocalfirst0930-api-1 python /tmp/probe_ordinary_output.py --role chat_expensive --cap 4096 --root /data/storage/weknora-probes-20261012 --acknowledge-unknown 046c3327-5dd9-4d44-a6e6-876897da41ab --acknowledge-followup-unknown 72917fff-bd37-4607-b3db-7c7e402d3fbf --new-probe-label owner-followup-1
```

TLS regression before repair: `.\.venv\Scripts\python.exe -m pytest -q backend/tests/test_ordinary_deepseek_tls.py -k eof_reconnect_preserves_inflight --tb=short -p no:cacheprovider`, **exit1,1failed/17deselected**, getresponse ResponseNotReady:Idle. After repair: same module without `-k`, **exit0,18passed/5warnings**. Actual tests are simulated sockets/calls, no provider or production DB.

Necessary associated service/provider group: `.\.venv\Scripts\python.exe -m pytest -q backend/tests/test_service_usage.py backend/tests/test_model_provider_contracts.py --tb=short -p no:cacheprovider`, **exit0,86passed/5warnings**.

Probe stop rules: `.\.venv\Scripts\python.exe -m pytest -q backend/tests/test_output_probe_failure.py -p no:cacheprovider`, **exit0,7passed/5warnings**.

Standard-library smoke: `.\.venv\Scripts\python.exe scripts/smoke_ordinary_deepseek_tls.py`, **exit0**, Python3.13.0, four simulated cases PASS. Success after first EOF:2connect/3guard/1HTTPrequest; terminal EOF2connect, certificate1connect, revoked-fence1connect all0HTTPrequests.

Read-only actual probe/ledger collector: `.\.venv\Scripts\python.exe var/reports/weknora-ordinary-20261012/collect-probes-readonly.py`, **exit0**, sixprobes/fullbounds19755/occupied17247/UNKNOWN10671. Internal Xing proof preview: `docker exec -e PYTHONPATH=/app raglocalfirst0930-api-1 python /tmp/bind-xing4096-readonly.py`, **exit0**, no generation/live configuration change.

Earlier source reads named nonexistent frontend App.tsx/ordinary-recovery.spec.ts; they failed and did not verify UI. The actual stopped batch dispatched no frontend action.

## Remaining gate

Candidate image/native Linux verification and actual deployment evidence are added below when executed. Source-level/offline repair PASS is not real DeepSeek capacity or RAG success. No three-question replay, current factual answer/citation readback/reload, or G5 cleanup has run.

Under the owner-approved stop condition, a fresh decision is required for the single remaining seventh DeepSeek4096 probe on the repaired candidate, followed only after known valid capacity evidence by the original scoped three-question fenced replay. This would not enlarge7/3/75000. All three UNKNOWN remain included. Any new UNKNOWN halts again. No Push/Tag/release or owner acceptance is claimed. **未实现的命令不得报告通过。**

## Candidate image / native Linux evidence

Source repair commit `47f3445` built as `personal-rag:weknora-tls-state-20261012`, with OCI revision label set to the actual full commit SHA. `docker build -f deploy/Dockerfile.api -t personal-rag:weknora-tls-state-20261012 --label "org.opencontainers.image.revision=$($taskSourceSha.Trim())" .` (taskSourceSha was actual `git rev-parse HEAD`): **exit0**. Image config `sha256:0859d4c8319fc1a48132eefbb3ca0c2ab614cb6547fdb1acbbdac9bed3e900c4`, manifest list `sha256:cfd161ba200916708eb4d0f33a9d0d7b46b829bb8cf84fc3d2cbbfd339f05668`.

```powershell
$taskSmokePath = (Resolve-Path -LiteralPath scripts/smoke_ordinary_deepseek_tls.py).Path
docker run --rm --network none --mount "type=bind,source=$taskSmokePath,target=/tmp/smoke_ordinary_deepseek_tls.py,readonly" -e PYTHONPATH=/app personal-rag:weknora-tls-state-20261012 python /tmp/smoke_ordinary_deepseek_tls.py
```

**exit0 / PASS**, actual candidate Python3.11.16; check kind OFFLINE_ACTUAL_HTTP_STATE_MACHINE_SIMULATED_SOCKETS. Four cases PASS with same counts as above. provider_calls0, network_calls0, database_calls0. The actual repaired cloud module was imported from the candidate image; no private env file or DB volume was mounted, networking was disabled and the image migration CMD was replaced with the explicit Python command.

Candidate **built and natively tested, NOT DEPLOYED**. Live API/Worker remain on image5de88de8... and original source5ec3b65..., old proofacfa33...; there was no live service swap after the latest UNKNOWN. Native passing sockets do not prove a live provider response. Deployment, final capacity probe and original/short/long webpage recovery/citation verification still require the fresh decision described above.
