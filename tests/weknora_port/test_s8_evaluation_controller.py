"""Local synthetic controller tests: no server, cloud, DB or grants created."""
from copy import deepcopy
import pytest
from scripts import s8_evaluation_controller as controller


def plan():
    roles={role:{'provider':'deepseek' if role=='expensive' else 'siliconflow',
        'model':'Qwen/Qwen3.5-4B' if role=='cheap' else role,'model_id':role+'-id'}
        for role in ('cheap','expensive','embedding','rerank')}
    configured=deepcopy(roles);configured['cheap']['model']='Xing'
    return {'schema':'s8-evaluation-plan/v1','cases':[{'case_id':'one','dataset':'fixture',
        'qid':'q1','tenant_id':332,'knowledge_base_id':'kb','document_scope':['doc'],
        'question':'original question','scoring_only':{'Gold':'never send this'},'eligible_arms':['A','B','C']}],
        'source_snapshot_sha256':'a'*64,'runtime_identity_sha256':'b'*64,
        'principal':{'tenant_id':332,'subject_sha256':controller.sha('synthetic-subject'),'storage_root':'E:/synthetic-storage'},
        'source_fingerprints':controller.source_fingerprints(),
        'model_binding_snapshot':{'configured_roles':configured,'effective_roles':roles,
            'query_understand_model_id':'cheap-id','database_role_write':False},
        'arms':dict(deepcopy(controller.ARMS),D={'enabled':False})}


def identity(p,arm='A'):
    return {'tenant_id':332,'runtime_identity_sha256':p['runtime_identity_sha256'],
        'model_binding_snapshot':p['model_binding_snapshot'],'graph_enabled':controller.ARMS[arm]['graph'],
        'rerank_top1_floorless':controller.ARMS[arm]['floorless'],
        'subject_sha256':controller.sha('synthetic-subject'),'storage_root':'E:/synthetic-storage','base_url':'http://127.0.0.1:50001'}


def authorization(tmp_path,p):
    path=tmp_path/'existing-execution.json'
    ledger={'status':'OPEN','tenant_id':332,'subject_id':'synthetic-subject',
        'max_calls':12,'max_tokens':100000,'allowed_kbs':['kb'],'allowed_models':controller.model_allowlist(p),
        'allowed_request_prefixes':['*'],'entries':[]}
    # Synthetic existing ledger only, not a newly approved grant.
    controller.write(path,ledger)
    parent={'schema':'s8-owner-authorization/v1','status':'OPEN','owner_approval':
        {'answer':'synthetic test only','question_item':'offline fixture'},'plan_sha256':controller.sha(controller.canonical(p)),
        'approved_case_arms':[{'case_id':'one','arm':'A'}],'allowed_models':controller.model_allowlist(p),
        'ledgers':[str(path)],'max_calls':12,'max_tokens':100000,
        'checkpoint_path':str(tmp_path/'checkpoint.json'),'max_case_arms':{'A':49,'B':6,'C':4},'end_to_end_total':59}
    auth=tmp_path/'existing-parent.json';controller.write(auth,parent)
    return auth,path,parent,ledger


class Client:
    def __init__(self,fail=False):self.calls=[];self.fail=fail
    def create_session(self,**values):self.calls.append(('create',values));return {'id':'session'}
    def stream_once(self,session,payload,path):
        self.calls.append(('question',payload))
        if self.fail:raise OSError('synthetic interruption')
        yield {'response_type':'complete','data':{'status':'completed'}}
    def messages(self,session):
        self.calls.append(('history',session))
        return [{'role':'assistant','content':'answer','is_completed':True,'turn_status':'completed'}]


def test_prepare_requires_actual_nonempty_plan_and_no_network(tmp_path):
    p=plan();result=controller.prepare(p)
    assert result['new_provider_calls']==result['new_question_posts']==0
    assert result['authorization_created'] is False and result['source_fingerprints']
    p['cases']=[]
    with pytest.raises(ValueError,match='FROZEN_PLAN'):controller.prepare(p)


def test_abc_independent_and_d_forbidden():
    assert controller.arm_environment('A')=={'WEKNORA_PORT_GRAPH_ENABLED':'false','WEKNORA_PORT_RERANK_TOP1_FLOORLESS':'false'}
    assert controller.arm_environment('B')['WEKNORA_PORT_GRAPH_ENABLED']=='false'
    assert controller.arm_environment('C')['WEKNORA_PORT_RERANK_TOP1_FLOORLESS']=='false'
    with pytest.raises(ValueError,match='D_FORBIDDEN'):controller.arm_environment('D')


