"""Real FastAPI/RuntimeService/pipeline/generation; offline providers and storage."""
import asyncio
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace
import uuid

import httpx
import pytest

from scripts import s8_6_1_api_acceptance as profile
from scripts.s8_ecs_runtime import PrivateApiGuard
from backend.weknora_port.conversation.types import MemoryContext
from backend.weknora_port.generation import RoleBinding
from backend.weknora_port.generation import RouterPolicy
from backend.weknora_port.http_api import create_app
from backend.weknora_port.models.provider import ProviderModel
from backend.weknora_port.models.types import CallContext, TrustedScope, RerankResult, TokenUsage
from backend.weknora_port.retrieval.pipeline import RetrievalPipeline
from backend.weknora_port.retrieval.types import SearchResult
from backend.weknora_port.runtime import ConversationSettings
from backend.weknora_port.service import AcceptedTurn, RuntimeService, MemoryBundle
from test_generation import setup, event
from test_s86_qu_configuration import assembled_runtime
from test_s861_preflight import INVENTORY, ORIGINALS
from test_s862r_preflight import real_config, bounded_authorization, write_json


@pytest.fixture(autouse=True)
def no_real_provider(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError('offline ASGI must not invoke a real Provider')
    for method in ('chat', 'chat_stream', 'embed', 'rerank', 'vision'):
        monkeypatch.setattr(ProviderModel, method, forbidden)


@pytest.fixture(scope='module')
def current_budget():
    return asyncio.run(profile.dry_run_inventory(INVENTORY, ORIGINALS, bounded_query_profile=True))


def test_r3_profile_is_current_source_bound_and_only_initial_compare_is_proposed(current_budget):
    from pathlib import Path
    from backend.weknora_port.retrieval import query
    manifest = profile.profile_manifest(bounded_query_profile=True)
    assert manifest['goal'] == 'S8.6.2-R3'
    assert manifest['profile'] == 's8.6.2-r3-formal-api-prompt-b/v3'
    assert manifest['query_implementation_sha256_LF'] == hashlib.sha256(
        Path(query.__file__).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    assert manifest['prompt_b_suffix_sha256'] == '4501caef3b4b0d5b0de09e00e51d1ca9d50332637637e52ffc45ee90e2dc726b'
    assert set(manifest['question_inputs']) == {'compare'} and manifest['qu_only_inputs'] == {}
    assert set(manifest['candidate_inputs_not_authorized']) == {'parallel', 'dependent'}
    assert current_budget['schema'] == 's862r3-reservation-dryrun/v4'
    assert current_budget['requested_full_calls'] == current_budget['recommended_max_calls'] == 9
    assert set(current_budget['recommended_batches_not_authorized']) == {'compare'}


@pytest.mark.parametrize('case,kind', [('parallel','http'), ('dependent','http'), ('two-object','qu_only')])
def test_r3_initial_grant_cannot_admit_later_or_qu_only_cases(tmp_path, monkeypatch, current_budget, case, kind):
    owner, ledger, environment, auth_path, ledger_path = bounded_authorization(tmp_path, monkeypatch, current_budget)
    batch = owner['batches']['candidate-only']
    batch['kind'] = kind; batch['turns'][0]['case'] = case
    batch['turns'][0]['question'] = (profile.QU_ONLY_QUESTIONS if kind=='qu_only' else profile.BOUNDED_QUESTIONS)[case]
    write_json(auth_path, owner)
    before = ledger_path.read_bytes()
    with pytest.raises(ValueError, match='S862R3_INITIAL_COMPARE_ONLY'):
        profile._approved(environment)
    assert ledger_path.read_bytes() == before


@pytest.mark.parametrize('recovered_terminal_period', [False, True])
@pytest.mark.parametrize('answer_configuration', ['current-unknown-cheap', 'explicit-supported-model'])
def test_actual_asgi_session_message_client_run_admission_qu_wire_two_children_one_answer(
        monkeypatch, tmp_path, current_budget, recovered_terminal_period, answer_configuration):
    """Storage/provider mocks are explicit; public HTTP and orchestration run."""
    q0 = profile.BOUNDED_QUESTIONS['compare']
    configs = {role: real_config(role) for role in ('cheap','expensive','embedding','rerank')}
    ids = {role: cfg.model_id for role, cfg in configs.items()}
    runtime, stored, sql_trace = assembled_runtime(monkeypatch,
        ConversationSettings(query_understand_model_id=ids['cheap']))
    supported_answer = answer_configuration == 'explicit-supported-model'
    if supported_answer:
        # Explicit normal model selection with routing OFF is a separate
        # supported path. It does not certify the frozen hosted Cheap model.
        runtime.policy = RouterPolicy(mode='OFF')
    qu_calls, embedding_query_inputs, reranks, search_calls, active, peak = [], [], [], [], [0], [0]
    sources, source_reads = {}, []
    class QueryProvider:
        config = configs['cheap']
        def build_request_body(self, messages, options):
            return ProviderModel(self.config, None).build_request_body(messages, options)
        async def chat(self, messages, options, context):
            qu_calls.append((messages, options, context))
            body = {'rewrite_query': q0, 'intent': 'kb_search', 'image_description': '', 'query_type':'SIMPLE_COMPARE'}
            if recovered_terminal_period:
                body['rewrite_query'] = q0[:-1]
            return SimpleNamespace(content=json.dumps(body), finish_reason='stop')
    class RankProvider:
        config = configs['rerank']
        async def rerank(self, query, documents, context):
            reranks.append((query, context.request_id))
            return SimpleNamespace(results=[RerankResult(index,.85) for index in range(len(documents))])
    class Registry:
        def __init__(self,*args,**kwargs): pass
        async def chat(self,model_id,*args):
            return SimpleNamespace(config=configs['expensive']) if model_id==ids['expensive'] else QueryProvider()
        async def rerank(self,*args): return RankProvider()
    import backend.weknora_port.runtime as runtime_module
    monkeypatch.setattr(runtime_module,'NeutralModelRegistry', Registry)
    async def snapshot(*args):
        return SimpleNamespace(model_id=lambda role: ids[role], model_ids=tuple(ids.items()))
    async def rows(*args): return [{'id':profile.KB_IDS[0],
        'summary_model_id':ids['expensive'] if supported_answer else ids['cheap']}]
    async def load(model_id): return ({'id':model_id,'type':'KnowledgeQA','source':'remote'},None,None)
    runtime._snapshot, runtime._rows, runtime.models._load = snapshot, rows, load
    class SourceRepository:
        async def hybrid_search(self, targets, params, context, query_embedding=None):
            active[0]+=1; peak[0]=max(peak[0],active[0])
            try:
                await asyncio.sleep(0)
                assert params.serial is True
                # The production PG repository embeds this same query input.
                # This storage mock records that input only; it does not run
                # an Embedding Provider or its wire renderer.
                embedding_query_inputs.append((params.query_text, context.request_id))
                search_calls.append((params.query_text, context.request_id))
                which = 0 if 'Leyden jar' in params.query_text else 1
                text = ('A Leyden jar stores electrical charge in conductors separated by glass.' if which==0 else
                    'Franklin bells indicate electrostatic charge by a moving clapper.')
                row = SearchResult('source-'+str(which),text,.9,knowledge_id=profile.DOCUMENT_IDS[which],
                    knowledge_base_id=profile.KB_IDS[0],end_at=len(text),knowledge_title='Synthetic original '+str(which),
                    metadata={'source_version':'v1'},chunk_metadata={'source_version':'v1'})
                sources[row.id]=row
                return [deepcopy(row)]
            finally: active[0]-=1
        async def get_chunks(self, identifiers, context, *, current=False):
            source_reads.append((tuple(identifiers),current))
            assert all(sources[key].knowledge_base_id in context.scope.knowledge_base_ids
                and sources[key].knowledge_id in context.scope.document_ids for key in identifiers)
            return {key:deepcopy(sources[key]) for key in identifiers}
        async def get_chunk_children(self,*args): return []
        async def get_faq_answers(self,*args): return {}
        async def get_source_unit(self, anchor, context):
            raise AssertionError('Removed Source Unit window must not run')
    source_repository = SourceRepository()
    answer = ('A Leyden jar stores charge using separated conductors and glass <ref id="c1"/>. '
        'Franklin bells indicate electrostatic charge using a moving clapper <ref id="c2"/>.')
    response = event(answer, done=True, finish='stop', final=True, usage=TokenUsage(10,10,20))
    coordinator, cheap, expensive, store, committed, _ = setup([response],[response])
    cheap.config, expensive.config = configs['cheap'], configs['expensive']
    coordinator.bindings = {role:RoleBinding(role, model.config.provider, model.config.model,
        model.config.endpoint_url or model.config.base_url, model) for role,model in
        (('cheap',cheap),('expensive',expensive),('baseline',expensive))}
    async def get_events(session,message,offset): return store.events[offset:],len(store.events)
    store.get_events = get_events
    accepted_rows, failures = [], []
    class TurnStorage:
        async def accept(self, request, principal):
            assert principal == runtime.principal and request.session_id == stored['id']
            assert stored['context_config']['conversation'] == {'source_evidence_mode':'bounded_source_v1',
                'retrieval_strategy_mode':'query_structure_v1'}
            if accepted_rows:
                assert accepted_rows[0].client_request_id == request.request_id
                return replace(accepted_rows[0],is_new=False,replay_only=True)
            run_id=str(uuid.uuid4())
            turn=AcceptedTurn(run_id,request.session_id,'user-msg','assistant-msg',CallContext(run_id,
                TrustedScope(runtime.principal.subject_id,profile.KB_IDS,profile.DOCUMENT_IDS),
                session_id=request.session_id,cancelled=asyncio.Event()),
                MemoryContext(332,runtime.principal.subject_id),replace(request,request_id=run_id),
                is_new=True,replay_only=False,client_request_id=request.request_id)
            accepted_rows.append(turn); return turn
        async def validate_run(self,session,message,principal):
            assert principal == runtime.principal
            assert (session,message)==(accepted_rows[0].session_id,accepted_rows[0].assistant_message_id)
            return replace(accepted_rows[0],is_new=False,replay_only=True)
        async def commit_failure(self, turn, code): failures.append(code); return True
    async def history(*args,**kwargs): return ()
    async def memory(*args): return MemoryBundle()
    async def pipeline_factory(turn,progress):
        return RetrievalPipeline(source_repository,runtime._runs[turn.run_id]['registry'],on_progress=progress)
    async def generation_factory(turn,progress):
        coordinator.on_progress=progress;return coordinator
    service=RuntimeService(TurnStorage(),store,SimpleNamespace(load=history),SimpleNamespace(load=memory),
        configuration=runtime._configuration,pipeline_factory=pipeline_factory,generation_factory=generation_factory,
        poll_seconds=.001)
    chain_errors=[]
    publish_error=service.publish_error
    async def record_chain_error(turn,error):
        chain_errors.append((type(error).__name__,str(error)))
        await publish_error(turn,error)
    service.publish_error=record_chain_error
    runtime.ordinary=service
    client_id='s861-r3-offline-'+('period' if recovered_terminal_period else 'json')
    approved={'qu_model_id':ids['cheap'],'model_ids':ids,'turns':[{'request_id':client_id,
        'question':q0,'case':'compare','mode':'query_structure_v1'}],
        'qu_request_sha256s':{client_id:current_budget['records']['compare:query_structure_v1:QU']['body_sha256']}}
    profile.install_profile(runtime,tmp_path,approved_batch=approved,
        expires_at=datetime.now(timezone.utc)+timedelta(hours=1),bounded_query_profile=True)
    app=PrivateApiGuard(create_app(runtime,resolve_principal=lambda request:runtime.principal,
        manage_background_workers=False),'a'*40)
    async def exercise():
        headers={'Authorization':'Bearer '+'a'*40,'Origin':'http://test','Accept-Language':'en-US'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers=headers) as client:
            created=await client.post('/api/v1/sessions',json={'knowledge_base_scope':list(profile.KB_IDS),
                'document_scope':list(profile.DOCUMENT_IDS),'source_evidence_mode':'bounded_source_v1',
                'retrieval_strategy_mode':'query_structure_v1'})
            assert created.status_code==200
            sid=created.json()['data']['id']
            result=await client.post('/api/v1/sessions/'+sid+'/messages',json={'request_id':client_id,'content':q0,
                'knowledge_base_scope':list(profile.KB_IDS),'document_scope':list(profile.DOCUMENT_IDS),
                'reasoning_effort':'off'})
            await service.drain_background()
            assert result.status_code==200
            replay=await client.get('/api/v1/sessions/continue-stream/'+sid,params={'message_id':'assistant-msg'})
            return profile.parse_sse(result.text),profile.parse_sse(replay.text)
    frames,replayed=asyncio.run(exercise())
    if not supported_answer:
        # Keep the actual default failure visible; no fabricated capacity,
        # weakened guard or automatic Expensive substitution is permitted.
        assert failures == ['MODEL_GENERATION_CAPACITY_UNKNOWN']
        assert chain_errors == [('ProviderError','MODEL_GENERATION_CAPACITY_UNKNOWN')]
        assert not service.background_errors and not cheap.calls and not expensive.calls and not committed.calls
        assert len(qu_calls)==1 and len(search_calls)==2 and peak[0]==1
        return
    assert not failures and not service.background_errors, (failures,chain_errors,service.background_errors)
    turn=accepted_rows[0]
    assert isinstance(turn,AcceptedTurn) and turn.client_request_id==client_id
    assert turn.request.request_id==turn.context.request_id==turn.run_id!=client_id
    assert frames[-1]['response_type']=='complete' and frames[-1]['data'].get('finish_reason')=='stop'
    assert frames[-1]['data'].get('truncated') is False
    assert replayed[-1]['data']==frames[-1]['data']
    assert len(qu_calls)==1 and qu_calls[0][2].request_id==turn.run_id+'/query_rewrite'
    assert qu_calls[0][1].thinking is False and qu_calls[0][1].max_completion_tokens==256
    expected=['What is known about Leyden jar regarding operating principles and uses?',
              'What is known about Franklin bells regarding operating principles and uses?']
    assert [query for query,_ in search_calls]==[query for query,_ in embedding_query_inputs]==[query for query,_ in reranks]==expected
    assert peak[0]==1 and len(cheap.calls)+len(expensive.calls)==len(committed.calls)==1
    assert not cheap.calls and len(expensive.calls)==1
    assert runtime._runs[turn.run_id]['chat_id']==ids['expensive']
    messages=(cheap.calls or expensive.calls)[0][0]
    assert all(row.content in messages[-1].content for row in sources.values())
    assert any(current for _,current in source_reads), 'Final sources require current-version readback'
    assert profile.PROMPT_B_SUFFIX in messages[0].content
    actual=runtime._s861_profile_states[turn.run_id]
    assert actual.strategy_plan.strategy=='RULE_COMPARE' and actual.query==actual.rewrite_query==q0
    traces=[json.loads(path.read_text(encoding='utf8')) for path in tmp_path.glob('*.profile.json')]
    wire=next(row['fields'] for row in traces if row['stage']=='query_understanding_wire')
    assert wire['body_sha256']==approved['qu_request_sha256s'][client_id]
