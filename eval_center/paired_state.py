"""Fail-closed pairing and resumable attempt state for public three-arm runs.

This state is local run bookkeeping. Provider reservations and billing remain
owned by the existing provider guards and their authoritative ledgers.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from dataclasses import dataclass
from contextvars import ContextVar


ARMS = ("CHEAP_ONLY", "DYNAMIC", "EXPENSIVE_ONLY")
BATCH_ARMS = ("INDEXING", "SHARED_RETRIEVAL")
BATCH_PURPOSES = ("index_embedding", "query_embedding", "rerank")
CHAT_PURPOSES = ("answer", "understand", "rewrite", "expansion", "calibration")
DATASETS = ("scifact", "miracl-zh", "longbench-zh")
FULL_QA_DATASETS = (*DATASETS, "hotpotqa", "mmlongbench-doc")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_COMMON_IDENTITY = (
    "dataset", "dataset_version", "corpus_sha256", "pool_sha256",
    "qrels_sha256", "gold_sha256", "index_sha256", "prompt_sha256",
    "context_sha256",
)


class PairedContractError(ValueError):
    """A frozen input, arm comparison, or checkpoint violates the contract."""


@contextmanager
def _connect(path: Path):
    connection = sqlite3.connect(Path(path), timeout=10)
    try:
        yield connection
        if connection.in_transaction:
            connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class PairedDatasetSelection:
    dataset: str
    qids: tuple[str, ...]
    scope: Any
    index_identity_sha256: str


@dataclass(frozen=True)
class PairedCohortContract:
    """Versioned selection and immutable COHORT/RETRIEVAL/lease linkage.

    This is explicit owner input, never inferred from a short cohort. Missing
    contract keeps the historical full BATCH admission unchanged.
    """
    schema_version: int
    phase: str
    cohort_run_id: str
    retrieval_run_id: str
    cohort_sha256: str
    source_manifest_sha256: str
    index_manifest_sha256: str
    selections: tuple[PairedDatasetSelection, ...]
    execution_lease: Any

    def validate(self):
        from backend.app.domain.scope import Scope
        from backend.app.application.validation_usd_budget import V6ExecutionLease
        if (type(self) is not PairedCohortContract or type(self.schema_version) is not int
                or self.schema_version != 1 or self.phase not in {'SETUP', 'BATCH'}
                or type(self.execution_lease) is not V6ExecutionLease
                or any(type(v) is not str or not v for v in (self.cohort_run_id, self.retrieval_run_id))
                or self.cohort_run_id == self.retrieval_run_id
                or any(type(v) is not str or not _SHA256.fullmatch(v) for v in
                    (self.cohort_sha256, self.source_manifest_sha256, self.index_manifest_sha256))
                or type(self.selections) is not tuple or not self.selections):
            raise PairedContractError('paired_execution_contract_invalid')
        self.execution_lease.validate()
        qids, names, documents = [], [], 0
        for item in self.selections:
            if (type(item) is not PairedDatasetSelection or item.dataset not in FULL_QA_DATASETS
                    or type(item.qids) is not tuple or not item.qids
                    or any(type(q) is not str or not q for q in item.qids)
                    or type(item.scope) is not Scope or not item.scope.knowledge_base_ids
                    or not item.scope.document_ids
                    or any(type(v) is not str or not v for v in
                        (*item.scope.knowledge_base_ids, *item.scope.document_ids))
                    or type(item.index_identity_sha256) is not str
                    or not _SHA256.fullmatch(item.index_identity_sha256)):
                raise PairedContractError('paired_execution_selection_invalid')
            names.append(item.dataset); qids.extend(item.qids)
            documents += len(item.scope.document_ids)
        if len(set(names)) != len(names) or len(set(qids)) != len(qids):
            raise PairedContractError('paired_execution_selection_duplicate')
        if self.phase == 'SETUP':
            if not 1 <= len(qids) <= 5 or not 1 <= documents <= 5:
                raise PairedContractError('paired_setup_bounds_invalid')
        elif (set(names) not in (set(DATASETS), set(FULL_QA_DATASETS))
              or any(len(item.qids) != 8 for item in self.selections)):
            raise PairedContractError('cohort_sample_counts_invalid')

    @property
    def canonical_json(self):
        self.validate()
        payload = {k: getattr(self, k) for k in ('schema_version', 'phase', 'cohort_run_id',
            'retrieval_run_id', 'cohort_sha256', 'source_manifest_sha256', 'index_manifest_sha256')}
        payload['execution_lease'] = vars(self.execution_lease)
        payload['selections'] = [dict(dataset=i.dataset, qids=i.qids,
            scope=dict(kb=sorted(i.scope.knowledge_base_ids), docs=sorted(i.scope.document_ids)),
            index_identity_sha256=i.index_identity_sha256) for i in self.selections]
        return json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)

    @property
    def fingerprint(self):
        return hashlib.sha256(self.canonical_json.encode()).hexdigest()

    @property
    def lease_sha256(self):
        self.validate()
        return hashlib.sha256(json.dumps(vars(self.execution_lease), sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()

    def validate_cohort(self, rows):
        self.validate()
        actual = {i.dataset: tuple(r['qid'] for r in rows if r['dataset'] == i.dataset)
                  for i in self.selections}
        if (set(r['dataset'] for r in rows) != set(actual)
                or actual != {i.dataset: i.qids for i in self.selections}):
            raise PairedContractError('paired_execution_cohort_selection_mismatch')


def load_frozen_cohort(path: Path, *, expected_per_dataset: int = 8,
                       execution_contract: PairedCohortContract | None = None) -> list[dict[str, Any]]:
    """Load the frozen public dev cohort without touching holdout data."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PairedContractError(f"invalid cohort JSONL row {number}") from exc
        if not isinstance(row, dict):
            raise PairedContractError(f"invalid cohort JSONL row {number}")
        required = {"dataset", "dataset_version", "qid", "qrels", "question", "split"}
        allowed = required | {"answers", "qrels_kind", "paired_source_doc_id", "evaluation_metadata"}
        if not required <= set(row) or set(row) - allowed:
            raise PairedContractError("cohort_schema_mismatch")
        dataset, qid = row["dataset"], row["qid"]
        if dataset not in FULL_QA_DATASETS or row["split"] != "development":
            raise PairedContractError("cohort_split_or_dataset_invalid")
        if not isinstance(qid, str) or not qid or qid in seen:
            raise PairedContractError("cohort_qid_invalid_or_duplicate")
        if not isinstance(row["question"], str) or not row["question"].strip():
            raise PairedContractError("cohort_question_invalid")
        qrels = row["qrels"]
        if not isinstance(qrels, dict) or any(
            not isinstance(doc_id, str) or not doc_id or type(grade) is not int or not 0 <= grade <= 3
            for doc_id, grade in qrels.items()
        ):
            raise PairedContractError("cohort_qrels_invalid")
        if not isinstance(row["dataset_version"], str) or not row["dataset_version"]:
            raise PairedContractError("cohort_dataset_version_invalid")
        if "answers" in row and (not isinstance(row["answers"], list)
                                  or any(not isinstance(answer, str) for answer in row["answers"])):
            raise PairedContractError("cohort_answers_invalid")
        if "qrels_kind" in row and not isinstance(row["qrels_kind"], str):
            raise PairedContractError("cohort_qrels_kind_invalid")
        if "paired_source_doc_id" in row and not isinstance(row["paired_source_doc_id"], str):
            raise PairedContractError("cohort_paired_source_invalid")
        if dataset == "longbench-zh" and (not row.get("answers") or not row.get("paired_source_doc_id")):
            raise PairedContractError("longbench_answer_gold_missing")
        if dataset in {"hotpotqa", "mmlongbench-doc"} and (not row.get("answers") or not isinstance(row.get("evaluation_metadata"),dict)):
            raise PairedContractError("special_answer_gold_missing")
        if dataset in {"scifact", "miracl-zh"} and ("answers" in row or "paired_source_doc_id" in row):
            raise PairedContractError("unexpected_answer_gold")
        rows.append(row)
        seen.add(qid)
    if execution_contract is not None:
        if type(execution_contract) is not PairedCohortContract:
            raise PairedContractError('paired_execution_contract_invalid')
        if sha256_file(path) != execution_contract.cohort_sha256:
            raise PairedContractError('paired_execution_cohort_hash_mismatch')
        execution_contract.validate_cohort(rows)
        return rows
    names = FULL_QA_DATASETS if any(row['dataset'] in FULL_QA_DATASETS[3:] for row in rows) else DATASETS
    counts = {dataset: sum(row["dataset"] == dataset for row in rows) for dataset in names}
    if counts != {dataset: expected_per_dataset for dataset in names}:
        raise PairedContractError("cohort_sample_counts_invalid")
    return rows


