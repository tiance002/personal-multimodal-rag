"""Offline synthetic source/graph fixtures; no real graph build, DB or Provider.

All triples below pass the production extraction parser against their synthetic
source quotes. A passing test is not a real model extraction or RAG acceptance.
"""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from backend.weknora_port.graph.extraction import GraphExtractionError, parse_extraction
from backend.weknora_port.graph.retrieval import GraphRetrievalError, find_paths, fuse_paths
from backend.weknora_port.graph.types import GraphSource
from backend.weknora_port.models.types import RerankResult, TrustedScope
from backend.weknora_port.retrieval.context import into_chat_message
from backend.weknora_port.retrieval.pipeline import RetrievalPipeline
from backend.weknora_port.retrieval.search import RetrievalScopeError, SearchNothing
from backend.weknora_port.retrieval.types import SearchResult
from tests.weknora_port.test_pipeline import CTX, Model, Registry, Repo, row, state


def run(value):
    return asyncio.run(value)


def triple(subject, predicate, target, *, doc="first", subject_type="PERSON",
           target_type="ORGANIZATION", subject_qualifier="", target_qualifier="",
           text=None, **source_values):
    text = text or f"{subject} {predicate} {target}."
    source = GraphSource(tenant_id=1, owner_id="owner", knowledge_base_id="kb",
        document_id=doc, source_version="v1", chunk_id=f"chunk-{doc}",
        content=text, start_at=0, end_at=len(text), **source_values)
    entities = [
        dict(key="s", name=subject, type=subject_type, aliases=[], anchor=True,
             qualifier=subject_qualifier),
        dict(key="t", name=target, type=target_type, aliases=[], anchor=False,
             qualifier=target_qualifier),
    ]
    payload = dict(entities=entities, relations=[dict(source="s", target="t",
        predicate=predicate, quote=text, polarity="affirmed", time="")])
    return parse_extraction(source, json.dumps(payload), "offline-synthetic")


def chain():
    first = triple("Astrid Vale", "founded", "Lark Institute")
    second = triple("Lark Institute", "is located in", "Cedar Bay", doc="second",
                    subject_type="ORGANIZATION", target_type="PLACE")
    assert len(first.edges) == len(second.edges) == 1
    return first, second


def paths(batches=None, query="Where is the institute founded by Astrid Vale?", **kwargs):
    return find_paths(query, batches or chain(), CTX, tenant_id=1, **kwargs)


def source_row(source):
    return SearchResult(source.chunk_id, source.content, .71,
        knowledge_id=source.document_id, knowledge_base_id=source.knowledge_base_id,
        start_at=source.start_at, end_at=source.end_at, match_type="vector",
        metadata={"source_version": source.source_version})


class GraphRepo(Repo):
    tenant_id = 1

    def __init__(self, batches=(), rows=()):
        super().__init__(rows)
        self.batches = tuple(batches)
        self.graph_rows = {b.source.chunk_id: source_row(b.source) for b in batches}
        self.graph_reads = []
        self.graph_loads = 0

    async def get_graph_batches(self, context):
        self.graph_loads += 1
        return self.batches

    async def get_graph_chunks(self, ids, context):
        self.graph_reads.append(tuple(ids))
        return {identifier: deepcopy(self.graph_rows[identifier])
                for identifier in ids if identifier in self.graph_rows}


def test_exact_typed_cross_document_reference_returns_two_real_sources():
    batches = chain()
    found = paths(batches, max_paths=1)
    assert len(found) == 1 and len(found[0].edges) == 2
    assert {s.document_id for s in found[0].sources} == {"first", "second"}
    assert found[0].reference_links == ((batches[0].nodes[1].id, batches[1].nodes[0].id),)
    repo = GraphRepo(batches)
    s = state()
    assert run(fuse_paths(s, CTX, repo, found)) == 2
    assert [r.content for r in s.merge_results] == [b.source.content for b in batches]
    assert all(r.match_type == "graph" and r.score == 0 for r in s.merge_results)
    assert all("model_score" not in r.metadata for r in s.merge_results)
    final = run(into_chat_message(s, CTX))
    assert final.result_ids == ("chunk-first", "chunk-second")
    assert all(b.source.content in final.user_content for b in batches)
    assert "QUOTE_CHECKED_DERIVED_PATH" not in final.user_content
    assert "reference_links" not in final.user_content


def test_outgoing_paths_do_not_reverse_an_object_into_a_subject():
    assert paths(query="Cedar Bay") == ()


