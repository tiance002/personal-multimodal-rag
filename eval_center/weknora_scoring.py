"""Pure native evaluation metrics; QA Gold and parsing Gold remain separate."""
from __future__ import annotations
from collections import Counter
import re,string
from eval_center.metrics import ranking_metrics
from eval_center.omni_contract import score_page_parsing


def _normalize(value):return ''.join(c.lower() for c in value if c.isalnum())

def _char_f1(a,b):
    a,b=Counter(_normalize(a)),Counter(_normalize(b))
    if not a or not b:return float(a==b)
    common=sum((a&b).values());return 2*common/(sum(a.values())+sum(b.values()))

def answer_diagnostics(prediction,references,dataset):
    if not references:return {'status':'N/A_NO_ANSWER_GOLD','metrics':None}
    if dataset=='hotpotqa':
        def norm(v):return ' '.join(re.sub(r'\b(a|an|the)\b',' ',''.join(c for c in v.lower() if c not in string.punctuation)).split())
        p=norm(prediction);values=[]
        for reference in references:
            g=norm(reference);a,b=p.split(),g.split();hit=sum((Counter(a)&Counter(b)).values())
            f1=2*hit/(len(a)+len(b)) if a and b else float(a==b)
            if p!=g and (p in {'yes','no','noanswer'} or g in {'yes','no','noanswer'}):f1=0.
            values.append((float(p==g),f1))
        return {'status':'COMPUTED_ANSWER_DIAGNOSTICS','metrics':{'hotpot_answer_em':max(r[0] for r in values),'hotpot_answer_token_f1':max(r[1] for r in values)},'supporting_facts':'SEPARATE_FROM_ANSWER'}
    return {'status':'COMPUTED_PROJECT_DIAGNOSTICS_ON_NATIVE_GOLD','metrics':{'normalized_exact_match':max(int(_normalize(prediction)==_normalize(r)) for r in references),
        'character_f1':max(_char_f1(prediction,r) for r in references)},'official_judge':'NOT_RUN'}


def annotation_diagnostics(case,annotations):
    meta=case.evaluation_metadata or {}
    if annotations is None:return {'status':'NOT_RUN_SOURCE_ANNOTATION_MAPPING_REQUIRED','metrics':None}
    if 'evidence_pages' in meta:
        if not meta['evidence_pages']:return {'status':'N/A_NO_NATIVE_PAGE_REFERENCES','metrics':None}
        # Current frozen MML proof is UNKNOWN. Do not infer a base from ranges.
        return {'status':'NOT_RUN_PAGE_INDEX_CONVENTION_UNKNOWN','metrics':None,'native_page_references':meta['evidence_pages']}
    if 'supporting_facts' not in meta:return {'status':'N/A_NO_SUPPORTING_FACT_GOLD','metrics':None}
    gold={(r['document_id'],r['sentence_index_zero_based']) for r in meta['supporting_facts']}
    predicted={(r['document_id'],i) for r in annotations for i in r.get('sentence_indices_zero_based',[])}
    hit=len(gold&predicted);precision=hit/len(predicted) if predicted else 0.;recall=hit/len(gold) if gold else None
    return {'status':'COMPUTED_SOURCE_ANNOTATION_DIAGNOSTICS','metrics':{'supporting_fact_precision':precision,'supporting_fact_recall':recall,
        'supporting_fact_f1':2*precision*recall/(precision+recall) if recall is not None and precision+recall else 0.},'official_joint_qa':'NOT_RUN'}


def project_native_documents(rankings,bindings):
    result=[]
    for chunk,kb,document in rankings:
        if document not in bindings:raise ValueError('NATIVE_SOURCE_IDENTITY_MAPPING_REQUIRED')
        source=bindings[document]
        if source not in result:result.append(source)
    return result


def score_native_qa(case,*,dataset,prediction,ranked_source_ids,citations,execution_kind,k=10,annotations=None):
    if execution_kind!='REAL_PROVIDER':return {'status':'NOT_RUN_SIMULATED_OR_UNCONFIRMED_MODELS','quality_metrics':None,'execution_kind':execution_kind}
    if prediction is None:return {'status':'NOT_RUN_NO_NATIVE_RESULT','quality_metrics':None}
    answer=answer_diagnostics(prediction,case.answers,dataset)
    return {'status':'PROJECT_DIAGNOSTICS_RECORDED_NATIVE_OUTPUTS','ranking_unit':'ORIGINAL_DOCUMENT',
        'retrieval':ranking_metrics(ranked_source_ids,case.qrels,k=k),'answer':answer,
        'source_annotations':annotation_diagnostics(case,annotations),
        'citation_readback_rate':sum(citations)/len(citations) if citations else None,
        'citation_semantic_support':'NOT_EVALUATED','stage_acceptance':'NOT_CLAIMED'}


def score_native_omni(page,gold,prediction=None,*,execution_kind='NOT_RUN'):
    if execution_kind not in ('NATIVE_LOCAL_PARSER','RECORDED_NATIVE_OCR') or prediction is None:
        return {'status':'NOT_RUN','page_id':page['page_id'],'metrics':None,'independent_qa':'NOT_RUN_OVERLAY_UNAVAILABLE'}
    if prediction.get('implementation')!='weknora-python-port':raise ValueError('NATIVE_OMNI_PRODUCER_REQUIRED')
    row=score_page_parsing(page,gold,prediction)
    return {**row,'independent_qa':'NOT_RUN_OVERLAY_UNAVAILABLE','official_omnidocbench_score':'NOT_RUN'}
