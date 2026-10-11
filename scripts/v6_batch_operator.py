"""V6 six-pack ingestion operator. No service construction or automatic launch.

Uses normal CAS/repository/parser/prepared port/Factory/native claims. Coordinator
must inject reviewed ECS epoch1 guard and existing production dependencies.
CLI is offline inventory only; no credentials, gold artifacts or new ledger.
Incorporated inspected draft ingestion helpers are contract-tested here, and are
not runtime imports from var/reports. Two-stage runtime/scoring stays external.
"""
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta
from pathlib import Path
import hashlib,json,os,tempfile

class CheckpointBlocked(ValueError):pass

def canonical(value):
 return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()
def native_success_status(execution_kind):
 if execution_kind=='REAL':return 'succeeded'
 if execution_kind=='SIMULATED':return 'simulated'
 raise CheckpointBlocked('EXPLICIT_EXECUTION_KIND_REQUIRED')

def require_native_success(execution_kind,claims):
 expected=native_success_status(execution_kind)
 if not claims or any(c is None or c.get('status')!=expected for c in claims):
  raise CheckpointBlocked('NATIVE_KIND_MIXED_UNKNOWN_OR_INCOMPLETE_NO_REPLAY')

def read_bound_native_result(binding,claim,call):
 from backend.app.ports.xing_zero_reservation import free_dispatch_identity_sha256
 require_native_success(binding.permit.execution_kind,[claim])
 result=binding.read_result(claim['result'],request_id=call.request_id)
 if result.get('dispatch_identity_sha256')!=free_dispatch_identity_sha256(binding.permit,call):
  raise CheckpointBlocked('NATIVE_RESULT_EXECUTION_IDENTITY_DRIFT')
 return result
def utc(value):
 t=datetime.fromisoformat(value.replace('Z','+00:00')) if isinstance(value,str) else value
 if t.tzinfo is None or t.utcoffset()!=timedelta(0):raise CheckpointBlocked('UTC_REQUIRED')
 return t
def atomic_json(path,value):
 path=Path(path)
 if path.is_symlink():raise CheckpointBlocked('CHECKPOINT_SYMLINK_DENIED')
 fd,name=tempfile.mkstemp(prefix=path.name+'.pending-',dir=path.parent)
 try:
  with os.fdopen(fd,'wb') as f:f.write(canonical(value)+b'\n');f.flush();os.fsync(f.fileno())
  os.replace(name,path)
  if os.name!='nt':
   d=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
   try:os.fsync(d)
   finally:os.close(d)
 finally:
  # Remove only this exact newly-created temp file, never checkpoint/history.
  if os.path.exists(name):os.unlink(name)

class OperatorCheckpoint:
 def __init__(self,path,*,identity,execution_kind):
  if Path(path).is_symlink():raise CheckpointBlocked('CHECKPOINT_SYMLINK_DENIED')
  self.path=Path(path).resolve();self.kind=execution_kind;self.identity=identity
  if execution_kind not in ('REAL','SIMULATED') or not isinstance(identity,dict) or not identity:
   raise CheckpointBlocked('EXPLICIT_OPERATOR_IDENTITY_REQUIRED')
  self.path.parent.mkdir(parents=True,exist_ok=True)
  self.lock=self.path.with_suffix(self.path.suffix+'.lock')
  self.events=self.path.with_suffix(self.path.suffix+'.events.jsonl')
 @contextmanager
 def transaction(self):
  if self.lock.is_symlink() or self.path.is_symlink():raise CheckpointBlocked('CHECKPOINT_SYMLINK_DENIED')
  with self.lock.open('a+b') as lock:
   if os.name=='nt':
    import msvcrt
    if self.lock.stat().st_size==0:lock.write(b'0');lock.flush()
    lock.seek(0)
    try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:raise CheckpointBlocked('OPERATOR_SINGLE_WRITER_BUSY') from None
   else:
    import fcntl
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise CheckpointBlocked('OPERATOR_SINGLE_WRITER_BUSY') from None
   try:
    if self.path.exists():
     state=json.loads(self.path.read_bytes())
     if state.get('identity')!=self.identity or state.get('execution_kind')!=self.kind:
      raise CheckpointBlocked('OPERATOR_IDENTITY_DRIFT')
    else:state={'schema':'v6-operator-checkpoint/v1','identity':self.identity,'execution_kind':self.kind,'revision':0,'batch':None,'units':{},'history':[]}
    if state.get('schema')!='v6-operator-checkpoint/v1':raise CheckpointBlocked('CHECKPOINT_SCHEMA_INVALID')
    yield state
    state['revision']+=1
    atomic_json(self.path,state)
   finally:
    if os.name=='nt':lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
    else:fcntl.flock(lock,fcntl.LOCK_UN)
 def snapshot(self):
  with self.transaction() as state:return json.loads(canonical(state))
 def start_batch(self,*,phase,now,approved_batch_id,guard):
  if phase=='SETUP':guard();return None
  if phase!='BATCH' or not approved_batch_id:raise CheckpointBlocked('EXPLICIT_BATCH_APPROVAL_ID_REQUIRED')
  guard();now=utc(now)
  with self.transaction() as state:
   prior=state['batch']
   if prior:
    if prior['batch_id']!=approved_batch_id:raise CheckpointBlocked('BATCH_ID_DRIFT_NO_T0_RESET')
    if now>=utc(prior['deadline_utc']):raise CheckpointBlocked('BATCH_5H_DEADLINE_REACHED')
    return dict(prior)
   batch={'batch_id':approved_batch_id,'T0_utc':now.isoformat(),'deadline_utc':(now+timedelta(hours=5)).isoformat(),'meaning':'BEFORE_FIRST_FORMAL_BULK_OPERATION_INCLUDING_INGESTION'}
   state['batch']=batch;state['history'].append({'event':'BATCH_T0','value':batch})
   return dict(batch)
 def begin_allocation(self,key,pins):
  with self.transaction() as state:
   row=state['units'].get(key)
   if row:
    if row['pins']!=pins:raise CheckpointBlocked('UNIT_INPUT_IDENTITY_DRIFT')
    if row['status']=='ALLOCATING':raise CheckpointBlocked('ALLOCATION_UNKNOWN_RECONCILE_NO_DUPLICATE_CREATE')
    return dict(row)
   row={'pins':pins,'status':'ALLOCATING','formal':None,'plan':None,'result':None}
   state['units'][key]=row;state['history'].append({'event':'ALLOCATION_INTENT','key':key,'pins_sha256':digest(pins)})
   return None
 def bind(self,key,formal):
  required={'kb_id','document_id','version_id','job_id','source_sha256','source_document_id'}
  if set(formal)!=required or any(not v for v in formal.values()):raise CheckpointBlocked('EXACT_FORMAL_BINDING_REQUIRED')
  with self.transaction() as state:
   row=state['units'][key]
   if row['status']!='ALLOCATING' or row['formal'] is not None:raise CheckpointBlocked('FORMAL_BINDING_ALREADY_EXISTS')
   if formal['source_sha256']!=row['pins']['source_sha256']:raise CheckpointBlocked('FORMAL_SOURCE_DRIFT')
   row['formal']=formal;row['status']='BOUND';state['history'].append({'event':'BOUND','key':key,'formal':formal})
 def freeze_plan(self,key,plan):
  if plan.get('permit') and plan['permit'].get('execution_kind')!=self.kind:raise CheckpointBlocked('PLAN_CHECKPOINT_EXECUTION_KIND_MISMATCH')
  with self.transaction() as state:
   row=state['units'][key]
   if row['plan'] is not None:
    if row['plan']!=plan:raise CheckpointBlocked('FROZEN_PLAN_DRIFT')
    return
   if row['status']!='BOUND':raise CheckpointBlocked('BOUND_UNIT_REQUIRED')
   row['plan']=plan;row['status']='PLANNED';state['history'].append({'event':'PLANNED','key':key,'sha256':digest(plan)})
 def begin_dispatch(self,key,*,native_claims):
  with self.transaction() as state:
   row=state['units'][key]
   if row['status']=='READY':return False
   if row['status']!='PLANNED' or any(c is not None for c in native_claims):
    raise CheckpointBlocked('CONSUMED_PENDING_UNKNOWN_OR_PREVIOUS_DISPATCH_NO_REPLAY')
   row['status']='DISPATCHING';state['history'].append({'event':'DISPATCH_INTENT','key':key})
   return True
 def ready(self,key,receipt):
  if receipt.get('status')!='READY' or not receipt.get('identity_sha256') or receipt.get('execution_kind')!=self.kind:raise CheckpointBlocked('INDEPENDENT_READY_RECEIPT_REQUIRED')
  with self.transaction() as state:
   row=state['units'][key]
   if row['status']=='READY':
    if row['result']!=receipt:raise CheckpointBlocked('READY_RECEIPT_DRIFT')
    return
   if row['status'] not in ('DISPATCHING','PLANNED'):raise CheckpointBlocked('READY_NOT_A_NEW_OR_RECOVERABLE_PLAN')
   row['status']='READY';row['result']=receipt;state['history'].append({'event':'READY','key':key,'identity_sha256':receipt['identity_sha256']})
 def recover_succeeded(self,key,*,native_claims,receipt,validate_saved_results):
  with self.transaction() as state:
   row=state['units'][key]
   if row['status']=='READY':return False
   if row['status']!='DISPATCHING':
    raise CheckpointBlocked('RECOVERY_NOT_EXACT_SUCCEEDED_NO_REPLAY')
   require_native_success(self.kind,native_claims)
   validate_saved_results()
   if receipt.get('status')!='READY' or not receipt.get('identity_sha256') or receipt.get('execution_kind')!=self.kind:raise CheckpointBlocked('READY_RECEIPT_REQUIRED')
   row['status']='READY';row['result']=receipt;state['history'].append({'event':'RECOVERED_WITHOUT_SEND','key':key})
   return True
 def adopt_historical_ready(self,key,*,pins,formal,receipt,validate_identity):
  validate_identity()
  if receipt.get('status')!='READY' or not receipt.get('identity_sha256') or receipt.get('execution_kind')!=self.kind:raise CheckpointBlocked('HISTORICAL_READY_PROOF_REQUIRED')
  with self.transaction() as state:
   row={'pins':pins,'status':'READY','formal':formal,'plan':None,'result':receipt,'historical_reuse':True}
   if key in state['units'] and state['units'][key]!=row:raise CheckpointBlocked('HISTORICAL_REUSE_CONFLICT')
   if formal['source_sha256']!=pins['source_sha256']:raise CheckpointBlocked('HISTORICAL_SOURCE_DRIFT')
   state['units'][key]=row;state['history'].append({'event':'HISTORICAL_READY_REUSED_NO_SEND','key':key})

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import hashlib
import importlib.util
import json
import sys

