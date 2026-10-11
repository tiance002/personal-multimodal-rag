"""Fail-closed read-only attachment checks for prebuilt public indexes."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url


CLONE_CONTAINER_NAME = "rag-eval-trust0928-db-clone-20260929t091523z"
ORIGINAL_CONTAINER_NAME = "rag-eval-trust0928-db"
CLONE_VOLUME_NAME = "rag-eval-trust0928-db-clone-20260929t091523z"
CLONE_PORT = 25437
ALLOWED_DATABASES = frozenset({
    "rag_eval_trust_6f0c61007b_scifact",
    "rag_eval_trust_cffac0cd6a_miracl_zh",
    "rag_eval_trust_442ee43dd3_longbench_",
})
REQUIRED_INDEX_IDENTITY_FIELDS = (
    "dataset", "dataset_version", "split", "run_id", "run_manifest_sha256",
    "corpus_sha256", "development_cases_sha256", "prepared_manifest_sha256",
    "parser_version", "chunker_version", "chunk_size", "chunk_overlap",
    "embedding_model", "embedding_digest", "embedding_profile", "embedding_dimension",
    "schema_revision", "ready_state", "index_fingerprint", "index_counts",
    "source_binding_sha256", "index_code_sha256", "baseline_git_sha",
    "container_id", "container_name", "volume_name", "database", "port",
    "database_read_only",
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")


@dataclass(frozen=True)
class ECSReadOnlyDatabaseProfile:
    """Explicit owner evidence for the independent ECS DB; no inferred IDs."""
    port: int
    system_identifier: str
    database_oid: int
    schema_revision: str
    container_id: str | None
    volume_name: str | None
    evidence_sha256: str
    database: str = 'rag_p7_r1'
    container_name: str | None = 'rag-eval'
    host: str = '127.0.0.1'
    profile_id: str = 'OWNER_V6_ECS_INDEPENDENT'
    deployment_kind: str = 'DOCKER_CONTAINER'
    data_directory: str | None = None
    service_name: str | None = None
    postgres_executable: str | None = None

    def validate(self):
        if (type(self) is not ECSReadOnlyDatabaseProfile
                or self.profile_id != 'OWNER_V6_ECS_INDEPENDENT'
                or self.database != 'rag_p7_r1'
                or self.host != '127.0.0.1' or type(self.port) is not int
                or not 1 <= self.port <= 65535
                or type(self.system_identifier) is not str
                or not re.fullmatch(r'[1-9][0-9]{0,19}', self.system_identifier)
                or int(self.system_identifier) >= 2**64
                or type(self.database_oid) is not int or not 0 < self.database_oid < 2**32
                or type(self.schema_revision) is not str or not self.schema_revision
                or not _SHA256.fullmatch(str(self.evidence_sha256))):
            raise IndexReuseRejected('ecs_database_profile')
        if self.deployment_kind == 'DOCKER_CONTAINER':
            if (self.container_name != 'rag-eval' or not _SHA256.fullmatch(str(self.container_id))
                    or type(self.volume_name) is not str or not self.volume_name
                    or any(value is not None for value in (self.data_directory,self.service_name,self.postgres_executable))):
                raise IndexReuseRejected('ecs_container_identity')
        elif self.deployment_kind == 'HOST_NATIVE_POSTGRESQL':
            if (any(value is not None for value in (self.container_id,self.container_name,self.volume_name))
                    or not _absolute_postgres_path(self.data_directory)
                    or not _absolute_postgres_path(self.postgres_executable)
                    or PurePosixPath(self.postgres_executable).name != 'postgres'
                    or type(self.service_name) is not str
                    or not re.fullmatch(r'postgresql@[A-Za-z0-9_.-]+\.service',self.service_name)):
                raise IndexReuseRejected('ecs_host_instance_identity')
        else:
            raise IndexReuseRejected('deployment_kind')

    @classmethod
    def from_host_metadata_receipt(cls, evidence_path: Path, *, expected_sha256: str):
        """Load the exact approved external receipt; creates no Scope/index/ready flag."""
        try:
            raw=Path(evidence_path).read_bytes()
            if not _SHA256.fullmatch(expected_sha256) or hashlib.sha256(raw).hexdigest()!=expected_sha256:
                raise ValueError('metadata_hash')
            receipt=json.loads(raw)
            observed=json.loads(receipt['stdout'])
            service=observed['native_postgres_service'];process=observed['native_postgres_process']
            service_state=dict(line.split('=',1) for line in service['stdout'].splitlines())
            service_name=service['command'][2]
            if (receipt['exit_code']!=0 or observed['exit_code']!=0
                    or observed['deployment_kind']!='HOST_NATIVE_POSTGRESQL'
                    or any(observed[field] is not None for field in ('container_id','container_name','volume_name'))
                    or observed['docker_identity_status']!='NOT_APPLICABLE_HOST_NATIVE_POSTGRESQL'
                    or service['exit_code']!=0 or service['command'][:2]!=['systemctl','show']
                    or service_state['ActiveState']!='active' or int(service_state['MainPID'])<=0
                    or process['pid']!=int(service_state['MainPID'])
                    or not any(line.split(':',2)[-1].endswith('/'+service_name)
                               for line in process['cgroup'].splitlines())
                    or any(observed[field]!=0 for field in ('model_calls','ledger_operations','database_writes'))):
                raise ValueError('metadata_instance')
            meta=observed['target_database_metadata']
            if meta['transaction_read_only']!='on':raise ValueError('metadata_transaction')
            profile=cls(port=meta['port'],system_identifier=meta['system_identifier'],
                database_oid=meta['database_oid'],schema_revision=meta['schema_revision'],
                container_id=None,container_name=None,volume_name=None,evidence_sha256=expected_sha256,
                database=meta['database'],deployment_kind='HOST_NATIVE_POSTGRESQL',
                data_directory=meta['data_directory'],service_name=service_name,
                postgres_executable=process['executable'])
            profile.validate()
            return profile
        except (OSError,AttributeError,IndexError,KeyError,TypeError,ValueError) as exc:
            raise IndexReuseRejected('ecs_metadata_evidence') from exc


def _absolute_postgres_path(value):
    return (type(value) is str and bool(value) and PurePosixPath(value).is_absolute()
            and '..' not in PurePosixPath(value).parts)


class IndexReuseRejected(RuntimeError):
    """The prebuilt index cannot be proven identical and read-only."""

    def __init__(self, field: str) -> None:
        self.code = "INDEX_REUSE_REJECTED"
        self.field = field
        super().__init__(f"INDEX_REUSE_REJECTED:{field}")


def read_only_database_url(admin_url: str | URL, database: str, *,
                           database_profile: ECSReadOnlyDatabaseProfile | None = None) -> str:
    """Derive a URL pinned to the validated clone loopback port and read-only txns."""
    try:
        url = make_url(admin_url)
    except Exception as exc:
        raise IndexReuseRejected("database_url") from exc
    if database_profile is not None:
        database_profile.validate()
    port = database_profile.port if database_profile is not None else CLONE_PORT
    if (url.get_backend_name() != "postgresql" or url.host != "127.0.0.1"
            or url.port != port or url.database != "postgres"):
        raise IndexReuseRejected("clone_endpoint")
    if (database != database_profile.database if database_profile is not None
            else database not in ALLOWED_DATABASES):
        raise IndexReuseRejected("database")
    # Never forward driver parameters from the supplied DSN: psycopg query
    # values such as host/hostaddr/port/dbname can override the validated URL.
    query = {
        "options": "-c default_transaction_read_only=on",
        "application_name": "rag-public-bench-readonly",
    }
    return url.set(database=database, query=query).render_as_string(hide_password=False)


def require_read_only_connection(connection: Any, expected_database: str, *,
                                 database_profile: ECSReadOnlyDatabaseProfile | None = None) -> None:
    if database_profile is not None:
        database_profile.validate()
        if expected_database != database_profile.database:
            raise IndexReuseRejected('database')
    row = connection.execute(text(
        "SELECT current_database(), current_setting('transaction_read_only')"
    )).one()
    if row[0] != expected_database:
        raise IndexReuseRejected("connected_database")
    if str(row[1]).lower() not in {"on", "true", "1"}:
        raise IndexReuseRejected("database_read_only")
    if database_profile is not None:
        host_native=database_profile.deployment_kind=='HOST_NATIVE_POSTGRESQL'
        fields=('system_identifier','database_oid','schema_revision')
        extra_sql=''
        if host_native:
            fields+=('data_directory','port')
            extra_sql=", current_setting('data_directory'), current_setting('port')::integer"
        observed = connection.execute(text(
            "SELECT (pg_control_system()).system_identifier::text, "
            "(SELECT oid FROM pg_database WHERE datname=current_database()), "
            "(SELECT version_num FROM alembic_version)"+extra_sql
        )).one()
        if len(observed)!=len(fields):raise IndexReuseRejected('connected_instance_fields')
        for field, value in zip(fields, observed):
            if value != getattr(database_profile, field):
                raise IndexReuseRejected(field)


def validate_index_identity(expected: dict[str, Any], actual: dict[str, Any], *,
                            database_profile: ECSReadOnlyDatabaseProfile | None = None,
                            scope=None) -> dict[str, Any]:
    fields = REQUIRED_INDEX_IDENTITY_FIELDS
    if database_profile is not None:
        database_profile.validate()
        fields += ('system_identifier', 'database_oid', 'scope')
        if database_profile.deployment_kind=='HOST_NATIVE_POSTGRESQL':
            docker_fields=('container_id','container_name','volume_name')
            if any(expected.get(f) is not None or actual.get(f) is not None for f in docker_fields):
                raise IndexReuseRejected('docker_identity_not_applicable')
            fields=tuple(f for f in fields if f not in docker_fields)
            fields+=('deployment_kind','data_directory','service_name','postgres_executable')
    for field in fields:
        if field not in expected or expected[field] is None or field not in actual or actual[field] is None:
            raise IndexReuseRejected(field)
        if expected[field] != actual[field]:
            raise IndexReuseRejected(field)
    if expected["split"] != "development" or actual["split"] != "development":
        raise IndexReuseRejected("split")
    if expected["ready_state"] is not True or actual["ready_state"] is not True:
        raise IndexReuseRejected("ready_state")
    if expected["database_read_only"] is not True or actual["database_read_only"] is not True:
        raise IndexReuseRejected("database_read_only")
    if database_profile is None:
        if expected["port"] != CLONE_PORT or actual["port"] != CLONE_PORT:
            raise IndexReuseRejected("port")
    else:
        profile_fields=('database','port','system_identifier','database_oid','schema_revision')
        profile_fields+=(('deployment_kind','data_directory','service_name','postgres_executable')
            if database_profile.deployment_kind=='HOST_NATIVE_POSTGRESQL'
            else ('container_id','container_name','volume_name'))
        for field in profile_fields:
            if expected[field] != getattr(database_profile, field):
                raise IndexReuseRejected(field)
        from backend.app.domain.scope import Scope
        if (type(scope) is not Scope or not scope.knowledge_base_ids
                or actual['scope'] != {'kb': sorted(scope.knowledge_base_ids),
                                       'documents': sorted(scope.document_ids)}):
            raise IndexReuseRejected('scope')
    for field in ("run_manifest_sha256", "corpus_sha256", "development_cases_sha256",
                  "prepared_manifest_sha256", "embedding_digest", "index_fingerprint",
                  "source_binding_sha256", "index_code_sha256"):
        if not _SHA256.fullmatch(str(actual[field])):
            raise IndexReuseRejected(field)
    if not _GIT_SHA.fullmatch(str(actual["baseline_git_sha"])):
        raise IndexReuseRejected("baseline_git_sha")
    counts = actual["index_counts"]
    if (not isinstance(counts, dict) or set(counts) != {"documents", "chunks", "embeddings"}
            or any(type(value) is not int or value <= 0 for value in counts.values())
            or counts["chunks"] != counts["embeddings"]):
        raise IndexReuseRejected("index_counts")
    identity = expected if database_profile is None else {
        'index': expected, 'database_profile': asdict(database_profile)}
    serialized = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                            allow_nan=False).encode("utf-8")
    return {
        "status": "VERIFIED_READ_ONLY_REUSE",
        "identity_sha256": hashlib.sha256(serialized).hexdigest(),
        "verified_fields": sorted(fields),
    }


def validate_clone_runtime(runtime: dict[str, Any]) -> dict[str, Any]:
    clone = runtime.get("clone", {})
    original = runtime.get("original", {})
    if clone.get("name") != CLONE_CONTAINER_NAME or clone.get("state") != "running":
        raise IndexReuseRejected("clone_container")
    if original.get("name") != ORIGINAL_CONTAINER_NAME or original.get("state") != "exited":
        raise IndexReuseRejected("original_container")
    clone_id = str(clone.get("id", ""))
    if not _SHA256.fullmatch(clone_id):
        raise IndexReuseRejected("clone_container_id")
    if (clone.get("volume") != CLONE_VOLUME_NAME
            or clone.get("volume_destination") != "/var/lib/postgresql/data"
            or clone.get("volume") == original.get("volume")):
        raise IndexReuseRejected("clone_volume")
    if clone.get("host_ip") != "127.0.0.1" or clone.get("host_port") != CLONE_PORT:
        raise IndexReuseRejected("clone_port_binding")
    return {
        "clone_container_id": clone_id,
        "clone_container": CLONE_CONTAINER_NAME,
        "clone_volume": CLONE_VOLUME_NAME,
        "original_container_id": original.get("id"),
        "original_stopped": True,
        "database_port": CLONE_PORT,
        "clone_volume_mount_read_write": bool(clone.get("volume_read_write")),
    }


def inspect_known_containers() -> dict[str, Any]:
    def inspect(name: str) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                ["docker", "inspect", "--type", "container", name],
                check=True, capture_output=True, text=True, timeout=15,
            )
            records = json.loads(completed.stdout)
        except Exception as exc:
            raise IndexReuseRejected(f"docker_inspect_{name}") from exc
        if not isinstance(records, list) or len(records) != 1:
            raise IndexReuseRejected(f"docker_inspect_{name}")
        record = records[0]
        mounts = [mount for mount in record.get("Mounts", [])
                  if mount.get("Destination") == "/var/lib/postgresql/data"]
        if len(mounts) != 1 or mounts[0].get("Type") != "volume":
            raise IndexReuseRejected(f"docker_volume_{name}")
        bindings = []
        for item in record.get("NetworkSettings", {}).get("Ports", {}).get("5432/tcp") or []:
            bindings.append({"host_ip": item.get("HostIp"), "host_port": int(item.get("HostPort", "0"))})
        return {
            "id": record.get("Id"),
            "name": record.get("Name", "").lstrip("/"),
            "state": record.get("State", {}).get("Status"),
            "volume": mounts[0].get("Name"),
            "volume_destination": mounts[0].get("Destination"),
            "volume_read_write": mounts[0].get("RW"),
            "port_bindings": bindings,
        }

    clone = inspect(CLONE_CONTAINER_NAME)
    original = inspect(ORIGINAL_CONTAINER_NAME)
    clone_bindings = clone.pop("port_bindings")
    original.pop("port_bindings")
    if len(clone_bindings) != 1:
        raise IndexReuseRejected("clone_port_binding")
    clone.update(clone_bindings[0])
    return {"clone": clone, "original": original}


def index_code_identity(repository_root: Path, baseline_git_sha: str) -> dict[str, str]:
    fixed_paths = {
        "backend/app/domain/chunking.py",
        "backend/app/adapters/parsers/__init__.py",
        "backend/app/workers/ingestion.py",
        "backend/app/adapters/postgres/knowledge_repository.py",
        "backend/app/adapters/postgres/schema.py",
        "backend/app/adapters/storage.py",
        "backend/app/domain/models.py",
    }
    try:
        migrations = subprocess.run(
            ["git", "-C", str(repository_root), "ls-tree", "-r", "--name-only", baseline_git_sha,
             "--", "alembic/versions"], check=True, capture_output=True, text=True, timeout=15,
        ).stdout.splitlines()
        paths = sorted(fixed_paths | {path for path in migrations if path.endswith(".py")})
        baseline_rows = []
        current_rows = []
        for path in paths:
            baseline_bytes = subprocess.run(
                ["git", "-C", str(repository_root), "show", f"{baseline_git_sha}:{path}"],
                check=True, capture_output=True, timeout=15,
            ).stdout
            current_bytes = subprocess.run(
                ["git", "-C", str(repository_root), "show", f"HEAD:{path}"],
                check=True, capture_output=True, timeout=15,
            ).stdout
            baseline_rows.append((path, hashlib.sha256(baseline_bytes).hexdigest()))
            current_rows.append((path, hashlib.sha256(current_bytes).hexdigest()))
    except Exception as exc:
        raise IndexReuseRejected("index_code_identity") from exc

    def digest(rows: list[tuple[str, str]]) -> str:
        return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode("utf-8")).hexdigest()

    return {"baseline_index_code_sha256": digest(baseline_rows),
            "current_index_code_sha256": digest(current_rows)}


__all__ = [
    "ECSReadOnlyDatabaseProfile",
    "ALLOWED_DATABASES", "CLONE_CONTAINER_NAME", "CLONE_PORT", "CLONE_VOLUME_NAME",
    "IndexReuseRejected", "ORIGINAL_CONTAINER_NAME", "REQUIRED_INDEX_IDENTITY_FIELDS",
    "index_code_identity", "inspect_known_containers", "read_only_database_url",
    "require_read_only_connection", "validate_clone_runtime", "validate_index_identity",
]
