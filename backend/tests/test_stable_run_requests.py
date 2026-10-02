import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

def chain_fixture(tmp_path,monkeypatch):
    from backend.tests.test_deepseek_safety import gateway
    from backend.app.application.budget import InMemoryBudgetGate
    from backend.app.application.knowledge_gateway import KnowledgeGateway
    from backend.app.application.quick_chain import LangChainQuickChain, QuickSettings
    from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
    from backend.app.domain.models import ChunkRecord
    from backend.app.domain.scope import Scope
    cloud,path,requests=gateway(tmp_path,monkeypatch)
    repo=InMemoryRetrievalRepository()
    repo.add(ChunkRecord('c1','kb','doc','ver','Synthetic blue box.',{}))
    chain=LangChainQuickChain(KnowledgeGateway(HybridRetriever(repo)),cloud_answer_gateway=cloud,budget_gate=InMemoryBudgetGate(1000))
    def invoke():
        return chain.invoke('Synthetic',Scope.from_ids(['kb']),run_id='same-run',
            settings=QuickSettings(cloud_enabled=True,prefer_cloud=True),cloud_allowed_by_kb={'kb':True})
    return invoke,path,requests

def test_same_quick_run_cannot_submit_twice(tmp_path,monkeypatch):
    invoke,path,requests=chain_fixture(tmp_path,monkeypatch)
    invoke();invoke()
    assert len(requests)==1
    assert json.loads(path.read_text())['consumed_attempts']==1

def test_concurrent_same_quick_run_can_submit_only_once(tmp_path,monkeypatch):
    invoke,path,requests=chain_fixture(tmp_path,monkeypatch)
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _:invoke(),range(6)))
    assert len(requests)==1
    assert json.loads(path.read_text())['consumed_attempts']==1

def test_restarted_quick_run_cannot_submit_again(tmp_path,monkeypatch):
    invoke,path,_=chain_fixture(tmp_path,monkeypatch)
    invoke()
    script='''
import json,sys
from pathlib import Path
from backend.app.adapters.models import deepseek
from backend.app.application.session_attempts import SessionAttemptGate
from backend.app.application.budget import InMemoryBudgetGate
from backend.app.application.knowledge_gateway import KnowledgeGateway
from backend.app.application.quick_chain import LangChainQuickChain, QuickSettings
from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.models import ChunkRecord
from backend.app.domain.scope import Scope
def forbidden_transport(*args):
    raise AssertionError('Duplicate reached mocked transport')
deepseek.build_opener=forbidden_transport
path=Path(sys.argv[1])
cloud=deepseek.DeepSeekGateway(api_key='synthetic',cloud_enabled=True,attempt_gate=SessionAttemptGate(path))
repo=InMemoryRetrievalRepository()
repo.add(ChunkRecord('c1','kb','doc','ver','Synthetic blue box.',{}))
chain=LangChainQuickChain(KnowledgeGateway(HybridRetriever(repo)),cloud_answer_gateway=cloud,budget_gate=InMemoryBudgetGate(1000))
chain.invoke('Synthetic',Scope.from_ids(['kb']),run_id='same-run',settings=QuickSettings(cloud_enabled=True,prefer_cloud=True),cloud_allowed_by_kb={'kb':True})
assert json.loads(path.read_text())['consumed_attempts']==1
'''
    run=subprocess.run([sys.executable,'-B','-c',script,str(path)],capture_output=True,timeout=15)
    assert run.returncode==0,run.stderr.decode(errors='replace')

def test_same_run_distinct_purposes_and_explicit_attempts_have_stable_separate_identity(tmp_path):
    from backend.app.ports.session_attempts import request_identity
    from backend.app.application.session_attempts import SessionAttemptGate,AttemptDenied
    from backend.tests.test_deepseek_safety import ledger_file
    path=ledger_file(tmp_path)
    identities=[request_identity('same-run',purpose,attempt) for purpose,attempt in [('query',1),('quick.answer',1),('quick.answer',2)]]
    assert len(set(identities))==3
    for identity in identities:
        gate=SessionAttemptGate(path)
        attempt_id=gate.reserve(request_id=identity)
        gate.finish(attempt_id,'unknown')
        with pytest.raises(AttemptDenied,match='SESSION_REQUEST_ALREADY_RESERVED'):
            SessionAttemptGate(path).reserve(request_id=identity)
    assert json.loads(path.read_text())['consumed_attempts']==3
    assert request_identity('same-run','quick.answer',1)==identities[1]

def test_identity_does_not_confuse_delimiters_or_accept_automatic_retry_labels():
    from backend.app.ports.session_attempts import request_identity
    assert request_identity('run:answer','query')!=request_identity('run','answer.query')
    for attempt in (0,True,'1'):
        with pytest.raises(ValueError,match='REQUEST_IDENTITY_INVALID'):
            request_identity('run','quick.answer',attempt)