@dataclass(frozen=True)
class BoundPackageJob:
    dataset_root: str
    unit_id: str
    package_manifest_sha256: str
    source_document_id: str
    job_id: str
    document_id: str
    version_id: str
    source_sha256: str
    prepared_input: object

def load_pinned_loader(*, path, sha256):
    path=Path(path)
    if not path.is_absolute() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=sha256:
        raise ValueError('EXACT_PACKAGE_LOADER_PIN_REQUIRED')
    name='_exact_v6_transport_loader_'+sha256
    if name not in sys.modules:
        spec=importlib.util.spec_from_file_location(name,path)
        module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return sys.modules[name]

def load_package_input(*, loader, dataset_root, unit_id, package_manifest_sha256):
    descriptor,original=loader.load_original_source(dataset_root,unit_id,expected_manifest_sha256=package_manifest_sha256)
    prepared=None
    if descriptor['input_kind']=='prepared_input':
        same,again,prepared=loader.load_prepared_input(dataset_root,unit_id,expected_manifest_sha256=package_manifest_sha256)
        if same!=descriptor or again!=original:raise ValueError('PACKAGE_SOURCE_VIEW_CHANGED')
    elif descriptor['input_kind']!='original_sources':
        raise ValueError('EXPLICIT_RAW_OR_PREPARED_PACKAGE_KIND_REQUIRED')
    source=descriptor['source'];reference=source['artifact']
    if len(original)!=reference['bytes'] or hashlib.sha256(original).hexdigest()!=reference['sha256']:
        raise ValueError('PACKAGE_ORIGINAL_SOURCE_PIN_MISMATCH')
    return descriptor,original,prepared

def bind_package_job(*, store, loader, dataset_root, unit_id, package_manifest_sha256, kb_id, guard):
    from sqlalchemy import text
    from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
    from backend.app.domain.version_source import VersionSource
    from backend.app.ports.prepared_ingestion import validate_prepared_input
    if type(store) is not PostgresKnowledgeRepository or not callable(guard):
        raise ValueError('NORMAL_PACKAGE_WRITER_AND_GUARD_REQUIRED')
    guard()
    descriptor,original,prepared=load_package_input(loader=loader,dataset_root=dataset_root,
        unit_id=unit_id,package_manifest_sha256=package_manifest_sha256)
    source=descriptor['source'];reference=source['artifact']
    guard()
    stored=store.storage.put_stream(BytesIO(original))
    if stored.sha256!=reference['sha256'] or stored.size!=reference['bytes']:
        raise ValueError('NORMAL_CAS_SOURCE_PIN_MISMATCH')
    actual=store.create_upload(kb_id,source['file_name'],source['media_type'],stored,duplicate_policy='skip')
    if not actual.get('job_id'):
        # READY duplicates need an explicit existing-index receipt; never allocate
        # a replacement/reindex automatically or pretend they were consumed now.
        raise ValueError('EXISTING_DUPLICATE_REQUIRES_EXPLICIT_READY_REUSE')
    with store.engine.connect() as connection:
        version=dict(connection.execute(text('SELECT v.*,d.knowledge_base_id::text AS knowledge_base_id FROM document_versions v JOIN documents d ON d.id=v.document_id WHERE v.id=:id'),{'id':actual['version_id']}).mappings().one())
        job=dict(connection.execute(text('SELECT id::text,version_id::text,status,attempts FROM ingestion_jobs WHERE id=:id'),{'id':actual['job_id']}).mappings().one())
    if (version['knowledge_base_id']!=kb_id or str(version['document_id'])!=actual['document_id']
            or job['version_id']!=actual['version_id'] or job['status']!='queued' or job['attempts']!=0):
        raise ValueError('EXACT_NORMAL_NEW_JOB_DOCUMENT_VERSION_REQUIRED')
    formal=VersionSource.from_row(version)
    if formal.source_sha256!=reference['sha256'] or formal.original_size!=reference['bytes']:
        raise ValueError('FORMAL_VERSION_SOURCE_PIN_MISMATCH')
    if prepared is not None:
        # This normal port already binds the pinned native draft to formal IDs.
        document,chunks=validate_prepared_input(prepared,store.storage,formal,store.chunking_config)
        if document.document_id!=actual['document_id'] or document.version_id!=actual['version_id']:
            raise ValueError('NORMAL_PREPARED_FORMAL_IDS_REQUIRED')
    guard()
    return BoundPackageJob(str(Path(dataset_root).resolve()),unit_id,package_manifest_sha256,
        source['source_document_id'],actual['job_id'],actual['document_id'],actual['version_id'],reference['sha256'],prepared)

