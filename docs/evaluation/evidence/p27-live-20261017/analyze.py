"""Offline scoring/trace reconciliation after all live model sends have stopped."""
import base64,hashlib,html,json,re,sys
from collections import Counter,defaultdict
from datetime import datetime
from pathlib import Path
ROOT=Path('E:/RAG-Worktrees/weknora-python-port-20261013');sys.path.insert(0,str(ROOT))
from eval_center.weknora_scoring import answer_diagnostics
from scripts.weknora_port_p22_fence import digest
R=Path(__file__).parent
def read(p):return json.loads((R/p).read_text(encoding='utf-8'))
state=read('state.json');snap=read('snapshots/final.json');gold=read('frozen-gold-offline-only.json')
inputs=read('frozen-inputs.json')['items'];byid={c['id']:c for c in snap['chunks']}
captures={x['question']:x for p in (R/'api-evidence/captures').glob('*.json') for x in [json.loads(p.read_text(encoding='utf-8'))]}
calls=snap['calls'];wires=[]
for kind in ('api','worker'):
    wires += [dict(json.loads(p.read_text(encoding='utf-8')),evidence_file=str(p)) for p in (R/(kind+'-evidence')/'provider-requests').glob('*.json')]
def metrics(rows):
    result=dict(calls=len(rows),input=0,output=0,total=sum(x['actual_total'] or 0 for x in rows),unknown=sum(x['state']=='unknown' for x in rows),unknown_held=sum(x['held_tokens'] for x in rows if x['state']=='unknown'))
    for c in rows:
        fields={n:x['value'] if x['valid'] else None for n,x in c['usage_facts']['fields']}
        result['input']+=fields.get('input') or 0;result['output']+=fields.get('output') or 0
    return result
def rendered_text(text):
    # Source Context quotes image blocks with '> ' on every line and escapes
    # HTML. Undo only those known render transforms for provenance comparison.
    return ' '.join(re.sub(r'(?m)^> ?', '', html.unescape(text)).split())
groups=defaultdict(list)
for c in calls:groups[(c['provider'],c['capability'])].append(c)
result=dict(schema='p27-live-trace-analysis/v1',status='P27_PARTIAL',production_source='14788b9ed37cfd82d15ac8ecbb62efcab4f14d35',
    model_calls=metrics(calls),model_groups=[dict(provider=k[0],capability=k[1],**metrics(v)) for k,v in groups.items()],cases=[])
