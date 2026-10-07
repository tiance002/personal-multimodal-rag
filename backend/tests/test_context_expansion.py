"""SIMULATED: explicit metadata fixtures, no DB or model authorization proof."""
import hashlib
import importlib
import importlib.util
from dataclasses import replace

import pytest

from backend.app.domain.models import ChunkRecord
from backend.app.domain.scope import Scope


@pytest.fixture
def api():
    class API:
        def __getattr__(self, symbol):
            name = "backend.app.application.context_expansion"
            assert importlib.util.find_spec(name) is not None, "offline expansion contract not implemented"
            return getattr(importlib.import_module(name), symbol)
    return API()


def record(cid, index, text="正文", **changes):
    start = index * 100
    chunk = ChunkRecord(cid, "kb", "doc", "v1", text,
                        {"kind": "text", "start": start, "end": start + len(text), "quote": text},
                        content_sha256=hashlib.sha256(text.encode()).hexdigest())
    return replace(chunk, **changes)


def meta(api, cid, index, **changes):
    value = api.NeighborMetadata(record(cid, index), index, "v1", "ready", False,
                                 "text", "section", "s1", 0, 10000, None)
    return replace(value, **changes)


def expand(api, seeds, candidates, **kwargs):
    options = dict(enabled=True, remaining_items=4, remaining_chars=100,
                   label_overheads=(5, 5, 5, 5), separator_chars=2)
    options.update(kwargs)
    return api.expand_context(Scope.from_ids(["kb"], ["doc"]), seeds, candidates, **options)


def codes(result):
    return {reason.code for reason in result.reasons}


def test_default_disabled_keeps_original_seed_identity(api):
    seed = meta(api, "s", 3)
    result = api.expand_context(Scope.from_ids(["kb"]), [seed], [meta(api, "n", 4)])
    assert result.seeds == (seed.chunk,)
    assert result.seeds[0] is seed.chunk
    assert result.neighbors == ()
    assert codes(result) == {"DISABLED"}


def test_explicit_indices_override_candidate_order_and_ids(api):
    seed = meta(api, "uuid-z", 3)
    before, after = meta(api, "uuid-y", 2), meta(api, "uuid-a", 4)
    result = expand(api, [seed], [after, before, meta(api, "far", 5)])
    assert result.seeds == (seed.chunk,)
    assert result.neighbors == (before.chunk, after.chunk)
    assert result.neighbors[0] is before.chunk
    assert [(p.seed_id, p.neighbor_id, p.offset) for p in result.provenance] == [
        ("uuid-z", "uuid-y", -1), ("uuid-z", "uuid-a", 1)]
    assert result.added_chars == 18
    assert before.chunk.locator["quote"] == "正文"


@pytest.mark.parametrize("changes,reason", [
    ({"chunk_index": None}, "MISSING_METADATA"),
    ({"chunk_index": True}, "INVALID_METADATA"),
    ({"active_version_id": None}, "MISSING_METADATA"),
    ({"active_version_id": "old"}, "INACTIVE_VERSION"),
    ({"index_status": "queued"}, "INDEX_NOT_READY"),
    ({"document_deleted": None}, "MISSING_METADATA"),
    ({"document_deleted": True}, "DOCUMENT_DELETED"),
    ({"chunk_type": "table"}, "UNSUPPORTED_TYPE"),
    ({"chunk_type": "image_ocr"}, "UNSUPPORTED_TYPE"),
    ({"chunk_type": "image_caption"}, "UNSUPPORTED_TYPE"),
    ({"section_id": None}, "MISSING_METADATA"),
    ({"boundary_kind": None}, "MISSING_METADATA"),
    ({"boundary_start": None}, "MISSING_METADATA"),
    ({"section_id": "s2"}, "BOUNDARY_MISMATCH"),
    ({"boundary_end": 900}, "BOUNDARY_MISMATCH"),
])
def test_missing_or_unsafe_neighbor_metadata_never_expands(api, changes, reason):
    seed, neighbor = meta(api, "s", 3), meta(api, "n", 4, **changes)
    result = expand(api, [seed], [neighbor])
    assert result.neighbors == ()
    assert reason in codes(result)
    assert result.seeds[0] is seed.chunk


