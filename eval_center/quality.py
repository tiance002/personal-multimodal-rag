"""Local deterministic quality checks, distinct from semantic LLM judging.

Literal answer-point alternatives are dataset annotations. Citation booleans
come from real frozen-evidence readbacks. No semantic scores are fabricated.
Dedup here is an experiment analysis, not a mutation of business evidence.
"""
from __future__ import annotations

import re
import unicodedata


def _normalized(text):
    return ' '.join(unicodedata.normalize('NFKC',text).casefold().split())


def answer_statistics(*,answer,answer_points,citation_readbacks,answerable,refused):
    if answer is not None and not isinstance(answer,str):
        raise ValueError('answer must be text or unavailable')
    if type(answerable) is not bool or (refused is not None and type(refused) is not bool):
        raise ValueError('invalid answerability/refusal')
    matched=[]
    for alternatives in answer_points:
        if not isinstance(alternatives,list) or not alternatives or any(not isinstance(p,str) or not p.strip() for p in alternatives):
            raise ValueError('reviewed answer-point alternatives required')
        matched.append(answer is not None and any(_normalized(point) in _normalized(answer) for point in alternatives))
    labels=list(dict.fromkeys(re.findall(r'\[(E[1-9][0-9]*)\]',answer or '')))
    if any(type(value) is not bool for value in citation_readbacks.values()):
        raise ValueError('readback results must be booleans')
    return {'generated':answer is not None,'answerable':answerable,'refused':refused,
            'required_points':len(matched),'matched_points':sum(matched),
            'cited_labels':len(labels),'readable_labels':sum(citation_readbacks.get(label,False) for label in labels),
            'judge_status':'NOT_EVALUATED'}


def quality_metrics(stats):
    fields={'generated','answerable','refused','required_points','matched_points',
            'cited_labels','readable_labels','judge_status'}
    if not isinstance(stats,dict) or set(stats)!=fields:
        raise ValueError('invalid quality statistics')
    if type(stats['generated']) is not bool or type(stats['answerable']) is not bool or (stats['refused'] is not None and type(stats['refused']) is not bool):
        raise ValueError('invalid quality statistics')
    for total,hits in [('required_points','matched_points'),('cited_labels','readable_labels')]:
        if any(type(stats[key]) is not int for key in (total,hits)) or not 0<=stats[hits]<=stats[total]<=10000:
            raise ValueError('invalid quality counts')
    if stats['judge_status']!='NOT_EVALUATED':
        raise ValueError('unsupported judge claims')
    generated=stats['generated']
    return {'answer_point_coverage':stats['matched_points']/stats['required_points']
                if generated and stats['answerable'] and stats['required_points'] else None,
            'citation_readability':stats['readable_labels']/stats['cited_labels']
                if generated and stats['cited_labels'] else None,
            'refusal_accuracy':int(stats['refused'])
                if generated and not stats['answerable'] and stats['refused'] is not None else None,
            'faithfulness':None,'answer_relevance':None,'factual_correctness':None}


def duplicate_statistics(rows,*,token_counter=None):
    """Exact duplicates within the same immutable document/version only.

    Preserve all input provenance; return indices for counterfactual analysis.
    Different values, years, documents and source versions are never merged.
    Without labelled merge truth, semantic False Merge Rate is unavailable.
    """
    seen=set()
    removed=[]
    for index,row in enumerate(rows):
        if any(not isinstance(row.get(field),str) or not row[field] for field in ('text','document_id','source_version')):
            raise ValueError('immutable source identity required')
        key=(row['document_id'],row['source_version'],row['text'])
        if key in seen: removed.append(index)
        seen.add(key)
    savings={'availability':'unavailable','before':None,'after':None,'saved':None}
    if token_counter is not None:
        counts=[token_counter(row['text']) for row in rows]
        if any(type(value) is not int or value<0 for value in counts):
            raise ValueError('tokenizer must return nonnegative integer counts')
        before=sum(counts)
        after=sum(value for index,value in enumerate(counts) if index not in removed)
        savings={'availability':'estimated','before':before,'after':after,'saved':before-after}
    return {'exact_duplicate_rate':len(removed)/len(rows) if rows else None,
            'near_duplicate_rate':None,'removed_indices':removed,'false_merge_rate':None,
            'protected_fact_conflicts':0,'context_token_savings':savings,
            'tokenizer_method':'caller_supplied' if token_counter else 'unavailable'}
