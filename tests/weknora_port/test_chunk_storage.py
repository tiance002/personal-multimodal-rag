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
    assert child_rows[0].content==child_rows[0].source_content==" child literal "


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