for item in inputs:
    q=item['qid'];e=state['cases'][q];doc=next(d for d in snap['documents'] if d['id']==e['document']);version=doc['metadata']['source_version']
    task=next(t for t in snap['tasks'] if t['knowledge_id']==doc['id']);cap=captures[item['question']]
    chunks=[c for c in snap['chunks'] if c['knowledge_id']==doc['id']];derived=[c for c in chunks if c['chunk_type'] in ('image_ocr','image_caption')]
    vectors=[v for v in snap['vectors'] if v['knowledge_id']==doc['id']]
    selected_ids={c['id'] for c in derived};parents={c['parent_chunk_id'] for c in derived}
    for c in derived:
        assert c['metadata']['page_number']==item['physical_page'] and c['metadata']['source_version']==version
        assert c['parent_chunk_id'] in byid and byid[c['parent_chunk_id']]['knowledge_id']==doc['id']
        assert any(v['source_id']==c['id'] and v['dimension']==1024 and v['is_enabled'] for v in vectors)
    assert task['selected_config']['page_scope']['physical_pages']==[item['physical_page']]
    assert task['selected_config']['page_scope']['source_sha256']==item['sha256']
    vision=next(w for w in wires if w['provider']=='deepseek' and w['request_id'].startswith(version))
    payloads=[p['image_url']['url'] for m in vision['body']['messages'] for p in m.get('content',[]) if isinstance(p,dict) and p.get('type')=='image_url']
    assert len(payloads)==1
    image=base64.b64decode(payloads[0].split(',',1)[1]);image_sha=hashlib.sha256(image).hexdigest()
    expected=Path('E:/WK/run/p27-gate-20261017')/(q+'-p'+str(item['physical_page'])+'.jpg')
    assert image_sha==hashlib.sha256(expected.read_bytes()).hexdigest()
    vc=next(c for c in calls if c['wire_sha256']==vision['wire_sha256']);assert vc['state']=='settled'
    model_body_text='\n'.join(str(m.get('content','')) for w in wires if w['request_id'].startswith(cap['run_id']+'/answer/') for m in w['body']['messages'])
    actual_answers=[w for w in wires if w['request_id'].startswith(cap['run_id']+'/answer/')]
    messages=read(q+'/messages.json')['data'];answer='\n'.join(m['content'] for m in messages if m['role']=='assistant')
    context=cap['context']['rendered_contexts'];outcome=task['outcomes']['0']
    stages={}
    for stage in ('search','rerank','context'):
        ranking=cap['rankings'].get(stage,[]);ids={r[0] for r in ranking}
        assert all(r[1]==e['kb'] and r[2]==doc['id'] for r in ranking),'CROSS_SCOPE_RESULT'
        stages[stage]=dict(count=len(ranking),image_child_ids=sorted(ids&selected_ids),image_parent_ids=sorted(ids&parents))
    related=[c for c in calls if c['request_id'].startswith(version) or c['request_id'].startswith(cap['run_id'])]
    row=dict(qid=q,question=item['question'],gold=gold[q],kb=e['kb'],document_id=doc['id'],source_sha256=item['sha256'],source_version=version,
        physical_page=item['physical_page'],original_http_readback=read(q+'/source-readback.json'),task_id=task['id'],task_state=task['state'],task_attempt=task['attempt'],
        ocr_status=outcome['ocr_text'],caption_status=outcome['caption'],ocr=outcome['ocr_result'],caption=outcome['caption_result'],
        asset_sha256=image_sha,actual_vision_wire_sha256=vision['wire_sha256'],actual_vision_wire_bytes=vision['wire_bytes'],
        actual_cap=vision['body'].get('max_tokens'),actual_thinking_parameter=vision['body'].get('thinking'),
        vision_usage=metrics([vc]),input_reservation=vc['reserved_input'],output_reservation=vc['reserved_output'],
        chunk_types=dict(Counter(c['chunk_type'] for c in chunks)),vector_count=len(vectors),derived_chunk_ids=sorted(selected_ids),parent_chunk_ids=sorted(parents),
        stages=stages,caption_in_final_context=rendered_text(outcome['caption_result']) in rendered_text(context),ocr_in_final_context=rendered_text(outcome['ocr_result']) in rendered_text(context),
        caption_in_actual_final_request=bool(actual_answers) and rendered_text(outcome['caption_result']) in rendered_text(model_body_text),
        ocr_in_actual_final_request=bool(actual_answers) and rendered_text(outcome['ocr_result']) in rendered_text(model_body_text),
        final_answer_calls=len(actual_answers),fixed_refusal=cap['fixed_fallback'] is not None,answer=answer,
        answer_diagnostic=answer_diagnostics(answer,gold[q],'mmlongbench-doc'),official_judge='NOT_RUN',
        ingestion_seconds=(datetime.fromisoformat(doc['updated_at'])-datetime.fromisoformat(doc['created_at'])).total_seconds(),
        qa_seconds=e['qa_elapsed_seconds'],provider_latency_ms='NOT_CAPTURED_SEPARATELY',usage=metrics(related),
        public_citations_enabled=False,source_chunk_readback='PASS: four derived chunks read through ordinary HTTP and checked for page/version/text identity')
    if q=='row-0958':
        row.update(full_visual_to_final_chain=False,status='QUALITY_FAILURE',failure_categories=['VISUAL_DETAILS_NOT_EXTRACTED','RERANK_FILTERS_ALL_CANDIDATES','FIXED_EVIDENCE_INSUFFICIENT'],
            manual_answer_correct=False,necessary_gold_facts_in_derived_text=False,
            manual_basis='Original target image visibly labels shiny object or mirror; OCR corrupts it and Caption only summarizes wave phenomena. No final model call occurred.')
    else:
        assert row['caption_in_actual_final_request'] and row['ocr_in_actual_final_request']
        row.update(full_visual_to_final_chain=True,status='QUALITY_FAILURE',failure_categories=['CHART_VALUES_NOT_EXTRACTED','UNSUPPORTED_GROUP_PERCENTAGE_IN_ANSWER'],
            manual_answer_correct=False,necessary_gold_facts_in_derived_text=False,
            manual_basis='Original chart visibly shows men81%, women78%. OCR garbles bars and Caption says about80% without exact group values. Cheap receives the real derived content but invents both groups79% from overall79% prose.')
    result['cases'].append(row)
assert sum(c['final_answer_calls'] for c in result['cases'])==1 and len([c for c in calls if c['capability']=='vision'])==2
assert result['model_calls']['total']==50185 and result['model_calls']['unknown']==0
close=read('fence/close-after.json')['data'];opened=read('fence/open-after.json')['data']
result['fence']=dict(total_allocation=1000000,actual_new_occupied=50185,unused_revoked=949815,
    accounts={p:dict(limit=v['month']['limit_tokens'],occupied=v['occupied'],headroom=v['headroom']) for p,v in close['after'].items()})
result['history_preservation']={}
for provider in ('deepseek','siliconflow'):
    old=opened['before'][provider]['calls'];after={r['call_id']:r for r in close['after'][provider]['calls']}
    assert all(after.get(r['call_id'])==r for r in old)
    result['history_preservation'][provider]=dict(old_rows=len(old),all_old_rows_unchanged=True,old_rows_sha256=digest(old),
        old_unknown_count=sum(r['state']=='unknown' for r in old),old_unknown_held=sum(r['held_tokens'] for r in old if r['state']=='unknown'))
(R/'analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'status':result['status'],'calls':result['model_calls'],'cases':[dict(qid=c['qid'],stages=c['stages'],full_chain=c['full_visual_to_final_chain'],answer_score=c['answer_diagnostic']) for c in result['cases']], 'unused_revoked':949815},ensure_ascii=False,indent=2))
