"""One-shot, bounded read-only diagnosis for the frozen neighbor seed.

This does not import or invoke the repository, modify its query, or expose any
chunk body. It inspects only the frozen seed and chunk indexes seed_index +/- 1
inside the approved KB/document/version scope, in one verified read-only txn.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import sys
import time
import traceback
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row


HOST = "127.0.0.1"
PORT = 25438
DATABASE = "rag"
CONNECT_TIMEOUT_SECONDS = 3
STATEMENT_TIMEOUT_MS = 3000
LOCK_TIMEOUT_MS = 500
IDLE_TIMEOUT_MS = 5000
TOTAL_DEADLINE_SECONDS = 15
MAX_CANDIDATES_PER_INDEX = 4

KB_ID = "d78f3434-0e8f-40bf-afcb-f1c2c311ff51"
DOCUMENT_ID = "87c2d4e7-5a24-49e4-9c0c-e8694d61d2f3"
VERSION_ID = "5f93ddd1-79bc-48e2-95c0-51a76f05ae46"
SEED_ID = "9515a7b9-5322-4b9e-bf3a-d41ca7af458c"
SEED_HASH = "3a54b12a314e9429afbd1db82d89084c2f8d01dc2dd2d9914ca2746a7bd0cf93"
FROZEN_READER_SQL_SHA256 = "524b3aa3d8f59791beceb25007923511a5894307fc6cbc9e8605b1f5355610da"

READ_ONLY_VERIFY_SQL = """
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