def test_wire_allowlist_and_stable_claim_never_include_gold():
    p=plan();c=p['cases'][0];payload=controller.request_payload(c,'r')
    assert set(payload)=={'content','request_id','mode','reasoning_effort','knowledge_base_scope','document_scope'}
    assert 'never send this' not in controller.canonical(payload)
    changed=deepcopy(c);changed['scoring_only']={'Gold':'other'}
    assert controller.case_key(c,'A')==controller.case_key(changed,'A')
    assert controller.call_bound()['strict_max_calls']==11
    from backend.weknora_port.http_api import MessageCreate
    import uuid
    request_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'S8:'+controller.case_key(c,'A')))
    assert len(request_id)==36
    assert MessageCreate(**controller.request_payload(c,request_id)).request_id==request_id


@pytest.mark.parametrize('change',[{'tenant_id':1},{'graph_enabled':True},{'rerank_top1_floorless':True},
    {'runtime_identity_sha256':'x'*64},{'subject_sha256':'e'*64},{'storage_root':'E:/other-storage'}])
def test_wrong_trusted_identity_denied(change):
    p=plan();i=identity(p);i.update(change)
    with pytest.raises(ValueError,match='IDENTITY_MISMATCH'):controller.validate_identity(p,i,'A')


def test_changed_source_denied_but_docs_commit_identity_not_compared():
    p=plan();p['source_fingerprints']['backend/weknora_port/runtime.py']='bad'
    with pytest.raises(ValueError,match='UNRECORDED_SOURCE'):controller.validate_identity(p,identity(p),'A')
    p=plan();p['head']='old-documentation-head';controller.validate_identity(p,identity(p),'A')


@pytest.mark.parametrize('change',[{'status':'CLOSED'},{'owner_approval':{}},{'approved_case_arms':[]},
    {'allowed_models':[]},{'max_calls':11},{'max_tokens':99999}])
def test_missing_closed_or_insufficient_parent_denied(tmp_path,change):
    p=plan();auth,path,parent,ledger=authorization(tmp_path,p);parent.update(change)
    with pytest.raises(ValueError):controller.validate_authorization(parent,path,p,identity(p),p['cases'][0],'A')


def test_unknown_or_inflight_never_cleared_or_resubmitted(tmp_path):
    p=plan();client=Client();c=controller.S8Controller(p,'A',identity(p),client,tmp_path/'checkpoint.json')
    key=controller.case_key(p['cases'][0],'A')
    c.checkpoint['entries'][key]={'status':'UNKNOWN','session_id':'session','events':[
        {'response_type':'complete','data':{'status':'completed'}}]}
    result=c.resume_readonly(p['cases'][0],tmp_path/'readback')
    assert result['status']=='UNKNOWN' and result['history_status']=='COMPLETE'
    assert [name for name,value in client.calls]==['history']


@pytest.mark.parametrize('status',['UNKNOWN','INFLIGHT'])
def test_unsettled_existing_ledger_refuses_new_case_without_releasing_it(tmp_path,status):
    import json
    p=plan();auth,path,parent,ledger=authorization(tmp_path,p)
    model=ledger['allowed_models'][0]
    request_id='synthetic-old-operation'
    entry={**model,'id':controller.sha(json.dumps([request_id,model['capability']])),
        'request_id':request_id,'wire_sha256':'a'*64,'reserved_tokens':1024,
        'held_tokens':1024,'status':status}
    ledger['entries']=[entry]
    if status=='UNKNOWN':ledger['status']='STOP'
    controller.write(path,ledger);before=path.read_bytes()
    client=Client();c=controller.S8Controller(p,'A',identity(p),client,tmp_path/'checkpoint.json')
    with pytest.raises(ValueError,match='UNKNOWN_STOP|UNSETTLED'):
        c.run_case(p['cases'][0],auth,path,tmp_path/'out')
    assert path.read_bytes()==before and not client.calls


@pytest.mark.parametrize('change',[{'tenant_id':1},{'subject_id':'other'},
    {'allowed_kbs':['other-kb']},{'allowed_models':[]}])
def test_existing_ledger_cannot_lend_wrong_identity_or_scope(tmp_path,change):
    p=plan();auth,path,parent,ledger=authorization(tmp_path,p);ledger.update(change)
    controller.write(path,ledger)
    with pytest.raises(Exception):controller.validate_authorization(parent,path,p,identity(p),p['cases'][0],'A')


def test_insufficient_whole_case_capacity_does_not_claim_or_post(tmp_path):
    p=plan();auth,path,parent,ledger=authorization(tmp_path,p);ledger['max_calls']=10
    controller.write(path,ledger);client=Client()
    c=controller.S8Controller(p,'A',identity(p),client,tmp_path/'checkpoint.json')
    result=c.run_case(p['cases'][0],auth,path,tmp_path/'out')
    assert result['status']=='NOT_RUN_BATCH_CALL_CAPACITY'
    assert not client.calls and not c.checkpoint['entries']


