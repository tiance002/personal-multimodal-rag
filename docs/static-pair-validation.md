# Controlled static pair batch

This interface admits only the reviewed four synthetic prompts (Q01/Q02/Q04/Q07),
deepseek-flash, output256, temperature0, timeout<=30 and no automatic retries.
The exact manifest bytes are pinned in `APPROVED_SHA256`; changing any identity,
prompt, evidence, model or budget requires a separately reviewed source change.
The existing 50-attempt/1M-token canonical ledger remains the only accounting store.
The four conservative reservations sum to4205, within the batch bound8192.
Settling usage never refunds the batch's cumulative reserved bounds. Unknown,
truncated and pending attempts retain global conservative bounds and cannot replay.

Registration example (illustration only; NOT EXECUTED against the real ledger):

```python
from backend.app.application.static_pair_validation import StaticPairValidationGate, APPROVED_SHA256
gate = StaticPairValidationGate(scope_path=r'D:\codex_workspace\2026-10-01\task-17\pair-manifest.json',
                                expected_sha256=APPROVED_SHA256, case_id='Q01')
gate.register_disabled_scope(gate.scope_path, APPROVED_SHA256)
```

Registration requires the canonical ledger disabled with no pending attempts and
never enables calls. Independently reviewed live execution must explicitly enable
the global ledger and enter `gate.execution_scope()`; that context disables the
batch on exit. This implementation task never does either operation on the real
ledger. No standalone live runner or credential loading is added.

`DeepSeekGateway.last_receipt` preserves raw response content, provider finish
reason and usage/cache fields, actual response model (UNKNOWN if unavailable),
wall time, prompt/wire hashes, status and retry_count0. For this gate an optional
receipt_sink receives the receipt after reconciliation, including failures.
Nonstreaming TTFT remains None. Return-string callers continue to work.
Local num_ctx4096 is provenance only; the wire has one original user message,
thinking disabled and no seed/num_ctx. HTTPS endpoint is fixed, redirects denied,
proxy environment disabled. This is mock safety evidence, not answer-quality acceptance.

Rollback: restore the adapter's exact pre-task dirty bytes from task-17/dirty-backup;
remove only static_pair_validation.py, test_static_pair_validation.py and this
document. Never git-reset the dirty worktree or restore/reset the real ledger.
