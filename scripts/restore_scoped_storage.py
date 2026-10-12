"""Restore missing CAS objects from a verified local counterpart, never overwrite.

Default is a read-only plan. This does not migrate a database, alter active
versions/indexes, invoke providers, restart services or read credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import uuid


def run(args, *, stdin=None):
    result = subprocess.run(args, input=stdin, text=True, encoding="utf-8", capture_output=True)
    if result.returncode:
        # Third-party stderr/SQL/environment can contain private material.
        raise RuntimeError("RESTORE_COMMAND_FAILED")
    return result.stdout.strip()


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


CHECK = r'''
import hashlib,json,pathlib,sys
data=json.load(sys.stdin); root=pathlib.Path(data['root']).resolve(); rows=[]
for item in data['objects']:
    path=(root/item['key']).resolve()
    if not path.is_relative_to(root):raise RuntimeError('STORAGE_PATH_INVALID')
    exists=path.exists()
    sha=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    if exists and sha!=item['sha256']:raise RuntimeError('EXISTING_OBJECT_INTEGRITY_ERROR')
    rows.append(dict(key=item['key'],exists=exists,sha256=sha))
print(json.dumps(rows))
'''

INSTALL = r'''
import hashlib,json,os,pathlib,sys
data=json.load(sys.stdin);root=pathlib.Path(data['root']).resolve();stage=(root/'tmp'/data['stage']).resolve()
if stage.parent!=root/'tmp' or not data['stage'].startswith('nc-restore-'):raise RuntimeError('STAGE_PATH_INVALID')
rows=[]
for item in data['objects']:
    source=stage/item['sha256']; target=(root/item['key']).resolve()
    if not target.is_relative_to(root):raise RuntimeError('STORAGE_PATH_INVALID')
    if hashlib.sha256(source.read_bytes()).hexdigest()!=item['sha256']:raise RuntimeError('STAGED_OBJECT_INTEGRITY_ERROR')
# All staged objects are verified before publishing any immutable object.
for item in data['objects']:
    source=stage/item['sha256'];target=(root/item['key']).resolve()
    target.parent.mkdir(parents=True,exist_ok=True)
    created=True
    try:os.link(source,target)  # atomic, same filesystem, refuses overwrite
    except FileExistsError:created=False
    if hashlib.sha256(target.read_bytes()).hexdigest()!=item['sha256']:raise RuntimeError('EXISTING_OBJECT_INTEGRITY_ERROR')
    rows.append(dict(key=item['key'],created=created,sha256=item['sha256']))
for item in data['objects']:(stage/item['sha256']).unlink()
stage.rmdir()
print(json.dumps(rows))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-container", required=True)
    parser.add_argument("--db-container", required=True)
    parser.add_argument("--kb-id", required=True, type=uuid.UUID)
    parser.add_argument("--run-id", required=True, type=uuid.UUID)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-scope", type=uuid.UUID)
    args = parser.parse_args()
    report = {"status": "BLOCKED", "kb_scope": str(args.kb_id), "run_id": str(args.run_id),
              "provider_calls": 0, "database_writes": 0, "overwrites": 0}
    try:
        if args.execute and args.confirm_scope != args.kb_id:
            raise RuntimeError("SCOPE_CONFIRMATION_REQUIRED")
        info = json.loads(run(["docker", "inspect", args.api_container]))[0]
        db = json.loads(run(["docker", "inspect", args.db_container]))[0]
        report.update(api_container_id=info["Id"], api_image=info["Image"], db_container_id=db["Id"])
        env = dict(value.split("=", 1) for value in info["Config"]["Env"] if "=" in value)
        if args.target_root != env.get("RAG_STORAGE_ROOT") or args.target_root != "/data/storage":
            raise RuntimeError("STORAGE_IDENTITY_MISMATCH")
        db_url = env.get("RAG_DATABASE_URL", "")
        # This narrowly restores the G0-confirmed existing local stack only.
        if not db_url.endswith("@db:5432/rag"):
            raise RuntimeError("DATABASE_IDENTITY_MISMATCH")
        for container, service in ((info, "api"), (db, "db")):
            labels = container["Config"].get("Labels") or {}
            if (labels.get("com.docker.compose.project") != "raglocalfirst0930"
                    or labels.get("com.docker.compose.service") != service):
                raise RuntimeError("CONTAINER_IDENTITY_MISMATCH")
        sql = f"""BEGIN READ ONLY; SET LOCAL statement_timeout='5s';
