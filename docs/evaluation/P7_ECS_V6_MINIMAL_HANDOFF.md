# P7 v6 ECS offline handoff plan

Status: **PREPARATION ONLY**. This plan and the bundle-preparation CLI do not
connect to ECS, transfer files, install packages, migrate a database, or start a
provider call. The existing hard deadline remains `2026-10-10T04:25:44Z`; do
not reset it when access is restored.

## Minimal allowlisted payload

The prepared payload is limited to:

- The three full public corpus JSONL files and their three adapter manifests.
- `source_manifest.json` and the frozen `cohort_dev24.jsonl`, which contains
  selected public qrels and answer references.
- The pinned Linux x86_64 `deepseek-recipe` wheel and tokenizer identified by
  `ecs-linux-resource-manifest.json`.

The selected cohort is 24 QIDs (8 per dataset). It carries the evaluation Gold
needed for this run. The complete development case files and all
`locked_holdout` case/Gold files remain local. Public corpus documents remain
necessary as the candidate pool, including background documents; the package
does not include extra holdout labels. The pinned wheel is a Linux
`manylinux_2_28_x86_64` artifact. It must not be installed on the Windows
consumer; the tokenizer and wheel have only been hash checked locally, not
executed.

The bundle target layout is rooted at `/opt/rag-p7-r1/inputs`. It contains no
repository source tree, `.git`, `.env`, credentials, virtual environment,
private corpora, database dump, holdout payload, or historical DeepSeek ledger.
The deployment database and role stay `rag_p7_r1` and `rag-eval`. The legacy
`/srv/rag-eval` service and `study_platform` database are outside this plan and
must not be read for business data, migrated, stopped, or overwritten.

## Required ECS sequence after identity is restored

1. Use only the already approved connection route. Verify instance ID
   `i-bp1flliql08bz8yftild` in `cn-hangzhou`, OS identity, free disk, and the
   isolated root/DB/role. Do not guess another address or credential.
2. Before deployment, inspect the isolated job's service/process/timer,
   per-run lock, and checkpoint state. If a matching job is active, attach to
   its checkpoint; never create a duplicate. Leave the legacy service alone.
3. Record the existing UTC start/deadline in the remote job record before any
   heavy action. The job record must carry the immutable run ID, cohort hash,
   source-manifest hash, evaluator source-snapshot hash, unique attempt key,
   and hard deadline. A reconnect resumes only saved checkpoints. An unknown
   provider outcome remains `UNKNOWN` and is never replayed automatically.
4. Transfer only the generated allowlisted archive using the existing approved
   route. Verify every file's byte count and SHA256 on ECS before extraction;
   reject path traversal, symlinks, unexpected members, and overwrite of
   existing files. Do not enable public ingress or create a paid resource.
5. Verify there is enough time before the fixed deadline to complete the
   bounded run. At the dispatch cutoff stop new work; at the hard deadline
   checkpoint and stop heavy work. No new embedding, index, model, or paid job
   may start after the deadline.

No ECS identity or active-job evidence exists yet. The previous SSH attempt was
rejected with `Permission denied (publickey)`; no alternate identity or
connection attempt is authorized here.

## Provider credential check-in

Before any live query, establish whether the target has an existing approved
credential-loading entry for the same provider and model profile. The check
must return only `CONFIGURED` or `MISSING` for expected variable/key names; it
must never print values, authorization headers, or environment contents. Do
not copy a local key, `.env`, shell history, credential file, or session data
to ECS. Any new credential installation or persistent configuration requires a
separate secure handoff. If the approved entry is absent or its identity cannot
be verified, live generation remains blocked.

Potentially sensitive fields that must stay out of the public bundle, reports,
command output, and Langfuse include provider API keys and authorization
headers; database URLs/passwords; Langfuse credentials; questions, prompts,
contexts, model answers, private documents, and user identifiers; provider
request/response payloads; and the private usage/cost ledger. Public QIDs,
selected public qrels/Gold, allowlisted corpus bytes, and hashes are the only
evaluation inputs in this bundle.

## Single-writer DeepSeek ledger handoff

The Windows E: ledger is the only authority now. ECS must not receive an
independent spend-cap ledger or begin DeepSeek work while that ledger cannot be
consulted. Preserve all **65,569 historical records** and every `UNKNOWN`,
reservation, and settlement state; do not reset, normalize away, or create a
fork. The historical ledger contents are not part of this bundle and have not
been uploaded.

Before a future DeepSeek handoff, use a separately approved secure transfer
and an exclusive-writer sequence:

1. Freeze the E: ledger writer and dispatch, wait for known in-flight work to
   settle, and preserve uncertain attempts as `UNKNOWN` with their reserved
   amount still counted.
2. Produce a read-only snapshot with record count, byte count, ledger schema
   version, high-water mark, exact source hash, and reconciled used/reserved/
   unknown totals. Keep the private ledger payload outside this evaluation ZIP.
3. Transfer that one snapshot through the approved private channel, verify its
   hash and totals on the target, and record the source snapshot hash as the
   ledger lineage. Disable the E: writer before enabling the ECS writer.
4. Enable ECS DeepSeek only after the unique-writer lease and the cumulative
   one-million-token cap, including used, reserved, and unknown billable
   tokens, are proven from the unchanged history. If exclusive ownership or
   totals cannot be proven, keep DeepSeek disabled.

Until that handoff is designed and verified, cheaper/no-cost model routes also
remain subject to their own provider grant, capacity, usage, and source-freeze
checks. Never change provider or price tier to turn a blocked result into a
pass.

## Offline preparation command

From the repository root:

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts\prepare_p7_ecs_bundle.py
```

This is read-only dry-run by default. `--write-manifest` writes only the
allowlisted upload manifest inside the v6 report directory. `--build-archive`
explicitly creates a ZIP from the same exact allowlist and its manifest; it
still does not upload or deploy. Both output operations refuse to overwrite an
existing file. Neither option can inspect or execute an ECS job.

The later deployment executor must separately verify current target identity,
the exclusive job lock, existing work/checkpoint state, the hard deadline, and
the official provider/runtime injection gate before it can schedule work. This
preparation script is not a replacement for Evaluation Center or its paired
cohort scheduler.
