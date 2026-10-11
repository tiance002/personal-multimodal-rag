from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from eval_center.public_langfuse import PublicTelemetryError, write_and_readback_public_event


class _FakeClient:
    def __init__(self):
        self.trace_context = None
        self.observation = SimpleNamespace(id="a" * 16, metadata=None)
        self.observation.update = lambda **kwargs: setattr(self.observation, "metadata", kwargs.get("metadata"))
        self.created_scores = []
        self.flush_count = 0
        self.query_trace_id = None

    @contextmanager
    def start_as_current_observation(self, **kwargs):
        self.trace_context = kwargs["trace_context"]
        yield self.observation

    def create_score(self, **kwargs):
        self.created_scores.append(kwargs)

    def flush(self):
        self.flush_count += 1

    class _API:
        def __init__(self, outer):
            self.outer = outer
            self.scores_v3 = SimpleNamespace(
                get_many_v3=lambda **kwargs: {"data": [{"id": kwargs["id"]}]}
            )
            self.observations = SimpleNamespace(get_many=self._get_observations)

        def _get_observations(self, **kwargs):
            self.outer.query_trace_id = kwargs["trace_id"]
            return SimpleNamespace(data=[{"id": "a" * 16}])

    @property
    def api(self):
        return self._API(self)


def test_public_langfuse_event_writes_scores_flushes_and_reads_back():
    client = _FakeClient()
    receipt = write_and_readback_public_event(
        client,
        run_id="run-public-1",
        qid="public-qid-1",
        arm="DYNAMIC",
        metadata={
            "dataset": "longbench-zh", "dataset_version": "v1", "status": "succeeded",
            "corpus_sha256": "a" * 64, "pool_sha256": "b" * 64,
            "qrels_sha256": "c" * 64, "gold_sha256": "d" * 64,
            "index_sha256": "e" * 64, "prompt_sha256": "f" * 64,
            "context_sha256": "1" * 64, "envelope_sha256": "2" * 64,
            "input_tokens": 8, "output_tokens": 4,
        },
        scores={"answer_character_f1": 0.75, "citation_readback_rate": 1.0},
    )
    assert receipt["write"] == "FLUSHED"
    assert receipt["readback"] == "OBSERVATIONS_QUERIED"
    assert receipt["observations_count"] == 1
    assert receipt["scores_count"] == 2
    assert client.flush_count == 1
    assert client.query_trace_id == receipt["trace_id"]
    assert len(client.created_scores) == 2
    assert all("input" not in event and "output" not in event for event in client.created_scores)


def test_public_langfuse_rejects_question_or_answer_content():
    with pytest.raises(PublicTelemetryError, match="telemetry_field_not_allowlisted"):
        write_and_readback_public_event(
            _FakeClient(), run_id="run-public-1", qid="qid", arm="DYNAMIC",
            metadata={"dataset": "longbench-zh", "question": "private text"},
        )


def test_public_langfuse_does_not_accept_unknown_usage_as_zero():
    with pytest.raises(PublicTelemetryError, match="telemetry_usage_invalid"):
        write_and_readback_public_event(
            _FakeClient(), run_id="run-public-1", qid="qid", arm="CHEAP_ONLY",
            metadata={"dataset": "scifact", "input_tokens": "UNKNOWN"},
        )
