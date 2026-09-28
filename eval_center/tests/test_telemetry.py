import pytest

from eval_center.telemetry import telemetry_metrics


def telemetry(calls):
    return {'calls':calls,'timings':{'retrieval_ms':3.5,'generation_ms':None},
            'context_tokens':{'availability':'unavailable','count':None},
            'evidence_tokens':{'availability':'unavailable','count':None}}


def call(stage,role='business',input=20,output=5,status='ok'):
    return {'stage':stage,'role':role,'status':status,'input_tokens':input,'output_tokens':output,'latency_ms':1.5}


def test_server_recomputes_query_answer_retries_and_separates_judge():
    data=telemetry([call('query'),call('answer'),call('answer'),call('answer',role='judge',input=10,output=2)])
    result=telemetry_metrics(data)
    assert result['query_input_tokens']==20
    assert result['answer_input_tokens']==40
    assert result['business_total_tokens']==75
    assert result['judge_total_tokens']==12
    assert result['context_tokens_estimated'] is None
    assert result['retrieval_ms']==3.5
    assert result['generation_ms'] is None


def test_unknown_retry_consumption_keeps_total_unavailable_not_zero():
    result=telemetry_metrics(telemetry([call('answer',input=None,output=None,status='error'),call('answer')]))
    assert result['answer_input_tokens'] is None
    assert result['business_total_tokens'] is None
    assert result['query_input_tokens'] is None
    assert result['judge_total_tokens'] is None


def test_context_estimate_cannot_claim_actual_provider_tokens():
    data=telemetry([])
    data['context_tokens']={'availability':'estimated','count':130}
    assert telemetry_metrics(data)['context_tokens_estimated']==130
    data['context_tokens']['availability']='actual'
    with pytest.raises(ValueError): telemetry_metrics(data)


def test_unknown_latency_field_and_private_call_fields_are_rejected():
    data=telemetry([call('answer')])
    data['calls'][0]['prompt']='private'
    with pytest.raises(ValueError): telemetry_metrics(data)
    data=telemetry([])
    data['timings']['unknown']=1
    with pytest.raises(ValueError): telemetry_metrics(data)