def process_bound_package_job(*, store, bound, worker_id, claim_token, lease_seconds, guard):
    from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
    from backend.app.adapters.models.cloud import SiliconFlowEmbedding
    from backend.app.ports.xing_zero_reservation import SiliconFlowFreeBinding
    if type(store) is not PostgresKnowledgeRepository or type(bound) is not BoundPackageJob or not callable(guard):
        raise ValueError('EXACT_NORMAL_PACKAGE_JOB_REQUIRED')
    provider=store.embedding_provider
    binding=getattr(provider,'free_binding',None)
    if type(provider) is not SiliconFlowEmbedding or type(binding) is not SiliconFlowFreeBinding:
        raise ValueError('REAL_NORMAL_INDEX_EMBEDDING_BINDING_REQUIRED')
    if (binding.permit.role!='embedding' or not binding.permit.calls or any(
        call.purpose!='index_embedding' or call.arm!='INDEXING'
        or not call.document_version_ids
        or any(version_id!=bound.version_id for version_id in call.document_version_ids)
        for call in binding.permit.calls)):
        raise ValueError('EXACT_BOUND_VERSION_INDEX_PLAN_REQUIRED')
    guard()
    # No alternate parser/indexer: this is the existing guarded normal job API.
    return store.process_job(bound.job_id,worker_id=worker_id,claim_token=claim_token,
        lease_seconds=lease_seconds,prepared_input=bound.prepared_input)

from dataclasses import dataclass,asdict,replace
from datetime import datetime,timezone,timedelta
from pathlib import Path
import hashlib,json,uuid

def normal_store(store):
 from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
 if type(store) is not PostgresKnowledgeRepository:raise CheckpointBlocked('EXACT_NORMAL_REPOSITORY_REQUIRED')

def read_bound_source(store,bound):
 from sqlalchemy import text
 from backend.app.domain.version_source import VersionSource
 normal_store(store)
 with store.engine.connect() as c:
  row=dict(c.execute(text('SELECT v.*,d.knowledge_base_id::text AS knowledge_base_id FROM document_versions v JOIN documents d ON d.id=v.document_id WHERE v.id=:id'),{'id':bound.version_id}).mappings().one())
 if (str(row['document_id'])!=bound.document_id or row['source_sha256']!=bound.source_sha256):raise CheckpointBlocked('PERSISTED_FORMAL_SOURCE_DRIFT')
 original=store.storage.path_for(row['storage_key'])
 if original.stat().st_size!=row['original_size'] or hashlib.sha256(original.read_bytes()).hexdigest()!=bound.source_sha256:
  raise CheckpointBlocked('NORMAL_CAS_IMMUTABLE_SOURCE_DRIFT')
 return VersionSource.from_row(row),row['knowledge_base_id']

@dataclass(frozen=True)
class FrozenIndexPlan:
 bound: BoundPackageJob
 scope: object
 qid: str
 permit: object
 native_plan: object
 input_identity_sha256: str
 normalized_sha256: str
 lease_sha256: str
 expected_index: dict
 def evidence(self):
  p=asdict(self.permit);p['scope']={'knowledge_base_ids':sorted(self.scope.knowledge_base_ids),'document_ids':sorted(self.scope.document_ids)}
  value=dict(formal={k:getattr(self.bound,k) for k in ('unit_id','source_document_id','job_id','document_id','version_id','source_sha256')},
              qid=self.qid,permit=p,native_plan=json.loads(self.native_plan.canonical_json),native_plan_sha256=self.native_plan.fingerprint,
              input_identity_sha256=self.input_identity_sha256,normalized_sha256=self.normalized_sha256,lease_sha256=self.lease_sha256,expected_index=self.expected_index)
  return json.loads(canonical(value))

def plan_bound_index(*,store,bound,prototype,run_id,grant_id,source_manifest_sha256,authority_manifest_sha256,lease,guard,execution_kind='REAL'):
 from backend.app.domain.scope import Scope
 from backend.app.domain.adaptive_chunking import prepare_document
 from backend.app.ports.ingestion import parse_version_source
 from backend.app.ports.prepared_ingestion import validate_prepared_input
 from backend.app.adapters.models.cloud import SiliconFlowEmbedding
 from backend.app.ports.xing_zero_reservation import FreeModelPlannedCall,SiliconFlowFreePermit,SiliconFlowZeroPriceContract,free_dispatch_identity_sha256,free_dispatch_contract
 from eval_center.paired_state import ProviderBatchCall,ProviderBatchPlan,stable_provider_request_id
 normal_store(store);guard();lease.validate()
 if type(prototype) is not SiliconFlowEmbedding or prototype.free_binding is not None:raise CheckpointBlocked('NORMAL_UNBOUND_EMBEDDING_PLANNER_REQUIRED')
 source,kb=read_bound_source(store,bound)
 if bound.prepared_input is None:
  document=parse_version_source(store.parsers,store.storage,source);prepared=prepare_document(document,store.chunking_config)
 else:document,prepared=validate_prepared_input(bound.prepared_input,store.storage,source,store.chunking_config)
 children=prepared.children
 if not children:raise CheckpointBlocked('NORMAL_CHILDREN_REQUIRED')
 # The normal repository consumes IDs from the frozen permit; its unbound
 # _planned_index_chunk_ids returns (). Allocate deterministic caller-owned
 # UUIDs once here, as the existing first real indexing prep allocated IDs.
 ids=tuple(str(uuid.uuid5(uuid.NAMESPACE_URL,f'rag-v6-index-child:{bound.version_id}:{bound.job_id}:{n}:{hashlib.sha256(c.embedding_content.encode()).hexdigest()}')) for n,c in enumerate(children))
 batch_size=min(32,prototype.admission.max_batch_items)
 if type(batch_size) is not int or batch_size<=0:raise CheckpointBlocked('REAL_BATCH_ADMISSION_REQUIRED')
 scope=Scope.from_ids([kb],[bound.document_id]);qid='index-doc-'+bound.document_id;total=(len(children)+batch_size-1)//batch_size;calls=[]
 for number,start in enumerate(range(0,len(children),batch_size)):
  guard();inputs=[c.embedding_content for c in children[start:start+batch_size]]
  count=prototype.admission.count(inputs)
  # Use the exact normal request serializer, only digest request.data. Headers
  # (including credentials) are never persisted or printed by this module.
  request=prototype._prepare_request('/embeddings',{'model':prototype.spec.model_id,'input':inputs,'encoding_format':'float'})
  ordinal=number+1
  calls.append(FreeModelPlannedCall(stable_provider_request_id(run_id=run_id,qid=qid,arm='INDEXING',purpose='index_embedding',call_ordinal=ordinal),
      qid,'INDEXING','index_embedding',f'{bound.version_id}:{bound.job_id}:{number}:{total}',prototype.input_profile_sha256,hashlib.sha256(request.data).hexdigest(),
      count,0,len(inputs),60,ordinal,(bound.version_id,)*len(inputs),ids[start:start+batch_size],tuple(hashlib.sha256(t.encode()).hexdigest() for t in inputs)))
 permit=SiliconFlowFreePermit(grant_id,run_id,scope,None,source_manifest_sha256,authority_manifest_sha256,prototype.spec.model_id,'embedding',
     SiliconFlowZeroPriceContract(prototype.spec.model_id),tuple(calls),sum(c.input_token_bound for c in calls),'0'*64,execution_kind=execution_kind,
     owner_activity_id='owner-v6-public-rag-r3',execution_lease=lease)
 permit=replace(permit,frozen_plan_sha256=permit.fingerprint);permit.validate(prototype.spec)
 native=tuple(ProviderBatchCall(c.call_ordinal,c.document_version_ids,c.chunk_ids,c.input_sha256,c.wire_sha256,free_dispatch_identity_sha256(permit,c)) for c in calls)
 plan=ProviderBatchPlan('index_embedding','siliconflow',permit.model,prototype.identity.fingerprint,hashlib.sha256(store.chunking_config.identity.encode()).hexdigest(),free_dispatch_contract(permit,calls[0])['scope_sha256'],native);plan.validate()
 lease_pin=digest(asdict(lease));identity=digest(dict(job_id=bound.job_id,version_id=bound.version_id,source_sha256=bound.source_sha256,native_plan_sha256=plan.fingerprint,lease_sha256=lease_pin))
 expected=dict(parser_version=document.parser_version,chunker_version=__import__('backend.app.domain.adaptive_chunking',fromlist=['CHUNKER_VERSION']).CHUNKER_VERSION,
     index_identity=store.chunking_config.identity,normalized_content_sha256=document.content_sha256,
     profile_fingerprint=prototype.identity.fingerprint,dimension=prototype.identity.dimension,
     children=[dict(id=i,content_sha256=c.content_sha256,embedding_input_sha256=hashlib.sha256(c.embedding_content.encode()).hexdigest()) for i,c in zip(ids,children)])
 guard();return FrozenIndexPlan(bound,scope,qid,permit,plan,identity,document.content_sha256,lease_pin,expected)

