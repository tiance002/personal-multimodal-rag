"""SIMULATED provider transport/proofs; real local framing where marked.

No provider requests, private credentials, or business database operations.
"""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.adapters.models import cloud
from backend.app.ports.output_capability import (OutputCapabilityEvidence, PROTOCOLS,
    STREAM_PROTOCOLS, SYNTHETIC_OUTPUT_PROBE, load_output_capabilities)
from backend.app.ports.providers import ProviderRequestNotSent, TruncatedAnswer


def sse(model='deepseek-flash', *, finish='stop', cap=3):
    packet = dict(id='response-one', model=model, choices=[dict(index=0,
        delta={'content':'source-supported answer'},finish_reason=finish)],
        usage=dict(prompt_tokens=7,completion_tokens=cap,total_tokens=7+cap))
    return ('data: '+json.dumps(packet)+'\n\ndata: [DONE]\n\n').encode()


class Response:
    def __init__(self, chunks):
        self.chunks=iter(chunks);self.timeouts=[];self.closed=False;self.reads=0
        self.fp=SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=self.timeouts.append)))
        self.headers={'Content-Type':'text/event-stream; charset=utf-8'}
    def __enter__(self):return self
    def __exit__(self,*args):self.closed=True
    def getcode(self):return 200
    def read1(self,size):
        self.reads+=1
        return next(self.chunks,b'')


def send(monkeypatch, response, guard=lambda:None):
    opened=[]
    def opening(request,timeout):opened.append((request,timeout));return response
    monkeypatch.setattr(cloud,'build_opener',lambda *args:SimpleNamespace(open=opening))
    request=cloud.Request('https://api.deepseek.com/chat/completions',data=b'{}')
    result=cloud._send_ordinary_stream(request,120,before_send=guard,expected_model='deepseek-flash')
    assert len(opened)==1
    return result


def test_one_http_internal_aggregate_closes_at_done_without_waiting_eof(monkeypatch):
    raw=sse();response=Response([raw[:20],raw[20:],b'NEVER_READ'])
    result=send(monkeypatch,response)
    assert result.payload['choices'][0]['message']['content']=='source-supported answer'
    assert result.stream_wire==raw and response.closed and response.reads==2
    assert len(response.timeouts)==2 and all(0<n<=120 for n in response.timeouts)


def test_original_validator_still_rejects_length_with_known_usage(monkeypatch):
    result=send(monkeypatch,Response([sse(finish='length')]))
    assert result.payload['usage']['completion_tokens']==3
    with pytest.raises(TruncatedAnswer):cloud.CloudChat._validate(result.payload)


def test_postdispatch_revoke_is_never_refundable_not_sent(monkeypatch):
    calls=[]
    def revoke():
        calls.append(1)
        if len(calls)>1:raise ProviderRequestNotSent('TEST_REVOKE')
    with pytest.raises(cloud._DiagnosticFailure,match='MODEL_REQUEST_FAILED') as caught:
        send(monkeypatch,Response([sse()]),revoke)
    assert not isinstance(caught.value,ProviderRequestNotSent)
    assert caught.value.code=='STREAM_FENCE_REVOKED'


def test_absolute_deadline_not_extended_by_active_chunks(monkeypatch):
    clock=iter([0,1,2,121])
    monkeypatch.setattr(cloud.time,'monotonic',lambda:next(clock))
    with pytest.raises(TimeoutError):send(monkeypatch,Response([sse()]))


def test_half_stream_eof_does_not_return_a_candidate(monkeypatch):
    with pytest.raises(cloud._DiagnosticFailure) as caught:
        send(monkeypatch,Response([sse().split(b'data: [DONE]')[0]]))
    assert caught.value.code=='STREAM_RESPONSE_INCOMPLETE'


def test_exact_model_and_content_type_required(monkeypatch):
    with pytest.raises(cloud._DiagnosticFailure):send(monkeypatch,Response([sse(model='other-model')]))
    response=Response([sse()]);response.headers={'Content-Type':'application/json'}
    with pytest.raises(cloud._DiagnosticFailure):send(monkeypatch,response)


