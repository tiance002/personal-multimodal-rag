from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from eval_center.contracts import bundle_digest, normalize_bundle


class ExperimentConflictError(ValueError):
    """An experiment ID already exists with different validated content."""

    code = "experiment_content_conflict"

    def __init__(self) -> None:
        super().__init__("experiment ID is already registered with different content")


class StoreSchemaError(RuntimeError):
    """The local registry uses an unsupported or incomplete schema."""


@contextmanager
def _connect(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    try:
        yield connection
    finally:
        connection.close()


def initialize_database(path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version in (1, 2):
            expected = {"experiments", "experiment_cases", "experiment_errors"}
            existing = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if not expected <= existing:
                raise StoreSchemaError("schema v1 is incomplete")
            if version == 2:
                if 'experiment_verification' not in existing:
                    raise StoreSchemaError('schema v2 is incomplete')
                return
        if version not in (0, 1):
            raise StoreSchemaError("unsupported registry schema version")

        connection.execute("BEGIN IMMEDIATE")
        try:
            statements = (
                """CREATE TABLE IF NOT EXISTS experiments (
                    experiment_id TEXT PRIMARY KEY,
                    digest TEXT NOT NULL UNIQUE,
                    git_sha TEXT NOT NULL,
                    dataset_version TEXT NOT NULL,
                    corpus_hash TEXT NOT NULL,
                    config_hash TEXT NOT NULL,
                    model_profile TEXT NOT NULL,
                    embedding_profile TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT NOT NULL,
                    os_family TEXT NOT NULL,
                    python_version TEXT NOT NULL,
                    architecture TEXT NOT NULL,
                    evaluation_mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    sample_count INTEGER NOT NULL CHECK(sample_count >= 0),
                    config_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    imported_at TEXT NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS experiments_imported_idx ON experiments(imported_at DESC, experiment_id)",
                """CREATE TABLE IF NOT EXISTS experiment_cases (
                    experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id) ON DELETE CASCADE,
                    case_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT,
                    error_code TEXT,
                    metrics_json TEXT NOT NULL,
                    PRIMARY KEY(experiment_id, case_id)
                )""",
                """CREATE TABLE IF NOT EXISTS experiment_errors (
                    error_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id) ON DELETE CASCADE,
                    case_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    error_code TEXT NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS experiment_errors_run_idx ON experiment_errors(experiment_id, error_id)",
                """CREATE TABLE IF NOT EXISTS experiment_verification (
                    experiment_id TEXT PRIMARY KEY REFERENCES experiments(experiment_id),
                    schema_version INTEGER NOT NULL,
                    validation_status TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    runtime_json TEXT NOT NULL,
                    metric_counts_json TEXT NOT NULL,
                    statistics_json TEXT NOT NULL
                )""",
            )
            for statement in statements:
                connection.execute(statement)
            connection.execute("PRAGMA user_version=2")
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def import_bundle(path: Path, bundle: dict[str, object]) -> dict[str, str]:
    normalized = normalize_bundle(bundle)
    digest = bundle_digest(normalized)
    initialize_database(Path(path))
    manifest = normalized["manifest"]
    environment = manifest["environment"]
    experiment_id = manifest["experiment_id"]

    with _connect(Path(path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                "SELECT digest FROM experiments WHERE experiment_id=?", (experiment_id,)
            ).fetchone()
            if existing is not None:
                if existing["digest"] != digest:
                    raise ExperimentConflictError()
                connection.rollback()
                return {"status": "unchanged", "digest": digest}

            connection.execute(
                """INSERT INTO experiments (
                    experiment_id, digest, git_sha, dataset_version, corpus_hash, config_hash,
                    model_profile, embedding_profile, started_at, ended_at, os_family,
                    python_version, architecture, evaluation_mode, status, sample_count,
                    config_json, metrics_json, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    experiment_id, digest, manifest["git_sha"], manifest["dataset_version"],
                    manifest["corpus_hash"], manifest["config_hash"], manifest["model_profile"],
                    manifest["embedding_profile"], manifest["started_at"], manifest["ended_at"],
                    environment["os_family"], environment["python_version"],
                    environment["architecture"], manifest["evaluation_mode"], manifest["status"],
                    manifest["sample_count"], json.dumps(normalized["config"], sort_keys=True),
                    json.dumps(normalized["metrics"], sort_keys=True),
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
            connection.executemany(
                """INSERT INTO experiment_cases
                   (experiment_id, case_id, status, stage, error_code, metrics_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        experiment_id, case["case_id"], case["status"], case.get("stage"),
                        case.get("error_code"), json.dumps(case["metrics"], sort_keys=True),
                    )
                    for case in normalized["cases"]
                ],
            )
            connection.executemany(
                """INSERT INTO experiment_errors (experiment_id, case_id, stage, error_code)
                   VALUES (?, ?, ?, ?)""",
                [
                    (experiment_id, error["case_id"], error["stage"], error["error_code"])
                    for error in normalized["errors"]
                ],
            )
            if normalized['schema_version'] == 2:
                connection.execute('INSERT INTO experiment_verification VALUES (?,2,?,?,?,?,?)',
                    (experiment_id,'verified',json.dumps(manifest,sort_keys=True),
                     json.dumps(normalized['runtime'],sort_keys=True),
                     json.dumps(normalized['metric_counts'],sort_keys=True),
                     json.dumps({case['case_id']:case['statistics'] for case in normalized['cases']},sort_keys=True)))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return {"status": "imported", "digest": digest}


def _row_summary(row: sqlite3.Row) -> dict[str, object]:
    return {
        "experiment_id": row["experiment_id"],
        "digest": row["digest"],
        "git_sha": row["git_sha"],
        "dataset_version": row["dataset_version"],
        "corpus_hash": row["corpus_hash"],
        "config_hash": row["config_hash"],
        "model_profile": row["model_profile"],
        "embedding_profile": row["embedding_profile"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "environment": {
            "os_family": row["os_family"],
            "python_version": row["python_version"],
            "architecture": row["architecture"],
        },
        "evaluation_mode": row["evaluation_mode"],
        "status": row["status"],
        "sample_count": row["sample_count"],
        "metrics": json.loads(row["metrics_json"]),
        "validation_status": row['validation_status'],
        "schema_version": row['bundle_schema_version'] or 1,
        "runtime": json.loads(row['runtime_json']) if row['runtime_json'] else None,
        "metric_counts": json.loads(row['metric_counts_json']) if row['metric_counts_json'] else {},
        "gold_set_hash": json.loads(row['manifest_json']).get('gold_set_hash') if row['manifest_json'] else None,
        "index_version": json.loads(row['manifest_json']).get('index_version') if row['manifest_json'] else None,
    }


_VERIFIED_SELECT = """SELECT e.*, COALESCE(v.validation_status,'unverified') AS validation_status,
    v.schema_version AS bundle_schema_version, v.manifest_json, v.runtime_json,
    v.metric_counts_json, v.statistics_json FROM experiments e
    LEFT JOIN experiment_verification v ON v.experiment_id=e.experiment_id"""


def list_experiments(path: Path, *, limit: int, offset: int, include_unverified: bool = False) -> list[dict[str, object]]:
    if type(limit) is not int or type(offset) is not int or not 1 <= limit <= 200 or offset < 0:
        raise ValueError("invalid pagination")
    with _connect(Path(path)) as connection:
        rows = connection.execute(
            _VERIFIED_SELECT + ('' if include_unverified else " WHERE v.validation_status='verified'") +
            ' ORDER BY e.imported_at DESC,e.experiment_id LIMIT ? OFFSET ?',
            (limit, offset),
        ).fetchall()
    return [_row_summary(row) for row in rows]


def get_experiment(path: Path, experiment_id: str) -> dict[str, object] | None:
    try:
        parsed_id = uuid.UUID(experiment_id)
    except (ValueError, AttributeError, TypeError):
        return None
    if str(parsed_id) != experiment_id:
        return None
    with _connect(Path(path)) as connection:
        row = connection.execute(
            _VERIFIED_SELECT + ' WHERE e.experiment_id=?', (experiment_id,)
        ).fetchone()
        if row is None:
            return None
        cases = connection.execute(
            """SELECT case_id, status, stage, error_code, metrics_json
               FROM experiment_cases WHERE experiment_id=? ORDER BY case_id""",
            (experiment_id,),
        ).fetchall()
        errors = connection.execute(
            """SELECT case_id, stage, error_code FROM experiment_errors
               WHERE experiment_id=? ORDER BY error_id""",
            (experiment_id,),
        ).fetchall()
    result = _row_summary(row)
    result["config"] = json.loads(row["config_json"])
    result["cases"] = [
        {
            "case_id": item["case_id"],
            "status": item["status"],
            "stage": item["stage"],
            "error_code": item["error_code"],
            "metrics": json.loads(item["metrics_json"]),
        }
        for item in cases
    ]
    result["errors"] = [dict(item) for item in errors]
    if row['statistics_json']:
        statistics = json.loads(row['statistics_json'])
        for case in result['cases']:
            case['statistics'] = statistics[case['case_id']]
    return result


def compare_experiments(path: Path, experiment_ids: list[str]) -> dict[str, object]:
    if not isinstance(experiment_ids, list) or not 2 <= len(experiment_ids) <= 4:
        raise ValueError("comparison requires two to four experiment IDs")
    if any(not isinstance(experiment_id, str) for experiment_id in experiment_ids):
        raise ValueError("invalid experiment ID")
    if len(set(experiment_ids)) != len(experiment_ids):
        raise ValueError("duplicate experiment ID")
    records = [get_experiment(path, experiment_id) for experiment_id in experiment_ids]
    if any(record is None for record in records):
        raise ValueError("experiment not found")
    concrete = [record for record in records if record is not None]
    mismatches: dict[str, list[str]] = {}
    for field in ("dataset_version", "corpus_hash", "evaluation_mode"):
        values = [str(record[field]) for record in concrete]
        if len(set(values)) != 1:
            mismatches[field] = values

    if any(record['validation_status'] != 'verified' for record in concrete):
        mismatches['validation_status'] = [str(record['validation_status']) for record in concrete]
    for field in ('gold_set_hash','model_profile','embedding_profile'):
        values=[str(record[field]) for record in concrete]
        if len(set(values))!=1: mismatches[field]=values
    models=[json.dumps(record['runtime']['models'],sort_keys=True) if record['runtime'] else None for record in concrete]
    if len(set(models)) != 1: mismatches['model_identifiers'] = models
    if mismatches:
        return {'experiment_ids':experiment_ids,'comparable':False,'mismatches':mismatches,'metrics':{}}

    metric_names = sorted({name for record in concrete for name in record["metrics"]})
    metric_comparisons: dict[str, dict[str, list[float | None]]] = {}
    for name in metric_names:
        values: list[float | None] = [
            float(record["metrics"][name]) if record["metrics"].get(name) is not None else None
            for record in concrete
        ]
        first = next((value for value in values if value is not None), None)
        deltas = [None if value is None or first is None else value - first for value in values]
        percentages=[None if value is None or first is None or first==0 else (value-first)/abs(first)*100 for value in values]
        metric_comparisons[name] = {"values": values, "delta_from_first": deltas,
            'percent_change_from_first':percentages,
            'evaluated_counts':[record['metric_counts'].get(name,{}).get('evaluated',0) for record in concrete]}

    return {
        "experiment_ids": experiment_ids,
        "comparable": not mismatches,
        "mismatches": mismatches,
        "metrics": metric_comparisons,
    }
