"""Frozen source configuration rules; real entry checks are separate."""
import pytest
from backend.weknora_port.runtime import ConversationSettings
from backend.weknora_port.http_api import MessageCreate


def test_startup_mapping_does_not_drop_locale_or_resolved_prompt_fields():
    cfg=ConversationSettings.from_mapping({'language_locale':'ko-KR','conversation':{'rewrite_prompt_system':'SYSTEM {{language}}','rewrite_prompt_user':'USER {{query}}','intent_system_prompts':{'greeting':'GLOBAL {{language}}'},'query_understand_model_id':'dedicated','summary':{'temperature':0,'thinking':None,'prompt':'FINAL {{language}}','context_template':'CONTEXT {{query}}'}}},prompt_templates={})
    assert cfg.language_locale=='ko-KR'
    assert cfg.rewrite_prompt_system=='SYSTEM {{language}}' and cfg.rewrite_prompt_user=='USER {{query}}'
    assert cfg.intent_system_prompts['greeting']=='GLOBAL {{language}}'
    assert cfg.query_understand_model_id=='dedicated'
    assert cfg.summary['temperature']==0 and cfg.summary['thinking'] is None


def test_request_source_overrides_have_an_explicit_typed_contract():
    row=MessageCreate(content='q',request_id='x',knowledge_base_scope=['kb'],summary_model_id='baseline',reasoning_effort='off')
    assert row.summary_model_id=='baseline' and row.reasoning_effort=='off'


@pytest.mark.parametrize('startup,header,name',[
    ('ko-KR','en-US,en;q=0.8','English'),('ko-KR','','Korean'),('','en-US,en;q=0.8','English'),
    ('',' zh-TW ;q=0.9,en','Chinese (Traditional)'),('','','Chinese (Simplified)'),('','xx-Test','xx-Test')])
def test_source_locale_priority_and_names(startup,header,name):
    from backend.weknora_port.conversation_configuration import resolve_language
    assert resolve_language(startup,header)==name


def test_saved_null_zero_and_false_resolve_per_field_without_mutation():
    from backend.weknora_port.conversation_configuration import resolve_saved_conversation,resolved_chat_options
    source=ConversationSettings(summary={'temperature':.3,'max_completion_tokens':1024,'thinking':True})
    session={'max_rounds':None,'enable_rewrite':None,'vector_threshold':0,'summary_parameters':{'temperature':0,'max_completion_tokens':0,'thinking':None},'context_config':{'conversation':{'embedding_top_k':0,'rerank_threshold':0,'enable_rewrite':False,'multi_turn_enabled':False,'enable_query_expansion':False}}}
    out=resolve_saved_conversation(source,session)
    assert out['embedding_top_k']==30 and out['vector_threshold']==.2 and out['rerank_threshold']==0
    assert out['enable_rewrite'] is False and out['enable_query_expansion'] is False and out['max_rounds']==0
    options=resolved_chat_options(out['summary'],'off')
    assert options.temperature==0 and options.max_completion_tokens==1024 and options.thinking is False and options.reasoning_effort=='off'
    assert source.summary['thinking'] is True and session['summary_parameters']['max_completion_tokens']==0


@pytest.mark.parametrize('override,effort,thinking',[('none','off',False),('on','auto',True),('high','high',True),('HIGH','low',True),('','low',True)])
def test_request_reasoning_source_validation_preserves_defaults(override,effort,thinking):
    from backend.weknora_port.conversation_configuration import resolved_chat_options
    result=resolved_chat_options({'reasoning_effort':'low','thinking':True},override)
    assert result.reasoning_effort==effort and result.thinking is thinking


def test_dedicated_model_failure_falls_back_once_and_same_id_is_not_retried():
    import asyncio
    from types import SimpleNamespace
    from backend.weknora_port.retrieval.pipeline import PipelineState
    from backend.weknora_port.retrieval.query import understand_query
    from tests.weknora_port.test_pipeline import CTX,Model
    class Registry:
        def __init__(self):self.lookups=[];self.model=Model(['{"rewrite_query":"q0","intent":"greeting"}'])
        async def chat(self,id,context):
            self.lookups.append(id)
            if id=='missing':raise RuntimeError('deleted model')
            return self.model
    registry=Registry();state=PipelineState(query='q0',chat_model_id='baseline',query_understand_model_id='missing')
    asyncio.run(understand_query(state,CTX,registry));assert registry.lookups==['missing','baseline'] and state.intent=='greeting'
    registry=Registry();state=PipelineState(query='q0',chat_model_id='missing',query_understand_model_id='missing')
    asyncio.run(understand_query(state,CTX,registry));assert registry.lookups==['missing'] and not state.intent