def evidence(stream):
    provider,model='deepseek','deepseek-flash'
    body=dict(model=model,messages=[dict(role='user',content=SYNTHETIC_OUTPUT_PROBE)],
        max_tokens=4096,stream=stream,thinking={'type':'disabled'},temperature=0)
    response=(sse(finish='length',cap=4096).decode() if stream else json.dumps(dict(model=model,
        choices=[dict(message={'content':'public fixture'},finish_reason='length')],
        usage=dict(prompt_tokens=7,completion_tokens=4096,total_tokens=4103))))
    return OutputCapabilityEvidence(provider,model,(STREAM_PROTOCOLS if stream else PROTOCOLS)[(provider,model)][0],
        'https://api.deepseek.com/chat/completions',4096,json.dumps(body),response,'fixture-run','fixture-attempt')


@pytest.mark.parametrize('stream,schema',[(False,3),(True,4)])
def test_versioned_proof_loads_exact_raw_delivery_only(tmp_path,stream,schema):
    from dataclasses import asdict
    item=evidence(stream);raw=json.dumps(dict(schema_version=schema,receipts=[asdict(item)])).encode()
    path=tmp_path/'public-proof.json';path.write_bytes(raw)
    loaded=load_output_capabilities(path,hashlib.sha256(raw).hexdigest())
    assert loaded[0].streaming==stream and loaded[0].response['usage']['completion_tokens']==4096
    bad=json.dumps(dict(schema_version=4 if schema==3 else 3,receipts=[asdict(item)])).encode();path.write_bytes(bad)
    with pytest.raises(ProviderRequestNotSent):load_output_capabilities(path,hashlib.sha256(bad).hexdigest())


def test_aggregated_json_cannot_masquerade_as_raw_stream_proof():
    from dataclasses import replace
    item=evidence(True)
    with pytest.raises(ProviderRequestNotSent):replace(item,response_wire=json.dumps(item.response))


def test_real_local_stream_frame_and_wire_binding_keep_frozen_counter(monkeypatch):
    from importlib.metadata import version, PackageNotFoundError
    try:version('deepseek-recipe')
    except PackageNotFoundError:pytest.skip('Pinned recipe unavailable on this host; native Linux check required')
    from backend.app.adapters.models.v6_text_capacity import OrdinaryDeepseekV41TextCapacityCounter,DeepseekV41RecipeRenderer
    from backend.app.adapters.models.ordinary_stream_capacity import OrdinaryStreamCapacityCounter
    from backend.app.adapters.models.service_capacity import ServiceCapacity
    root=Path(__file__).resolve().parents[2]
    path=root/'var/model-assets/deepseek-v41/tokenizer.json'
    if not path.is_file():pytest.skip('Pinned local tokenizer unavailable; hosted checks NOT RUN')
    base=OrdinaryDeepseekV41TextCapacityCounter(DeepseekV41RecipeRenderer(path))
    normal=ServiceCapacity('deepseek','deepseek-flash',base,kind='deepseek_text')
    streamed=ServiceCapacity('deepseek','deepseek-flash',OrdinaryStreamCapacityCounter(base,'deepseek_text'),kind='deepseek_text')
    messages=[dict(role='user',content='Only original evidence.')]
    n=normal.render_chat(messages,4096);s=streamed.render_chat(messages,4096)
    assert n['stream'] is False and s['stream'] is True
    assert normal.measure(n,json.dumps(n).encode())==streamed.measure(s,json.dumps(s).encode())
    with pytest.raises(Exception):base.measure('deepseek','deepseek-flash',s,scope='LOCAL_FROZEN_REJECT')
    with pytest.raises(ProviderRequestNotSent):streamed.measure(s,json.dumps(n).encode())
    with pytest.raises(ProviderRequestNotSent):streamed.bind_output_evidence(evidence(False))
    with pytest.raises(ProviderRequestNotSent):streamed.assert_output_capacity(512,execution_kind='REAL')
    streamed.bind_output_evidence(evidence(True))
    assert streamed.output_readiness['observed_output_caps']==[4096]
    assert streamed.output_readiness['inferred_lower_output_caps']==[1024,2048]
