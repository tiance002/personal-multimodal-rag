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

The installer creates `/srv/rag-eval/{app,configs,datasets,experiments,reports,logs,backups,incoming,releases}`, installs `rag-eval.service`, and checks `http://127.0.0.1:8787/healthz` against the committed code SHA. It creates an online SQLite backup before the additive evaluation-registry v2 migration. Existing experiment data is preserved. It does not change SSH, Nginx, firewall, or Aliyun security-group settings.

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

## 可信评测 v2：实际 A/B 使用

先提交源码并保证工作树干净。使用仓库外运行时 JSON（字段 `database_url`）提供专用本地 PostgreSQL 管理连接；禁止提交连接文件。运行器仅允许 loopback PostgreSQL 与 `rag_eval_trust_` 数据库前缀，每组新建库、不删除旧实验库。

```powershell
python -m eval_center.runner --connection-file $evalConnectionFile
```

固定 `evaluations/trust_v1` 含33个来源核查问题。运行器真实摄取、切分、Embedding、检索及8题Qwen问答，输出 `var/trust-acceptance/<run-token>/A|B/`。完整问题/答案/来源只保留本地，只有严格 v2 `bundle.json` 可上传。当前默认配置为A1200/120、B700/70、top_k5/candidate_k32/RRF60/context8000字符；从实际组件复核配置，错误声明或脏源码直接 INVALID。

部署脚本要求 clean Git HEAD，带上CODE_SHA、全部服务依赖并重启。可用 `-PythonExecutable $pythonPath` 指定真实Python路径；上传脚本同样支持。部署SHA必须与实验manifest一致。`healthz.git_sha` 应匹配实际执行提交。默认Dashboard只列verified；诊断模式显示旧记录或已作废实验并禁用选择。四阶段排名与稳定来源覆盖、actual/estimated/unavailable Tokens、有效样本数量分别核对，不能用prepared上下文声称已送入模型。

### 数据恢复

安装器备份目录含原SQLite、service、runtime.env与previous-release。恢复前停止服务、保留当前DB及WAL。使用sqlite3 backup API把选定备份恢复到专用数据目录，恢复服务单位/配置和旧release symlink，校正rag-eval所有权及0600权限，再daemon-reload/restart并检查health SHA、实验ID/digest与integrity_check。不要在服务写入时仅覆盖主SQLite文件。恢复具体备份属于单独操作；本轮已执行当前数据备份副本的6行摘要与完整性验证，未冒称全实例灾难恢复。

实际部署/真实A/B及云端篡改拒绝证据见 `docs/reviews/rag-evaluation-final-audit.md`。语义Judge默认NOT_EVALUATED，当前真实RAG质量partial，评测平台PASS不代表产品发布验收。