SELECT json_build_object('knowledge_base_scope',knowledge_base_scope,'document_scope',document_scope)
FROM rag_runs WHERE id='{args.run_id}';
SELECT json_agg(json_build_object('document_id',d.id,'version_id',v.id,
 'source',v.storage_key,'source_sha256',v.source_sha256,
 'normalized',v.normalized_content_key,'normalized_sha256',v.normalized_content_sha256))
FROM documents d JOIN document_versions v ON v.id=d.active_version_id
WHERE d.knowledge_base_id='{args.kb_id}' AND d.deleted_at IS NULL; COMMIT;"""
        output = run(["docker", "exec", args.db_container, "psql", "-U", "rag", "-d", "rag", "-X", "-A", "-t", "-c", sql])
        values = [json.loads(line) for line in output.splitlines() if line.startswith(("{", "["))]
        if len(values) != 2 or values[0] != {"knowledge_base_scope": [str(args.kb_id)], "document_scope": []}:
            raise RuntimeError("SERVER_SCOPE_MISMATCH")
        docs = values[1]
        if not isinstance(docs, list) or not 1 <= len(docs) <= 32:
            raise RuntimeError("SCOPE_SIZE_INVALID")
        root = args.source_root.resolve(strict=True)
        objects = {}
        for doc in docs:
            for kind in ("source", "normalized"):
                key, sha = doc[kind], doc[kind + "_sha256"]
                if (not isinstance(sha, str) or not re.fullmatch("[0-9a-f]{64}", sha)
                        or key != f"objects/{sha[:2]}/{sha}"):
                    raise RuntimeError("CAS_IDENTITY_INVALID")
                path = (root / key).resolve(strict=True)
                if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 50 * 1024 * 1024:
                    raise RuntimeError("LOCAL_OBJECT_INVALID")
                if digest(path) != sha:
                    raise RuntimeError("LOCAL_OBJECT_INTEGRITY_ERROR")
                objects[key] = {"key": key, "sha256": sha, "source_path": str(path)}
        payload = {"root": args.target_root, "objects": list(objects.values())}
        before = json.loads(run(["docker", "exec", "-i", args.api_container, "python", "-B", "-c", CHECK], stdin=json.dumps(payload)))
        missing_keys = {row["key"] for row in before if not row["exists"]}
        missing = [row for row in payload["objects"] if row["key"] in missing_keys]
        report.update(documents=docs, source_root=str(root), target_root=args.target_root,
                      checked_objects=len(objects), before=before, missing_objects=len(missing))
        if args.execute and missing:
            stage = "nc-restore-" + uuid.uuid4().hex
            report["stage"] = stage
            payload.update(stage=stage, objects=missing)
            # Private source bytes stay on this computer, in the verified local
            # Docker daemon. No filenames, content or credentials reach models.
            run(["docker", "exec", args.api_container, "python", "-B", "-c",
                 "import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.mkdir(exist_ok=False)",
                 f"{args.target_root}/tmp/{stage}"])
            for row in missing:
                run(["docker", "cp", row["source_path"], f"{args.api_container}:{args.target_root}/tmp/{stage}/{row['sha256']}"])
            report["installed"] = json.loads(run(["docker", "exec", "-i", args.api_container, "python", "-B", "-c", INSTALL], stdin=json.dumps(payload)))
        after_payload = {"root": args.target_root, "objects": list(objects.values())}
        report["after"] = json.loads(run(["docker", "exec", "-i", args.api_container, "python", "-B", "-c", CHECK], stdin=json.dumps(after_payload)))
        report["status"] = "RESTORED" if args.execute else "READ_ONLY_PLAN"
        if args.execute and not all(row["exists"] for row in report["after"]):
            raise RuntimeError("RESTORE_INCOMPLETE")
    except Exception as error:
        report["status"] = "BLOCKED"
        report["reason"] = str(error) if isinstance(error, RuntimeError) else "RESTORE_CHECK_FAILED"
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report.get(key) for key in ("status", "reason", "checked_objects", "missing_objects", "overwrites")}, ensure_ascii=False))
    return 0 if report["status"] != "BLOCKED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
