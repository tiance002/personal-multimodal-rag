from dataclasses import replace
import hashlib
import json

import pytest

from backend.weknora_port.graph.extraction import (
    EXTRACTION_PROMPT, GraphExtractionError, MAX_NODES, MAX_SOURCE_CHARS,
    build_extraction_payload, parse_extraction, validate_batch_proofs,
)
from backend.weknora_port.graph.types import GraphSource, VERIFICATION


def source(text="Orchid Observatory is located in Cedar Bay.", **kwargs):
    values = dict(tenant_id=1, owner_id="owner", knowledge_base_id="kb", document_id="doc",
                  source_version="v1", chunk_id="c1", content=text, start_at=0, end_at=len(text))
    return GraphSource(**(values | kwargs))


def entity(key, name, *, anchor=False, aliases=None, qualifier="", kind="PLACE"):
    return dict(key=key, name=name, type=kind, aliases=aliases or [], anchor=anchor, qualifier=qualifier)


def payload(*, text="Orchid Observatory is located in Cedar Bay.", reverse=False, **changes):
    relation = dict(source="e1", target="e2", predicate="is located in", quote=text, polarity="affirmed", time="")
    if reverse:
        relation.update(source="e2", target="e1")
    relation.update(changes)
    return dict(entities=[entity("e1", "Orchid Observatory", anchor=True), entity("e2", "Cedar Bay")], relations=[relation])


def parse(src=None, data=None):
    return parse_extraction(src or source(), json.dumps(data or payload()), "extract-1")


def test_positive_preserves_original_quote_and_scope():
    src = source()
    batch = parse(src)
    assert batch.status == "EXTRACTED"
    assert len(batch.nodes) == 2 and len(batch.edges) == 1
    edge = batch.edges[0]
    assert src.content[edge.quote_start:edge.quote_end] == edge.quote == src.content
    assert edge.verification == VERIFICATION
    assert batch.nodes[0].is_anchor and not batch.nodes[1].is_anchor
    assert src.content_sha256 == hashlib.sha256(src.content.encode()).hexdigest()


@pytest.mark.parametrize("changes,reason", [
    ({"quote": "Orchid Observatory is located in Invented Town."}, "QUOTE_NOT_UNIQUE_IN_SOURCE"),
    ({"reverse": True}, "DIRECTION_OR_PREDICATE_NOT_IN_QUOTE"),
    ({"predicate": "was founded in"}, "DIRECTION_OR_PREDICATE_NOT_IN_QUOTE"),
    ({"polarity": "negated"}, "NOT_AFFIRMED"),
    ({"source": "invented"}, "UNKNOWN_ENDPOINT"),
    ({"source": "e2"}, "SELF_RELATION"),
    ({"time": "in 1980"}, "TIME_NOT_IN_QUOTE"),
])
def test_rejects_unproven_or_inverted_relation(changes, reason):
    batch = parse(data=payload(**changes))
    assert not batch.edges
    assert batch.rejected[-1]["reason"] == reason


@pytest.mark.parametrize("text", [
    "Orchid Observatory is not located in Cedar Bay.",
    "Orchid Observatory may be located in Cedar Bay.",
    "If Orchid Observatory is located in Cedar Bay, it is nearby.",
])
def test_negation_uncertainty_and_condition_are_not_facts(text):
    batch = parse(source(text), payload(text=text, predicate="located in"))
    assert batch.status == "REJECTED"
    assert batch.rejected[-1]["reason"] == "NEGATED_UNCERTAIN_OR_CONDITIONAL"


def test_quote_cannot_chop_off_negation_or_uncertainty_from_same_statement():
    quote = source().content
    for prefix in ("It is not true that ", "It is uncertain whether "):
        batch = parse(source(prefix + quote), payload())
        assert not batch.edges
        assert batch.rejected[-1]["reason"] == "SURROUNDING_NEGATION_OR_UNCERTAINTY"


def test_unrelated_next_sentence_does_not_negate_a_complete_quote():
    assert len(parse(source(source().content + " Another station is not in service.")).edges) == 1