def register_index_run(*,native_state,plans,cohort_sha256,source_manifest_sha256,code_sha,guard):
 from eval_center.paired_state import register_run
 if not plans:raise CheckpointBlocked('EXACT_INDEX_PLANS_REQUIRED')
 first=plans[0];lease=first.permit.execution_lease
 if any(p.permit.run_id!=first.permit.run_id or p.permit.execution_lease is not lease for p in plans):raise CheckpointBlocked('ONE_NATIVE_RUN_AND_LEASE_REQUIRED')
 guard();register_run(native_state,run_id=first.permit.run_id,cohort_sha256=cohort_sha256,source_manifest_sha256=source_manifest_sha256,code_sha=code_sha,
     deadline_utc=lease.deadline_utc,dispatch_deadline_utc=(lease.deadline-timedelta(seconds=120)).isoformat(),round_deadline_utc=lease.deadline_utc,
     expected_qids=[p.qid for p in plans],execution_purpose='INDEXING')

def execute_planned_index(*,checkpoint,key,store,plan,native_state,budget_gate,result_storage,admission,guard,worker_id,lease_seconds=180,simulation_sender=None):
 from backend.app.adapters.models.cloud import SiliconFlowAuthorizedTransport
 from backend.app.adapters.models.factory import ProviderFactory
 from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
 from backend.app.application.provider_usage import BudgetUsageGuard
 from backend.app.domain.model_registry import ModelRegistry
 from backend.app.ports.xing_zero_reservation import SiliconFlowFreeBinding,FreeModelRequestContext,free_model_requests
 from eval_center.paired_state import provider_send_claim_record,begin_provider_batch_scope,SQLiteProviderBatchSendClaims,finish_attempt,provider_send_claim_scope
 from backend.app.adapters.models.cloud import EmbeddingAdmission,SimulatedXingSend
 from backend.app.application.budget import PostgresBudgetGate,InMemoryBudgetGate
 from backend.app.adapters.storage import ContentAddressedStorage
 normal_store(store);guard()
 if checkpoint.kind!=plan.permit.execution_kind:raise CheckpointBlocked('PLAN_CHECKPOINT_EXECUTION_KIND_MISMATCH')
 simulated=checkpoint.kind==plan.permit.execution_kind=='SIMULATED'
 expected_gate=InMemoryBudgetGate if simulated else PostgresBudgetGate
 if type(admission) is not EmbeddingAdmission or type(budget_gate) is not expected_gate or type(result_storage) is not ContentAddressedStorage:
  raise CheckpointBlocked('EXPLICIT_NORMAL_ADMISSION_BUDGET_RESULT_CAS_REQUIRED')
 if simulated:
  if type(simulation_sender) is not SimulatedXingSend:raise CheckpointBlocked('EXPLICIT_SIMULATED_SENDER_REQUIRED')
 elif checkpoint.kind!='REAL' or plan.permit.execution_kind!='REAL' or simulation_sender is not None:
  raise CheckpointBlocked('REAL_EXECUTION_NOT_A_SIMULATION_FIXTURE')
 checkpoint.freeze_plan(key,plan.evidence())
 before=[provider_send_claim_record(native_state,request_id=c.request_id) for c in plan.permit.calls]
 if not checkpoint.begin_dispatch(key,native_claims=before):return {'status':'REUSE_READY_NO_SEND'}
 begin_provider_batch_scope(native_state,run_id=plan.permit.run_id,qid=plan.qid,input_identity_sha256=plan.input_identity_sha256)
 claim=store.claim_job(worker_id=worker_id,lease_seconds=lease_seconds,job_id=plan.bound.job_id)
 if not claim:raise CheckpointBlocked('NORMAL_JOB_CLAIM_REQUIRED_NO_REPLAY')
 def lease_guard(pin):
  guard();plan.permit.execution_lease.validate()
  if pin!=plan.lease_sha256 or datetime.now(timezone.utc)>=plan.permit.execution_lease.deadline-timedelta(seconds=120):raise CheckpointBlocked('INDEX_FINITE_LEASE_EXPIRED')
  if store.renew_job(plan.bound.job_id,worker_id=worker_id,claim_token=claim['claim_token'],lease_seconds=lease_seconds) is not True:raise CheckpointBlocked('NORMAL_JOB_FENCE_LOST')
 callback=SQLiteProviderBatchSendClaims(native_state,plan.permit.run_id,plan.qid,'INDEXING',plan.input_identity_sha256,plan.native_plan,plan.lease_sha256,lease_guard)
 def clock():lease_guard(plan.lease_sha256);return datetime.now(timezone.utc)
 # Admission/identity was measured by the pinned normal unbound prototype.
 transport=SiliconFlowAuthorizedTransport(plan.permit,send_claim_callback=callback,clock=clock,**({'sender':simulation_sender} if simulated else {}))
 binding=SiliconFlowFreeBinding(plan.permit,transport,admission,result_storage)
 registry=ModelRegistry.frozen_defaults();usage=BudgetUsageGuard(budget_gate,registry.select('embedding'),0,zero_price_permit=plan.permit)
 provider=ProviderFactory(registry,enabled_roles=('embedding',),usage_guards={'embedding':usage},embedding_admission=admission,
     chunking_config=store.chunking_config,free_model_bindings={'embedding':binding}).build('embedding')
 writer=PostgresKnowledgeRepository(store.engine,store.storage,parsers=store.parsers,embedding_provider=provider,max_chunk_chars=store.max_chunk_chars,chunk_overlap=store.chunk_overlap)
 writer.chunking_config=store.chunking_config
 writer.event_sink=store.event_sink
 contexts=tuple(FreeModelRequestContext(plan.permit.run_id,c.request_id,'index_embedding',c.batch_id,plan.scope,plan.native_plan) for c in plan.permit.calls)
 # The normal provider owns atomic budget reservation, claims, transport, result
 # CAS and settlement. This layer neither sends HTTP nor modifies those guards.
 try:
  with provider_send_claim_scope(callback),free_model_requests(contexts):
   job=process_bound_package_job(store=writer,bound=plan.bound,worker_id=worker_id,claim_token=claim['claim_token'],lease_seconds=lease_seconds,guard=guard)
  children=[provider_send_claim_record(native_state,request_id=c.request_id) for c in plan.permit.calls]
  if job.get('status')!='succeeded':raise CheckpointBlocked('EXACT_JOB_SUCCESS_REQUIRED')
  require_native_success(checkpoint.kind,children)
  # Normal CAS reader verifies each saved provider artifact before READY reuse.
  for call,child in zip(plan.permit.calls,children):read_bound_native_result(binding,child,call)
  receipt=observe_ready_index(store=writer,bound=plan.bound,expected_child_ids=tuple(c for call in plan.permit.calls for c in call.chunk_ids),expected_profile=provider.identity.fingerprint,expected_manifest=plan.expected_index,execution_kind=checkpoint.kind)
  finish_attempt(native_state,run_id=plan.permit.run_id,qid=plan.qid,arm='INDEXING',status=native_success_status(checkpoint.kind),result=receipt);checkpoint.ready(key,receipt)
  return receipt
 except Exception:
  # Durable DISPATCHING and native receipts remain; automatic retry is denied.
  raise


