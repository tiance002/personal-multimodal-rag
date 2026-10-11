from __future__ import annotations

import sqlite3
import uuid

from eval_center.public_benchmark_cli import _parser, _run


def _args(*items):
    return _parser().parse_args([*items])


def test_paired_simulate_cli_persists_72_attempts_and_correct_gold_denominators(tmp_path):
    state = tmp_path / "simulated-ledger.sqlite3"
    run_id = f"test-p7-sim-{uuid.uuid4().hex}"
    args = _args("--phase", "paired-simulate", "--run-id", run_id,
                 "--state-path", str(state))
    first = _run(args)
    second = _run(args)
    assert first["status"] == "SIMULATED_PAIRED_COHORT_COMPLETED"
    assert second["status"] == first["status"]
    assert second["results_sha256"] == first["results_sha256"]
    assert first["attempt_counts"] == {
        "CHEAP_ONLY": 24, "DYNAMIC": 24, "EXPENSIVE_ONLY": 24,
    }
    assert first["simulated_quickchain_arm_attempts"] == 72
    assert first["simulated_deepseek_transport_calls"] == 24
    assert first["provider_calls"] == 0
    assert first["answer_gold_denominator"] == 8
    assert first["answer_not_scored_no_gold_rows"] == 16
    assert first["retrieval_qrels_available_rows"] == 24
    assert first["retrieval_scored_rows"] == 0
    connection = sqlite3.connect(state)
    try:
        assert connection.execute(
            "SELECT status,COUNT(*) FROM paired_attempts WHERE run_id=? GROUP BY status",
            (run_id,),
        ).fetchall() == [("simulated", 72)]
        assert connection.execute(
            "SELECT COUNT(*) FROM paired_case_results WHERE run_id=?", (run_id,),
        ).fetchone()[0] == 24
    finally:
        connection.close()


def test_paired_run_missing_injection_refuses_before_any_provider_send():
    result = _run(_args("--phase", "paired-run"))
    assert result["status"] == "BLOCKED_ZERO_SEND"
    assert result["container"] == "EXPLICIT_RUNTIME_INJECTION_REQUIRED"
    assert result["quickchain_invocations"] == 0
    assert result["provider_calls"] == result["send_attempts"] == 0
    assert result["database_queries"] == 0
    assert "owner_grants" in result["missing_gates"]
    assert "frozen_index_manifest" in result["missing_gates"]