def test_temporal_claim_keeps_exact_temporal_text():
    text = "Orchid Observatory was located in Cedar Bay in 1994."
    data = payload(text=text, predicate="was located in")
    assert parse(source(text), data).rejected[-1]["reason"] == "TIME_QUALIFIER_MISSING"
    data["relations"][0]["time"] = "in 1994"
    edge = parse(source(text), data).edges[0]
    assert edge.temporal_text == "in 1994"


def test_temporal_scope_cannot_be_removed_or_weakened():
    quote = "Orchid Observatory was located in Cedar Bay."
    data = payload(text=quote, predicate="was located in")
    assert parse(source("Previously, " + quote), data).rejected[-1]["reason"] == "TEMPORAL_SCOPE_OUTSIDE_QUOTE"
    quote = "Orchid Observatory was formerly located in Cedar Bay in 1994."
    data = payload(text=quote, predicate="was formerly located in", time="in 1994")
    assert parse(source(quote), data).rejected[-1]["reason"] == "INCOMPLETE_TIME_QUALIFIER"


@pytest.mark.parametrize("field,value", [("name", "Orchid"), ("aliases", ["ObservatoryAlias"]), ("qualifier", "district-9")])
def test_entity_surfaces_are_grounded_at_independent_boundaries(field, value):
    data = payload()
    # Shorter exact surface Orchid is legal, but a partial word is not.
    data["entities"][0][field] = "Orchi" if field == "name" else value
    batch = parse(data=data)
    assert batch.rejected[0]["reason"] == "SURFACE_NOT_IN_SOURCE"
    assert not batch.edges


def test_identifier_prefix_is_not_a_standalone_entity_name():
    src = source("Station NP-204 measures 37 units.")
    data = {"entities": [entity("e1", "NP")], "relations": []}
    assert parse(src, data).rejected[0]["reason"] == "SURFACE_NOT_IN_SOURCE"


def test_alias_is_accepted_only_when_present_and_can_ground_relation():
    text = "Orchid Observatory (OO) operates here. OO is located in Cedar Bay."
    data = payload(text="OO is located in Cedar Bay.")
    data["entities"][0]["aliases"] = ["OO"]
    batch = parse(source(text), data)
    assert len(batch.edges) == 1
    assert batch.nodes[0].aliases == ("OO",)


@pytest.mark.parametrize("text,name", [
    ("The Orchid Observatory is located in Cedar Bay.", "Orchid Observatory"),
    ("The Orchid Observatory is located in Cedar Bay.", "The Orchid Observatory"),
    (" An Orchid Observatory is located in Cedar Bay.", "Orchid Observatory"),
])
def test_anchor_allows_one_article_without_stripping_entity_name(text, name):
    data = payload(text=text)
    data["entities"][0]["name"] = name
    assert parse(source(text), data).nodes[0].is_anchor


@pytest.mark.parametrize("kwargs", [{"start_at": 200, "end_at": 244}, {"start_at": 0, "end_at": 0}])
def test_other_or_unknown_chunk_cannot_claim_document_subject_anchor(kwargs):
    assert not parse(source(**kwargs)).nodes[0].is_anchor


def test_later_mention_is_not_anchor():
    text = "A note follows. Orchid Observatory is located in Cedar Bay."
    assert not parse(source(text), payload(text="Orchid Observatory is located in Cedar Bay.")).nodes[0].is_anchor


def test_same_entity_in_different_source_identity_does_not_merge():
    baseline = parse().nodes[0].id
    for key, value in [("tenant_id", 2), ("owner_id", "other"), ("knowledge_base_id", "other"),
                       ("document_id", "other"), ("source_version", "v2"), ("chunk_id", "c2")]:
        assert parse(source(**{key: value})).nodes[0].id != baseline


def test_duplicates_have_stable_ids_and_no_duplicate_edges():
    data = payload()
    data["entities"].append(entity("e3", "Orchid Observatory", anchor=True))
    data["relations"].append(data["relations"][0].copy())
    batch = parse(data=data)
    assert len(batch.nodes) == 2 and len(batch.edges) == 1
    other = payload()
    other["entities"][0]["key"] = "renamed"
    other["relations"][0]["source"] = "renamed"
    assert parse(data=other).nodes[0].id == batch.nodes[0].id


