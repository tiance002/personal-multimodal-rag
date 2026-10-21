"""Audit only new B/C saved receipts; A9 quality scores are reused unchanged."""
from collections import defaultdict
import hashlib
import html
import json
from pathlib import Path
import re

import s8_1_score as scoring
from a_baseline_review import audit_case, entries, state

BASE = scoring.BASE
ROOT = BASE.parent/'final-evidence'


def canon(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def sha_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def load_case(folder):
    ev = ROOT/'executions'/folder.name/'evidence'
    requests = [(p,scoring.read(p)) for p in ev.glob('*.request.json')]
    qu_path, qu = next((p,r) for p,r in requests if r['request_id'].endswith('query_rewrite'))
    rr_path, rr = next((p,r) for p,r in requests if r['capability']=='rerank')
    qu_raw = scoring.read(qu_path.with_name(qu_path.name.replace('.request.json','.response.body')))
    rr_raw = scoring.read(rr_path.with_name(rr_path.name.replace('.request.json','.response.body')))
    stage = scoring.read(next(ev.glob('*.stages.json')))
    retrieval = scoring.read(next(ev.glob('*.retrieval.json')))
    return {'requests':requests,'qu':qu,'qu_raw':qu_raw,'rr':rr,'rr_raw':rr_raw,'stage':stage,'retrieval':retrieval}


def compare_inputs(a,b):
    outputs = {}
    for name, data in [('A',a),('experiment',b)]:
        output = data['qu_raw']['choices'][0]['message']['content']
        outputs[name] = {'raw':output,'sha256':sha_text(output)}
    comparisons = {}
    for channel in ('vector','bm25'):
        left = next(x for x in a['retrieval']['channels'] if x['channel']==channel)
        right = next(x for x in b['retrieval']['channels'] if x['channel']==channel)
        lid,rid = [r['chunk_id'] for r in left['rows']],[r['chunk_id'] for r in right['rows']]
        lcontent = {r['chunk_id']:sha_text(r['content']) for r in left['rows']}
        rcontent = {r['chunk_id']:sha_text(r['content']) for r in right['rows']}
        common=set(lid)&set(rid)
        comparisons[channel]={'A_count':len(lid),'experiment_count':len(rid),'ordered_ids_equal':lid==rid,
            'A_ordered_chunk_ids':lid,'experiment_ordered_chunk_ids':rid,
            'id_set_equal':set(lid)==set(rid),'shared_content_sha_equal':all(lcontent[i]==rcontent[i] for i in common),
            'content_sha_by_id_A':lcontent,'content_sha_by_id_experiment':rcontent,
            'raw_params_A':left['params'],'raw_params_experiment':right['params']}
    left,right=a['retrieval']['returned_results'],b['retrieval']['returned_results']
    lids,rids=[r['id'] for r in left],[r['id'] for r in right]
    docs_a,docs_b=a['rr']['body']['documents'],b['rr']['body']['documents']
    ascores={r['index']:r['relevance_score'] for r in a['rr_raw']['results']}
    bscores={r['index']:r['relevance_score'] for r in b['rr_raw']['results']}
    delta=[abs(ascores[i]-bscores[i]) for i in ascores.keys()&bscores.keys()] if docs_a==docs_b else []
    return {'query_understanding':outputs,'qu_input_messages_and_options_equal':a['qu']['body']==b['qu']['body'],
        'qu_output_exact_equal':outputs['A']['raw']==outputs['experiment']['raw'],
        'rewrite_query_A':a['retrieval']['params']['query_text'],'rewrite_query_experiment':b['retrieval']['params']['query_text'],
        'observed_expansion':{'A_hybrid_search_invocations':len(list(a['stage'].get('retrieval_calls',[]))) or 1,
            'experiment_hybrid_search_invocations':len(list(b['stage'].get('retrieval_calls',[]))) or 1,
            'A_channel_queries':[c['params'].get('query') for c in a['retrieval']['channels']],
            'experiment_channel_queries':[c['params'].get('query') for c in b['retrieval']['channels']],
            'additional_expansion_provider_requests_observed':False,'scope':'Saved evidence has one retrieval capture/query and no expansion request; do not infer unrecorded output.'},
        'channels':comparisons,'search_top30_ordered_ids_equal':lids==rids,'A_search_ids':lids,'experiment_search_ids':rids,
        'search_top30_content_sha_equal':{r['id']:sha_text(r['content']) for r in left}=={r['id']:sha_text(r['content']) for r in right},
        'rerank_entire_body_equal':a['rr']['body']==b['rr']['body'],
        'rerank_input_documents_sha_A':sha_text(canon(docs_a)),'rerank_input_documents_sha_experiment':sha_text(canon(docs_b)),
        'raw_rerank_scores_by_index_A':ascores,'raw_rerank_scores_by_index_experiment':bscores,
        'same_input_exact_score_count':sum(v==0 for v in delta) if delta else None,
        'same_input_max_absolute_score_delta':max(delta) if delta else None,
        'strict_retrieval_input_comparability':a['rr']['body']==b['rr']['body'] and lids==rids,
        'causal_warning':'Same original question/model roles does not guarantee same Rewrite/retrieval input. Record observed differences before attributing effects.'}


def graph_review(data,case_report,graph_snapshot):
    st=data['stage']
    snapshots=[e for e in entries(st) if state(e).get('graph_trace')]
    traces=state(snapshots[-1])['graph_trace'] if snapshots else []
    records={r['chunk_id']:r for r in graph_snapshot['records']}
    edges={e['id']:(r,e) for r in graph_snapshot['records'] for e in r['edges']}
    details=[]
    wire=html.unescape(scoring.textual_messages({'body':{'messages':case_report['final_messages']}}))
    for trace in traces:
        quotes=[]
        for eid in trace['edge_ids']:
            source,edge=edges[eid]
            assert source['text_hash_valid'] and source['current_chunk_text_equal'] and source['version_valid'] and edge['quote_offsets_valid']
            assert source['source_content'][edge['start']:edge['end']]==edge['quote']
            readback=next((x for x in case_report['context_chunks'] if x['chunk_id']==source['chunk_id']),None)
            quotes.append({'edge_id':eid,'predicate':edge['predicate'],'quote':edge['quote'],
                'source_chunk_id':source['chunk_id'],'source_document_id':source['document_id'],
                'source_version':source['source_version'],'source_content':source['source_content'],
                'quote_original_offsets_valid':True,'source_chunk_retained_in_context':readback is not None,
                'source_quote_in_actual_wire':scoring.textual_messages({'body':{'messages':[{'content':edge['quote']}]}}).strip() in wire,
                'source_readback_same_version':bool(readback and readback['readback_identity_version_matches']),
                'inferred_endpoint_labels':'NOT_CAPTURED_READINESS_HAS_PREDICATE_AND_QUOTE_ONLY; no invented entity IDs'})
        details.append({'trace':trace,'source_bound_edges':quotes})
    return {'graph_completed_fields':[e['fields'] for e in entries(st) if e['stage']=='graph' and e['status']=='completed'],
        'fusion_completed_fields':[e['fields'] for e in entries(st) if e['stage']=='graph_fusion' and e['status']=='completed'],
        'paths':details,'graph_new_extraction_calls':0,'supporting_fact_entailment':'Requires sentence/fact review separate from valid quote offsets'}


def main():
    frozen={c['identity']['case_id']:c for c in scoring.read(scoring.GOLD)['cases']}
    a9=scoring.read(BASE/'a9-independent-audit-v2.json')
    afinal=scoring.read(BASE/'a9-independent-final.results.json')
    graph_snapshot=scoring.read(BASE.parent/'graph-readiness.stdout')
    folders=list((ROOT/'cases').iterdir())
    complete=[]
    for folder in folders:
        if folder.name.startswith('A-') or not list(folder.glob('api-cases/*/result.json')):
            continue
        res=scoring.read(next(folder.glob('api-cases/*/result.json')))
        report=audit_case(ROOT,folder,frozen[res['case_id']])
        assert report['completed'] and report['raw_sse_pg_history_result_equal'] and report['same_run_identity']
        assert not report['readback_failures'] and report['ledger_status']=='CLOSED' and report['context_bound_to_actual_wire']
        assert all(x['source_hash_matches_metadata'] and x['readback_identity_version_matches'] for x in report['context_chunks'])
        assert all(c['wire_hash_matches_ledger_pg'] and c['scope_matches'] and c['settled'] and c['response_http200'] and c['protocol_complete'] for c in report['provider_calls'])
        baseline=next(r for r in a9['cases'] if r['case_id']==res['case_id'])
        apath=next(p for p in folders if p.name.startswith('A-') and scoring.read(next(p.glob('api-cases/*/result.json')))['case_id']==res['case_id'])
        a,b=load_case(apath),load_case(folder)
        compare=compare_inputs(a,b)
        if res['arm']=='C':
            report['graph_review']=graph_review(b,report,graph_snapshot)
        added_ids=set(report['context']['result_ids'])-set((baseline.get('context') or {}).get('result_ids',[]))
        removed_ids=set((baseline.get('context') or {}).get('result_ids',[]))-set(report['context']['result_ids'])
        report['A_comparison']={'inputs':compare,'A_answer':baseline['answer'],'A_answer_metrics':baseline['answer_metric'],
            'A_context_chars':len((baseline.get('context') or {}).get('rendered_contexts','')),
            'experiment_context_chars':len(report['context']['rendered_contexts']),
            'A_actual_wire_user_chars':sum(len(x['content']) for x in baseline['final_messages'] if x['role']=='user'),
            'experiment_actual_wire_user_chars':sum(len(x['content']) for x in report['final_messages'] if x['role']=='user'),
            'A_chunks':len(baseline['context_chunks']),'experiment_chunks':len(report['context_chunks']),
            'added_chunk_ids':sorted(added_ids),'removed_chunk_ids':sorted(removed_ids),
            'A_tokens':baseline['actual_tokens'],'experiment_tokens':report['actual_tokens'],
            'A_post_seconds':baseline['elapsed_seconds'],'experiment_post_seconds':report['elapsed_seconds'],
            'A_factual_review':next(r['factual_review'] for r in afinal['cases'] if r['case_id']==res['case_id'])}
        complete.append(report)
    # Verify legal terminal close without changing B0434's interrupted history.
    bfailed=next(p for p in folders if p.name.startswith('B-') and not list(p.glob('api-cases/*/result.json')))
    before=scoring.read(bfailed/'execution.STOP.before-audited-close.json')
    after=scoring.read(ROOT/'executions'/bfailed.name/'execution.json')
    assert before['entries']==after['entries'] and before['status']=='STOP' and after['status']=='CLOSED'
    assert sum(e['held_tokens'] for e in before['entries'])==sum(e['held_tokens'] for e in after['entries'])==7621
    bclosure={'before_status':before['status'],'after_status':after['status'],
        'previous_status':after.get('previous_status'),'stop_reason_preserved':after.get('stop_reason')==before.get('stop_reason'),
        'entries_identical':True,'held_tokens_identical':True,'held_tokens':7621,'closed_reason':after.get('closed_reason'),
        'driver_and_pending_pg_unmodified':True,'no_resend':True}
    all_calls=[]
    by_stage=defaultdict(lambda:{'calls':0,'input':0,'output':0,'total':0})
    for f in (ROOT/'executions').iterdir():
        ledger=scoring.read(f/'execution.json')
        assert ledger['status']=='CLOSED'
        for e in ledger['entries']:
            assert e['status']=='SETTLED'
            role='answer' if '/answer/' in e['request_id'] else 'rewrite' if e['request_id'].endswith('/query_rewrite') else e['capability']
            u=e['usage'];target=by_stage[role]
            target['calls']+=1;target['input']+=u['prompt_tokens'];target['output']+=u['completion_tokens'];target['total']+=u['total_tokens']
            all_calls.append({'arm':f.name[0],'request_id':e['request_id'],'role':role,'model':e['model'],
                'input':u['prompt_tokens'],'output':u['completion_tokens'],'total':u['total_tokens'],'status':e['status']})
    assert len(all_calls)==51 and sum(x['total'] for x in all_calls)==118703
    manifest=scoring.read(ROOT/'MANIFEST.json')
    bad=[name for name,m in manifest.items() if not(ROOT/name).is_file() or scoring.sha(ROOT/name)!=m['sha256'] or (ROOT/name).stat().st_size!=m['bytes']]
    assert not bad
    output={'schema':'s8-1-final-bc-independent-audit/v1','completed_new_cases':complete,'A9_scores_reused':str(BASE/'a9-independent-final.results.json'),
        'B0434_interrupted_quality_review_reused':str(BASE/'b0434-partial-review.results.json'),'B0434_terminal_close':bclosure,
        'total_usage':{'calls':len(all_calls),'input':sum(r['input'] for r in all_calls),'output':sum(r['output'] for r in all_calls),
            'total':sum(r['total'] for r in all_calls),'stages':dict(by_stage),'by_arm':{a:{'calls':sum(c['arm']==a for c in all_calls),
                'input':sum(c['input'] for c in all_calls if c['arm']==a),'output':sum(c['output'] for c in all_calls if c['arm']==a),
                'total':sum(c['total'] for c in all_calls if c['arm']==a)} for a in ['A','B','C']},'calls_detail':all_calls},
        'manifest_checked':len(manifest),'manifest_failures':bad,'new_provider_calls_by_audit':0,
        'conclusion_scope':'Exploratory paired sample; no significance, official-whole-benchmark or promotion claim.'}
    scoring.write_new(BASE/'final-bc-independent-audit.results.json',output)
    print(json.dumps({'completed_new':len(complete),'tokens':output['total_usage']['total'],'manifest':len(manifest),
        'pairs':[{ 'case':r['case_id'],'arm':r['arm'],'same_retrieval_input':r['A_comparison']['inputs']['strict_retrieval_input_comparability'],
            'qu_output_equal':r['A_comparison']['inputs']['qu_output_exact_equal'],'wire_bound':r['context_bound_to_actual_wire'],
            'metric':r['answer_metric']} for r in complete]},ensure_ascii=True))


if __name__=='__main__':
    main()
