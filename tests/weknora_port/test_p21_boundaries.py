"""P21 regressions: real orchestration, injected offline repository/providers."""
import asyncio
from types import SimpleNamespace

import pytest

from backend.weknora_port.generation import GenerationRequest, RouterPolicy, prepare_generation
from backend.weknora_port.retrieval.context import FinalizedContext
from backend.weknora_port.retrieval.pipeline import RetrievalPipeline
from backend.weknora_port.retrieval.query import needs_retrieval, resolve_intent
from backend.weknora_port.service import TurnConfiguration, KNOWLEDGE_EVIDENCE_INSUFFICIENT
from test_generation import CTX, E, event, setup
from test_pipeline import Model, Registry, Repo, row, state
from test_service import runtime, REQ


def run(value):
    return asyncio.run(value)


@pytest.mark.parametrize('intent', ['', 'general_qa', 'UNKNOWN', 'follow_up', 'image_only', 'doc_only'])
def test_p19_utc_empty_history_never_bypasses_retrieval(intent):
    s = state(query='utc时间转换北京时间', intent=intent)
    repo = Repo([row()])
    result = run(RetrievalPipeline(repo, Registry()).run(s, CTX))
    assert s.intent == 'kb_search' and s.needs_retrieval
    assert len(repo.calls) == 1 and result.result_ids == ('a',)


@pytest.mark.parametrize('intent', ['chitchat', 'greeting', 'follow_up', 'image_only', 'doc_only', 'web_search', 'unknown'])
def test_original_explicit_kb_request_overrides_wrong_classification(intent):
    s = state(query='请依据知识库回答 UTC 时间如何转换为北京时间', intent=intent,
              history=[SimpleNamespace(answer='old answer')], images=('image',), attachment_prompt='actual attachment')
    assert resolve_intent(s) == 'kb_search'


@pytest.mark.parametrize('intent,question,history,images,attachment', [
    ('greeting', '你好！', [], (), ''),
    ('chitchat', '你是谁？', [], (), ''),
    ('chitchat', '讲个笑话', [], (), ''),
    ('chitchat', 'Tell me a funny story about a cat.', [], (), ''),
    ('greeting', '你好，今天过得怎么样？', [], (), ''),
    ('follow_up', '上面第二点展开讲讲', [SimpleNamespace(answer='prior answer')], (), ''),
    ('image_only', '这张图是什么意思', [], ('real-image',), ''),
    ('doc_only', '根据文档总结一下', [], (), 'real document content'),
])
def test_legitimate_bypass_prerequisites_remain_available(intent, question, history, images, attachment):
    s = state(query=question, intent=intent, history=history, images=images, attachment_prompt=attachment)
    assert resolve_intent(s) == intent
    assert not needs_retrieval(intent)


@pytest.mark.parametrize('question', ['请查找相关文档', '列出所有文档', '帮我找类似文件', 'Search for related documents', 'List all stored reports'])
@pytest.mark.parametrize('intent', ['follow_up', 'image_only', 'doc_only', 'chitchat'])
def test_explicit_source_acquisition_precedes_attachment_and_history_exceptions(question, intent):
    s = state(query=question, intent=intent, history=[SimpleNamespace(answer='old answer')],
              images=('real-image',), attachment_prompt='real attached document')
    assert resolve_intent(s) == 'kb_search'


def test_disabled_default_keeps_internal_evidence_and_source_identity():
    req = GenerationRequest('run', 'session', 'assistant', 'question',
                            FinalizedContext('question', '', (E.id,)), evidence=(E,))
    assert req.citations_enabled is False
    prepared = prepare_generation(req)
    assert 'Source citations are disabled' in prepared.messages[0].content
    assert 'Cite a knowledge chunk with exactly' not in prepared.messages[0].content
    assert E.content in prepared.messages[-1].content
    assert prepared.registry.snapshot().chunks[0][1][:3] == (E.id, E.knowledge_id, E.knowledge_base_id)


@pytest.mark.parametrize('fallback', [None, {'strategy': 'model', 'prompt': 'FREE ANSWER {{query}}',
                                          'response': 'old fallback', 'options': None,
                                          'documents': lambda: pytest.fail('must not list sources for free-form answer')}])
def test_no_evidence_commits_fixed_refusal_without_answer_model(fallback):
    async def scenario():
        service, repo, retrieval, co, cheap, expensive, store, commit, counts = runtime(
            configuration=lambda turn: TurnConfiguration(state(), {'policy': RouterPolicy(mode='CHEAP_ONLY')}, fallback))
        retrieval.rows = []
        accepted = await service.accept_turn(REQ, 'owner')
        await service.drain_background()
        assert not repo.failures
        assert len(retrieval.calls) == 1
        assert not cheap.calls and not expensive.calls
        assert len(commit.calls) == 1 and commit.calls[0].answer == KNOWLEDGE_EVIDENCE_INSUFFICIENT
        assert not commit.calls[0].references
        assert store.events[-1].type == 'complete'
        # Replay reuses the same durable refusal without another call.
        before = len(store.events)
        replay = await service.accept_turn(REQ, 'owner')
        assert replay.replay_only and await service.run_turn(replay) is None
        assert len(store.events) == before
    run(scenario())


def test_wrong_rewrite_for_explicit_kb_cannot_apply_nonretrieval_prompt():
    async def scenario():
        s = state(query='根据知识库查找协议', enable_rewrite=True, chat_model_id='cheap',
                  intent_system_prompts={'chitchat': 'FREE CHAT OVERRIDE'})
        model = Model(['{"rewrite_query":"讲个笑话","intent":"chitchat"}'])
        repo = Repo([row()])
        result = await RetrievalPipeline(repo, Registry(model)).run(s, CTX)
        assert repo.calls and s.needs_retrieval
        assert result.system_prompt_override == ''
    run(scenario())


def test_default_off_stream_final_and_replay_keep_no_public_references():
    async def scenario():
        req = GenerationRequest('run', 'session', 'assistant', 'question',
            FinalizedContext('question', '', (E.id,)), evidence=(E,), policy=RouterPolicy(mode='CHEAP_ONLY'))
        co, cheap, expensive, store, commit, life = setup([
            event('answer <re'), event('f> <ref id="c1"/>'),
            event('</ref> ![x](https://example.test/i.png) [link](https://example.test)', done=True, finish='stop', final=True)])
        result = await co.run(req, CTX)
        assert result.references == () and '<ref' not in result.answer and '</ref' not in result.answer
        assert '![x](https://example.test/i.png)' in result.answer
        assert not any(e.type == 'references' for e in store.events)
        assert ''.join(e.content for e in store.events if e.type == 'answer') == result.answer
        assert commit.calls[0].answer == result.answer
        assert not expensive.calls and len(cheap.calls) == 1
    run(scenario())
