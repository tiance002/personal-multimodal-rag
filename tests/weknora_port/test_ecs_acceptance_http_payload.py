"""Offline driver integration only: no SSH, application, DB or Provider calls."""
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from scripts import ecs_acceptance_http_payload as payload


@pytest.fixture
def prepared(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    summary = tmp_path / "summary.json"
    marker = tmp_path / "invocations.txt"
    finished_marker = tmp_path / "driver-finished.txt"
    driver = tmp_path / "frozen-driver.py"
    receipt = tmp_path / "execution.json"
    receipt.write_bytes(b'{"status":"CLOSED","entries":[]}\n')
    complete = {"status": payload.COMPLETE, "completed_submissions": 2,
                "totals": {"attempts": 3, "settled": 3, "unknown": 0, "unsettled": 0,
                           "actual_tokens": 1234, "held_tokens": 1400}}

    def freeze(summary_value=complete, exit_code=0, delay=0):
        source = "\n".join([
            "import json, sys, time",
            "from pathlib import Path",
            "sys.stdout.reconfigure(encoding='utf-8')",
            f"marker = Path({str(marker)!r})",
            "with marker.open('a', encoding='utf8') as stream: stream.write('executed\\n')",
            "for number in (1,2):",
            "    print(json.dumps({'case_id':'中文题-'+str(number), 'status':'HTTP_COMPLETED_REVIEW_REQUIRED', 'completed':number, 'retry':False}, ensure_ascii=False), flush=True)",
            f"    if number == 1: time.sleep({delay})",
            "print('原始日志 🌟', flush=True)",
            "print('driver-stderr', file=sys.stderr, flush=True)",
            f"summary = {summary_value!r}",
            f"if summary is not None: Path({str(summary)!r}).write_text(json.dumps(summary), encoding='utf8')",
            f"Path({str(finished_marker)!r}).write_text('driver complete', encoding='utf8')",
            f"raise SystemExit({exit_code})", "",
        ])
        driver.write_text(source, encoding="utf8")
        return hashlib.sha256(driver.read_bytes()).hexdigest()

    return SimpleNamespace(output=output, driver=driver, summary=summary, marker=marker,
                           finished_marker=finished_marker,
                           receipt=receipt, complete=complete, freeze=freeze)


def execute(prepared, digest, receipts=()):
    return payload.run_payload(prepared.driver, digest, prepared.summary, receipts, output=prepared.output)


def result(prepared):
    return json.loads((prepared.output / "result.json").read_text(encoding="utf8"))


def test_frozen_two_case_driver_progress_unicode_and_receipts(prepared, capfd, monkeypatch):
    digest = prepared.freeze()
    original_receipt = prepared.receipt.read_bytes()
    calls = []
    real_popen = payload.subprocess.Popen

    def record_popen(argv, **options):
        calls.append((argv, options))
        return real_popen(argv, **options)

    monkeypatch.setattr(payload.subprocess, "Popen", record_popen)
    assert execute(prepared, digest, [prepared.receipt]) == 0
    saved = result(prepared)
    assert saved["runtime_complete"] is True
    assert saved["completed_submissions"] == 2
    assert saved["unknown"] == saved["unsettled"] == 0
    assert saved["totals"] == prepared.complete["totals"]
    assert saved["actual_tokens"] == 1234 and saved["held_tokens"] == 1400
    assert saved["quality_review_required"] is True
    assert saved["acceptance_verdict"] == "NOT_EVALUATED"
    checkpoint = json.loads((prepared.output / "checkpoint.json").read_text(encoding="utf8"))
    assert checkpoint["completed_submissions"] == 2
    assert checkpoint["case_id"] == "中文题-2"
    logs = capfd.readouterr()
    assert "中文题-1" in logs.out and "原始日志 🌟" in logs.out
    assert "driver-stderr" in logs.err
    assert prepared.receipt.read_bytes() == original_receipt
    snapshots = json.loads((prepared.output / "receipt-snapshots.json").read_text(encoding="utf8"))
    assert not snapshots["errors"] and len(snapshots["files"]) == 2
    assert all(item["stable_during_copy"] for item in snapshots["files"])
    for item in snapshots["files"]:
        copied = prepared.output / item["snapshot"]
        assert hashlib.sha256(copied.read_bytes()).hexdigest() == item["sha256"]
    assert calls[0][0] == [sys.executable, str(prepared.driver)]
    assert calls[0][1]["stdin"] == payload.subprocess.DEVNULL
    assert calls[0][1]["stderr"] is None
    assert calls[0][1]["close_fds"] is True


def test_cli_uses_existing_job_output_environment(prepared, monkeypatch):
    digest = prepared.freeze()
    monkeypatch.setenv("ECS_ACCEPTANCE_OUTPUT_DIR", str(prepared.output))
    assert payload.main(["--driver", str(prepared.driver), "--driver-sha256", digest,
                         "--summary", str(prepared.summary), "--receipt", str(prepared.receipt)]) == 0
    assert result(prepared)["runtime_complete"] is True


def test_failure_after_case_progress_keeps_failure_and_selected_snapshot(prepared):
    digest = prepared.freeze(summary_value=None, exit_code=7)
    original = prepared.receipt.read_bytes()
    assert execute(prepared, digest, [prepared.receipt]) == 1
    saved = result(prepared)
    assert saved["runtime_complete"] is False and saved["driver_exit_code"] == 7
    assert saved["status"] == "INCOMPLETE_UNCONFIRMED"
    assert (prepared.output / "checkpoint.json").exists()
    assert prepared.receipt.read_bytes() == original
    snapshots = json.loads((prepared.output / "receipt-snapshots.json").read_text(encoding="utf8"))
    assert any(item["source"] == str(prepared.receipt) for item in snapshots["files"])
    assert snapshots["errors"]


def test_second_execution_cannot_repeat_driver_even_after_success(prepared):
    digest = prepared.freeze()
    assert execute(prepared, digest) == 0
    first_result = (prepared.output / "result.json").read_bytes()
    with pytest.raises(FileExistsError):
        execute(prepared, digest)
    assert prepared.marker.read_text(encoding="utf8").splitlines() == ["executed"]
    assert (prepared.output / "result.json").read_bytes() == first_result


def test_second_execution_cannot_repeat_after_failure(prepared):
    digest = prepared.freeze(summary_value=None, exit_code=7)
    assert execute(prepared, digest) == 1
    with pytest.raises(FileExistsError):
        execute(prepared, digest)
    assert prepared.marker.read_text(encoding="utf8").splitlines() == ["executed"]


def test_frozen_driver_hash_mismatch_never_executes(prepared):
    digest = prepared.freeze()
    prepared.driver.write_text("raise RuntimeError('changed')", encoding="utf8")
    with pytest.raises(payload.PayloadError, match="driver_sha256"):
        execute(prepared, digest)
    assert not prepared.marker.exists()
    assert not (prepared.output / "http-driver-started.json").exists()


def test_old_complete_summary_cannot_be_reused_or_mutated(prepared, monkeypatch):
    digest = prepared.freeze()
    old = json.dumps(prepared.complete).encode("utf8")
    prepared.summary.write_bytes(old)
    launches = []
    monkeypatch.setattr(payload.subprocess, "Popen", lambda *a, **kw: launches.append((a, kw)))
    with pytest.raises(payload.PayloadError, match="must not preexist"):
        execute(prepared, digest)
    assert launches == [] and not prepared.marker.exists()
    assert prepared.summary.read_bytes() == old
    assert not (prepared.output / "http-driver-started.json").exists()


@pytest.mark.parametrize("change", [
    "missing_summary", "missing_totals", "missing_unknown", "missing_unsettled",
    "missing_attempts", "missing_settled", "missing_completed", "unknown",
    "unsettled", "unsettled_attempt", "stopped", "bool_counter", "bool_completed",
])
def test_missing_or_unconfirmed_summary_never_claims_complete(prepared, change):
    value = json.loads(json.dumps(prepared.complete))
    if change == "missing_summary":
        value = None
    elif change == "missing_totals":
        value.pop("totals")
    elif change.startswith("missing_"):
        name = change.removeprefix("missing_")
        (value if name == "completed" else value["totals"]).pop("completed_submissions" if name == "completed" else name)
    elif change in {"unknown", "unsettled"}:
        value["totals"][change] = 1
    elif change == "unsettled_attempt":
        value["totals"]["settled"] = 2
    elif change == "stopped":
        value["status"] = "STOPPED"
    elif change == "bool_counter":
        value["totals"]["unknown"] = False
    elif change == "bool_completed":
        value["completed_submissions"] = True
    digest = prepared.freeze(value)
    assert execute(prepared, digest) == 1
    assert result(prepared)["runtime_complete"] is False


def test_exit_failure_overrides_complete_summary(prepared):
    digest = prepared.freeze(exit_code=7)
    assert execute(prepared, digest) == 1
    assert result(prepared)["driver_exit_code"] == 7
    assert result(prepared)["runtime_complete"] is False


def test_selected_secret_is_not_exported(prepared):
    digest = prepared.freeze()
    secret = prepared.receipt.with_name("credentials.env")
    secret.write_text("SHOULD_NOT_EXPORT", encoding="utf8")
    assert execute(prepared, digest, [secret]) == 1
    assert result(prepared)["runtime_complete"] is False
    assert not list((prepared.output / "receipts").glob("*credentials.env"))


def test_selected_symlink_is_not_exported(prepared):
    digest = prepared.freeze()
    link = prepared.receipt.with_name("linked-receipt.json")
    try:
        link.symlink_to(prepared.receipt)
    except OSError as exc:
        pytest.skip("test symlinks unavailable: " + str(exc))
    assert execute(prepared, digest, [link]) == 1
    assert not list((prepared.output / "receipts").glob("*linked-receipt.json"))


def test_snapshot_failure_does_not_mask_driver_failure(prepared, monkeypatch):
    digest = prepared.freeze(summary_value=None, exit_code=9)

    def unavailable(*args, **kwargs):
        raise OSError("snapshot unavailable")

    monkeypatch.setattr(payload, "_snapshot", unavailable)
    assert execute(prepared, digest, [prepared.receipt]) == 1
    saved = result(prepared)
    assert saved["driver_exit_code"] == 9
    assert saved["error_type"] == "PayloadError"
    assert all(item["error"] == "snapshot unavailable" for item in saved["snapshot_errors"])


def test_checkpoint_failure_drains_and_waits_for_single_original_driver(prepared, monkeypatch):
    digest = prepared.freeze(delay=0.2)
    real_durable, real_popen = payload.durable, payload.subprocess.Popen
    children = []

    def checkpoint_unavailable(path, value):
        if path.name == "checkpoint.json":
            raise OSError("checkpoint persistence unavailable")
        return real_durable(path, value)

    def record_child(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(payload, "durable", checkpoint_unavailable)
    monkeypatch.setattr(payload.subprocess, "Popen", record_child)
    assert execute(prepared, digest) == 1
    assert prepared.finished_marker.read_text(encoding="utf8") == "driver complete"
    assert len(children) == 1 and children[0].returncode == 0
    assert prepared.marker.read_text(encoding="utf8").splitlines() == ["executed"]
    saved = result(prepared)
    assert saved["runtime_complete"] is False
    assert saved["driver_exit_code"] == 0
    assert len(saved["observation_errors"]) == 2
