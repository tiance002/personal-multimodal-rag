"""Local per-turn metrics. No source, prompt, answer or credential text."""
from __future__ import annotations

import time
from collections.abc import Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from copy import deepcopy
from typing import Any, Iterator

from backend.app.application.retrieval_provenance import pass_metadata
from backend.app.application.model_usage import ModelCall, UsageCapture, capture_usage

NOT_AVAILABLE = "NOT_AVAILABLE"
_CURRENT: ContextVar[RunMetrics | None] = ContextVar("rag_run_metrics", default=None)


class RunMetrics:
    def __init__(self, query_id: str, usage: UsageCapture) -> None:
        self.query_id = query_id
        self.usage = usage
        self.started = time.perf_counter()
        self.retrievals: list[dict[str, Any]] = []
        self.retrieval_merges: list[dict[str, Any]] = []
        self.context_sizes: dict[str, int] = {}
        self.context_latency = 0.0
        self.rendered_context_chars: int | None = None
        self.quality: dict[str, Any] | None = None
        self.call_paths: dict[int, str] = {}
        self.providers: set[str] = set()
        self.cloud_called = False
        self.hardening: dict[str, Any] = {}

    def record_retrieval(self, result: Any) -> None:
        self.retrievals.append({
            "call_index": len(self.retrievals) + 1,
            "mode": result.retrieval_mode, "reason": list(result.route_reason),
            "vector_candidate_count": len(result.candidate_rankings.get("vector", ())),
            "keyword_candidate_count": len(result.candidate_rankings.get("keyword", ())),
            "retrieved_chunk_ids": [item.chunk.chunk_id for item in result.items],
            "latency_ms": result.latency_ms,
            "stage_latency_ms": result.stage_latency_ms,
            "embedding_cache_hit": result.embedding_cache_hit,
            "degradation_flags": list(result.degradation_flags),
            "pass_metadata": pass_metadata(result),
        })

    def record_retrieval_merge(self, provenance: dict[str, Any] | None) -> None:
        if provenance is not None:
            row = deepcopy(provenance)
            recorded = self.retrievals[-2:]
            for index, source in enumerate(row["passes"]):
                body = {k: v for k, v in source.items() if k not in ("pass_index", "role")}
                call = recorded[index] if len(recorded) == 2 else None
                source["retrieval_call_index"] = (call["call_index"]
                    if call is not None and call["pass_metadata"] == body else None)
            self.retrieval_merges.append(row)

    def record_context(self, items: Sequence[Any], *, latency_ms: float = 0.0,
                       rendered_chars: int | None = None, quality: Any | None = None) -> None:
        for item in items:
            self.context_sizes[item.chunk.chunk_id] = len(item.chunk.content)
        self.context_latency += latency_ms
        if rendered_chars is not None:
            self.rendered_context_chars = rendered_chars
        if quality is not None:
            self.quality = asdict(quality)

    def note_provider(self, *, path: str, provider: str) -> None:
        self.providers.add(provider)
        self.cloud_called |= path == "CLOUD"

    def record_generation(self, *, path: str, provider: str, model: str,
                          usage_start: int, latency_ms: float, status: str) -> None:
        self.note_provider(path=path, provider=provider)
        if len(self.usage.calls) == usage_start:
            # An injected provider did not instrument usage. Keep it unknown.
            self.usage.calls.append(ModelCall("answer", "business", model, status, latency_ms, None, None))
        for index in range(usage_start, len(self.usage.calls)):
            self.call_paths[index] = path

    def snapshot(self, *, citations: Sequence[str], error: str | None) -> dict[str, Any]:
        calls = [(index, call) for index, call in enumerate(self.usage.calls) if call.stage in ("query", "answer")]
        def total(attribute: str) -> int | str:
            values = [getattr(call, attribute) for _, call in calls]
            return sum(values) if all(value is not None for value in values) else NOT_AVAILABLE
        input_tokens, output_tokens = total("input_tokens"), total("output_tokens")
        modes = list(dict.fromkeys(row["mode"] for row in self.retrievals))
        cache = [row["embedding_cache_hit"] for row in self.retrievals if row["embedding_cache_hit"] is not None]
        models = list(dict.fromkeys(call.model for _, call in calls))
        return {
            "schema_version": "rag-run-metrics-v1", "query_id": self.query_id,
            "retrieval_mode": modes[0] if len(modes) == 1 else ("mixed" if modes else NOT_AVAILABLE),
            "retrieval_route_reason": list(dict.fromkeys(reason for row in self.retrievals for reason in row["reason"])),
            "vector_candidate_count": sum(row["vector_candidate_count"] for row in self.retrievals),
            "keyword_candidate_count": sum(row["keyword_candidate_count"] for row in self.retrievals),
            "retrieved_chunk_ids": list(dict.fromkeys(chunk for row in self.retrievals for chunk in row["retrieved_chunk_ids"])),
            "selected_context_chunk_ids": list(self.context_sizes),
            "retrieval_latency_ms": sum(row["latency_ms"] or 0 for row in self.retrievals),
            "context_build_latency_ms": self.context_latency,
            "local_model_latency_ms": sum(call.latency_ms for index, call in calls if self.call_paths.get(index, "LOCAL") == "LOCAL"),
            "cloud_model_latency_ms": sum(call.latency_ms for index, call in calls if self.call_paths.get(index) == "CLOUD"),
            "total_latency_ms": (time.perf_counter() - self.started) * 1000,
            "embedding_cache_hit": any(cache) if cache else NOT_AVAILABLE,
            "context_chars": sum(self.context_sizes.values()),
            "context_chars_basis": "unique_selected_source_text",
            "rendered_context_chars": self.rendered_context_chars if self.rendered_context_chars is not None else NOT_AVAILABLE,
            "context_tokens": NOT_AVAILABLE,
            "llm_provider": sorted(self.providers) if self.providers else ("local" if calls else "none"),
            "llm_model": models[0] if len(models) == 1 else (models if models else "none"),
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens if isinstance(input_tokens, int) and isinstance(output_tokens, int) else NOT_AVAILABLE,
            "cloud_called": self.cloud_called, "citations": list(citations), "error": error,
            "retry_count": 0, "retry_count_basis": "no_application_or_provider_retries_configured",
            "model_calls": [asdict(call) | {"path": self.call_paths.get(index, "LOCAL")} for index, call in enumerate(self.usage.calls)],
            "retrieval_calls": list(self.retrievals),
            "retrieval_merges": deepcopy(self.retrieval_merges),
            "evidence_quality": self.quality if self.quality is not None else NOT_AVAILABLE,
            "answer_hardening": self.hardening,
        }


def current_metrics() -> RunMetrics | None:
    return _CURRENT.get()


@contextmanager
def collect_metrics(query_id: str) -> Iterator[RunMetrics]:
    with capture_usage() as usage:
        metrics = RunMetrics(query_id, usage)
        token = _CURRENT.set(metrics)
        try:
            yield metrics
        finally:
            _CURRENT.reset(token)
