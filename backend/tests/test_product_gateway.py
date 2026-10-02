import json,uuid,threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from backend.tests.test_answer_service import RecordingRunStore
from backend.tests.test_deepseek_safety import gateway
from backend.app.application.retrieval import InMemoryRetrievalRepository
from backend.app.application.budget import InMemoryBudgetGate
from backend.app.domain.models import ChunkRecord
from backend.app.config import Settings

class Local:
    provider_kind='local';provider_name='ollama';chat_model='synthetic-local-generation'
    def __init__(self,fail=False):self.fail=fail;self.calls=0
    def answer_with_budget(self,prompt,timeout_seconds,max_tokens):
        self.calls+=1
        if self.fail:raise TimeoutError('synthetic-local-timeout')
        return 'Synthetic blue box [E1]'

class Store(RecordingRunStore,InMemoryRetrievalRepository):
    def __init__(self,allowed=True):
        RecordingRunStore.__init__(self);InMemoryRetrievalRepository.__init__(self)
        self.allowed=allowed;self.claims={};self.lock=threading.Lock()
        self.conversation={'id':str(uuid.uuid4()),'knowledge_base_scope':['kb'],'document_scope':['doc']}
        self.add(ChunkRecord('c1','kb','doc','v-current','Synthetic blue box.',{}))
        self.add(ChunkRecord('private','other','foreign','v-foreign','Synthetic green box.',{}))
    def get_knowledge_base(self,kb):return {'id':kb,'cloud_allowed':self.allowed if kb=='kb' else False}
    def get_conversation(self,id):return self.conversation if id==self.conversation['id'] else None
    def create_run_once(self,conversation_id,kb_scope,document_scope,q0,request_id):
        with self.lock:
            payload=(conversation_id,kb_scope,document_scope,q0)
            if request_id in self.claims:
                if self.claims[request_id]!=payload:raise ValueError('REQUEST_ID_CONFLICT')
                return request_id,False
            self.claims[request_id]=payload;return request_id,True
    def get_document_content(self,*args):return ''
    def list_documents(self,*args):return []
    def get_document_access(self,*args):return None

def setup(tmp_path,monkeypatch,*,enabled=False,preferred=False,fallback=False,allowed=True,fail=False,budget=100,estimate=1,transport_error=None):
    from backend.app import bootstrap
    from backend.app.main import create_app
    cloud,path,requests=gateway(tmp_path,monkeypatch,error=transport_error)
    local=Local(fail);store=Store(allowed);cost=InMemoryBudgetGate(budget)
    monkeypatch.setattr(bootstrap,'PostgresKnowledgeRepository',lambda *a,**kw:store)
    monkeypatch.setattr(bootstrap,'PostgresBudgetGate',lambda *a,**kw:cost)
    settings=Settings(database_url='sqlite+pysqlite:///:memory:',storage_root=tmp_path/'storage',
        cloud_enabled=enabled,prefer_cloud=preferred,cloud_fallback_enabled=fallback,cloud_cost_estimate_microunits=estimate)
    container=bootstrap.build_container(settings,model=local,agent_model=None,cloud_model=cloud)
    app=create_app(settings,container=container)
    return TestClient(app),store,local,cloud,path,requests,cost

def send(fixture,request_id=None,question='Synthetic',**extra):
    client,store,*_=fixture
    return client.post('/api/v1/conversations/'+store.conversation['id']+'/messages',json={
        'content':question,'mode':'quick','request_id':request_id or str(uuid.uuid4()),
        'expected_knowledge_base_scope':['kb'],'expected_document_scope':['doc'],**extra})

@pytest.mark.parametrize('enabled,preferred',[(False,False),(False,True),(True,False)])
def test_product_default_or_disabled_stays_local(tmp_path,monkeypatch,enabled,preferred):
    f=setup(tmp_path,monkeypatch,enabled=enabled,preferred=preferred)
    response=send(f);assert response.status_code==201
    assert response.json()['data']['error_code'] is None
    assert f[2].calls==1 and not f[5] and json.loads(f[4].read_text())['consumed_attempts']==0

