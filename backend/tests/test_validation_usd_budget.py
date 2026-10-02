import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone

import pytest

from backend.app.application.validation_usd_budget import ValidationAttemptGate,POLICY,SYNTHETIC_PROMPT
from backend.app.application.usd_pricing import PEAK_RATES,input_upper_bound,quote_micro_usd
from backend.app.application.session_attempts import SessionAttemptGate
from backend.app.ports.session_attempts import AttemptDenied
from backend.tests.test_deepseek_safety import ledger_file


def gate(tmp_path,**kwargs):
    p=ledger_file(tmp_path,enabled=False)
    g=ValidationAttemptGate(p,**kwargs)
    g.initialize_disabled_policy()
    d=json.loads(p.read_text());d['calls_allowed_in_this_task']=True;p.write_text(json.dumps(d))
    return g,p


def test_currency_exact_rounding_cache_and_input_bound():
    r=PEAK_RATES['deepseek-flash']
    assert r.currency=='USD' and r.scale==1_000_000
    assert quote_micro_usd(r,input_tokens=256,output_tokens=512)==692
    assert quote_micro_usd(r,input_tokens=1,output_tokens=0)==1
    assert quote_micro_usd(r,input_tokens=10,output_tokens=0,cached_input_tokens=10)==1
    assert quote_micro_usd(r,input_tokens=10,output_tokens=0)==3
    assert input_upper_bound(SYNTHETIC_PROMPT)>=len(SYNTHETIC_PROMPT.encode())


@pytest.mark.parametrize('kwargs',[{'input_tokens':-1,'output_tokens':1},
    {'input_tokens':1,'output_tokens':1,'cached_input_tokens':2},
    {'input_tokens':True,'output_tokens':1}])
def test_invalid_usage_fails_closed(kwargs):
    with pytest.raises(ValueError):quote_micro_usd(PEAK_RATES['deepseek-flash'],**kwargs)


def test_parallel_atomic_count_and_token_upper_bounds(tmp_path):
    g,p=gate(tmp_path,input_tokens_cap=199_488)
    def reserve(i):
        try:return g.reserve(request_id=f'synthetic-{i}')
        except AttemptDenied:return None
    with ThreadPoolExecutor(max_workers=12) as pool:
        results=list(pool.map(reserve,range(24)))
    data=json.loads(p.read_text())
    assert sum(a is not None for a in results)==5
    assert data['consumed_attempts']==data['reserved_attempts']==5
    assert g._used(data)==1_000_000


def test_fifty_is_hard_cap_and_unknown_requests_count(tmp_path):
    g,p=gate(tmp_path)
    for i in range(50):
        a=g.reserve(request_id=f'synthetic-{i}');g.finish(a,'unknown')
    with pytest.raises(AttemptDenied,match='SESSION_CALL_LIMIT'):g.reserve(request_id='fifty-first')
    assert json.loads(p.read_text())['consumed_attempts']==50


def test_retracted_half_dollar_cap_is_not_a_stop_condition(tmp_path):
    g,p=gate(tmp_path,model='deepseek-v4-pro',input_tokens_cap=600_000)
    g.reserve(request_id='audited-not-usd-limited')
    d=json.loads(p.read_text())
    assert d['attempts'][0]['validation_cost']['reserved_upper_bound_micro_usd']>500_000
    assert d['validation_policy']['usd_stop_limit'] is None


def test_unknown_cannot_refund_duplicate_or_reset_on_restart_next_day(tmp_path,monkeypatch):
    import backend.app.application.validation_usd_budget as module
    g,p=gate(tmp_path,input_tokens_cap=600_000)
    a=g.reserve(request_id='before-midnight');g.finish(a,'unknown')
    class NextDay:
        @staticmethod
        def now(tz):return datetime(2026,10,2,tzinfo=timezone.utc)
    monkeypatch.setattr(module,'datetime',NextDay)
    fresh=ValidationAttemptGate(p,input_tokens_cap=600_000)
    with pytest.raises(AttemptDenied,match='SESSION_REQUEST_ALREADY_RESERVED'):fresh.reserve(request_id='before-midnight')
    with pytest.raises(AttemptDenied,match='VALIDATION_TOKEN_CAP'):fresh.reserve(request_id='after-midnight')
    d=json.loads(p.read_text())
    assert d['consumed_attempts']==1 and fresh._used(d)==600_512


def test_provider_usage_success_settles_tokens_and_audits_usd(tmp_path):
    g,p=gate(tmp_path)
    a=g.reserve(request_id='success')
    g.finish_with_usage(a,'ok',{'prompt_tokens':30,'completion_tokens':6,'total_tokens':36})
    d=json.loads(p.read_text());row=d['attempts'][0]
    assert g._used(d)==36 and row['validation_tokens']['state']=='settled'
    assert row['validation_cost']['observed_usage_peak_upper_bound_micro_usd']==17
    assert row['validation_cost']['actual_charge_verified'] is False
    assert d['consumed_attempts']==1 and d['reserved_attempts']==0


@pytest.mark.parametrize('usage',[{}, {'prompt_tokens':True,'completion_tokens':6},
    {'prompt_tokens':30,'completion_tokens':6,'total_tokens':99},
    {'prompt_tokens':1_000_001,'completion_tokens':6}])
