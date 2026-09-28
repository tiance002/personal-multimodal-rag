"""Strict privacy projection and independent consistency validation for v2."""
from __future__ import annotations

import copy
import hashlib
import os
import re
from typing import Any

from eval_center.contracts import (_object, _exact_fields, _fail, _canonical_json,
    _HASH_RE, _CASE_ID_RE, _MANIFEST_FIELDS, _MAX_CASES, _MAX_BUNDLE_BYTES, normalize_bundle)
from eval_center.contracts import _ERROR_CODES, _STAGES, _MAX_ERRORS
from eval_center.verification import verify_report, ExperimentInvalidError
from eval_center.quality import quality_metrics
from eval_center.source_metrics import OPTIONAL

_ITEM = re.compile(r'item_[0-9a-f]{24}\Z')
_GOLD = re.compile(r'gold_[0-9a-f]{24}\Z')
_MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}\Z')
_CONFIG = {'top_k','candidate_k','rrf_k','chunk_size','chunk_overlap','context_budget_chars'}
_V2_ERROR_CODES=_ERROR_CODES|{'VECTOR_UNAVAILABLE','NO_CANDIDATES','LOW_COVERAGE','SECTION_TRUNCATED',
    'SEMANTIC_MISMATCH','VERSION_CONFLICT','INDEX_ERROR','NO_EVIDENCE_AFTER_RETRY','INVALID_CITATION',
    'ANSWER_MODEL_UNAVAILABLE','PARTIAL_EVIDENCE','QUERY_MODEL_UNAVAILABLE','QUERY_MODEL_TIMEOUT'}


def _validate_source_ids(stats):
    required={'gold_sources','index_intersections','rankings','coverage_threshold'}
    if not required<=set(stats) or set(stats)-required-OPTIONAL: _fail('invalid_statistics')
    gold=_object(stats['gold_sources'])
    if any(not _GOLD.fullmatch(key) for key in gold): _fail('private_statistics')
    index=_object(stats['index_intersections'])
    if len(index)>100000 or any(not _ITEM.fullmatch(key) for key in index): _fail('private_statistics')
    for intersections in index.values():
        if any(not _GOLD.fullmatch(key) for key in _object(intersections)): _fail('private_statistics')
    rankings=_object(stats['rankings'])
    _exact_fields(rankings,{'keyword','vector','fused','context'})
    for ranking in rankings.values():
        if not isinstance(ranking,list) or len(ranking)>10000 or any(not isinstance(key,str) or not _ITEM.fullmatch(key) for key in ranking): _fail('private_statistics')
    attempts=stats.get('retrieval_attempts',[])
    if not isinstance(attempts,list) or len(attempts)>3: _fail('invalid_statistics')
    for attempt in attempts:
        for ranking in _object(attempt).values():
            if not isinstance(ranking,list) or any(not isinstance(key,str) or not _ITEM.fullmatch(key) for key in ranking): _fail('private_statistics')


