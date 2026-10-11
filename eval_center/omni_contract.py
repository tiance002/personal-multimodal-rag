"""Omni page parsing and independently reviewed QA are separate contracts."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime
from pathlib import Path
from eval_center.special_public_data import canonical, source_path, verified_bytes
from eval_center.public_data import PublicCase

def page_input_contract(freeze_path,*,expected_sha256,asset_root=None):
    raw=Path(freeze_path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected_sha256:raise ValueError('omni_freeze_hash_mismatch')
    freeze=json.loads(raw);mapping=freeze['omni_page_Gold_mapping']
    if len(mapping)!=24 or len({r['qid'] for r in mapping})!=24:raise ValueError('omni_fixed24_required')
    inputs=[]
    for page in mapping:
        record=next(r for r in freeze['source_files'] if r['dataset']=='OmniDocBench'
            and Path(r['relative_path']).name==page['image_name'])
        path=source_path(record,asset_root)
        verified_bytes(path,{'bytes':record['actual_bytes'],'sha256':record['actual_sha256']})
        inputs.append({'page_id':page['qid'],'document_id':page['image_name'],'image_path':str(path),
            'image_sha256':record['actual_sha256'],'width':page['gold_width'],'height':page['gold_height'],
            'page_gold_sha256':page['original_page_canonical_sha256'],
            'gold_row_index':page['source_gold_row_index'],'gold_role':'EVALUATION_ONLY_NOT_INGESTION'})
    return {'schema':'omni-page-parsing-v1','pages':inputs,'page_count':24,'official_qa_count':0,
        'parsing_execution':'NOT_RUN','rag_qa_execution':'NOT_RUN_NEEDS_INDEPENDENT_PREMODEL_QA',
        'official_qa_accuracy':'NOT_AVAILABLE','six_dataset_full_rag_acceptance':'NOT_RUN',
        'normal_rag_requirements':['independently reviewed premodel QA overlay','normal image/PDF ingestion and verified index',
            'server-scoped normal KnowledgeGateway retrieval','official QuickChain generation','actual citation/version/chunk readback'],
        'metric_contract':{'layout':'PROJECT bbox/category F1 at IoU .5',
            'text_table_formula':'PROJECT normalized edit distance for matched eligible targets',
            'reading_order':'PROJECT matched-block pairwise order accuracy',
            'official_omnidocbench_score':'NOT_RUN'}}

def selected_page_gold(freeze_path,contract,*,asset_root=None):
    freeze=json.loads(Path(freeze_path).read_text(encoding='utf-8'))
    record=next(r for r in freeze['source_files'] if r['relative_path'].endswith('/OmniDocBench.json'))
    gold=json.loads(verified_bytes(source_path(record,asset_root),
        {'bytes':record['actual_bytes'],'sha256':record['actual_sha256']}))
    result={}
    for page in contract['pages']:
        raw=gold[page['gold_row_index']]
        if hashlib.sha256(canonical(raw).encode()).hexdigest()!=page['page_gold_sha256']:
            raise ValueError('omni_original_page_gold_changed')
        result[page['page_id']]=raw
    return result

def _bbox(poly):
    if not isinstance(poly,list) or len(poly)!=8 or any(type(x) not in (int,float) for x in poly):
        raise ValueError('omni_polygon_invalid')
    import math
    if any(not math.isfinite(x) for x in poly):raise ValueError('omni_polygon_invalid')
    return min(poly[::2]),min(poly[1::2]),max(poly[::2]),max(poly[1::2])

def _iou(a,b):
    a,b=_bbox(a),_bbox(b);area=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-area
    return area/union if union>0 else 0.0

def _ned(a,b):
    prior=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        row=[i]
        for j,y in enumerate(b,1):row.append(min(row[-1]+1,prior[j]+1,prior[j-1]+int(x!=y)))
        prior=row
    return prior[-1]/max(len(a),len(b),1)

def score_page_parsing(page,gold,prediction=None):
    """Project diagnostics on original annotations, never official QA accuracy."""
    if prediction is None:return {'status':'NOT_RUN','page_id':page['page_id'],'metrics':None}
    if (prediction.get('page_id')!=page['page_id'] or prediction.get('image_sha256')!=page['image_sha256']
        or not isinstance(prediction.get('blocks'),list)):raise ValueError('omni_prediction_source_mismatch')
    targets=[b for b in gold['layout_dets'] if not b.get('ignore',False)]
    predicted=prediction['blocks'];matched=[];used=set()
    for i,target in enumerate(targets):
        candidates=[(_iou(target['poly'],p['poly']),j) for j,p in enumerate(predicted)
            if j not in used and p.get('category_type')==target['category_type']]
        if candidates:
            overlap,j=max(candidates)
            if overlap>=.5:matched.append((target,predicted[j]));used.add(j)
    metrics={'layout_f1':2*len(matched)/(len(targets)+len(predicted)) if targets or predicted else None}
    counts={'layout_f1':{'gold_blocks':len(targets),'predicted_blocks':len(predicted),'matched_blocks':len(matched)}}
    for name,key in [('text','text'),('table','html'),('formula','latex')]:
        eligible=[t for t in targets if isinstance(t.get(key),str) and t[key]]
        values=[_ned(t[key],p.get(key,'')) for t,p in matched if isinstance(t.get(key),str) and t[key]
            and isinstance(p.get(key,''),str)]
        metrics[name+'_normalized_edit_distance']=sum(values)/len(values) if values else None
        counts[name]={'eligible_gold':len(eligible),'matched_scored':len(values),
            'unmatched_or_unavailable':len(eligible)-len(values)}
    pairs=[(a,b) for i,a in enumerate(matched) for b in matched[i+1:] if type(a[0].get('order')) is int
        and type(b[0].get('order')) is int and a[0]['order']!=b[0]['order']
        and type(a[1].get('order')) is int and type(b[1].get('order')) is int]
    metrics['reading_order_pairwise_accuracy']=sum((a[0]['order']<b[0]['order'])==(a[1]['order']<b[1]['order']) for a,b in pairs)/len(pairs) if pairs else None
    counts['reading_order_pairwise_accuracy']={'evaluated_pairs':len(pairs)}
    return {'status':'SCORED_PROJECT_DIAGNOSTICS','page_id':page['page_id'],'metrics':metrics,'counts':counts,
        'gold_source':'OFFICIAL_ORIGINAL_PAGE_ANNOTATIONS','official_omnidocbench_score':'NOT_RUN',
        'official_qa_accuracy':'NOT_AVAILABLE'}

def load_independent_qa_overlay(path,*,expected_sha256,contract,first_model_result_utc,review_receipt_sha256):
    """Explicit separately frozen QA input to run_public_paired_generation_case.

    No overlay is authored/generated here. An absent overlay does not block the
    independent 40-question official-Gold cohort.
    """
    if path is None:return {'status':'NOT_RUN_NEEDS_INDEPENDENT_PREMODEL_QA','cases':()}
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected_sha256:raise ValueError('omni_qa_overlay_hash_mismatch')
    data=json.loads(raw)
    import re
    if (data.get('schema')!='omni-independent-qa-v1' or data.get('gold_origin')!='INDEPENDENT_PREMODEL'
        or data.get('review_receipt_sha256')!=review_receipt_sha256
        or not isinstance(review_receipt_sha256,str) or not re.fullmatch('[0-9a-f]{64}',review_receipt_sha256)):
        raise ValueError('omni_qa_independent_review_required')
    frozen=datetime.fromisoformat(data['frozen_at_utc'].replace('Z','+00:00'))
    first=datetime.fromisoformat(first_model_result_utc.replace('Z','+00:00'))
    if frozen.tzinfo is None or first.tzinfo is None or frozen>=first:raise ValueError('omni_qa_must_precede_model_results')
    pages={p['page_id']:p for p in contract['pages']};cases=[];seen=set()
    for row in data['cases']:
        p=pages.get(row.get('page_id'))
        if (p is None or row.get('source_page_gold_sha256')!=p['page_gold_sha256'] or row.get('qid') in seen
            or not isinstance(row.get('qid'),str) or not row['qid'].startswith('omni-independent:')
            or not isinstance(row.get('question'),str) or not row['question'].strip()
            or not isinstance(row.get('answers'),list) or not row['answers']
            or any(not isinstance(x,str) or not x for x in row['answers'])):raise ValueError('omni_qa_overlay_source_invalid')
        seen.add(row['qid'])
        cases.append(PublicCase(row['qid'],row['question'],{p['document_id']:1},'development',tuple(row['answers']),
            qrels_kind='INDEPENDENT_PREMODEL_SOURCE_PAGE_LABEL',evaluation_metadata={'gold_source':'INDEPENDENT_PREMODEL',
                'page_id':p['page_id'],'source_page_gold_sha256':p['page_gold_sha256'],'review_receipt_sha256':review_receipt_sha256}))
    if not cases:raise ValueError('omni_qa_overlay_empty')
    return {'status':'INDEPENDENT_QA_FROZEN_FOR_NORMAL_RAG','cases':tuple(cases),'official_qa_accuracy':'NOT_AVAILABLE'}
