"""SIMULATED regressions for independently demonstrated product receipt P1s."""
import dataclasses,hashlib,json,os
from pathlib import Path
import pytest
from backend.tests.test_reviewed_product_request import (
    env,client,transport,enable,invoke,read,write,RUN,TASK,digest,
    request_identity,ProviderRequestNotSent,ProviderUnavailable,deepseek,LangChainQuickChain,
)

def test_missing_durable_receipt_must_deny(env,monkeypatch):
    e=env;sent=transport(monkeypatch);enable(e.p)
    with e.g.execution_scope():
        with pytest.raises(ProviderRequestNotSent):invoke(e,client(e))
    assert not sent and read(e.p)['consumed_attempts']==23
    assert read(e.p)['calls_allowed_in_this_task'] is False

def test_product_subclass_without_context_must_deny(env,monkeypatch):
    e=env;sent=transport(monkeypatch)
    class Derived(e.m.ReviewedProductRequestGate):pass
    e.g=Derived(e.p,scope_id=TASK);enable(e.p)
    with e.g.execution_scope():
        with pytest.raises(ProviderRequestNotSent):
            client(e,lambda r:None).answer_with_budget(e.prompt,30,512,cloud_authorized=True,
                request_id=request_identity(RUN,'quick.answer',1))
    assert not sent and read(e.p)['consumed_attempts']==23

def durable(e,path=None):
    return client(e,e.m.ProductReceiptFileSink(path or e.p.parent/'receipt.json',scope_id=TASK))

@pytest.mark.parametrize('sink',[None,lambda r:None,lambda r:'ACK'])
def test_missing_or_memory_only_sink_never_reserves(env,monkeypatch,sink):
    e=env;sent=transport(monkeypatch);enable(e.p)
    with e.g.execution_scope():
        with pytest.raises(ProviderRequestNotSent):invoke(e,client(e,sink))
    assert not sent and read(e.p)['consumed_attempts']==23

@pytest.mark.parametrize('kind',['subclass','wrapper','sink_subclass'])
def test_unsupported_gate_or_sink_types_cannot_silently_bypass(env,monkeypatch,kind):
    e=env;sent=transport(monkeypatch);c=durable(e);enable(e.p)
    if kind=='sink_subclass':
        class Sink(e.m.ProductReceiptFileSink):pass
        c.receipt_sink=Sink(e.p.parent/'derived-receipt.json',scope_id=TASK)
    elif kind=='subclass':
        class Gate(e.m.ReviewedProductRequestGate):pass
        c.attempt_gate=Gate(e.p,scope_id=TASK)
    else:
        class Wrapper:
            def __getattr__(self,name):return getattr(e.g,name)
        c.attempt_gate=Wrapper()
    with e.g.execution_scope():
        with pytest.raises(ProviderRequestNotSent):invoke(e,c)
    assert not sent and read(e.p)['consumed_attempts']==23

def test_receipt_writer_preflight_path_failure_prevents_transport_and_reserve(env,monkeypatch):
    e=env;sent=transport(monkeypatch);c=durable(e,e.p.parent/'absent'/'receipt.json');enable(e.p)
    with e.g.execution_scope():
        with pytest.raises(ProviderRequestNotSent,match='RECEIPT'):invoke(e,c)
    assert not sent and read(e.p)['consumed_attempts']==23

def test_receipt_binding_and_provider_evidence_persist_before_settlement(env,monkeypatch):
    e=env;sent=transport(monkeypatch);c=durable(e);enable(e.p)
    observed=[];original_finish=e.g.finish_with_usage
    def finish(attempt,status,usage):
        saved=read(c.receipt_sink.path)
        observed.append(saved)
        assert saved['state']=='receipt_persisted' and saved['receipt']['attempt_id']==attempt
        return original_finish(attempt,status,usage)
    monkeypatch.setattr(e.g,'finish_with_usage',finish)
    with e.g.execution_scope():assert invoke(e,c)=='顾遥 [E1]'
    d=read(e.p);saved=read(c.receipt_sink.path);r=saved['receipt']
    assert len(sent)==len(observed)==1 and d['consumed_attempts']==24 and e.g._used(d)==6383
    assert r['attempt_id']==d['attempts'][-1]['attempt_id'] and r['product_scope_id']==TASK
    assert r['product_spec_sha256']==e.m._digest(dataclasses.asdict(e.spec))
    assert r['request_id']==request_identity(RUN,'quick.answer',1) and r['prompt_sha256']==digest(e.prompt)
    assert r['wire_request_sha256']==hashlib.sha256(json.dumps(sent[0]).encode('utf-8')).hexdigest()
    assert saved['binding']['wire_request_sha256']==r['wire_request_sha256']

@pytest.mark.parametrize('stage',['replace','readback'])
def test_persistence_failure_preserves_unknown_budget_and_closes_scope(env,monkeypatch,stage):
    e=env;sent=transport(monkeypatch);c=durable(e);enable(e.p)
    original_replace=os.replace;original_read=Path.read_bytes
    def replace(src,dst):
        if stage=='replace' and Path(dst)==c.receipt_sink.path:raise OSError('SIMULATED receipt persistence failure')
        return original_replace(src,dst)
    def read_bytes(p):
        data=original_read(p)
        if stage=='readback' and p==c.receipt_sink.path and b'receipt_persisted' in data:
            raise OSError('SIMULATED receipt acknowledgement failure')
        return data
    with monkeypatch.context() as patch:
        patch.setattr(os,'replace',replace);patch.setattr(Path,'read_bytes',read_bytes)
        with pytest.raises(ProviderUnavailable,match='RECEIPT'):
            with e.g.execution_scope():invoke(e,c)
    d=read(e.p);assert len(sent)==1 and d['consumed_attempts']==24 and d['reserved_attempts']==0
    assert d['attempts'][-1]['status']=='unknown' and d['attempts'][-1]['validation_tokens']['state']=='unknown'
    assert e.g._used(d)==6276 + len(e.prompt.encode("utf-8")) + 256 + e.spec.output_cap and d['calls_allowed_in_this_task'] is False
    enable(e.p)
    with pytest.raises(Exception):
        with e.g.execution_scope():invoke(e,durable(e,e.p.parent/'no-retry.json'))
    assert len(sent)==1 and read(e.p)['calls_allowed_in_this_task'] is False

@pytest.mark.parametrize('field',['request_id','attempt_id','product_scope_id','product_spec_sha256','prompt_sha256','wire_request_sha256'])
def test_foreign_receipt_cannot_replace_bound_pending_evidence(env,monkeypatch,field):
    e=env;transport(monkeypatch);c=durable(e);enable(e.p)
    original_persist=c.receipt_sink.persist
    before_after=[]
    def persist(**kw):
        before=c.receipt_sink.path.read_bytes();forged=dict(kw['receipt']);forged[field]='foreign'
        with pytest.raises(Exception):original_persist(gate=kw['gate'],attempt_id=kw['attempt_id'],receipt=forged)
        assert c.receipt_sink.path.read_bytes()==before
        before_after.append(True)
        return original_persist(**kw)
    monkeypatch.setattr(e.m.ProductReceiptFileSink,'persist',lambda self,**kw:persist(**kw))
    with e.g.execution_scope():invoke(e,c)
    assert before_after==[True] and read(c.receipt_sink.path)['state']=='receipt_persisted'