def normalize_v2(raw: dict[str, Any]) -> dict[str, Any]:
    _exact_fields(raw, {'schema_version','manifest','config','runtime','metrics','metric_counts','cases','errors'})
    config = _object(raw['config'])
    _exact_fields(config,_CONFIG)
    if any(type(value) is not int or not 0 <= value <= 100000 for value in config.values()):
        _fail('invalid_config')
    if any(config[key] <= 0 for key in _CONFIG-{'chunk_overlap'}) or config['chunk_overlap'] >= config['chunk_size']:
        _fail('invalid_config')
    manifest = _object(raw['manifest'])
    _exact_fields(manifest,_MANIFEST_FIELDS|{'gold_set_hash','index_version'})
    for key in ('gold_set_hash','index_version'):
        if not isinstance(manifest[key],str) or not _HASH_RE.fullmatch(manifest[key]): _fail('invalid_manifest')
    if not isinstance(manifest['git_sha'],str) or not re.fullmatch('[0-9a-f]{40}',manifest['git_sha']): _fail('invalid_manifest')
    config_hash = hashlib.sha256(_canonical_json(config).encode()).hexdigest()
    if manifest['config_hash'] != config_hash: _fail('config_hash_mismatch')
    # Reuse the proven v1 metadata validator, never its submitted metric claims.
    legacy_config={key:config[key] for key in ('top_k','rrf_k','chunk_size','chunk_overlap')}
    legacy_manifest={key:manifest[key] for key in _MANIFEST_FIELDS}
    legacy_manifest.update(sample_count=0,config_hash=hashlib.sha256(_canonical_json(legacy_config).encode()).hexdigest())
    normalize_bundle({'schema_version':1,'manifest':legacy_manifest,'config':legacy_config,
                      'metrics':{'count':0},'cases':[],'errors':[]})
    runtime = _object(raw['runtime'])
    _exact_fields(runtime,{'effective_config','git_sha','corpus_hash','gold_set_hash','index_version','models','index_counts'})
    if runtime['effective_config'] != config: _fail('effective_config_mismatch')
    for key in ('git_sha','corpus_hash','gold_set_hash','index_version'):
        if runtime[key] != manifest[key]: _fail('manifest_runtime_mismatch')
    deployed=os.environ.get('EVAL_CENTER_CODE_SHA')
    if deployed and deployed != manifest['git_sha']: _fail('deployed_code_mismatch')
    models=_object(runtime['models'])
    _exact_fields(models,{'chat','embedding'})
    for model in models.values():
        _exact_fields(_object(model),{'name','digest'})
        if not isinstance(model['name'],str) or not _MODEL.fullmatch(model['name']): _fail('invalid_model_identity')
        if not isinstance(model['digest'],str) or not _HASH_RE.fullmatch(model['digest']): _fail('invalid_model_identity')
    counts=_object(runtime['index_counts'])
    _exact_fields(counts,{'documents','chunks','embeddings'})
    if any(type(value) is not int or not 0 <= value <= 10000000 for value in counts.values()): _fail('invalid_index_counts')
    if counts['documents'] < 1 or counts['chunks'] != counts['embeddings']: _fail('incomplete_index')
    cases=raw['cases']
    if not isinstance(cases,list) or not 1 <= len(cases) <= _MAX_CASES: _fail('invalid_cases')
    for case in cases:
        _exact_fields(_object(case),{'case_id','status','statistics','metrics'})
        if not isinstance(case['case_id'],str) or not _CASE_ID_RE.fullmatch(case['case_id']): _fail('invalid_case_id')
        if case['status'] not in ('passed','failed','not_evaluated'): _fail('invalid_case')
        stats=_object(case['statistics'])
        if 'gold_sources' in stats:
            _validate_source_ids(stats)
            continue  # all values, quality counts and bounds validated by recomputation
        required={'qrels','rankings','coverage'}
        if not required <= set(stats) or set(stats)-required-{'quality','telemetry'}: _fail('invalid_statistics')
        if 'quality' in stats:
            try: quality_metrics(stats['quality'])
            except (ValueError,TypeError,KeyError): _fail('invalid_quality_statistics')
        qrels=_object(stats['qrels'])
        if any(not _ITEM.fullmatch(key) for key in qrels): _fail('private_statistics')
        rankings=_object(stats['rankings'])
        _exact_fields(rankings,{'keyword','vector','fused','context'})
        for ranking in rankings.values():
            if not isinstance(ranking,list) or len(ranking)>10000 or any(not isinstance(key,str) or not _ITEM.fullmatch(key) for key in ranking): _fail('private_statistics')
        coverage=_object(stats['coverage'])
        _exact_fields(coverage,{'gold_lengths','intervals','threshold'})
        for key in ('gold_lengths','intervals'):
            values=_object(coverage[key])
            if any(not _GOLD.fullmatch(name) for name in values): _fail('private_statistics')
    errors=raw['errors']
    if not isinstance(errors,list) or len(errors)>_MAX_ERRORS: _fail('invalid_errors')
    case_ids={case['case_id'] for case in cases}
    for error in errors:
        _exact_fields(_object(error),{'case_id','stage','error_code'})
        if (not isinstance(error['case_id'],str) or error['case_id'] not in case_ids
            or not isinstance(error['stage'],str) or error['stage'] not in _STAGES
            or not isinstance(error['error_code'],str) or error['error_code'] not in _V2_ERROR_CODES): _fail('invalid_errors')
    try: summary=verify_report(raw)
    except ExperimentInvalidError as exc: _fail(exc.code)
    result=copy.deepcopy(raw)
    # Store only recomputed values, even if equivalent claims used rounding.
    result['metrics']=summary['metrics']
    result['metric_counts']=summary['counts']
    if len(_canonical_json(result).encode()) > _MAX_BUNDLE_BYTES: _fail('bundle_too_large')
    return result
