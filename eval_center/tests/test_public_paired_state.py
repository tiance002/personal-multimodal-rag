from __future__ import annotations

import unittest
from pathlib import Path
import uuid
import os

from eval_center.paired_state import (
    ARMS,
    PairedContractError,
    begin_attempt,
    finish_attempt,
    load_frozen_cohort,
    load_case_results,
    pending_attempts,
    register_run,
    save_case_result,
    validate_paired_results,
)


def _sha(char: str) -> str:
    return char * 64


def _temporary_state_path() -> tuple[Path, Path]:
    root = Path(os.environ.get("RAG_PAIRED_TEST_ROOT",
        str(Path(__file__).resolve().parents[2] / "var/reports/v6-cohort-runtime-r1/test-scratch")))
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{uuid.uuid4().hex}.sqlite3", root


def _remove_temporary_state(path: Path, root: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = Path(str(path) + suffix)
        if candidate.is_file():
            candidate.unlink()
    try:
        root.rmdir()
    except OSError:
        pass


def _row(qid: str, arm: str, *, context: str = "b") -> dict:
    return {
        "qid": qid, "arm": arm, "dataset": "longbench-zh", "dataset_version": "v1",
        "corpus_sha256": _sha("a"), "pool_sha256": _sha("b"),
        "qrels_sha256": _sha("c"), "gold_sha256": _sha("d"),
        "index_sha256": _sha("e"), "prompt_sha256": _sha("f"),
        "context_sha256": _sha(context),
    }


class PublicPairedStateTests(unittest.TestCase):
    def test_frozen_dev24_is_balanced(self):
        path = Path(__file__).resolve().parents[2] / "var/reports/v6-first-evaluation-20261010/cohort_dev24.jsonl"
        if not path.is_file():
            self.skipTest("v6 dev24 artifact is absent")
        cases = load_frozen_cohort(path)
        self.assertEqual(len(cases), 24)
        self.assertEqual({name: sum(row["dataset"] == name for row in cases)
                          for name in ("scifact", "miracl-zh", "longbench-zh")},
                         {"scifact": 8, "miracl-zh": 8, "longbench-zh": 8})

    def test_triplet_requires_identical_frozen_inputs(self):
        rows = [_row("qid-1", arm) for arm in ARMS]
        result = validate_paired_results(rows)
        self.assertEqual((result["qid_count"], result["arm_count"], result["result_count"]), (1, 3, 3))
        with self.assertRaisesRegex(PairedContractError, "paired_input_mismatch"):
            validate_paired_results([_row("qid-1", arm, context="9" if arm == "DYNAMIC" else "b")
                                     for arm in ARMS])
        with self.assertRaisesRegex(PairedContractError, "paired_arm_missing"):
            validate_paired_results([_row("qid-1", arm) for arm in ARMS[:2]])

    def test_checkpoint_is_written_before_dispatch_and_blocks_retries(self):
        state, root = _temporary_state_path()
        try:
            register_run(state, run_id="run-1", cohort_sha256=_sha("a"),
                         source_manifest_sha256=_sha("b"), code_sha="1" * 40,
                         deadline_utc="2026-10-10T04:25:44Z", now_utc="2026-10-09T20:25:44Z")
            begin_attempt(state, run_id="run-1", qid="qid-1", arm="CHEAP_ONLY",
                          input_identity_sha256=_sha("c"), now_utc="2026-10-09T20:30:00Z")
            self.assertEqual(len(pending_attempts(state, "run-1")), 1)
            with self.assertRaisesRegex(PairedContractError, "attempt_already_exists_no_retry"):
                begin_attempt(state, run_id="run-1", qid="qid-1", arm="CHEAP_ONLY",
                              input_identity_sha256=_sha("c"), now_utc="2026-10-09T20:31:00Z")
            finish_attempt(state, run_id="run-1", qid="qid-1", arm="CHEAP_ONLY",
                           status="unknown", error_code="usage_unknown", now_utc="2026-10-09T20:32:00Z")
            self.assertEqual(pending_attempts(state, "run-1"), [])
            with self.assertRaisesRegex(PairedContractError, "attempt_already_exists_no_retry"):
                begin_attempt(state, run_id="run-1", qid="qid-1", arm="CHEAP_ONLY",
                              input_identity_sha256=_sha("c"), now_utc="2026-10-09T20:33:00Z")
        finally:
            _remove_temporary_state(state, root)

    def test_deadline_blocks_dispatch(self):
        state, root = _temporary_state_path()
        try:
            register_run(state, run_id="run-2", cohort_sha256=_sha("a"),
                         source_manifest_sha256=_sha("b"), code_sha="2" * 40,
                         deadline_utc="2026-10-10T04:25:44Z", now_utc="2026-10-09T20:25:44Z")
            with self.assertRaisesRegex(PairedContractError, "run_deadline_expired"):
                begin_attempt(state, run_id="run-2", qid="qid-1", arm="DYNAMIC",
                              input_identity_sha256=_sha("c"), now_utc="2026-10-10T04:25:44Z")
        finally:
            _remove_temporary_state(state, root)

    def test_paired_case_result_requires_three_terminal_attempts_and_is_immutable(self):
        state, root = _temporary_state_path()
        try:
            register_run(state, run_id="run-store", cohort_sha256=_sha("a"),
                         source_manifest_sha256=_sha("b"), code_sha="3" * 40,
                         deadline_utc="2026-10-10T04:25:44Z", now_utc="2026-10-09T20:25:44Z")
            for arm in ARMS:
                begin_attempt(state, run_id="run-store", qid="qid-store", arm=arm,
                              input_identity_sha256=_sha("c"), now_utc="2026-10-09T20:30:00Z")
                finish_attempt(state, run_id="run-store", qid="qid-store", arm=arm,
                               status="succeeded", result={"answer": "public test"},
                               now_utc="2026-10-09T20:31:00Z")
            case = {"qid": "qid-store", "arms": {arm: {"status": "succeeded"} for arm in ARMS}}
            saved = save_case_result(state, run_id="run-store", result=case)
            self.assertEqual(saved["status"], "saved")
            self.assertEqual(save_case_result(state, run_id="run-store", result=case)["status"], "unchanged")
            self.assertEqual(load_case_results(state, "run-store"), [case])
            changed = {**case, "status": "altered"}
            with self.assertRaisesRegex(PairedContractError, "paired_case_result_conflict"):
                save_case_result(state, run_id="run-store", result=changed)
        finally:
            _remove_temporary_state(state, root)


if __name__ == "__main__":
    unittest.main()
