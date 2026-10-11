"""State mapping counterexamples: SIMULATED providers, real temporary native SQLite."""
from dataclasses import replace
import json
import pytest
from backend.app.application.rule_router_v1 import GenerationRole
from backend.app.ports.providers import ProviderRequestNotSent
from eval_center.paired_simulation import build_simulated_runtime
from eval_center.paired_state import run_record, close_run, begin_attempt
from eval_center.public_benchmark_cli import _parser, _run


@pytest.mark.parametrize('outcome', ['success', 'one_failed', 'all_failed', 'unknown'])
def test_native_completion_tracks_actual_success_matrix_and_recovery(tmp_path, monkeypatch, outcome):
    state = tmp_path/'SIMULATED-native-status.sqlite3'
    rid = 'SIMULATED-status-'+outcome
    runtime = build_simulated_runtime(state_path=state, run_id=rid)
    invoke = GenerationRole.invoke
    observed = []
    def controlled(role, envelope, **kwargs):
        observed.append((role.role, kwargs['request_id']))
        if outcome == 'all_failed' or (outcome in {'one_failed', 'unknown'} and len(observed)==1):
            if outcome == 'unknown': raise TimeoutError('SIMULATED uncertain outcome')
            raise ProviderRequestNotSent('SIMULATED controlled failure')
        return invoke(role, envelope, **kwargs)
    monkeypatch.setattr(GenerationRole, 'invoke', controlled)
    args = _parser().parse_args(['--phase','paired-run','--run-id',rid,'--state-path',str(state)])
    first = _run(args, runtime=runtime)
    expected = 'completed' if outcome=='success' else 'partial'
    assert first['qid_count']==24 and first['quickchain_invocations']==72
    assert first['run_status']==run_record(state,rid)['status']==expected
    assert first['status']=='SIMULATED_PAIRED_COHORT_'+('COMPLETED' if expected=='completed' else 'PARTIAL')
    assert bool(first['failures']) == (outcome!='success')
    before=len(observed)
    second=_run(args,runtime=runtime)
    assert len(observed)==before and second['new_quickchain_invocations']==0 and second['reused_qids']==24
    assert second['results_sha256']==first['results_sha256'] and second['run_status']==expected
    assert second['status']==first['status']
    # Recovery iterates durable rows in SQLite order; compare every failure and
    # its multiplicity, without inventing a cohort-order guarantee.
    assert sorted(json.dumps(f,sort_keys=True) for f in second['failures']) == sorted(
        json.dumps(f,sort_keys=True) for f in first['failures'])
    (tmp_path/'outcome.json').write_text(json.dumps(dict(outcome=outcome,native_status=expected,
        cohort_status=first['status'],attempt_status_counts=first['attempt_status_counts'],
        failures=first['failures'],resume_new_calls=second['new_quickchain_invocations']),indent=2)+'\n',encoding='utf8')


@pytest.mark.parametrize('paused', [False, True])
def test_cancelled_dispatch_is_never_completed_and_preserves_paused(tmp_path, monkeypatch, paused):
    from eval_center.public_runner import ExperimentInvalidError
    state=tmp_path/'SIMULATED-cancel.sqlite3';rid='SIMULATED-cancel-'+str(paused)
    runtime=build_simulated_runtime(state_path=state,run_id=rid)
    def cancel(*args, **kwargs):raise ExperimentInvalidError('CANCELLED')
    def configure(deadline):
        if paused:close_run(state,run_id=rid,status='paused')
    runtime=replace(runtime,configure_deadline=configure)
    monkeypatch.setattr('eval_center.public_runner.run_public_paired_generation_case',cancel)
    args=_parser().parse_args(['--phase','paired-run','--run-id',rid,'--state-path',str(state)])
    report=_run(args,runtime=runtime)
    assert report['run_status']==('paused' if paused else 'partial')
    assert report['status']=='SIMULATED_PAIRED_COHORT_PARTIAL'
    assert report['quickchain_invocations']==report['new_quickchain_invocations']==0
    assert len(report['failures'])==24 and all(
        f['error_code']==str(ExperimentInvalidError('CANCELLED')) for f in report['failures'])


def test_pending_partial_qid_remains_running_and_is_not_replayed(tmp_path,monkeypatch):
    import eval_center.public_runner as runner
    state=tmp_path/'SIMULATED-pending.sqlite3';rid='SIMULATED-pending-native'
    runtime=build_simulated_runtime(state_path=state,run_id=rid)
    original=runner.run_public_paired_generation_case
    interrupted=[]
    def interrupt(container,case,scope,**kwargs):
        if not interrupted:
            interrupted.append(case.qid)
            begin_attempt(state,run_id=rid,qid=case.qid,arm='CHEAP_ONLY',input_identity_sha256='a'*64)
            raise RuntimeError('SIMULATED process stopped with pending attempt')
        return original(container,case,scope,**kwargs)
    monkeypatch.setattr(runner,'run_public_paired_generation_case',interrupt)
    args=_parser().parse_args(['--phase','paired-run','--run-id',rid,'--state-path',str(state)])
    first=_run(args,runtime=runtime)
    assert first['run_status']=='running' and first['status']=='SIMULATED_PAIRED_COHORT_PARTIAL'
    assert first['qid_count']==23 and first['quickchain_invocations']==69
    second=_run(args,runtime=runtime)
    assert second['run_status']=='running' and second['new_quickchain_invocations']==0
    assert second['reused_qids']==23 and any(f['status']=='CARRIED_NO_RETRY' for f in second['failures'])


@pytest.mark.parametrize('change', ['success', 'real_success', 'wrong_qid', 'duplicate_qid',
    'missing_case_arm', 'missing_native_arm', 'native_unknown', 'cell_error', 'provider_unknown', 'real_simulated'])
def test_completion_requires_exact_frozen_matrix_and_native_success(change):
    from copy import deepcopy
    from eval_center.paired_state import ARMS
    from eval_center.public_runner import _paired_cohort_success_matrix
    cohort=[dict(qid='q1',dataset='miracl-zh'),dict(qid='q2',dataset='miracl-zh')]
    cells={arm:dict(attempt_status='simulated',error_code=None) for arm in ARMS}
    results=[dict(**row,status='PAIRED_INPUTS_VERIFIED',arms=deepcopy(cells)) for row in cohort]
    receipts={r['qid']:{arm:dict(status='simulated',error_code=None,result={'error_code':None},
        provider_calls=[dict(status='simulated',error_code=None)]) for arm in ARMS} for r in cohort}
    kind='SIMULATED'
    if change=='wrong_qid':results[1]['qid']='unexpected'
    if change=='duplicate_qid':results[1]['qid']='q1'
    if change=='missing_case_arm':results[0]['arms'].pop('CHEAP_ONLY')
    if change=='missing_native_arm':receipts['q1'].pop('CHEAP_ONLY')
    if change=='native_unknown':receipts['q1']['CHEAP_ONLY']['status']='unknown'
    if change=='cell_error':results[0]['arms']['CHEAP_ONLY']['error_code']='CANCELLED'
    if change=='provider_unknown':receipts['q1']['CHEAP_ONLY']['provider_calls'][0]['status']='unknown'
    if change=='real_simulated':kind='REAL'
    if change=='real_success':
        kind='REAL'
        for r in results:
            for cell in r['arms'].values():cell['attempt_status']='succeeded'
        for arms in receipts.values():
            for receipt in arms.values():
                receipt['status']='succeeded';receipt['provider_calls'][0]['status']='succeeded'
    assert _paired_cohort_success_matrix(cohort,results,receipts,kind) == (change in {'success','real_success'})
