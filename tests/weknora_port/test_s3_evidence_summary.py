import json

from scripts.s3_evidence_summary import summarize


def _saved_run(tmp_path, *, done=True, outcome="RESPONSE_RECEIVED"):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    prefix = evidence / "answer"
    request = {"request_id": "run-1/answer/1/cheap", "capability": "chat", "wire_sha256": "wire-1"}
    prefix.with_suffix(".request.json").write_text(json.dumps(request), encoding="utf-8")
    prefix.with_suffix(".response.json").write_text(json.dumps({"complete": False, "status_code": 200}), encoding="utf-8")
    packet = {"model": "Qwen/Qwen3.5-4B", "choices": [{"index": 0, "delta": {"content": "Hello"}, "finish_reason": "stop"}],
              "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5}}
    body = "data: " + json.dumps(packet) + "\n\n" + ("data: [DONE]\n\n" if done else "")
    prefix.with_suffix(".response.body").write_text(body, encoding="utf-8")
    database = {"calls": [{**request, "outcome": outcome, "finished_at": "2026-10-19T08:00:00Z",
                           "usage": {"status": "REPORTED", "prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5}}],
                "turns": [{"run_id": "run-1", "status": "completed", "assistant_message_id": "answer-1", "q0": "Hello?"}],
                "messages": [{"id": "answer-1", "content": "Hello", "is_completed": True, "turn_status": "completed"}]}
    db_path = tmp_path / "db-current.json"
    db_path.write_text(json.dumps(database), encoding="utf-8")
    return db_path, prefix.with_suffix(".response.json")


def test_done_and_valid_finish_with_settled_ledger_complete_without_transport_eof(tmp_path):
    db_path, metadata_path = _saved_run(tmp_path)
    original_db, original_metadata = db_path.read_bytes(), metadata_path.read_bytes()

    case = summarize(tmp_path)["cases"][0]
    call = case["calls"][0]

    assert call["capture_complete"] is False
    assert call["provider_done_marker_seen"] is True
    assert call["protocol_complete"] is True
    assert case["provider_answer_complete"] is True
    assert case["status"] == "VERIFIED"
    assert case["persistence"]["status"] == "MATCH"
    assert db_path.read_bytes() == original_db
    assert metadata_path.read_bytes() == original_metadata


def test_finish_and_settled_ledger_without_done_do_not_prove_protocol_completion(tmp_path):
    _saved_run(tmp_path, done=False)

    case = summarize(tmp_path)["cases"][0]

    assert case["calls"][0]["provider_done_marker_seen"] is False
    assert case["calls"][0]["protocol_complete"] is False
    assert case["provider_answer_complete"] is False
    assert case["status"] == "UNVERIFIED"


def test_done_body_does_not_settle_or_complete_a_historical_unknown(tmp_path):
    db_path, _ = _saved_run(tmp_path, outcome="UNKNOWN")
    original_db = db_path.read_bytes()

    case = summarize(tmp_path)["cases"][0]
    call = case["calls"][0]

    assert call["provider_done_marker_seen"] is True
    assert call["protocol_complete"] is False
    assert call["settled_provider_usage"] is None
    assert call["ledger_completion_state"] == "TERMINAL_UNKNOWN"
    assert case["provider_answer_complete"] is False
    assert case["status"] == "TERMINAL_UNKNOWN"
    assert db_path.read_bytes() == original_db