def test_inverted_extraction_cannot_enter_the_graph():
    batch = chain()[0]
    data = dict(entities=[
        dict(key="s", name="Astrid Vale", type="PERSON", aliases=[], anchor=True, qualifier=""),
        dict(key="t", name="Lark Institute", type="ORGANIZATION", aliases=[], anchor=False, qualifier="")],
        relations=[dict(source="t", target="s", predicate="founded", quote=batch.source.content,
                        polarity="affirmed", time="")])
    rejected = parse_extraction(batch.source, json.dumps(data), "offline-inverted")
    assert not rejected.edges
    assert rejected.rejected[-1]["reason"] == "DIRECTION_OR_PREDICATE_NOT_IN_QUOTE"


def test_same_label_multiple_document_anchors_refuses_arbitrary_second_hop():
    first, second = chain()
    other = triple("Lark Institute", "is located in", "Pine Village", doc="third",
                   subject_type="ORGANIZATION", target_type="PLACE")
    found = paths((first, second, other))
    assert found and all(len(p.edges) == 1 for p in found)
    assert paths((first, second, other), query="Lark Institute") == ()


def test_conflicting_source_qualifiers_do_not_join_same_label():
    first = triple("Astrid Vale", "founded", "Lark Institute", target_qualifier="East",
                   text="Astrid Vale founded Lark Institute (East).")
    second = triple("Lark Institute", "is located in", "Cedar Bay", doc="second",
                    subject_type="ORGANIZATION", target_type="PLACE", subject_qualifier="West",
                    text="Lark Institute (West) is located in Cedar Bay.")
    assert all(len(p.edges) == 1 for p in paths((first, second)))


def test_same_name_different_entity_type_is_not_a_reference_link():
    first, _ = chain()
    second = triple("Lark Institute", "is located in", "Cedar Bay", doc="second",
                    subject_type="PLACE", target_type="PLACE")
    assert all(len(p.edges) == 1 for p in paths((first, second)))


@pytest.mark.parametrize("field,value", [
    ("tenant_id", 2), ("owner_id", "other"), ("knowledge_base_id", "other")])
def test_graph_source_scope_rejection(field, value):
    first, second = chain()
    source = replace(first.source, **{field: value})
    # Reparse under its actual alternate identity, rather than merely forge IDs.
    data = dict(entities=[dict(key="s", name="Astrid Vale", type="PERSON", aliases=[], anchor=True,
                               qualifier=""), dict(key="t", name="Lark Institute", type="ORGANIZATION",
                               aliases=[], anchor=False, qualifier="")],
                relations=[dict(source="s", target="t", predicate="founded", quote=source.content,
                                polarity="affirmed", time="")])
    changed = parse_extraction(source, json.dumps(data), "alternate-source")
    with pytest.raises(GraphRetrievalError, match="GRAPH_SCOPE_MISMATCH"):
        paths((changed, second))


def test_document_scope_does_not_widen_for_a_reference():
    context = replace(CTX, scope=TrustedScope("owner", ("kb",), ("first",)))
    with pytest.raises(GraphRetrievalError, match="GRAPH_SCOPE_MISMATCH"):
        find_paths("Astrid Vale", chain(), context, tenant_id=1)


@pytest.mark.parametrize("tenant_id", [None, True, 0, -1, "1"])
def test_missing_or_untrusted_server_tenant_is_rejected(tenant_id):
    with pytest.raises(GraphRetrievalError, match="GRAPH_TENANT_IDENTITY_REQUIRED"):
        find_paths("Astrid Vale", chain(), CTX, tenant_id=tenant_id)


def test_no_second_hop_does_not_claim_two_hops():
    found = paths((chain()[0],))
    assert found and all(len(p.edges) == 1 and not p.reference_links for p in found)


def test_forged_quote_is_rejected_before_any_source_read():
    first, second = chain()
    forged = replace(first, edges=(replace(first.edges[0], quote="Invented relationship"),))
    with pytest.raises((GraphExtractionError, GraphRetrievalError)):
        paths((forged, second))


def test_fusion_does_not_trust_a_forged_path_quote():
    batches = chain()
    path = paths(batches, max_paths=1)[0]
    forged = replace(path, edges=(replace(path.edges[0], quote="Invented relationship"),
                                  *path.edges[1:]))
    s = state()
    with pytest.raises((GraphExtractionError, GraphRetrievalError)):
        run(fuse_paths(s, CTX, GraphRepo(batches), (forged,)))
    assert s.merge_results == []


