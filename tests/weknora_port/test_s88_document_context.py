"""Synthetic current-source projection; no Provider, database or frozen raw edits."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import hashlib
import pytest

from backend.weknora_port.models.types import CallContext, TrustedScope
from backend.weknora_port.retrieval.context import into_chat_message
from backend.weknora_port.retrieval.pipeline import PipelineState, RetrievalPipeline
from backend.weknora_port.retrieval.search import SearchTarget
from backend.weknora_port.retrieval.search import RetrievalScopeError
from backend.weknora_port.retrieval.strategy_executor import RoundResult
from backend.weknora_port.retrieval.types import SearchResult
from backend.weknora_port.generation.context import Evidence, prepare_generation
from backend.weknora_port.generation.types import GenerationRequest
from backend.weknora_port.retrieval.source_context import project_document_context


KB = '00000000-0000-4000-8000-000000000001'
DOC = '00000000-0000-4000-8000-000000000002'
CTX = CallContext('offline', TrustedScope('owner', (KB,), (DOC,)))


def source(id='body', content='培训人数为2.2万人。', **updates):
    values = dict(knowledge_id=DOC, knowledge_base_id=KB, chunk_index=1,
        start_at=100, end_at=100 + len(content), metadata={'source_version':'v1'},
        chunk_metadata={'source_version':'v1'})
    values.update(updates)
    return SearchResult(id, content, .8, **values)


def heading(**updates):
    text = '2019年北松市政府工作报告\n回顾2018年全市工作。'
    values = dict(chunk_index=0, start_at=0, end_at=len(text))
    values.update(updates)
    return source('head', text, **values)


def state(**updates):
    values = dict(query='本年度培训了多少人？', rewrite_query='本年度培训了多少人？',
        targets=(SearchTarget(KB, 'knowledge', (DOC,)),), enable_rewrite=False,
        enable_expansion=False, source_evidence_mode='bounded_source_v1',
        rerank_context_enabled=True, merge_results=[source()])
    values.update(updates)
    return PipelineState(**values)


def prepared(s, finalized):
    return prepare_generation(GenerationRequest('r', 's', 'm', s.query, finalized,
        evidence=tuple(Evidence.from_result(row) for row in s.merge_results), citations_enabled=True))


class Repo:
    def __init__(self, head=None):
        self.head = head or heading()
        self.calls = []

    async def get_first_source_chunks(self, ids, context):
        self.calls.append((ids, context))
        return {DOC:deepcopy(self.head)}


def test_pipeline_adds_current_head_as_separate_real_evidence(monkeypatch):
    async def run():
        first = heading()
        class Repo:
            calls = []
            async def get_first_source_chunks(self, ids, context):
                self.calls.append((ids, context))
                return {DOC:deepcopy(first)}
        repo = Repo()
        async def selected(self, current, context, **kwargs):
            return RoundResult(current)
        monkeypatch.setattr(RetrievalPipeline, 'retrieve_once', selected)
        s = state(); original = deepcopy(s.merge_results[0])
        finalized = await RetrievalPipeline(repo, object()).run(s, CTX)
        assert finalized.result_ids == ('body', 'head')
        assert s.merge_results[0] == original
        assert repo.calls == [((DOC,), CTX)]
        wire = prepared(s, finalized)
        assert first.content in wire.messages[-1].content
        refs = {ref[0]:handle for handle, ref in wire.registry.snapshot().chunks}
        assert refs == {'body':'c1', 'head':'c2'}
        assert 'supports="document_identity_only"' in wire.messages[-1].content
    asyncio.run(run())


def test_strategy_adds_head_only_after_retrieval_selection_and_coverage(monkeypatch):
    async def run():
        async def selected(self, current, context, **kwargs):
            current.merge_results = [source()]
            return RoundResult(current)
        monkeypatch.setattr(RetrievalPipeline, 'retrieve_once', selected)
        s = state(retrieval_strategy_mode='query_structure_v1', query_type='DIRECT')
        repo = Repo()
        finalized = await RetrievalPipeline(repo, object()).run(s, CTX)
        assert finalized.result_ids == ('body', 'head') and repo.calls == [((DOC,), CTX)]
        assert len(s.strategy_rounds) == 1
        assert [row.id for row in s.strategy_rounds[0].merge_results] == ['body']
        assert s.strategy_coverage['sq-1']['selected_ids'] == ['body']
        assert s.strategy_coverage['sq-1']['candidate_ids'] == ['body']
        prepared(s, finalized)
    asyncio.run(run())


def test_exact_interval_and_current_source_are_not_cleaned_or_rewritten():
    async def run():
        first = heading(); first.content = '原文 https://example.test/r/21 <tag>\n' + '源'*500
        first.end_at = len(first.content)
        repo = Repo(first); s = state(); original = deepcopy(s.merge_results)
        await project_document_context(s, CTX, repo)
        assert s.merge_results[:-1] == original and repo.head == first
        quote = s.merge_results[-1]
        assert quote.id == first.id and quote.content == first.content[:400]
        assert (quote.start_at, quote.end_at) == (0, 400)
        assert quote.metadata['source_document_context'] == {
            'source_chunk_id':'head', 'document_id':DOC, 'source_version':'v1',
            'start':0, 'end':400,
            'quote_sha256':hashlib.sha256(first.content[:400].encode()).hexdigest()}
        assert quote.content_revision == 0 and not quote.content_rewritten
        assert '原文 https://example.test/r/21 &lt;tag&gt;' in prepared(s, await into_chat_message(s)).messages[-1].content
        admitted = deepcopy(s.merge_results)
        await project_document_context(s, CTX, repo)
        assert s.merge_results == admitted and s.evidence_trace[-1]['decision'] == 'ALREADY_SELECTED'
    asyncio.run(run())


@pytest.mark.parametrize('updates', [
    {'rerank_context_enabled':False}, {'source_evidence_mode':'off'},
    {'needs_retrieval':False}, {'merge_results':[]},
    {'merge_results':[source(chunk_type='faq')]},
])
def test_optout_or_no_original_selected_document_has_no_read_or_change(updates):
    async def run():
        s = state(**updates); original = deepcopy(s.merge_results); repo = Repo()
        await project_document_context(s, CTX, repo)
        assert s.merge_results == original and not repo.calls
    asyncio.run(run())


@pytest.mark.parametrize('updates', [
    {'metadata':{'source_version':'old'}}, {'chunk_metadata':{'source_version':'old'}},
    {'chunk_type':'summary'}, {'chunk_index':2}, {'is_enabled':False},
    {'content_revision':1}, {'content_rewritten':True}, {'end_at':999},
])
def test_stale_generated_or_unverifiable_head_is_omitted(updates):
    async def run():
        s = state(); original = deepcopy(s.merge_results)
        await project_document_context(s, CTX, Repo(replace(heading(), **updates)))
        assert s.merge_results == original
        assert s.evidence_trace[-1]['reason'] == 'HEAD_IDENTITY_UNVERIFIABLE'
    asyncio.run(run())


@pytest.mark.parametrize('updates', [{'knowledge_id':'foreign'}, {'knowledge_base_id':'foreign'}])
def test_foreign_document_or_kb_is_terminal_scope_failure(updates):
    async def run():
        s = state(); original = deepcopy(s.merge_results)
        with pytest.raises(RetrievalScopeError):
            await project_document_context(s, CTX, Repo(replace(heading(), **updates)))
        assert s.merge_results == original
    asyncio.run(run())


def test_already_selected_head_is_not_duplicated_and_inconsistent_identity_is_rejected():
    async def run():
        first = heading(); s = state(merge_results=[source(), deepcopy(first)])
        original = deepcopy(s.merge_results)
        await project_document_context(s, CTX, Repo(first))
        assert s.merge_results == original and s.evidence_trace[-1]['decision'] == 'ALREADY_SELECTED'
        s.merge_results[-1].content = 'other'
        s.merge_results[-1].end_at = len('other')
        with pytest.raises(RetrievalScopeError):
            await project_document_context(s, CTX, Repo(first))
    asyncio.run(run())


def test_multiple_selected_chunks_share_one_head_and_query_does_not_select_it():
    async def run():
        s = state(merge_results=[source(), source('second', '其他事实', chunk_index=2)])
        s.query = s.rewrite_query = 'unrelated query with no source aliases'
        repo = Repo(); await project_document_context(s, CTX, repo)
        assert repo.calls == [((DOC,), CTX)]
        assert [row.id for row in s.merge_results] == ['body', 'second', 'head']
        assert s.query == s.rewrite_query == 'unrelated query with no source aliases'
    asyncio.run(run())


def test_generation_encoder_budget_omits_only_optional_head():
    async def run():
        first = heading(); first.content = '源'*500; first.end_at = 500
        full = state(); await project_document_context(full, CTX, Repo(first))
        raw = await into_chat_message(full)
        wire = prepared(full, raw).messages[-1].content
        encoded = wire[wire.index('<retrieval'):wire.index('</retrieval>')+len('</retrieval>')]
        assert len(encoded) > len(raw.rendered_contexts)
        s = state(graph_context_char_limit=len(raw.rendered_contexts))
        original = deepcopy(s.merge_results)
        # The raw Context fits this limit; the actual generation encoder does not.
        await project_document_context(s, CTX, Repo(first))
        assert s.merge_results == original
        assert s.evidence_trace[-1]['reason'] == 'FINAL_CONTEXT_BUDGET'
        prepared(s, await into_chat_message(s))
    asyncio.run(run())


@pytest.mark.parametrize('error', [RuntimeError('offline'), PermissionError('denied'), RetrievalScopeError()])
def test_optional_read_unavailable_is_explicit_but_permission_failures_are_terminal(error):
    async def run():
        class Failed:
            async def get_first_source_chunks(self, ids, context):
                raise error
        s = state(); original = deepcopy(s.merge_results)
        if isinstance(error, (PermissionError, RetrievalScopeError)):
            with pytest.raises(type(error)):
                await project_document_context(s, CTX, Failed())
        else:
            await project_document_context(s, CTX, Failed())
            assert s.evidence_trace[-1]['reason'] == 'SOURCE_READBACK_UNAVAILABLE'
        assert s.merge_results == original
    asyncio.run(run())


@pytest.mark.parametrize('stop', ['run_deadline', 'model_deadline', 'cancel'])
def test_optional_read_cannot_swallow_cancel_or_deadline(stop):
    from backend.weknora_port.models.types import CallCancelled, RunDeadlineExceeded
    async def run():
        started, closed, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        class Blocked:
            async def get_first_source_chunks(self, ids, context):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    closed.set()
        ctx = replace(CTX, cancelled=cancelled, deadline_kind='model' if stop=='model_deadline' else 'run',
            deadline=None if stop=='cancel' else asyncio.get_running_loop().time()+.03)
        s = state(); original = deepcopy(s.merge_results)
        task = asyncio.create_task(project_document_context(s, ctx, Blocked()))
        await asyncio.wait_for(started.wait(), 1)
        if stop=='cancel':
            cancelled.set()
        with pytest.raises(CallCancelled if stop=='cancel' else RunDeadlineExceeded):
            await asyncio.wait_for(task, .5)
        assert closed.is_set() and s.merge_results == original
    asyncio.run(run())
