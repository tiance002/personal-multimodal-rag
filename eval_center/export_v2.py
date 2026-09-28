"""Project a full local runtime report into anonymous sufficient statistics.

Claims are retained and validated, never repaired during export. The projection
contains no prompt, question, answer, source text, path or provider endpoint.
"""
from __future__ import annotations

import copy
import hashlib

from eval_center.contracts import BundleValidationError, _MANIFEST_FIELDS, _canonical_json, normalize_bundle
from eval_center.contracts_v2 import _CONFIG
from eval_center.source_metrics import OPTIONAL


def _select(value, fields):
    if not isinstance(value, dict) or not fields <= value.keys():
        raise BundleValidationError('missing_runtime_statistics')
    return {key:copy.deepcopy(value[key]) for key in fields}


def _opaque(value, prefix, width):
    if not isinstance(value,str) or not value or len(value)>4096:
        raise BundleValidationError('invalid_statistics_id')
    return prefix + hashlib.sha256(value.encode('utf-8')).hexdigest()[:width]


def build_v2_bundle(report, manifest, config):
    clean_config=_select(config,_CONFIG)
    clean_manifest=_select(manifest,_MANIFEST_FIELDS|{'gold_set_hash','index_version'})
    clean_manifest['environment']=_select(manifest['environment'],{'os_family','python_version','architecture'})
    if report.get('dataset_version') != clean_manifest['dataset_version']:
        raise BundleValidationError('dataset_version_mismatch')
    clean_manifest['config_hash']=hashlib.sha256(_canonical_json(clean_config).encode()).hexdigest()
    runtime=_select(report.get('runtime'),{'effective_config','git_sha','corpus_hash','gold_set_hash',
                        'index_version','models','index_counts'})
    runtime['effective_config']=_select(runtime['effective_config'],_CONFIG)
    # Keep nested structures strict so unrecognized data never silently passes.
    raw_cases=report.get('cases')
    if not isinstance(raw_cases,list):
        raise BundleValidationError('invalid_cases')
    cases=[]
    for raw_case in raw_cases:
        case=_select(raw_case,{'case_id','status','statistics','metrics'})
        case['case_id']=_opaque(case['case_id'],'case_',12)
        stats=case['statistics']
        if isinstance(stats,dict) and 'gold_sources' in stats:
            required={'gold_sources','index_intersections','rankings','coverage_threshold'}
            if not required<=set(stats) or set(stats)-required-OPTIONAL:
                raise BundleValidationError('invalid_statistics')
            try:
                stats['gold_sources']={_opaque(key,'gold_',24):value for key,value in stats['gold_sources'].items()}
                stats['index_intersections']={_opaque(key,'item_',24):{
                    _opaque(identity,'gold_',24):spans for identity,spans in intersections.items()}
                    for key,intersections in stats['index_intersections'].items()}
                stats['rankings']={stage:[_opaque(key,'item_',24) for key in ranking]
                    for stage,ranking in stats['rankings'].items() if isinstance(ranking,list)}
                if 'retrieval_attempts' in stats:
                    stats['retrieval_attempts']=[{stage:[_opaque(key,'item_',24) for key in ranking]
                        for stage,ranking in attempt.items()} for attempt in stats['retrieval_attempts']]
            except (TypeError,AttributeError):
                raise BundleValidationError('invalid_statistics') from None
            cases.append(case)
            continue
        required={'qrels','rankings','coverage'}
        if not isinstance(stats,dict) or not required <= set(stats) or set(stats)-required-{'quality','telemetry'}:
            raise BundleValidationError('invalid_statistics')
        qrels=stats['qrels']
        rankings=stats['rankings']
        coverage=stats['coverage']
        if not isinstance(qrels,dict) or not isinstance(rankings,dict) or not isinstance(coverage,dict):
            raise BundleValidationError('invalid_statistics')
        if set(coverage)!={'gold_lengths','intervals','threshold'}:
            raise BundleValidationError('invalid_statistics')
        stats['qrels']={_opaque(key,'item_',24):value for key,value in qrels.items()}
        projected_rankings={}
        for stage,ranking in rankings.items():
            if not isinstance(ranking,list):
                raise BundleValidationError('invalid_statistics')
            projected_rankings[stage]=[_opaque(key,'item_',24) for key in ranking]
        stats['rankings']=projected_rankings
        for field in ('gold_lengths','intervals'):
            if not isinstance(coverage[field],dict):
                raise BundleValidationError('invalid_statistics')
            coverage[field]={_opaque(key,'gold_',24):value for key,value in coverage[field].items()}
        cases.append(case)
    errors=[]
    if not isinstance(report.get('errors',[]),list): raise BundleValidationError('invalid_errors')
    for raw_error in report.get('errors',[]):
        error=_select(raw_error,{'case_id','stage','error_code'})
        error['case_id']=_opaque(error['case_id'],'case_',12)
        errors.append(error)
    bundle={'schema_version':2,'manifest':clean_manifest,'config':clean_config,'runtime':runtime,
            'metrics':copy.deepcopy(report.get('metrics')),
            'metric_counts':copy.deepcopy(report.get('metric_counts')),
            'cases':cases,'errors':errors}
    return normalize_bundle(bundle)
