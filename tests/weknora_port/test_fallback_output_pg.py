"""Real loopback HTTP + PG/Redis; provider failures injected only at mock wire."""
import json,uuid
from pathlib import Path
from dataclasses import replace
import httpx
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.test_memory_storage_pg import actual_pg_run
from tests.weknora_port.test_runtime_pg import stream_response
from tests.weknora_port.test_qa_followup_fallback_pg import http_client,kb,session,ask,frames,REDIS_ID


def test_fallback_wire_failures_preserve_attempt_unknown_and_single_send():
    current={'kind':'startup'}
    def response(request,body):
        if not body.get('stream'):return None
        kind=current['kind']
        if kind=='startup':return httpx.Response(503,json={'error':{'message':'synthetic unavailable'}})
        if kind in ('auth401','auth403'):return httpx.Response(int(kind[-3:]),json={'error':{'message':'synthetic denied'}})
        if kind=='timeout':raise httpx.ReadTimeout('synthetic',request=request)
        if kind=='empty':return httpx.Response(200,headers={'content-type':'text/event-stream'},content=b'')
        if kind in ('eof','thinking_eof'):
            row={'model':body['model'],'choices':[{'delta':{'content':'partial '},'finish_reason':None}]}
            if kind=='thinking_eof':row['choices'][0]['delta']['reasoning_content']='private thought'
            return httpx.Response(200,headers={'content-type':'text/event-stream'},content='data: '+json.dumps(row)+'\n\n')
        if kind=='identity':return stream_response('wrong-model','untrusted answer','stop')
        return stream_response(body['model'],'normal answer','stop')
    async def scenario():
        rows=[]
        async with isolated_mock_application(expected_redis_run_id=REDIS_ID,redis_db=15,response_handler=response) as (runtime,wire,root):
            runtime.conversation=replace(runtime.conversation,fallback_prompt='QA_FALLBACK {{query}}',fallback_response='FIXED_SOURCE_REPLY')
            async with http_client(runtime) as client:
                kbid=await kb(client,'QA output fallback')
                for kind in ('startup','empty','eof','thinking_eof','normal','auth401','auth403','timeout','identity'):
                    current['kind']=kind;sid=await session(client,kbid);start=len(wire)
                    reply,assistant,request=await ask(client,runtime,sid,kbid,'What is here?')
                    sends=[row for row in wire[start:] if row['body'].get('stream')]
                    assert len(sends)==1,(kind,sends)
                    attempts=await runtime._rows('SELECT role,status,error_code,cost_status,usage FROM generation_attempts WHERE run_id=%s ORDER BY ordinal',
                        (assistant['request_id'],))
                    assert len(attempts)==1
                    ledger=await runtime._rows("SELECT state,outcome,usage FROM send_calls WHERE tenant_id=%s AND subject_id=%s AND request_id LIKE %s ORDER BY created_at",
                        (runtime.principal.tenant_id,runtime.principal.subject_id,assistant['request_id']+'/answer/%'))
                    assert len(ledger)==1
                    event_frames=frames(reply)
                    if kind in ('startup','empty','eof','thinking_eof'):
                        expected=('partial ' if kind in ('eof','thinking_eof') else '')+'FIXED_SOURCE_REPLY'
                        assert assistant['is_completed'] and not assistant['turn_error_code'] and assistant['content']==expected,reply.text
                        assert ''.join(f['content'] for f in event_frames if f['response_type']=='answer')==expected
                        assert attempts[0]['status']=='FAILED' and ledger[0]['state']=='unknown'
                        assert not assistant['knowledge_references']
                    elif kind=='normal':
                        assert assistant['is_completed'] and assistant['content']=='normal answer' and not assistant['turn_error_code']
                        assert attempts[0]['status']=='COMPLETED' and ledger[0]['state']=='settled'
                    else:
                        assert assistant['turn_error_code'] and 'FIXED_SOURCE_REPLY' not in reply.text,reply.text
                        assert attempts[0]['status']=='FAILED' and ledger[0]['state']=='unknown'
                    assert not any(f['response_type']=='thinking' for f in event_frames)
                    assert sum(f['response_type']=='complete' for f in event_frames)==1
                    before=len(wire)
                    replay=await client.post(f'/api/v1/sessions/{sid}/messages',json=request)
                    assert replay.status_code==200 and len(wire)==before
                    rows.append({'kind':kind,'session':sid,'assistant':assistant,'attempts':attempts,'ledger':ledger,'events':event_frames,
                        'generation_sends':len(sends),'replay_sends':0,'real_provider_calls':0})
                output=Path('docs/weknora-port/evidence/QA_FALLBACK_OUTPUT_20261015/http.'+uuid.uuid4().hex+'.json')
                output.write_text(json.dumps(rows,indent=2,default=str)+'\n',encoding='utf-8')
    actual_pg_run(scenario())
