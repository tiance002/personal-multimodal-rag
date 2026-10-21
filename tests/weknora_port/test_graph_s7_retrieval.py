"""S7 source identity and query-aware bounded paths: offline synthetic fixtures.

All relations pass the normal extraction proof validator. No Provider, DB,
historical score or evaluation Gold is involved in these local regressions.
"""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from backend.weknora_port.graph.extraction import parse_extraction
from backend.weknora_port.graph.retrieval import find_paths, fuse_paths
from backend.weknora_port.graph.types import GraphSource
from backend.weknora_port.retrieval.context import into_chat_message
from tests.weknora_port.test_graph_retrieval import (
    CTX, GraphRepo, chain, paths, run, source_row, state, triple,
)


def branches(count=4):
    predicates = ["founded", "visited", "funded", "joined"]
    predicates += [f"administered project {i}" for i in range(count - 4)]
    sentences = [f"Astrid Vale {predicate} Institute {i}."
                 for i, predicate in enumerate(predicates)]
    text = " ".join(sentences)
    source = GraphSource(1, "owner", "kb", "person", "v1", "chunk-person",
                         text, 0, len(text))
    entities = [dict(key="s", name="Astrid Vale", type="PERSON", aliases=[],
                     anchor=True, qualifier="")]
    entities += [dict(key=f"t{i}", name=f"Institute {i}", type="ORGANIZATION",
                      aliases=[], anchor=False, qualifier="")
                 for i in range(count)]
    relations = [dict(source="s", target=f"t{i}", predicate=predicate,
                      quote=sentences[i], polarity="affirmed", time="")
                 for i, predicate in enumerate(predicates)]
    first = parse_extraction(source, json.dumps(dict(entities=entities,
        relations=relations)), "s7-offline-branches")
    locations = tuple(triple(f"Institute {i}", "is located in", f"Place {i}",
                      doc=f"location-{i}", subject_type="ORGANIZATION",
                      target_type="PLACE") for i in range(count))
    assert len(first.edges) == count and not first.rejected
    return (first, *locations)


def test_same_entity_different_relations_select_different_required_paths():
    graph = branches()
    selected = []
    for predicate in ("founded", "visited", "funded", "joined"):
        found = paths(graph, query=f"Where is the institute that Astrid Vale {predicate}?",
                      max_paths=1)
        assert len(found) == 1 and len(found[0].edges) == 2
        assert found[0].edges[0].predicate == predicate
        selected.append(found[0].sources[-1].document_id)
    assert len(set(selected)) == 4


def test_relation_scoring_runs_before_outgoing_twelve_and_path_three_limits():
    graph = branches(18)
    # Select an edge which the original hash order would cut before traversal.
    formerly_cut = sorted(graph[0].edges, key=lambda edge: edge.id)[-1]
    found = paths(graph,
        query=f"Where is the institute Astrid Vale {formerly_cut.predicate}?",
        max_paths=1)
    assert len(graph[0].edges) > 12
    assert found[0].edges[0].id == formerly_cut.id
    assert len(found[0].edges) == 2
    assert len(paths(graph, query="Astrid Vale")) <= 3


@pytest.mark.parametrize("question", [
    "Tell me where the institute founded by Astrid Vale is located.",
    "Astrid Vale founded which institute, and where is it?",
])
def test_word_order_rephrasing_keeps_the_requested_relation(question):
    found = paths(branches(), query=question, max_paths=1)
    assert found[0].edges[0].predicate == "founded"


def test_year_overlap_ranks_matching_quote_but_does_not_invent_timeline():
    text = "Astrid Vale visited Museum Alpha in 1998. Astrid Vale visited Museum Beta in 2008."
    source = GraphSource(1, "owner", "kb", "visits", "v1", "chunk-visits", text, 0, len(text))
    payload = dict(entities=[
        dict(key="s", name="Astrid Vale", type="PERSON", aliases=[], anchor=True, qualifier=""),
        dict(key="a", name="Museum Alpha", type="PLACE", aliases=[], anchor=False, qualifier=""),
        dict(key="b", name="Museum Beta", type="PLACE", aliases=[], anchor=False, qualifier="")],
        relations=[dict(source="s", target="a", predicate="visited",
                        quote=text.split(". ")[0] + ".", polarity="affirmed", time="in 1998"),
                   dict(source="s", target="b", predicate="visited",
                        quote="Astrid Vale visited Museum Beta in 2008.", polarity="affirmed", time="in 2008")])
    batch = parse_extraction(source, json.dumps(payload), "s7-offline-years")
    assert len(batch.edges) == 2
    found = paths((batch,), query="Which museum did Astrid Vale visited in 2008?", max_paths=1)
    assert found[0].edges[0].temporal_text == "in 2008"
    # Lexical retrieval is not a temporal/negation judge: it never edits an edge.
    assert found[0].edges[0] in batch.edges
    assert not paths((batch,), query="Where was Unknown Person in 2008?")


def test_unrelated_query_and_reverse_object_do_not_create_a_source_path():
    graph = branches(18)
    assert paths(graph, query="How many stars orbit the distant planet?") == ()
    assert paths(graph, query="Place 0") == ()


