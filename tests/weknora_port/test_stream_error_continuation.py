"""Same-channel errors and decoder teardown; closed source-derived fixtures."""
from dataclasses import replace
import pytest
from tests.weknora_port.test_generation import setup, event, request, CTX, E, run
from backend.weknora_port.models.types import ProviderError


def test_generic_error_does_not_drop_subsequent_answer_or_reopen_thinking():
    co,c,e,store,commit,life=setup([
        event('first',kind='thinking',done=True),
        event('second',kind='thinking'),
        event('partial '),
        event('RAW PRIVATE PROVIDER BODY',kind='error',done=True,data={'error_code':'PROVIDER_RESPONSE_INVALID'}),
        event('recovered <ref id="c1"/>',done=True,finish='stop',final=True)])
    out=run(co.run(request('CHEAP_ONLY'),CTX))
    assert out.answer.startswith('partial recovered') and out.thinking=='firstsecond'
    assert len(out.references)==1 and len(commit.calls)==1 and not e.calls
    assert [(x.content,x.done) for x in store.events if x.type=='thinking']==[('first',False),('',True),('second',False),('',True)]
    errors=[x for x in store.events if x.type=='error']
    assert len(errors)==1 and errors[0].content=='PROVIDER_RESPONSE_INVALID'
    assert errors[0].data['stage']=='chat_completion_stream' and errors[0].data['non_terminal'] is True
    assert all('RAW PRIVATE' not in x.content for x in store.events)
    assert out.attempts[0].usage.status=='UNKNOWN'


def test_generic_error_eof_flushes_held_tail_without_success_or_upgrade():
    evidence=replace(E,content='![image](resource://abcdefghijklmnopqrstuv)')
    co,c,e,store,commit,life=setup([
        event('thought re',kind='thinking'), event('answer re'),
        event(kind='error',data={'error_code':'PROVIDER_RESPONSE_INVALID'})])
    with pytest.raises(ProviderError):run(co.run(request('CHEAP_ONLY',evidence=(evidence,)),CTX))
    assert ''.join(x.content for x in store.events if x.type=='thinking')=='thought re'
    assert ''.join(x.content for x in store.events if x.type=='answer')=='answer re'
    assert sum(x.type=='thinking' and x.done for x in store.events)==1
    assert not commit.calls and not e.calls and life.finished[0][0].status=='FAILED'
    assert not any(x.type=='complete' for x in store.events)


@pytest.mark.parametrize('code',['MODEL_BUDGET_DENIED','MODEL_EGRESS_DENIED','PROVIDER_MODEL_IDENTITY_MISMATCH','MODEL_USAGE_SETTLEMENT_FAILED','MODEL_CALL_TIMEOUT'])
def test_hard_boundaries_do_not_recover_or_upgrade(code):
    co,c,e,store,commit,life=setup([event('private draft'),event(kind='error',data={'error_code':code}),event('must not read',done=True,finish='stop')])
    with pytest.raises(ProviderError):run(co.run(request(),CTX))
    assert not e.calls and not commit.calls and not store.events


def test_buffered_cheap_recovered_error_only_publishes_selected_attempt():
    co,c,e,store,commit,life=setup([event(kind='error'),event('recovered',done=True,finish='stop')])
    out=run(co.run(request(),CTX))
    assert out.answer=='recovered' and not out.upgraded and not e.calls
    assert len([x for x in store.events if x.type=='error'])==1
    co,c,e,store,commit,life=setup([event(kind='error'),event('truncated',done=True,finish='length')])
    out=run(co.run(request(),CTX))
    assert out.upgraded and not any(x.type=='error' for x in store.events)


def test_exact_go_consumer_resource_traces():
    import json
    from pathlib import Path
    fixture=json.loads((Path(__file__).parent/'fixtures/stream_error_go_oracle.json').read_text())
    assert fixture['exit_code']==0 and len(fixture['cases'])==3
    evidence=replace(E,content='![image](resource://abcdefghijklmnopqrstuv)')
    for inputs,expected in zip(fixture['cases'],fixture['rows']):
        outputs=[event(row.get('Content',''),kind=row['ResponseType'],done=row.get('Done',False),finish=row.get('FinishReason',''),data={'error_code':'PROVIDER_RESPONSE_INVALID'} if row['ResponseType']=='error' else None) for row in inputs]
        co,c,e,store,commit,life=setup(outputs)
        if any(row.get('Done') and row['ResponseType']=='answer' for row in inputs):
            run(co.run(request('CHEAP_ONLY',evidence=(evidence,)),CTX))
        else:
            with pytest.raises(ProviderError):run(co.run(request('CHEAP_ONLY',evidence=(evidence,)),CTX))
        actual=[{'kind':x.type,'content':x.content,'done':x.done} for x in store.events if x.type in ('answer','thinking','error')]
        assert actual==expected