# The seed CTE repeats the frozen seed's eligibility checks. The two lateral
# targets are exactly the neighboring chunk indexes; no other document/version
# can enter the candidate join. Candidate text and raw locator JSON are never
# selected into the report.
DIAGNOSTIC_SQL = """
WITH seed_rows AS (
    SELECT c.chunk_index AS seed_index, c.section_id AS seed_section_id
    FROM chunks c
    JOIN documents d
      ON d.id = c.document_id AND d.active_version_id = c.version_id
    JOIN document_versions dv
      ON dv.id = c.version_id AND dv.document_id = c.document_id
    JOIN knowledge_bases kb
      ON kb.id = c.knowledge_base_id
    JOIN document_sections seed_section
      ON seed_section.id = c.section_id
     AND seed_section.document_id = c.document_id
     AND seed_section.version_id = c.version_id
     AND seed_section.knowledge_base_id = c.knowledge_base_id
    WHERE c.id = %(seed_id)s::uuid
      AND c.knowledge_base_id = %(kb_id)s::uuid
      AND c.document_id = %(document_id)s::uuid
      AND c.version_id = %(version_id)s::uuid
      AND c.content_sha256 = %(seed_hash)s
      AND d.knowledge_base_id = c.knowledge_base_id
      AND d.deleted_at IS NULL
      AND dv.index_status = 'ready'
      AND kb.deleted_at IS NULL
      AND c.chunk_type = 'text'
      AND c.start_pos >= seed_section.start_pos
      AND c.start_pos < c.end_pos
      AND c.end_pos <= seed_section.end_pos
      AND c.locator->'start' = to_jsonb(c.start_pos)
      AND c.locator->'end' = to_jsonb(c.end_pos)
      AND (
          (c.locator->>'kind' IN ('text', 'markdown')
           AND seed_section.page_start IS NULL
           AND seed_section.page_end IS NULL)
          OR
          (c.locator->>'kind' = 'pdf'
           AND seed_section.page_start > 0
           AND seed_section.page_start = seed_section.page_end
           AND c.locator->'page' = to_jsonb(seed_section.page_start))
      )
),
seed_meta AS (
    SELECT COUNT(*)::integer AS seed_count,
           MIN(seed_index)::integer AS seed_index,
           (ARRAY_AGG(seed_section_id))[1] AS seed_section_id
    FROM seed_rows
),
targets AS (
    SELECT offsets.target_offset,
           seed_meta.seed_index + offsets.target_offset AS target_index,
           seed_meta.seed_section_id
    FROM seed_meta
    CROSS JOIN (VALUES (-1), (1)) AS offsets(target_offset)
    WHERE seed_meta.seed_count = 1
),
candidate_rows AS (
    SELECT t.target_offset,
           t.target_index,
           t.seed_section_id,
           c.id AS candidate_id,
           c.knowledge_base_id AS candidate_kb_id,
           c.document_id AS candidate_document_id,
           c.version_id AS candidate_version_id,
           c.section_id AS candidate_section_id,
           c.chunk_type,
           c.start_pos,
           c.end_pos,
           c.locator,
           d.id AS document_row_id,
           d.knowledge_base_id AS document_kb_id,
           d.active_version_id,
           d.deleted_at,
           dv.id AS version_row_id,
           dv.index_status,
           section.id AS same_scope_section_id,
           section.start_pos AS section_start_pos,
           section.end_pos AS section_end_pos,
           section.page_start,
           section.page_end,
           COUNT(c.id) OVER (PARTITION BY t.target_offset)::integer
               AS candidate_count,
           ROW_NUMBER() OVER (
               PARTITION BY t.target_offset
               ORDER BY c.id NULLS FIRST
           )::integer AS candidate_row
    FROM targets t
    LEFT JOIN chunks c
      ON c.knowledge_base_id = %(kb_id)s::uuid
     AND c.document_id = %(document_id)s::uuid
     AND c.version_id = %(version_id)s::uuid
     AND c.chunk_index = t.target_index
    LEFT JOIN documents d
      ON d.id = c.document_id
    LEFT JOIN document_versions dv
      ON dv.id = c.version_id AND dv.document_id = c.document_id
    LEFT JOIN document_sections section
      ON section.id = c.section_id
     AND section.knowledge_base_id = c.knowledge_base_id
     AND section.document_id = c.document_id
     AND section.version_id = c.version_id
),
flags AS (
    SELECT target_offset,
           target_index,
           candidate_id IS NOT NULL AS candidate_exists,
           candidate_count,
           candidate_row,
           CASE WHEN candidate_id IS NULL THEN NULL
                ELSE candidate_section_id IS NOT DISTINCT FROM seed_section_id
           END AS same_section,
           CASE WHEN candidate_id IS NULL THEN NULL
                ELSE document_row_id IS NOT NULL
           END AS document_found,
           CASE WHEN candidate_id IS NULL OR document_row_id IS NULL THEN NULL
                ELSE document_kb_id = candidate_kb_id
           END AS document_scope_match,
           CASE WHEN candidate_id IS NULL OR document_row_id IS NULL THEN NULL
                ELSE active_version_id = candidate_version_id
           END AS current_version,
           CASE WHEN candidate_id IS NULL OR document_row_id IS NULL THEN NULL
                ELSE deleted_at IS NOT NULL
           END AS document_deleted,
           CASE WHEN candidate_id IS NULL THEN NULL
                ELSE version_row_id IS NOT NULL
           END AS document_version_found,
           CASE WHEN candidate_id IS NULL OR version_row_id IS NULL THEN NULL
                ELSE index_status = 'ready'
           END AS ready,
           CASE WHEN candidate_id IS NULL THEN NULL
                ELSE same_scope_section_id IS NOT NULL
           END AS section_scope_match,
           CASE WHEN candidate_id IS NULL THEN NULL
                ELSE chunk_type = 'text'
           END AS chunk_type_text,
           CASE WHEN candidate_id IS NULL OR same_scope_section_id IS NULL THEN NULL
                ELSE start_pos >= section_start_pos
           END AS chunk_start_within_section,
           CASE WHEN candidate_id IS NULL THEN NULL
                ELSE start_pos < end_pos
           END AS chunk_range_positive,
           CASE WHEN candidate_id IS NULL OR same_scope_section_id IS NULL THEN NULL
                ELSE end_pos <= section_end_pos
           END AS chunk_end_within_section,
           CASE WHEN candidate_id IS NULL THEN NULL
                ELSE COALESCE(locator->'start' = to_jsonb(start_pos), FALSE)
           END AS locator_start_match,
           CASE WHEN candidate_id IS NULL THEN NULL
                ELSE COALESCE(locator->'end' = to_jsonb(end_pos), FALSE)
           END AS locator_end_match,
           CASE WHEN candidate_id IS NULL OR same_scope_section_id IS NULL THEN NULL
                ELSE COALESCE(
                    (locator->>'kind' IN ('text', 'markdown')
                     AND page_start IS NULL AND page_end IS NULL)
                    OR
                    (locator->>'kind' = 'pdf'
                     AND page_start > 0 AND page_start = page_end
                     AND locator->'page' = to_jsonb(page_start)),
                    FALSE
                )
           END AS page_locator_bounds_match
    FROM candidate_rows
),
evaluated AS (
    SELECT *,
           (
               candidate_exists
               AND same_section IS TRUE
               AND document_found IS TRUE
               AND document_scope_match IS TRUE
               AND current_version IS TRUE
               AND document_deleted IS FALSE
               AND document_version_found IS TRUE
               AND ready IS TRUE
               AND section_scope_match IS TRUE
               AND chunk_type_text IS TRUE
               AND chunk_start_within_section IS TRUE
               AND chunk_range_positive IS TRUE
               AND chunk_end_within_section IS TRUE
               AND locator_start_match IS TRUE
               AND locator_end_match IS TRUE
               AND page_locator_bounds_match IS TRUE
           ) AS reader_eligible_now
    FROM flags
    WHERE candidate_row <= %(max_candidates_per_index)s
),
with_reasons AS (
    SELECT *,
           CASE
             WHEN NOT candidate_exists THEN ARRAY['CANDIDATE_ABSENT']::text[]
             ELSE array_remove(ARRAY[
               CASE WHEN same_section IS FALSE THEN 'DIFFERENT_SECTION' END,
               CASE WHEN document_found IS FALSE THEN 'DOCUMENT_ROW_MISSING' END,
               CASE WHEN document_scope_match IS FALSE THEN 'DOCUMENT_KB_SCOPE_MISMATCH' END,
               CASE WHEN current_version IS FALSE THEN 'NOT_CURRENT_VERSION' END,
               CASE WHEN document_deleted IS TRUE THEN 'DOCUMENT_DELETED' END,
               CASE WHEN document_version_found IS FALSE THEN 'DOCUMENT_VERSION_ROW_MISSING' END,
               CASE WHEN ready IS FALSE THEN 'INDEX_NOT_READY' END,
               CASE WHEN section_scope_match IS FALSE THEN 'SECTION_MISSING_OR_OUT_OF_SCOPE' END,
               CASE WHEN chunk_type_text IS FALSE THEN 'CHUNK_TYPE_NOT_TEXT' END,
               CASE WHEN chunk_start_within_section IS FALSE THEN 'CHUNK_START_OUTSIDE_SECTION' END,
               CASE WHEN chunk_range_positive IS FALSE THEN 'NON_POSITIVE_CHUNK_RANGE' END,
               CASE WHEN chunk_end_within_section IS FALSE THEN 'CHUNK_END_OUTSIDE_SECTION' END,
               CASE WHEN locator_start_match IS FALSE THEN 'LOCATOR_START_MISMATCH' END,
               CASE WHEN locator_end_match IS FALSE THEN 'LOCATOR_END_MISMATCH' END,
               CASE WHEN page_locator_bounds_match IS FALSE THEN 'PAGE_OR_KIND_LOCATOR_BOUNDARY_MISMATCH' END
             ]::text[], NULL)
           END AS exclusion_reason_codes,
           CASE WHEN candidate_count > 1
                THEN ARRAY['DUPLICATE_CANDIDATE_INDEX']::text[]
                ELSE ARRAY[]::text[]
           END AS integrity_notes
    FROM evaluated
)
SELECT seed_meta.seed_count,
       with_reasons.target_offset,
       with_reasons.target_index,
       with_reasons.candidate_exists,
       with_reasons.candidate_count,
       with_reasons.candidate_row,
       with_reasons.same_section,
       with_reasons.document_found,
       with_reasons.document_scope_match,
       with_reasons.current_version,
       with_reasons.ready,
       with_reasons.document_deleted,
       with_reasons.document_version_found,
       with_reasons.section_scope_match,
       with_reasons.chunk_type_text,
       with_reasons.chunk_start_within_section,
       with_reasons.chunk_range_positive,
       with_reasons.chunk_end_within_section,
       with_reasons.locator_start_match,
       with_reasons.locator_end_match,
       with_reasons.page_locator_bounds_match,
       with_reasons.reader_eligible_now,
       with_reasons.exclusion_reason_codes,
       with_reasons.integrity_notes
FROM seed_meta
LEFT JOIN with_reasons ON TRUE
ORDER BY with_reasons.target_offset, with_reasons.candidate_row
"""

