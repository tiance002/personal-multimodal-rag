"""Offline aggregation only. Reads preserved files; writes final-metrics.json."""
from pathlib import Path
from collections import defaultdict
import hashlib
import html
import json
import re

BASE = Path(__file__).resolve().parent
RAW = BASE / 'downloaded-final'

def read(p):
    return json.loads(p.read_text(encoding='utf-8-sig'))

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def ref(p):
    return {'path': str(p.relative_to(BASE)), 'sha256': sha(p), 'bytes': p.stat().st_size}

def short_usage(u):
    return {k:v for k,v in u.items() if k != 'provider_usage_facts'}

reviews = {}
review_refs = []
comparisons = []
for name in ['batch1-fact-review.json', 'batch2-fact-review.json']:
    p = BASE/name
    d = read(p)
    review_refs.append({**ref(p), 'overall_judgment_unchanged': d['overall']})
    for c in d['cases']:
        reviews[c['case_id']] = (name, c)
    comparisons.extend(d['comparisons'])

cases = []
for cd in sorted((RAW/'cases').iterdir()):
    recovered = (cd/'recovered-case-summary.json').exists()
    sp = cd/('recovered-case-summary.json' if recovered else 'case-summary.json')
    hp = cd/('recovered-http-result.json' if recovered else 'http-result.json')
    summary, http = read(sp), read(hp)
    exdir = RAW/'executions'/cd.name
    ep = exdir/'execution.json'
    execution = read(ep)
    stagep = next((exdir/'evidence').glob('*.stages.json'))
    stage = read(stagep)
    review_name, reviewed = reviews[summary['case_id']]
    audit = read(cd/'postgres-audit.json')
    state = next((s['state'] for s in reversed(stage['stages']) if 'state' in s), {})
    selected = {r['id']:r for r in state.get('merge_results', [])}
    all_results = {}
    for s in stage['stages']:
        for k in ['search_results','rerank_results','merge_results']:
            for r in s.get('state',{}).get(k,[]):
                all_results[r['id']] = r
    calls = []
    request_data = {}
    for p in sorted((exdir/'evidence').glob('*.request.json')):
        request = read(p)
        request_data[request['request_id']] = (p, request)
    for entry in execution['entries']:
        p, request = request_data[entry['request_id']]
        body = request['body']
        stem = p.name.removesuffix('.request.json')
        files = [p, p.with_name(stem+'.response.json'), p.with_name(stem+'.response.body'), p.with_name(stem+'.timing.json')]
        call = {k:entry.get(k) for k in ['id','request_id','provider','model','model_id','capability','status','outcome','wire_sha256']}
        call['usage'] = short_usage(entry.get('usage',{}))
        call['raw_files'] = [ref(f) for f in files if f.exists()]
        call['headers_saved'] = request.get('headers_saved')
        call['generation_parameters'] = {k:v for k,v in body.items() if k not in ['messages','input','query','documents','model']}
        response_file = p.with_name(stem+'.response.body')
        raw = response_file.read_text(encoding='utf-8')
        if body.get('stream'):
            events = []
            for line in raw.splitlines():
                if line.startswith('data:') and line[5:].strip() != '[DONE]':
                    try: events.append(json.loads(line[5:]))
                    except json.JSONDecodeError: pass
            call['model_reported'] = next((x['model'] for x in reversed(events) if x.get('model')),None)
            call['finish_reason'] = next((c['finish_reason'] for x in reversed(events) for c in x.get('choices',[]) if c.get('finish_reason')),None)
            final_usage = next((x['usage'] for x in reversed(events) if x.get('usage')),None)
            call['raw_response_final_usage'] = final_usage
        else:
            response = json.loads(raw)
            call['model_reported'] = response.get('model')
            call['finish_reason'] = next((x.get('finish_reason') for x in response.get('choices',[]) if x.get('finish_reason')),None)
            call['raw_response_final_usage'] = response.get('usage',response.get('meta',{}).get('tokens'))
        if request['capability'] == 'rerank':
            retrieval = next(s['state']['search_results'] for s in stage['stages'] if s['stage']=='retrieval' and s['status']=='completed')
            target_ids = {t['id'] for t in reviewed.get('target_chunks',[])}
            # The request document order is the retrieval order. Only claim a mapping
            # where the literal request document occurs in that candidate content.
            targets = []
            for rr in json.loads(raw).get('results',[]):
                i = rr['index']
                if i >= len(retrieval): continue
                r = retrieval[i]
                if r['id'] not in target_ids: continue
                doc = body['documents'][i]
                normalized = lambda t: re.sub(r'\s+',' ',re.sub(r'[#*`]','',t)).strip()
                verified = normalized(doc) in normalized(r['content']) or normalized(r['content']) in normalized(doc)
                targets.append({'id':r['id'],'chunk_type':r.get('chunk_type'),'request_document_index':i,'order_mapping_content_verified':verified,'provider_relevance_score':rr.get('relevance_score'),'score_semantics':'raw provider relevance_score; not composite stage score'})
            call['raw_target_rerank_scores'] = targets
            call['request_document_count'] = len(body['documents'])
        calls.append(call)
    generation = next(c for c in calls if '/answer/' in c['request_id'])
    genp, genrequest = request_data[generation['request_id']]
    messages = genrequest['body']['messages']
    text = '\n'.join(m['content'] for m in messages)
    user = '\n'.join(m['content'] for m in messages if m['role']=='user')
    wire_chunks = []
    for docattrs, docbody in re.findall(r'<document\b([^>]*)>(.*?)</document>',text,re.S):
        filename = re.search(r'filename="([^"]*)"',docattrs)
        filename = html.unescape(filename.group(1)) if filename else None
        for attrs, content in re.findall(r'<chunk\b([^>]*)>\s*<content>(.*?)</content>\s*</chunk>',docbody,re.S):
            a = dict(re.findall(r'(\w+)="([^"]*)"',attrs))
            unescaped = html.unescape(content)
            candidates = [r for r in selected.values() if r.get('knowledge_filename') == filename and str(r.get('chunk_index'))==a.get('index') and r.get('chunk_type')==a.get('type')]
            if len(candidates)!=1:
                candidates = [r for r in all_results.values() if r.get('knowledge_filename') == filename and str(r.get('chunk_index'))==a.get('index') and r.get('chunk_type')==a.get('type') and r.get('content')==unescaped]
            result = candidates[0] if len(candidates)==1 else {}
            md, cmd = result.get('metadata',{}), result.get('chunk_metadata',{})
            wire_chunks.append({'id':result.get('id'),'document_id':result.get('knowledge_id'),'source_id':result.get('knowledge_id'),'source_version':cmd.get('source_version',md.get('source_version')),'filename':filename,'chunk_index':a.get('index'),'chunk_type':a.get('type'),'evidence_kind':a.get('evidence_kind','NOT_PRESENT_IN_WIRE'),'content_chars_actualwire':len(content),'content_chars_html_unescaped':len(unescaped),'content_sha256_utf8':hashlib.sha256(content.encode()).hexdigest(),'stage_original_content_exact_equal':result.get('content')==unescaped,'generated_origin':cmd.get('origin')})
    retrieval_block = re.search(r'<retrieval\b.*?</retrieval>',text,re.S)
    counts = {s['stage']:s['fields'] for s in stage['stages'] if s['status']=='completed'}
    counts = {k:v for k,v in counts.items() if k in ['understand','retrieval','rerank','merge','source_unit','context']}
    case = {'case_id':summary['case_id'],'arm':reviewed['arm'],'source_evidence_mode':execution['source_evidence_mode'],'execution_key':cd.name,'session_id':summary['session_id'],'client_request_id':summary['request_id'],'server_run_id':http.get('server_run_id',stage['run_id']),'stage_counts':counts,'stage_sequence':[{'stage':s['stage'],'status':s['status']} for s in stage['stages']], 'finalized_result_ids':stage.get('finalized_context',{}).get('result_ids',[]),'actualwire':{'generation_request':ref(genp),'all_message_content_chars_sum':sum(len(m['content']) for m in messages),'user_message_content_chars_sum':sum(len(m['content']) for m in messages if m['role']=='user'),'retrieval_xml_chars':len(retrieval_block.group()) if retrieval_block else 0,'chunk_content_chars_sum':sum(c['content_chars_actualwire'] for c in wire_chunks),'chunks':wire_chunks,'description_tag_present': '<description' in text,'generated_summary_chunk_count':sum(c['chunk_type']=='summary' for c in wire_chunks),'source_text_chunk_count':sum(c['evidence_kind']=='source_text' for c in wire_chunks)},'legacy_context_chars':len(stage.get('finalized_context',{}).get('rendered_contexts','')),'legacy_context_is_actualwire':False,'provider_calls':calls,'reported_tokens':summary['reported_tokens'],'unreported_usage_calls':summary.get('unreported_usage_calls',sum(c['usage'].get('status')!='REPORTED' for c in calls)),'held_tokens':summary['held_tokens'],'latency_ms':{k:http.get(k) for k in ['first_sse_event_ms','first_nonempty_content_ms','post_to_complete_ms','post_to_history_total_ms']},'provider_ttft_ms':'NOT_RECORDED','rag_sql_observation':read(cd/'rag-sql-observation.json'),'memory':{'status':'NOT_RECORDED','memory_json_exists':(cd/'memory.json').exists(),'sample_count':summary.get('memory',{}).get('sample_count',0),'container_limit_bytes':1073741824,'observed_peak_bytes':None},'persistence':{'http_status':http.get('http_status'),'sse_complete':http.get('sse_complete'),'sse_history_content_match':http.get('sse_history_content_match'),'postgres_audit_status':audit['audit_status'],'checks':audit['checks']},'factual_review_reference':{'file':review_name,'case_id':summary['case_id'],'judgment_unchanged':reviewed.get('factual_review'),'citation_review_unchanged':reviewed.get('citation_review'),'fact_coverage_unchanged':reviewed.get('fact_coverage')},'evidence':[ref(p)for p in [sp,hp,ep,stagep,cd/'rag-sql-observation.json',cd/'postgres-audit.json',cd/'runtime-identity.json']],'recovery':{'used_readonly_recovery':recovered}}
    case['actualwire']['all_messages_newline_joined_chars'] = len(text)
    case['actualwire']['reviewer_all_messages_chars'] = reviewed['provider_wire_chars_all_messages']
    case['actualwire']['reviewer_wire_length_matches_newline_joined'] = len(text) == reviewed['provider_wire_chars_all_messages']
    case['actualwire']['target_membership_proof_unchanged'] = reviewed.get('target_chunks',[])
    case['actualwire']['target_stage_membership_unchanged'] = reviewed.get('target_stage_membership')
    case['generation_usage'] = generation['usage']
    case['generation_finish_reason'] = generation['finish_reason']
    case['generation_parameters'] = generation['generation_parameters']
    complete = next((x for x in http.get('events',[]) if x.get('response_type')=='complete'),{})
    case['generation_identity'] = [{k:a.get(k) for k in ['attempt_id','provider','model_requested','model_reported','endpoint','status','finish_reason']} for a in complete.get('data',{}).get('attempts',[])]
    if recovered:
        original = read(cd/'http-result.json')
        case['recovery'].update({'original_http_result':ref(cd/'http-result.json'),'original_status':original['status'],'original_http_status':original.get('http_status'),'recovery_provider_sends':summary.get('provider_sends'),'recovery_question_posts':summary.get('question_posts'),'original_question_posts':summary.get('original_question_posts')})
    cases.append(case)

