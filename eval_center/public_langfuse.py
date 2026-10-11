"""Content-free Langfuse adapter for public paired-evaluation telemetry.

This module is deliberately explicit: it never constructs a client or reads
credentials. Callers must pass an already-approved client. Only identifiers,
hashes, numeric usage, and numeric scores cross this boundary; prompt and
answer content are rejected by the allowlist.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any


_ARMS = {"CHEAP_ONLY", "DYNAMIC", "EXPENSIVE_ONLY"}
_HASH_FIELDS = {
    "corpus_sha256", "pool_sha256", "qrels_sha256", "gold_sha256",
    "index_sha256", "prompt_sha256", "context_sha256", "envelope_sha256",
}
_TEXT_FIELDS = {"dataset", "dataset_version", "qid", "arm", "status", "route_model_id"}
_USAGE_FIELDS = {"input_tokens", "output_tokens", "extra_billable_tokens", "cost_microusd"}
from eval_center.scoring_mappers import NUMERIC_SCORE_FIELDS

_SCORE_FIELDS = {"answer_exact_match", "answer_character_f1", "citation_readback_rate"} | NUMERIC_SCORE_FIELDS
_SHA = re.compile(r"[0-9a-f]{64}\Z")


class PublicTelemetryError(ValueError):
    """Invalid or incomplete content-free telemetry payload."""


def _stable_id(namespace: str, value: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"p7-v6:{namespace}:{value}").hex


def _validate_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, float]]:
    if not isinstance(payload, dict):
        raise PublicTelemetryError("telemetry_payload_invalid")
    allowed = _TEXT_FIELDS | _HASH_FIELDS | _USAGE_FIELDS | _SCORE_FIELDS
    if set(payload) - allowed:
        raise PublicTelemetryError("telemetry_field_not_allowlisted")
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        if key in _HASH_FIELDS:
            if not isinstance(value, str) or not _SHA.fullmatch(value):
                raise PublicTelemetryError("telemetry_hash_invalid")
            clean[key] = value
        elif key in _TEXT_FIELDS:
            if not isinstance(value, str) or not value or len(value) > 200:
                raise PublicTelemetryError("telemetry_label_invalid")
            clean[key] = value
        elif key in _USAGE_FIELDS:
            if value is not None and (type(value) is not int or value < 0):
                raise PublicTelemetryError("telemetry_usage_invalid")
            if value is not None:
                clean[key] = value
    if clean.get("arm") not in _ARMS or not clean.get("qid") or not clean.get("dataset"):
        raise PublicTelemetryError("telemetry_identity_missing")
    scores: dict[str, float] = {}
    for key in _SCORE_FIELDS:
        value = payload.get(key)
        if value is None or value == "NOT_SCORED":
            continue
        if type(value) not in (int, float) or not 0 <= float(value) <= 1:
            raise PublicTelemetryError("telemetry_score_invalid")
        scores[key] = float(value)
    return clean, scores


def _records(response: Any) -> list[Any]:
    if isinstance(response, dict):
        data = response.get("data", [])
    else:
        data = getattr(response, "data", [])
    return list(data or [])


def _record_id(record: Any) -> str | None:
    return record.get("id") if isinstance(record, dict) else getattr(record, "id", None)


def write_and_readback_public_event(
    client: Any, *, run_id: str, qid: str, arm: str,
    metadata: dict[str, Any], scores: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Write one content-free trace and scores, flush, then read them back.

    This function performs network I/O only when explicitly passed a live
    Langfuse SDK client. Tests inject a fake client. It does not retry writes:
    an uncertain telemetry result must not replay a generation attempt.
    """
    if not isinstance(run_id, str) or not run_id or not isinstance(qid, str) or not qid:
        raise PublicTelemetryError("telemetry_identity_missing")
    payload = dict(metadata)
    payload.update(qid=qid, arm=arm)
    clean, inferred_scores = _validate_payload(payload)
    if scores is not None:
        if set(scores) - _SCORE_FIELDS:
            raise PublicTelemetryError("telemetry_score_not_allowlisted")
        for key, value in scores.items():
            if type(value) not in (int, float) or not 0 <= float(value) <= 1:
                raise PublicTelemetryError("telemetry_score_invalid")
            inferred_scores[key] = float(value)

    trace_id = _stable_id("trace", f"{run_id}:{qid}:{arm}")
    with client.start_as_current_observation(
        as_type="span", name="p7-public-evaluation-arm",
        trace_context={"trace_id": trace_id},
    ) as span:
        span.update(metadata=clean)
        for name, value in sorted(inferred_scores.items()):
            client.create_score(
                score_id=_stable_id("score", f"{run_id}:{qid}:{arm}:{name}"),
                trace_id=trace_id, name=name, value=float(value), data_type="NUMERIC",
            )
    client.flush()
    observations = client.api.observations.get_many(
        trace_id=trace_id, limit=100, fields="core,basic,usage",
    )
    observation_records = _records(observations)
    if not observation_records:
        raise PublicTelemetryError("langfuse_observation_readback_missing")
    score_records = []
    for name in sorted(inferred_scores):
        score_id = _stable_id("score", f"{run_id}:{qid}:{arm}:{name}")
        response = client.api.scores_v3.get_many_v3(id=score_id)
        records = _records(response)
        if not any(_record_id(record) == score_id for record in records):
            raise PublicTelemetryError("langfuse_score_readback_missing")
        score_records.extend(records)
    return {
        "trace_id": trace_id,
        "write": "FLUSHED",
        "readback": "OBSERVATIONS_QUERIED",
        "observations_count": len(observation_records),
        "scores_count": len(score_records),
    }
