"""SIMULATED providers/fees/Trace; real public hash reads and independent SQLite."""
from __future__ import annotations
import hashlib,json,sqlite3
from dataclasses import replace
from datetime import datetime,timezone,timedelta
from pathlib import Path
import pytest
from eval_center.public_benchmark_cli import _parser,_run
from eval_center.public_paired_reporting import settlement_cost,time_profile,score_answer,join_latest_attempt_report
from eval_center.special_public_data import prepare_full_cohort,load_special_datasets
from eval_center.omni_contract import page_input_contract,selected_page_gold,score_page_parsing,load_independent_qa_overlay
from eval_center.paired_state import load_case_results,load_frozen_cohort,ARMS
from eval_center.public_runner import retry_public_paired_telemetry,run_public_paired_generation_case
from eval_center.paired_simulation import build_simulated_runtime

FREEZE=Path('var/reports/v6-special-subsets-r1/download-verification-attempt1/freeze-manifest.json')
BASE=Path('var/reports/v6-first-evaluation-20261010')
TEST_NOW=datetime(2026,10,10,3,0,tzinfo=timezone.utc)


@pytest.fixture(scope='module',autouse=True)
def frozen_lifecycle_clock():
    """SIMULATED clock only; keep real deadline guards and boundary assertions."""
    from unittest.mock import patch
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls,tz=None):
            return (cls.fromtimestamp(TEST_NOW.timestamp(),tz=tz) if tz is not None
                    else cls.fromtimestamp(TEST_NOW.timestamp(),tz=timezone.utc).replace(tzinfo=None))
    with patch('eval_center.paired_state.datetime',FrozenDateTime), \
         patch('eval_center.public_runner.datetime',FrozenDateTime), \
         patch('eval_center.public_benchmark_cli.datetime',FrozenDateTime), \
         patch('backend.app.application.validation_usd_budget.datetime',FrozenDateTime), \
         patch('backend.app.adapters.models.v6_text_capacity.datetime',FrozenDateTime):
        yield


def test_frozen_clock_keeps_expired_and_boundary_runs_rejected(tmp_path):
    from eval_center.paired_state import register_run, PairedContractError
    # Explicit now_utc and patched lifecycle default share exactly one instant.
    for suffix,deadline,explicit in [('expired',TEST_NOW-timedelta(seconds=1),True),
                                     ('boundary',TEST_NOW,False)]:
        kwargs={'now_utc':TEST_NOW.isoformat()} if explicit else {}
        with pytest.raises(PairedContractError,match='run_deadline_expired'):
            register_run(tmp_path/'boundary.sqlite3',run_id='SIMULATED-'+suffix,
                cohort_sha256='a'*64,source_manifest_sha256='b'*64,code_sha='c'*64,
                deadline_utc=deadline.isoformat(),**kwargs)

@pytest.fixture(scope='module')
def full_inputs(tmp_path_factory):
    root=tmp_path_factory.mktemp('special40');digest=hashlib.sha256(FREEZE.read_bytes()).hexdigest()
    data=prepare_full_cohort(base_cohort_path=BASE/'cohort_dev24.jsonl',base_source_path=BASE/'source_manifest.json',
        freeze_path=FREEZE,freeze_sha256=digest,output_dir=root/'prepared')
    return root,digest,data

def args(root,data,phase='paired-simulate',run='special40-SIMULATED'):
    return _parser().parse_args(['--phase',phase,'--run-id',run,'--state-path',str(root/'center.sqlite3'),
        '--cohort-file',data['cohort']['path'],'--source-manifest',data['source_manifest']['path'],
        '--paired-time-profile','owner-40q-24page'])