PARAMETERS = {
    "seed_id": SEED_ID,
    "kb_id": KB_ID,
    "document_id": DOCUMENT_ID,
    "version_id": VERSION_ID,
    "seed_hash": SEED_HASH,
    "max_candidates_per_index": MAX_CANDIDATES_PER_INDEX,
}


class DiagnosticStop(RuntimeError):
    def __init__(self, code: str, stage: str) -> None:
        super().__init__(code)
        self.code = code
        self.stage = stage


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sanitized_sqlstate(exc: BaseException) -> str | None:
    value = getattr(exc, "sqlstate", None)
    return value if isinstance(value, str) else None


def json_safe_row(row: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "target_offset",
        "target_index",
        "candidate_exists",
        "candidate_count",
        "same_section",
        "document_found",
        "document_scope_match",
        "current_version",
        "ready",
        "document_deleted",
        "document_version_found",
        "section_scope_match",
        "chunk_type_text",
        "chunk_start_within_section",
        "chunk_range_positive",
        "chunk_end_within_section",
        "locator_start_match",
        "locator_end_match",
        "page_locator_bounds_match",
        "reader_eligible_now",
    )
    safe = {field: row.get(field) for field in fields}
    safe["exclusion_reason_codes"] = row.get("exclusion_reason_codes") or []
    safe["integrity_notes"] = row.get("integrity_notes") or []
    return safe


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report_dir = Path(args.report_dir).expanduser()
    report_path = report_dir / "report.json"
    if report_dir.exists():
        print(json.dumps({
            "status": "BLOCKED",
            "stage": "report_dir",
            "reason": "directory_exists",
        }, ensure_ascii=False))
        return 2

    report_dir.mkdir(parents=True, exist_ok=False)
    script_path = Path(__file__).resolve()
    report: dict[str, Any] = {
        "schema": "context-neighbor-db-diagnostic/v1",
        "status": "RUNNING",
        "stage": "credential_prompt",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "target": {"host": HOST, "port": PORT, "database": DATABASE},
        "scope": {
            "knowledge_base_id": KB_ID,
            "document_id": DOCUMENT_ID,
            "version_id": VERSION_ID,
            "seed_id": SEED_ID,
            "seed_content_sha256": SEED_HASH,
            "offsets": [-1, 1],
            "frozen_reader_sql_sha256": FROZEN_READER_SQL_SHA256,
        },
        "limits": {
            "connection_attempts": 1,
            "max_candidates_per_index_reported": MAX_CANDIDATES_PER_INDEX,
            "connect_timeout_seconds": CONNECT_TIMEOUT_SECONDS,
            "statement_timeout_ms": STATEMENT_TIMEOUT_MS,
            "lock_timeout_ms": LOCK_TIMEOUT_MS,
            "idle_timeout_ms": IDLE_TIMEOUT_MS,
            "total_deadline_seconds": TOTAL_DEADLINE_SECONDS,
        },
        "credential_prompt": "PENDING_MASKED_LOCAL_INPUT",
        "script_sha256": hashlib.sha256(script_path.read_bytes()).hexdigest(),
        "diagnostic_sql_sha256": sha256_text(DIAGNOSTIC_SQL),
        "diagnostic_sql": DIAGNOSTIC_SQL.strip(),
        "read_only_verify_sql": READ_ONLY_VERIFY_SQL.strip(),
        "parameters": PARAMETERS,
        "transaction_commands": [
            "BEGIN TRANSACTION READ ONLY",
            f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'",
            f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT_MS}ms'",
            f"SET LOCAL idle_in_transaction_session_timeout = '{IDLE_TIMEOUT_MS}ms'",
            "ROLLBACK",
        ],
        "db": {
            "connection_attempts": 0,
            "connections_opened": 0,
            "connections_closed": 0,
            "transaction_started": False,
            "read_only_checks": [],
            "diagnostic_query_count": 0,
            "rollbacks": 0,
            "cleanup_failures": [],
        },
        "seed_revalidation": "NOT_RUN",
        "candidate_results": [],
        "findings": None,
        "failure": None,
    }
    write_report(report_path, report)

    conn: psycopg.Connection | None = None
    deadline: float | None = None
    started: float | None = None
    exit_code = 1
    phase = "credential_prompt"
    try:
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            raise DiagnosticStop("MASKED_TTY_REQUIRED", phase)
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            username = getpass.getpass("PostgreSQL username (hidden): ")
            password = getpass.getpass("PostgreSQL password (hidden): ")
        if not username or not password:
            raise DiagnosticStop("EMPTY_CREDENTIAL_INPUT", phase)

        phase = "connect"
        report["stage"] = phase
        started = time.monotonic()
        deadline = started + TOTAL_DEADLINE_SECONDS
        report["credential_prompt"] = "MASKED_LOCAL_INPUT_COMPLETED"
        report["db"]["connection_attempts"] = 1
        conn = psycopg.connect(
            host=HOST,
            port=PORT,
            dbname=DATABASE,
            user=username,
            password=password,
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            autocommit=True,
            row_factory=dict_row,
            options=(
                "-c default_transaction_read_only=on"
                f" -c statement_timeout={STATEMENT_TIMEOUT_MS}"
                f" -c lock_timeout={LOCK_TIMEOUT_MS}"
                f" -c idle_in_transaction_session_timeout={IDLE_TIMEOUT_MS}"
            ),
        )
        del username, password
        report["db"]["connections_opened"] = 1

        phase = "readonly_transaction"
        report["stage"] = phase
        conn.execute("BEGIN TRANSACTION READ ONLY")
        report["db"]["transaction_started"] = True
        conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'")
        conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT_MS}ms'")
        conn.execute(f"SET LOCAL idle_in_transaction_session_timeout = '{IDLE_TIMEOUT_MS}ms'")
        check = conn.execute(READ_ONLY_VERIFY_SQL).fetchone()
        if check is None:
            raise DiagnosticStop("READ_ONLY_CHECK_RETURNED_NO_ROW", phase)
        check_json = dict(check)
        report["db"]["read_only_checks"].append(check_json)
        expected_check_names = (
            "read_only",
            "statement_timeout_positive",
            "statement_timeout_bounded",
            "lock_timeout_exact",
            "idle_timeout_exact",
        )
        if not all(check_json.get(key) is True for key in expected_check_names):
            raise DiagnosticStop("READ_ONLY_OR_TIMEOUT_VERIFICATION_FAILED", phase)

        if deadline is None or time.monotonic() > deadline:
            raise DiagnosticStop("TOTAL_DEADLINE_EXCEEDED_BEFORE_QUERY", phase)
        phase = "candidate_diagnostic"
        report["stage"] = phase
        report["db"]["diagnostic_query_count"] = 1
        rows = conn.execute(DIAGNOSTIC_SQL, PARAMETERS).fetchall()
        if time.monotonic() > deadline:
            raise DiagnosticStop("TOTAL_DEADLINE_EXCEEDED_AFTER_QUERY", phase)
        if not rows:
            raise DiagnosticStop("DIAGNOSTIC_QUERY_RETURNED_NO_META_ROW", phase)

        seed_count = int(rows[0]["seed_count"])
        report["seed_revalidation"] = {
            "eligible_seed_rows": seed_count,
            "expected": 1,
        }
        if seed_count != 1:
            raise DiagnosticStop("FROZEN_SEED_REVALIDATION_FAILED", "seed_revalidation")

        grouped: dict[int, list[dict[str, Any]]] = {-1: [], 1: []}
        for row in rows:
            offset = row.get("target_offset")
            if offset in grouped:
                grouped[int(offset)].append(row)
        if any(not grouped[offset] for offset in (-1, 1)):
            raise DiagnosticStop("EXPECTED_ADJACENT_TARGET_MISSING", "candidate_diagnostic")

        candidate_results: list[dict[str, Any]] = []
        truncated = False
        for offset in (-1, 1):
            candidate_rows = grouped[offset]
            candidate_count = int(candidate_rows[0]["candidate_count"])
            if candidate_count > MAX_CANDIDATES_PER_INDEX:
                truncated = True
            for row in candidate_rows:
                safe = json_safe_row(row)
                safe["candidate_count"] = candidate_count
                safe["candidate_output_truncated"] = truncated and candidate_count > MAX_CANDIDATES_PER_INDEX
                candidate_results.append(safe)
        report["candidate_results"] = candidate_results
        if truncated:
            raise DiagnosticStop("CANDIDATE_OUTPUT_LIMIT_EXCEEDED", "candidate_diagnostic")

        eligible_now = [row for row in candidate_results if row["reader_eligible_now"] is True]
        present_count = sum(
            int(offset_rows[0]["candidate_count"])
            for offset_rows in grouped.values()
            if offset_rows and int(offset_rows[0]["candidate_count"]) > 0
        )
        if present_count == 0:
            neighbor_state = "NO_CANDIDATE_AT_EITHER_ADJACENT_INDEX"
        elif eligible_now:
            neighbor_state = "ELIGIBLE_CANDIDATE_PRESENT_IN_THIS_DIAGNOSTIC_SNAPSHOT"
        else:
            neighbor_state = "CANDIDATES_PRESENT_BUT_EXCLUDED_BY_RECORDED_CONDITIONS"
        report["findings"] = {
            "neighbor_state": neighbor_state,
            "supported_query_defect": [],
            "temporal_comparison_note": (
                "This batch uses a later transaction than v6; a currently eligible row "
                "would be a discrepancy signal, but this batch alone cannot prove the "
                "database snapshot was unchanged between runs."
                if eligible_now
                else "No currently eligible adjacent row was observed; this does not prove "
                     "whether a candidate was absent or ineligible at the earlier v6 snapshot."
            ),
            "eligible_candidate_count_now": len(eligible_now),
            "candidate_rows_present": present_count,
        }
        if eligible_now:
            report["findings"]["unresolved_discrepancy"] = (
                "At least one candidate currently satisfies the frozen reader predicates "
                "although v6 returned none; data-state change versus query behavior remains "
                "unresolved across separate batches."
            )
        report["status"] = "DIAGNOSTIC_COMPLETE"
        report["stage"] = "complete"
        exit_code = 0
    except DiagnosticStop as exc:
        report["status"] = "BLOCKED"
        report["stage"] = exc.stage
        report["failure"] = {
            "exception_type": type(exc).__name__,
            "reason_code": exc.code,
            "sqlstate": None,
        }
        exit_code = 2
    except BaseException as exc:
        report["status"] = "INTERRUPTED" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "FAIL"
        report["stage"] = phase
        report["failure"] = {
            "exception_type": type(exc).__name__,
            "sqlstate": sanitized_sqlstate(exc),
            "traceback_frames": [
                {"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
                for frame in traceback.extract_tb(exc.__traceback__)[-12:]
            ],
        }
        exit_code = 2 if report["status"] == "INTERRUPTED" else 1
    finally:
        if conn is not None:
            if report["db"]["transaction_started"]:
                try:
                    conn.execute("ROLLBACK")
                    report["db"]["rollbacks"] = 1
                except Exception as exc:
                    report["db"]["cleanup_failures"].append({
                        "operation": "rollback",
                        "exception_type": type(exc).__name__,
                        "sqlstate": sanitized_sqlstate(exc),
                    })
            try:
                conn.close()
                if conn.closed:
                    report["db"]["connections_closed"] = 1
            except Exception as exc:
                report["db"]["cleanup_failures"].append({
                    "operation": "close",
                    "exception_type": type(exc).__name__,
                    "sqlstate": sanitized_sqlstate(exc),
                })
                report["status"] = "FAIL"
                report["stage"] = "cleanup"
                exit_code = 1
        if started is not None:
            report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report["db"]["connection_attempts"] = min(
            int(report["db"]["connection_attempts"]), 1
        )
        write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