DATASETS = {'scifact': (200, 'original_sources'), 'miracl-zh': (400, 'original_sources'),
            'longbench-zh': (48, 'original_sources'), 'hotpotqa': (73, 'prepared_input'),
            'mmlongbench-doc': (3, 'prepared_input'), 'omnidocbench': (24, 'prepared_input')}


@dataclass(frozen=True)
class FrozenPackageIdentity:
    """Pins from the coordinator's independently reviewed record, not the package."""
    manifest_sha256: str
    loader_sha256: str
    dataset_manifest_sha256: tuple
    review_id: str
    review_record_sha256: str

    def validate(self):
        import re
        hashes = [self.manifest_sha256, self.loader_sha256, self.review_record_sha256]
        pins = dict(self.dataset_manifest_sha256)
        if (not self.review_id or len(self.dataset_manifest_sha256) != 6
                or set(pins) != set(DATASETS)
                or any(not re.fullmatch('[0-9a-f]{64}', h) for h in hashes + list(pins.values()))):
            raise CheckpointBlocked('INDEPENDENT_REVIEWED_PACKAGE_PINS_REQUIRED')

    @property
    def fingerprint(self):
        self.validate()
        return digest(asdict(self))


def load_reviewed_package_identity(*, review_path, expected_review_sha256):
    """Expected review SHA is an independent coordinator input; no self-hash trust."""
    path = Path(review_path)
    if path.is_symlink():
        raise CheckpointBlocked('PACKAGE_REVIEW_SYMLINK_DENIED')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_review_sha256:
        raise CheckpointBlocked('PACKAGE_REVIEW_RECORD_DRIFT')
    record = json.loads(raw)
    if record.get('schema') != 'v6-reviewed-package-identity/v1':
        raise CheckpointBlocked('PACKAGE_REVIEW_RECORD_SCHEMA_INVALID')
    identity = FrozenPackageIdentity(record['manifest_sha256'], record['loader_sha256'],
        tuple(sorted(record['dataset_manifest_sha256'].items())), record['review_id'], expected_review_sha256)
    identity.validate()
    return identity