def test_empty_is_valid_but_not_successful_evidence():
    batch = parse(data={"entities": [], "relations": []})
    assert batch.status == "EMPTY" and not batch.nodes and not batch.edges


def test_duplicate_quote_is_ambiguous():
    text = source().content
    assert parse(source(text + " " + text)).rejected[-1]["reason"] == "QUOTE_NOT_UNIQUE_IN_SOURCE"


@pytest.mark.parametrize("raw", [
    "{}", '{"entities": [], "relations": [], "extra": true}',
    '{"entities": [], "entities": [], "relations": []}',
    '{"entities": [], "relations": NaN}', "```json\n{}\n```",
])
def test_strict_envelope_rejects_unknown_duplicate_or_non_json_fields(raw):
    with pytest.raises(GraphExtractionError):
        parse_extraction(source(), raw, "x")


def test_bounded_output_and_unknown_record_fields():
    data = payload()
    data["entities"][0]["confidence"] = 1.0
    assert parse(data=data).rejected[0]["reason"] == "ENTITY_FIELDS"
    with pytest.raises(GraphExtractionError):
        parse(data={"entities": [entity(str(i), "Cedar Bay") for i in range(MAX_NODES + 1)], "relations": []})
    with pytest.raises(GraphExtractionError):
        build_extraction_payload(source("x" * (MAX_SOURCE_CHARS + 1)))


def test_payload_contains_exact_unmodified_source_not_a_synthesized_fact():
    src = source("Original  text\nNP-204 / noise−x\tkept.")
    request = build_extraction_payload(src)
    wire_source = json.loads(request["messages"][1]["content"])
    assert wire_source["content"] == src.content
    assert wire_source["source_version"] == "v1"
    assert request["messages"][0]["content"] == EXTRACTION_PROMPT


@pytest.mark.parametrize("kwargs", [{"tenant_id": True}, {"tenant_id": 0}, {"owner_id": ""},
                                    {"end_at": -1}, {"page_number": 0}])
def test_source_identity_and_offsets_fail_closed(kwargs):
    with pytest.raises(ValueError):
        source(**kwargs)


def test_storage_proof_replay_accepts_valid_retained_subset_despite_old_rejections():
    batch = parse()
    validate_batch_proofs(replace(batch, rejected=({"kind": "relation", "reason": "OLD_REJECTED_SUGGESTION"},)))
    validate_batch_proofs(parse(data={"entities": [], "relations": []}))


@pytest.mark.parametrize("changes", [
    {"quote": "Orchid Observatory is located in Invented Town."},
    {"source_id": "gn-fake"},
    {"id": "ge-fake"},
    {"quote_start": 1},
    {"quote_end": 2},
    {"verification": "SEMANTICALLY_PROVED"},
])
def test_storage_proof_replay_rejects_forged_edge(changes):
    batch = parse()
    forged = replace(batch, edges=(replace(batch.edges[0], **changes),))
    with pytest.raises(GraphExtractionError):
        validate_batch_proofs(forged)


def test_storage_proof_replay_rejects_reversed_edge():
    batch = parse()
    edge = batch.edges[0]
    forged = replace(batch, edges=(replace(edge, source_id=edge.target_id, target_id=edge.source_id),))
    with pytest.raises(GraphExtractionError):
        validate_batch_proofs(forged)


@pytest.mark.parametrize("changes", [
    {"name": "Invented Observatory"}, {"id": "gn-fake"}, {"aliases": ("InventedAlias",)},
    {"identity_qualifier": "unseen district"},
])
def test_storage_proof_replay_rejects_ungrounded_or_forged_node(changes):
    batch = parse()
    forged = replace(batch, nodes=(replace(batch.nodes[0], **changes), batch.nodes[1]))
    with pytest.raises(GraphExtractionError):
        validate_batch_proofs(forged)


def test_storage_proof_replay_rejects_forged_anchor_and_duplicates():
    batch = parse()
    with pytest.raises(GraphExtractionError):
        validate_batch_proofs(replace(batch, nodes=(batch.nodes[0], replace(batch.nodes[1], is_anchor=True))))
    with pytest.raises(GraphExtractionError):
        validate_batch_proofs(replace(batch, nodes=(*batch.nodes, batch.nodes[0])))