def test_product_explicit_cloud_and_scope_reaches_provider(tmp_path,monkeypatch):
    f=setup(tmp_path,monkeypatch,enabled=True,preferred=True)
    response=send(f);assert response.status_code==201
    data=response.json()['data'];assert data['error_code'] is None and data['citations']==['E1']
    assert f[2].calls==0 and len(f[5])==1
    assert data['trace']['metrics']['cloud_called'] is True
    assert data['trace']['metrics']['selected_context_chunk_ids']==['c1']

@pytest.mark.parametrize('fallback',[False,True])
def test_private_scope_never_sends_cloud(tmp_path,monkeypatch,fallback):
    f=setup(tmp_path,monkeypatch,enabled=True,preferred=not fallback,fallback=fallback,allowed=False,fail=fallback)
    response=send(f);assert response.json()['data']['error_code']=='CLOUD_EGRESS_DISABLED'
    assert not f[5] and json.loads(f[4].read_text())['consumed_attempts']==0

def test_sensitive_configuration_question_never_sends(tmp_path,monkeypatch):
    f=setup(tmp_path,monkeypatch,enabled=True,preferred=True)
    response=send(f,question='Does cloud_allowed allow external egress?')
    assert response.status_code==201 and not f[5] and f[2].calls==0

def test_local_failure_has_one_authorized_bounded_fallback(tmp_path,monkeypatch):
    f=setup(tmp_path,monkeypatch,enabled=True,fallback=True,fail=True)
    data=send(f).json()['data'];assert data['error_code'] is None
    assert f[2].calls==1 and len(f[5])==1
    assert [x['path'] for x in data['trace']['metrics']['model_calls']]==['LOCAL','CLOUD']
    assert data['trace']['metrics']['answer_hardening']['generation_routes'][-1]['reason']=='LOCAL_FAILURE_CLOUD_FALLBACK'

@pytest.mark.parametrize('preferred,fallback',[(True,False),(False,True)])
def test_budget_exhaustion_never_sends(tmp_path,monkeypatch,preferred,fallback):
    f=setup(tmp_path,monkeypatch,enabled=True,preferred=preferred,fallback=fallback,fail=fallback,budget=0)
    assert send(f).json()['data']['error_code']=='MONTHLY_BUDGET_EXCEEDED'
    assert not f[5]

def test_missing_cost_estimate_fails_closed(tmp_path,monkeypatch):
    f=setup(tmp_path,monkeypatch,enabled=True,preferred=True,estimate=0)
    assert send(f).json()['data']['error_code']=='CLOUD_COST_ESTIMATE_REQUIRED'
    assert not f[5]

@pytest.mark.parametrize('unknown',[False,True])
def test_duplicate_product_request_does_not_regenerate_or_refund_unknown(tmp_path,monkeypatch,unknown):
    f=setup(tmp_path,monkeypatch,enabled=True,preferred=True,transport_error=TimeoutError() if unknown else None)
    identity=str(uuid.uuid4());send(f,identity);response=send(f,identity)
    assert response.status_code==409 and response.json()['error']['code']=='DUPLICATE_REQUEST'
    assert len(f[5])==1 and json.loads(f[4].read_text())['consumed_attempts']==1
    assert [r['state'] for r in f[6].reservations.values()]==['unknown' if unknown else 'settled']

def test_concurrent_product_duplicate_claim_sends_once(tmp_path,monkeypatch):
    f=setup(tmp_path,monkeypatch,enabled=True,preferred=True);identity=str(uuid.uuid4())
    with ThreadPoolExecutor(max_workers=4) as pool:responses=list(pool.map(lambda _:send(f,identity),range(4)))
    assert sorted(r.status_code for r in responses)==[201,409,409,409] and len(f[5])==1

def test_scope_change_blocks_before_generation(tmp_path,monkeypatch):
    f=setup(tmp_path,monkeypatch,enabled=True,preferred=True)
    response=send(f,expected_document_scope=['foreign'])
    assert response.status_code==409 and not f[5] and f[2].calls==0


