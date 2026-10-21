# P1 immutable physical model store

## Goal and changes

`storage/models.py::PostgresModelStore` persists the models behind configurable
roles independently of the current five-role configuration snapshot. Changing
that snapshot does not delete or reinterpret model IDs already pinned by KBs
or memory embedding rows. Runtime wiring and model selection remain parent-owned.
The store never updates KB model bindings or memory embedding metadata.

The constructor takes a fresh verified connection lease factory, trusted fixed
`LocalPrincipal`, injected `ModelCatalogue.validate(ConfiguredRole)` and injected
`SecretCipher`. Its public ABI is:

```python
await store.persist(configured_role)  # -> model_id, VARCHAR(64)
await store.resolve(model_id)       # -> ConfiguredRole; strict secret decrypt
await store.physical_key(model_id)  # -> SHA256 string, no credentials
await store.public(model_id)        # -> limited public identity projection
```

IDs include tenant/sole subject, role, provider, source model name, base URL,
source ModelType and all internal non-secret parameters in canonical JSON.
They are `mdl` plus 61 hexadecimal fingerprint characters (64 total). API key
and app secret are excluded. The complete untruncated identity hash is stored
and checked alongside the entire canonical non-secret parameter object. ID
collisions, physical metadata tampering, foreign owners, unavailable tenants
and deleted/non-active model rows fail closed. There is no deletion or row
replacement API.

Roles map to fixed source types: embedding→`Embedding`, rerank→`Rerank`,
cheap/expensive→`KnowledgeQA`, vlm→`VLLM`. Distinct roles have distinct persistent
IDs. `physical_key` omits role and owner but includes ModelType, provider/model/
base URL and all non-secret parameters, allowing compatible different model IDs
to be grouped by actual configuration. It includes internal `spec`, embedding
dimensions and other source parameter fields; it never groups just by a name.

`persist` validates trusted catalogue metadata, locks the model ID and active
tenant, then inserts once. An existing row must match every immutable identity
field. Only credential fields in its parameters are changed; all non-secret
parameters must compare identically before an update. Changing provider, model,
endpoint, role or internal parameters creates a new row and retains the old one.
Every method obtains a new physical identity-verified lease. The store performs
no model calls, permission grants, budget operations, configuration snapshot
updates, old index rewrites or automatic rebinding.

## Fixed-source persistence and secrets

Reference SHA: `3e8b0bfc80b845b2d4b2ed683994748741450a97`.

- `migrations/paradedb/00-init-db.sql:33–52`: exact `models` table/index block.
- `internal/types/model.go:211`: Model fields and source types.
- `internal/types/model.go:255`: `ModelParameters.Value` encrypts APIKey and
  AppSecret with configured AES key before JSON persistence; unset-key source
  behavior retains plaintext.
- `internal/types/model.go:273`: `Scan` unmarshals parameters and leniently
  decodes stored credentials, accepting legacy plaintext.
- `internal/application/repository/model.go:29`: `GetByID` selects tenant-owned
  or builtin rows; `Update` scopes by ID/tenant. This sole-owner adapter uses an
  additional subject predicate and does not grant shared builtin access.

`008_models.sql` copies the source DDL verbatim, then adds local subject, role,
full identity hash and physical hash metadata. Attribution, source block lines
and hash are in `source_manifest_models.json`. It does not modify 006/007 or the
parent's migration runner. Explicit `models_migrate.py::apply_model_schema`
verifies physical identity and generated SHA, takes advisory migration lock 8
and applies/rechecks the immutable 008 migration record. Apply after 001/003.

API key is stored in source `parameters.api_key`; source app secret, when
provided internally, is independently stored in `parameters.app_secret`.
Both use injected `cipher.encrypt`. Runtime `resolve` calls `decrypt(strict=True)`
for both and refuses incorrect/missing AES keys. `public` never decrypts or
returns parameter objects or credentials. Its `key_configured` reports stored
key presence, not a tested provider credential. `ConfiguredRole` excludes keys
and parameters from repr. Parameters are copied before encryption; caller
objects are not mutated.

