"""Reproduce the S8.6.3 audit from frozen local ECS40 records. No network/DB."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import html
import json
from pathlib import Path

from scripts.s8_6_3_fixed_evidence import canonical, digest

FROZEN_SHA = '79ecc4f19dd1d08b415270387be8df7003627d8005c14e906c21fc90665943a7'
ROOT = Path(__file__).resolve().parents[1]
PACK_CASES = ('miracl-zh-91294#0',
    'longbench-zh-multifieldqa_zh:51abd2060651563dfb6982a9e5380c6dfdf7c4917056feed',
    'longbench-zh-multifieldqa_zh:1a6c19fed6361cbfbfd0df3fb7ca1e01f2a2ffa0da9903bc',
    'mmlongbench-doc-mmlongbench-doc:2ff6aa9237fc777b6627dc57a486e9225ac5fb86:row-0687')


def text_sha(text):
    return hashlib.sha256(text.encode('utf8')).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


def state_of(case):
    states = [a['record'].get('state', {}) for a in case['strategy_audit']]
    # Root progress snapshots during serial child retrieval are empty. The
    # completed Context/generation snapshot carries the copied child trace.
    return max(states, key=lambda s: sum(len(s.get(k, [])) for k in
        ('search_results','rerank_results','merge_results','evidence_trace','strategy_trace')))


def final_wire(case):
    attempts = [w for w in case['provider_evidence'] if '/answer/' in w['request']['request_id']]
    return max(attempts, key=lambda w: int(w['request']['request_id'].split('/answer/')[1].split('/')[0]))


def messages_text(body):
    return '\n'.join(html.unescape(m['content']) for m in body['messages'] if isinstance(m['content'], str))


def identity(row):
    return {k:row.get(k) for k in ('id','knowledge_base_id','knowledge_id','chunk_type',
        'chunk_index','start_at','end_at','score','content_revision','content_rewritten','sub_chunk_ids')} | {
        'source_version':row.get('metadata',{}).get('source_version'),
        'chunk_source_version':row.get('chunk_metadata',{}).get('source_version'),
        'content_sha256':text_sha(row['content']), 'characters':len(row['content'])}


def stage_rows(state, wire_text):
    raw = next((t for t in state.get('evidence_trace',[]) if t['stage']=='rerank_raw'),{})
    scores = {raw['input_ids'][r['index']]:r['score'] for r in raw.get('response',[])}
    groups = {}
    for key in ('search_results','rerank_results','merge_results'):
        groups[key] = [identity(row) | {'bge_score':scores.get(row['id']),
            'intact_body_in_actual_messages':row['content'] in wire_text}
            for row in state.get(key,[])]
    return groups


def matrix(case, prior, annotation):
    state = state_of(case); wire = final_wire(case)
    text = messages_text(wire['request']['body'])
    groups = stage_rows(state,text)
    target = annotation['target_chunk_id']
    source = next((r for r in state.get('search_results',[]) if r['id']==target),None)
    traces = state.get('evidence_trace',[])
    raw_qu = next((a['record']['fields']['content'] for a in case['strategy_audit']
                   if isinstance(a['record'].get('fields',{}).get('content'),str)),None)
    try: proposed = json.loads(raw_qu) if raw_qu else None
    except ValueError: proposed = {'parse':'INVALID_JSON','raw_sha256':text_sha(raw_qu)}
    counts = {a['record']['stage']:a['record']['fields'].get('candidate_count')
              for a in case['strategy_audit'] if a['record'].get('status')=='completed'
              and 'candidate_count' in a['record'].get('fields',{})}
    if source:
        presence = {'authorized_original':'OBSERVED_SCOPED_SEARCH_ORIGINAL',
            'target_role':annotation.get('target_role','NECESSARY_ORIGINAL_OR_BEST_AVAILABLE_SOURCE'),
            'search':True, 'rerank':target in {r['id'] for r in state['rerank_results']},
            'merge_before_source_projection':'NOT_INDEPENDENTLY_SAVED; candidate count and later trace only',
            'source_projection_body':any(source['content'] in r['content'] for r in state['merge_results']),
            'context_body':any(source['content'] in r['content'] for r in state['merge_results']),
            'actual_provider_body':source['content'] in text}
    else:
        presence = {'authorized_original':'PDF_REFERENCE_ONLY; parsed full source NOT_RECORDED',
            'search_required_answer':'NOT_OBSERVED_IN_SAVED_CANDIDATES',
            'rerank_required_answer':'NOT_OBSERVED', 'merge_required_answer':'NOT_RECORDED',
            'source_projection_required_answer':False,'context_required_answer':False,
            'actual_provider_required_answer':False}
    return {**annotation, 'q0':case['question'], 'dataset':case['dataset'],
        'gold_nature':('RETRIEVAL_QRELS_ONLY_PROJECT_SOURCE_REVIEW' if case['dataset'] in ('scifact','miracl-zh')
                       else 'FROZEN_NATIVE_ANSWER_PROJECT_REVIEW_NOT_OFFICIAL_JUDGE'),
        'historical_core_review':prior['core_answer_review'],
        'secondary_review':prior['secondary_review'], 'answer':case['answer'],
        'answer_sha256':case['answer_sha256'],
        'query_understanding':{'proposed':proposed,'raw_response_sha256':text_sha(raw_qu) if raw_qu else None,
            'effective_type':state.get('query_type'),'effective_query':state.get('rewrite_query'),
            'trace':state.get('query_trace'), 'plan':state.get('plan')},
        'necessary_source_presence':presence, 'stage_candidate_counts':counts,
        'stage_sources':groups, 'evidence_trace':[t for t in traces if t['stage']!='rerank_raw'],
        'strategy_trace':state.get('strategy_trace'), 'target_observation':next(
            (r for r in groups['search_results'] if r['id']==target),None),
        'threshold_contract':{'primary':0.3,'empty_set_best_candidate_floor':0.15,
            'warning':'Only the best returned candidate may use the empty-set floor; thresholds unchanged.'},
        'actual_generation':{'model':wire['request']['model'], 'request_id':wire['request']['request_id'],
            'body_sha256':digest(wire['request']['body']), 'messages_sha256':digest(wire['request']['body']['messages']),
            'request_artifact':wire['request_artifact'],
            'all_message_contents_text':all(isinstance(m['content'],str) for m in wire['request']['body']['messages']),
            'source_context':case.get('source_context_review'),
            'new_model_verification':'NOT_RUN'}}


def citation_history(case, frozen):
    state=state_of(case); id=frozen['anchor_id']; wire=final_wire(case)
    text=messages_text(wire['request']['body'])
    search=next(r for r in state['search_results'] if r['id']==id)
    ranked=next(r for r in state['rerank_results'] if r['id']==id)
    context=next(r for r in state['merge_results'] if r['id']==id)
    ref=next(r for r in case['references'] if r['id']==id)
    history_artifact=next(a for a in case['artifact_manifest'] if a['path'].endswith('history.json'))
    raw=Path(history_artifact['path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==history_artifact['sha256']
    history=json.loads(raw)['body']['data']
    href=next(r for m in history if m['role']=='assistant' for r in m['knowledge_references'] if r['id']==id)
    pg=case['reconciliation_before_close']['pg']
    pref=next(r for m in pg['messages'] if m['role']=='assistant' for r in m['knowledge_references'] if r['id']==id)
    srefs=[r for f in case['sse_frames'] for r in f.get('knowledge_references',[]) if r['id']==id]
    def key(r):return r['id'],r['content'],r['start_at'],r['end_at'],r['knowledge_id'],r['knowledge_base_id']
    assert key(ref)==key(href)==key(pref) and any(key(r)==key(ref) for r in srefs)
    return {'case_id':case['case_id'],'anchor_id':id, 'scope':wire['request']['scope'],
        'tenant_ids_from_saved_pg':sorted({m['tenant_id'] for m in pg['messages'] if 'tenant_id' in m}),
        'search':identity(search),'rerank':identity(ranked),
        'merge':'extend replay reconstructs exact historical Context; independent intermediate body snapshot NOT_RECORDED',
        'source_projection_and_context':identity(context),
        'generation_request':'Evidence.from_result and coordinator projection verified by offline test; separate historical object NOT_RECORDED',
        'actual_provider':{'body_sha256':digest(wire['request']['body']),'composite_body_present':context['content'] in text},
        'registry':'historical wire maps cN to composite ID; offline replay exercises real SourceRegistry',
        'historical_reference':identity(ref), 'sse_pg_history_references_identical':True,
        'history_artifact':history_artifact, 'source_http':frozen['source_http'],
        'before':'FAIL_ID_BODY_RANGE_MISMATCH', 'after':'OFFLINE_EXACT_ORIGINALS_PASS',
        'restored_source_ids':[r['id'] for r in frozen['reranked_originals']],
        'restored_source_identities':[{k:v for k,v in identity(r).items() if k != 'score'}
                                      for r in frozen['reranked_originals']],
        'score_contract':'Original anchor admitted score retained; additional readback rows keep repository score=0. Historical Rerank results unchanged.',
        'optional_window_decisions':frozen['optional_windows'],
        'new_http_or_provider_run':'NOT_RUN'}


def package(cases, prior, output):
    manifest={'schema':'s8.6.3-fixed-evidence/v1','status':'OFFLINE_ONLY_NEW_AUTHORIZATION_REQUIRED',
        'snapshot_sha256':FROZEN_SHA,'reviewer':'AGENT_NOT_OWNER',
        'requested_pair':{'cheap':'Qwen/Qwen3.5-4B','expensive':'deepseek-flash',
            'max_output_tokens_each':2048,'thinking':False,'model_token_counts_need_not_match':True},
        'execution':'Use load_inputs/render_pair with existing Registry, SendGate and LocalExecution in an authorized job; no historical IDs or grants accepted here.',
        'cases':[]}
    scoring=[]
    for i,id in enumerate(PACK_CASES,1):
        case=cases[id]; w=final_wire(case); body=deepcopy(w['request']['body'])
        assert digest(body)==w['recomputed_wire_sha256']
        filename=f'requests/{i:02d}.json'; write(output/filename,body)
        manifest['cases'].append({'case_id':id,'role':'D_FAILURE' if i<=2 else 'CORRECT_CONTROL',
            'request_file':filename,'body_sha256':digest(body),'messages_sha256':digest(body['messages']),
            'system_sha256':digest([m for m in body['messages'] if m['role']=='system']),
            'user_sha256':digest([m for m in body['messages'] if m['role']=='user']),
            'parameters':{k:v for k,v in body.items() if k!='messages'},
            'historical_request_id':w['request']['request_id'],'historical_request_artifact':w['request_artifact'],
            'gold_injection_check':'PASS_EXACT_FROZEN_BODY_EQUALITY; scoring metadata read only after request copy',
            'citation_identity_limit':'Control keeps historical original wire including old composite identity; not fresh repaired HTTP proof' if i>2 else ''})
        scoring.append({'case_id':id,'historical_answer':case['answer'],
            'historical_verdict':prior[id]['final_answer_correct'],'review':prior[id]['core_answer_review'],
            'secondary':prior[id]['secondary_review'], 'scoring_only':True})
    write(output/'manifest.json',manifest);write(output/'scoring_only.json',scoring)
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    raw=args.snapshot.read_bytes()
    assert hashlib.sha256(raw).hexdigest()==FROZEN_SHA,'FROZEN_SNAPSHOT_CHANGED'
    data=json.loads(raw); cases={c['case_id']:c for c in data['cases']}
    prior_path=ROOT/'docs/evaluation/S8_6_2_R3_ECS40_ACCEPTANCE.results.json'
    prior={c['case_id']:c for c in json.loads(prior_path.read_text(encoding='utf8'))['cases']}
    annotations=json.loads((ROOT/'docs/evaluation/evidence/s8_6_3/failure_layers.annotations.json').read_text(encoding='utf8'))
    rows=[matrix(cases[a['case_id']],prior[a['case_id']],a) for a in annotations]
    frozen=json.loads((ROOT/'tests/fixtures/s8_6_3/ecs40_citation_identity.json').read_text(encoding='utf8'))['cases']
    citations=[citation_history(cases[f['case_id']],f) for f in frozen]
    pkg=package(cases,prior,args.output.parent/'evidence/s8_6_3/fixed_evidence')
    result={'schema':'s8.6.3-source-identity-failure-layers/v1','status':'OFFLINE_REPAIRED_REAL_NOT_RUN',
        'baseline':'d5b685046b7338250e011a518f924671439a0cd0',
        'source_fix_commit':'c13eecda61ae6ac8c636e56b9a440898d934ab0e',
        'source_fix_commits':['922b5c3884693cb520378a2c18f90cb177916536',
                              'c13eecda61ae6ac8c636e56b9a440898d934ab0e'],
        'branch':'codex/s8-6-adaptive-retrieval-20261021',
        'provenance':{'frozen_snapshot':str(args.snapshot),'sha256':FROZEN_SHA,
            'previous_report_sha256':hashlib.sha256(prior_path.read_bytes()).hexdigest(),
            'historical_source':'5585f41dab1103738bd2aecd7161ded08598e198',
            'current_code_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in (
                'backend/weknora_port/retrieval/source_evidence.py',
                'backend/weknora_port/retrieval/search.py',
                'backend/weknora_port/storage/knowledge.py',
                'scripts/s8_6_3_fixed_evidence.py')}},
        'new_usage':{'provider_attempts':0,'settlements':0,'input_tokens':0,'output_tokens':0,'unknown':0,
            'database_calls':0,'ecs_operations':0},
        'historical_only':{'http_completed':38,'protected_not_run':2,'settled_calls':158,'tokens':364593,
            'counts_not_new_measurement':True,'foreground_ecs40':True,'detached_real_provider_batch':'NOT_RUN'},
        'review_counts':dict(Counter(r['historical_verdict'] for r in rows)),
        'main_layers':dict(Counter(r['main_layer'] for r in rows)),
        'citation_reproductions':citations,'failure_layers':rows,
        'fixed_evidence_package':{'path':'evidence/s8_6_3/fixed_evidence','cases':len(pkg['cases']),
            'D_cases':2,'correct_controls':2,'real_comparison':'NOT_RUN','gold_isolated':True},
        'offline_validation':json.loads((ROOT/'docs/evaluation/evidence/s8_6_3/validation.json').read_text(encoding='utf8')),
        'limits':['Agent closed-set diagnosis, not Owner approval, new unseen-set accuracy, or official dataset scoring.',
            'Merge bodies before source projection were not separately saved by the serial child observer.',
            'Full parsed MML source is absent from these records; row0434 root A versus B unresolved.',
            'No new model answer, live PostgreSQL/SSE/HTTP readback or production availability is claimed.']}
    write(args.output,result)
    print(json.dumps({'status':result['status'],'cases':len(rows),'citation_reproductions':len(citations),
        'layers':result['main_layers'],'new_provider_calls':0,'comparison_inputs':4},ensure_ascii=False))


if __name__=='__main__':main()