def test_complete_sse_maps_complete_and_claim_prevents_second_post(tmp_path):
    p=plan();auth,path,parent,ledger=authorization(tmp_path,p);client=Client()
    c=controller.S8Controller(p,'A',identity(p),client,tmp_path/'checkpoint.json')
    result=c.run_case(p['cases'][0],auth,path,tmp_path/'out')
    assert result['status']=='COMPLETE'
    assert [name for name,value in client.calls]==['create','question','history']
    with pytest.raises(ValueError,match='ALREADY_CLAIMED'):c.run_case(p['cases'][0],auth,path,tmp_path/'out')
    assert len(client.calls)==3


def test_interruption_is_durable_no_resend_even_after_restart(tmp_path):
    p=plan();auth,path,parent,ledger=authorization(tmp_path,p);client=Client(fail=True)
    c=controller.S8Controller(p,'A',identity(p),client,tmp_path/'checkpoint.json')
    with pytest.raises(OSError):c.run_case(p['cases'][0],auth,path,tmp_path/'out')
    restored=controller.S8Controller(p,'A',identity(p),Client(),tmp_path/'checkpoint.json')
    with pytest.raises(ValueError,match='ALREADY_CLAIMED'):restored.run_case(p['cases'][0],auth,path,tmp_path/'out')
    assert controller.read(tmp_path/'checkpoint.json')['entries'][controller.case_key(p['cases'][0],'A')]['status']=='TRANSPORT_OR_TOOL_UNCERTAIN_NO_RESEND'


def test_changed_plan_cannot_reuse_checkpoint(tmp_path):
    p=plan();c=controller.S8Controller(p,'A',identity(p),Client(),tmp_path/'checkpoint.json');c.save()
    changed=deepcopy(p);changed['cases'][0]['question']='rewritten'
    with pytest.raises(ValueError,match='PLAN_CHANGED'):
        controller.S8Controller(changed,'A',identity(changed),Client(),tmp_path/'checkpoint.json')


def test_parent_checkpoint_and_all_claims_bound_stage_question_count(tmp_path):
    p=plan();auth,path,parent,ledger=authorization(tmp_path,p);client=Client()
    wrong=controller.S8Controller(p,'A',identity(p),client,tmp_path/'other-checkpoint.json')
    with pytest.raises(ValueError,match='FIXED_CHECKPOINT'):wrong.run_case(p['cases'][0],auth,path,tmp_path/'out')
    c=controller.S8Controller(p,'A',identity(p),client,tmp_path/'checkpoint.json')
    # Both failed-before-Provider and successful claims consume case capacity.
    c.checkpoint['entries']={'old':{'arm':'A','status':'TRANSPORT_OR_TOOL_UNCERTAIN_NO_RESEND'}}
    parent.update(max_case_arms={'A':1,'B':6,'C':4},end_to_end_total=11);controller.write(auth,parent)
    with pytest.raises(ValueError,match='CASE_OR_ARM_LIMIT'):c.run_case(p['cases'][0],auth,path,tmp_path/'out')
    parent.update(max_case_arms={'A':49,'B':6,'C':4},end_to_end_total=1);controller.write(auth,parent)
    with pytest.raises(ValueError,match='CASE_OR_ARM_LIMIT'):c.run_case(p['cases'][0],auth,path,tmp_path/'out')
    assert not client.calls


def test_b_requires_completed_a_legal_low_receipt_or_designated_control(tmp_path):
    p=plan();auth,path,parent,ledger=authorization(tmp_path,p);case=p['cases'][0]
    c=controller.S8Controller(p,'B',identity(p,'B'),Client(),tmp_path/'checkpoint.json')
    with pytest.raises(ValueError,match='COMPLETED_A'):c.validate_case_limits(parent,case)
    c.checkpoint['entries'][controller.case_key(case,'A')]={'arm':'A','status':'COMPLETE'}
    receipt=tmp_path/'synthetic-a-rerank.json';controller.write(receipt,{'results':[{'index':0,'relevance_score':.1}]})
    proof={'kind':'A_LEGAL_TOP1_BELOW_015','rerank_response_path':str(receipt),
        'rerank_response_sha256':controller.sha(receipt.read_bytes()),'candidate_count':1}
    parent['b_approval_evidence']={case['case_id']:proof}
    c.validate_case_limits(parent,case)
    controller.write(receipt,{'results':[{'index':0,'relevance_score':.2}]})
    proof['rerank_response_sha256']=controller.sha(receipt.read_bytes())
    with pytest.raises(ValueError,match='DID_NOT_TRIGGER'):c.validate_case_limits(parent,case)
    proof['kind']='DESIGNATED_SUCCESS_CONTROL'
    with pytest.raises(ValueError,match='CONTROL_NOT_DESIGNATED'):c.validate_case_limits(parent,case)
    parent['b_success_control_case_ids']=[case['case_id']]
    c.validate_case_limits(parent,case)
