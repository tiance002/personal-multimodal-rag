"""Synthetic full run_case composition: no network, private data or real grants."""
import asyncio
from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace

import pytest

from scripts import s8_6_5_generation_compare as compare
from backend.weknora_port import execution
from backend.weknora_port.models.types import StreamResponse


def exercise(tmp_path, monkeypatch, finish, provider_done, *, final_type='answer'):
    package=tmp_path/'synthetic-package';package.mkdir()
    case='SYNTHETIC-FINISH';row=dict(case_id=case,kb_scope=['SYNTHETIC-KB'],document_scope=[])
    body={'fixture':'SYNTHETIC-NO-SEND'}
    compare.write(package/'manifest.json',{'cases':[row]})
    compare.write(package/'wire_manifest.json',{'wires':[dict(case_id=case,arm=a,body_sha256=compare.digest(body)) for a in 'ABC']})
    grant_path=tmp_path/'synthetic-grant.json';output=tmp_path/'output'
    config=SimpleNamespace(model_id='SYNTHETIC-MODEL',provider='SYNTHETIC',model='SYNTHETIC')
    grant=dict(tenant_id=1,subject_id='SYNTHETIC-OWNER',request_prefix='SYNTHETIC-PREFIX',status='OPEN',max_calls=3,max_tokens=100000,batch_deadline_unix=compare.time.time()+60,resolved_role_bindings={r:vars(config) for r in ['cheap','expensive']})
    compare.write(grant_path,grant)
    ledger=dict(status='OPEN',entries=[],max_calls=3,tenant_id=1,subject_id=grant['subject_id'],allowed_kbs=row['kb_scope'],allowed_request_prefixes=[compare.case_request_prefix(grant,case)])
    class SyntheticExecution:
        accounting_mode=compare.ACCOUNTING
        @contextmanager
        def _ledger(self):yield ledger
    calls=[]
    class SyntheticModel:
        def __init__(self):self.config=config
        def build_request_body(self,*a,**kw):return body
        async def chat_stream(self,messages,options,context):
            arm=context.request_id.rsplit('/',1)[1];calls.append(arm)
            ledger['entries'].append(dict(request_id=context.request_id,status='SETTLED',outcome='RESPONSE_RECEIVED',usage=dict(status='REPORTED',prompt_tokens=3,completion_tokens=2,total_tokens=5)))
            # A completes normally. B carries the exact protocol shape under test.
            reason,done=('stop',True) if arm!='B' else (finish,provider_done)
            data={} if done is None else {'provider_answer_done':done}
            yield StreamResponse('answer','SYNTHETIC answer',False)
            yield StreamResponse(final_type if arm=='B' else 'answer','',True,finish_reason=reason,data=data,is_final=True)
    model=SyntheticModel()
    class Registry:
        async def chat(self,*a):return model
        async def close(self):pass
    @asynccontextmanager
    async def lease():yield object()
    async def snapshot(guard):return SimpleNamespace(model_id=lambda role:config.model_id)
    app=SimpleNamespace(execution=SyntheticExecution(),principal=SimpleNamespace(tenant_id=1,subject_id=grant['subject_id']),registry_for_context=Registry,leases=SimpleNamespace(lease=lease),_snapshot=snapshot)
    monkeypatch.setattr(execution,'LocalExecution',SyntheticExecution)
    monkeypatch.setattr(compare,'validate_owner_grant',lambda *a:None)
    monkeypatch.setattr(compare,'validate_source_proof',lambda *a:{'fixture':'SYNTHETIC'})
    monkeypatch.setattr(compare,'read_manifest',lambda *a:{'cases':[row]})
    monkeypatch.setattr(compare,'load_case',lambda *a:{arm:(None,[]) for arm in 'ABC'})
    return lambda:asyncio.run(compare.run_case(package,case,app=app,owner_grant=grant_path,output=output)),calls,ledger,grant_path,output


@pytest.mark.parametrize('finish',['stop','end_turn','completed'])
def test_valid_provider_completion_allows_serial_abc(tmp_path,monkeypatch,finish):
    run,calls,ledger,grant,output=exercise(tmp_path,monkeypatch,finish,True)
    result=run()
    assert calls==['A','B','C'] and result['status']=='COMPLETE'
    assert result['accounting']['reported_total_tokens']==15 and ledger['status']=='CLOSED'


@pytest.mark.parametrize('finish,done,final_type',[
    ('',False,'answer'),  # Actual-like EOF/[DONE] usage bookkeeping.
    ('',True,'answer'),
    ('stop',False,'answer'),
    ('stop',None,'answer'),
    ('length',True,'answer'),
    ('tool_calls',True,'answer'),
    ('stop',True,'thinking'),
])
def test_invalid_completion_stops_before_c_keeps_settled_usage(tmp_path,monkeypatch,finish,done,final_type):
    run,calls,ledger,grant,output=exercise(tmp_path,monkeypatch,finish,done,final_type=final_type)
    with pytest.raises(RuntimeError,match='S865_UNKNOWN_INCOMPLETE_OR_TRUNCATED_STOP_NO_RETRY'):
        run()
    state=compare.read(output/'state.json');parent=compare.read(grant)
    assert calls==['A','B'] and ledger['status']=='STOP'
    assert state['status']=='STOP_NO_RETRY' and parent['status']=='STOP'
    assert state['accounting']['reported_total_tokens']==parent['reported_total_tokens']==10
    assert state['accounting']['unknown_entries']==0 and len(state['ledger_entries'])==2
    assert state['arms']['B']['ledger_status']=='SETTLED'
    assert compare.read(output/'B.events.json')[-1]['is_final'] is True
    assert not (output/'C.events.json').exists()
