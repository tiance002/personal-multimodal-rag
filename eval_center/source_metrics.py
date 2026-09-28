"""Conventional chunk ranking + stable-source Gold coverage, separate units.

Rank qrels are derived from positive intersections with reviewed source spans
over the *entire* scoped index, not just retrieved results. Their denominator
can vary with chunking. Cross-chunking evidence recall instead unions intervals
per immutable Gold ID, counting each Gold exactly once at its threshold.
"""
from __future__ import annotations

from eval_center.gold import coverage, union_length
from eval_center.metrics import ranking_metrics

STAGES=('keyword','vector','fused','context')
FIELDS={'gold_sources','index_intersections','rankings','coverage_threshold'}
OPTIONAL={'quality','telemetry','context_mode','retrieval_attempts','dedup'}


def source_case_metrics(statistics,config):
    if not isinstance(statistics,dict) or not FIELDS<=set(statistics) or set(statistics)-FIELDS-OPTIONAL:
        raise ValueError('invalid source statistics')
    gold=statistics['gold_sources']
    index=statistics['index_intersections']
    rankings=statistics['rankings']
    threshold=statistics['coverage_threshold']
    if not isinstance(gold,dict) or not isinstance(index,dict) or not isinstance(rankings,dict) or set(rankings)!=set(STAGES):
        raise ValueError('invalid source statistics')
    if type(threshold) not in (int,float) or not 0<threshold<=1:
        raise ValueError('invalid threshold')
    for identity,row in gold.items():
        if not isinstance(identity,str) or not identity or not isinstance(row,dict) or set(row)!={'length','grade'}:
            raise ValueError('invalid Gold units')
        if type(row['length']) is not int or not 1<=row['length']<=10000000 or type(row['grade']) is not int or not 1<=row['grade']<=3:
            raise ValueError('invalid Gold units')
    qrels={}
    for item,intersections in index.items():
        if not isinstance(item,str) or not item or not isinstance(intersections,dict) or set(intersections)-set(gold):
            raise ValueError('unknown source identity')
        grade=0
        for identity,spans in intersections.items():
            if not isinstance(spans,list): raise ValueError('invalid source intersections')
            for span in spans:
                if not isinstance(span,list) or len(span)!=2 or any(type(value) is not int for value in span):
                    raise ValueError('invalid source intersections')
                if not 0<=span[0]<span[1]<=gold[identity]['length']:
                    raise ValueError('source intersections out of bounds')
            if spans: grade=max(grade,gold[identity]['grade'])
        qrels[item]=grade
    result={}
    def source_coverage(ranking):
        fractions=[union_length(tuple(span) for item in set(ranking) for span in index[item].get(identity,[]))/row['length']
                   for identity,row in gold.items()]
        return (sum(value+1e-12>=threshold for value in fractions)/len(fractions),sum(fractions)/len(fractions)) if fractions else (None,None)
    for stage in STAGES:
        k=config['candidate_k'] if stage in ('keyword','vector') else config['top_k']
        ranking=rankings[stage]
        limit=2*k if stage=='context' else k
        if not isinstance(ranking,list) or len(ranking)>limit or any(not isinstance(item,str) or item not in index for item in ranking):
            raise ValueError('ranking outside scoped index or effective limit')
        result.update({stage+'.'+name:value for name,value in ranking_metrics(ranking,qrels,k=k).items()})
        recall,_=source_coverage(ranking[:k])
        full_recall,evidence=source_coverage(ranking)
        result[stage+'.source_recall_at_k']=recall
        result[stage+'.evidence_coverage']=evidence
        if stage=='context': result.update(context_recall=full_recall,evidence_coverage=evidence,**{'context.selected_count':len(ranking)})
    if 'context_mode' in statistics:
        mode=statistics['context_mode']
        if mode not in ('prepared','sent','unconfirmed','not_run'): raise ValueError('invalid context mode')
        result['context_sent_to_model']=1 if mode=='sent' else 0 if mode=='not_run' else None
    attempts=statistics.get('retrieval_attempts',[])
    if not isinstance(attempts,list) or len(attempts)>3: raise ValueError('invalid retrieval attempts')
    for number,attempt in enumerate(attempts):
        if not isinstance(attempt,dict) or set(attempt)!={'keyword','vector','fused'}: raise ValueError('invalid retrieval attempts')
        for stage,ranking in attempt.items():
            k=config['top_k'] if stage=='fused' else config['candidate_k']
            if not isinstance(ranking,list) or len(ranking)>k or any(not isinstance(item,str) or item not in index for item in ranking):
                raise ValueError('invalid retrieval attempt ranking')
            prefix=f'attempt_{number}.{stage}.'
            result.update({prefix+name:value for name,value in ranking_metrics(ranking,qrels,k=k).items()})
            result[prefix+'source_recall_at_k'],result[prefix+'evidence_coverage']=source_coverage(ranking)
    if 'dedup' in statistics:
        row=statistics['dedup']
        fields={'total_units','duplicate_units','tokens_before','tokens_after','reviewed_merges','false_merges','tokenizer'}
        if not isinstance(row,dict) or set(row)!=fields: raise ValueError('invalid duplicate statistics')
        for key in ('total_units','duplicate_units','reviewed_merges','false_merges'):
            if type(row[key]) is not int or not 0<=row[key]<=100000: raise ValueError('invalid duplicate counts')
        if row['duplicate_units']>row['total_units'] or row['false_merges']>row['reviewed_merges']: raise ValueError('invalid duplicate counts')
        if row['tokenizer']=='cl100k_base':
            if any(type(row[key]) is not int for key in ('tokens_before','tokens_after')) or not 0<=row['tokens_after']<=row['tokens_before']<=100000000:
                raise ValueError('invalid estimated savings')
            savings=row['tokens_before']-row['tokens_after']
        elif row['tokenizer']=='unavailable' and row['tokens_before'] is None and row['tokens_after'] is None: savings=None
        else: raise ValueError('invalid tokenizer identity')
        result.update(exact_duplicate_rate=row['duplicate_units']/row['total_units'] if row['total_units'] else None,
                      context_token_savings_estimated=savings,
                      false_merge_rate=row['false_merges']/row['reviewed_merges'] if row['reviewed_merges'] else None)
    return result


def build_source_statistics(gold,index_spans,rankings,*,threshold=.8):
    """Bind actual indexed chunk IDs to immutable source spans before export.

    index_spans must contain the entire scoped index. Sheet coverage is expressed
    as ordinal cell intervals; only reviewed cells count, never the whole sheet.
    """
    lengths={item.evidence_id:{'length':len(item.span.cells) if item.span.kind=='sheet' else item.span.end-item.span.start,
                              'grade':item.grade} for item in gold}
    intersections={}
    for chunk_id,span in index_spans.items():
        spans=coverage(gold,[span],threshold=threshold)['intersections']
        for item in gold:
            if item.span.kind=='sheet' and span.identity==item.span.identity:
                spans[item.evidence_id]=[[number,number+1] for number,cell in enumerate(item.span.cells) if cell in span.cells]
        intersections[chunk_id]={identity:values for identity,values in spans.items() if values}
    return {'gold_sources':lengths,'index_intersections':intersections,'rankings':rankings,'coverage_threshold':threshold}