@pytest.fixture(scope='module')
def completed40(full_inputs):
    from unittest.mock import patch
    from backend.app.application.quick_chain import LangChainQuickChain
    from backend.app.application.rule_router_v1 import GenerationRole
    root,digest,data=full_inputs
    names=tuple(dict.fromkeys(r['dataset'] for r in load_frozen_cohort(Path(data['cohort']['path']))))
    runtime=build_simulated_runtime(state_path=root/'center.sqlite3',run_id='special40-SIMULATED',dataset_names=names)
    settings_seen,envelopes_seen,wire_seen=[],[],[]
    original_invoke=LangChainQuickChain.invoke
    original_role=GenerationRole.invoke
    def observe_invoke(self,*values,**kwargs):
        settings=kwargs['settings'];policy=settings.router_policy
        settings_seen.append({'mode':policy.mode,'cap':settings.answer_output_token_cap,
            'threshold':str(policy.threshold),'fallback':policy.provider_fallback,
            'escalation':policy.quality_escalation or policy.abstention_escalation or policy.truncated_escalation,
            'memory':getattr(getattr(self,'context_manager',None),'memory_retriever',None) is not None})
        return original_invoke(self,*values,**kwargs)
    def observe_role(self,envelope,**kwargs):
        envelopes_seen.append({'request_id':kwargs['request_id'],'role':self.role,'cap':envelope.max_tokens,
            'prompt_sha256':hashlib.sha256(envelope.prompt.encode()).hexdigest(),
            'context_sha256':envelope.context_hash,'envelope_sha256':envelope.identity,
            'question_sha256':hashlib.sha256(envelope.q0.encode()).hexdigest()})
        return original_role(self,envelope,**kwargs)
    for container in {id(c):c for c in runtime.containers.values()}.values():
        gateway=container.quick_chain.generation_roles['chat_expensive'].gateway
        transport=gateway._simulated_transport
        def observe_wire(request,timeout,_send=transport.send):
            body=json.loads(request.data)
            wire_seen.append({'provider':'deepseek','execution_kind':'SIMULATED',
                'cap':body['max_tokens'],'wire_sha256':hashlib.sha256(request.data).hexdigest()})
            return _send(request,timeout)
        gateway._simulated_transport=replace(transport,send=observe_wire)
    with patch.object(LangChainQuickChain,'invoke',observe_invoke),patch.object(GenerationRole,'invoke',observe_role):
        report=_run(args(root,data),runtime=runtime)
    assert len(settings_seen)==len(envelopes_seen)==120
    assert all(r['cap']==512 and r['threshold']=='0.35' and not r['fallback'] and not r['escalation'] and not r['memory'] for r in settings_seen)
    assert all(r['cap']==512 for r in envelopes_seen+wire_seen)
    assert wire_seen and {r['role'] for r in envelopes_seen}=={'chat_cheap','chat_expensive'}
    assert len(wire_seen)==report['current_provider_observations']['deepseek_fixture_calls']
    observations={'execution_kind':'SIMULATED','settings':settings_seen,'envelopes':envelopes_seen,'deepseek_wire':wire_seen}
    (root/'normal512-observations.json').write_text(json.dumps(observations,indent=2),encoding='utf-8')
    (root/'raw-report40.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return root,data,runtime,report

def test_special_preparation_binds_full40_and_native_gold(full_inputs):
    root,digest,data=full_inputs;cases=load_frozen_cohort(Path(data['cohort']['path']))
    assert len(cases)==40 and sum(bool(r.get('answers')) for r in cases)==24
    assert data['omni_page_count']==24 and data['omni_official_qa_count']==0
    special=load_special_datasets(FREEZE,expected_sha256=digest)
    assert len(special['hotpotqa'].documents)==73 and len(special['mmlongbench-doc'].documents)==3
    assert all(d.text=='' for d in special['mmlongbench-doc'].documents)
    assert all(set(r)<= {'doc_id','path','bytes','sha256','source_kind'} for r in special['mmlongbench-doc'].manifest['source_payload'])
    hot_payload=special['hotpotqa'].manifest['source_payload']
    assert all(not ({'answer','supporting_facts','question'}&set(r)) for r in hot_payload)
    assert sum(not c.evaluation_metadata['evidence_pages'] for c in special['mmlongbench-doc'].cases)==2
    assert len({r['qid'] for r in cases})==40

def test_full40_official_quickchain_fixed_context_and_reports(completed40):
    root,data,runtime,r=completed40
    # New Run uses the owner's supported normal QuickSettings cap. Original
    # uncapped PARTIAL Run/cell remain in their immutable historical evidence.
    assert r['status']=='SIMULATED_PAIRED_COHORT_COMPLETED'
    assert r['attempt_status_counts']=={'simulated':120}
    assert r['failures']==[] and r['answer_output_token_cap']==512
    assert r['qid_count']==40 and r['quickchain_invocations']==120 and r['provider_calls']==0
    assert r['dataset_counts']=={'scifact':8,'miracl-zh':8,'longbench-zh':8,'hotpotqa':8,'mmlongbench-doc':8}
    assert r['answer_gold_denominator']==24 and r['answer_not_scored_no_gold_rows']==16
    assert r['retrieval_scored_rows']==0 and r['retrieval_not_run_rows']==40
    for row in r['paired_arm_report']:
        assert row['status']=='PAIRED_INPUTS_VERIFIED' and row['observed_retrieval_calls']==1
        assert row['answer_output_token_cap']==512
        assert set(row['arms'])==set(ARMS)
        for arm,cell in row['arms'].items():
            assert cell['association']['arm']==arm and cell['association']['qid']==row['qid']
            assert cell['cost']['actual_by_currency_microunits'] is None
            assert cell['cost']['status']=='SIMULATED'
            assert cell['latency']['quickchain_end_to_end_ms']>=cell['latency']['generation_stage_ms']>=0
            assert cell['latency']['retrieval_stage_ms']>=0
    assert r['answer_macro']['DYNAMIC']['counts']['hotpot_answer_token_f1']['evaluated']==8
    assert r['answer_macro']['DYNAMIC']['counts']['character_f1']['evaluated']==16
    assert r['report_closure_status']=='CLOSED_WITHIN_DEADLINE'
    assert r['time_profile']['round_minutes']==60 and r['hard_deadline_utc']=='2026-10-10T07:44:02+00:00'

def test_same_full40_run_id_reuses_zero_new_sends(completed40):
    root,data,runtime,first=completed40
    before=len(runtime.containers['scifact'].simulated_provider_calls)
    second=_run(args(root,data,phase='paired-run'),runtime=runtime)
    assert second['reused_qids']==40 and second['new_quickchain_invocations']==0
    assert len(runtime.containers['scifact'].simulated_provider_calls)==before
    assert second['results_sha256']==first['results_sha256']
    (root/'raw-resume40.json').write_text(json.dumps(second,ensure_ascii=False,indent=2),encoding='utf-8')

def test_full40_missing_native_grants_zero_send(full_inputs,tmp_path):
    _,_,data=full_inputs;r=_run(args(tmp_path,data,phase='paired-run',run='special40-BLOCKED'))
    assert r['status']=='BLOCKED_ZERO_SEND' and r['provider_calls']==0 and r['quickchain_invocations']==0
    assert r['cohort_rows']==40
    (tmp_path/'blocked40.json').write_text(json.dumps(r,indent=2),encoding='utf-8')

def _children(status='succeeded'):
    return [dict(request_id='p7v6-fixture',provider='fixture-provider',request_identity_sha256='a'*64,status=status)]

def _fact():
    return dict(request_id='p7v6-fixture',provider='fixture-provider',request_identity_sha256='a'*64,
        execution_kind='REAL',settlement='COMPLETED',actual_charge_verified=True,currency='CNY',
        actual_charge_microunits=17,source_sha256='b'*64)

@pytest.mark.parametrize('change',['absent','quote','wrong_id','wrong_provider','wrong_hash','unknown','negative','duplicate','simulated'])
def test_unknown_fees_never_become_zero_or_native_verified(change):
    fact=_fact();children=_children();facts=[fact]
    if change=='absent':facts=None
    elif change=='quote':fact['actual_charge_verified']=False
    elif change=='wrong_id':fact['request_id']='different'
    elif change=='wrong_provider':fact['provider']='different'
    elif change=='wrong_hash':fact['request_identity_sha256']='c'*64
    elif change=='unknown':children=_children('unknown')
    elif change=='negative':fact['actual_charge_microunits']=-1
    elif change=='duplicate':facts=[fact,dict(fact)]
    else:fact['execution_kind']='SIMULATED'
    value=settlement_cost(children=children,facts=facts,execution_kind='REAL')
    assert value['status']=='UNKNOWN' and value['actual_by_currency_microunits'] is None
    assert value['calls'][0]['amount_microunits'] is None

def test_native_settlement_fixture_keeps_currency_amount_and_source():
    # Formatter-only synthetic receipt; no real billing/provider has run.
    value=settlement_cost(children=_children(),facts=[_fact()],execution_kind='REAL')
    assert value['actual_by_currency_microunits']=={'CNY':17}
    assert value['calls'][0]['source']=={'kind':'NATIVE_SETTLEMENT','sha256':'b'*64}
    value=settlement_cost(children=_children(),facts=[_fact()],execution_kind='SIMULATED')
    assert value['status']=='SIMULATED' and value['actual_by_currency_microunits'] is None
    assert settlement_cost(children=[],execution_kind='REAL')['status']=='NOT_RUN'

def test_trace_retry_updates_report_join_not_initial_case_or_generation(completed40,monkeypatch):
    root,data,runtime,first=completed40;state=root/'center.sqlite3';run=first['run_id']
    before=load_case_results(state,run);before_hash=hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest()
    calls=len(runtime.containers['scifact'].simulated_provider_calls)
    def fail(client,**kwargs):raise TimeoutError('SIMULATED Trace timeout')
    monkeypatch.setattr('eval_center.public_langfuse.write_and_readback_public_event',fail)
    failed=retry_public_paired_telemetry(state_path=state,run_id=run,client=object(),work_guard=lambda:None)
    assert failed=={'delivered':0,'unknown':120,'generation_dispatches':0}
    monkeypatch.setattr('eval_center.public_langfuse.write_and_readback_public_event',lambda client,**kwargs:{'trace_id':'f'*32})
    result=retry_public_paired_telemetry(state_path=state,run_id=run,client=object(),work_guard=lambda:None)
    assert result=={'delivered':120,'unknown':0,'generation_dispatches':0}
    after=load_case_results(state,run)
    assert hashlib.sha256(json.dumps(after,sort_keys=True).encode()).hexdigest()==before_hash
    assert len(runtime.containers['scifact'].simulated_provider_calls)==calls
    report=join_latest_attempt_report(after,state_path=state,run_id=run)
    assert all(c['latest_telemetry']['trace_id']=='f'*32 and c['initial_telemetry']['trace_id'] is None
        for row in report for c in row['arms'].values())
    (root/'raw-latest-trace-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')

def test_full_round_and_small_profile_caps_and_hard_cutoff():
    now=datetime(2026,10,10,3,0,tzinfo=timezone.utc)
    profile=time_profile('owner-40q-24page');profile.validate(question_count=40,omni_page_count=24)
    hard,dispatch,end=profile.deadlines(now)
    assert dispatch-now==timedelta(minutes=30) and end-now==timedelta(minutes=60)
    assert time_profile('owner-small').deadlines(now)[2]-now==timedelta(minutes=40)
    near=hard-timedelta(minutes=10);assert profile.deadlines(near)[2]==hard
    with pytest.raises(ValueError):profile.validate(question_count=24,omni_page_count=24)
    with pytest.raises(ValueError):replace(profile,round_minutes=120).validate(question_count=40,omni_page_count=24)

def test_hotpot_answer_and_mml_typed_gold_scoring_are_separate():
    assert score_answer('The Zeffirelli!',('Zeffirelli',),dataset='hotpotqa')['hotpot_answer_token_f1']==1
    assert score_answer('yes',('yes maybe',),dataset='hotpotqa')['hotpot_answer_token_f1']==0
    r=score_answer('Not answerable',('Not answerable',),dataset='mmlongbench-doc',metadata={'answer_format':'None'})
    assert r['normalized_exact_match']==1 and r['official_gpt_normalized_accuracy']=='NOT_RUN'

def test_omni24_page_contract_and_gold_not_qa(full_inputs):
    _,digest,_=full_inputs;contract=page_input_contract(FREEZE,expected_sha256=digest)
    assert contract['page_count']==24 and contract['official_qa_count']==0
    assert contract['rag_qa_execution']=='NOT_RUN_NEEDS_INDEPENDENT_PREMODEL_QA'
    gold=selected_page_gold(FREEZE,contract);assert len(gold)==24
    p=contract['pages'][0];g=gold[p['page_id']]
    assert score_page_parsing(p,g)['status']=='NOT_RUN'
    prediction={'page_id':p['page_id'],'image_sha256':p['image_sha256'],
        'blocks':[dict(b) for b in g['layout_dets'] if not b.get('ignore',False)]}
    result=score_page_parsing(p,g,prediction)
    assert result['metrics']['layout_f1']==1 and result['official_omnidocbench_score']=='NOT_RUN'
    # Gold-as-prediction is SIMULATED scorer-contract verification, not OCR.
    assert result['official_qa_accuracy']=='NOT_AVAILABLE'
    prediction['image_sha256']='0'*64
    with pytest.raises(ValueError):score_page_parsing(p,g,prediction)
    assert load_independent_qa_overlay(None,expected_sha256=None,contract=contract,
        first_model_result_utc='2026-10-10T04:00:00Z',review_receipt_sha256=None)['cases']==()

@pytest.mark.parametrize('change',['late','official','no_review','wrong_page','page_as_qid'])
def test_independent_omni_qa_requires_premodel_reviewed_separate_gold(full_inputs,tmp_path,change):
    _,digest,_=full_inputs;contract=page_input_contract(FREEZE,expected_sha256=digest);p=contract['pages'][0]
    body={'schema':'omni-independent-qa-v1','gold_origin':'INDEPENDENT_PREMODEL','review_receipt_sha256':'e'*64,
        'frozen_at_utc':'2026-10-10T03:00:00Z','cases':[{'qid':'omni-independent:reviewed-1','page_id':p['page_id'],
            'source_page_gold_sha256':p['page_gold_sha256'],'question':'SIMULATED independently authored question',
            'answers':['SIMULATED independently reviewed answer']}]}
    if change=='late':body['frozen_at_utc']='2026-10-10T05:00:00Z'
    elif change=='official':body['gold_origin']='OFFICIAL'
    elif change=='no_review':body['review_receipt_sha256']=None
    elif change=='wrong_page':body['cases'][0]['source_page_gold_sha256']='0'*64
    else:body['cases'][0]['qid']=p['page_id']
    file=tmp_path/'independent-fixture.json';file.write_text(json.dumps(body),encoding='utf-8')
    with pytest.raises(ValueError):load_independent_qa_overlay(file,expected_sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
        contract=contract,first_model_result_utc='2026-10-10T04:00:00Z',review_receipt_sha256='e'*64)

def test_reviewed_independent_qa_reaches_normal_quickchain_without_gold(full_inputs,tmp_path,monkeypatch):
    from backend.app.application.rule_router_v1 import GenerationRole
    from eval_center.paired_state import register_run
    _,digest,_=full_inputs;contract=page_input_contract(FREEZE,expected_sha256=digest);p=contract['pages'][0]
    body={'schema':'omni-independent-qa-v1','gold_origin':'INDEPENDENT_PREMODEL','review_receipt_sha256':'e'*64,
        'frozen_at_utc':'2026-10-10T02:00:00Z','cases':[{'qid':'omni-independent:SIMULATED-contract',
            'page_id':p['page_id'],'source_page_gold_sha256':p['page_gold_sha256'],
            'question':'SIMULATED what is shown on this page?','answers':['GOLD_SECRET_NEVER_IN_GENERATION']}]}
    file=tmp_path/'SIMULATED-reviewed-overlay.json';file.write_text(json.dumps(body),encoding='utf-8')
    overlay=load_independent_qa_overlay(file,expected_sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
        contract=contract,first_model_result_utc='2026-10-10T06:00:00Z',review_receipt_sha256='e'*64)
    case=overlay['cases'][0];state=tmp_path/'independent.sqlite3'
    runtime=build_simulated_runtime(state_path=state,run_id='independent-SIMULATED')
    register_run(state,run_id='independent-SIMULATED',cohort_sha256='a'*64,source_manifest_sha256='b'*64,
        code_sha='c'*64,deadline_utc='2026-10-10T07:44:02Z',expected_qids=[case.qid])
    original=GenerationRole.invoke;observed=[]
    def invoke(role,envelope,**kwargs):
        assert 'GOLD_SECRET_NEVER_IN_GENERATION' not in envelope.prompt
        assert 'source_page_gold_sha256' not in envelope.prompt
        observed.append((envelope.prompt,envelope.context_hash,envelope.identity))
        return original(role,envelope,**kwargs)
    monkeypatch.setattr(GenerationRole,'invoke',invoke)
    result=run_public_paired_generation_case(runtime.containers['scifact'],case,runtime.scopes['scifact'],
        run_id='independent-SIMULATED',dataset_name='omni-independent',dataset_version='SIMULATED-contract',
        state_path=state,shared_identity={k:'a'*64 for k in ('corpus_sha256','pool_sha256','qrels_sha256','gold_sha256','index_sha256')},
        cloud_allowed_by_kb=runtime.cloud_allowed_by_kb,dispatch_guard=lambda:None,execution_kind='SIMULATED')
    assert result['status']=='PAIRED_INPUTS_VERIFIED' and len(observed)==3 and len(set(observed))==1
    assert all(c['gold_contract']['metadata']['gold_source']=='INDEPENDENT_PREMODEL' for c in result['arms'].values())


@pytest.mark.parametrize('outcome', ['success', 'timeout'])
def test_optional_timing_gateway_preserves_outcome_and_no_replay(tmp_path, outcome):
    """A legal QuickChain double need not implement optional timing observations."""
    from types import SimpleNamespace
    from eval_center.paired_state import register_run, attempts_for_qid
    from eval_center.public_runner import invoke_paired_generation_arm, ExperimentInvalidError
    from backend.app.application.run_metrics import current_metrics

    calls = []

    class QuickChain:
        def __init__(self):
            self.context_manager = SimpleNamespace(memory_retriever=object())
            self.generation_roles = {
                'chat_cheap': SimpleNamespace(gateway=SimpleNamespace(usage_guard=object()),
                                             invoke=lambda *args, **kwargs: None),
                'chat_expensive': SimpleNamespace(usage_guard=object(),
                                                 invoke=lambda *args, **kwargs: None),
            }

        def clone_without_memory(self):
            return QuickChain()

        def invoke(self, *args, **kwargs):
            calls.append((args, kwargs))
            current_metrics().rule_router = {
                'mode': kwargs['settings'].router_policy.mode,
                'context_hash': 'a' * 64, 'prompt_hash': 'b' * 64,
                'envelope_identity': 'c' * 64,
                'attempts': [{'send_status': 'SIMULATED'}], 'simulation': 'SIMULATED',
            }
            if outcome == 'timeout':
                raise TimeoutError('SIMULATED provider timeout')
            return SimpleNamespace(answer='SIMULATED answer', error_code=None,
                                   citations=(), evidence=())

    state = tmp_path / 'optional-timing.sqlite3'
    register_run(state, run_id='optional-timing-SIMULATED', cohort_sha256='a' * 64,
                 source_manifest_sha256='b' * 64, code_sha='1' * 40,
                 deadline_utc='2026-10-10T04:25:44Z', now_utc=TEST_NOW.isoformat())
    kwargs = dict(arm='DYNAMIC', qid='optional-timing', run_id='optional-timing-SIMULATED',
                  state_run_id='optional-timing-SIMULATED', conversation_id='conversation',
                  cloud_allowed_by_kb={'kb': True}, dispatch_guard=lambda: None,
                  state_path=state, input_identity_sha256='c' * 64)
    container = SimpleNamespace(quick_chain=QuickChain(), store=object())
    result = invoke_paired_generation_arm(container, 'public question', object(), **kwargs)
    assert result['latency']['retrieval_stage_ms'] is None
    assert result['latency']['retrieval_basis'] == 'NOT_OBSERVED'
    assert result['latency'].get('generation_stage_ms') is None
    assert result['latency']['quickchain_end_to_end_ms'] >= 0
    assert len(calls) == 1
    saved = attempts_for_qid(state, run_id=kwargs['run_id'], qid=kwargs['qid'])['DYNAMIC']
    if outcome == 'timeout':
        assert result['attempt_status'] == saved['status'] == 'unknown'
        assert result['result'].error_code == 'DISPATCH_OUTCOME_UNKNOWN'
        with pytest.raises(ExperimentInvalidError, match='paired_attempt_pending_no_retry'):
            invoke_paired_generation_arm(container, 'public question', object(), **kwargs)
    else:
        assert result['attempt_status'] == saved['status'] == 'simulated'
        assert result['result'].answer == 'SIMULATED answer'
        resumed = invoke_paired_generation_arm(container, 'public question', object(), **kwargs)
        assert resumed['attempt_status'] == 'simulated'
    assert len(calls) == 1