`SecretCipher(None)` remains only the explicit source unset-key compatibility
behavior. The formal runtime is expected to inject its generated private key;
these tests do not provision a production key. No encryption dependency was
installed into the old/shared venv.

## Commands and results

Working directory:
`C:\Users\22088\.codex\worktrees\weknora-python-port\RAG quention`.

SQL doubles command:

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest tests/weknora_port/test_model_store.py -q --tb=short
```

Exit **0**, **9 passed in 0.41s**. Fixture cipher results are explicitly simulated,
not AES or PostgreSQL evidence. The unset-key compatibility case uses the actual
`SecretCipher(None)` implementation.

The isolated P2 interpreter has real `cryptography==46.0.3`, Python 3.13.0,
and pytest. Its standalone psycopg import initially failed (`ModuleNotFoundError`,
exit 1). A read-only import check after appending the existing shared venv's
site-packages succeeded (`psycopg==3.2.10`, exit 0). No install, package update
or venv modification was performed. The actual PG command therefore supplied
command-local PYTHONPATH to reuse the already installed psycopg:

```powershell
$env:PYTHONPATH='C:\Users\22088\.codex\worktrees\weknora-python-port\RAG quention;E:\RAG quention\.venv\Lib\site-packages'
& '.\var\weknora-port\p2-venv\Scripts\python.exe' -m pytest --noconftest tests/weknora_port/test_model_store.py tests/weknora_port/test_model_store_pg.py -q --tb=short
```

The explicit approved fixture DSN was supplied in `WEKNORA_PORT_TEST_DSN`;
credentials are excluded from this report. The DSN allowlist is checked before
connecting, then physical identity is verified before writes. Tests use real
AES-256-GCM with a synthetic fixture key and actual independent PG connections.

**PASS: 9 SQL doubles + 3 actual PG tests**, total 12. Final exit **0**, output
**12 passed in 1.06s**, recorded in `P1_MODEL_STORE_EVIDENCE.json`.
`git diff --check` exited **0** with no whitespace errors (Git emitted LF→CRLF
notices for shared files outside this worker's ownership). Actual checks prove:

1. A KB keeps its old embedding model ID after another model is persisted;
   the old model's exact dimensions/name remain resolvable. Credential update
   preserves its model ID. Both source secrets are encrypted in the actual
   stored JSONB, and public projection omits them.
2. Concurrent separate connections persisting one identity produce one row and
   the same ID. Cheap/expensive role IDs differ but share a physical key for
   the same physical configuration.
3. Wrong-key strict loading fails; limited public metadata remains readable;
   a foreign subject cannot resolve the row; the original fixture key decrypts
   the stored synthetic API key correctly.

Actual target: `weknora_python_port_20261013`, localhost port 56413,
system identifier `7696038373595934758`. Old identifiers
`7691227493754040358` and `7689510505384681511` are rejected.
Applied 008 SHA256:
`e3d22edc4a5d53415835be33ea2620d955248c038747b62ad8db5e5477427691`.
Synthetic owner/model/KB rows remain for review; evidence saves IDs and safe
readback assertions without plaintext or ciphertext credentials.

## Handoff and limitations

Owned files are ready for the parent's coordinated commit. No worker commit,
tag or milestone acceptance claim. Model/provider requests **0**, old DB writes
**0**. Formal runtime integration, real provider credentials, memory embedding
backfill/model calls and full Goal acceptance are **NOT RUN**. This adapter
preserves memory's old ID by retaining its catalog row; the test proves a KB pin,
not a live memory embedding invocation. 未实现的命令不得报告通过。

Next minimal parent step: persist selected role configurations, bind new KBs to
the returned IDs, resolve existing KB/memory pins by their stored IDs, and use
`physical_key` for grouping without consulting the current role snapshot as the
identity of an older index. Existing pins must not be silently reconfigured.