@pytest.mark.parametrize("changes,reason", [
    ({"knowledge_base_id": "other"}, "OUT_OF_SCOPE"),
    ({"document_id": "other"}, "OUT_OF_SCOPE"),
    ({"version_id": "old"}, "INACTIVE_VERSION"),
    ({"is_current": False}, "INACTIVE_VERSION"),
    ({"content_sha256": "0" * 64}, "INVALID_EVIDENCE"),
    ({"locator": {}}, "INVALID_EVIDENCE"),
    ({"locator": {"kind": "text", "start": 40, "end": 42, "quote": "fake"}}, "INVALID_EVIDENCE"),
    ({"locator": {"kind": "table", "start": 40, "end": 42}}, "UNSUPPORTED_TYPE"),
    ({"locator": {"kind": "text", "start": 40, "end": 42,
                  "raw_evidence": {"evidence_kind": "model_generated_caption"}}}, "UNSUPPORTED_TYPE"),
])
def test_chunk_identity_and_quote_integrity(api, changes, reason):
    candidate = meta(api, "n", 4, chunk=record("n", 4, **changes))
    result = expand(api, [meta(api, "s", 3)], [candidate])
    assert result.neighbors == ()
    assert reason in codes(result)


def test_seed_missing_metadata_blocks_neighbors_without_reselecting_seed(api):
    seed = meta(api, "s", 3, chunk_index=None)
    result = expand(api, [seed], [meta(api, "n", 4)])
    assert result.seeds == (seed.chunk,)
    assert result.neighbors == ()
    assert "MISSING_METADATA" in codes(result)


def test_same_scope_other_document_cannot_be_neighbor(api):
    seed = meta(api, "s", 3)
    neighbor = meta(api, "n", 4, chunk=record("n", 4, document_id="doc2"))
    result = api.expand_context(Scope.from_ids(["kb"]), [seed], [neighbor], enabled=True,
                               remaining_items=4, remaining_chars=100,
                               label_overheads=(5,) * 4, separator_chars=2)
    assert result.neighbors == ()
    assert "IDENTITY_MISMATCH" in codes(result)


def test_no_jumping_over_index_gaps(api):
    result = expand(api, [meta(api, "s", 3)], [meta(api, "far", 5)])
    assert result.neighbors == ()
    assert "NO_NEIGHBOR" in codes(result)


def test_global_four_cap_and_shared_neighbor_identity(api):
    seeds = [meta(api, "s3", 3), meta(api, "s5", 5), meta(api, "s9", 9)]
    candidates = [meta(api, f"n{i}", i) for i in (2, 4, 6, 8, 10)]
    result = expand(api, seeds, candidates * 2, remaining_items=10)
    assert [c.chunk_id for c in result.neighbors] == ["n2", "n4", "n6", "n8"]
    assert result.seeds == tuple(s.chunk for s in seeds)
    assert "GLOBAL_LIMIT" in codes(result)
    assert len({(c.version_id, c.chunk_id) for c in result.neighbors}) == 4


def test_seed_is_never_readded_as_neighbor(api):
    seeds = [meta(api, "s3", 3), meta(api, "s4", 4)]
    result = expand(api, seeds, seeds)
    assert result.neighbors == ()
    assert "ALREADY_SELECTED" in codes(result)


@pytest.mark.parametrize("chars,items,expected", [(8, 4, []), (9, 4, ["n2"]), (18, 1, ["n2"]), (18, 2, ["n2", "n4"]), (0, 0, [])])
def test_remaining_budget_accounts_for_label_and_separator(api, chars, items, expected):
    result = expand(api, [meta(api, "s", 3)], [meta(api, "n2", 2), meta(api, "n4", 4)],
                    remaining_chars=chars, remaining_items=items)
    assert [c.chunk_id for c in result.neighbors] == expected
    assert result.added_chars <= chars


def test_long_neighbor_skipped_whole_and_short_one_can_fit(api):
    long = meta(api, "long", 2, chunk=record("long", 2, text="正文" * 10))
    short = meta(api, "short", 4)
    result = expand(api, [meta(api, "s", 3)], [long, short], remaining_chars=9)
    assert result.neighbors == (short.chunk,)
    assert "CHAR_BUDGET" in codes(result)
    assert long.chunk.content == "正文" * 10


def test_label_width_is_supplied_for_each_actual_slot(api):
    result = expand(api, [meta(api, "s", 3)], [meta(api, "n2", 2), meta(api, "n4", 4)],
                    label_overheads=(5, 6), remaining_items=2, remaining_chars=18)
    assert [c.chunk_id for c in result.neighbors] == ["n2"]
    assert result.added_chars == 9