by_model = defaultdict(lambda:{'calls':0,'prompt_tokens':0,'completion_tokens':0,'total_tokens':0})
for case in cases:
    for call in case['provider_calls']:
        bucket=by_model[call['provider']+'/'+call['model']]
        bucket['calls']+=1
        for k in ['prompt_tokens','completion_tokens','total_tokens']:
            bucket[k]+=call['usage'].get(k,0)
totals={'cases':len(cases),'provider_calls':sum(len(c['provider_calls'])for c in cases),'reported_tokens':sum(c['reported_tokens']for c in cases),'unreported_usage_calls':sum(c['unreported_usage_calls']for c in cases),'new_unknown':sum(call['status']=='UNKNOWN'for c in cases for call in c['provider_calls']),'by_provider_model':dict(by_model),'sql':{k:round(sum(c['rag_sql_observation'].get(k,0)for c in cases),3)for k in ['execute_calls','completed_execute_calls','failed_execute_calls','execute_elapsed_sum_ms','window_elapsed_ms']},'memory_recorded_cases':sum(c['memory']['memory_json_exists']for c in cases)}
assert (totals['cases'],totals['provider_calls'],totals['reported_tokens'],totals['new_unknown'])==(12,48,106570,0), totals
assert len({c['case_id']for c in cases})==12
assert all(call['headers_saved'] is False for c in cases for call in c['provider_calls'])
out={'schema':'s8-3-r-final-offline-metrics/v1','scope':'OFFLINE saved-evidence aggregation; zero network/model/database calls and zero reruns. No factual judgments changed.','totals':totals,'limitations':['One physical execution per arm; no statistical or exclusive causal quality/latency claim. See unchanged paired comparison evidence.','Exact lengths are Python Unicode code-point lengths of preserved actual request message strings. Both sum without separators and newline-joined length are supplied; reviewer all-message lengths use newline join (one added separator for two messages). Chunk content lengths preserve actual XML text; decoded lengths also supplied. Legacy finalized_context differs from actual provider wire.','A uses older rendering with no evidence_kind tag; source_text_chunk_count counts literal source_text tags only and is not original evidence coverage. Explicit original target membership/proofs are retained.','First visible SSE and first nonempty application content are not provider TTFT. Provider TTFT was not recorded.','All 12 memory.json files are absent and sample_count is 0. Peak memory is NOT_RECORDED; 1 GiB container limit is a configured cap only.','SQL execute counts/times cover private API process execute calls during accepted-question ASGI window, include request persistence/provider accounting, exclude fetch/decode and other processes; sums may overlap concurrent calls. Counts are not SELECT-only counts.','Raw rerank relevance_score is distinct from composite stage scores. Only target chunks found in the raw response are listed; absent targets have no recorded raw score.','Raw request/response references include SHA256 and bytes; headers were not saved. Provider identity is the observed provider/model/model_id, not a claim about hidden model implementation.','Filename citations are not UI click verification. Batch2 original byte provenance limits and all quality judgments remain as stated in independent reviews.','No-context fallback may place generated metadata in system prompt without XML chunks; zero chunk content does not mean zero context or zero metadata.'],'review_sources':review_refs,'paired_comparisons_unchanged':comparisons,'cases':cases}
(BASE/'final-metrics.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'path':str(BASE/'final-metrics.json'),'sha256':sha(BASE/'final-metrics.json'),'totals':totals,'wire_mapping_unresolved':[{'case_id':c['case_id'],'chunk':x}for c in cases for x in c['actualwire']['chunks']if not x['id']],'rerank_mapping_unverified':[{'case_id':c['case_id'],'target':r}for c in cases for call in c['provider_calls']for r in call.get('raw_target_rerank_scores',[])if not r['order_mapping_content_verified']]},ensure_ascii=True))
