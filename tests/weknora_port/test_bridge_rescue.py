"""Engineering contracts for the opt-in bridge; saved Frost replay is separate."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from backend.weknora_port.models.types import (CallCancelled, CallContext, ChatResponse,
    ModelCallTimeout, ProviderError, RerankResponse, RerankResult, RerankSettings,
    RunDeadlineExceeded, TokenUsage, TrustedScope)
from backend.weknora_port.retrieval.bridge_rescue import (MODE, fact_windows,
    rescue_bridge, sentence_spans, validate_extraction)
from backend.weknora_port.retrieval.search import RetrievalScopeError, SearchTarget
from backend.weknora_port.retrieval.types import SearchResult

QUESTION = 'Where is the seminar Alice Bennett directed in 2011–2014 located?'
QUOTE = 'Alice Bennett directed the Arbor Poetry Seminar from 2011–2014.'
USAGE = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150, status='REPORTED')
CTX = CallContext('run', TrustedScope('owner', ('kb',)))


def row(id, content, *, document='doc', version='v1', kind='text', score=.2, **fields):
    result = SearchResult(id, content, score, knowledge_id=document, knowledge_base_id='kb',
        chunk_type=kind, start_at=100, end_at=100+len(content),
        metadata={'source_version': version, 'tenant_id': 7},
        chunk_metadata={'source_version': version})
    for key, value in fields.items():
        setattr(result, key, value)
    return result


def extraction(main, **fields):
    start = main.content.index(QUOTE)
    data = dict(status='FIRST_HOP_CANDIDATE', source_chunk_id=main.id,
        quote_start=start, quote_end=start+len(QUOTE), quote=QUOTE,
        subject='Alice Bennett', subject_resolution='EXPLICIT', predicate='directed',
        object='Arbor Poetry Seminar', time='2011–2014', target_attribute='location',
        bridge_search_phrases=['Arbor Poetry Seminar'])
    data.update(fields)
    return data


class Repository:
    tenant_id = 7

    def __init__(self, rows):
        self.rows = {r.id: deepcopy(r) for r in rows}
        self.calls = []

    async def get_current_chunks(self, ids, context):
        self.calls.append((tuple(ids), context))
        return {id: deepcopy(self.rows[id]) for id in ids if id in self.rows}


class Models:
    def __init__(self, content, *, chat_error=None, rerank_error=None, scores=None,
                 settings=None, chat_usage=USAGE, rerank_usage=USAGE, after_chat=None,
                 finish_reason='stop'):
        self.content, self.chat_error, self.rerank_error = content, chat_error, rerank_error
        self.scores = scores
        self.chat_usage, self.rerank_usage, self.after_chat = chat_usage, rerank_usage, after_chat
        self.finish_reason = finish_reason
        self.chat_calls, self.rerank_calls, self.role_calls = [], [], []
        self.config = SimpleNamespace(reranking=settings or RerankSettings())

    async def chat(self, *args):
        if len(args) == 2:
            model_id, context = args
            self.role_calls.append(('cheap', model_id, context))
            return SimpleNamespace(chat=self._chat)
        raise AssertionError('Use formal registry.chat -> model.chat seam')

    async def _chat(self, messages, options, context):
        self.chat_calls.append((messages, options, context))
        if self.chat_error:
            raise self.chat_error
        if self.after_chat:
            self.after_chat()
        return ChatResponse(self.content, finish_reason=self.finish_reason, usage=self.chat_usage)

    async def rerank(self, model_id, context):
        self.role_calls.append(('rerank', model_id, context))
        return SimpleNamespace(config=self.config, rerank=self._rerank)

    async def _rerank(self, query, passages, context):
        self.rerank_calls.append((query, tuple(passages), context))
        if self.rerank_error:
            raise self.rerank_error
        scores = self.scores if self.scores is not None else tuple(
            RerankResult(index, .91 - index*.01) for index in range(len(passages)))
        return RerankResponse(tuple(scores), usage=self.rerank_usage)


def case(*, extra=(), main=None, **model_options):
    main = main or row('first', '序言。\n'+QUOTE+'\nHer poems were published widely.', score=.8)
    second = row('second', 'Arbor Poetry Seminar is located in Harbor City, Canada.',
                 document='place', score=.25)
    rows = [main, second, *extra]
    state = SimpleNamespace(bridge_rescue_mode=MODE, bridge_cheap_model_id='cheap-snapshot',
        rerank_model_id='bge', query=QUESTION, needs_retrieval=True,
        targets=(SearchTarget('kb'),), search_results=deepcopy(rows),
        rerank_results=[deepcopy(main)], merge_results=[deepcopy(main)],
        rerank_threshold=.3, rescue_evidence=[], bridge_trace=[], evidence_trace=[
            {'stage':'rerank_raw', 'input_ids':[r.id for r in rows],
             'response':[{'index':index, 'score':.95 if index==0 else .02}
                         for index in range(len(rows))]},
            {'stage':'rerank_filter_mmr', 'accepted_ids':[main.id], 'mmr_ids':[main.id]}])
    content = json.dumps(extraction(main), ensure_ascii=False) if QUOTE in main.content else ''
    return state, Repository(rows), Models(content, **model_options)


def run(state, repository, models, context=CTX):
    return asyncio.run(rescue_bridge(state, context, repository, models))


def last_decision(state):
    return state.bridge_trace[-1]


def test_explicit_relation_rescues_intact_source_without_mutating_normal_rankings():
    state, repository, models = case()
    before = deepcopy((state.search_results, state.rerank_results, state.merge_results))
    run(state, repository, models)
    assert len(state.rescue_evidence)==1
    rescued=state.rescue_evidence[0]
    assert rescued.id=='second' and rescued.content==repository.rows['second'].content
    assert rescued.start_at==100 and rescued.end_at==100+len(rescued.content)
    assert rescued.score==.25 and rescued is not repository.rows['second']
    assert before==(state.search_results,state.rerank_results,state.merge_results)
    details=rescued.metadata['bridge_rescue']
    assert details['first_rerank_score']==.02 and details['second_rerank_score']==.91
    assert details['relation_status']=='EXACT_OBJECT_LOCATION'
    assert len(models.chat_calls)==len(models.rerank_calls)==1
    messages,options,child=models.chat_calls[0]
    assert options.temperature==0 and options.send_temperature is True
    assert options.thinking is False and options.max_tokens==256
    assert options.format=='json' and child.request_id=='run/bridge-extract'
    assert child.scope is CTX.scope
    payload=json.loads(messages[-1].content)
    assert payload['question']==QUESTION and all('text' in w for w in payload['windows'])
    assert models.rerank_calls[0][0]=='Where is Arbor Poetry Seminar located?'
    assert models.rerank_calls[0][1]==(repository.rows['second'].content,)
    assert models.rerank_calls[0][2].request_id=='run/bridge-rerank'
    assert models.role_calls[0][1]=='cheap-snapshot'


def test_unicode_window_offsets_are_original_character_offsets():
    state,_,_=case(); main=state.search_results[0]
    windows=fact_windows(main)
    data=validate_extraction(json.dumps(extraction(main)),windows,{main.id:main},QUESTION)
    assert data['quote_start']==len('序言。\n')
    assert data['quote_start']!=len('序言。\n'.encode('utf-8'))
    assert main.content[data['quote_start']:data['quote_end']]==QUOTE
    for window in windows:
        assert window.text==main.content[window.start:window.end]
    assert list(sentence_spans(' α。\nβ!'))==[(1,3),(4,6)]


@pytest.mark.parametrize('changes',[
    {'quote_start':True}, {'quote_start':'4'}, {'quote_end':99999}, {'quote':'fabricated'},
    {'subject':'Jeanne Marie Beaumont'}, {'object':'Imagined Seminar'}, {'time':'2019'},
    {'source_chunk_id':'not-admitted'}, {'target_attribute':'postcode'},
    {'bridge_search_phrases':['Poetry Sem']}, {'bridge_search_phrases':['The']},
    {'bridge_search_phrases':['Arbor Poetry Seminar invented alias']},
    {'confidence':.99}, {'subject_resolution':'VERIFIED'}])
def test_invalid_extractions_never_start_second_rerank(changes):
    state,repository,models=case()
    models.content=json.dumps(extraction(state.search_results[0],**changes))
    run(state,repository,models)
    assert not state.rescue_evidence and not models.rerank_calls
    assert last_decision(state)['decision']=='BRIDGE_EXTRACTION_UNVERIFIED'


@pytest.mark.parametrize('text',['not-json','```json\n{}\n```','[]',
    '{"status":"NO_FIRST_HOP","status":"FIRST_HOP_CANDIDATE"}',
    '{"status":"NO_FIRST_HOP","answer":"Harbor City"}'])
def test_strict_json_contract_rejects_fences_duplicates_and_extra_answers(text):
    state,repository,models=case();models.content=text
    run(state,repository,models)
    assert not models.rerank_calls and not state.rescue_evidence


def test_literal_subject_elsewhere_in_sentence_is_not_relation_proof():
    quote='Alice Bennett denied that Bob Smith directed the Arbor Poetry Seminar from 2011–2014.'
    main=row('first',quote)
    state,repository,models=case(main=main)
    models.content=json.dumps(dict(status='FIRST_HOP_CANDIDATE',source_chunk_id='first',
        quote_start=0,quote_end=len(quote),quote=quote,subject='Alice Bennett',
        subject_resolution='EXPLICIT',predicate='directed',object='Arbor Poetry Seminar',
        time='2011–2014',target_attribute='location',bridge_search_phrases=['Arbor Poetry Seminar']))
    run(state,repository,models)
    assert not models.rerank_calls and last_decision(state)['reason']=='RELATION_UNVERIFIED'


def test_chunk_ancestry_does_not_prove_fact_survived_final_merge():
    state,repository,models=case()
    state.merge_results=[row('merged','Unrelated retained raw text.',sub_chunk_ids=['first'])]
    run(state,repository,models)
    assert not models.rerank_calls
    assert last_decision(state)['reason']=='FIRST_HOP_QUOTE_NOT_IN_FINAL_EVIDENCE'


def test_missing_current_source_reader_skips_instead_of_using_history_reader():
    state,repository,models=case()
    repository.get_current_chunks=None
    async def unsafe(ids,context):
        raise AssertionError('Historical get_chunks fallback is forbidden')
    repository.get_chunks=unsafe
    run(state,repository,models)
    assert not models.chat_calls and not models.rerank_calls


def test_truncated_extraction_does_not_use_partial_json_or_retry():
    state,repository,models=case(finish_reason='length')
    run(state,repository,models)
    assert len(models.chat_calls)==1 and not models.rerank_calls


def test_pronoun_remains_unverified_despite_preceding_name_and_question_name():
    quote='She directed the Arbor Poetry Seminar from 2011–2014.'
    main=row('first',quote,pre_chunk_id='previous',chunk_index=2)
    previous=row('previous','Alice Bennett is a poet.',chunk_index=1,next_chunk_id='first')
    state,repository,models=case(main=main)
    repository.rows[previous.id]=previous
    models.content=json.dumps(dict(status='FIRST_HOP_CANDIDATE',source_chunk_id=main.id,
        quote_start=0,quote_end=len(quote),quote=quote,subject='She',
        subject_resolution='UNVERIFIED',predicate='directed',object='Arbor Poetry Seminar',
        time='2011–2014',target_attribute='location',bridge_search_phrases=['Arbor Poetry Seminar']))
    run(state,repository,models)
    assert len(models.chat_calls)==1 and not models.rerank_calls and not state.rescue_evidence
    assert last_decision(state)['reason']=='SUBJECT_IDENTITY_UNVERIFIED'
    assert last_decision(state)['span_verified'] and not last_decision(state)['subject_verified']
    assert any(x['role']=='preceding' for x in json.loads(models.chat_calls[0][0][-1].content)['windows'])


@pytest.mark.parametrize('index,next_id',[(0,'first'),(1,'other'),(3,'first')])
def test_preceding_source_requires_exact_adjacent_bidirectional_link(index,next_id):
    main=row('first','She directed a seminar.',pre_chunk_id='previous',chunk_index=2)
    state,repository,models=case(main=main)
    repository.rows['previous']=row('previous','Alice Bennett is a poet.',
        chunk_index=index,next_chunk_id=next_id)
    with pytest.raises(RetrievalScopeError):
        run(state,repository,models)
    assert not models.chat_calls and not models.rerank_calls


@pytest.mark.parametrize('location',[
    'Arbor Poetry Seminar is located in Another City, Canada.',
    'Arbor Poetry Seminar is located in Harbor City, France.'])
def test_same_name_conflicting_location_rejected_before_rerank(location):
    state,repository,models=case(extra=[row('other',location,document='other-place')])
    run(state,repository,models)
    assert not models.rerank_calls and not state.rescue_evidence
    assert last_decision(state)['reason']=='AMBIGUOUS_SAME_NAME_LOCATION'


def test_short_phrase_is_only_institution_location_clue_not_event_venue_equivalence():
    full='The Arbor Center Advanced Seminar'
    quote=f'Alice Bennett directed {full} from 2011–2014.'
    main=row('first',quote)
    state,repository,models=case(main=main)
    repository.rows['second'].content='The Arbor Center is a poetry institution located in Harbor City, Canada.'
    repository.rows['second'].end_at=100+len(repository.rows['second'].content)
    state.search_results[1]=deepcopy(repository.rows['second'])
    models.content=json.dumps(dict(status='FIRST_HOP_CANDIDATE',source_chunk_id='first',
        quote_start=0,quote_end=len(quote),quote=quote,subject='Alice Bennett',
        subject_resolution='EXPLICIT',predicate='directed',object=full,time='2011–2014',
        target_attribute='location',bridge_search_phrases=[full,'The Arbor Center']))
    run(state,repository,models)
    assert len(state.rescue_evidence)==1
    assert state.rescue_evidence[0].metadata['bridge_rescue']['relation_status']=='INSTITUTION_LOCATION_ONLY_EVENT_VENUE_UNVERIFIED'
    assert models.rerank_calls[0][0]==f'Where is {full} located?'


@pytest.mark.parametrize('field,value',[
    ('knowledge_base_id','elsewhere'),('knowledge_id','other-doc'),
    ('metadata',{'source_version':'changed','tenant_id':7}),
    ('chunk_metadata',{'source_version':'changed'}),('content','modified'),
    ('end_at',999),('content_rewritten',True),('is_enabled',False),
    ('chunk_type','summary'),('pre_chunk_id','forged-previous')])
def test_main_readback_mismatch_fails_closed_before_extraction(field,value):
    state,repository,models=case()
    setattr(repository.rows['first'],field,value)
    with pytest.raises((RetrievalScopeError,PermissionError)):
        run(state,repository,models)
    assert not models.chat_calls and not models.rerank_calls


def test_cross_tenant_candidate_is_rejected_after_extraction_before_rerank():
    state,repository,models=case()
    state.search_results[1].metadata['tenant_id']=99
    repository.rows['second'].metadata['tenant_id']=99
    with pytest.raises(RetrievalScopeError):
        run(state,repository,models)
    assert len(models.chat_calls)==1 and not models.rerank_calls


def test_source_summary_is_not_eligible_as_first_hop():
    state,repository,models=case()
    state.search_results[0].chunk_type='summary'
    run(state,repository,models)
    assert not models.chat_calls and not models.rerank_calls


@pytest.mark.parametrize('query',[
    'What man invented both the Leyden jar and the Tribometer?',
    'Increased lipolysis leads to higher P38 phosphorylation in adipose tissue.',
    'Who has a higher median campaign grade? Trump or Clinton?',
    'Where can I find the postal code of the seminar?',
    '这个研讨会的邮编在哪里？'])
def test_nonlocation_and_postcode_controls_do_not_call_models(query):
    state,repository,models=case();state.query=query
    run(state,repository,models)
    assert not models.chat_calls and not models.rerank_calls and not state.rescue_evidence


def test_normal_complete_location_needs_no_second_call():
    state,repository,models=case()
    main=state.search_results[0]
    main.content+=' Arbor Poetry Seminar is located in Harbor City, Canada.'
    main.end_at=main.start_at+len(main.content)
    repository.rows[main.id]=deepcopy(main)
    state.rerank_results=[deepcopy(main)];state.merge_results=[deepcopy(main)]
    run(state,repository,models)
    assert len(models.chat_calls)==1 and not models.rerank_calls
    assert last_decision(state)['decision']=='NORMAL_EVIDENCE_ALREADY_COMPLETE'


def test_one_physical_rerank_batch_is_required():
    extra=row('other','Arbor Poetry Seminar is located in Harbor City, Canada.',document='other')
    state,repository,models=case(extra=[extra],settings=RerankSettings(max_documents=1))
    run(state,repository,models)
    assert not models.rerank_calls and not state.rescue_evidence
    assert last_decision(state)['reason']=='SINGLE_RERANK_BATCH_REQUIRED'


def test_candidate_cap_skips_without_slicing_source_pool():
    extra=[row(f'other{i}','Arbor Poetry Seminar is located in Harbor City, Canada.',document=f'doc{i}')
           for i in range(8)]
    state,repository,models=case(extra=extra)
    run(state,repository,models)
    assert not models.rerank_calls and not state.rescue_evidence
    assert last_decision(state)['reason']=='CANDIDATE_COUNT'


@pytest.mark.parametrize('error,raises',[
    (ModelCallTimeout(sent=False),False), (ModelCallTimeout(sent=True),True),
    (ProviderError('LOCAL_EXECUTION_UNKNOWN_STOP'),True),
    (ProviderError('MODEL_CLOUD_DISABLED'),True),
    (ProviderError('LOCAL_EXECUTION_LIMIT_DENIED'),True),
    (CallCancelled(),True), (RunDeadlineExceeded(),True)])
def test_extraction_failure_contract_has_no_retry_or_model_fallback(error,raises):
    state,repository,models=case(chat_error=error)
    if raises:
        with pytest.raises(type(error)):
            run(state,repository,models)
    else:
        run(state,repository,models)
    assert len(models.chat_calls)==1 and not models.rerank_calls and not state.rescue_evidence


@pytest.mark.parametrize('phase',['chat','rerank'])
def test_unknown_reported_usage_stops_the_sequence(phase):
    options={f'{phase}_usage':TokenUsage()}
    state,repository,models=case(**options)
    with pytest.raises(ProviderError,match='LOCAL_EXECUTION_UNKNOWN_STOP'):
        run(state,repository,models)
    assert not state.rescue_evidence
    if phase=='chat':
        assert not models.rerank_calls


def test_cancelled_context_and_cancellation_after_extraction_propagate():
    event=asyncio.Event();event.set()
    state,repository,models=case()
    with pytest.raises(CallCancelled):
        run(state,repository,models,replace(CTX,cancelled=event))
    assert not models.chat_calls
    event=asyncio.Event()
    state,repository,models=case(after_chat=event.set)
    with pytest.raises(CallCancelled):
        run(state,repository,models,replace(CTX,cancelled=event))
    assert len(models.chat_calls)==1 and not models.rerank_calls


@pytest.mark.parametrize('scores',[
    [RerankResult(1,.99)], [RerankResult(0,float('nan'))],
    [RerankResult(0,1.1)], [RerankResult(True,.99)],
    [RerankResult(0,.99),RerankResult(0,.88)]])
def test_invalid_second_rerank_indices_and_scores_do_not_select_source(scores):
    state,repository,models=case(scores=scores)
    run(state,repository,models)
    assert not state.rescue_evidence and len(models.rerank_calls)==1


def test_no_source_slicing_to_force_second_chunk_budget():
    state,repository,models=case()
    second=state.search_results[1]
    second.content+=' x'*900
    second.end_at=second.start_at+len(second.content)
    repository.rows['second']=deepcopy(second)
    run(state,repository,models)
    assert not state.rescue_evidence
    assert not models.rerank_calls
    assert last_decision(state)['reason']=='SECOND_HOP_CHUNK_BUDGET'


@pytest.mark.parametrize('suffix,time',[
    (' from 2015–2018.','2015–2018'),
    (' and the Arbor Arts Workshop from 2011–2014.','2011–2014')])
def test_different_requested_time_or_multiple_relation_objects_remain_unverified(suffix,time):
    quote='Alice Bennett directed the Arbor Poetry Seminar'+suffix
    state,repository,models=case(main=row('first',quote))
    data=dict(status='FIRST_HOP_CANDIDATE',source_chunk_id='first',quote_start=0,
        quote_end=len(quote),quote=quote,subject='Alice Bennett',subject_resolution='EXPLICIT',
        predicate='directed',object='Arbor Poetry Seminar',time=time,target_attribute='location',
        bridge_search_phrases=['Arbor Poetry Seminar'])
    models.content=json.dumps(data)
    run(state,repository,models)
    assert not models.rerank_calls and last_decision(state)['reason']=='RELATION_UNVERIFIED'


def test_off_mode_and_repeated_same_state_do_not_make_additional_calls():
    state,repository,models=case();state.bridge_rescue_mode='off'
    before=deepcopy(state)
    run(state,repository,models)
    assert state.__dict__==before.__dict__ and not models.chat_calls
    state.bridge_rescue_mode=MODE
    run(state,repository,models)
    run(state,repository,models)
    assert len(models.chat_calls)==len(models.rerank_calls)==1


@pytest.mark.parametrize('mode',[True,None,'on','cheap_bridge_v2'])
def test_invalid_mode_fails_before_any_calls(mode):
    state,repository,models=case();state.bridge_rescue_mode=mode
    with pytest.raises(ValueError,match='BRIDGE_RESCUE_MODE_INVALID'):
        run(state,repository,models)
    assert not models.chat_calls and not models.rerank_calls
