"""Audit saved B0434 interruption without completing/resending the operation."""
import hashlib
import json
from pathlib import Path
import s8_1_score as scoring


def main():
    root = scoring.BASE.parent/'b0434-failure-evidence'
    case = next(p for p in (root/'cases').iterdir() if p.name.startswith('B-'))
    evidence = root/'executions'/case.name/'evidence'
    read = scoring.read
    ledger = read(root/'executions'/case.name/'execution.json')
    pg = read(case/'pg-final-after-stop.json')
    state = read(case/'controlled-container-after-stop.json')['state']
    stage = read(next(evidence.glob('*.stages.json')))
    driver = read(case/'driver-result.json')
    reqs = [(p,read(p)) for p in evidence.glob('*.request.json')]
    rrpath, rr = next((p,r) for p,r in reqs if r['capability']=='rerank')
    raw = read(rrpath.with_name(rrpath.name.replace('.request.json','.response.body')))
    acase = next(p for p in (root/'cases').iterdir() if p.name.startswith('A') and 'row-0434' in read(next(p.glob('api-cases/*/result.json')))['case_id'])
    aevidence = root/'executions'/acase.name/'evidence'
    arrpath = next(p for p in aevidence.glob('*.request.json') if read(p)['capability']=='rerank')
    arr = read(arrpath)
    araw = read(arrpath.with_name(arrpath.name.replace('.request.json','.response.body')))
    checks = []
    for p,r in reqs:
        bodysha = hashlib.sha256(json.dumps(r['body'],ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        row = next(x for x in ledger['entries'] if x['request_id']==r['request_id'])
        pgcall = next(x for x in pg['provider_calls'] if x['request_id']==r['request_id'])
        response = read(p.with_name(p.name.replace('.request.json','.response.json')))
        checks.append({'request_id':r['request_id'],'capability':r['capability'],'model':r['model'],
            'wire_sha_matches_capture_ledger_pg':bodysha==row['wire_sha256']==pgcall['wire_sha256'],
            'status':row['status'],'tokens':row['usage']['total_tokens'],
            'http200_response_complete':response['status_code']==200 and response['complete']})
    manifest = read(root/'MANIFEST.json')
    bad = [name for name,m in manifest.items() if not(root/name).is_file() or scoring.sha(root/name)!=m['sha256'] or(root/name).stat().st_size!=m['bytes']]
    assert not bad and len(checks)==3 and all(c['wire_sha_matches_capture_ledger_pg'] and c['status']=='SETTLED' and c['http200_response_complete'] for c in checks)
    assert state['OOMKilled'] is True and state['ExitCode']==137
    assert 'finalized_context' not in stage and not pg['generation_attempts'] and not any('/answer/' in r['request_id'] for _,r in reqs)
    report = {'schema':'s8-1-b0434-partial-independent-review/v1','case_id':driver['case_id'],'arm':'B',
        'status':'EXECUTION_INCOMPLETE_CONTAINER_OOM_NO_ANSWER_SCORE','application_question_post_attempted':True,
        'container_state':state,'checkpoint_status':driver['checkpoint_entry']['status'],'pg_turns':pg['turns'],
        'generation_attempt_count':len(pg['generation_attempts']),'final_answer_metric':None,
        'new_provider_unknown':0,'provider_calls':checks,'actual_tokens':sum(c['tokens'] for c in checks),
        'ledger_snapshot_status':ledger['status'],'driver_child_closed':driver['child_closed'],
        'closure_note':'Saved snapshot is STOP, not CLOSED. A later legitimate close receipt must be recorded separately.',
        'stage_events':[(s['stage'],s['status']) for s in stage['stages']],
        'rerank':{'actual_provider_response_complete':True,'input_query_documents_exactly_same_as_A':rr['body']==arr['body'],
            'raw_result_exactly_same_as_A':raw['results']==araw['results'],
            'top':raw['results'][0],'top_passage':rr['body']['documents'][raw['results'][0]['index']],
            'contains_coding_time_section_heading':any('coding time' in p.lower() for p in rr['body']['documents']),
            'project_factual_interpretation':'Top imports/pylearn2 code does not itself establish the Coding time training dataset. This says nothing about an unobserved final B answer.',
            'mmr_merge_context':'NOT_COMPLETED_DO_NOT_INFER_TOP1_RETENTION'},
        'B_vs_A_answer_improvement':'UNVERIFIED_NO_B_ANSWER','manifest_files':len(manifest),'manifest_failures':bad,
        'new_calls_by_review':0,'resend':'NOT_PERFORMED'}
    scoring.write_new(scoring.BASE/'b0434-partial-review.results.json',report)
    print(json.dumps({'status':report['status'],'calls':len(checks),'tokens':report['actual_tokens'],
        'same_rerank_input':report['rerank']['input_query_documents_exactly_same_as_A'],
        'same_raw_scores':report['rerank']['raw_result_exactly_same_as_A'],'ledger':ledger['status'],'manifest_files':len(manifest)},ensure_ascii=True))


if __name__=='__main__':
    main()
