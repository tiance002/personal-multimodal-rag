"""One-shot read-only validation of the frozen context-neighbor repository query.

This script performs one formal project import, compiles the generated query
with SQLAlchemy's PostgreSQL dialect, reads one frozen seed, then invokes the
real PostgresKnowledgeRepository reader once. It never prints document text or
credentials and never retries a denied or failed operation.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import sys
import time
import traceback
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any


HOST = "127.0.0.1"
PORT = 25438
DATABASE = "rag"
CONNECT_TIMEOUT_SECONDS = 3
STATEMENT_TIMEOUT_MS = 3000
LOCK_TIMEOUT_MS = 500
IDLE_TIMEOUT_MS = 5000
TOTAL_DEADLINE_SECONDS = 15
MAX_CONNECTION_ATTEMPTS = 2
MAX_READER_ROWS = 3

# These are the approved identities and fingerprints from
# context-neighbor-db-plan-20261005/scope-and-sql-freeze.json.
KB_ID = "d78f3434-0e8f-40bf-afcb-f1c2c311ff51"
DOCUMENT_ID = "87c2d4e7-5a24-49e4-9c0c-e8694d61d2f3"
VERSION_ID = "5f93ddd1-79bc-48e2-95c0-51a76f05ae46"
SEED_ID = "9515a7b9-5322-4b9e-bf3a-d41ca7af458c"
SEED_HASH = "3a54b12a314e9429afbd1db82d89084c2f8d01dc2dd2d9914ca2746a7bd0cf93"
FROZEN_READER_SOURCE_SHA256 = "41ee5b8411bdc5a5482987f6b47a4d8216578aef3df96407248534a81ebe8355"
FROZEN_SQL_SHA256 = "524b3aa3d8f59791beceb25007923511a5894307fc6cbc9e8605b1f5355610da"

BOOTSTRAP_SQL = """
SELECT c.id::text AS chunk_id, c.knowledge_base_id::text AS knowledge_base_id,
       c.document_id::text AS document_id, c.version_id::text AS version_id,
       c.chunk_index, c.content, c.content_sha256, c.locator, c.chunk_type
FROM chunks c
JOIN documents d ON d.id = c.document_id AND d.active_version_id = c.version_id
JOIN document_versions dv ON dv.id = c.version_id AND dv.document_id = c.document_id
JOIN knowledge_bases kb ON kb.id = c.knowledge_base_id
JOIN document_sections section ON section.id = c.section_id
    AND section.document_id = c.document_id AND section.version_id = c.version_id
    AND section.knowledge_base_id = c.knowledge_base_id
WHERE c.id = CAST(:seed_id AS uuid)
  AND c.knowledge_base_id = CAST(:kb_id AS uuid)
  AND c.document_id = CAST(:document_id AS uuid)
  AND c.version_id = CAST(:version_id AS uuid)
  AND c.content_sha256 = :seed_hash
  AND d.knowledge_base_id = c.knowledge_base_id
  AND d.deleted_at IS NULL AND dv.index_status = 'ready' AND kb.deleted_at IS NULL
  AND c.chunk_type = 'text'
  AND c.start_pos >= section.start_pos AND c.start_pos < c.end_pos
  AND c.end_pos <= section.end_pos
  AND c.locator->'start' = to_jsonb(c.start_pos)
  AND c.locator->'end' = to_jsonb(c.end_pos)
  AND (
      (c.locator->>'kind' IN ('text', 'markdown')
       AND section.page_start IS NULL AND section.page_end IS NULL)
      OR (c.locator->>'kind' = 'pdf'
          AND section.page_start > 0 AND section.page_start = section.page_end
          AND c.locator->'page' = to_jsonb(section.page_start))
  )
LIMIT 2
"""

VERIFY_READ_ONLY_SQL = """
SELECT current_setting('transaction_read_only')::boolean AS read_only,
       current_setting('statement_timeout')::interval > interval '0'
           AS statement_timeout_positive,
       current_setting('statement_timeout')::interval <= interval '3 seconds'
           AS statement_timeout_bounded,
       current_setting('lock_timeout')::interval = interval '500 milliseconds'
           AS lock_timeout_exact,
       current_setting('idle_in_transaction_session_timeout')::interval
           = interval '5 seconds' AS idle_timeout_exact