def validate_frozen_package(*, package_root, package_identity):
    if type(package_identity) is not FrozenPackageIdentity:
        raise CheckpointBlocked('TYPED_FROZEN_PACKAGE_IDENTITY_REQUIRED')
    package_identity.validate()
    root = Path(package_root)
    raw = (root / 'six-package-manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != package_identity.manifest_sha256:
        raise CheckpointBlocked('SIX_PACKAGE_MANIFEST_DRIFT')
    if hashlib.sha256((root / 'package_loader.py').read_bytes()).hexdigest() != package_identity.loader_sha256:
        raise CheckpointBlocked('PACKAGE_LOADER_DRIFT')
    index = json.loads(raw)
    packages = index.get('packages', [])
    if (index.get('schema') != 'rag-v6-final-six-pack-index/v1' or len(packages) != 6
            or {p['dataset'] for p in packages} != set(DATASETS)):
        raise CheckpointBlocked('EXACT_SIX_DATASETS_REQUIRED')
    expected = dict(package_identity.dataset_manifest_sha256)
    for p in packages:
        if p['manifest']['sha256'] != expected[p['dataset']]:
            raise CheckpointBlocked('INDEPENDENT_DATASET_PIN_DRIFT')
        if (p['units'], p['input_kind']) != DATASETS[p['dataset']]:
            raise CheckpointBlocked('FROZEN_SOURCE_COUNT_OR_KIND_DRIFT')
    return index


@dataclass(frozen=True)
class SourceUnit:
    dataset: str
    dataset_root: str
    unit_id: str
    manifest_sha256: str
    source_document_id: str
    source_sha256: str
    input_kind: str
    kb_id: str
    file_name: str
    media_type: str

    @property
    def key(self):
        return canonical([self.dataset, self.unit_id, self.source_document_id]).decode()

    def pins(self):
        return asdict(self)


def six_package_units(*, package_root, package_identity, dataset_roots, kb_ids):
    """Read only pinned source descriptors; never inspect/select by Gold."""
    root = Path(package_root)
    index = validate_frozen_package(package_root=root, package_identity=package_identity)
    if index.get('schema') != 'rag-v6-final-six-pack-index/v1':
        raise CheckpointBlocked('SIX_PACKAGE_SCHEMA_INVALID')
    packages = index['packages']
    if len(packages) != 6 or {p['dataset'] for p in packages} != set(DATASETS):
        raise CheckpointBlocked('EXACT_SIX_DATASETS_REQUIRED')
    if set(dataset_roots) != set(DATASETS) or set(kb_ids) != set(DATASETS):
        raise CheckpointBlocked('EXPLICIT_SIX_ROOTS_AND_KB_IDS_REQUIRED')
    units = []
    upload_names = set()
    for p in packages:
        dataset = p['dataset']
        expected_count, kind = DATASETS[dataset]
        # No extraction/upload here. ECS caller supplies already verified roots.
        manifest = Path(dataset_roots[dataset]) / 'manifest.json'
        raw = manifest.read_bytes()
        if hashlib.sha256(raw).hexdigest() != p['manifest']['sha256']:
            raise CheckpointBlocked('DATASET_MANIFEST_DRIFT')
        m = json.loads(raw)
        if (m['schema'] != 'rag-v6-file-transport/v1' or m['dataset'] != dataset
                or m.get('Gold_qrels_answers_included') is not False
                or len(m['units']) != expected_count or p['units'] != expected_count
                or p['input_kind'] != kind or not kb_ids[dataset]):
            raise CheckpointBlocked('DATASET_SOURCE_COUNT_OR_KIND_DRIFT')
        seen_ids, seen_sources = set(), set()
        for e in m['units']:
            source = e['source']
            if (e['input_kind'] != kind or e['unit_id'] in seen_ids
                    or source['source_document_id'] in seen_sources):
                raise CheckpointBlocked('SOURCE_UNIT_AMBIGUOUS')
            seen_ids.add(e['unit_id']); seen_sources.add(source['source_document_id'])
            upload_name = (kb_ids[dataset], source['file_name'])
            if upload_name in upload_names:
                raise CheckpointBlocked('NORMAL_UPLOAD_FILENAME_COLLISION')
            upload_names.add(upload_name)
            units.append(SourceUnit(dataset, str(Path(dataset_roots[dataset]).resolve()), e['unit_id'],
                p['manifest']['sha256'], source['source_document_id'], source['artifact']['sha256'], kind, kb_ids[dataset],
                source['file_name'], source['media_type']))
    return tuple(units)


def observe_ready_index(*, store, bound, expected_child_ids, expected_profile, expected_manifest, execution_kind):
    """Independent CAS/SQL readback, including quote, exact Child set and inputs."""
    from sqlalchemy import text
    from backend.app.domain.models import SourceLocator
    native_success_status(execution_kind)
    normal_store(store)
    source, kb = read_bound_source(store, bound)
    with store.engine.connect() as c:
        v = dict(c.execute(text('SELECT v.*,d.active_version_id::text,j.status AS job_status '
            'FROM document_versions v JOIN documents d ON d.id=v.document_id '
            'JOIN ingestion_jobs j ON j.version_id=v.id WHERE v.id=:version AND j.id=:job '
            'AND d.deleted_at IS NULL'), {'version': bound.version_id, 'job': bound.job_id}).mappings().one())
        rows = [dict(r) for r in c.execute(text("SELECT c.id::text,c.content_sha256,c.content,c.context_header,"
            "c.locator AS source_locator,ce.embedding::text AS vector,ep.fingerprint,vector_dims(ce.embedding) AS dims,"
            "(SELECT count(*) FROM chunk_terms ct WHERE ct.chunk_id=c.id) AS terms "
            "FROM chunks c JOIN chunk_embeddings ce ON ce.chunk_id=c.id JOIN embedding_profiles ep ON ep.id=ce.profile_id "
            "WHERE c.version_id=:version AND c.chunk_role='child' ORDER BY c.id"), {'version': bound.version_id}).mappings()]
    if (v['index_status'], v['active_version_id'], v['job_status']) != ('ready', bound.version_id, 'succeeded'):
        raise CheckpointBlocked('CURRENT_VERSION_READY_AND_JOB_SUCCESS_REQUIRED')
    expected = {row['id']: row for row in expected_manifest['children']}
    if (set(expected) != set(expected_child_ids) or len(expected) != len(expected_child_ids)
            or {r['id'] for r in rows} != set(expected) or len(rows) != len(expected)):
        raise CheckpointBlocked('EXACT_READY_CHILD_SET_REQUIRED')
    for field in ('parser_version', 'chunker_version', 'index_identity', 'normalized_content_sha256'):
        if v[field] != expected_manifest[field]:
            raise CheckpointBlocked('READY_MANIFEST_DRIFT:' + field)
    normalized = store.storage.path_for(v['normalized_content_key']).read_bytes()
    manifest = store.storage.path_for(v['processing_manifest_key']).read_bytes()
    if (hashlib.sha256(normalized).hexdigest() != v['normalized_content_sha256']
            or hashlib.sha256(manifest).hexdigest() != v['processing_manifest_sha256']):
        raise CheckpointBlocked('READY_CAS_ARTIFACT_DRIFT')
    processing = json.loads(manifest)
    if any(processing[k] != val for k, val in dict(document_id=bound.document_id,
            version_id=bound.version_id, index_identity=expected_manifest['index_identity'],
            parser_version=expected_manifest['parser_version'], chunker_version=expected_manifest['chunker_version']).items()):
        raise CheckpointBlocked('PROCESSING_MANIFEST_IDENTITY_DRIFT')
    content = normalized.decode('utf-8')
    children = []
    for r in rows:
        loc = SourceLocator.model_validate(r['source_locator'])
        body = r['content'].strip()
        embedded = (r['context_header'] + '\n\n' + body) if r['context_header'] else body
        if (r['dims'] != expected_manifest['dimension'] or r['fingerprint'] != expected_profile
                or expected_profile != expected_manifest['profile_fingerprint']
                or hashlib.sha256(r['content'].encode()).hexdigest() != r['content_sha256']
                or r['content_sha256'] != expected[r['id']]['content_sha256']
                or hashlib.sha256(embedded.encode()).hexdigest() != expected[r['id']]['embedding_input_sha256']
                or loc.start is None or loc.end is None or not 0 <= loc.start < loc.end <= len(content)
                or loc.quote != r['content'] or content[loc.start:loc.end] != r['content']):
            raise CheckpointBlocked('READY_PROFILE_CONTENT_OR_QUOTE_DRIFT')
        children.append(dict(id=r['id'], content_sha256=r['content_sha256'],
            embedding_input_sha256=expected[r['id']]['embedding_input_sha256'],
            vector_sha256=hashlib.sha256(r['vector'].encode()).hexdigest(), terms=r['terms']))
    proof = dict(execution_kind=execution_kind,kb_id=kb, document_id=bound.document_id, version_id=bound.version_id,
        job_id=bound.job_id, source_sha256=source.source_sha256, source_document_id=bound.source_document_id,
        expected_index=expected_manifest, children=children, processing_manifest_sha256=v['processing_manifest_sha256'])
    return dict(status='READY',execution_kind=execution_kind,identity_sha256=digest(proof),actual=proof)


class BatchOperator:
    """Sequential ingestion only; caller owns ECS authority and downstream runtime.

    guard MUST revalidate reviewed release/source, epoch1/local fence and lease.
    Production dependency construction is deliberately outside this adapter.
    Native claims/budget/CAS settlement stay in their existing implementations.
    """
    def __init__(self, *, checkpoint, store, loader, prototype, native_state,
                 budget_gate, result_storage, lease, source_manifest_sha256,
                 authority_manifest_sha256, code_sha, package_root, package_identity, guard, clock=None):
        self.checkpoint, self.store, self.loader = checkpoint, store, loader
        self.prototype, self.native_state = prototype, native_state
        self.budget_gate, self.result_storage, self.lease = budget_gate, result_storage, lease
        self.source_manifest_sha256, self.authority_manifest_sha256 = source_manifest_sha256, authority_manifest_sha256
        self.code_sha, self.external_guard = code_sha, guard
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        if not callable(guard) or checkpoint.kind != 'REAL':
            raise CheckpointBlocked('REVIEWED_REAL_ECS_DEPENDENCIES_REQUIRED')
        validate_frozen_package(package_root=package_root, package_identity=package_identity)
        self.package_root, self.package_identity = str(Path(package_root).resolve()), package_identity
        if checkpoint.identity.get('package_identity_sha256') != package_identity.fingerprint:
            raise CheckpointBlocked('CHECKPOINT_REVIEWED_PACKAGE_IDENTITY_DRIFT')
        loader_path = Path(getattr(loader, '__file__', ''))
        if (not loader_path.is_file() or loader_path.resolve() != (Path(package_root) / 'package_loader.py').resolve()
                or hashlib.sha256(loader_path.read_bytes()).hexdigest() != package_identity.loader_sha256):
            raise CheckpointBlocked('EXACT_FROZEN_PACKAGE_LOADER_REQUIRED')

    def guard(self):
        if os.name == 'nt':
            raise CheckpointBlocked('LOCAL_FENCED_NO_REAL_EXECUTION')
        self.external_guard()
        self.lease.validate()
        now = utc(self.clock())
        if not self.lease.not_before <= now < self.lease.deadline - timedelta(seconds=120):
            raise CheckpointBlocked('FINITE_LEASE_DISPATCH_STOP')
        batch = self.checkpoint.snapshot()['batch']
        if batch and now >= utc(batch['deadline_utc']):
            raise CheckpointBlocked('BATCH_5H_DEADLINE_REACHED')

    def _bound(self, unit, row, prepared):
        f = row['formal']
        if f['source_document_id'] != unit.source_document_id or f['source_sha256'] != unit.source_sha256:
            raise CheckpointBlocked('RESUME_FROZEN_SOURCE_ID_DRIFT')
        bound = BoundPackageJob(unit.dataset_root, unit.unit_id, unit.manifest_sha256,
            unit.source_document_id, f['job_id'], f['document_id'], f['version_id'], unit.source_sha256, prepared)
        source, kb = read_bound_source(self.store, bound)
        if (kb != unit.kb_id or f['kb_id'] != kb or source.source_sha256 != unit.source_sha256
                or source.file_name != unit.file_name or source.media_type != unit.media_type):
            raise CheckpointBlocked('RESUME_FORMAL_SOURCE_OR_SCOPE_DRIFT')
        return bound

    def ingest(self, unit, *, phase, batch_id):
        from eval_center.paired_state import provider_send_claim_record
        self.guard()
        row = self.checkpoint.snapshot()['units'].get(unit.key)
        if row and row['pins'] != unit.pins():
            raise CheckpointBlocked('UNIT_INPUT_IDENTITY_DRIFT')
        if row and row['status'] == 'ALLOCATING':
            raise CheckpointBlocked('ALLOCATION_UNKNOWN_RECONCILE_NO_DUPLICATE_CREATE')
        descriptor, original, prepared = load_package_input(loader=self.loader, dataset_root=unit.dataset_root,
            unit_id=unit.unit_id, package_manifest_sha256=unit.manifest_sha256)
        if (descriptor['source']['source_document_id'] != unit.source_document_id
                or descriptor['source']['artifact']['sha256'] != unit.source_sha256
                or descriptor['input_kind'] != unit.input_kind
                or descriptor['source']['file_name'] != unit.file_name
                or descriptor['source']['media_type'] != unit.media_type):
            raise CheckpointBlocked('FROZEN_UNIT_SOURCE_DRIFT')
        # Includes reused/bound SETUP sources when entering the formal BATCH.
        self.checkpoint.start_batch(phase=phase, now=self.clock(), approved_batch_id=batch_id, guard=self.guard)
        if row is None:
            # First formal bulk mutation, including CAS, is after durable T0.
            self.checkpoint.begin_allocation(unit.key, unit.pins())
            bound = bind_package_job(store=self.store, loader=self.loader, dataset_root=unit.dataset_root,
                unit_id=unit.unit_id, package_manifest_sha256=unit.manifest_sha256, kb_id=unit.kb_id, guard=self.guard)
            self.checkpoint.bind(unit.key, dict(kb_id=unit.kb_id, document_id=bound.document_id,
                version_id=bound.version_id, job_id=bound.job_id, source_sha256=bound.source_sha256,
                source_document_id=bound.source_document_id))
            row = self.checkpoint.snapshot()['units'][unit.key]
        else:
            bound = self._bound(unit, row, prepared)
        if row['status'] in ('READY', 'DISPATCHING'):
            if row['plan'] and row['plan']['permit']['execution_kind'] != self.checkpoint.kind:
                raise CheckpointBlocked('PLAN_CHECKPOINT_EXECUTION_KIND_MISMATCH')
            expected = row['plan']['expected_index'] if row['plan'] else row['result']['actual']['expected_index']
            receipt = observe_ready_index(store=self.store, bound=bound,
                expected_child_ids=tuple(c['id'] for c in expected['children']),
                expected_profile=expected['profile_fingerprint'], expected_manifest=expected,execution_kind=self.checkpoint.kind)
            if row['status'] == 'READY':
                if row['result'] != receipt:
                    raise CheckpointBlocked('READY_RECEIPT_DRIFT')
                return receipt
            # Recovery does not construct a new permit/lease/request or call process_job.
            calls = row['plan']['permit']['calls']
            claims = [provider_send_claim_record(self.native_state, request_id=c['request_id']) for c in calls]
            def validate_saved():
                for call, claim in zip(calls, claims):
                    result = (claim or {}).get('result')
                    if not result:
                        raise CheckpointBlocked('NATIVE_SUCCESS_RESULT_MISSING')
                    from backend.app.ports.xing_zero_reservation import SiliconFlowFreeBinding
                    # CAS receipt ABI belongs to production binding; no result reinterpretation.
                    binding=self._recovery_binding(row)
                    typed_call=next(c for c in binding.permit.calls if c.request_id==call['request_id'])
                    read_bound_native_result(binding,claim,typed_call)
            from eval_center.paired_state import attempts_for_qid, finish_attempt
            permit = row['plan']['permit']
            def verify_and_finish():
                validate_saved()
                attempt = attempts_for_qid(self.native_state, run_id=permit['run_id'], qid=row['plan']['qid'])['INDEXING']
                if attempt['status'] == 'pending':
                    finish_attempt(self.native_state, run_id=permit['run_id'], qid=row['plan']['qid'],
                        arm='INDEXING', status=native_success_status(self.checkpoint.kind), result=receipt)
                elif attempt['status'] != native_success_status(self.checkpoint.kind) or attempt['result'] != receipt:
                    raise CheckpointBlocked('NATIVE_PARENT_TERMINAL_DRIFT')
            self.checkpoint.recover_succeeded(unit.key, native_claims=claims, receipt=receipt, validate_saved_results=verify_and_finish)
            return receipt
        run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, self.checkpoint.identity['run_id'] + ':' + unit.key))
        plan = plan_bound_index(store=self.store, bound=bound, prototype=self.prototype,
            run_id=run_id, grant_id='owner-v6-public-rag-r3:' + run_id,
            source_manifest_sha256=self.source_manifest_sha256, authority_manifest_sha256=self.authority_manifest_sha256,
            lease=self.lease, guard=self.guard,execution_kind=self.checkpoint.kind)
        self.checkpoint.freeze_plan(unit.key, plan.evidence())
        register_index_run(native_state=self.native_state, plans=[plan], cohort_sha256=self.package_identity.manifest_sha256,
            source_manifest_sha256=self.source_manifest_sha256, code_sha=self.code_sha, guard=self.guard)
        return execute_planned_index(checkpoint=self.checkpoint, key=unit.key, store=self.store, plan=plan,
            native_state=self.native_state, budget_gate=self.budget_gate, result_storage=self.result_storage,
            admission=self.prototype.admission, guard=self.guard, worker_id='v6-operator-' + run_id)

    def _recovery_binding(self, row):
        from backend.app.domain.scope import Scope
        from backend.app.application.validation_usd_budget import V6ExecutionLease
        from backend.app.ports.xing_zero_reservation import (SiliconFlowFreeBinding,
            SiliconFlowFreePermit, SiliconFlowZeroPriceContract, FreeModelPlannedCall)
        p = dict(row['plan']['permit'])
        p['scope'] = Scope.from_ids(p['scope']['knowledge_base_ids'], p['scope']['document_ids'])
        p['price_contract'] = SiliconFlowZeroPriceContract(**p['price_contract'])
        p['execution_lease'] = V6ExecutionLease(**p['execution_lease'])
        calls = []
        for c in p['calls']:
            c = dict(c)
            for field in ('document_version_ids', 'chunk_ids', 'input_sha256'):
                c[field] = tuple(c[field])
            calls.append(FreeModelPlannedCall(**c))
        p['calls'] = tuple(calls)
        permit = SiliconFlowFreePermit(**p)
        if permit.execution_kind!=self.checkpoint.kind:
            raise CheckpointBlocked('PLAN_CHECKPOINT_EXECUTION_KIND_MISMATCH')
        if permit.fingerprint != p['frozen_plan_sha256']:
            raise CheckpointBlocked('FROZEN_RECOVERY_PERMIT_DRIFT')
        # Read-only binding: deliberately has no transport; expired old lease is
        # not renewed. Production read_result checks exact CAS receipt identity.
        return SiliconFlowFreeBinding(permit, None, self.prototype.admission, self.result_storage)

    def run(self, *, units, phase, batch_id=None, resume=False):
        if phase not in ('SETUP', 'BATCH') or (phase == 'BATCH' and not batch_id):
            raise CheckpointBlocked('EXPLICIT_PHASE_AND_BATCH_REVIEW_ID_REQUIRED')
        if len(units) != 748 or len({u.key for u in units}) != 748:
            raise CheckpointBlocked('FROZEN_FULL_748_UNIT_INVENTORY_REQUIRED')
        for dataset, (count, kind) in DATASETS.items():
            matching = [u for u in units if u.dataset == dataset]
            if len(matching) != count or any(u.input_kind != kind for u in matching):
                raise CheckpointBlocked('FROZEN_SIX_DATASET_COUNTS_REQUIRED')
        expected_pins=dict(self.package_identity.dataset_manifest_sha256)
        if any(u.manifest_sha256!=expected_pins[u.dataset] for u in units):
            raise CheckpointBlocked('UNIT_INDEPENDENT_PACKAGE_PIN_DRIFT')
        selected = units if phase == 'BATCH' else (
            next(u for u in units if u.input_kind == 'original_sources'),
            next(u for u in units if u.input_kind == 'prepared_input'))
        # Lock spans external calls, not merely checkpoint writes. Crash releases
        # OS lock; durable ALLOCATING/DISPATCHING still forbids blind replay.
        with execution_lock(self.checkpoint.path.with_suffix('.writer.lock')):
            with self.checkpoint.transaction() as frozen:
                pin = digest([u.pins() for u in units])
                if frozen.get('inventory_sha256', pin) != pin:
                    raise CheckpointBlocked('FROZEN_FULL_INVENTORY_DRIFT')
                frozen['inventory_sha256'] = pin
            state = self.checkpoint.snapshot()
            if resume and not state['units']:
                raise CheckpointBlocked('RESUME_REQUIRES_EXISTING_CHECKPOINT')
            if phase == 'BATCH' and state['batch'] and state['batch']['batch_id'] != batch_id:
                raise CheckpointBlocked('BATCH_ID_DRIFT_NO_T0_RESET')
            completed = []
            for unit in selected:
                self.guard()
                try:
                    completed.append(dict(key=unit.key, receipt=self.ingest(unit, phase=phase, batch_id=batch_id)))
                except Exception as exc:
                    # Record failure without clearing ALLOCATING/DISPATCHING or
                    # touching native receipts/reservations. Do not persist
                    # arbitrary provider exception strings or secrets.
                    with self.checkpoint.transaction() as failed:
                        failed.setdefault('failures', []).append(dict(key=unit.key,
                            exception_type=type(exc).__name__, observed_utc=utc(self.clock()).isoformat(),
                            state=(failed['units'].get(unit.key) or {}).get('status', 'UNBOUND')))
                    raise
            inventory = None
            if phase == 'BATCH':
                inventory = freeze_ready_inventory(checkpoint=self.checkpoint, units=units,
                    path=self.checkpoint.path.with_suffix('.ready-inventory.json'),package_identity=self.package_identity,guard=self.guard)
            return dict(status='INGESTION_COMPLETE', phase=phase, units=len(completed), entries=completed,
                batch=self.checkpoint.snapshot()['batch'], inventory=inventory,
                downstream='TWO_STAGE_RUNTIME_AND_SCORING_NOT_RUN')

    def adopt_existing_ready(self, *, unit, formal, expected_index, expected_runtime_manifest,
                             actual_runtime_manifest, database_profile, scope):
        """Explicit historical adoption only after the existing identity validator."""
        from eval_center.verified_index import validate_index_identity
        self.guard()
        validate_index_identity(expected_runtime_manifest, actual_runtime_manifest,
            database_profile=database_profile, scope=scope)
        row = dict(formal=formal)
        bound = self._bound(unit, row, None)
        receipt = observe_ready_index(store=self.store, bound=bound,
            expected_child_ids=tuple(c['id'] for c in expected_index['children']),
            expected_profile=expected_index['profile_fingerprint'], expected_manifest=expected_index,execution_kind=self.checkpoint.kind)
        self.checkpoint.adopt_historical_ready(unit.key, pins=unit.pins(), formal=formal,
            receipt=receipt, validate_identity=self.guard)
        return receipt


