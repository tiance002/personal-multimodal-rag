import copy
import importlib
import importlib.util

import pytest

from eval_center.metrics import aggregate_metrics, ranking_metrics


def subject():
    assert importlib.util.find_spec('eval_center.verification') is not None, 'server independent validation is missing'
    return importlib.import_module('eval_center.verification')


def sample_report():
    config = {'top_k': 5, 'candidate_k': 32, 'rrf_k': 60, 'chunk_size': 1200,
              'chunk_overlap': 120, 'context_budget_chars': 8000}
    runtime = {'effective_config': config.copy(), 'git_sha': 'a'*40,
               'corpus_hash': 'b'*64, 'gold_set_hash': 'c'*64, 'index_version': 'd'*64}
    stats = {'qrels': {'a': 2, 'b': 1}, 'rankings': {'keyword':['x','a','b'],
              'vector':['b','x','a'], 'fused':['a','x','b'], 'context':['a','b']},
              'coverage': {'gold_lengths': {'g1': 10, 'g2': 20},
                           'intervals': {'g1': [[0,10]], 'g2': [[0,10],[5,20]]},
                           'threshold': 0.8}}
    metrics = {}
    for stage, ranking in stats['rankings'].items():
        k = config['candidate_k'] if stage in ('keyword','vector') else config['top_k']
        metrics.update({f'{stage}.{name}': value for name,value in ranking_metrics(ranking, stats['qrels'], k=k).items()})
    metrics.update({'context_recall': 1.0, 'evidence_coverage': 1.0})
    cases = [{'case_id': 'case_0123456789ab', 'statistics': stats, 'metrics': metrics}]
    summary = aggregate_metrics([metrics])
    return {'config': config, 'runtime':runtime,
            'manifest':{**{key:value for key,value in runtime.items() if key!='effective_config'}, 'sample_count':1},
            'cases':cases, 'metrics':summary['metrics'], 'metric_counts':summary['counts']}


def test_independent_validator_recomputes_all_stages_and_coverage():
    m = subject()
    report = sample_report()
    verified = m.verify_report(report)
    assert verified['metrics']['fused.recall_at_k'] == 1
    assert verified['metrics']['fused.precision_at_k'] == 0.4
    assert verified['metrics']['context_recall'] == 1


@pytest.mark.parametrize('tamper,reason', [
    ('summary','summary_mismatch'), ('case','case_metrics_mismatch'),
    ('sample','sample_count_mismatch'), ('config','effective_config_mismatch'),
    ('manifest','manifest_runtime_mismatch'), ('counts','metric_counts_mismatch')])
def test_requested_tampering_is_rejected(tamper, reason):
    m = subject()
    report = sample_report()
    if tamper=='summary': report['metrics']['fused.recall_at_k']=0.99
    elif tamper=='case': report['cases'][0]['metrics']['fused.mrr_at_k']=0.5
    elif tamper=='sample': report['manifest']['sample_count']=2
    elif tamper=='config': report['config']['top_k']=9
    elif tamper=='manifest': report['manifest']['git_sha']='f'*40
    elif tamper=='counts': report['metric_counts']['context_recall']['evaluated']=100
    with pytest.raises(m.ExperimentInvalidError) as error:
        m.verify_report(report)
    assert error.value.code==reason


def test_interval_statistics_cannot_cover_more_than_gold_or_double_count():
    m = subject()
    report = sample_report()
    report['cases'][0]['statistics']['coverage']['intervals']['g2']=[[0,10],[0,10]]
    recomputed = m.compute_case_metrics(report['cases'][0]['statistics'], report['runtime']['effective_config'])
    assert recomputed['evidence_coverage']==0.75
    assert recomputed['context_recall']==0.5
    report['cases'][0]['statistics']['coverage']['intervals']['g2']=[[0,21]]
    with pytest.raises(m.ExperimentInvalidError):
        m.verify_report(report)


def test_no_answer_is_excluded_and_null_is_not_accepted_as_zero():
    m = subject()
    stats = {'qrels':{}, 'rankings':dict.fromkeys(('keyword','vector','fused','context'),[]),
             'coverage':{'gold_lengths':{}, 'intervals':{}, 'threshold':0.8}}
    metrics = m.compute_case_metrics(stats, sample_report()['config'])
    assert metrics['fused.recall_at_k'] is None
    assert metrics['context_recall'] is None
    report = sample_report()
    report['cases'].append({'case_id':'case_aaaaaaaaaaaa', 'statistics':stats, 'metrics':metrics})
    report['manifest']['sample_count']=2
    aggregate = aggregate_metrics([case['metrics'] for case in report['cases']])
    report['metrics']=aggregate['metrics']
    report['metric_counts']=aggregate['counts']
    assert m.verify_report(report)['metrics']['fused.recall_at_k']==1
    report['cases'][1]['metrics']['fused.recall_at_k']=0
    with pytest.raises(m.ExperimentInvalidError):
        m.verify_report(report)