def validate_paired_results(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Require a complete QID × arm matrix with identical frozen inputs."""
    if not isinstance(rows, list) or not rows:
        raise PairedContractError("paired_results_empty")
    by_qid: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise PairedContractError("paired_result_invalid")
        qid, arm = row.get("qid"), row.get("arm")
        if not isinstance(qid, str) or not qid or arm not in ARMS:
            raise PairedContractError("paired_result_identity_invalid")
        if any(not isinstance(row.get(field), str) or not _SHA256.fullmatch(row[field])
               for field in _COMMON_IDENTITY if field.endswith("_sha256")):
            raise PairedContractError("paired_result_hash_invalid")
        if any(not isinstance(row.get(field), str) or not row[field]
               for field in ("dataset", "dataset_version")):
            raise PairedContractError("paired_result_dataset_invalid")
        arms = by_qid.setdefault(qid, {})
        if arm in arms:
            raise PairedContractError("paired_arm_duplicate")
        arms[arm] = row
    expected_arms = set(ARMS)
    for arms in by_qid.values():
        if set(arms) != expected_arms:
            raise PairedContractError("paired_arm_missing")
        first = arms[ARMS[0]]
        for arm in ARMS[1:]:
            if any(arms[arm][field] != first[field] for field in _COMMON_IDENTITY):
                raise PairedContractError("paired_input_mismatch")
    return {"status": "PAIRED_INPUTS_VERIFIED", "qid_count": len(by_qid),
            "arm_count": len(ARMS), "result_count": len(rows)}


def initialize_state(path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        # Rebuild two CHECKs atomically. Keep the registry's global version 2;
        # paired migration versions live in this same center, not another DB.
        connection.execute("PRAGMA foreign_keys=OFF")
        if connection.execute("PRAGMA user_version").fetchone()[0] not in (0, 1, 2):
            raise PairedContractError('paired_registry_version_unsupported')
        connection.execute("BEGIN IMMEDIATE")
        statements = """
            CREATE TABLE IF NOT EXISTS paired_runs (
                run_id TEXT PRIMARY KEY,
                cohort_sha256 TEXT NOT NULL,
                source_manifest_sha256 TEXT NOT NULL,
                code_sha TEXT NOT NULL,
                deadline_utc TEXT NOT NULL,
                dispatch_deadline_utc TEXT,
                round_deadline_utc TEXT,
                expected_qids_json TEXT,
                status TEXT NOT NULL CHECK(status IN ('prepared','running','paused','partial','completed')),
                created_at_utc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paired_attempts (
                run_id TEXT NOT NULL REFERENCES paired_runs(run_id),
                qid TEXT NOT NULL,
                arm TEXT NOT NULL CHECK(arm IN ('CHEAP_ONLY','DYNAMIC','EXPENSIVE_ONLY')),
                ordinal INTEGER NOT NULL CHECK(ordinal = 1),
                input_identity_sha256 TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','succeeded','failed','unknown','blocked','simulated')),
                started_at_utc TEXT NOT NULL,
                ended_at_utc TEXT,
                result_json TEXT,
                error_code TEXT,
                PRIMARY KEY(run_id,qid,arm,ordinal)
            );
            CREATE TABLE IF NOT EXISTS paired_case_results (
                run_id TEXT NOT NULL REFERENCES paired_runs(run_id),
                qid TEXT NOT NULL,
                result_sha256 TEXT NOT NULL,
                result_json TEXT NOT NULL,
                recorded_at_utc TEXT NOT NULL,
                PRIMARY KEY(run_id,qid)
            );
            CREATE TABLE IF NOT EXISTS paired_provider_calls (
                request_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                qid TEXT NOT NULL,
                arm TEXT NOT NULL,
                attempt_ordinal INTEGER NOT NULL DEFAULT 1 CHECK(attempt_ordinal=1),
                purpose TEXT NOT NULL CHECK(purpose IN ('answer','understand','rewrite','expansion','calibration')),
                call_ordinal INTEGER NOT NULL CHECK(call_ordinal>=1),
                request_identity_sha256 TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                role TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','succeeded','failed','unknown','blocked','simulated')),
                send_claimed_at_utc TEXT NOT NULL,
                ended_at_utc TEXT,
                result_json TEXT,
                error_code TEXT,
                FOREIGN KEY(run_id,qid,arm,attempt_ordinal) REFERENCES paired_attempts(run_id,qid,arm,ordinal),
                UNIQUE(run_id,qid,arm,purpose,call_ordinal)
            );
            CREATE TABLE IF NOT EXISTS paired_execution_contracts (
                cohort_run_id TEXT PRIMARY KEY REFERENCES paired_runs(run_id),
                retrieval_run_id TEXT UNIQUE NOT NULL REFERENCES paired_runs(run_id),
                schema_version INTEGER NOT NULL CHECK(schema_version=1),
                contract_sha256 TEXT NOT NULL,
                contract_json TEXT NOT NULL,
                retrieval_binding_sha256 TEXT NOT NULL,
                CHECK(cohort_run_id<>retrieval_run_id)
            );
        """
        for statement in statements.split(';'):
            if statement.strip():
                connection.execute(statement)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(paired_runs)")}
        for name in ("dispatch_deadline_utc", "round_deadline_utc", "expected_qids_json"):
            if name not in columns:
                connection.execute(f"ALTER TABLE paired_runs ADD COLUMN {name} TEXT")
        if "execution_purpose" not in columns:
            connection.execute("ALTER TABLE paired_runs ADD COLUMN execution_purpose TEXT NOT NULL DEFAULT 'COHORT'")
        attempt_columns = {row[1] for row in connection.execute("PRAGMA table_info(paired_attempts)")}
        if "purpose" not in attempt_columns:
            connection.execute("ALTER TABLE paired_attempts ADD COLUMN purpose TEXT NOT NULL DEFAULT 'answer'")
        _migrate_provider_batch_schema(connection)
        connection.execute("PRAGMA user_version=2")


def _migrate_provider_batch_schema(connection) -> None:
    """Transactional v2 -> paired-v3; old values/indices/triggers survive intact."""
    connection.execute('CREATE TABLE IF NOT EXISTS paired_schema_migrations '
        '(version INTEGER PRIMARY KEY, applied_at_utc TEXT NOT NULL, preservation_sha256 TEXT NOT NULL)')
    applied = connection.execute('SELECT version FROM paired_schema_migrations').fetchall()
    if any(row[0] != 3 for row in applied):
        raise PairedContractError('paired_schema_version_unsupported')
    if applied:
        columns = {r[1] for r in connection.execute('PRAGMA table_info(paired_provider_calls)')}
        if not {'batch_plan_sha256','batch_plan_json','lease_identity_sha256'} <= columns:
            raise PairedContractError('paired_batch_schema_incomplete')
        return
    tables = ('paired_attempts', 'paired_provider_calls')
    existing = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for table in existing:
        if table not in tables and re.fullmatch('[A-Za-z0-9_]+', table):
            if any(row[2] in tables for row in connection.execute(f'PRAGMA foreign_key_list("{table}")')):
                raise PairedContractError('paired_schema_external_dependency')
    if connection.execute('PRAGMA foreign_key_check').fetchall():
        raise PairedContractError('paired_schema_existing_foreign_key_failure')
    saved, columns, ddl = {}, {}, {}
    custom = connection.execute("SELECT sql FROM sqlite_master WHERE type IN ('index','trigger') "
        "AND tbl_name IN ('paired_attempts','paired_provider_calls') AND sql IS NOT NULL").fetchall()
    for table in tables:
        columns[table] = [row[1] for row in connection.execute(f'PRAGMA table_info({table})')]
        if any(not re.fullmatch('[A-Za-z0-9_]+', c) for c in columns[table]):
            raise PairedContractError('paired_schema_column_unsupported')
        order = 'run_id,qid,arm,ordinal' if table=='paired_attempts' else 'request_id'
        saved[table] = connection.execute(f'SELECT * FROM {table} ORDER BY {order}').fetchall()
        ddl[table] = connection.execute('SELECT sql FROM sqlite_master WHERE type=\'table\' AND name=?',(table,)).fetchone()[0]
        if table + '_batch_v3' in existing:
            raise PairedContractError('paired_migration_artifact_conflict')
    attempts_ddl = ddl['paired_attempts'].replace("'EXPENSIVE_ONLY'", "'EXPENSIVE_ONLY','INDEXING','SHARED_RETRIEVAL'", 1)
    calls_ddl = ddl['paired_provider_calls'].replace("'calibration'", "'calibration','index_embedding','query_embedding','rerank'", 1)
    head, body = calls_ddl.split('(', 1)
    calls_ddl = head + '(batch_plan_sha256 TEXT, batch_plan_json TEXT, lease_identity_sha256 TEXT, ' + body
    for table, statement in zip(tables, (attempts_ddl, calls_ddl)):
        statement, count = re.subn(r'(?i)^(CREATE TABLE(?: IF NOT EXISTS)?\s+)"?'+table+r'"?',
            r'\1'+table+'_batch_v3', statement, count=1)
        if count != 1:
            raise PairedContractError('paired_schema_ddl_unsupported')
        connection.execute(statement)
        names = ','.join('"'+name+'"' for name in columns[table])
        connection.execute(f'INSERT INTO {table}_batch_v3 ({names}) SELECT {names} FROM {table}')
    connection.execute('DROP TABLE paired_provider_calls')
    connection.execute('DROP TABLE paired_attempts')
    for table in tables:
        connection.execute(f'ALTER TABLE {table}_batch_v3 RENAME TO {table}')
    for (statement,) in custom:
        connection.execute(statement)
    for table in tables:
        names = ','.join('"'+name+'"' for name in columns[table])
        order = 'run_id,qid,arm,ordinal' if table=='paired_attempts' else 'request_id'
        if connection.execute(f'SELECT {names} FROM {table} ORDER BY {order}').fetchall() != saved[table]:
            raise PairedContractError('paired_migration_preservation_failed')
    if connection.execute('PRAGMA foreign_key_check').fetchall():
        raise PairedContractError('paired_migration_foreign_key_failure')
    digest = hashlib.sha256(json.dumps(saved, sort_keys=True, ensure_ascii=False,
        separators=(',', ':')).encode()).hexdigest()
    connection.execute('INSERT INTO paired_schema_migrations VALUES (3,?,?)',
        (datetime.now(timezone.utc).isoformat(), digest))


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise PairedContractError("invalid_utc_timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PairedContractError("timestamp_must_be_timezone_aware")
    return parsed.astimezone(timezone.utc)


def register_run(
    path: Path, *, run_id: str, cohort_sha256: str, source_manifest_sha256: str,
    code_sha: str, deadline_utc: str, now_utc: str | None = None,
    dispatch_deadline_utc: str | None = None, round_deadline_utc: str | None = None,
    expected_qids: list[str] | None = None,
    execution_purpose: str = "COHORT",
) -> None:
    """Register immutable run identity; same-ID mismatches are rejected."""
    if execution_purpose not in {"COHORT", "CALIBRATION", "INDEXING", "RETRIEVAL"}:
        raise PairedContractError("run_execution_purpose_invalid")
    for digest in (cohort_sha256, source_manifest_sha256):
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise PairedContractError("run_source_hash_invalid")
    if not isinstance(code_sha, str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", code_sha):
        raise PairedContractError("run_code_sha_invalid")
    deadline = _parse_utc(deadline_utc)
    now = _parse_utc(now_utc or datetime.now(timezone.utc).isoformat())
    dispatch_deadline = _parse_utc(dispatch_deadline_utc or deadline.isoformat())
    round_deadline = _parse_utc(round_deadline_utc or deadline.isoformat())
    if expected_qids is not None and (not isinstance(expected_qids, list) or not expected_qids
            or any(not isinstance(qid, str) or not qid for qid in expected_qids)
            or len(set(expected_qids)) != len(expected_qids)):
        raise PairedContractError("run_expected_qids_invalid")
    if execution_purpose in {'INDEXING','RETRIEVAL'} and expected_qids is None:
        raise PairedContractError('batch_run_expected_units_required')
    expected_json = json.dumps(expected_qids, ensure_ascii=False, separators=(",", ":")) if expected_qids is not None else None
    initialize_state(path)
    payload = (run_id, cohort_sha256, source_manifest_sha256, code_sha,
               deadline.isoformat(), dispatch_deadline.isoformat(), round_deadline.isoformat(),
               expected_json, "prepared", now.isoformat())
    with _connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        prior = connection.execute("SELECT cohort_sha256,source_manifest_sha256,code_sha,deadline_utc,dispatch_deadline_utc,round_deadline_utc,expected_qids_json,execution_purpose FROM paired_runs WHERE run_id=?",
                                   (run_id,)).fetchone()
        if prior is not None:
            if prior != (*payload[1:8], execution_purpose):
                connection.rollback()
                raise PairedContractError("run_identity_conflict")
            connection.rollback()
            return
        if deadline <= now:
            connection.rollback()
            raise PairedContractError("run_deadline_expired")
        if dispatch_deadline > round_deadline or round_deadline > deadline or dispatch_deadline <= now:
            connection.rollback()
            raise PairedContractError("run_deadline_window_invalid")
        connection.execute("INSERT INTO paired_runs (run_id,cohort_sha256,source_manifest_sha256,code_sha,deadline_utc,dispatch_deadline_utc,round_deadline_utc,expected_qids_json,status,created_at_utc,execution_purpose) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (*payload, execution_purpose))
        connection.commit()


def begin_attempt(
    path: Path, *, run_id: str, qid: str, arm: str, input_identity_sha256: str,
    now_utc: str | None = None,
) -> None:
    """Persist the one allowed attempt before dispatch; never auto-retry."""
    if arm not in ARMS or not qid or not _SHA256.fullmatch(input_identity_sha256):
        raise PairedContractError("attempt_identity_invalid")
    now = _parse_utc(now_utc or datetime.now(timezone.utc).isoformat())
    with _connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        run = connection.execute("SELECT deadline_utc,status,dispatch_deadline_utc,execution_purpose FROM paired_runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None:
            connection.rollback()
            raise PairedContractError("run_not_registered")
        if run[1] not in ("prepared", "running"):
            connection.rollback()
            raise PairedContractError("run_not_dispatchable")
        if run[3] != "COHORT":
            raise PairedContractError("calibration_requires_explicit_send_claim")
        dispatch_deadline = _parse_utc(run[2] or run[0])
        if now >= min(_parse_utc(run[0]), dispatch_deadline):
            connection.execute("UPDATE paired_runs SET status='paused' WHERE run_id=?", (run_id,))
            connection.commit()
            raise PairedContractError("run_deadline_expired")
        try:
            connection.execute("INSERT INTO paired_attempts (run_id,qid,arm,ordinal,input_identity_sha256,status,started_at_utc) VALUES (?,?,?,?,?,'pending',?)",
                               (run_id, qid, arm, 1, input_identity_sha256, now.isoformat()))
            connection.execute("UPDATE paired_runs SET status='running' WHERE run_id=?", (run_id,))
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise PairedContractError("attempt_already_exists_no_retry") from exc


@dataclass(frozen=True)
class ProviderSendClaim:
    """Durable local receipt, never cloud authorization/provider idempotency."""
    run_id: str
    qid: str
    arm: str
    request_id: str
    request_identity_sha256: str
    purpose: str
    call_ordinal: int
    provider: str
    model: str
    role: str
    claimed_at_utc: str


class ProviderSendClaimCallback(Protocol):
    def __call__(self, *, request_id: str, purpose: str, provider: str,
                 model: str, role: str, request_identity_sha256: str,
                 call_ordinal: int = 1) -> ProviderSendClaim: ...

    def settle(self, *, request_id: str, status: str,
               result: dict[str, Any] | None = None,
               error_code: str | None = None) -> None: ...

    def check_before_send(self, *, request_id: str) -> None: ...


def stable_provider_request_id(*, run_id: str, qid: str, arm: str,
                               purpose: str, call_ordinal: int = 1) -> str:
    """Caller-owned operation identity; model changes cannot evade a used slot."""
    if (not isinstance(run_id, str) or not run_id or not isinstance(qid, str) or not qid
            or arm not in (*ARMS, *BATCH_ARMS) or purpose not in (*CHAT_PURPOSES, *BATCH_PURPOSES)
            or type(call_ordinal) is not int or call_ordinal < 1):
        raise PairedContractError('provider_send_claim_identity_invalid')
    identity = json.dumps([run_id, qid, arm, purpose, call_ordinal], separators=(',', ':'))
    return 'p7v6-' + hashlib.sha256(identity.encode('utf-8')).hexdigest()


def claim_provider_send(
    path: Path, *, run_id: str, qid: str, arm: str, request_id: str,
    input_identity_sha256: str, request_identity_sha256: str,
    provider: str, model: str, role: str, purpose: str = "answer",
    call_ordinal: int = 1, now_utc: str | None = None,
    batch_plan_sha256: str | None = None, batch_plan_json: str | None = None,
    lease_identity_sha256: str | None = None,
) -> ProviderSendClaim:
    """Atomically claim ONE actual provider call as an existing Attempt child.

    BEGIN IMMEDIATE + registry-wide request PK + stable purpose/ordinal UNIQUE
    reject repeat calls across processes, even if a caller changes request_id.
    Helpers have independent slots/outcomes; UNKNOWN is never released.
    CALIBRATION owns a separately labelled Run in this SAME Evaluation Center
    SQLite file; it creates a parent CHEAP_ONLY Attempt atomically. Cohort
    callers must have persisted their parent Attempt before entering QuickChain.
    A successful return occurs after COMMIT and permits delegation of ONE call
    only. Provider authorization/capacity/billing are still the original gates.
    """
    batch = purpose in BATCH_PURPOSES
    purposes = (*CHAT_PURPOSES, *BATCH_PURPOSES)
    if (arm not in (*ARMS,*BATCH_ARMS) or not isinstance(qid, str) or not qid
            or not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", request_id)
            or any(not isinstance(value, str) or not _SHA256.fullmatch(value)
                   for value in (input_identity_sha256, request_identity_sha256))
            or purpose not in purposes or type(call_ordinal) is not int or call_ordinal < 1
            or any(not isinstance(value, str) or not value or len(value) > 200 for value in (provider, model, role))
            or (not batch and (arm not in ARMS or role not in {'chat_cheap','chat_expensive'}
                or purpose != 'answer' and role != 'chat_cheap'))
            or (batch and (role != ('rerank' if purpose=='rerank' else 'embedding')
                or purpose=='index_embedding' and arm!='INDEXING'
                or purpose!='index_embedding' and arm not in (*ARMS,'SHARED_RETRIEVAL')
                or any(not isinstance(v,str) or not _SHA256.fullmatch(v)
                       for v in (batch_plan_sha256,lease_identity_sha256))
                or not isinstance(batch_plan_json,str)
                or hashlib.sha256(batch_plan_json.encode()).hexdigest()!=batch_plan_sha256))):
        raise PairedContractError("provider_send_claim_identity_invalid")
    with _connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        # SQLite may wait for another writer. Admission must use the clock
        # AFTER the write lock is acquired, never the pre-wait timestamp.
        now = _parse_utc(now_utc or datetime.now(timezone.utc).isoformat())
        run = connection.execute(
            "SELECT deadline_utc,dispatch_deadline_utc,status,execution_purpose,expected_qids_json,round_deadline_utc "
            "FROM paired_runs WHERE run_id=?", (run_id,),
        ).fetchone()
        if run is None:
            raise PairedContractError("run_not_registered")
        if run[2] not in {"prepared", "running"}:
            raise PairedContractError("run_not_dispatchable")
        if now >= min(_parse_utc(run[0]), _parse_utc(run[1] or run[0]), _parse_utc(run[5] or run[0])):
            raise PairedContractError("run_deadline_expired")
        calibration = run[3] == 'CALIBRATION'
        if (batch and (purpose=='index_embedding' and run[3]!='INDEXING'
                or purpose!='index_embedding' and run[3] not in {'COHORT','RETRIEVAL'}
                or arm=='SHARED_RETRIEVAL' and run[3]!='RETRIEVAL')
                or not batch and run[3] not in {'COHORT','CALIBRATION'}):
            raise PairedContractError('provider_send_claim_run_purpose_mismatch')
        if purpose == 'calibration' and not calibration or calibration and arm != 'CHEAP_ONLY':
            raise PairedContractError("provider_send_claim_run_purpose_mismatch")
        if run[4] is not None and qid not in json.loads(run[4]):
            raise PairedContractError("provider_send_claim_qid_not_registered")
        row = connection.execute(
            "SELECT input_identity_sha256,status FROM paired_attempts "
            "WHERE run_id=? AND qid=? AND arm=? AND ordinal=1", (run_id, qid, arm),
        ).fetchone()
        if row is None:
            if not calibration:
                raise PairedContractError("pending_attempt_not_found")
            connection.execute(
                "INSERT INTO paired_attempts "
                "(run_id,qid,arm,ordinal,input_identity_sha256,status,started_at_utc,purpose) "
                "VALUES (?,?,?,1,?,'pending',?,'calibration')",
                (run_id, qid, arm, input_identity_sha256, now.isoformat()),
            )
        elif row[0] != input_identity_sha256:
            raise PairedContractError("provider_send_claim_input_identity_conflict")
        elif row[1] != 'pending':
            raise PairedContractError("provider_parent_attempt_already_terminal_no_retry")
        if batch:
            previous = connection.execute('SELECT batch_plan_sha256 FROM paired_provider_calls '
                'WHERE run_id=? AND qid=? AND arm=? AND purpose=?',(run_id,qid,arm,purpose)).fetchall()
            if any(r[0]!=batch_plan_sha256 for r in previous):
                raise PairedContractError('provider_batch_plan_identity_conflict')
        # Admission and INSERT share the same write transaction. A persisted
        # overrun must reject before consuming a fresh child slot; final-send
        # checks still cover a settlement racing after this COMMIT.
        _check_persisted_provider_window(connection, run_id=run_id,
            provider=provider, model=model, role=role)
        try:
            connection.execute(
                "INSERT INTO paired_provider_calls "
                "(request_id,run_id,qid,arm,purpose,call_ordinal,request_identity_sha256,"
                "provider,model,role,status,send_claimed_at_utc,batch_plan_sha256,batch_plan_json,lease_identity_sha256) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,'pending',?,?,?,?)",
                (request_id, run_id, qid, arm, purpose, call_ordinal, request_identity_sha256,
                 provider, model, role, now.isoformat(),batch_plan_sha256,batch_plan_json,lease_identity_sha256),
            )
        except sqlite3.IntegrityError as exc:
            raise PairedContractError("provider_request_or_slot_already_claimed_no_retry") from exc
        connection.execute("UPDATE paired_runs SET status='running' WHERE run_id=?", (run_id,))
        connection.commit()
    # Commit/connection cleanup may also take time. An expired committed
    # claim remains consumed (pending/uncertain); it is never released/retried.
    if _parse_utc(now_utc or datetime.now(timezone.utc).isoformat()) >= min(
            _parse_utc(run[0]), _parse_utc(run[1] or run[0]), _parse_utc(run[5] or run[0])):
        raise PairedContractError("run_deadline_expired")
    return ProviderSendClaim(run_id, qid, arm, request_id, request_identity_sha256,
                             purpose, call_ordinal, provider, model, role, now.isoformat())


def provider_send_claim_record(path: Path, *, request_id: str) -> dict[str, Any] | None:
    """Recovery lookup only; an existing receipt never admits another send."""
    with _connect(path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM paired_provider_calls WHERE request_id=?", (request_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    result['result'] = json.loads(result.pop('result_json')) if result['result_json'] else None
    return result


def finish_provider_send(path: Path, *, request_id: str, status: str,
                         result: dict[str, Any] | None = None, error_code: str | None = None,
                         now_utc: str | None = None) -> None:
    """Settle ONE provider child; parent lifecycle stays with its existing owner."""
    if status not in {'succeeded','failed','unknown','blocked','simulated'}:
        raise PairedContractError('attempt_terminal_status_invalid')
    if result is not None and not isinstance(result, dict):
        raise PairedContractError('attempt_result_invalid')
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False) if result is not None else None
    now = _parse_utc(now_utc or datetime.now(timezone.utc).isoformat())
    with _connect(path) as connection:
        cursor = connection.execute(
            "UPDATE paired_provider_calls SET status=?,ended_at_utc=?,result_json=?,error_code=? "
            "WHERE request_id=? AND status='pending'", (status, now.isoformat(), encoded, error_code, request_id),
        )
        if cursor.rowcount != 1:
            raise PairedContractError('pending_provider_send_claim_not_found')


def _check_persisted_provider_window(connection: sqlite3.Connection, *, run_id: str,
                                     provider: str, model: str, role: str) -> None:
    """Read the existing native window; DB/receipt errors fail closed."""
    rows = connection.execute(
        "SELECT error_code,result_json FROM paired_provider_calls "
        "WHERE run_id=? AND provider='siliconflow' AND provider=? AND model=? AND role=? "
        "AND (error_code='SF_FREE_TOKEN_BOUND_EXCEEDED' OR result_json IS NOT NULL)",
        (run_id, provider, model, role),
    ).fetchall()
    for error_code, result_json in rows:
        result = json.loads(result_json) if result_json is not None else None
        dispatch = result.get('provider_dispatch') if isinstance(result, dict) else None
        if (error_code == 'SF_FREE_TOKEN_BOUND_EXCEEDED'
                or isinstance(dispatch, dict) and dispatch.get('request_window_blocked') is True):
            raise PairedContractError('provider_send_window_blocked_by_token_overrun')


def check_provider_send_before_send(path: Path, *, run_id: str, qid: str, arm: str,
                                   input_identity_sha256: str, request_id: str) -> None:
    """Read-only final send check; never creates, releases or replays a claim.

    Native transports call their callback.check_before_send immediately before
    the wire send, after any reservation/lock/wire validation delay. Existing
    __call__/settle signatures remain unchanged.
    """
    with _connect(path) as connection:
        row = connection.execute(
            "SELECT c.run_id,c.qid,c.arm,c.status,a.input_identity_sha256,"
            "r.deadline_utc,r.dispatch_deadline_utc,r.round_deadline_utc,r.status,c.provider,c.model,c.role "
            "FROM paired_provider_calls c JOIN paired_attempts a "
            "ON (a.run_id=c.run_id AND a.qid=c.qid AND a.arm=c.arm AND a.ordinal=c.attempt_ordinal) "
            "JOIN paired_runs r ON r.run_id=c.run_id WHERE c.request_id=?", (request_id,),
        ).fetchone()
        # This finite window is persisted in the existing native call records.
        # Rebuilding a transport/callback or switching QID/arm cannot erase a
        # committed overrun. Unknown timeouts alone do not assert an overrun.
        if row is None or row[:3] != (run_id, qid, arm) or row[4] != input_identity_sha256:
            raise PairedContractError("provider_send_claim_scope_or_identity_mismatch")
        if row[3] != "pending":
            raise PairedContractError("provider_send_claim_already_terminal_no_retry")
        _check_persisted_provider_window(connection, run_id=run_id,
            provider=row[9], model=row[10], role=row[11])
    if row[8] not in {"prepared", "running"}:
        raise PairedContractError("run_not_dispatchable")
    # Take time after the read completes, including possible lock/I/O delay.
    if datetime.now(timezone.utc) >= min(_parse_utc(row[5]),
            _parse_utc(row[6] or row[5]), _parse_utc(row[7] or row[5])):
        raise PairedContractError("run_deadline_expired")


@dataclass(frozen=True)
class SQLiteProviderSendClaims:
    """Bound callback for the normal backend transport; no second persistence implementation."""
    path: Path
    run_id: str
    qid: str
    arm: str
    input_identity_sha256: str

    def __call__(self, *, request_id: str, purpose: str, provider: str,
                 model: str, role: str, request_identity_sha256: str,
                 call_ordinal: int = 1) -> ProviderSendClaim:
        return claim_provider_send(self.path, run_id=self.run_id, qid=self.qid, arm=self.arm,
            input_identity_sha256=self.input_identity_sha256, request_id=request_id,
            purpose=purpose, provider=provider, model=model, role=role,
            request_identity_sha256=request_identity_sha256, call_ordinal=call_ordinal)

    def settle(self, *, request_id: str, status: str, result: dict[str, Any] | None = None,
               error_code: str | None = None) -> None:
        finish_provider_send(self.path, request_id=request_id, status=status, result=result, error_code=error_code)

    def check_before_send(self, *, request_id: str) -> None:
        check_provider_send_before_send(self.path, run_id=self.run_id, qid=self.qid,
            arm=self.arm, input_identity_sha256=self.input_identity_sha256, request_id=request_id)

    def request_id(self, *, purpose: str, call_ordinal: int = 1) -> str:
        return stable_provider_request_id(run_id=self.run_id, qid=self.qid, arm=self.arm,
                                          purpose=purpose, call_ordinal=call_ordinal)


@dataclass(frozen=True)
class ProviderBatchCall:
    batch_ordinal: int
    document_version_ids: tuple[str, ...]
    chunk_ids: tuple[str, ...]
    input_sha256: tuple[str, ...]
    wire_request_sha256: str
    request_identity_sha256: str
    candidate_order: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProviderBatchPlan:
    """Digest-only frozen ordered plan; no source text, vector or secret storage."""
    purpose: str
    provider: str
    model: str
    embedding_identity_sha256: str
    index_profile_sha256: str
    scope_sha256: str
    calls: tuple[ProviderBatchCall, ...]
    query_sha256: str | None = None

    def validate(self):
        if (type(self) is not ProviderBatchPlan or self.purpose not in BATCH_PURPOSES
                or any(type(v) is not str or not v or len(v)>200 for v in (self.provider,self.model))
                or any(type(v) is not str or not _SHA256.fullmatch(v) for v in
                    (self.embedding_identity_sha256,self.index_profile_sha256,self.scope_sha256))
                or type(self.calls) is not tuple or not self.calls
                or any(type(c) is not ProviderBatchCall for c in self.calls)
                or tuple(c.batch_ordinal for c in self.calls)!=tuple(range(1,len(self.calls)+1))
                or (self.purpose!='index_embedding' and (type(self.query_sha256) is not str
                    or not _SHA256.fullmatch(self.query_sha256)))
                or self.purpose=='index_embedding' and self.query_sha256 is not None):
            raise PairedContractError('provider_batch_plan_invalid')
        for c in self.calls:
            ids = (*c.document_version_ids,*c.chunk_ids,*c.candidate_order)
            if (any(type(v) is not tuple for v in
                    (c.document_version_ids,c.chunk_ids,c.input_sha256,c.candidate_order))
                    or not c.input_sha256 or type(c.batch_ordinal) is not int
                    or any(type(v) is not str or not _SHA256.fullmatch(v)
                        for v in (*c.input_sha256,c.wire_request_sha256,c.request_identity_sha256))
                    or any(type(v) is not str or not re.fullmatch('[A-Za-z0-9_.:-]{1,160}',v) for v in ids)
                    or (self.purpose=='query_embedding' and (len(c.input_sha256)!=1 or c.document_version_ids
                        or c.chunk_ids or c.candidate_order or c.input_sha256[0]!=self.query_sha256))
                    or (self.purpose!='query_embedding' and (len(c.document_version_ids)!=len(c.input_sha256)
                        or len(c.chunk_ids)!=len(c.input_sha256) or len(set(c.chunk_ids))!=len(c.chunk_ids)))
                    or (self.purpose=='rerank' and c.candidate_order!=c.chunk_ids)
                    or (self.purpose!='rerank' and c.candidate_order)):
                raise PairedContractError('provider_batch_call_invalid')

    @property
    def canonical_json(self):
        self.validate()
        payload = {name:getattr(self,name) for name in ('purpose','provider','model',
            'embedding_identity_sha256','index_profile_sha256','scope_sha256','query_sha256')}
        payload['calls'] = [vars(c) for c in self.calls]
        return json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)

    @property
    def fingerprint(self):
        return hashlib.sha256(self.canonical_json.encode()).hexdigest()


def begin_provider_batch_scope(path: Path, *, run_id: str, qid: str,
                               input_identity_sha256: str) -> str:
    """Explicit non-answer parent for INDEXING or fixed shared RETRIEVAL Run."""
    if type(qid) is not str or not qid or type(input_identity_sha256) is not str or not _SHA256.fullmatch(input_identity_sha256):
        raise PairedContractError('provider_batch_scope_invalid')
    with _connect(path) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('BEGIN IMMEDIATE')
        now = datetime.now(timezone.utc)
        run = connection.execute('SELECT execution_purpose,expected_qids_json,status,deadline_utc,'
            'dispatch_deadline_utc,round_deadline_utc FROM paired_runs WHERE run_id=?',(run_id,)).fetchone()
        if run is None or run[0] not in {'INDEXING','RETRIEVAL'}:
            raise PairedContractError('provider_batch_scope_run_purpose_invalid')
        if not run[1] or qid not in json.loads(run[1]):
            raise PairedContractError('provider_send_claim_qid_not_registered')
        if run[2] not in {'prepared','running'}:
            raise PairedContractError('run_not_dispatchable')
        if now>=min(_parse_utc(run[3]),_parse_utc(run[4] or run[3]),_parse_utc(run[5] or run[3])):
            raise PairedContractError('run_deadline_expired')
        arm,purpose = ('INDEXING','index_embedding') if run[0]=='INDEXING' else ('SHARED_RETRIEVAL','retrieval')
        try:
            connection.execute('INSERT INTO paired_attempts '
                '(run_id,qid,arm,ordinal,input_identity_sha256,status,started_at_utc,purpose) '
                'VALUES (?,?,?,1,?,\'pending\',?,?)',(run_id,qid,arm,input_identity_sha256,now.isoformat(),purpose))
        except sqlite3.IntegrityError as exc:
            raise PairedContractError('attempt_already_exists_no_retry') from exc
    return arm


@dataclass(frozen=True)
class SQLiteProviderBatchSendClaims:
    """Same ProviderSendClaims ABI, scoped to a frozen plan and native lease."""
    path: Path
    run_id: str
    qid: str
    arm: str
    input_identity_sha256: str
    plan: ProviderBatchPlan
    lease_identity_sha256: str
    lease_validator: Any

    def _call(self, purpose, ordinal):
        self.plan.validate()
        if (purpose!=self.plan.purpose or type(ordinal) is not int or not 1<=ordinal<=len(self.plan.calls)
                or type(self.lease_identity_sha256) is not str or not _SHA256.fullmatch(self.lease_identity_sha256)
                or not callable(self.lease_validator)):
            raise PairedContractError('provider_batch_callback_invalid')
        return self.plan.calls[ordinal-1]

    def _lease(self):
        if self.lease_validator(self.lease_identity_sha256) is not None:
            raise PairedContractError('provider_batch_lease_validation_failed')

    def request_id(self, *, purpose: str, call_ordinal: int = 1) -> str:
        self._call(purpose,call_ordinal)
        return stable_provider_request_id(run_id=self.run_id,qid=self.qid,arm=self.arm,
            purpose=purpose,call_ordinal=call_ordinal)

    def __call__(self, *, request_id: str, purpose: str, provider: str,
                 model: str, role: str, request_identity_sha256: str,
                 call_ordinal: int = 1) -> ProviderSendClaim:
        call = self._call(purpose,call_ordinal)
        if (request_id!=self.request_id(purpose=purpose,call_ordinal=call_ordinal)
                or provider!=self.plan.provider or model!=self.plan.model
                or role!=('rerank' if purpose=='rerank' else 'embedding')
                or request_identity_sha256!=call.request_identity_sha256):
            raise PairedContractError('provider_batch_request_binding_mismatch')
        claim = claim_provider_send(self.path,run_id=self.run_id,qid=self.qid,arm=self.arm,
            input_identity_sha256=self.input_identity_sha256,request_id=request_id,purpose=purpose,
            provider=provider,model=model,role=role,request_identity_sha256=request_identity_sha256,
            call_ordinal=call_ordinal,batch_plan_sha256=self.plan.fingerprint,batch_plan_json=self.plan.canonical_json,
            lease_identity_sha256=self.lease_identity_sha256)
        # Failure after COMMIT remains consumed; no release/reclaim/replay.
        self.check_before_send(request_id=request_id)
        return claim

    def _record(self, request_id):
        row = provider_send_claim_record(self.path,request_id=request_id)
        if (row is None or (row['run_id'],row['qid'],row['arm'])!=(self.run_id,self.qid,self.arm)
                or row['purpose']!=self.plan.purpose or row['batch_plan_sha256']!=self.plan.fingerprint
                or type(row['batch_plan_json']) is not str
                or hashlib.sha256(row['batch_plan_json'].encode()).hexdigest()!=self.plan.fingerprint
                or row['request_identity_sha256']!=self._call(row['purpose'],row['call_ordinal']).request_identity_sha256
                or request_id!=self.request_id(purpose=row['purpose'],call_ordinal=row['call_ordinal'])):
            raise PairedContractError('provider_batch_receipt_identity_mismatch')
        return row

    def check_before_send(self, *, request_id: str) -> None:
        row = self._record(request_id)
        if row['lease_identity_sha256']!=self.lease_identity_sha256:
            raise PairedContractError('provider_batch_lease_identity_mismatch')
        self._lease()
        # Clock is read after lease I/O, then again at the actual native wire boundary.
        check_provider_send_before_send(self.path,run_id=self.run_id,qid=self.qid,arm=self.arm,
            input_identity_sha256=self.input_identity_sha256,request_id=request_id)

    def settle(self, *, request_id: str, status: str, result: dict[str,Any] | None = None,
               error_code: str | None = None) -> None:
        row = self._record(request_id)
        if result is not None:
            if (type(result) is not dict or set(result)!={'result_sha256','result_ref','item_count'}
                    or type(result['result_sha256']) is not str or not _SHA256.fullmatch(result['result_sha256'])
                    or result['result_ref']!='sha256:'+result['result_sha256']
                    or type(result['item_count']) is not int
                    or result['item_count']!=len(self._call(row['purpose'],row['call_ordinal']).input_sha256)):
                raise PairedContractError('provider_batch_result_summary_invalid')
        if status in {'succeeded','simulated'} and result is None:
            raise PairedContractError('provider_batch_result_summary_required')
        if error_code is not None and (type(error_code) is not str or not re.fullmatch('[A-Za-z0-9_.:-]{1,100}',error_code)):
            raise PairedContractError('provider_batch_error_code_invalid')
        finish_provider_send(self.path,request_id=request_id,status=status,result=result,error_code=error_code)

    def reusable_result(self, *, request_id: str) -> dict[str,Any] | None:
        """Digest/reference recovery only; never grants another send."""
        row = self._record(request_id)
        return row['result'] if row['status'] in {'succeeded','simulated'} else None


_CURRENT_SEND_CLAIMS: ContextVar[SQLiteProviderSendClaims | SQLiteProviderBatchSendClaims | None] = ContextVar(
    "evaluation_provider_send_claims", default=None)


@contextmanager
def provider_send_claim_scope(claims: SQLiteProviderSendClaims | SQLiteProviderBatchSendClaims):
    """Bind the parent Attempt for synchronous transport callbacks/helper calls."""
    if type(claims) not in {SQLiteProviderSendClaims,SQLiteProviderBatchSendClaims}:
        raise PairedContractError("provider_send_claim_scope_invalid")
    token = _CURRENT_SEND_CLAIMS.set(claims)
    try:
        yield
    finally:
        _CURRENT_SEND_CLAIMS.reset(token)


class CurrentProviderSendClaims:
    """Inject once into a normal transport; delegate to the current Attempt's DB."""
    @staticmethod
    def _bound() -> SQLiteProviderSendClaims | SQLiteProviderBatchSendClaims:
        claims = _CURRENT_SEND_CLAIMS.get()
        if claims is None:
            raise PairedContractError("provider_send_claim_scope_unavailable")
        return claims

    def __call__(self, *, request_id: str, purpose: str, provider: str,
                 model: str, role: str, request_identity_sha256: str,
                 call_ordinal: int = 1) -> ProviderSendClaim:
        return self._bound()(request_id=request_id, purpose=purpose, provider=provider,
            model=model, role=role, request_identity_sha256=request_identity_sha256,
            call_ordinal=call_ordinal)

    def settle(self, *, request_id: str, status: str, result: dict[str, Any] | None = None,
               error_code: str | None = None) -> None:
        self._bound().settle(request_id=request_id, status=status, result=result, error_code=error_code)

    def check_before_send(self, *, request_id: str) -> None:
        self._bound().check_before_send(request_id=request_id)

    def request_id(self, *, purpose: str, call_ordinal: int = 1) -> str:
        return self._bound().request_id(purpose=purpose, call_ordinal=call_ordinal)


def finish_attempt(
    path: Path, *, run_id: str, qid: str, arm: str, status: str,
    result: dict[str, Any] | None = None, error_code: str | None = None,
    now_utc: str | None = None,
) -> None:
    if status not in {"succeeded", "failed", "unknown", "blocked", "simulated"}:
        raise PairedContractError("attempt_terminal_status_invalid")
    if result is not None and not isinstance(result, dict):
        raise PairedContractError("attempt_result_invalid")
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) if result is not None else None
    now = _parse_utc(now_utc or datetime.now(timezone.utc).isoformat())
    with _connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        prior = connection.execute(
            "SELECT result_json FROM paired_attempts WHERE run_id=? AND qid=? AND arm=? AND status='pending'",
            (run_id, qid, arm),
        ).fetchone()
        if prior and prior[0]:
            checkpoint = json.loads(prior[0])
            result = {**checkpoint, **(result or {})}
            encoded = json.dumps(result, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False)
        cursor = connection.execute(
            "UPDATE paired_attempts SET status=?,ended_at_utc=?,result_json=?,error_code=? "
            "WHERE run_id=? AND qid=? AND arm=? AND ordinal=1 AND status='pending'",
            (status, now.isoformat(), encoded, error_code, run_id, qid, arm),
        )
        if cursor.rowcount != 1:
            raise PairedContractError("pending_attempt_not_found")


def pending_attempts(path: Path, run_id: str) -> list[dict[str, Any]]:
    """Read durable in-flight rows for operator recovery; never resubmit them."""
    with _connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT qid,arm,ordinal,input_identity_sha256,started_at_utc "
            "FROM paired_attempts WHERE run_id=? AND status='pending' ORDER BY qid,arm",
            (run_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _validate_contract_runs(connection, contract, *, for_dispatch=False):
    contract.validate()
    qids = {q for i in contract.selections for q in i.qids}
    records = []
    for run_id, purpose in ((contract.cohort_run_id, 'COHORT'), (contract.retrieval_run_id, 'RETRIEVAL')):
        row = connection.execute('SELECT cohort_sha256,source_manifest_sha256,expected_qids_json,'
            'execution_purpose,deadline_utc,dispatch_deadline_utc,round_deadline_utc,created_at_utc,status '
            'FROM paired_runs WHERE run_id=?', (run_id,)).fetchone()
        if (row is None or row[:2] != (contract.cohort_sha256, contract.source_manifest_sha256)
                or not row[2] or set(json.loads(row[2])) != qids or len(json.loads(row[2])) != len(qids)
                or row[3] != purpose or _parse_utc(row[4]) != contract.execution_lease.deadline
                or not contract.execution_lease.not_before <= _parse_utc(row[7]) < _parse_utc(row[5])
                or not _parse_utc(row[5]) <= _parse_utc(row[6]) <= _parse_utc(row[4])):
            raise PairedContractError('paired_run_link_identity_mismatch')
        if for_dispatch:
            now = datetime.now(timezone.utc)
            if row[8] not in {'prepared', 'running'}:
                raise PairedContractError('paired_run_link_not_dispatchable')
            if not contract.execution_lease.not_before <= now < min(map(_parse_utc, row[4:7])):
                raise PairedContractError('paired_run_link_deadline_expired')
        records.append(row)
    if tuple(map(_parse_utc, records[0][4:7])) != tuple(map(_parse_utc, records[1][4:7])):
        raise PairedContractError('paired_run_link_lease_mismatch')


def register_paired_run_link(path: Path, contract: PairedCohortContract, *, retrieval_binding_sha256: str):
    """Append one immutable relationship in the existing registry, under its lock.

    Neither native Run nor any Attempt/claim is changed or retried. The new
    table's explicit schema_version=1 is independent of the retained paired-v3
    migration and registry user_version=2.
    """
    if type(retrieval_binding_sha256) is not str or not _SHA256.fullmatch(retrieval_binding_sha256):
        raise PairedContractError('paired_retrieval_binding_hash_invalid')
    encoded, digest = contract.canonical_json, contract.fingerprint
    initialize_state(path)
    with _connect(path) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('BEGIN IMMEDIATE')
        _validate_contract_runs(connection, contract)
        payload = (contract.cohort_run_id, contract.retrieval_run_id, 1, digest, encoded, retrieval_binding_sha256)
        prior = connection.execute('SELECT * FROM paired_execution_contracts WHERE cohort_run_id=? '
            'OR retrieval_run_id=?', payload[:2]).fetchall()
        if prior:
            if prior != [payload]:
                raise PairedContractError('paired_run_link_conflict')
            return
        connection.execute('INSERT INTO paired_execution_contracts VALUES (?,?,?,?,?,?)', payload)


def validate_paired_run_link(path: Path, contract: PairedCohortContract, *,
                             retrieval_binding_sha256: str, for_dispatch=False):
    """Read exact persisted linkage; usable by the operator's native guard."""
    encoded, digest = contract.canonical_json, contract.fingerprint
    with _connect(path) as connection:
        prior = connection.execute('SELECT * FROM paired_execution_contracts WHERE cohort_run_id=?',
            (contract.cohort_run_id,)).fetchone()
        if prior != (contract.cohort_run_id, contract.retrieval_run_id, 1, digest, encoded, retrieval_binding_sha256):
            raise PairedContractError('paired_run_link_missing_or_changed')
        _validate_contract_runs(connection, contract, for_dispatch=for_dispatch)
    return {'COHORT': contract.cohort_run_id, 'RETRIEVAL': contract.retrieval_run_id}


def run_record(path: Path, run_id: str) -> dict[str, Any] | None:
    """Return immutable run identity and status for safe CLI resume."""
    with _connect(path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM paired_runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    if result.get("expected_qids_json"):
        result["expected_qids"] = json.loads(result.pop("expected_qids_json"))
    return result


def attempts_for_qid(path: Path, *, run_id: str, qid: str) -> dict[str, dict[str, Any]]:
    """Read terminal attempt receipts for resume; pending rows remain non-retryable."""
    with _connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT arm,status,input_identity_sha256,result_json,error_code,purpose FROM paired_attempts "
            "WHERE run_id=? AND qid=? ORDER BY arm", (run_id, qid),
        ).fetchall()
        provider_rows = connection.execute(
            "SELECT * FROM paired_provider_calls WHERE run_id=? AND qid=? ORDER BY arm,purpose,call_ordinal",
            (run_id, qid),
        ).fetchall()
    result = {}
    for row in rows:
        item = dict(row)
        item["result"] = json.loads(item.pop("result_json")) if item["result_json"] else None
        item["provider_calls"] = []
        for child in provider_rows:
            if child["arm"] == item["arm"]:
                child_item = dict(child)
                child_item["result"] = json.loads(child_item.pop("result_json")) if child_item["result_json"] else None
                item["provider_calls"].append(child_item)
        result[item["arm"]] = item
    return result


def checkpoint_envelope(path: Path, *, run_id: str, qid: str, arm: str,
                        identity: dict[str, str]) -> None:
    """Commit actual prompt/context identity before the first provider admission.

    This uses the existing pending Attempt row. A mismatched later arm is
    rejected before its provider is invoked, not merely after comparison.
    """
    if (set(identity) != {"prompt_hash", "context_hash", "envelope_identity"}
            or any(not _SHA256.fullmatch(value) for value in identity.values())):
        raise PairedContractError("paired_envelope_identity_invalid")
    with _connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            "SELECT arm,status,result_json FROM paired_attempts WHERE run_id=? AND qid=?",
            (run_id, qid),
        ).fetchall()
        current = next((row for row in rows if row[0] == arm), None)
        if current is None or current[1] != "pending":
            raise PairedContractError("pending_attempt_not_found")
        for _, _, encoded in rows:
            receipt = json.loads(encoded) if encoded else {}
            prior = receipt.get("dispatch_identity")
            if prior is not None and prior != identity:
                raise PairedContractError("paired_prompt_or_context_mismatch_before_send")
        connection.execute(
            "UPDATE paired_attempts SET result_json=? WHERE run_id=? AND qid=? AND arm=?",
            (json.dumps({"dispatch_identity": identity}, sort_keys=True), run_id, qid, arm),
        )


@contextmanager
def exclusive_runner_lock(path: Path):
    """Cross-process single-writer lock for the paired runner (concurrency=1)."""
    lock_path = Path(str(Path(path)) + ".runner.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as stream:
        if stream.seek(0, 2) == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            raise PairedContractError("paired_runner_already_active_concurrency_one") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def record_telemetry_status(
    path: Path, *, run_id: str, qid: str, arm: str, status: str,
    trace_id: str | None = None, error_code: str | None = None,
    retry_telemetry_only: bool = False,
) -> None:
    """Attach telemetry delivery/readback status without changing attempt state."""
    if status not in {"flushed_and_read_back", "unknown", "not_configured"}:
        raise PairedContractError("telemetry_status_invalid")
    if trace_id is not None and not re.fullmatch(r"[0-9a-f]{32}", trace_id):
        raise PairedContractError("telemetry_trace_id_invalid")
    if error_code is not None and not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", error_code):
        raise PairedContractError("telemetry_error_code_invalid")
    with _connect(path) as connection:
        row = connection.execute(
            "SELECT result_json,status FROM paired_attempts "
            "WHERE run_id=? AND qid=? AND arm=? AND ordinal=1",
            (run_id, qid, arm),
        ).fetchone()
        if row is None or row[1] == "pending" or row[0] is None:
            raise PairedContractError("terminal_attempt_result_missing")
        result = json.loads(row[0])
        if "telemetry" in result:
            if not retry_telemetry_only or result["telemetry"]["status"] == "flushed_and_read_back":
                raise PairedContractError("telemetry_status_already_recorded")
            result.setdefault("telemetry_prior_receipts", []).append(result["telemetry"])
        result["telemetry"] = {"status": status, "trace_id": trace_id,
                               "error_code": error_code}
        connection.execute(
            "UPDATE paired_attempts SET result_json=? "
            "WHERE run_id=? AND qid=? AND arm=? AND ordinal=1",
            (json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
             run_id, qid, arm),
        )


def save_case_result(path: Path, *, run_id: str, result: dict[str, Any]) -> dict[str, str]:
    """Persist one immutable, complete paired QID result for recovery/reporting."""
    if not isinstance(result, dict) or not isinstance(result.get("qid"), str) or not result["qid"]:
        raise PairedContractError("paired_case_result_invalid")
    if set(result.get("arms", {})) != set(ARMS):
        raise PairedContractError("paired_case_result_incomplete")
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    with _connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT result_sha256 FROM paired_case_results WHERE run_id=? AND qid=?",
            (run_id, result["qid"]),
        ).fetchone()
        if row is not None:
            if row[0] != digest:
                connection.rollback()
                raise PairedContractError("paired_case_result_conflict")
            connection.rollback()
            return {"status": "unchanged", "sha256": digest}
        attempts = connection.execute(
            "SELECT arm,status FROM paired_attempts WHERE run_id=? AND qid=?",
            (run_id, result["qid"]),
        ).fetchall()
        if len(attempts) != len(ARMS) or {row[0] for row in attempts} != set(ARMS):
            connection.rollback()
            raise PairedContractError("paired_case_attempts_incomplete")
        if any(row[1] == "pending" for row in attempts):
            connection.rollback()
            raise PairedContractError("paired_case_attempts_pending")
        connection.execute(
            "INSERT INTO paired_case_results VALUES (?,?,?,?,?)",
            (run_id, result["qid"], digest, encoded,
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        connection.commit()
    return {"status": "saved", "sha256": digest}


def load_case_results(path: Path, run_id: str) -> list[dict[str, Any]]:
    """Read saved paired QID outcomes in stable order."""
    with _connect(path) as connection:
        rows = connection.execute(
            "SELECT result_sha256,result_json FROM paired_case_results "
            "WHERE run_id=? ORDER BY qid", (run_id,),
        ).fetchall()
    results = []
    for digest, encoded in rows:
        if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != digest:
            raise PairedContractError("paired_case_result_hash_mismatch")
        results.append(json.loads(encoded))
    return results


def close_run(path: Path, *, run_id: str, status: str) -> None:
    if status not in {"paused", "partial", "completed"}:
        raise PairedContractError("run_terminal_status_invalid")
    with _connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        run = connection.execute("SELECT status,expected_qids_json,execution_purpose FROM paired_runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None:
            raise PairedContractError("run_not_registered")
        if run[0] in {"completed", "partial", "paused"}:
            raise PairedContractError("run_already_closed")
        pending = connection.execute(
            "SELECT COUNT(*) FROM paired_attempts WHERE run_id=? AND status='pending'", (run_id,)
        ).fetchone()[0]
        pending += connection.execute(
            "SELECT COUNT(*) FROM paired_provider_calls WHERE run_id=? AND status='pending'", (run_id,),
        ).fetchone()[0]
        if pending:
            raise PairedContractError("run_has_pending_attempts")
        if status == "completed":
            if run[2] == "CALIBRATION":
                attempts = connection.execute(
                    "SELECT qid,purpose FROM paired_attempts WHERE run_id=?", (run_id,),
                ).fetchall()
                if not attempts or any(row[1] != "calibration" for row in attempts):
                    raise PairedContractError("calibration_attempts_missing_or_mixed")
                if run[1] and {row[0] for row in attempts} != set(json.loads(run[1])):
                    raise PairedContractError("calibration_attempts_incomplete")
                connection.execute("UPDATE paired_runs SET status=? WHERE run_id=?", (status, run_id))
                return
            if run[2] in {'INDEXING','RETRIEVAL'}:
                expected = set(json.loads(run[1] or '[]'))
                parents = connection.execute('SELECT qid,arm,status FROM paired_attempts WHERE run_id=?',(run_id,)).fetchall()
                arm = 'INDEXING' if run[2]=='INDEXING' else 'SHARED_RETRIEVAL'
                if (not expected or {r[0] for r in parents}!=expected or len(parents)!=len(expected)
                        or any(r[1]!=arm or r[2] not in {'succeeded','simulated'} for r in parents)):
                    raise PairedContractError('batch_run_scopes_incomplete')
                for qid in expected:
                    children = connection.execute('SELECT purpose,call_ordinal,status,batch_plan_json '
                        'FROM paired_provider_calls WHERE run_id=? AND qid=?',(run_id,qid)).fetchall()
                    if not children or any(r[2] not in {'succeeded','simulated'} for r in children):
                        raise PairedContractError('batch_run_calls_incomplete')
                    for purpose in {r[0] for r in children}:
                        group = [r for r in children if r[0]==purpose]
                        if (len({r[3] for r in group})!=1 or not group[0][3]
                                or sorted(r[1] for r in group)!=list(range(1,len(json.loads(group[0][3])['calls'])+1))):
                            raise PairedContractError('batch_run_calls_incomplete')
                connection.execute('UPDATE paired_runs SET status=? WHERE run_id=?',(status,run_id))
                return
            if not run[1]:
                raise PairedContractError("run_expected_qids_missing")
            expected = set(json.loads(run[1]))
            stored = {row[0] for row in connection.execute(
                "SELECT qid FROM paired_case_results WHERE run_id=?", (run_id,)).fetchall()}
            if stored != expected:
                raise PairedContractError("run_case_results_incomplete")
            for qid in expected:
                arms = connection.execute(
                    "SELECT arm,status FROM paired_attempts WHERE run_id=? AND qid=?",
                    (run_id, qid),
                ).fetchall()
                if len(arms) != len(ARMS) or {row[0] for row in arms} != set(ARMS):
                    raise PairedContractError("run_arm_matrix_incomplete")
                if any(row[1] == "pending" for row in arms):
                    raise PairedContractError("run_has_pending_attempts")
        connection.execute("UPDATE paired_runs SET status=? WHERE run_id=?", (status, run_id))