def test_rejected_negated_uncertain_and_inverted_claims_are_not_revived_by_ranking():
    for text, polarity, direction in (
            ("Astrid Vale did not found Lark Institute.", "negated", False),
            ("Astrid Vale might have founded Lark Institute.", "uncertain", False),
            ("Astrid Vale founded Lark Institute.", "affirmed", True)):
        source = GraphSource(1, "owner", "kb", "negative", "v1", "chunk-negative", text, 0, len(text))
        payload = dict(entities=[
            dict(key="s", name="Astrid Vale", type="PERSON", aliases=[], anchor=True, qualifier=""),
            dict(key="t", name="Lark Institute", type="ORGANIZATION", aliases=[], anchor=False, qualifier="")],
            relations=[dict(source="t" if direction else "s", target="s" if direction else "t",
                            predicate="founded", quote=text, polarity=polarity, time="")])
        batch = parse_extraction(source, json.dumps(payload), "s7-offline-negative")
        assert not batch.edges
        assert paths((batch,), query="What institute Astrid Vale founded?") == ()


@pytest.mark.parametrize("kind", ["summary", "image_caption", "table_summary"])
def test_generated_same_text_cannot_claim_raw_graph_sources_already_present(kind):
    graph = chain()
    selected = []
    for batch in graph:
        generated = source_row(batch.source)
        generated.id = f"generated-{kind}-{batch.source.document_id}"
        generated.chunk_type = kind
        generated.sub_chunk_ids = [batch.source.chunk_id]
        selected.append(generated)
    s = state(merge_results=selected)
    assert run(fuse_paths(s, CTX, GraphRepo(graph), paths(graph, max_paths=1))) == 2
    assert {batch.source.chunk_id for batch in graph} <= {row.id for row in s.merge_results}
    trace = s.graph_trace[0]
    assert trace["added_source_chunk_ids"] == [batch.source.chunk_id for batch in graph]
    assert trace["covered_sources"] == []
    final = run(into_chat_message(s, CTX))
    assert all(batch.source.chunk_id in final.result_ids for batch in graph)


@pytest.mark.parametrize("mapping", ["same_id", "sub_chunk", "parent"])
def test_raw_same_version_coverage_requires_full_body_and_source_identity(mapping):
    graph = chain()
    repo = GraphRepo(graph)
    raw = source_row(graph[0].source)
    raw.chunk_metadata = {"source_version": "v1"}
    raw.content = "Context before. " + raw.content + " More raw context."
    if mapping == "sub_chunk":
        raw.id = "merged-source"
        raw.sub_chunk_ids = [graph[0].source.chunk_id]
        raw.content_rewritten = True
    elif mapping == "parent":
        raw.id = "parent-source"
        raw.chunk_type = "parent_text"
        repo.graph_rows[graph[0].source.chunk_id].parent_chunk_id = raw.id
    s = state(merge_results=[raw])
    assert run(fuse_paths(s, CTX, repo, paths(graph, max_paths=1))) == 1
    trace = s.graph_trace[0]
    assert trace["added_source_chunk_ids"] == [graph[1].source.chunk_id]
    assert trace["covered_sources"][0]["source_chunk_id"] == graph[0].source.chunk_id
    assert trace["covered_sources"][0]["retained_chunk_id"] == raw.id


@pytest.mark.parametrize("change", ["no_mapping", "version", "chunk_version", "partial", "disabled", "kb", "doc"])
def test_false_raw_coverage_is_not_accepted(change):
    graph = chain()
    raw = source_row(graph[0].source)
    raw.chunk_metadata = {"source_version": "v1"}
    if change == "no_mapping":
        raw.id = "unmapped-copy"
    elif change == "version":
        raw.metadata["source_version"] = "old"
    elif change == "chunk_version":
        raw.chunk_metadata["source_version"] = "old"
    elif change == "partial":
        raw.content = raw.content[:12]
    elif change == "disabled":
        raw.is_enabled = False
    elif change == "kb":
        raw.knowledge_base_id = "other"
    else:
        raw.knowledge_id = "other"
    s = state(merge_results=[raw])
    assert run(fuse_paths(s, CTX, GraphRepo(graph), paths(graph, max_paths=1))) == 2
    assert not s.graph_trace[0]["covered_sources"]


def test_summary_cannot_satisfy_atomic_path_when_raw_budget_is_insufficient():
    graph = chain()
    generated = source_row(graph[0].source)
    generated.id, generated.chunk_type = "summary", "summary"
    s = state(merge_results=[generated])
    assert run(fuse_paths(s, CTX, GraphRepo(graph), paths(graph, max_paths=1), max_chunks=1)) == 0
    assert s.merge_results == [generated]
    trace = s.graph_trace[0]
    assert trace["status"] == "BUDGET_DROPPED"
    assert trace["budget_dropped_source_chunk_ids"] == [batch.source.chunk_id for batch in graph]
    assert trace["added_source_chunk_ids"] == []
    assert trace["covered_sources"] == []


def test_equal_query_scores_use_stable_ids_independent_of_snapshot_order():
    graph = branches(18)
    left = paths(graph, query="Astrid Vale")
    right = paths(tuple(reversed(graph)), query="Astrid Vale")
    assert [tuple(edge.id for edge in path.edges) for path in left] == [
        tuple(edge.id for edge in path.edges) for path in right]


def test_unshared_verb_paraphrase_remains_bounded_without_fabricating_a_predicate():
    graph = branches(18)
    found = paths(graph, query="Where is the institute Astrid Vale established?")
    assert len(found) <= 3
    real_edges = {edge.id: edge for batch in graph for edge in batch.edges}
    assert all(edge == real_edges[edge.id] for path in found for edge in path.edges)
    assert all(edge.predicate != "established" for path in found for edge in path.edges)
    # This safety regression is not a semantic success assertion: lexical
    # overlap does not implement established->founded synonym understanding.
