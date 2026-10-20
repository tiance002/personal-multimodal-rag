"""P21 regressions: real orchestration, injected offline repository/providers."""
import asyncio
from types import SimpleNamespace

import pytest

from backend.weknora_port.generation import GenerationRequest, RouterPolicy, prepare_generation
from backend.weknora_port.retrieval.context import FinalizedContext
from backend.weknora_port.retrieval.pipeline import RetrievalPipeline
from backend.weknora_port.retrieval.query import needs_retrieval, resolve_intent
from backend.weknora_port.service import TurnConfiguration
from backend.weknora_port.models.types import ChatOptions
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


def test_source_scoped_followup_with_history_still_retrieves_when_model_says_follow_up():
    async def scenario():
        question = '课件还提到这些远距离通信方式可能遇到什么问题？请根据课件回答。'
        history = [SimpleNamespace(query='课件介绍了哪些远距离通信方式？', answer='课件提到了几种远距离通信方式。')]
        s = state(query=question, intent='', history=history, enable_rewrite=True,
                  chat_model_id='cheap')
        model = Model(['{"rewrite_query":"课件中提到的远距离通信方式可能遇到什么问题","intent":"follow_up","image_description":""}'])
        repo = Repo([row(content='远距离通信可能受到信号衰减和干扰。')])
        await RetrievalPipeline(repo, Registry(model)).run(s, CTX)
        assert s.intent == 'kb_search' and s.needs_retrieval
        assert len(repo.calls) == 1 and s.search_results
        assert repo.calls[0][1].query_text == '课件中提到的远距离通信方式可能遇到什么问题'

    run(scenario())


def test_history_only_followup_and_attached_document_question_keep_bypass_intents():
    history_only = state(query='上面第二点展开讲讲', intent='follow_up',
                         history=[SimpleNamespace(answer='第二点的内容')])
    attached_doc = state(query='根据文档总结一下', intent='doc_only',
                         attachment_prompt='actual attached document content')
    absent_doc = state(query='请根据课件回答', intent='follow_up',
                       history=[SimpleNamespace(answer='earlier answer')],
                       attachment_prompt='<no_document_attached />')
    assert resolve_intent(history_only) == 'follow_up'
    assert resolve_intent(attached_doc) == 'doc_only'
    assert resolve_intent(absent_doc) == 'kb_search'


@pytest.mark.parametrize('intent', ['follow_up', 'image_only', 'chitchat'])
def test_image_does_not_replace_explicit_courseware_source(intent):
    s = state(query='这张图里的方案与课件相比有什么优缺点？请根据课件回答。',
              intent=intent, images=('real-image',),
              history=[SimpleNamespace(answer='earlier courseware answer')])
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
    # Public citations remain disabled; S3/S4 use supplied filenames rather
    # than the retired P21 prose marker. Internal identity is still registered.
    assert 'only the exact supplied filenames that support the answer' in prepared.messages[0].content
    assert 'Cite a knowledge chunk with exactly' not in prepared.messages[0].content
    assert E.content in prepared.messages[-1].content
    assert prepared.registry.snapshot().chunks[0][1][:3] == (E.id, E.knowledge_id, E.knowledge_base_id)


@pytest.mark.parametrize('strategy', ['model', 'fixed'])
def test_no_evidence_respects_native_fallback_and_replay(strategy):
    # P28 Owner reversal: an empty KB must respect model/fixed, not force P21's
    # fixed Chinese refusal. Scope/gate/citation counterexamples remain below.
    async def scenario():
        listings = []
        def documents():
            listings.append(True)
            return '- document title (metadata only)'
        fallback = {'strategy': strategy, 'prompt': 'FALLBACK {{query}} {{kb_documents}}',
                    'response': 'configured fixed reply', 'options': ChatOptions(thinking=False),
                    'documents': documents}
        service, repo, retrieval, co, cheap, expensive, store, commit, counts = runtime(
            configuration=lambda turn: TurnConfiguration(state(), {'policy': RouterPolicy(mode='CHEAP_ONLY')}, fallback))
        retrieval.rows = []
        accepted = await service.accept_turn(REQ, 'owner')
        await service.drain_background()
        assert not repo.failures
        assert len(retrieval.calls) == 1
        assert len(cheap.calls) == (strategy == 'model') and not expensive.calls
        assert len(commit.calls) == 1
        assert commit.calls[0].answer == ('partial' if strategy == 'model' else 'configured fixed reply')
        assert bool(listings) == (strategy == 'model')
        if strategy == 'model':
            rendered = '\n'.join(message.content for message in cheap.calls[0][0])
            assert 'FALLBACK extract source' in rendered and 'metadata only' in rendered
            assert 'historic question' in rendered and 'historic answer' in rendered
            assert 'memory prompt' not in rendered
        assert not commit.calls[0].references
        assert store.events[-1].type == 'complete'
        # Replay reuses the same durable answer without another call.
        before = len(store.events)
        replay = await service.accept_turn(REQ, 'owner')
        assert replay.replay_only and await service.run_turn(replay) is None
        assert len(store.events) == before
        assert len(cheap.calls) == (strategy == 'model')
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
