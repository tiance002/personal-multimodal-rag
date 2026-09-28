import copy
import json

import pytest

from eval_center.contracts import BundleValidationError
from eval_center.tests.test_v2_import import make_v2
from scripts.package_eval_bundle import build_bundle
from eval_center.verification import compute_case_metrics
from eval_center.metrics import aggregate_metrics


def inputs():
    original = make_v2()
    report = {key:copy.deepcopy(original[key]) for key in
              ('schema_version','runtime','metrics','metric_counts','cases','errors')}
    report.update(status='PASS', mode=original['manifest']['evaluation_mode'],
                  dataset_version=original['manifest']['dataset_version'])
    case = report['cases'][0]
    case.update(case_id='PRIVATE_CASE',question='PRIVATE_QUESTION',answer='PRIVATE_ANSWER')
    stats=case['statistics']
    stats['qrels']={'PRIVATE_CHUNK':1}
    stats['rankings']={stage:['PRIVATE_CHUNK'] for stage in stats['rankings']}
    stats['coverage'].update(gold_lengths={'PRIVATE_SOURCE':20},intervals={'PRIVATE_SOURCE':[[0,20]]})
    report['runtime']['host']='PRIVATE_HOST'
    manifest=copy.deepcopy(original['manifest'])
    manifest['token']='PRIVATE_TOKEN'
    config=copy.deepcopy(original['config'])
    config['password']='PRIVATE_PASSWORD'
    return report,manifest,config


def test_v2_export_keeps_recomputable_statistics_and_removes_private_fields():
    report,manifest,config=inputs()
    bundle=build_bundle(report,manifest,config)
    assert bundle['schema_version']==2
    assert 'PRIVATE_' not in json.dumps(bundle)
    assert bundle['metrics']==report['metrics']
    assert bundle['runtime']['effective_config']==bundle['config']
    item=next(iter(bundle['cases'][0]['statistics']['qrels']))
    assert bundle['cases'][0]['statistics']['rankings']['fused']==[item]
    assert item.startswith('item_')


@pytest.mark.parametrize('part',['summary','case','sample','config'])
def test_v2_export_never_fixes_inconsistent_claims(part):
    report,manifest,config=inputs()
    if part=='summary': report['metrics']['fused.recall_at_k']=.3
    elif part=='case': report['cases'][0]['metrics']['fused.recall_at_k']=.3
    elif part=='sample': manifest['sample_count']=2
    else: config['top_k']=9
    with pytest.raises(BundleValidationError): build_bundle(report,manifest,config)


def test_v2_export_rejects_unknown_statistics_instead_of_downgrading_to_v1():
    report,manifest,config=inputs()
    report['cases'][0]['statistics']['question']='PRIVATE_QUESTION'
    with pytest.raises(BundleValidationError): build_bundle(report,manifest,config)


def test_full_source_mapping_and_actual_call_stats_export_without_private_text():
    report,manifest,config=inputs()
    case=report['cases'][0]
    case['statistics']={'gold_sources':{'PRIVATE_SOURCE':{'length':20,'grade':1}},
        'index_intersections':{'PRIVATE_CHUNK':{'PRIVATE_SOURCE':[[0,20]]}},
        'rankings':{stage:['PRIVATE_CHUNK'] for stage in ('keyword','vector','fused','context')},
        'coverage_threshold':.8,'context_mode':'sent',
        'retrieval_attempts':[{stage:['PRIVATE_CHUNK'] for stage in ('keyword','vector','fused')}],
        'dedup':{'total_units':1,'duplicate_units':0,'tokens_before':30,'tokens_after':30,
            'reviewed_merges':0,'false_merges':0,'tokenizer':'cl100k_base'},
        'telemetry':{'calls':[{'stage':'answer','role':'business','status':'ok',
            'input_tokens':100,'output_tokens':20,'latency_ms':3}],
            'timings':{'generation_ms':3},'context_tokens':{'availability':'estimated','count':30},
            'evidence_tokens':{'availability':'unavailable','count':None}}}
    case['metrics']=compute_case_metrics(case['statistics'],config)
    summary=aggregate_metrics([case['metrics']])
    report.update(metrics=summary['metrics'],metric_counts=summary['counts'])
    report['errors']=[{'case_id':'PRIVATE_CASE','stage':'retrieve','error_code':'VECTOR_UNAVAILABLE',
                      'message':'PRIVATE_TRACEBACK'}]
    bundle=build_bundle(report,manifest,config)
    assert 'PRIVATE_' not in json.dumps(bundle)
    assert bundle['metrics']['business_total_tokens']==120
    assert bundle['metrics']['context_tokens_estimated']==30
    assert bundle['metrics']['context_recall']==1
    assert bundle['metrics']['context_sent_to_model']==1
    assert bundle['metrics']['context_token_savings_estimated']==0
    assert bundle['errors'][0]['error_code']=='VECTOR_UNAVAILABLE'
