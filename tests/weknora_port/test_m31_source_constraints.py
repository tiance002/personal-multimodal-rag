"""M3.1 source constraints; offline orchestration, no external calls."""
import asyncio
from types import SimpleNamespace

import pytest

from backend.weknora_port.conversation import Attachment, attachment_prompt
from backend.weknora_port.generation import RouterPolicy
from backend.weknora_port.http_api import MessageCreate
from backend.weknora_port.models.types import ChatOptions
from backend.weknora_port.retrieval.pipeline import RetrievalPipeline
from backend.weknora_port.retrieval.query import resolve_intent
from backend.weknora_port.service import TurnConfiguration
from pydantic import ValidationError
from test_pipeline import CTX, Model, Registry, Repo, row, state
from test_service import REQ, runtime


@pytest.mark.parametrize('question', [
    '不要根据课件回答，只解释一般原理。',
    '不用依据课件回答，只解释一般原理。',
    '不要根据知识库回答，只展开上面第二点。',
])
def test_negated_source_does_not_authorize_new_facts_from_history(question):
    s = state(query=question, intent='follow_up', history=[SimpleNamespace(answer='prior answer')])
    # S8.5: explaining principles is not a pure history-only transformation.
    assert resolve_intent(s) == 'kb_search'


@pytest.mark.parametrize('question', [
    '不要根据课件回答，但请搜索知识库中的相关资料。',
    '不要根据课件回答，请依据教材回答。',
])
def test_separate_positive_source_request_still_retrieves(question):
    s = state(query=question, intent='follow_up', history=[SimpleNamespace(answer='prior answer')])
    assert resolve_intent(s) == 'kb_search'


def test_not_only_courseware_is_not_source_negation():
    s = state(query='不要只根据课件回答，还要结合一般原理。', intent='follow_up',
              history=[SimpleNamespace(answer='prior answer')])
    assert resolve_intent(s) == 'kb_search'


def test_courseware_evaluation_of_attached_scheme_retrieves_and_keeps_attachment():
    async def scenario():
        attachment = attachment_prompt((Attachment('方案.txt', content='方案中的待评估内容'),))
        s = state(query='请根据课件评价我上传的方案文档。', enable_rewrite=True,
                  chat_model_id='cheap', attachment_prompt=attachment)
        model = Model(['{"rewrite_query":"课件对方案的评价","intent":"doc_only"}'])
        repo = Repo([row(content='真实课件证据')])
        finalized = await RetrievalPipeline(repo, Registry(model)).run(s, CTX)
        assert s.intent == 'kb_search' and repo.calls
        assert '真实课件证据' in finalized.rendered_contexts
        assert attachment in finalized.user_content
        assert '方案中的待评估内容' in finalized.user_content
    asyncio.run(scenario())


def test_actual_attached_courseware_self_summary_keeps_document_path():
    s = state(query='请根据课件总结一下。', intent='doc_only',
              attachment_prompt=attachment_prompt((Attachment('课件.pdf', content='课件正文'),)))
    assert resolve_intent(s) == 'doc_only'


def test_mixed_source_model_fallback_keeps_current_attachment():
    async def scenario():
        attachment = attachment_prompt((Attachment('方案.txt', content='方案中的待评估内容'),))
        fallback = {'strategy': 'model', 'prompt': 'FALLBACK {{query}}', 'response': '',
                    'options': ChatOptions(thinking=False), 'documents': lambda: ''}
        service, repo, retrieval, co, cheap, expensive, store, commit, counts = runtime(
            configuration=lambda turn: TurnConfiguration(
                state(attachment_prompt=attachment), {'policy': RouterPolicy(mode='CHEAP_ONLY')}, fallback))
        retrieval.rows = []
        await service.accept_turn(REQ, 'owner')
        await service.drain_background()
        assert not repo.failures and cheap.calls
        messages = cheap.calls[0][0]
        assert any(m.role == 'user' and attachment in m.content for m in messages)
        assert all(attachment not in m.content for m in messages if m.role == 'system')
    asyncio.run(scenario())


@pytest.mark.parametrize('field', ['images', 'attachments'])
def test_ordinary_message_api_does_not_claim_attachment_transport(field):
    with pytest.raises(ValidationError):
        MessageCreate(content='请结合这张图，并依据课件说明其原理。', request_id='m31',
                      knowledge_base_scope=['kb'], **{field: ['not-a-supported-payload']})
