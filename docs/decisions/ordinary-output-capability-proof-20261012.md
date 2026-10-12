# Ordinary output-capability evidence (G1)

This change covers ordinary `chat_cheap` / `chat_expensive` text capacities at
1024, 2048 and 4096 requested output tokens. Frozen V6/base 512 contracts and
caption/vision protocols remain separate. No hosted tokenizer equivalence,
model-weight identity, invoice settlement or long-answer quality is inferred
from a synthetic output-cap reach probe.

## Primary documentation

- [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)
  specifies `deepseek-flash` and a `max_tokens` range of 1 through 393216,
  constrained by the combined input/output context window.
- [SiliconFlow Chat Completions](https://docs.siliconflow.cn/docs/api/chat-completions-post)
  defines `max_tokens` as an integer maximum of generated tokens, excluding
  chain of thought, and advises room for input/system context overhead. This
  alone does not establish exact Xing hosted cap reach or its tokenizer.

The official pages were read on 2026-10-12. They justify bounded probes; they
do not replace actual provider-returned evidence that reaches the requested cap.

## Local and hosted evidence boundaries

Ordinary counters list 512/1024/2048/4096 locally reviewed output reservations.
Their complete input framing, exact final request bytes, output reservation and
ordinary identity are measured without widening frozen base validation.
Xing input admission reserves room for the largest locally reviewed cap.
The ordinary DeepSeek counter labels its renderer identity separately.

`OutputCapabilityEvidence` is immutable and includes provider, requested model,
protocol, canonical endpoint, output cap, exact serialized synthetic request,
serialized provider-returned response, run and attempt identities. Validation
requires matching provider-returned model, positive bounded usage, consistent
total usage, nonempty output, `finish_reason=length` and `completion_tokens`
exactly equal to the requested output cap. A short `OK` or other early `stop`
does not prove that the provider honored the maximum; a silently clamped 512
response cannot prove a requested 4096 cap. Reaching the cap is not a successful
RAG answer. Binding remeasures the exact
request bytes using the real local counter and rejects usage outside its bound.

Proof schema version 3 and the `output-reach/v3` protocol identify this strict
predicate with the natural tutorial prompt. Schema versions 1/2 and historical
short/repetition prompts cannot be promoted or reinterpreted.
The deployment pins a public JSON receipt file with
`RAG_OUTPUT_CAPABILITY_FILE` and `RAG_OUTPUT_CAPABILITY_SHA256`. API and worker
load the same proof through `configured_service_capacity`; invalid evidence
fails closed. `verified_output_caps` and `observed_output_caps` list only caps
actually reached in receipts. `supported_output_caps` additionally admits
locally reviewed lower integer caps at or below a reached ceiling. This is an
explicit inference from the current vendor `max_tokens` upper-bound contract;
the lower caps are not claimed to have been individually reached. The basis
records the primary documentation URL, review date, observed ceiling and
`lower_caps_individually_observed=false`. Thus a reached 2048 supports 1024/2048
but not 4096; a reached 4096 supports all three ordinary tiers. Readiness
requires support for all three tiers. Each real request still reserves its
actual selected cap, remeasures final bytes and fences reported usage that
exceeds its reservation. No frozen V6/base contract changes.

The CLI-only synthetic admission context unlocks one exact cap for one active
run/attempt, verifies lifecycle ownership and limits the payload to the fixed
version-3 public prompt requesting a twelve-section educational tutorial about
a personal local document library, aiming for at least 6000 English words.
The request output cap still bounds generation to at most 4096 tokens. It
creates no budget, scope or transport authority;
the owner-approved probe runner still durably enforces the current approved
batch and invokes ordinary egress, lifecycle and ServiceUsageGuard paths.

## Verification ownership and status

Offline tests exercise local framing and frozen-contract isolation, receipt
validation/hash tampering, scoped single-use admission and explicit readiness.
Real Xing offline tests use the pinned official local resources. DeepSeek's
real recipe test requires Linux plus `DEEPSEEK_TEST_TOKENIZER`; a Windows skip
must not be described as passing real DeepSeek recipe verification.

Provider probes, actual ledger inspection, evidence promotion, deployment and
live RAG acceptance belong to the root execution report. This source change
does not itself assert any provider probe or milestone acceptance.

The root reported the first actual Xing 1024 probe failed after about 90
seconds with `ProviderUnavailable`; no response or actual usage was observed.
Run `046c3327-5dd9-4d44-a6e6-876897da41ab` retains its 39 input + 1024 output
reservation in UNKNOWN. That round stopped with no resend. No hosted
output-cap evidence has been bound from this failure.
That original short-prompt receipt is historical and immutable. Only a future
explicitly approved new run may execute the changed probe; source verification
does not authorize any additional paid call.

The owner subsequently approved six new probes (seven total including the
first UNKNOWN), retaining the 75000-token limit. Root reported the second
actual Xing 1024 v2 repetition prompt returned `stop`, with known usage of
45 input + 131 completion tokens, and its token reservation settled. It is
`NOT_PROVEN` for cap reach; it is neither proof of an unsupported cap nor
UNKNOWN usage. Invoice/cost confirmation remains separate. Both historical
receipts stay immutable. The v3 prompt change does not itself authorize a
call, reset either receipt or release the original UNKNOWN occupancy.

Windows offline command (exit 0):

```powershell
$env:XING_TEST_ASSETS=(Join-Path (Get-Location) 'var/model-assets/xing4.0/baae3c3e813cad5f888f1f485cfff659c89076c5'); .\.venv\Scripts\python.exe -m pytest -q backend/tests/test_output_capability.py backend/tests/test_siliconflow_xing_capacity.py backend/tests/test_v6_text_capacity.py backend/tests/test_service_configuration.py backend/tests/test_runtime_readiness.py backend/tests/test_config.py --tb=short -p no:cacheprovider
```

Observed result for the initial local implementation: 137 passed, 3 skipped, 7 warnings. The three skips are the
explicit Linux DeepSeek recipe cases; all actual local Xing cases ran.

Additional affected ordinary budget / ledger / frozen cap command (exit 0):

```powershell
.\.venv\Scripts\python.exe -m pytest -q backend/tests/test_ordinary_output_budget.py backend/tests/test_service_configuration.py backend/tests/test_service_usage.py backend/tests/test_v6_output_cap.py --tb=short -p no:cacheprovider
```

Observed result: 62 passed, 7 warnings. The exact-cap receipt binding test was
rerun after adding independent local usage-bound verification: exit 0,
1 passed, 74 deselected, 7 warnings (`test_siliconflow_xing_capacity.py -k
ordinary_service_requires_exact_pinned_cap_receipt`). Simulated receipts in
these tests are explicitly offline fixtures, not real provider proof.

After strengthening to proof version 2, targeted command (exit 0):

```powershell
$env:XING_TEST_ASSETS=(Join-Path (Get-Location) 'var/model-assets/xing4.0/baae3c3e813cad5f888f1f485cfff659c89076c5'); .\.venv\Scripts\python.exe -m pytest -q backend/tests/test_output_capability.py backend/tests/test_siliconflow_xing_capacity.py -k 'output_capability or ordinary_service_requires_exact_pinned_cap_receipt' --tb=short -p no:cacheprovider
```

Observed result: 25 passed, 74 deselected, 7 warnings. Includes explicit
requested-4096 / returned-512 silent-clamp rejection, short `OK` rejection for
all ordinary caps, early `stop` rejection even with cap-sized reported usage,
old schema/prompt rejection, positive simulated cap reach and real local Xing
receipt binding. Provider calls remain zero for these offline tests.

After adding v3 tutorial probes and explicitly inferred lower-tier support,
targeted command (exit 0):

```powershell
$env:XING_TEST_ASSETS=(Join-Path (Get-Location) 'var/model-assets/xing4.0/baae3c3e813cad5f888f1f485cfff659c89076c5'); .\.venv\Scripts\python.exe -m pytest -q backend/tests/test_output_capability.py backend/tests/test_siliconflow_xing_capacity.py backend/tests/test_service_configuration.py backend/tests/test_ordinary_output_budget.py -k 'output_capability or ordinary_service_requires_exact_pinned_cap_receipt or ordinary_readiness or real_output_capacity or unproved_higher or real_local_xing' --tb=short -p no:cacheprovider
```

Observed result: 38 passed, 100 deselected, 7 warnings. Both vendor contract
sources are checked; a reached 2048 admits 1024 without marking it observed
and keeps 4096 denied/NOT_READY. A reached 4096 supports all project tiers.
All strict length/usage, old prompt/schema, silent-clamp and early-stop
rejection checks remain present. These are offline simulated receipt tests
with real local Xing counting; they do not establish hosted cap reach.

Linux zero-provider smoke command (execution and report owned by root):

```sh
PYTHONPATH=/app python /app/scripts/smoke_ordinary_output_capacity.py --tokenizer /assets/deepseek-v41/tokenizer.json --report /reports/ordinary-output-local.json
```

Requires the already pinned `deepseek-recipe==0.1.1` native Linux x86_64
dependency and verified tokenizer. No installation, DB or provider calls occur.