def freeze_ready_inventory(*, checkpoint, units, path, package_identity, guard):
    """Persist the full source-to-formal manifest after independent per-unit readback.

    This is not the evaluator's environment/model index manifest. Its existing
    manifest assembler must consume the formal source pool, then invoke the
    existing validator with independently pinned expected/actual environment.
    """
    guard()
    state = checkpoint.snapshot()
    if set(state['units']) != {u.key for u in units}:
        raise CheckpointBlocked('EXACT_FROZEN_READY_UNIT_SET_REQUIRED')
    entries = []
    for unit in units:
        row = state['units'][unit.key]
        if row['status'] != 'READY' or row['pins'] != unit.pins():
            raise CheckpointBlocked('PARTIAL_OR_CHANGED_INDEX_NOT_READY_INVENTORY')
        entries.append(dict(key=unit.key, pins=row['pins'], formal=row['formal'], receipt=row['result']))
    manifest = dict(schema='v6-formal-source-inventory/v1', execution_kind=state['execution_kind'],
        identity=state['identity'], package_manifest_sha256=package_identity.manifest_sha256,
        package_identity_sha256=package_identity.fingerprint,batch=state['batch'],
        units=len(entries), entries=entries, inventory_sha256=digest(entries))
    target = Path(path)
    if target.exists() and json.loads(target.read_bytes()) != manifest:
        raise CheckpointBlocked('READY_INVENTORY_IMMUTABLE_DRIFT')
    atomic_json(target, manifest)
    return manifest


