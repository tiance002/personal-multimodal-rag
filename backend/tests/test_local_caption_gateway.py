"""SIMULATED HTTP transport only; no real Ollama/API/model traffic."""
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from backend.app.adapters.models import ollama
from backend.app.ports.providers import ProviderUnavailable


def png(size=(8,8)):
    stream=io.BytesIO();Image.new('1',size).save(stream,format='PNG');return stream.getvalue()


def transport(monkeypatch, *, vision=True, busy=False, response=None, error=None):
    requests=[];handlers=[]
    body=response or {'done':True,'done_reason':'stop','model':'qwen3.5:4b',
        'message':{'content':json.dumps({'caption':'SIMULATED 上升后下降。'})},
        'prompt_eval_count':11,'eval_count':8,'total_duration':123}
    class Response:
        def __init__(self,data):self.data=json.dumps(data).encode()
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,n):assert n==65537;return self.data[:n]
    class Opener:
        def open(self,request,timeout):
            requests.append((request,timeout))
            if request.full_url.endswith('/api/show'):return Response({'capabilities':['vision'] if vision else ['completion']})
            if request.full_url.endswith('/api/ps'):return Response({'models':[{'name':'bge-m3:latest'}] if busy else []})
            if error:raise error
            return Response(body)
    def build(*items):handlers.extend(items);return Opener()
    monkeypatch.setattr(ollama,'build_opener',build)
    return requests,handlers


@pytest.mark.parametrize('url',['http://localhost:11434','https://127.0.0.1:11434',
    'http://203.0.113.1:11434','http://127.0.0.1','http://user:pass@127.0.0.1:11434',
    'http://127.0.0.1:11434/path','http://127.0.0.1:11434?x=1','http://127.0.0.1:11434#x'])
def test_nonliteral_or_unsafe_endpoint_never_opens_transport(monkeypatch,url):
    calls,_=transport(monkeypatch)
    gateway=ollama.OllamaGateway(url,'qwen3.5:4b','bge-m3:latest')
    with pytest.raises(ProviderUnavailable,match='CAPTION_LOCAL_ENDPOINT_REQUIRED'):
        gateway.caption_preflight(10)
    assert calls==[]


@pytest.mark.parametrize('url',['http://127.0.0.1:11434','http://[::1]:11434'])
def test_local_request_has_no_proxy_redirect_or_observation_and_records_observed_usage(monkeypatch,url):
    calls,handlers=transport(monkeypatch)
    observer=SimpleNamespace(observe=lambda **kw:pytest.fail('caption must not export observation'))
    gateway=ollama.OllamaGateway(url,'qwen3.5:4b','bge-m3:latest',observability=observer)
    gateway.caption_preflight(10)
    result=gateway.caption_image(png(),10)
    assert result.text=='SIMULATED 上升后下降。'
    assert result.model_requested=='qwen3.5:4b' and result.model_reported=='qwen3.5:4b'
    assert result.model_digest is None
    assert result.usage_actual['input_tokens']==11 and result.usage_actual['output_tokens']==8
    assert all(h.proxies=={} for h in handlers if isinstance(h,ollama.ProxyHandler))
    redirect=next(h for h in handlers if isinstance(h,ollama.HTTPRedirectHandler))
    with pytest.raises(ProviderUnavailable,match='CAPTION_REDIRECT_DENIED'):
        redirect.redirect_request(None,None,302,'redirect',{},'https://example.invalid')
    payload=json.loads(calls[-1][0].data)
    assert payload['keep_alive']=='0s' and payload['think'] is False
    assert payload['options']=={'temperature':0,'num_predict':256,'num_ctx':4096}
    assert len(calls)==3 and calls[1][0].get_method()=='GET'


@pytest.mark.parametrize('vision,busy,code',[(False,False,'CAPTION_VISION_UNAVAILABLE'),(True,True,'CAPTION_RESOURCE_BUSY')])
def test_preflight_rejects_no_vision_and_loaded_bge_without_unloading(monkeypatch,vision,busy,code):
    calls,_=transport(monkeypatch,vision=vision,busy=busy)
    gateway=ollama.OllamaGateway('http://127.0.0.1:11434','qwen3.5:4b','bge-m3:latest')
    with pytest.raises(ProviderUnavailable,match=code):gateway.caption_preflight(10)
    assert all(not c[0].full_url.endswith('/api/chat') for c in calls)


