"""P31 regression checks for the native, scoped evidence path.

All inputs are synthetic; RetrievalPipeline, Search, Merge, top-K and Context
run as production code. No model or database adapter is involved.
"""
import asyncio

import pytest

from backend.weknora_port.models.types import CallContext, TrustedScope
from backend.weknora_port.retrieval.pipeline import PipelineState, RetrievalPipeline
from backend.weknora_port.retrieval.search import RetrievalScopeError, SearchTarget
from backend.weknora_port.retrieval.types import SearchResult


class MemoryRepository:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def hybrid_search(self, targets, params, context, query_embedding=None):
        self.calls.append((tuple(targets), params, context))
        return list(self.rows)


def _result(chunk_id, chunk_type, content, *, kb="kb", doc="doc"):
    # Deliberately provide no source_version, original-source quote, or offsets.
    return SearchResult(
        id=chunk_id,
        content=content,
        score=0.9,
        knowledge_id=doc,
        knowledge_base_id=kb,
        chunk_type=chunk_type,
        chunk_index=1,
        start_at=0,
        end_at=0,
        chunk_metadata={},
    )


def _context(*, document_ids=()):
    return CallContext(
        request_id="p31-native-evidence-test",
        scope=TrustedScope("tenant-1", ("kb",), tuple(document_ids)),
    )


def _state(*, target=None):
    return PipelineState(
        query="retrieve these synthetic passages",
        targets=(target or SearchTarget("kb"),),
        enable_rewrite=False,
        enable_expansion=False,
        rerank_model_id="",  # disable the provider stage for this path test
        embedding_top_k=10,
        rerank_top_k=10,
    )


def test_native_pipeline_puts_summary_and_original_image_text_in_context_without_source_proof():
    async def run():
        rows = [
            _result("summary-1", "summary", "summary passage, no provenance fields"),
            _result("text-1", "text", "ordinary text passage"),
            _result("ocr-1", "image_ocr", "OCR-derived text passage"),
            _result("caption-1", "image_caption", "caption-derived text passage"),
        ]
        assert all(not row.chunk_metadata and row.start_at == row.end_at == 0 for row in rows)
        repo = MemoryRepository(rows)
        pipeline = RetrievalPipeline(repo, registry=object())
        state = _state()

        finalized = await pipeline.run(state, _context())

        assert len(repo.calls) == 1
        assert {row.chunk_type for row in state.merge_results} == {
            "summary", "text", "image_ocr", "image_caption"
        }
        assert set(finalized.result_ids) == {row.id for row in rows}
        for row in rows:
            assert row.content in finalized.rendered_contexts
            assert row.content in finalized.user_content

    asyncio.run(run())


@pytest.mark.parametrize(
    ("document_ids", "target", "bad_row"),
    [
        ((), SearchTarget("kb"), _result("foreign-kb", "text", "out of scope", kb="other-kb")),
        (("doc-allowed",), SearchTarget("kb", target_type="knowledge", knowledge_ids=("doc-allowed",)),
         _result("foreign-doc", "summary", "out of document scope", doc="other-doc")),
    ],
)
def test_native_pipeline_rejects_search_results_outside_server_resolved_scope(
    document_ids, target, bad_row
):
    async def run():
        repo = MemoryRepository([bad_row])
        pipeline = RetrievalPipeline(repo, registry=object())
        state = _state(target=target)

        with pytest.raises(RetrievalScopeError):
            await pipeline.run(state, _context(document_ids=document_ids))

        assert len(repo.calls) == 1
        assert state.merge_results == []

    asyncio.run(run())
