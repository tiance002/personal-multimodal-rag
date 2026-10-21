from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.weknora_port.storage.chunks import DocumentScope, prepare_chunks


SCOPE = DocumentScope(1,"owner","kb","doc","version")


def parsed(source, role, seq, content, parent=None, header=""):
    return SimpleNamespace(owner_id="owner",knowledge_base_id="kb",document_id="doc",
        source_version="version",source_id=source,role=role,seq=seq,content=content,
        start=seq*10,end=seq*10+len(content),parent_source_id=parent,context_header=header,metadata={})


def ids():
    it = iter(["parent1","parent2","child1","child2","child3"])
    return lambda: next(it)


def test_parent_rows_not_indexed_child_evidence_not_replaced_with_embedding_header():
    rows=[parsed("p0","parent",0,"parent text"),parsed("p1","parent",1,"other parent"),
          parsed("c0","child",0," child literal ","p0","# Section"),
          parsed("c1","child",1,"second", "p1"),parsed("c2","child",2,"standalone")]
    plan=prepare_chunks(SCOPE,rows,title=" Document title ",new_id=ids())
    parent_rows=[row for row in plan.persisted if row.chunk_type=="parent_text"]
    child_rows=[row for row in plan.persisted if row.chunk_type=="text"]
    assert parent_rows[0].next_chunk_id==parent_rows[1].id
    assert child_rows[0].parent_chunk_id==parent_rows[0].id
    assert all(not row.pre_chunk_id and not row.next_chunk_id for row in child_rows)
    assert [row.source_id for row in plan.indexed]==["child1","child2","child3"]
    assert plan.indexed[0].content=="Document title\n# Section\n\nchild literal"
    assert child_rows[0].content==" child literal "
    # Fixed CreateChunks backfills immutable parser output only on first edit.
    assert child_rows[0].source_content==""


def test_standalone_chunks_link_and_empty_children_are_skipped():
    plan=prepare_chunks(SCOPE,[parsed("a","child",0,"one"),parsed("empty","child",1,"  "),
                              parsed("b","child",2,"two")],new_id=ids())
    assert len(plan.persisted)==2
    assert plan.persisted[0].next_chunk_id==plan.persisted[1].id
    assert plan.persisted[1].pre_chunk_id==plan.persisted[0].id


def test_wrong_owner_kb_document_or_version_fail_before_allocating_ids():
    def should_not_run(): raise AssertionError("identity allocator must not run")
    for scope in (replace(SCOPE,owner_id="other"),replace(SCOPE,knowledge_base_id="other"),
                  replace(SCOPE,knowledge_id="other"),replace(SCOPE,source_version="other")):
        with pytest.raises(PermissionError):
            prepare_chunks(scope,[parsed("a","child",0,"text")],new_id=should_not_run)


def test_child_links_follow_sorted_indexes_and_parent_cannot_reference_another_child():
    plan=prepare_chunks(SCOPE,[parsed("later","child",2,"later"),parsed("first","child",0,"first")],new_id=ids())
    assert plan.persisted[0].content=="first"
    assert plan.persisted[0].next_chunk_id==plan.persisted[1].id
    plan=prepare_chunks(SCOPE,[parsed("a","child",0,"one",parent="b"),
                              parsed("b","child",1,"two")],new_id=ids())
    assert not plan.persisted[0].parent_chunk_id


def test_mixed_parent_child_links_consecutive_standalone_runs_only():
    rows = [parsed("p0", "parent", 0, "first parent"),
            parsed("p1", "parent", 1, "second parent"),
            parsed("d", "child", 5, "last standalone"),
            parsed("c0", "child", 0, "first child", parent="p0"),
            parsed("b", "child", 2, "second standalone"),
            parsed("a", "child", 1, "first standalone"),
            parsed("c1", "child", 3, "second child", parent="p1"),
            parsed("c", "child", 4, "third standalone")]
    generated = iter(f"id-{i}" for i in range(len(rows)))
    plan = prepare_chunks(SCOPE, rows, new_id=lambda: next(generated))
    by_source = {row.metadata["parsed_source_id"]: row for row in plan.persisted}
    assert by_source["p0"].next_chunk_id == by_source["p1"].id
    assert by_source["c0"].parent_chunk_id == by_source["p0"].id
    assert by_source["c1"].parent_chunk_id == by_source["p1"].id
    assert all(not by_source[key].pre_chunk_id and not by_source[key].next_chunk_id
               for key in ("c0", "c1"))
    assert by_source["a"].pre_chunk_id == by_source["b"].next_chunk_id == ""
    assert by_source["a"].next_chunk_id == by_source["b"].id
    assert by_source["b"].pre_chunk_id == by_source["a"].id
    assert by_source["c"].pre_chunk_id == by_source["d"].next_chunk_id == ""
    assert by_source["c"].next_chunk_id == by_source["d"].id
    assert by_source["d"].pre_chunk_id == by_source["c"].id
    assert all(row.scope == SCOPE for row in plan.persisted)


