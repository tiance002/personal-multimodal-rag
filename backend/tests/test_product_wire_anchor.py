"""SIMULATED independent-wire-anchor regressions; prior gold/fixtures unchanged."""
import hashlib,json
import pytest
from backend.tests.test_reviewed_product_request import (
    env,transport,durable_client,enable,invoke,read,write,digest,TASK,AttemptDenied,ProviderUnavailable,
)

@pytest.mark.parametrize('change',['intent','receipt','both'])
def test_mutable_wire_evidence_cannot_replace_original_anchor(env,monkeypatch,change):
    e=env;sent=transport(monkeypatch);c=durable_client(e);enable(e.p)
    original=e.m.ProductReceiptFileSink.persist;checks=[]
    def substitute(self,**kw):
        prepared=read(self.path);foreign=digest('another provider payload');receipt=dict(kw['receipt'])
        if change in ('intent','both'):
            prepared['binding']['wire_request_sha256']=foreign;write(self.path,prepared)
        if change in ('receipt','both'):receipt['wire_request_sha256']=foreign
        before=self.path.read_bytes()
        with pytest.raises(AttemptDenied,match='BINDING'):
            original(self,gate=kw['gate'],attempt_id=kw['attempt_id'],receipt=receipt)
        assert self.path.read_bytes()==before
        checks.append(True)
        raise OSError('SIMULATED discard rejected wire evidence')
    monkeypatch.setattr(e.m.ProductReceiptFileSink,'persist',substitute)
    with pytest.raises(ProviderUnavailable,match='RECEIPT'):
        with e.g.execution_scope():invoke(e,c)
    d=read(e.p)
    assert checks==[True] and len(sent)==1 and d['consumed_attempts']==24
    assert d['attempts'][-1]['status']=='unknown' and e.g._used(d)==8365
    assert d['calls_allowed_in_this_task'] is False

@pytest.mark.parametrize('loss',['memory_lost','fresh_writer'])
def test_no_live_anchor_cannot_recover_trust_from_files(env,monkeypatch,loss):
    e=env;sent=transport(monkeypatch);c=durable_client(e);enable(e.p)
    original=e.m.ProductReceiptFileSink.persist;checks=[]
    def substitute(self,**kw):
        candidate=self
        if loss=='memory_lost':self._wire_anchor=None
        else:candidate=e.m.ProductReceiptFileSink(self.path,scope_id=TASK)
        before=self.path.read_bytes()
        with pytest.raises(AttemptDenied,match='ANCHOR'):
            original(candidate,**kw)
        assert self.path.read_bytes()==before
        checks.append(True)
        raise OSError('SIMULATED no process anchor after restart/loss')
    monkeypatch.setattr(e.m.ProductReceiptFileSink,'persist',substitute)
    with pytest.raises(ProviderUnavailable,match='RECEIPT'):
        with e.g.execution_scope():invoke(e,c)
    d=read(e.p)
    assert checks==[True] and len(sent)==1 and d['consumed_attempts']==24
    assert d['attempts'][-1]['status']=='unknown' and e.g._used(d)==8365
    assert d['calls_allowed_in_this_task'] is False

def test_legitimate_sent_payload_matches_memory_anchor_and_persisted_receipt(env,monkeypatch):
    e=env;sent=transport(monkeypatch);c=durable_client(e);enable(e.p)
    prepare=e.m.ProductReceiptFileSink.prepare;seen=[]
    def observe(self,**kw):
        payload=kw['wire_request_payload'];assert type(payload) is bytes
        result=prepare(self,**kw)
        seen.append(hashlib.sha256(payload).hexdigest())
        return result
    monkeypatch.setattr(e.m.ProductReceiptFileSink,'prepare',observe)
    with e.g.execution_scope():assert invoke(e,c)=='顾遥 [E1]'
    saved=read(c.receipt_sink.path);expected=digest(json.dumps(sent[0]))
    assert seen==[expected] and saved['binding']['wire_request_sha256']==expected
    assert saved['receipt']['wire_request_sha256']==expected and saved['state']=='receipt_persisted'
    assert e.g._used(read(e.p))==6383 and read(e.p)['consumed_attempts']==24
    assert c.receipt_sink._wire_anchor is None


def test_independent_coherent_foreign_wire_receipt_must_deny(env,monkeypatch):
    e=env; sent=transport(monkeypatch); c=durable_client(e); enable(e.p)
    original=e.m.ProductReceiptFileSink.persist; checks=[]
    def substitute(self,**kw):
        # Simulate a mismatched stored intent plus receipt from another payload.
        # All spec/request/attempt fields remain the correctly reserved identity.
        prepared=read(self.path); foreign=digest('another provider payload')
        prepared['binding']['wire_request_sha256']=foreign; write(self.path,prepared)
        receipt=dict(kw['receipt']); receipt['wire_request_sha256']=foreign
        with pytest.raises(AttemptDenied,match='BINDING'):
            original(self,gate=kw['gate'],attempt_id=kw['attempt_id'],receipt=receipt)
        checks.append(True)
        raise OSError('SIMULATED preserve unknown after rejected foreign evidence')
    monkeypatch.setattr(e.m.ProductReceiptFileSink,'persist',substitute)
    with pytest.raises(ProviderUnavailable,match='RECEIPT'):
        with e.g.execution_scope(): invoke(e,c)
    assert checks==[True] and len(sent)==1
