# Private RAG Evaluation Center

## Boundary

The ECS stores only the v1 allowlisted experiment manifest, numeric metrics, hashed case IDs, pass/fail status, approved stage names, and machine error codes. Local reports, questions, answers, retrieved text, chunk/document IDs, model outputs, prompts, embeddings, traces, and provider settings stay on the evaluation machine.

The service runs as the unprivileged `rag-eval` account, binds to `127.0.0.1:8787`, accepts only HTTP GET requests, and uses `/srv/rag-eval/experiments/registry.sqlite3`. Do not add a public security-group rule. The existing RAG product, its PostgreSQL database, roles, backups, and files are outside this service's installer.

## Local package and validation

Add a stable lowercase `case_id` to every dataset row before packaging. The checked-in core, incremental, and quality datasets already have stable IDs. The existing four required dataset fields remain unchanged.

Create a manifest JSON beside the local evaluation report. Use the dataset version actually emitted by the report and a SHA-256 digest of the corpus being evaluated. Do not add a hostname, username, local path, URL, token, prompt, or free-text note.

Example manifest shape (replace the placeholders):

```json
{
  "experiment_id": "00000000-0000-4000-8000-000000000000",
  "git_sha": "0123456789abcdef0123456789abcdef01234567",
  "dataset_version": "quality-v1",
  "corpus_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "model_profile": "q0-deterministic",
  "embedding_profile": "none",
  "started_at": "2026-09-27T10:00:00+08:00",
  "ended_at": "2026-09-27T10:00:03+08:00",
  "environment": {
    "os_family": "windows",
    "python_version": "3.12.5",
    "architecture": "x86_64"
  }
}
```

Allowed configuration keys are `top_k`, `rrf_k`, `chunk_size`, `chunk_overlap`, `bm25_weight`, `vector_weight`, `retrieval_mode`, `query_mode`, `answer_mode`, `model_profile`, and `embedding_profile`. Unknown configuration keys are discarded during projection; the server validates the resulting allowlist and its hash.

From the repository root, create the sanitized bundle:

```powershell
python -m scripts.package_eval_bundle `
  --report var/reports/eval-rag-quality.json `
  --manifest var/local/manifest.json `
  --config var/local/config.json `
  --output var/outgoing/eval-bundle.json
python -m eval_center.cli validate var/outgoing/eval-bundle.json
```

The full report remains local. The packager prints only status and experiment ID; the validator prints only status, experiment ID, and digest. A missing/duplicate case ID, disallowed profile, unsupported metric, invalid timestamp, or dataset-version mismatch stops packaging.

## Initial deployment

The deployment helper uses the current user's existing `known_hosts` file with strict host-key checking. Verify the server's host key independently before running it. The helper accepts the private-key path only as a runtime argument and does not read, print, copy, or store key contents.

```powershell
./scripts/deploy_eval_center.ps1 `
  -HostName <approved-host> -User <ssh-user> -Port 22 `
  -IdentityFile <private-key-path> -Apply
```

The installer creates `/srv/rag-eval/{app,configs,datasets,experiments,reports,logs,backups,incoming,releases}`, installs `rag-eval.service`, and checks `http://127.0.0.1:8787/healthz`. It does not change SSH, Nginx, firewall, or Aliyun security-group settings and does not remove or migrate any existing application data.

## Tunnel and dashboard

On the local evaluation machine, keep this SSH process open:

```powershell
ssh -N -L 8787:127.0.0.1:8787 -p 22 -i <private-key-path> <ssh-user>@<approved-host>
```

Open `http://127.0.0.1:8787/`. The dashboard compares two to four runs and labels differences in dataset version, corpus hash, or evaluation mode. Its detail view shows opaque failed case IDs, stage, machine error code, and numeric metrics.

## Import sanitized results

Use the upload helper only after reviewing the generated bundle. Without `-Apply`, it validates locally and performs no SSH/SFTP transfer. With `-Apply`, it rejects duplicate JSON keys, writes a strict canonical allowlist snapshot, holds that exact snapshot read-only during SFTP, uploads it under a temporary name, atomically renames the bundle into the incoming directory, invokes the importer as `rag-eval`, and removes the staged file only after a successful or unchanged import.

```powershell
./scripts/upload_eval_bundle.ps1 `
  -Bundle var/outgoing/eval-bundle.json `
  -HostName <approved-host> -User <ssh-user> -Port 22 `
  -IdentityFile <private-key-path> -Apply
```

The HTTP API is read-only:

- `GET /healthz`
- `GET /api/v1/experiments?limit=50&offset=0`
- `GET /api/v1/experiments/<experiment-uuid>`
- `GET /api/v1/compare?ids=<uuid>,<uuid>`

Repeated ID plus identical content returns `unchanged`. Reusing an ID with changed content returns a conflict and keeps the original row.

## Service and SQLite operations

```sh
systemctl status rag-eval.service
systemctl restart rag-eval.service
curl --fail http://127.0.0.1:8787/healthz
ss -ltnp '( sport = :8787 )'
```

Before a manual SQLite backup, stop the service so the database and WAL are quiescent, copy the database to the root-owned backup area, then start and health-check the service again. Do not copy only the main database while the service is writing. Keep backups under `/srv/rag-eval/backups/` with mode `0600`; restore only after stopping the service and preserving the current database file.

No remote deployment, cleanup, firewall change, or private-data transfer is implied by this document. Those are separate owner-approved operations.
