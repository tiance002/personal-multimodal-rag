import copy
import hashlib
import json
import sqlite3

import pytest

from eval_center.contracts import BundleValidationError, normalize_bundle
from eval_center.metrics import aggregate_metrics
from eval_center.verification import compute_case_metrics
from eval_center.store import import_bundle, list_experiments, get_experiment, compare_experiments
from eval_center.store import initialize_database, ExperimentConflictError
from eval_center.tests.test_store import make_bundle
from eval_center.quality import answer_statistics


def make_v2():
    bundle = make_bundle()
    config = {'top_k':5,'candidate_k':32,'rrf_k':60,'chunk_size':1200,'chunk_overlap':120,'context_budget_chars':8000}
    item = 'item_'+'a'*24
    gold = 'gold_'+'b'*24
    stats = {'qrels':{item:1}, 'rankings':{stage:[item] for stage in ('keyword','vector','fused','context')},
             'coverage':{'gold_lengths':{gold:20},'intervals':{gold:[[0,20]]},'threshold':0.8}}
    case_metrics = compute_case_metrics(stats,config)
    summary = aggregate_metrics([case_metrics])
    bundle.update(schema_version=2,config=config,cases=[{'case_id':'case_123456789abc','status':'passed','statistics':stats,'metrics':case_metrics}],
                  metrics=summary['metrics'],metric_counts=summary['counts'])
    bundle['manifest'].update(config_hash=hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                              gold_set_hash='c'*64,index_version='d'*64)
    bundle['runtime'] = {key:bundle['manifest'][key] for key in ('git_sha','corpus_hash','gold_set_hash','index_version')}
    bundle['runtime'].update(effective_config=copy.deepcopy(config),
        models={'chat':{'name':'qwen3.5:4b','digest':'e'*64},'embedding':{'name':'bge-m3:latest','digest':'f'*64}},
        index_counts={'documents':2,'chunks':6,'embeddings':6})
    return bundle


def test_v2_import_recomputes_and_preserves_sufficient_statistics(tmp_path):
    bundle=make_v2()
    assert normalize_bundle(bundle)['schema_version']==2
    db=tmp_path/'runs.sqlite3'
    assert import_bundle(db,bundle)['status']=='imported'
    detail=get_experiment(db,bundle['manifest']['experiment_id'])
    assert detail['validation_status']=='verified'
    assert detail['runtime']==bundle['runtime']
    assert detail['cases'][0]['statistics']==bundle['cases'][0]['statistics']
    assert detail['metrics']==bundle['metrics']
    assert len(list_experiments(db,limit=20,offset=0))==1