@pytest.mark.parametrize("change", ["missing", "version", "content", "document", "kb"])
def test_source_reread_fails_closed_after_identity_or_content_changes(change):
    batches = chain()
    repo = GraphRepo(batches)
    if change == "missing":
        del repo.graph_rows["chunk-second"]
    elif change == "version":
        repo.graph_rows["chunk-second"].metadata["source_version"] = "v2"
    elif change == "content":
        repo.graph_rows["chunk-second"].content += " Altered fact."
    elif change == "document":
        repo.graph_rows["chunk-second"].knowledge_id = "different-doc"
    else:
        repo.graph_rows["chunk-second"].knowledge_base_id = "outside-kb"
    s = state()
    with pytest.raises((GraphRetrievalError, RetrievalScopeError),
                       match="GRAPH_SOURCE|RETRIEVAL_SCOPE_REJECTED"):
        run(fuse_paths(s, CTX, repo, paths(batches, max_paths=1)))
    assert s.merge_results == []


@pytest.mark.parametrize("budget", [dict(added_chars=45), dict(total_chars=45), dict(max_chunks=1)])
def test_path_budget_is_atomic_and_never_keeps_only_one_hop(budget):
    batches = chain()
    existing = row("ordinary", "Previously retained unrelated source.")
    s = state(merge_results=[existing])
    assert run(fuse_paths(s, CTX, GraphRepo(batches), paths(batches, max_paths=1), **budget)) == 0
    assert s.merge_results == [existing]
    assert s.graph_trace[0]["status"] == "BUDGET_DROPPED"
    assert s.graph_trace[0]["hops"] == 2


def test_graph_disabled_never_reads_graph_or_adds_model_calls():
    class Disabled(GraphRepo):
        async def get_graph_batches(self, context):
            raise AssertionError("Graph repository must not run when disabled")
    repo = Disabled(chain(), rows=[row()])
    model = Model([])
    reg = Registry(model)
    result = run(RetrievalPipeline(repo, reg).run(state(graph_enabled=False), CTX))
    assert result.result_ids == ("a",)
    assert not model.calls and not reg.lookups and not repo.graph_reads


def test_rejected_rerank_search_can_be_rescued_without_reviving_low_score_noise():
    batches = chain()
    repo = GraphRepo(batches, rows=[row("noise", "Unrelated low-score search material.", .01)])
    model = Model([[RerankResult(index=0, score=.01)]])
    s = state(query="Astrid Vale", rewrite_query="Astrid Vale", graph_enabled=True,
              rerank_model_id="existing-bge")
    final = run(RetrievalPipeline(repo, Registry(model)).run(s, CTX))
    assert len(model.calls) == 1 and repo.graph_loads == 1
    assert not s.rerank_results
    assert set(final.result_ids) == {"chunk-first", "chunk-second"}
    assert "Unrelated low-score" not in final.user_content
    assert all(r.score == 0 and r.match_type == "graph" for r in s.merge_results)
    assert any(t["hops"] == 2 and t["status"] == "SOURCE_RETAINED" for t in s.graph_trace)


def test_empty_graph_preserves_ordinary_rag_output_and_model_call_count():
    results = []
    for enabled in (False, True):
        repo = GraphRepo(rows=[row("ordinary", "Ordinary source evidence remains.", .8)])
        model = Model([[RerankResult(index=0, score=.8)]])
        final = run(RetrievalPipeline(repo, Registry(model)).run(
            state(graph_enabled=enabled, rerank_model_id="existing-bge"), CTX))
        results.append((final.result_ids, final.rendered_contexts, len(model.calls)))
    assert results[0] == results[1]


def test_empty_graph_and_no_source_still_signal_native_search_nothing():
    with pytest.raises(SearchNothing):
        run(RetrievalPipeline(GraphRepo(), Registry()).run(state(graph_enabled=True), CTX))


def test_empty_graph_keeps_existing_model_fallback_service_contract():
    from backend.weknora_port.generation import RouterPolicy
    from backend.weknora_port.models.types import ChatOptions
    from backend.weknora_port.service import TurnConfiguration
    from tests.weknora_port.test_service import REQ, runtime

    async def scenario():
        cfg = TurnConfiguration(state(graph_enabled=True), {"policy": RouterPolicy(mode="CHEAP_ONLY")},
            {"strategy": "model", "prompt": "NATIVE FALLBACK {{query}} {{kb_documents}}",
             "response": "CONFIGURED FIXED", "documents": lambda: "- title metadata",
             "options": ChatOptions(thinking=False)})
        service, durable, _, _, cheap, expensive, store, commit, _ = runtime(configuration=lambda _: cfg)
        graph_repo = GraphRepo()
        service.pipeline_factory = lambda turn, progress: RetrievalPipeline(
            graph_repo, Registry(), on_progress=progress)
        await service.accept_turn(REQ, "owner")
        await service.drain_background()
        assert not durable.failures and not service.background_errors
        assert len(cheap.calls) == 1 and not expensive.calls
        assert commit.calls[0].answer == "partial" and not commit.calls[0].references
        assert store.events[-1].type == "complete"
        assert graph_repo.graph_loads == 1 and not graph_repo.graph_reads
    run(scenario())