def validate_runtime_index_manifest(*, expected, actual, database_profile, scope):
    """Existing evaluator identity contract, after its real manifest assembly."""
    from eval_center.verified_index import validate_index_identity
    return validate_index_identity(expected, actual, database_profile=database_profile, scope=scope)


@contextmanager
def execution_lock(path):
    path = Path(path)
    if path.is_symlink():
        raise CheckpointBlocked('WRITER_LOCK_SYMLINK_DENIED')
    with path.open('a+b') as f:
        if os.name == 'nt':
            import msvcrt
            if path.stat().st_size == 0:
                f.write(b'0'); f.flush()
            f.seek(0)
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise CheckpointBlocked('OPERATOR_SINGLE_WRITER_BUSY') from None
        else:
            import fcntl
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise CheckpointBlocked('OPERATOR_SINGLE_WRITER_BUSY') from None
        try:
            yield
        finally:
            if os.name == 'nt':
                f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)


def inventory_report(*, package_root, package_identity):
    """Offline index manifest inspection. Does not construct REAL dependencies."""
    root = Path(package_root)
    index = validate_frozen_package(package_root=root, package_identity=package_identity)
    units = []
    for p in index['packages']:
        raw_manifest = (root / p['manifest']['path']).read_bytes()
        if hashlib.sha256(raw_manifest).hexdigest() != p['manifest']['sha256']:
            raise CheckpointBlocked('DATASET_MANIFEST_DRIFT')
        m = json.loads(raw_manifest)
        count, kind = DATASETS[p['dataset']]
        if len(m['units']) != count or p['input_kind'] != kind or m.get('Gold_qrels_answers_included') is not False:
            raise CheckpointBlocked('FROZEN_SOURCE_INVENTORY_DRIFT')
        for e in m['units']:
            units.append(dict(dataset=p['dataset'], unit_id=e['unit_id'], input_kind=e['input_kind'],
                source_document_id=e['source']['source_document_id'], source_sha256=e['source']['artifact']['sha256'],
                dataset_manifest_sha256=p['manifest']['sha256']))
    if len(units) != 748 or len({(u['dataset'],u['unit_id']) for u in units}) != 748:
        raise CheckpointBlocked('EXACT_748_SOURCES_REQUIRED')
    return dict(status='OFFLINE_SOURCE_INVENTORY_ONLY', package_manifest_sha256=package_identity.manifest_sha256,
        package_identity_sha256=package_identity.fingerprint,
        source_units=748, raw_units=648, prepared_units=100, formal_ingestion='NOT_RUN',
        existing_three_ready='NOT_INSPECTED_NO_AUTOMATIC_REUSE', units=units)


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description='Offline six-pack source inventory; REAL API requires coordinator-reviewed ECS wiring.')
    p.add_argument('--package-root', type=Path, required=True)
    p.add_argument('--review-record', type=Path, required=True)
    p.add_argument('--review-sha256', required=True)
    p.add_argument('--report', type=Path, required=True)
    args = p.parse_args(argv)
    package_identity=load_reviewed_package_identity(review_path=args.review_record,expected_review_sha256=args.review_sha256)
    report = inventory_report(package_root=args.package_root,package_identity=package_identity)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(args.report, report)
    print(json.dumps({k:v for k,v in report.items() if k != 'units'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