"""


class ValidationStop(RuntimeError):
    pass


class CaptureRows:
    """Capture one real repository-generated TextClause without a DB connection."""

    def __init__(self, owner: "CaptureEngine") -> None:
        self.owner = owner

    def mappings(self) -> "CaptureRows":
        return self

    def all(self) -> list[Any]:
        return []


class CaptureConnection:
    def __init__(self, owner: "CaptureEngine") -> None:
        self.owner = owner

    def __enter__(self) -> "CaptureConnection":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def execute(self, statement: Any, params: dict[str, Any]) -> CaptureRows:
        self.owner.statement = statement
        self.owner.params = dict(params)
        return CaptureRows(self.owner)


class CaptureEngine:
    """Small SQL capture seam; it does not emulate results or open a socket."""

    def __init__(self) -> None:
        self.statement: Any = None
        self.params: dict[str, Any] = {}

    def connect(self) -> CaptureConnection:
        return CaptureConnection(self)


class ReadOnlyConnectable:
    """Engine facade that bounds, verifies, rolls back, and closes each reader connection."""

    def __init__(self, engine: Any, state: dict[str, Any], deadline: float, text: Any) -> None:
        self._engine = engine
        self._state = state
        self._deadline = deadline
        self._text = text

    @contextmanager
    def connect(self):
        state = self._state
        if state["connection_attempts"] >= MAX_CONNECTION_ATTEMPTS:
            raise ValidationStop("connection quota exhausted")
        remaining = self._deadline - time.monotonic()
        if remaining <= CONNECT_TIMEOUT_SECONDS + 0.1:
            raise TimeoutError("remaining validation deadline is below connect bound")

        state["connection_attempts"] += 1
        conn = None
        transaction = None
        try:
            conn = self._engine.connect()
            state["connections_opened"] += 1
            transaction = conn.begin()
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            conn.exec_driver_sql(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT_MS}ms'")
            conn.exec_driver_sql(f"SET LOCAL idle_in_transaction_session_timeout = '{IDLE_TIMEOUT_MS}ms'")

            observed = conn.execute(self._text(VERIFY_READ_ONLY_SQL)).mappings().one()
            checks = {key: bool(observed[key]) for key in (
                "read_only",
                "statement_timeout_positive",
                "statement_timeout_bounded",
                "lock_timeout_exact",
                "idle_timeout_exact",
            )}
            state["read_only_checks"].append(checks)
            if not all(checks.values()):
                raise ValidationStop("read-only transaction or timeout verification failed")
            yield conn
        finally:
            exception_in_flight = sys.exc_info()[0] is not None
            rollback_error = None
            close_error = None
            if conn is not None:
                try:
                    if transaction is not None and transaction.is_active:
                        transaction.rollback()
                        state["rollbacks"] += 1
                except Exception as exc:  # record type only; never emit driver text
                    rollback_error = type(exc).__name__
                try:
                    conn.close()
                    state["connections_closed"] += 1
                except Exception as exc:
                    close_error = type(exc).__name__
            if rollback_error or close_error:
                state["cleanup_failures"].append({
                    "rollback_error_type": rollback_error,
                    "close_error_type": close_error,
                })
                if not exception_in_flight:
                    raise ValidationStop("connection cleanup failed")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_sqlstate(exc: BaseException) -> str | None:
    for candidate in (exc, getattr(exc, "orig", None)):
        if candidate is None:
            continue
        code = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        if isinstance(code, str) and len(code) == 5 and code.isalnum():
            return code
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", required=True, type=Path)
    args = parser.parse_args()
    report_dir = args.report_dir.expanduser().absolute()
    try:
        report_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print(json.dumps({"status": "BLOCKED", "stage": "report_dir", "reason": "directory_exists"}))
        return 2

    report_path = report_dir / "report.json"
    report: dict[str, Any] = {
        "schema": "context-neighbor-db-validation/v1",
        "status": "RUNNING",
        "stage": "source_identity",
        "target": {"host": HOST, "port": PORT, "database": DATABASE},
        "limits": {
            "connection_attempts": MAX_CONNECTION_ATTEMPTS,
            "bootstrap_rows": 1,
            "reader_rows": MAX_READER_ROWS,
            "connect_timeout_seconds": CONNECT_TIMEOUT_SECONDS,
            "statement_timeout_ms": STATEMENT_TIMEOUT_MS,
            "lock_timeout_ms": LOCK_TIMEOUT_MS,
            "idle_timeout_ms": IDLE_TIMEOUT_MS,
            "total_deadline_seconds": TOTAL_DEADLINE_SECONDS,
        },
        "formal_import_attempts": 0,
        "credential_prompt": "NOT_RUN",
        "db": {
            "connection_attempts": 0,
            "connections_opened": 0,
            "connections_closed": 0,
            "read_only_checks": [],
            "rollbacks": 0,
            "cleanup_failures": [],
        },
        "checks": {
            "formal_import": "NOT_RUN",
            "sql_compile": "NOT_RUN",
            "seed_bootstrap": "NOT_RUN",
            "repository_query": "NOT_RUN",
            "neighbor_observed": "NOT_RUN",
        },
        "reader_source_sha256": None,
        "failure": None,
    }
    write_report(report_path, report)

    engine = None
    state = report["db"]
    exit_code = 2
    phase = "source_identity"
    started: float | None = None
    try:
        repo_root = Path(__file__).resolve().parents[1]
        reader_source = repo_root / "backend" / "app" / "adapters" / "postgres" / "knowledge_repository.py"
        source_hash = sha256_file(reader_source)
        report["reader_source_sha256"] = source_hash
        if source_hash != FROZEN_READER_SOURCE_SHA256:
            raise ValidationStop("reader source differs from frozen source identity")
        write_report(report_path, report)

        phase = "formal_import"
        report["stage"] = phase
        report["formal_import_attempts"] = 1
        write_report(report_path, report)
        from sqlalchemy import create_engine, event, text
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.engine import URL
        from sqlalchemy.pool import NullPool

        from backend.app.adapters.parsers import ParserRegistry
        from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
        from backend.app.domain.models import ChunkRecord
        from backend.app.domain.scope import Scope

        report["checks"]["formal_import"] = "PASS"
        phase = "sql_compile"
        report["stage"] = phase
        write_report(report_path, report)

        scope = Scope.from_ids([KB_ID], [DOCUMENT_ID])
        compile_content = "compile-only"
        compile_hash = hashlib.sha256(compile_content.encode("utf-8")).hexdigest()
        compile_seed = ChunkRecord(
            SEED_ID, KB_ID, DOCUMENT_ID, VERSION_ID, compile_content,
            {"kind": "text", "start": 0, "end": len(compile_content), "quote": compile_content},
            is_current=True, content_sha256=compile_hash,
        )
        capture = CaptureEngine()
        compile_repository = PostgresKnowledgeRepository(capture, None, ParserRegistry())
        compile_repository.read_context_rows(scope, [compile_seed])
        statement = capture.statement
        params = capture.params
        if statement is None or not isinstance(params, dict):
            raise ValidationStop("repository did not produce a bindable statement")
        raw_sql = str(statement)
        raw_sql_hash = hashlib.sha256(raw_sql.encode("utf-8")).hexdigest()
        if raw_sql_hash != FROZEN_SQL_SHA256:
            raise ValidationStop("generated repository SQL differs from frozen SQL")
        compiled = statement.compile(dialect=postgresql.dialect())
        if set(compiled.params) != set(params):
            raise ValidationStop("compiled SQL bind names differ from repository parameters")
        compiled_sql_hash = hashlib.sha256(str(compiled).encode("utf-8")).hexdigest()
        report["checks"]["sql_compile"] = "PASS"
        report["sql_compile"] = {
            "dialect": "postgresql",
            "raw_sql_sha256": raw_sql_hash,
            "compiled_sql_sha256": compiled_sql_hash,
            "bind_count": len(compiled.params),
            "matches_frozen_sql": True,
        }

        phase = "credential_prompt"
        report["stage"] = phase
        report["credential_prompt"] = "PENDING_MASKED_LOCAL_INPUT"
        write_report(report_path, report)
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            raise ValidationStop("masked credentials require an interactive terminal")
        username = getpass.getpass("PostgreSQL username (hidden): ")
        password = getpass.getpass("PostgreSQL password (hidden; Enter if none): ")
        if not username:
            raise ValidationStop("database username is empty")
        report["credential_prompt"] = "MASKED_LOCAL_INPUT_COMPLETED"

        phase = "database_setup"
        report["stage"] = phase
        started = time.monotonic()
        deadline = started + TOTAL_DEADLINE_SECONDS
        connect_options = (
            "-c default_transaction_read_only=on "
            f"-c statement_timeout={STATEMENT_TIMEOUT_MS} "
            f"-c lock_timeout={LOCK_TIMEOUT_MS} "
            f"-c idle_in_transaction_session_timeout={IDLE_TIMEOUT_MS}"
        )
        engine = create_engine(
            URL.create(
                "postgresql+psycopg",
                username=username,
                password=password or None,
                host=HOST,
                port=PORT,
                database=DATABASE,
            ),
            poolclass=NullPool,
            pool_pre_ping=False,
            hide_parameters=True,
            connect_args={
                "connect_timeout": CONNECT_TIMEOUT_SECONDS,
                "options": connect_options,
            },
        )
        del username, password

        def before_execute(conn, clauseelement, multiparams, bound_params, execution_options):
            remaining_ms = int((deadline - time.monotonic()) * 1000) - 50
            if remaining_ms <= 0:
                raise TimeoutError("total validation deadline reached")
            query_timeout_ms = min(STATEMENT_TIMEOUT_MS, remaining_ms)
            conn.exec_driver_sql(f"SET LOCAL statement_timeout = '{query_timeout_ms}ms'")
            sql = str(clauseelement)
            if sql.lstrip().startswith("WITH requested("):
                actual_hash = hashlib.sha256(sql.encode("utf-8")).hexdigest()
                compiled_actual = clauseelement.compile(dialect=conn.dialect)
                if actual_hash != FROZEN_SQL_SHA256 or set(compiled_actual.params) != set(bound_params):
                    raise ValidationStop("executed reader statement differs from compiled frozen query")
                report["repository_execute_sql_sha256"] = actual_hash
                report["repository_execute_bind_count"] = len(compiled_actual.params)
                report["repository_execute_count"] = report.get("repository_execute_count", 0) + 1

        event.listen(engine, "before_execute", before_execute)
        read_engine = ReadOnlyConnectable(engine, state, deadline, text)

        phase = "seed_bootstrap"
        report["stage"] = phase
        write_report(report_path, report)
        bootstrap_params = {
            "seed_id": SEED_ID,
            "kb_id": KB_ID,
            "document_id": DOCUMENT_ID,
            "version_id": VERSION_ID,
            "seed_hash": SEED_HASH,
        }
        with read_engine.connect() as conn:
            bootstrap_rows = conn.execute(text(BOOTSTRAP_SQL), bootstrap_params).mappings().all()
        if len(bootstrap_rows) != 1:
            raise ValidationStop("frozen seed did not resolve to exactly one active eligible row")
        row = bootstrap_rows[0]
        content = row["content"]
        locator = row["locator"]
        if (
            str(row["chunk_id"]) != SEED_ID
            or str(row["knowledge_base_id"]) != KB_ID
            or str(row["document_id"]) != DOCUMENT_ID
            or str(row["version_id"]) != VERSION_ID
            or row["content_sha256"] != SEED_HASH
            or not isinstance(content, str)
            or not content.strip()
            or hashlib.sha256(content.encode("utf-8")).hexdigest() != SEED_HASH
            or not isinstance(locator, dict)
        ):
            raise ValidationStop("frozen seed identity or evidence hash verification failed")
        seed = ChunkRecord(
            SEED_ID, KB_ID, DOCUMENT_ID, VERSION_ID, content, locator,
            is_current=True, content_sha256=SEED_HASH,
        )
        report["checks"]["seed_bootstrap"] = "PASS"
        report["seed"] = {
            "chunk_id": SEED_ID,
            "chunk_index": int(row["chunk_index"]),
            "content_sha256": SEED_HASH,
        }

        phase = "repository_query"
        report["stage"] = phase
        write_report(report_path, report)
        repository = PostgresKnowledgeRepository(read_engine, None, ParserRegistry())
        result = repository.read_context_rows(scope, [seed], max_seeds=1)
        if report.get("repository_execute_count") != 1:
            raise ValidationStop("repository SQL was not observed before execution")
        report["checks"]["repository_query"] = "PASS"

        phase = "neighbor_validation"
        report["stage"] = phase
        observed_rows = []
        seed_matches = 0
        if len(result.rows) > MAX_READER_ROWS:
            raise ValidationStop("repository returned more than the frozen row limit")
        for item in result.rows:
            chunk = item.metadata.chunk
            if (
                item.seed_id != SEED_ID
                or item.offset not in (-1, 0, 1)
                or chunk.knowledge_base_id != KB_ID
                or chunk.document_id != DOCUMENT_ID
                or chunk.version_id != VERSION_ID
                or not scope.contains(chunk.knowledge_base_id, chunk.document_id)
                or hashlib.sha256(chunk.content.encode("utf-8")).hexdigest() != chunk.content_sha256
            ):
                raise ValidationStop("repository returned a row outside the frozen scope or evidence boundary")
            if item.offset == 0:
                if chunk.chunk_id != SEED_ID:
                    raise ValidationStop("reader offset zero does not identify the frozen seed")
                seed_matches += 1
            observed_rows.append({
                "offset": item.offset,
                "chunk_id": chunk.chunk_id,
                "content_sha256": chunk.content_sha256,
            })
        neighbor_count = sum(item["offset"] != 0 for item in observed_rows)
        if seed_matches != 1:
            raise ValidationStop("actual repository query did not return exactly one verified seed")
        report["reader_rows"] = observed_rows
        report["neighbor_count"] = neighbor_count
        report["reason_codes"] = sorted({reason.code for reason in result.reasons})
        if neighbor_count < 1:
            report["checks"]["neighbor_observed"] = "FAIL"
            raise ValidationStop("query completed but no eligible adjacent row was observed")
        report["checks"]["neighbor_observed"] = "PASS"
        report["status"] = "PASS"
        report["stage"] = "complete"
        exit_code = 0
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            report["status"] = "INTERRUPTED"
        elif phase == "formal_import":
            report["status"] = "BLOCKED"
            report["checks"]["formal_import"] = "BLOCKED"
        else:
            report["status"] = "FAIL"
            if phase == "sql_compile":
                report["checks"]["sql_compile"] = "FAIL"
            elif phase == "seed_bootstrap":
                report["checks"]["seed_bootstrap"] = "FAIL"
            elif phase == "repository_query":
                report["checks"]["repository_query"] = "FAIL"
            elif phase == "neighbor_validation":
                report["checks"]["neighbor_observed"] = "FAIL"
        report["stage"] = phase
        report["failure"] = {
            "exception_type": type(exc).__name__,
            "sqlstate": safe_sqlstate(exc),
            "traceback_frames": [
                {
                    "file": Path(frame.filename).name,
                    "function": frame.name,
                    "line": frame.lineno,
                }
                for frame in traceback.extract_tb(exc.__traceback__)[-40:]
            ],
        }
        exit_code = 2 if report["status"] in {"BLOCKED", "INTERRUPTED"} else 1
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception as exc:
                state["cleanup_failures"].append({
                    "engine_dispose_error_type": type(exc).__name__,
                })
                report["status"] = "FAIL"
                report["stage"] = "cleanup"
                if report["failure"] is None:
                    report["failure"] = {"exception_type": "EngineDisposeFailure", "sqlstate": None}
                exit_code = 1
        if state["cleanup_failures"] and report["status"] == "PASS":
            report["status"] = "FAIL"
            report["stage"] = "cleanup"
            if report["failure"] is None:
                report["failure"] = {"exception_type": "ConnectionCleanupFailure", "sqlstate": None}
            exit_code = 1
        if started is not None:
            report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report["db"] = state
        write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
