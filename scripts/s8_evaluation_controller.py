"""Bounded S8 normal-API controller. `prepare` is local and zero-send.

The trusted launcher owns tenant332 identity, short-lived Bearer, effective
model binding and root-registered LocalExecution. This controller neither
launches a server nor creates/renews authorization. Gold remains scoring_only.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.m4_tenant332_api import M4Tenant332ApiClient

ARMS={'A':{'graph':False,'floorless':False},'B':{'graph':False,'floorless':True},
      'C':{'graph':True,'floorless':False}}


def source_fingerprints():
    names=['scripts/s8_evaluation_controller.py','scripts/m4_tenant332_api.py',
        'scripts/s7_graph_api_check.py','scripts/s7_graph_generation.py',
        'scripts/s2_generation_quality.py','scripts/local_runtime.py',
        'backend/weknora_port/development.py','backend/weknora_port/runtime.py',
        'backend/weknora_port/execution.py','backend/weknora_port/conversation_configuration.py']
    for folder in ('retrieval','graph','generation','models','conversation_assets'):
        names.extend(str(p.relative_to(ROOT)).replace('\\','/')
            for p in (ROOT/'backend/weknora_port'/folder).rglob('*')
            if p.is_file() and p.suffix in ('.py','.yaml','.json'))
    names.extend(str(p.relative_to(ROOT)).replace('\\','/') for p in (ROOT/'scripts').glob('s8_*.py'))
    return {name:sha((ROOT/name).read_bytes()) for name in sorted(set(names))}


def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def sha(value):return hashlib.sha256(value.encode() if isinstance(value,str) else value).hexdigest()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(path)


def arm_environment(arm):
    if arm not in ARMS:raise ValueError('S8_ARM_NOT_APPROVED_D_FORBIDDEN')
    return {'WEKNORA_PORT_GRAPH_ENABLED':str(ARMS[arm]['graph']).lower(),
            'WEKNORA_PORT_RERANK_TOP1_FLOORLESS':str(ARMS[arm]['floorless']).lower()}


def validate_plan(plan):
    if plan.get('schema')!='s8-evaluation-plan/v1' or not plan.get('cases'):
        raise ValueError('S8_FROZEN_PLAN_REQUIRED')
    for arm,settings in ARMS.items():
        if plan.get('arms',{}).get(arm)!=settings:raise ValueError('S8_ARM_CONTRACT_CHANGED')
    if plan.get('arms',{}).get('D',{}).get('enabled',False):raise ValueError('S8_D_FORBIDDEN')
    if not all(isinstance(plan.get(k),str) and len(plan[k])==64 for k in
               ('source_snapshot_sha256','runtime_identity_sha256')):
        raise ValueError('S8_SOURCE_RUNTIME_FREEZE_REQUIRED')
    binding=plan.get('model_binding_snapshot',{})
    if not binding.get('configured_roles') or not binding.get('effective_roles') or not binding.get('query_understand_model_id'):
        raise ValueError('S8_CONFIGURED_EFFECTIVE_MODELS_REQUIRED')
    if binding.get('database_role_write') is not False:raise ValueError('S8_ROLE_WRITE_FORBIDDEN')
    principal=plan.get('principal',{})
    if (principal.get('tenant_id')!=332 or not isinstance(principal.get('subject_sha256'),str)
            or len(principal['subject_sha256'])!=64 or not principal.get('storage_root')):
        raise ValueError('S8_VERIFIED_PRINCIPAL_REQUIRED')
    seen=set()
    for case in plan['cases']:
        if not all(isinstance(case.get(k),str) and case[k].strip() for k in
                   ('case_id','dataset','qid','knowledge_base_id','question')) or case.get('tenant_id')!=332:
            raise ValueError('S8_CASE_IDENTITY_INVALID')
        if case['case_id'] in seen:raise ValueError('S8_DUPLICATE_CASE')
        seen.add(case['case_id'])
        docs=case.get('document_scope',[])
        if not isinstance(docs,list) or any(not isinstance(v,str) or not v for v in docs):raise ValueError('S8_DOCUMENT_SCOPE_INVALID')
        eligible=case.get('eligible_arms',['A'])
        if not isinstance(eligible,list) or not eligible or any(arm not in ARMS for arm in eligible):
            raise ValueError('S8_CASE_ARM_INVALID')
    return plan


def call_bound(*,memory_embedding_possible=True):
    # One KB, normal 0.3 threshold, max five source query expansions, no worker.
    # Graph query is local; construction is a separate authorized phase.
    return {'query_understanding':1,'initial_embedding':1,'expansion_embeddings':5,
        'rerank':1,'final_generation_including_upgrade':2,
        'memory_recall_embedding':int(memory_embedding_possible),
        'strict_max_calls':10+int(memory_embedding_possible),
        'assumptions':['one KB per case','threshold 0.3','DYNAMIC at most two attempts',
            'no background worker or graph extraction','all actual sends retain Gate reservation'],
        'token_sufficiency':'NOT_YET_PROVEN_FOR_DYNAMIC_WIRES; existing per-file 100000 cap remains fail-closed'}


def request_payload(case,request_id):
    # Explicit allowlist: no tenant, subject, Graph, Gold, qrel or ideal Chunk.
    return {'content':case['question'],'request_id':request_id,'mode':'quick','reasoning_effort':'off',
        'knowledge_base_scope':[case['knowledge_base_id']],'document_scope':list(case.get('document_scope',[]))}


def case_key(case,arm):
    return sha(canonical(['S8',case['dataset'],case['qid'],case['knowledge_base_id'],case.get('document_scope',[]),arm]))


def model_allowlist(plan):
    models=[]
    for role,row in plan['model_binding_snapshot']['effective_roles'].items():
        if role not in ('cheap','expensive','embedding','rerank'):continue
        if not isinstance(row,dict) or any(not isinstance(row.get(k),str) or not row[k]
                for k in ('provider','model','model_id')):raise ValueError('S8_MODEL_BINDING_INVALID')
        models.append({'provider':row['provider'],'model':row['model'],'model_id':row['model_id'],
            'capability':{'embedding':'embedding','rerank':'rerank'}.get(role,'chat')})
    if not {'cheap','expensive','embedding','rerank'} <= set(plan['model_binding_snapshot']['effective_roles']):
        raise ValueError('S8_MODEL_BINDING_INCOMPLETE')
    if plan['model_binding_snapshot']['query_understand_model_id'] not in {r['model_id'] for r in models}:
        raise ValueError('S8_QUERY_MODEL_NOT_IN_EFFECTIVE_BINDING')
    return models


def validate_identity(plan,identity,arm):
    arm_environment(arm)
    if (identity.get('tenant_id')!=332 or identity.get('runtime_identity_sha256')!=plan['runtime_identity_sha256']
        or identity.get('model_binding_snapshot')!=plan['model_binding_snapshot']
        or identity.get('graph_enabled') is not ARMS[arm]['graph']
        or identity.get('rerank_top1_floorless') is not ARMS[arm]['floorless']
        or identity.get('subject_sha256')!=plan['principal']['subject_sha256']
        or identity.get('storage_root')!=plan['principal']['storage_root'] or not identity.get('base_url')):
        raise ValueError('S8_TRUSTED_STARTUP_IDENTITY_MISMATCH')
    if plan.get('source_fingerprints')!=source_fingerprints():
        raise ValueError('S8_UNRECORDED_SOURCE_CHANGE')


def validate_authorization(parent,ledger_path,plan,identity,case,arm):
    if (parent.get('schema')!='s8-owner-authorization/v1' or parent.get('status')!='OPEN'
        or not parent.get('owner_approval',{}).get('answer')
        or not parent.get('owner_approval',{}).get('question_item')
        or parent.get('plan_sha256')!=sha(canonical(plan))
        or {'case_id':case['case_id'],'arm':arm} not in parent.get('approved_case_arms',[])):
        raise ValueError('S8_NEW_OWNER_AUTHORIZATION_REQUIRED')
    registered=[Path(p).resolve() for p in parent.get('ledgers',[])]
    if Path(ledger_path).resolve() not in registered or len(registered)!=len(set(registered)):
        raise ValueError('S8_ROOT_REGISTERED_LEDGER_REQUIRED')
    calls=held=0;active=[]
    for path in registered:
        from backend.weknora_port.execution import _validate_ledger
        value=read(path);_validate_ledger(value)
        calls+=len(value['entries']);held+=sum(e['held_tokens'] for e in value['entries'])
        if value['status']=='STOP':raise ValueError('S8_NEW_UNKNOWN_STOP')
        if any(e['status']!='SETTLED' for e in value['entries']):raise ValueError('S8_UNSETTLED_OR_UNKNOWN_STOP')
        if value['status']=='OPEN':active.append(path)
    if calls>parent['max_calls'] or held>parent['max_tokens'] or active!=[Path(ledger_path).resolve()]:
        raise ValueError('S8_SERIAL_PARENT_LIMIT_DENIED')
    ledger=read(ledger_path)
    if (ledger['status']!='OPEN' or ledger['tenant_id']!=332
        or sha(ledger['subject_id'])!=identity['subject_sha256']
        or case['knowledge_base_id'] not in ledger['allowed_kbs']
        or any(model not in ledger['allowed_models'] for model in model_allowlist(plan))
        or any(model not in parent.get('allowed_models',[]) for model in model_allowlist(plan))
        or not 0<ledger['max_calls']<=12 or not 0<ledger['max_tokens']<=100000):
        raise ValueError('S8_EXECUTION_SCOPE_MODEL_LIMIT_MISMATCH')
    # The current file's entire remaining capacity must fit its parent. This
    # does not replace the existing Gate's actual wire reservation at each send.
    own_held=sum(e['held_tokens'] for e in ledger['entries'])
    if (calls+ledger['max_calls']-len(ledger['entries'])>parent['max_calls']
            or held+ledger['max_tokens']-own_held>parent['max_tokens']):
        raise ValueError('S8_PARENT_REMAINING_CAPACITY_DENIED')
    return ledger


class S8ApiClient(M4Tenant332ApiClient):
    def stream_once(self,session_id,payload,raw_path):
        # Same normal POST and bearer/same-origin contract as M4; persist every
        # observed frame instead of losing partial SSE on a client interruption.
        with Path(raw_path).open('x',encoding='utf-8') as trace:
            with self._client.stream('POST',self.base_url+f'/api/v1/sessions/{session_id}/messages',
                    headers={'Authorization':'Bearer '+self._token,'Origin':self.base_url,
                             'Accept-Language':'zh-CN','Accept':'text/event-stream'},
                    json=payload,timeout=None) as response:
                response.raise_for_status();data=[]
                for line in response.iter_lines():
                    trace.write(line+'\n');trace.flush()
                    if line.startswith('data:'):data.append(line[5:].lstrip())
                    elif not line and data:
                        event=json.loads('\n'.join(data));data=[];yield event
                        if event.get('response_type')=='complete':return
                if data:yield json.loads('\n'.join(data))


class S8Controller:
    def __init__(self,plan,arm,identity,client,checkpoint):
        self.plan=validate_plan(plan);validate_identity(plan,identity,arm)
        self.arm,self.identity,self.client,self.path=arm,identity,client,Path(checkpoint)
        digest=sha(canonical(plan))
        if self.path.exists():
            self.checkpoint=read(self.path)
            if self.checkpoint.get('plan_sha256')!=digest:raise ValueError('S8_CHECKPOINT_PLAN_CHANGED')
        else:self.checkpoint={'schema':'s8-checkpoint/v1','plan_sha256':digest,'entries':{}}

    def save(self):write(self.path,self.checkpoint)

    def validate_case_limits(self,parent,case):
        if Path(parent.get('checkpoint_path','')).resolve()!=self.path.resolve():
            raise ValueError('S8_PARENT_FIXED_CHECKPOINT_REQUIRED')
        limits=parent.get('max_case_arms',{})
        total=parent.get('end_to_end_total')
        if (set(limits)!=set(ARMS) or any(type(n) is not int or n<0 for n in limits.values())
                or type(total) is not int or not 0<total<=sum(limits.values())):
            raise ValueError('S8_OWNER_CASE_LIMITS_REQUIRED')
        entries=list(self.checkpoint['entries'].values())
        if (len(entries)>=total or sum(e.get('arm')==self.arm for e in entries)>=limits[self.arm]):
            raise ValueError('S8_CASE_OR_ARM_LIMIT_DENIED')
        if self.arm!='B':return
        proof=parent.get('b_approval_evidence',{}).get(case['case_id'],{})
        if proof.get('kind')=='DESIGNATED_SUCCESS_CONTROL':
            if case['case_id'] not in parent.get('b_success_control_case_ids',[]):
                raise ValueError('S8_B_CONTROL_NOT_DESIGNATED')
            return
        a=self.checkpoint['entries'].get(case_key(case,'A'),{})
        if proof.get('kind')!='A_LEGAL_TOP1_BELOW_015' or a.get('status')!='COMPLETE':
            raise ValueError('S8_B_COMPLETED_A_TRIGGER_REQUIRED')
        path=Path(proof.get('rerank_response_path',''))
        if not path.is_file() or sha(path.read_bytes())!=proof.get('rerank_response_sha256'):
            raise ValueError('S8_B_A_RERANK_RECEIPT_REQUIRED')
        from backend.weknora_port.retrieval.rerank import _validate_raw_results
        from backend.weknora_port.models.types import RerankResult
        count=proof.get('candidate_count')
        if type(count) is not int or count<=0:raise ValueError('S8_B_A_CANDIDATE_COUNT_REQUIRED')
        response=read(path)
        raw=[RerankResult(row.get('index'),row.get('relevance_score')) for row in response.get('results',[])]
        _validate_raw_results(raw,count)
        if not raw or max(row.score for row in raw)>=.15:
            raise ValueError('S8_B_A_DID_NOT_TRIGGER_FLOORLESS')

    def preflight_api(self):
        # Called only after root permits read-only API checks; never a model call.
        self.client.get_json('/api/v1/runtime')
        public=self.client.get_json('/api/v1/settings/model-configuration')
        roles={r['role']:r for r in public['roles']}
        for role,expected in self.plan['model_binding_snapshot']['configured_roles'].items():
            if role in ('cheap','expensive','embedding','rerank'):
                if any(roles[role].get(k)!=expected[k] for k in ('provider','model')):
                    raise ValueError('S8_CONFIGURED_MODELS_CHANGED')
        visible={r['id'] for r in self.client.get_json('/api/v1/knowledge-bases')}
        if any(c['knowledge_base_id'] not in visible for c in self.plan['cases']):raise ValueError('S8_KB_NOT_VISIBLE')
        return {'status':'READONLY_API_PASS','tenant_id':332,'provider_calls':0}

    def run_case(self,case,authorization,execution,output):
        key=case_key(case,self.arm)
        if key in self.checkpoint['entries']:raise ValueError('S8_ALREADY_CLAIMED_USE_READONLY_RESUME')
        if self.arm not in case.get('eligible_arms',['A']):raise ValueError('S8_CASE_ARM_NOT_SELECTED')
        parent=read(authorization)
        ledger=validate_authorization(parent,execution,self.plan,self.identity,case,self.arm)
        self.validate_case_limits(parent,case)
        remaining=ledger['max_calls']-len(ledger['entries'])
        if remaining<call_bound()['strict_max_calls']:
            return {'status':'NOT_RUN_BATCH_CALL_CAPACITY','strict_calls_required':11,'remaining':remaining}
        out=Path(output)/key;out.mkdir(parents=True,exist_ok=False)
        request_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'S8:'+key))
        # Normal FastAPI creates a separate server run UUID; the client UUID is
        # only its idempotency key, not the Provider request prefix.
        if '*' not in ledger['allowed_request_prefixes']:
            raise ValueError('S8_NORMAL_API_RUN_PREFIX_NOT_AUTHORIZED')
        record={'case_id':case['case_id'],'qid':case['qid'],'arm':self.arm,'batch_key':key,
            'request_id':request_id,'status':'CREATING_SESSION','output':str(out),'events':[]}
        self.checkpoint['entries'][key]=record;self.save()
        try:
            session=self.client.create_session(knowledge_base_scope=[case['knowledge_base_id']],
                document_scope=case.get('document_scope',[]),title='S8 '+key[:20])
            record.update(session_id=session['id'],status='SESSION_CREATED');self.save()
            payload=request_payload(case,request_id);write(out/'question.json',payload)
            record['status']='SUBMITTING';self.save()  # Durable before the sole question POST.
            started=time.monotonic()
            for event in self.client.stream_once(session['id'],payload,out/'response.sse'):
                record['events'].append(event)
                record['status']='SUBMITTED';self.save()
            record['elapsed_seconds']=time.monotonic()-started
            self._history(record,out)
            current=read(execution)
            if any(e['status'] not in ('SETTLED','INFLIGHT') for e in current['entries']):record['status']='UNKNOWN'
            elif any(e['status']=='INFLIGHT' for e in current['entries']):record['status']='AWAITING_PROVIDER_SETTLEMENT'
        except Exception as error:
            record.update(status='TRANSPORT_OR_TOOL_UNCERTAIN_NO_RESEND',error_type=type(error).__name__)
            self.save();raise
        self.save();write(out/'result.json',record)
        return record

    def _history(self,record,out):
        history=self.client.messages(record['session_id']);write(out/'history.json',history)
        assistant=next((m for m in reversed(history) if m.get('role')=='assistant'),{})
        errors=[e for e in record['events'] if e.get('response_type')=='error' or
            e.get('response_type')=='complete' and e.get('data',{}).get('status') in ('error','failed','cancelled')]
        complete=any(e.get('response_type')=='complete' for e in record['events'])
        good=assistant.get('is_completed') is True and bool((assistant.get('content') or '').strip())
        record['status']='FAILED' if errors or assistant.get('turn_error_code') or assistant.get('turn_status') in ('error','cancelled') else 'COMPLETE' if complete and good else 'PARTIAL'
        record['assistant']=assistant

    def resume_readonly(self,case,output):
        record=self.checkpoint['entries'][case_key(case,self.arm)]
        if not record.get('session_id'):raise ValueError('S8_SESSION_CREATE_UNCERTAIN_MANUAL_REVIEW_NO_POST')
        # No ask/create_session call exists in this path, including after UNKNOWN.
        out=Path(output);out.mkdir(parents=True,exist_ok=True)
        previous=record['status']
        self._history(record,out)
        # A completed message does not settle missing Provider usage or prove
        # that an interrupted POST was safe. Never erase that original state.
        if previous in ('UNKNOWN','AWAITING_PROVIDER_SETTLEMENT','TRANSPORT_OR_TOOL_UNCERTAIN_NO_RESEND'):
            record['history_status']=record['status'];record['status']=previous
        self.save();write(out/'readonly-result.json',record)
        return record


def prepare(plan):
    validate_plan(plan)
    return {'status':'OFFLINE_PLAN_VALIDATED_API_AND_CLOUD_NOT_RUN','plan_sha256':sha(canonical(plan)),
        'case_count':len(plan['cases']),'arms':ARMS,'D':'FORBIDDEN','call_bound':call_bound(),
        'model_binding_snapshot':plan['model_binding_snapshot'],'new_provider_calls':0,
        'new_question_posts':0,'authorization_created':False,
        'controller_source_sha256':sha(Path(__file__).read_bytes()),
        'source_fingerprints':source_fingerprints(),
        'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'worktree_changes':subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).splitlines()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('prepare','run','resume'))
    for name in ('plan','output','identity','checkpoint','authorization','execution'):
        parser.add_argument('--'+name,type=Path)
    parser.add_argument('--arm',choices=tuple(ARMS));parser.add_argument('--case-id',action='append',default=[])
    args=parser.parse_args()
    if args.plan is None or args.output is None:parser.error('--plan/--output required')
    plan=validate_plan(read(args.plan))
    if args.command=='prepare':write(args.output,prepare(plan));print('S8_OFFLINE_PREPARED Provider0 POST0');return
    if not args.case_id:parser.error('at least one --case-id required for run/resume')
    cases={c['case_id']:c for c in plan['cases']}
    if any(identifier not in cases for identifier in args.case_id):parser.error('unknown --case-id')
    if args.identity is None or args.checkpoint is None or args.arm is None:parser.error('--identity/--checkpoint/--arm required')
    token=os.environ.get('S8_API_BEARER_TOKEN','')
    if len(token)<32:raise ValueError('S8_SHORT_LIVED_API_TOKEN_REQUIRED')
    identity=read(args.identity);client=S8ApiClient(identity['base_url'],token)
    try:
        controller=S8Controller(plan,args.arm,identity,client,args.checkpoint);controller.preflight_api()
        for identifier in args.case_id:
            case=cases[identifier]
            if args.command=='resume':controller.resume_readonly(case,args.output/case_key(case,args.arm))
            else:
                if args.authorization is None or args.execution is None:parser.error('--authorization/--execution required')
                result=controller.run_case(case,args.authorization,args.execution,args.output)
                if result['status'] in ('UNKNOWN','AWAITING_PROVIDER_SETTLEMENT') or result['status'].startswith('NOT_RUN'):break
    finally:client.close()


if __name__=='__main__':main()