@pytest.mark.parametrize("options", [
    {"remaining_items": True}, {"remaining_chars": -1},
    {"separator_chars": None}, {"label_overheads": ()},
    {"label_overheads": (True,) * 4}, {"enabled": "yes"},
])
def test_invalid_enabled_budget_contract_raises(api, options):
    with pytest.raises(ValueError):
        expand(api, [meta(api, "s", 3)], [], **options)


def test_conflicting_identity_is_rejected_independent_of_order(api):
    first = meta(api, "n", 4)
    conflict = replace(first, chunk=record("n", 4, text="冲突"))
    for candidates in ([first, conflict], [conflict, first]):
        result = expand(api, [meta(api, "s", 3)], candidates)
        assert result.neighbors == ()
        assert "IDENTITY_CONFLICT" in codes(result)


def test_duplicate_index_with_distinct_ids_is_ambiguous(api):
    result = expand(api, [meta(api, "s", 3)], [meta(api, "a", 4), meta(api, "b", 4)])
    assert result.neighbors == ()
    assert "AMBIGUOUS_NEIGHBOR" in codes(result)


def test_pdf_requires_same_explicit_page_boundary(api):
    def pdf(cid, index, page):
        chunk = record(cid, index)
        chunk = replace(chunk, locator={**chunk.locator, "kind": "pdf", "page": page})
        return meta(api, cid, index, chunk=chunk, boundary_kind="page", page=page)
    seed = pdf("s", 3, 1)
    assert expand(api, [seed], [pdf("n", 4, 1)]).neighbors
    result = expand(api, [seed], [pdf("n", 4, 2)])
    assert result.neighbors == ()
    assert "BOUNDARY_MISMATCH" in codes(result)
    result = expand(api, [seed], [replace(pdf("n", 4, 1), page=None)])
    assert result.neighbors == ()
    assert "MISSING_METADATA" in codes(result)


def test_position_must_agree_with_index_direction(api):
    result = expand(api, [meta(api, "s", 3)], [meta(api, "n", 4, chunk=record("n", 1))])
    assert result.neighbors == ()
    assert "POSITION_MISMATCH" in codes(result)


def test_inputs_remain_unchanged_and_replay_is_deterministic(api):
    seed, neighbor = meta(api, "s", 3), meta(api, "n", 4)
    before = dict(neighbor.chunk.locator)
    first = expand(api, [seed], [neighbor])
    assert first == expand(api, [seed], [neighbor])
    assert first.chunks == (seed.chunk, neighbor.chunk)
    assert neighbor.chunk.locator == before
    assert first.neighbors[0].locator is neighbor.chunk.locator


@pytest.mark.parametrize("changes", [
    {"locator": {"kind": [], "start": 400, "end": 402}},
    {"knowledge_base_id": []},
    {"document_id": []},
    {"chunk_id": []},
    {"version_id": []},
])
def test_malformed_evidence_fields_are_skipped_with_reason(api, changes):
    candidate = meta(api, "n", 4, chunk=record("n", 4, **changes))
    result = expand(api, [meta(api, "s", 3)], [candidate])
    assert result.neighbors == ()
    assert "INVALID_EVIDENCE" in codes(result)


@pytest.mark.parametrize("section_id", [" ", "\t", "\n"])
def test_whitespace_only_section_ids_never_prove_boundary(api, section_id):
    seed = meta(api, "s", 3, section_id=section_id)
    neighbor = meta(api, "n", 4, section_id=section_id)
    result = expand(api, [seed], [neighbor])
    assert result.seeds == (seed.chunk,)
    assert result.neighbors == ()
    assert "INVALID_METADATA" in codes(result)


def test_nonempty_section_identity_is_preserved_without_normalization(api):
    seed = meta(api, "s", 3, section_id=" s1 ")
    matching = meta(api, "n", 4, section_id=" s1 ")
    result = expand(api, [seed], [matching])
    assert result.neighbors == (matching.chunk,)
    assert seed.section_id == matching.section_id == " s1 "
    distinct = replace(matching, section_id="s1")
    result = expand(api, [seed], [distinct])
    assert result.neighbors == ()
    assert "BOUNDARY_MISMATCH" in codes(result)
