import json,hashlib,uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend.tests.test_deepseek_safety import ledger_file
from backend.app.application.validation_usd_budget import ValidationAttemptGate
from backend.app.ports.session_attempts import request_identity,AttemptDenied
from backend.app.application.budget import BudgetDenied
from backend.app.application.usd_pricing import input_upper_bound,quote_micro_usd,PEAK_RATES


def fixture(tmp_path,count=7):
    from backend.app.application.rag_validation_scope import RagValidationGate,RagValidationBudgetGate,PHASE
    cases=[]
    for i in range(count):
        run=str(uuid.uuid4());prompt='Synthetic approved RAG context only. '*5+str(i)
        cases.append({'id':str(i),'run_id':run,'request_id':request_identity(run,'quick.answer',1),
            'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
            'input_cap':input_upper_bound(prompt),'max_output_tokens':512,'prompt':prompt})
    scope={'schema_version':1,'phase':PHASE,'model':'deepseek-flash','synthetic_only':True,'cases':cases}
    path=tmp_path/'scope.json';raw=json.dumps(scope,sort_keys=True).encode();path.write_bytes(raw)
    digest=hashlib.sha256(raw).hexdigest()
    ledger=ledger_file(tmp_path,enabled=False)
    ValidationAttemptGate(ledger).initialize_disabled_policy()
    d=json.loads(ledger.read_text());d['calls_allowed_in_this_task']=True;ledger.write_text(json.dumps(d))
    def make(i):
        gate=RagValidationGate(ledger,scope_path=path,scope_sha256=digest,case_id=str(i))
        return gate,RagValidationBudgetGate(gate)
    return make,cases,ledger,path,digest


def reserve(gate,budget,case):
    estimate=quote_micro_usd(PEAK_RATES['deepseek-flash'],input_tokens=case['input_cap'],output_tokens=512)
    r=budget.reserve(run_id=case['run_id'],provider='deepseek',model_name='deepseek-flash',capability='chat',estimate_microunits=estimate)
    return gate.reserve(request_id=case['request_id']),r


def test_scope_hash_and_exact_context_preflight(tmp_path):
    make,cases,_,path,digest=fixture(tmp_path)
    g,_=make(0);g.validate_request(cases[0]['prompt'],30,512,model='deepseek-flash')
    with pytest.raises(AttemptDenied):g.validate_request('unregistered private context',30,512,model='deepseek-flash')
    path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(AttemptDenied,match='RAG_SCOPE_HASH_MISMATCH'):make(0)


def test_atomic_six_phase_cap_across_parallel_gates(tmp_path):
    make,cases,p,_,_=fixture(tmp_path)
    def one(i):
        g,b=make(i)
        try:return reserve(g,b,cases[i])[0]
        except (BudgetDenied,AttemptDenied):return None
    with ThreadPoolExecutor(max_workers=7) as pool:out=list(pool.map(one,range(7)))
    assert sum(x is not None for x in out)==6
    d=json.loads(p.read_text());assert d['consumed_attempts']==6
    assert len({x['validation_phase'] for x in d['attempts']})==1


def test_unregistered_budget_run_denied_and_default_unchanged(tmp_path):
    from backend.app.config import Settings
    make,cases,p,_,_=fixture(tmp_path);g,b=make(0)
    with pytest.raises(BudgetDenied):b.reserve(run_id=str(uuid.uuid4()),provider='deepseek',model_name='deepseek-flash',capability='chat',estimate_microunits=1)
    assert json.loads(p.read_text())['consumed_attempts']==0
    assert Settings().monthly_cloud_budget_microunits==0


def test_unknown_audit_cannot_be_settled_as_known_fee_and_duplicate_is_denied(tmp_path):
    make,cases,p,_,_=fixture(tmp_path);g,b=make(0)
    a,r=reserve(g,b,cases[0]);g.finish(a,'unknown');b.mark_unknown(r.reservation_id)
    b.settle(r.reservation_id,r.reserved_microunits)
    d=json.loads(p.read_text())
    assert d['rag_validation_cost_reservations'][0]['state']=='unknown'
    fresh,b2=make(0)
    with pytest.raises((BudgetDenied,AttemptDenied)):reserve(fresh,b2,cases[0])
    assert json.loads(p.read_text())['consumed_attempts']==1


def test_pretransport_release_only_audit_never_refunds_attempts(tmp_path):
    make,cases,p,_,_=fixture(tmp_path);g,b=make(0)
    r=b.reserve(run_id=cases[0]['run_id'],provider='deepseek',model_name='deepseek-flash',capability='chat',estimate_microunits=quote_micro_usd(PEAK_RATES['deepseek-flash'],input_tokens=cases[0]['input_cap'],output_tokens=512))
    b.release(r.reservation_id)
    d=json.loads(p.read_text());assert d['consumed_attempts']==0
    assert d['rag_validation_cost_reservations'][0]['state']=='released'


def test_success_usage_audit_is_not_actual_debit(tmp_path):
    make,cases,p,_,_=fixture(tmp_path);g,b=make(0)
    a,r=reserve(g,b,cases[0]);g.finish_with_usage(a,'ok',{'prompt_tokens':30,'completion_tokens':6})
    b.settle(r.reservation_id,r.reserved_microunits)
    d=json.loads(p.read_text());row=d['rag_validation_cost_reservations'][0]
    assert row['state']=='usage_observed_charge_unverified'
    assert row['observed_usage_peak_upper_bound_micro_usd']==17
    assert row['actual_charge_verified'] is False


def test_larger_output_registered_bound_compatible_with_global_ledger(tmp_path):
    from backend.app.application.rag_validation_scope import RagValidationGate
    make,cases,p,path,_=fixture(tmp_path)
    d=json.loads(path.read_text());d['cases'][0]['max_output_tokens']=896
    raw=json.dumps(d,sort_keys=True).encode();path.write_bytes(raw)
    g=RagValidationGate(p,scope_path=path,scope_sha256=hashlib.sha256(raw).hexdigest(),case_id='0')
    g.validate_request(cases[0]['prompt'],30,896,model='deepseek-flash')
    assert g.output_cap==896
    with pytest.raises(AttemptDenied):g.validate_request(cases[0]['prompt'],30,1280,model='deepseek-flash')
