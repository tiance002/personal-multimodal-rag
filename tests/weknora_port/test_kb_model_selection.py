"""Normal configuration counterexample: first KB model differs from saved Cheap."""
import asyncio
import pytest
from types import SimpleNamespace
from contextlib import asynccontextmanager
from backend.weknora_port.runtime import ApplicationRuntime,ConversationSettings
from backend.weknora_port.model_registry import SourceCatalogue
from backend.weknora_port.storage.configuration import ConfiguredRole
from backend.weknora_port.generation import RouterPolicy
from backend.weknora_port.models.types import CallContext,TrustedScope
from backend.weknora_port.service import OrdinaryTurnRequest


@pytest.mark.parametrize("bridge_mode",["off","cheap_bridge_v1"])
def test_configuration_uses_first_requested_kb_not_legacy_session_cheap(bridge_mode):
    async def scenario():
        roles={'cheap':ConfiguredRole('cheap','siliconflow','XingChenAGI/Xing4.0-29B','http://fixture.invalid/v1','synthetic'), 'expensive':ConfiguredRole('expensive','deepseek','deepseek-flash','http://fixture.invalid/v1','synthetic')}
        app=object.__new__(ApplicationRuntime)
        class Leases:
            @asynccontextmanager
            async def lease(self):yield SimpleNamespace()
        class Models:
            async def resolve(self,id):return roles[id]
            async def _load(self,id):return {'id':id,'type':'KnowledgeQA','source':roles[id].provider},{},roles[id]
        app.leases=Leases();app.principal=SimpleNamespace(tenant_id=1,subject_id='owner');app.models=Models();app.catalogue=SourceCatalogue()
        app.graph_enabled=False
        app.conversation=ConversationSettings(bridge_rescue_mode=bridge_mode);app.policy=RouterPolicy(mode='OFF');app._runs={}
        app.model_factory=lambda config,key,gate:SimpleNamespace(config=config,transport=SimpleNamespace(gate=gate))
        async def session(*args):return {'summary_model_id':'cheap','max_rounds':5,'enable_rewrite':True,'embedding_top_k':30,'vector_threshold':.2,'keyword_threshold':.3,'rerank_top_k':30,'rerank_threshold':.3}
        app.turns=SimpleNamespace(_session=session)
        async def snapshot(*args):return SimpleNamespace(model_ids=(('cheap','cheap'),('expensive','expensive')),model_id=lambda name:name)
        app._snapshot=snapshot
        async def rows(sql,args):
            if 'knowledge_bases' in sql:return [{'id':id,'summary_model_id':'expensive' if id=='second' else 'cheap'} for id in args[-1]]
            return [{'id':'cheap'},{'id':'expensive'}]
        app._rows=rows
        request=OrdinaryTurnRequest('session','q0','run',knowledge_base_scope=('second','first'))
        turn=SimpleNamespace(session_id='session',run_id='run',request=request,context=CallContext('run',TrustedScope('owner',('first','second'))))
        config=await app._configuration(turn)
        assert config.pipeline_state.chat_model_id=='expensive'
        assert config.pipeline_state.bridge_cheap_model_id==('cheap' if bridge_mode!='off' else '')
        assert config.pipeline_state.bridge_rescue_mode==bridge_mode
    asyncio.run(scenario())


def test_fixed_go_normal_model_selection_cases():
    import json
    from pathlib import Path
    from backend.weknora_port.model_selection import resolve_chat_model_id
    from backend.weknora_port.storage.configuration import ModelConfigurationError
    fixture=json.loads((Path(__file__).parent/'fixtures/affinity_kb_go_oracle.json').read_text())
    assert fixture['exit_code']==0
    for case,expected in zip(fixture['cases'],fixture['rows']):
        if case['Kind']!='model':continue
        models={row['ID']:{'id':row['ID'],'type':row['Type'],'source':row['Source']} for row in case['Models']}
        bases={row['ID']:{'id':row['ID'],'summary_model_id':row['SummaryModelID']} for row in case['Bases']}
        async def get_model(id):
            if id not in models:raise ModelConfigurationError('MODEL_NOT_FOUND')
            return models[id]
        async def get_kb(id):return bases.get(id)
        async def list_models():return list(models.values())
        async def run():return await resolve_chat_model_id(case['Request'],case['Profile'],tuple(case['KBIDs']),get_kb,get_model,list_models)
        if expected['error']:
            import pytest
            from backend.weknora_port.models.types import ProviderError
            with pytest.raises(ProviderError):asyncio.run(run())
        else:assert asyncio.run(run())==expected['id']


def test_canonical_scope_retains_explicit_order_in_new_request_snapshot():
    from backend.weknora_port.storage.turns import PostgresTurnRepository,ScopeSnapshot
    repo=object.__new__(PostgresTurnRepository)
    request=OrdinaryTurnRequest('s','q','r',knowledge_base_scope=('b','a'))
    assert repo._request_configuration(request)=={'knowledge_base_order':['b','a']}
    snapshot=ScopeSnapshot('q',('a','b'),())
    assert repo._payload(request,snapshot)!=repo._payload(request,snapshot,include_order=False)


def test_sorted_multi_kb_new_request_is_not_unversioned_legacy():
    from dataclasses import replace
    from backend.weknora_port.storage.turns import PostgresTurnRepository,ScopeSnapshot
    repo=object.__new__(PostgresTurnRepository)
    request=OrdinaryTurnRequest('s','q','r',knowledge_base_scope=('a','b'))
    snapshot=ScopeSnapshot('q',('a','b'),())
    assert repo._request_configuration(request)=={'knowledge_base_order':['a','b']}
    assert repo._payload(request,snapshot)!=repo._payload(replace(request,knowledge_base_scope=('b','a')),snapshot)
    assert repo._payload(request,snapshot,include_order=False)==repo._payload(replace(request,knowledge_base_scope=('b','a')),snapshot,include_order=False)