from backend.weknora_port.retrieval.filter_top_k import filter_top_k
from backend.weknora_port.retrieval.types import SearchResult, SearchState


def row(id, score, doc="", kind="text", index=0):
    return SearchResult(id,"",score,knowledge_id=doc,chunk_type=kind,chunk_index=index)


def test_merge_first_then_exact_source_tiebreaks_before_truncating():
    rows = [None, row("c",.7,"b"), row("d",.7,"a","text",1),
            row("b",.7,"a","text",0), row("z",.7,"a","faq"), row("high",.8)]
    original_search = [row("search",1)]
    state = SearchState(rerank_top_k=3,search_results=original_search,merge_results=rows)
    filter_top_k(state)
    assert [r.id for r in state.merge_results] == ["high","z","b"]
    assert state.search_results is original_search


def test_zero_limit_sorts_without_truncating_and_no_retrieval_preserves_order():
    rows = [row("b",.4),row("a",.9),None]
    state = SearchState(needs_retrieval=False,rerank_top_k=0,rerank_results=rows)
    filter_top_k(state)
    assert state.rerank_results == rows
    state.needs_retrieval = True
    filter_top_k(state)
    assert [r.id if r else None for r in state.rerank_results] == ["a","b",None]


def test_search_fallback_and_equal_doc_type_index_uses_id():
    state = SearchState(rerank_top_k=1,search_results=[row("b",.5),row("a",.5)])
    filter_top_k(state)
    assert state.search_results[0].id == "a"
