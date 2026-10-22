# S8.6.4 fixed prompt diagnostics

This package is offline preparation. New Provider calls, input tokens and output
tokens are zero. Model extraction and answer quality are `NOT_RUN`.

`python -m scripts.s8_6_4_prompt_diagnostics` validates and renders all pairs
without transport, credentials, grants, database connections or ledger writes.

`render_image_pair(package, case_id)` returns complete old and candidate request
bodies through the existing DeepSeek adapter. The original base64 image is read
from the immutable tracked P27 request; the package verifies its file, image and
body hashes. Only caption text changes. Model, image detail, temperature, output
limit and absence of a thinking parameter remain identical. The manifest points
to the original raw output and usage receipt. All such usage is historical.

`load_generation_pair(package, case_id)` returns saved complete old/candidate
bodies. `render_generation_pair` renders each through the existing Cheap and
Expensive adapters. The candidate appends a test-only preservation suffix to the
unchanged historical system prompt, including unchanged Prompt B. Source text,
order, user messages, historical English locale and options are fixed. A locale
change must be evaluated separately. The candidate does not change production
generation configuration.

Four cases have sufficient-input D diagnoses. MIRACL692002 is primary C with a
secondary D entity error: the Romer source did not reach generation. It is not a
sufficient-input answer-quality case. Character and duplicate counts describe
the fixed inputs; they do not establish that input length caused the errors.

`scoring_only.json` holds historical answers, review quotes and evaluation
dimensions. Neither request loader reads it. No question or Gold is added to
image requests, and no source text is supplemented in generation requests.

Future real comparisons require new authorization and fresh request identities
through the existing Registry, SendGate and LocalExecution. Historical request
IDs, CLOSED authorizations and UNKNOWN allocations cannot be reused. Do not run
`--prepare` against this existing package: creation refuses to overwrite evidence.