@pytest.mark.parametrize("invalid_parent", ["missing", "c"])
def test_mixed_plan_unresolved_parent_is_not_an_adjacency_anchor(invalid_parent):
    rows = [parsed("p", "parent", 0, "parent"),
            parsed("a", "child", 0, "before"),
            parsed("bad", "child", 1, "unresolved parent", parent=invalid_parent),
            parsed("c", "child", 2, "after"), parsed("d", "child", 3, "last")]
    generated = iter(f"id-{i}" for i in range(len(rows)))
    plan = prepare_chunks(SCOPE, rows, new_id=lambda: next(generated))
    by_source = {row.metadata["parsed_source_id"]: row for row in plan.persisted}
    assert by_source["bad"].parent_chunk_id == ""  # Existing mapping contract.
    assert all(not by_source[key].pre_chunk_id and not by_source[key].next_chunk_id
               for key in ("a", "bad"))
    assert by_source["c"].pre_chunk_id == ""
    assert by_source["c"].next_chunk_id == by_source["d"].id
    assert by_source["d"].pre_chunk_id == by_source["c"].id


@pytest.mark.parametrize("anchor, expected", [
    ("before", ("before",)),
    ("coding", ("coding", "dataset")),
    ("dataset", ("coding", "dataset")),
])
def test_merge_reads_new_standalone_links_and_stops_at_parent_child(anchor, expected):
    import asyncio
    from copy import deepcopy
    from backend.weknora_port.models.types import CallContext, TrustedScope
    from backend.weknora_port.retrieval.merge import merge
    from backend.weknora_port.retrieval.pipeline import PipelineState
    from backend.weknora_port.retrieval.search import SearchTarget
    from backend.weknora_port.retrieval.types import SearchResult

    # Public source excerpts exercise source-order adjacency, not a runtime
    # chapter inference. The synthetic anchor is already admitted by rerank.
    rows = [parsed("p", "parent", 0, "parent-only context"),
            parsed("before", "child", 55, "Unrelated prior section"),
            parsed("nested", "child", 56, "nested child", parent="p"),
            parsed("coding", "child", 57, "Coding time!\nour ingredients..."),
            parsed("dataset", "child", 58,
                   "Kaggle's Cat vs Dog dataset (25k dog/cat pictures)")]
    generated = iter(f"id-{i}" for i in range(len(rows)))
    plan = prepare_chunks(SCOPE, rows, new_id=lambda: next(generated))
    stored = {row.metadata["parsed_source_id"]: row for row in plan.persisted}
    sources = {row.id: SearchResult(row.id, row.content, .8,
        knowledge_id=SCOPE.knowledge_id, knowledge_base_id=SCOPE.knowledge_base_id,
        chunk_type=row.chunk_type, chunk_index=row.chunk_index,
        parent_chunk_id=row.parent_chunk_id, pre_chunk_id=row.pre_chunk_id,
        next_chunk_id=row.next_chunk_id, chunk_metadata=dict(row.metadata))
        for row in plan.persisted}

    class Repository:
        async def get_chunks(self, identifiers, context):
            return {key: sources[key] for key in identifiers if key in sources}

    state = PipelineState(query="source question", targets=(SearchTarget("kb"),),
        rerank_results=[deepcopy(sources[stored[anchor].id])])
    context = CallContext("adjacency", TrustedScope("owner", ("kb",)))
    asyncio.run(merge(state, context, Repository()))
    assert len(state.merge_results) == 1
    result = state.merge_results[0]
    assert result.content == "\n\n".join(stored[key].content for key in expected)
    assert set(result.sub_chunk_ids) == {stored[key].id for key in expected if key != anchor}
    assert result.id == stored[anchor].id
    assert (result.knowledge_base_id, result.knowledge_id) == ("kb", "doc")
    assert result.chunk_metadata["source_version"] == "version"
    assert "parent-only context" not in result.content
