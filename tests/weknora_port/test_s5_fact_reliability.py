"""S5 experiment construction contracts; no models, services or database.

These tests check source mappings and one-factor fixtures, not model quality.
The committed case pack makes the tests independent of external run directories.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path

from openpyxl import Workbook
import pytest

from scripts import s5_fact_reliability as s5


def _workbook(headers, rows, *, merged=None):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Measurements"
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    if merged:
        sheet.merge_cells(merged)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _verified(data, **kwargs):
    return s5.verified_header_parse(data, hashlib.sha256(data).hexdigest(), **kwargs)


def test_body_identity_is_canonical_and_replace_preserves_non_source_fields():
    body = {"model": "configured-model", "temperature": 0.3, "messages": [
        {"role": "system", "content": "Use supplied facts."},
        {"role": "user", "content": "SOURCE\nSite: East Pier\nQUESTION"},
    ]}
    expected = hashlib.sha256(json.dumps(
        body, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode()).hexdigest()
    assert s5.body_sha(body) == expected
    assert s5.body_sha(dict(reversed(list(body.items())))) == expected
    original = deepcopy(body)
    result = s5.replace_once(body, "Site: East Pier", "Site: East Pier,Unit: EP-17")
    assert body == original
    assert result["messages"][0] == body["messages"][0]
    assert result["messages"][1]["content"] == "SOURCE\nSite: East Pier,Unit: EP-17\nQUESTION"
    assert {k: v for k, v in result.items() if k != "messages"} == {
        k: v for k, v in body.items() if k != "messages"}


@pytest.mark.parametrize("messages,before", [
    ([{"role": "user", "content": "source source"}], "source"),
    ([{"role": "user", "content": "source"},
      {"role": "user", "content": "source"}], "source"),
    ([{"role": "system", "content": "source"}], "source"),
    ([{"role": "assistant", "content": "source"}], "source"),
    ([{"role": "user", "content": "different"}], "source"),
    ([{"role": "user", "content": "different"}], ""),
])
def test_replace_declines_ambiguous_missing_and_non_user_spans(messages, before):
    with pytest.raises(ValueError):
        s5.replace_once({"messages": messages}, before, "replacement")


def test_explicit_headers_follow_actual_columns_and_source_cells():
    data = _workbook(["Unit", "Site", "CheckedOn", "Reading"], [
        ["EP-17", "East Pier", "2031-02-03", 24],
        ["WH-22", "West Hall", "2031-02-04", 51],
    ])
    result = _verified(data)
    assert result["text"] == (
        "Unit: EP-17,Site: East Pier,CheckedOn: 2031-02-03,Reading: 24\n"
        "Unit: WH-22,Site: West Hall,CheckedOn: 2031-02-04,Reading: 51\n"
    )
    assert result["sha256"] == hashlib.sha256(result["text"].encode()).hexdigest()
    assert [r["row"] for r in result["rows"]] == [2, 3]
    assert {p["header_cell"] for p in result["cell_proofs"]} == {"A1", "B1", "C1", "D1"}
    unit = next(p for p in result["cell_proofs"] if p["value"] == "WH-22")
    assert (unit["header"], unit["header_cell"], unit["value_cell"]) == ("Unit", "A1", "A3")
    for proof in result["cell_proofs"]:
        assert result["text"][proof["text_start"]:proof["text_end"]] == (
            f"{proof['header']}: {proof['value']}")
    assert result["mapping_kind"] == "DIAGNOSTIC_DERIVED_TEXT_TO_SOURCE_CELLS_NOT_TABLE_ROW_PROOF"


def test_missing_unit_column_stays_missing_instead_of_becoming_site():
    data = _workbook(["Site", "Reading", "CheckedOn"], [
        ["East Pier", 24, "2031-02-03"],
        ["West Hall", 51, "2031-02-04"],
    ])
    result = _verified(data)
    assert "Site: West Hall,Reading: 51,CheckedOn: 2031-02-04" in result["text"]
    assert "Unit:" not in result["text"]
    assert {p["header"] for p in result["cell_proofs"]} == {"Site", "Reading", "CheckedOn"}


@pytest.mark.parametrize("headers,rows,merged", [
    (["Site", "Site"], [["East Pier", "EP-17"]], None),
    (["Site", " site "], [["East Pier", "EP-17"]], None),
    (["Site", None], [["East Pier", "EP-17"]], None),
    (["Site", 17], [["East Pier", "EP-17"]], None),
    (["Site", "Unit"], [["East Pier", "EP-17"]], "A2:B2"),
    (["Site", "Unit"], [["East Pier", "=1+2"]], None),
    (["Site", "Unit"], [["East Pier", 'DISPIMG("asset",1)']], None),
])
def test_ambiguous_headers_merged_cells_formulas_and_image_functions_are_declined(headers, rows, merged):
    with pytest.raises(ValueError):
        _verified(_workbook(headers, rows, merged=merged))


@pytest.mark.parametrize("header_row", [0, 2, True, "1"])
def test_header_selection_must_be_explicit_first_integer_row(header_row):
    data = _workbook(["Site", "Unit"], [["East Pier", "EP-17"]])
    with pytest.raises(ValueError, match="EXPLICIT_FIRST_HEADER_REQUIRED"):
        _verified(data, header_row=header_row)


def test_source_sha_mismatch_is_declined_before_any_derived_evidence():
    data = _workbook(["Site", "Unit"], [["East Pier", "EP-17"]])
    with pytest.raises(ValueError, match="SOURCE_SHA_MISMATCH"):
        s5.verified_header_parse(data, "0" * 64)


def test_literal_excerpt_keeps_exact_offsets_and_excludes_generated_caption():
    source = (
        "原文 unit ZX-17.\r\n"
        "<image_caption>generated turbine unit subtype</image_caption>\n"
        "<image_ocr>\nUNIT: ZX-17\nREADING: 7\n</image_ocr>\n"
        "![image](assets/card.png)\n<!-- source marker -->\n"
    )
    result = s5.literal_excerpt(source)
    assert result["all_spans_exact"]
    assert result["not_a_fact_or_language_quality_pass"]
    assert result["input_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert result["spans"]
    for span in result["spans"]:
        assert source[span["start"]:span["end"]] == span["text"]
    kept = "\n".join(s["text"] for s in result["spans"])
    assert "原文 unit ZX-17." in kept
    assert "UNIT: ZX-17" in kept
    assert "turbine" not in kept and "assets/card.png" not in kept
    assert {s["kind"] for s in result["spans"]} == {"input_text", "ocr_recognition"}


def test_literal_excerpt_is_bounded_and_does_not_fill_omitted_facts():
    source = "unit ZX-17\nReading: 7\nmissing site\n"
    result = s5.literal_excerpt(source, max_chars=11)
    assert sum(len(s["text"]) for s in result["spans"]) <= 11
    assert all(s["text"] in source for s in result["spans"])
    assert all(source[s["start"]:s["end"]] == s["text"] for s in result["spans"])
    with pytest.raises(ValueError, match="INVALID_EXCERPT_LIMIT"):
        s5.literal_excerpt(source, max_chars=0)


def test_committed_pack_freezes_eight_cases_and_only_one_candidate_factor():
    path = Path(__file__).resolve().parents[2] / "docs/evaluation/evidence/S5_FACT_RELIABILITY_CASES_20261019.json"
    pack = json.loads(path.read_text(encoding="utf-8"))
    assert pack["scope"] == "OFFLINE_FIXED_CONTEXT_DIAGNOSTICS_NOT_RAG_ACCEPTANCE"
    assert pack["authorization"] == "CLOSED_NOT_REOPENED"
    assert not pack["production_changed"]
    assert (pack["new_provider_calls"], pack["new_tokens"], pack["new_unknown"]) == (0, 0, 0)
    assert pack["requested_future_new_calls"] == 12
    cases = {case["id"]: case for case in pack["cases"]}
    assert set(cases) == {
        "summary_failure", "summary_correct", "summary_missing_identity", "summary_distractor",
        "table_failure", "table_correct", "table_missing_unit", "table_column_distractor",
    }
    history_count = 0
    for case in cases.values():
        baseline, candidate = case["baseline"], case["candidate"]
        assert baseline["sha256"] == s5.body_sha(baseline["body"])
        assert candidate["sha256"] == s5.body_sha(candidate["body"])
        assert candidate["response"] == "NOT_RUN_BLOCKED_AUTHORIZATION"
        assert case["quality_status"] == "UNVERIFIED_REQUIRES_NEW_AUTHORIZED_RESPONSE"
        assert {k: v for k, v in baseline["body"].items() if k != "messages"} == {
            k: v for k, v in candidate["body"].items() if k != "messages"}
        if case["id"].startswith("summary_"):
            assert candidate["body"]["messages"][0]["content"] == (
                baseline["body"]["messages"][0]["content"] + s5.SUMMARY_CONSTRAINT)
            assert candidate["body"]["messages"][1:] == baseline["body"]["messages"][1:]
        else:
            assert candidate["body"]["messages"][0] == baseline["body"]["messages"][0]
            # The question, metadata and language suffix remain frozen.
            before = baseline["body"]["messages"][-1]["content"].split("## User request", 1)[1]
            after = candidate["body"]["messages"][-1]["content"].split("## User request", 1)[1]
            assert before == after
        history_count += isinstance(baseline["response"], dict)
    assert history_count == 4
    assert 8 + len(cases) - history_count == pack["requested_future_new_calls"]


def test_real_plan_excludes_saved_controls_and_fits_approved_fence():
    from scripts.s5_fact_validation import planned_calls
    path = Path(__file__).resolve().parents[2] / "docs/evaluation/evidence/S5_FACT_RELIABILITY_CASES_20261019.json"
    pack = json.loads(path.read_text(encoding="utf-8"))
    plan = planned_calls(pack)
    assert len(plan) == len({(identifier, arm) for identifier, arm, _ in plan}) == 12
    assert sum(arm == "candidate" for _, arm, _ in plan) == 8
    assert all(not isinstance(item["response"], dict) for _, _, item in plan)
    reserve = 0
    for _, _, item in plan:
        body = item["body"]
        assert body["model"] == "Qwen/Qwen3.5-4B"
        assert body["enable_thinking"] is False and body["max_tokens"] == 1024
        wire = json.dumps(body, ensure_ascii=False, separators=(",", ":"),
                          sort_keys=True, allow_nan=False).encode()
        reserve += len(wire) + body["max_tokens"] + 1024
    assert reserve == 60069 < 65000