def test_empty_new_options_preserve_historical_payload_hash_and_changes_conflict():
    from backend.weknora_port.service import OrdinaryTurnRequest
    from backend.weknora_port.storage.turns import PostgresTurnRepository,ScopeSnapshot,_json
    import hashlib
    request=OrdinaryTurnRequest('s','q','r',knowledge_base_scope=('kb',))
    snapshot=ScopeSnapshot('q',('kb',),())
    repo=object.__new__(PostgresTurnRepository)
    original=hashlib.sha256(_json({'version':'ordinary-turn/v1','session':'s','mode':'quick','q0':'q','knowledge_bases':('kb',),'documents':()}).encode()).hexdigest()
    assert repo._payload(request,snapshot)==original
    from dataclasses import replace
    assert repo._payload(replace(request,reasoning_effort='off'),snapshot)!=original


def test_exact_go_configuration_function_rows():
    import json,asyncio
    from pathlib import Path
    from backend.weknora_port.conversation_configuration import resolve_startup_templates,resolved_chat_options,resolve_language
    from backend.weknora_port.retrieval.pipeline import PipelineState
    from backend.weknora_port.retrieval.query import understand_query
    from tests.weknora_port.test_pipeline import CTX,Model
    fixture=json.loads((Path(__file__).parent/'fixtures/configuration_go_oracle.json').read_text())
    assert fixture['exit_code']==0 and len(fixture['rows'])==20
    templates={'system_prompt':[{'id':'same','content':'SYSTEM FIRST'},{'id':'sys','content':'FINAL {{language}}'}],
        'rewrite':[{'id':'same','content':'SHOULD NOT WIN'},{'id':'rw','content':'REWRITE {{language}} {{conversation}}','user':'USER {{query}}'}],
        'context_template':[{'id':'ctx','content':'CONTEXT {{query}}'}],
        'intent_prompts':[{'id':'greeting','content':'GLOBAL {{language}}'},{'id':'','content':'skip'},{'id':'empty','content':''}]}
    for row in fixture['rows']:
        kind,value,expected=row['kind'],row['input'],row['output']
        if kind=='startup':
            result=resolve_startup_templates({'rewrite_prompt_id':value,'rewrite_prompt_system':'SAVED','rewrite_prompt_user':'USER SAVED','summary':{'prompt_id':'sys','context_template_id':'ctx'}},templates)
            actual={'system':result['rewrite_prompt_system'],'user':result['rewrite_prompt_user'],'prompt':result['summary']['prompt'],'context':result['summary']['context_template'],'intents':result['intent_system_prompts']}
        elif kind=='reasoning':
            result=resolved_chat_options({'thinking':True,'reasoning_effort':'low'},value);actual={'thinking':result.thinking,'effort':result.reasoning_effort}
        elif kind=='locale':actual=resolve_language('',value)
        else:
            class Registry:
                def __init__(self):self.calls=[];self.model=Model(['{"rewrite_query":"q0","intent":"greeting"}'])
                async def chat(self,id,context):
                    self.calls.append(id)
                    if id=='missing':raise RuntimeError('deleted')
                    return self.model
            registry=Registry();state=PipelineState(query='q0',chat_model_id='baseline',query_understand_model_id=value if kind=='model' else '')
            if kind=='intent':state.intent_system_prompts={'greeting':value};state.global_intent_system_prompts={'greeting':'GLOBAL'}
            elif kind=='prompts':
                from types import SimpleNamespace
                state.language='Korean';state.rewrite_system_prompt='SESSION {{language}} {{conversation}}';state.rewrite_user_prompt='SESSION {{query}}';state.memory_background=' BACKGROUND';state.history=[SimpleNamespace(query='old',answer='answer')]
            asyncio.run(understand_query(state,CTX,registry))
            if kind=='intent':actual=state.system_prompt_override
            elif kind=='model':actual={'id':registry.calls[-1],'images':False,'calls':registry.calls}
            else:actual={'system':registry.model.calls[0][0][0].content,'user':registry.model.calls[0][0][1].content}
        assert actual==expected,(kind,value,actual,expected)


def test_unresolved_template_id_does_not_silently_select_default_prompt():
    result=ConversationSettings.from_mapping({'conversation':{'rewrite_prompt_id':'missing','summary':{'prompt_id':'missing','context_template_id':'missing'}}},prompt_templates={})
    assert result.rewrite_prompt_system==result.rewrite_prompt_user==''
    assert result.summary['prompt']==result.summary['context_template']==''


def test_bundled_fixed_templates_resolve_and_explicit_request_null_is_unset():
    settings=ConversationSettings.from_mapping({'conversation':{'rewrite_prompt_id':'default_rewrite','summary':{'prompt_id':'default_kb','context_template_id':'default_context'}}})
    assert settings.rewrite_prompt_system and settings.rewrite_prompt_user and settings.summary['prompt'] and settings.summary['context_template']
    assert settings.intent_system_prompts['greeting']
    request=MessageCreate(content='q',request_id='r',knowledge_base_scope=['kb'],summary_model_id=None,reasoning_effort=None)
    assert request.summary_model_id is None and request.reasoning_effort is None
