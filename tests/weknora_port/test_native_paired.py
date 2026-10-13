"""Offline native preparation + actual role-binding/coordinator mechanism, explicit doubles."""
import asyncio
from types import SimpleNamespace
from dataclasses import replace
import pytest
from test_service import runtime,REQ
from test_generation import setup,event,Lifecycle,Committer
from backend.weknora_port.runtime import ApplicationRuntime
from eval_center.weknora_paired import execute_native_pair,MODES
from backend.weknora_port.models.types import ProviderError


def test_native_roles_share_one_preparation_and_original_baseline_binding():
    async def scenario():
        service,repo,retrieval,co,cheap,expensive,store,committer,counts=runtime()
        class Registry:
            closed=0
            async def get(self,key,context):return cheap if key=='cheap-id' else expensive
            async def chat(self,key,context):return await self.get(key,context)
            async def close(self):self.closed+=1
        registry=Registry();runs={};original_config=service.configuration
        async def config(turn):
            runs[turn.run_id]={'registry':registry,'snapshot':SimpleNamespace(model_ids=(('cheap','cheap-id'),('expensive','expensive-id'))),'chat_id':'cheap-id'}
            return original_config(turn)
        service.configuration=config
        app=SimpleNamespace(ordinary=service,_runs=runs,principal=SimpleNamespace(tenant_id=1,subject_id='owner'),policy=co.policy if hasattr(co,'policy') else None,store=store,turns=repo,estimator=None)
        async def rows(*args):return []
        app._rows=rows
        async def generation(turn,progress,policy=None):
            result=await ApplicationRuntime._generation(app,turn,progress,policy=policy)
            result.lifecycle=Lifecycle();result.committer=Committer(store)
            return result
        app._generation=generation
        turns=tuple([await repo.accept(replace(REQ,request_id='paired-'+mode),'owner') for mode in MODES])
        result=await execute_native_pair(app,turns,execution_kind='ISOLATED_MOCK')
        assert counts['pipeline']==1 and len(retrieval.calls)==1
        assert [r['result'].selected_role for r in result.outcomes]==['baseline','cheap','expensive','expensive']
        assert [r['result'].upgraded for r in result.outcomes]==[False,False,False,True]
        assert len(cheap.calls)==3 and len(expensive.calls)==2
        messages=[row[0] for row in cheap.calls+expensive.calls]
        assert all(row==messages[0] for row in messages)
        assert all(r['result'].prompt_sha256==result.prompt_sha256 and r['result'].context_sha256==result.context_sha256 for r in result.outcomes)
        assert result.model_quality_status=='NOT_RUN_SIMULATED_MODELS'
        assert registry.closed==1 and not runs
    asyncio.run(scenario())


def test_pair_rejects_scope_or_query_change_before_preparation():
    async def scenario():
        service,repo,retrieval,*_=runtime()
        turns=tuple([await repo.accept(replace(REQ,request_id='paired-'+mode),'owner') for mode in MODES])
        altered=replace(turns[-1],request=replace(turns[-1].request,content='different question'))
        app=SimpleNamespace(ordinary=service)
        with pytest.raises(ProviderError,match='PAIRED_SERVER_SCOPE_OR_QUERY_MISMATCH'):
            await execute_native_pair(app,(*turns[:-1],altered),execution_kind='ISOLATED_MOCK')
        assert not retrieval.calls
    asyncio.run(scenario())
