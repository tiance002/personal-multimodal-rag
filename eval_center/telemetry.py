"""Recompute consumption and timing summaries from anonymous actual calls.

Counts are provider usage, never characters. Unknown failed/retried consumption
makes the corresponding total unavailable. Estimated context/evidence tokens
use separately named metrics and cannot claim actual usage.
"""
from __future__ import annotations

import math

TIMINGS={'query_processing_ms','keyword_retrieval_ms','vector_retrieval_ms','fusion_ms',
         'context_building_ms','retrieval_ms','generation_ms','end_to_end_ms','ingestion_ms','embedding_ms'}
FIELDS={'calls','timings','context_tokens','evidence_tokens'}
CALL_FIELDS={'stage','role','status','input_tokens','output_tokens','latency_ms'}


def _number(value):
    return type(value) in (int,float) and math.isfinite(value) and 0<=value<=1e12


def telemetry_metrics(data):
    if not isinstance(data,dict) or set(data)!=FIELDS:
        raise ValueError('invalid telemetry statistics')
    calls=data['calls']
    timings=data['timings']
    if not isinstance(calls,list) or len(calls)>1000 or not isinstance(timings,dict) or set(timings)-TIMINGS:
        raise ValueError('invalid telemetry statistics')
    for call in calls:
        if not isinstance(call,dict) or set(call)!=CALL_FIELDS:
            raise ValueError('invalid call statistics')
        if call['stage'] not in ('query','answer','embedding') or call['role'] not in ('business','judge') or call['status'] not in ('ok','error'):
            raise ValueError('invalid call statistics')
        if not _number(call['latency_ms']): raise ValueError('invalid call timing')
        for field in ('input_tokens','output_tokens'):
            value=call[field]
            if value is not None and (type(value) is not int or not 0<=value<=100000000):
                raise ValueError('invalid provider usage')
            if call['status']=='error' and value is not None:
                raise ValueError('unknown failed-call consumption')
    result={}
    for name in TIMINGS:
        value=timings.get(name)
        if value is not None and not _number(value): raise ValueError('invalid timing')
        result[name]=value
    def total(selected,field):
        values=[call[field] for call in selected]
        return sum(values) if values and all(value is not None for value in values) else None
    for stage in ('query','answer','embedding'):
        selected=[call for call in calls if call['stage']==stage and call['role']=='business']
        result[stage+'_call_count']=len(selected)
        for field in ('input_tokens','output_tokens'):
            result[stage+'_'+field]=total(selected,field)
    for role in ('business','judge'):
        selected=[call for call in calls if call['role']==role and call['stage'] in ('query','answer')]
        inputs=total(selected,'input_tokens')
        outputs=total(selected,'output_tokens')
        result[role+'_total_tokens']=inputs+outputs if inputs is not None and outputs is not None else None
    for kind in ('context','evidence'):
        tokens=data[kind+'_tokens']
        if not isinstance(tokens,dict) or set(tokens)!={'availability','count'}:
            raise ValueError('invalid estimated usage')
        if tokens['availability']=='estimated':
            if type(tokens['count']) is not int or not 0<=tokens['count']<=100000000:
                raise ValueError('invalid estimated usage')
        elif tokens['availability']!='unavailable' or tokens['count'] is not None:
            raise ValueError('context counts cannot claim actual provider usage')
        result[kind+'_tokens_estimated']=tokens['count']
    return result


def project_calls(capture):
    """Provider model names stay in validated runtime identity, no prompts here."""
    return [{key:row[key] for key in CALL_FIELDS} for row in capture.to_dict()['calls']]