def test_bad_success_usage_retains_full_reservation(tmp_path,usage):
    g,p=gate(tmp_path);a=g.reserve(request_id='uncertain')
    with pytest.raises(AttemptDenied,match='VALIDATION_USAGE_UNVERIFIED'):g.finish_with_usage(a,'ok',usage)
    d=json.loads(p.read_text())
    assert d['attempts'][0]['validation_tokens']['state']=='unknown'
    assert g._used(d)==g.input_cap+512


def test_truncated_response_even_with_usage_stays_reserved_upper_bound(tmp_path):
    g,p=gate(tmp_path);a=g.reserve(request_id='truncated')
    g.finish_with_usage(a,'truncated',{'prompt_tokens':30,'completion_tokens':6})
    assert g._used(json.loads(p.read_text()))==g.input_cap+512


def test_policy_initialization_cannot_reinterpret_legacy_or_reset_counts(tmp_path):
    p=ledger_file(tmp_path)
    a=SessionAttemptGate(p).reserve(request_id='legacy');SessionAttemptGate(p).finish(a,'unknown')
    d=json.loads(p.read_text());d['calls_allowed_in_this_task']=False;p.write_text(json.dumps(d))
    before=p.read_bytes()
    with pytest.raises(AttemptDenied,match='LEGACY_ATTEMPT_COST_OR_TOKENS_UNDEFINED'):
        ValidationAttemptGate(p).initialize_disabled_policy()
    assert p.read_bytes()==before


def test_generic_gate_cannot_bypass_token_policy(tmp_path):
    _,p=gate(tmp_path)
    with pytest.raises(AttemptDenied,match='VALIDATION_GATE_REQUIRED'):SessionAttemptGate(p).reserve(request_id='bypass')
    assert json.loads(p.read_text())['consumed_attempts']==0


def test_disabled_ledger_and_product_monthly_default_unchanged(tmp_path):
    from backend.app.config import Settings
    g,p=gate(tmp_path)
    d=json.loads(p.read_text());d['calls_allowed_in_this_task']=False;p.write_text(json.dumps(d))
    with pytest.raises(AttemptDenied,match='SESSION_CALLS_DISABLED'):g.reserve(request_id='disabled')
    assert Settings().monthly_cloud_budget_microunits==0 and Settings().cloud_enabled is False
    assert json.loads(p.read_text())['consumed_attempts']==0


@pytest.mark.parametrize('mode',['valid','private_prompt','too_many_output','timeout','model_mismatch'])
def test_actual_adapter_request_boundary_and_usage(tmp_path,monkeypatch,mode):
    from backend.tests.test_deepseek_safety import gateway
    from backend.app.ports.providers import ProviderRequestNotSent
    cloud,p,requests=gateway(tmp_path,monkeypatch)
    d=json.loads(p.read_text());d['calls_allowed_in_this_task']=False;p.write_text(json.dumps(d))
    cloud.attempt_gate=ValidationAttemptGate(p);cloud.attempt_gate.initialize_disabled_policy()
    d=json.loads(p.read_text());d['calls_allowed_in_this_task']=True;p.write_text(json.dumps(d))
    if mode=='model_mismatch':cloud.chat_model='deepseek-v4-pro'
    prompt='unapproved document prompt' if mode=='private_prompt' else SYNTHETIC_PROMPT
    tokens=896 if mode=='too_many_output' else 512
    timeout=31 if mode=='timeout' else 30
    if mode=='valid':
        cloud.answer_with_budget(prompt,timeout,tokens,cloud_authorized=True,request_id='synthetic')
        d=json.loads(p.read_text())
        assert len(requests)==1 and d['consumed_attempts']==1 and cloud.attempt_gate._used(d)==36
    else:
        with pytest.raises(ProviderRequestNotSent):
            cloud.answer_with_budget(prompt,timeout,tokens,cloud_authorized=True,request_id='synthetic')
        assert requests==[] and json.loads(p.read_text())['consumed_attempts']==0


def test_reported_usage_beyond_bound_blocks_further_calls(tmp_path):
    g,p=gate(tmp_path);a=g.reserve(request_id='overrun')
    with pytest.raises(AttemptDenied):
        g.finish_with_usage(a,'ok',{'prompt_tokens':g.input_cap+1,'completion_tokens':6})
    with pytest.raises(AttemptDenied,match='VALIDATION_USAGE_BOUND_EXCEEDED'):
        g.reserve(request_id='must-not-send-more')
    assert json.loads(p.read_text())['consumed_attempts']==1


def test_reinitialize_disabled_does_not_reset_completed_unknown_attempt(tmp_path):
    g,p=gate(tmp_path);a=g.reserve(request_id='unknown');g.finish(a,'unknown')
    d=json.loads(p.read_text());d['calls_allowed_in_this_task']=False;p.write_text(json.dumps(d))
    before=json.loads(p.read_text())
    ValidationAttemptGate(p).initialize_disabled_policy()
    after=json.loads(p.read_text())
    assert before==after and after['consumed_attempts']==1