def test_legacy_cloud_request_without_stable_id_is_rejected(tmp_path,monkeypatch):
    f=setup(tmp_path,monkeypatch,enabled=True,preferred=True)
    response=f[0].post('/api/v1/conversations/'+f[1].conversation['id']+'/messages',json={'content':'Synthetic'})
    assert response.status_code==409 and response.json()['error']['code']=='IDEMPOTENCY_REQUIRED'
    assert not f[5] and f[2].calls==0

def test_invalid_request_uuid_is_rejected_before_generation(tmp_path,monkeypatch):
    f=setup(tmp_path,monkeypatch,enabled=True,preferred=True)
    assert send(f,'not-a-uuid').status_code==422 and not f[5] and f[2].calls==0

def test_conflicting_same_uuid_cannot_change_payload(tmp_path,monkeypatch):
    f=setup(tmp_path,monkeypatch,enabled=True,preferred=True);identity=str(uuid.uuid4())
    send(f,identity)
    response=send(f,identity,question='Different synthetic question')
    assert response.status_code==409 and response.json()['error']['code']=='REQUEST_ID_CONFLICT' and len(f[5])==1

def test_fallback_global_switch_off_never_sends(tmp_path,monkeypatch):
    f=setup(tmp_path,monkeypatch,enabled=False,fallback=True,fail=True)
    assert send(f).json()['data']['error_code']=='CLOUD_EGRESS_DISABLED' and not f[5]

def test_factory_cloud_disabled_does_not_access_credentials_or_ledger(tmp_path,monkeypatch):
    from backend.app import bootstrap
    def forbidden(*a,**kw):raise AssertionError('disabled cloud constructed provider/gate')
    monkeypatch.setattr(bootstrap,'DeepSeekGateway',forbidden)
    monkeypatch.setattr(bootstrap,'SessionAttemptGate',forbidden)
    c=bootstrap.build_container(Settings(database_url='sqlite+pysqlite:///:memory:',storage_root=tmp_path),model=None)
    assert c.cloud_gateway is None

def test_factory_enabled_uses_existing_fail_closed_gate_without_transport(tmp_path,monkeypatch):
    from backend.app import bootstrap
    sentinel=object();seen={}
    monkeypatch.setattr(bootstrap,'SessionAttemptGate',lambda:sentinel)
    monkeypatch.setenv('DEEPSEEK_API_KEY','synthetic-fixture-only')
    def provider(**kw):seen.update(kw);return object()
    monkeypatch.setattr(bootstrap,'DeepSeekGateway',provider)
    c=bootstrap.build_container(Settings(database_url='sqlite+pysqlite:///:memory:',storage_root=tmp_path,cloud_enabled=True),model=None)
    assert c.cloud_gateway is c.quick_chain.cloud_answer_gateway and seen['attempt_gate'] is sentinel

def test_product_claim_sql_survives_repository_recreation_and_conflicts():
    from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
    data={};sql=[]
    class Result:
        def __init__(self,value):self.value=value
        def scalar(self):return self.value
        def mappings(self):return self
        def one(self):return self.value
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,statement,params):
            text=str(statement);sql.append(text);identity=str(params['id'])
            if 'INSERT INTO rag_runs' in text:
                assert 'ON CONFLICT (id) DO NOTHING RETURNING id' in text
                if identity in data:return Result(None)
                data[identity]={'conversation_id':params['conversation_id'],'knowledge_base_scope':json.loads(params['kb']),'document_scope':json.loads(params['docs']),'q0':params['q0']}
                return Result(identity)
            return Result(data[identity])
    class Engine:
        def begin(self):return Connection()
    def repository():
        obj=object.__new__(PostgresKnowledgeRepository);obj.engine=Engine();return obj
    identity=str(uuid.uuid4());args=('conv',['kb'],['doc'],'Synthetic',identity)
    assert repository().create_run_once(*args)==(identity,True)
    assert repository().create_run_once(*args)==(identity,False)
    with pytest.raises(ValueError,match='REQUEST_ID_CONFLICT'):
        repository().create_run_once('different',['kb'],['doc'],'Synthetic',identity)
    assert len(data)==1