@pytest.mark.parametrize('fault',['length','empty','invalid','not_done','missing_model','timeout','oversize_response'])
def test_generation_failure_never_returns_ready_result(monkeypatch,fault):
    response={'done':True,'done_reason':'stop','model':'qwen3.5:4b',
              'message':{'content':json.dumps({'caption':'SIMULATED'})}}
    if fault=='length':response['done_reason']='length'
    if fault=='empty':response['message']['content']=json.dumps({'caption':''})
    if fault=='invalid':response['message']['content']='not json'
    if fault=='not_done':response['done']=False
    if fault=='missing_model':response.pop('model')
    if fault=='oversize_response':response['message']['content']='x'*70000
    calls,_=transport(monkeypatch,response=response,error=TimeoutError() if fault=='timeout' else None)
    gateway=ollama.OllamaGateway('http://127.0.0.1:11434','qwen3.5:4b','bge-m3:latest')
    gateway.caption_preflight(10)
    with pytest.raises(ProviderUnavailable,match='CAPTION_'):gateway.caption_image(png(),10)
    assert len(calls)==3


@pytest.mark.parametrize('image',[b'bad',b'x'*(4*1024*1024+1),png((2001,2000))],ids=['malformed','encoded-limit','pixel-limit'])
def test_image_limits_reject_before_generation(monkeypatch,image):
    calls,_=transport(monkeypatch)
    gateway=ollama.OllamaGateway('http://127.0.0.1:11434','qwen3.5:4b','bge-m3:latest')
    gateway.caption_preflight(10)
    with pytest.raises(ProviderUnavailable,match='CAPTION_'):gateway.caption_image(image,10)
    assert len(calls)==2


def test_unknown_usage_stays_unknown(monkeypatch):
    transport(monkeypatch,response={'done':True,'done_reason':'stop','model':'qwen3.5:4b',
        'message':{'content':json.dumps({'caption':'SIMULATED'})}})
    gateway=ollama.OllamaGateway('http://127.0.0.1:11434','qwen3.5:4b','bge-m3:latest')
    gateway.caption_preflight(10)
    result=gateway.caption_image(png(),10)
    assert result.usage_actual['input_tokens'] is None and result.usage_actual['output_tokens'] is None


def test_caption_requires_successful_preflight(monkeypatch):
    calls,_=transport(monkeypatch)
    gateway=ollama.OllamaGateway('http://127.0.0.1:11434','qwen3.5:4b','bge-m3:latest')
    with pytest.raises(ProviderUnavailable,match='CAPTION_VISION_UNAVAILABLE'):gateway.caption_image(png(),10)
    assert not calls


def test_preflight_rejects_ps_response_returning_after_total_deadline(monkeypatch):
    calls = []
    clock = [0.0]
    class Response:
        def __init__(self, path): self.path = path
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, n):
            assert n == 65537
            if self.path.endswith('/api/ps'):
                clock[0] = 11.0
                return b'{"models":[]}'
            return b'{"capabilities":["vision"]}'
    class Opener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            return Response(request.full_url)
    monkeypatch.setattr(ollama, 'build_opener', lambda *args: Opener())
    monkeypatch.setattr(ollama.time, 'monotonic', lambda: clock[0])
    gateway = ollama.OllamaGateway('http://127.0.0.1:11434', 'qwen3.5:4b', 'bge-m3:latest')
    with pytest.raises(ProviderUnavailable, match='CAPTION_TIMEOUT'):
        gateway.caption_preflight(10)
    with pytest.raises(ProviderUnavailable, match='CAPTION_VISION_UNAVAILABLE'):
        gateway.caption_image(png(), 10)
    assert len(calls) == 2


def test_wrapped_url_timeout_is_classified_as_timeout(monkeypatch):
    calls, _ = transport(monkeypatch, error=ollama.URLError(TimeoutError()))
    gateway = ollama.OllamaGateway('http://127.0.0.1:11434', 'qwen3.5:4b', 'bge-m3:latest')
    gateway.caption_preflight(10)
    with pytest.raises(ProviderUnavailable, match='^CAPTION_TIMEOUT$'):
        gateway.caption_image(png(), 10)
    assert len(calls) == 3