@pytest.mark.parametrize('part',['summary','case','sample','config','manifest','private'])
def test_v2_real_import_rejects_tampering_before_inserting(tmp_path,part):
    bundle=make_v2()
    if part=='summary': bundle['metrics']['fused.recall_at_k']=0.7
    elif part=='case': bundle['cases'][0]['metrics']['fused.precision_at_k']=0.7
    elif part=='sample': bundle['manifest']['sample_count']=2
    elif part=='config':
        bundle['config']['top_k']=9
        bundle['manifest']['config_hash']=hashlib.sha256(json.dumps(bundle['config'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    elif part=='manifest': bundle['manifest']['git_sha']='f'*40
    elif part=='private': bundle['cases'][0]['statistics']['question']='PRIVATE CANARY'
    with pytest.raises(BundleValidationError): import_bundle(tmp_path/'runs.sqlite3',bundle)


def test_legacy_rows_are_diagnostic_and_cannot_be_official_comparisons(tmp_path):
    db=tmp_path/'runs.sqlite3'
    first=make_bundle()
    second=make_bundle('89e6a1cf-913b-4caf-9023-04723b5a98a2')
    import_bundle(db,first)
    import_bundle(db,second)
    assert list_experiments(db,limit=20,offset=0)==[]
    assert len(list_experiments(db,limit=20,offset=0,include_unverified=True))==2
    assert get_experiment(db,first['manifest']['experiment_id'])['validation_status']=='unverified'
    comparison=compare_experiments(db,[first['manifest']['experiment_id'],second['manifest']['experiment_id']])
    assert comparison['comparable'] is False
    assert 'validation_status' in comparison['mismatches']
    assert comparison['metrics']=={}


def test_v2_runtime_sha_must_match_deployed_code_when_configured(tmp_path,monkeypatch):
    monkeypatch.setenv('EVAL_CENTER_CODE_SHA','f'*40)
    with pytest.raises(BundleValidationError): import_bundle(tmp_path/'runs.sqlite3',make_v2())


def test_actual_v1_database_migration_preserves_original_rows_and_digest(tmp_path):
    db=tmp_path/'runs.sqlite3'
    legacy=make_bundle()
    imported=import_bundle(db,legacy)
    # Reconstruct the exact preceding schema: all three v1 tables, no sidecar.
    with sqlite3.connect(db) as connection:
        before={table:connection.execute(f'SELECT * FROM {table}').fetchall()
                for table in ('experiments','experiment_cases','experiment_errors')}
        connection.execute('DROP TABLE experiment_verification')
        connection.execute('PRAGMA user_version=1')
    initialize_database(db)
    initialize_database(db)
    with sqlite3.connect(db) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0]==2
        for table,rows in before.items():
            assert connection.execute(f'SELECT * FROM {table}').fetchall()==rows
    assert import_bundle(db,legacy)=={'status':'unchanged','digest':imported['digest']}
    assert get_experiment(db,legacy['manifest']['experiment_id'])['validation_status']=='unverified'


def test_same_v2_id_is_idempotent_and_valid_conflicting_content_cannot_overwrite(tmp_path):
    db=tmp_path/'runs.sqlite3'
    original=make_v2()
    result=import_bundle(db,original)
    assert import_bundle(db,original)=={'status':'unchanged','digest':result['digest']}
    conflicting=copy.deepcopy(original)
    conflicting['manifest']['ended_at']='2026-09-27T10:00:04+08:00'
    normalize_bundle(conflicting)
    with pytest.raises(ExperimentConflictError): import_bundle(db,conflicting)
    detail=get_experiment(db,original['manifest']['experiment_id'])
    assert detail['digest']==result['digest']
    assert detail['metrics']==original['metrics']
    assert len(list_experiments(db,limit=20,offset=0))==1


def test_quality_counts_are_recomputed_on_import_and_do_not_expose_answers(tmp_path):
    bundle=make_v2()
    case=bundle['cases'][0]
    case['statistics']['quality']=answer_statistics(answer='PRIVATE answer [E1]',
        answer_points=[['PRIVATE']],citation_readbacks={'E1':True},answerable=True,refused=False)
    case['metrics']=compute_case_metrics(case['statistics'],bundle['config'])
    summary=aggregate_metrics([case['metrics']])
    bundle.update(metrics=summary['metrics'],metric_counts=summary['counts'])
    assert 'PRIVATE' not in json.dumps(bundle)
    assert import_bundle(tmp_path/'runs.sqlite3',bundle)['status']=='imported'
    assert bundle['metrics']['answer_point_coverage']==1
    case['metrics']['answer_point_coverage']=.2
    with pytest.raises(BundleValidationError): import_bundle(tmp_path/'other.sqlite3',bundle)


def test_source_gold_statistics_import_recomputes_mapping_and_union(tmp_path):
    bundle=make_v2()
    item='item_'+'a'*24
    gold='gold_'+'b'*24
    case=bundle['cases'][0]
    case['statistics']={'gold_sources':{gold:{'length':20,'grade':2}},
        'index_intersections':{item:{gold:[[0,15],[5,20]]}},
        'rankings':{stage:[item] for stage in ('keyword','vector','fused','context')},'coverage_threshold':.8}
    case['metrics']=compute_case_metrics(case['statistics'],bundle['config'])
    summary=aggregate_metrics([case['metrics']])
    bundle.update(metrics=summary['metrics'],metric_counts=summary['counts'])
    assert import_bundle(tmp_path/'runs.sqlite3',bundle)['status']=='imported'
    assert bundle['metrics']['fused.source_recall_at_k']==1
    case['statistics']['index_intersections'][item][gold]=[[0,10]]
    with pytest.raises(BundleValidationError): import_bundle(tmp_path/'other.sqlite3',bundle)


def test_compatible_v2_comparison_keeps_unavailable_values_and_counts(tmp_path):
    db=tmp_path/'runs.sqlite3'
    first=make_v2()
    second=make_v2()
    second['manifest']['experiment_id']='89e6a1cf-913b-4caf-9023-04723b5a98a2'
    import_bundle(db,first)
    import_bundle(db,second)
    result=compare_experiments(db,[first['manifest']['experiment_id'],second['manifest']['experiment_id']])
    assert result['comparable']
    missing=result['metrics']['fused.no_answer_retrieval_empty']
    assert missing['values']==[None,None]
    assert missing['delta_from_first']==[None,None]
    assert missing['evaluated_counts']==[0,0]
