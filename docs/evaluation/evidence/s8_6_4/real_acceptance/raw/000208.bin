# S8.6.4 accounting and offline validation addendum

**Source baseline:** `f96446742d1f32d7fa13635e43159b08fc3e2e0a`  
**Status:** Offline accounting/vision evidence assembled; authorized real acceptance remains **PENDING / NOT RUN**. No new HTTP or VLM answers were generated.

## Owner decision and accounting behavior

The initial Owner approval authorized the exact three fresh-session HTTP cases and four fixed-image comparisons under a 500,000-token overall cap, a 40-attempt/40-minute operation ceiling, 12 attempts and 100,000 tokens per HTTP child, with no retries. The later Owner supplement, recorded after the vision/accounting change and tied to source `f96446742d1f32d7fa13635e43159b08fc3e2e0a`, explicitly waives per-request token estimation/reservation for this round. It keeps scope and caps unchanged and applies `reported_usage_only_v1` to fresh ledgers only. Old ledgers and historical UNKNOWN rows remain unchanged.

This addresses the difference between an input estimate and actual usage: the legacy reservation path treated serialized wire bytes as a conservative input-token proxy. For inline images, those bytes include base64 encoding and can inflate reservations dramatically; that number was not Provider-reported usage and must not be described as actual token consumption. The vision path still validates provider/model/API, body identity, HTTPS endpoint, decoded JPEG/PNG format, dimensions and image/wire size bounds, and retains the actual wire hash and model output bound. In legacy reservation mode, the input proxy removes validated image data URLs before measuring text and applies a fixed per-image proxy instead of counting base64 bytes as tokens.

For the newly authorized round, the Owner waived those per-request estimates entirely: no input-token estimate, token reservation, or `held_tokens` is written to fresh reported-only ledgers. A row settles only from a structurally valid Provider-reported prompt/completion/total usage. Missing/invalid usage, unclear outcome, or UNKNOWN stops future sends without retry. If settled actual usage reaches or exceeds a limit, the full actual usage is retained and later sends stop. This protects settlement truth and stop behavior but cannot guarantee that the final already-sent request stays below the cap. Request/body, model output, call-count, scope, attempt and elapsed-time guards remain.

## Offline commands and results

The recorded runs are overlapping affected-test sets and **must not be summed** as unique test counts. `52 passed` is the preliminary vision-only run; the combined vision/execution run later reported `164 passed, 1 skipped`; the final owner-helper and integration runs reported `49 passed` and `35 passed`, respectively. All are offline with recorded `provider_calls: 0`; the profile separately records a read-only readiness inventory with `db_writes: 0` and transaction rollback. These results do not imply any real acceptance call ran.

| Run | Recorded command | Exit | Result |
|---|---|---:|---|
| Vision-only preliminary | `python -m pytest tests/weknora_port/test_execution_vision.py -q --tb=short -p no:cacheprovider --basetemp E:/RAG quention/var/reports/s8-6-4/vision-reported-green1-tmp` | 0 | 52 passed; preliminary only. Command/path is recorded verbatim in the evidence file. |
| Vision + execution final | `New-Item -ItemType Directory -Path 'D:/RAG-s864-vision-offline-20261022-0cab81' -Force; WEKNORA_EXECUTION_TEST_ROOT='D:/RAG-s864-vision-offline-20261022-0cab81/fixtures' python -m pytest tests/weknora_port/test_execution_vision.py tests/weknora_port/test_execution_fence.py tests/weknora_port/test_execution_linux_paths.py tests/weknora_port/test_output_budget_contract.py -q --tb=short -p no:cacheprovider --basetemp 'D:/RAG-s864-vision-offline-20261022-0cab81/pytest-tmp-clean2'` | 0 | 164 passed, 1 skipped; skip is the real POSIX resolve/flock test on Windows. |
| Owner helper final | `accounting-helper/final-v2.run.json` 里的 `command` argv array（python executable, `-m`, `pytest`, `-q`, `tests/weknora_port/test_s862r3_40_acceptance.py`, `--basetemp`, report temp path） | 0 | 49 passed in 5.35s. Exact argv and cwd are in the hashed run metadata. |
| Offline integration final | `accounting-integration/final.run.json` 的 `executable` 与 `arguments` 原样拼接 | 0 | 35 passed in 3.32s. Exact argv and cwd are in the hashed run metadata. |

## Preserved setup and retry failures

The raw history retains local preparation failures and superseded test runs. The first local profile proof failed with `UnicodeDecodeError` because a subprocess used the Windows locale encoding; it was rerun with explicit UTF-8. The second local profile identity attempt failed with `ModuleNotFoundError: scripts` because the standalone private artifact lacked the repository on `sys.path`; the verified worktree root was then added locally. Neither involved a remote operation or Provider send.

An earlier E: pytest temp run failed on `PermissionError: [WinError 5]` during atomic `os.replace` of an execution fixture. A retry on D: initially failed because its parent temp root had not been created (`WinError 3`, fixture setup errors). Both logs remain preserved; the later command explicitly created the isolated D: parent and reached the passing 164/1-skip result. Early red/intermediate owner-helper failures also remain archived; the final helper run is the 49-pass evidence.

## Archive and limits

`accounting-offline-summary.json` records SHA-256 and byte length for the approvals, fresh profile, readiness record, successful command/result files, and representative failure logs. Archive these captured bytes unchanged. The fresh reported-only profile and offline test logs record zero Provider calls. Real authorized execution results remain pending and must be scored separately from these offline checks.
