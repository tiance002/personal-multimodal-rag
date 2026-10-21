"""Bounded dependent recall over verified originals; no planning or answers.

Only the closed direct predicates below establish semantic support. A literal
name prefix is a retrieval clue and never an entity alias or an event venue.
"""
from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import asdict, dataclass, replace
from enum import Enum
import json
import math
import re

from ..models.cloud_errors import raise_if_send_denied
from ..models.request import split_batches
from ..models.types import ProviderError
from .bridge_rescue import (ADDED_CHARS, CANDIDATES, CONTRACT_VERSION,
    SECOND_THRESHOLD, BridgePlan, BridgeRejected, _check,
    _literal_prefix_search_clues, _phrase_positions, _read_originals,
    _require_known_usage, _trace, prepare_verified_bridge, sentence_spans)
from .search import RetrievalScopeError, validate_results, validate_targets
from .source_evidence import SourceEvidenceBudgetError, _domain, _validate_source


class BridgeStatus(str, Enum):
    COMPLETE = 'COMPLETE'
    RELATED_ONLY = 'RELATED_ONLY'
    NO_CANDIDATE = 'NO_CANDIDATE'
    UNVERIFIED = 'UNVERIFIED'
    CONFLICT = 'CONFLICT'


@dataclass(frozen=True)
class LocationFact:
    source_chunk_id: str
    knowledge_base_id: str
    knowledge_id: str
    source_version: str
    quote_start: int
    quote_end: int
    quote: str
    phrase: str
    attribute: str
    location_description: str
    exact_entity: bool


@dataclass(frozen=True)
class BridgeAssessment:
    status: BridgeStatus
    reason: str
    source_validity: str = 'NOT_CHECKED'
    semantic_support: str = 'UNVERIFIED'
    evidence: tuple = ()
    facts: tuple[LocationFact, ...] = ()
    scores: tuple[tuple[str, float], ...] = ()
    q1: str = ''
    new_recall: bool = False
    delivered: bool = False
    final_answer_supported: bool = False
    budget_omitted: bool = False
    context_admitted: bool = False
    source_chain_supported: bool = False
    required_source_ids: tuple[str, ...] = ()
    observed_sources: tuple = ()


_UNSAFE = re.compile(
    r'\b(?:not|never|no|may|might|could|would|should|unlikely|allegedly|possibly|'
    r'formerly|previously|historically|originally|until|before|after|during|'
    r'or|and|either|whether|who|which|where|while|but|according|painting|'
    r'story|novel|fictional|fiction)\b|不|未|可能|曾经|以前|过去|或|虚构|小说', re.I)
_DISQUALIFY = re.compile(r'\b(?:not|never|may|might|could|would|should|unlikely|'
    r'allegedly|possibly|formerly|previously|historically|originally|until|before|after)\b|'
    r'不|未|可能|曾经|以前|过去', re.I)


def _attribute(question):
    """A user's explicit event request changes only the requested predicate."""
    if re.search(r'\bwhere\b[^?？]*\bheld\b|(?:哪|何处)[^。？?]{0,40}(?:举行|举办)|'
                 r'(?:举行|举办)[^。？?]{0,40}(?:哪|何处)', question, re.I):
        return 'event_venue'
    return 'location'


def _question_matches_plan(plan, attribute):
    """Bind a narrow location request to the verified object's head noun.

    An accurate first-hop sentence about a person does not prove that every
    ``where`` question about that person asks for the first-hop object's
    location. Unsupported wording stays unverified rather than gaining an
    arbitrary target from the extraction model.
    """
    def canonical(value):
        value=re.sub(r'(?<=\d)[–—-](?=\d)','-',value)
        return re.sub(r'\s+',' ',value).strip().rstrip('?？').casefold()
    # q0 owns the answer requirement; the validated effective query may only
    # supply the literal antecedent needed by a history-dependent question.
    questions={canonical(plan.q0),canonical(plan.effective_query)}
    if canonical(f'Where is {plan.object} located?') in questions and attribute=='location':
        return True
    if canonical(f'Where was {plan.object} held?') in questions and attribute=='event_venue':
        return True
    noun=re.search(r'([A-Za-z][A-Za-z-]*)$',plan.object)
    if not noun:
        return False
    predicate=plan.extraction['predicate']
    verbs=[predicate]
    role=re.fullmatch(r'(?:served as|was) (?:the )?(director|head|chair)(?: of)?',predicate,re.I)
    if role:
        verbs.append({'director':'directed','head':'headed','chair':'chaired'}[role[1].casefold()])
    when=plan.extraction['time']
    temporals=[f' {preposition} {when}' for preposition in ('in','from','during')] if when else ['']
    for verb in verbs:
        for temporal in temporals:
            entity=f'the {noun[1]} {plan.identity_proof.resolved_name} {verb}{temporal}'
            expected=(f'Where was {entity} held?' if attribute=='event_venue' else
                      f'Where is {entity} located?')
            if canonical(expected) in questions:
                return True
    return False


def _place(value):
    value = value.strip(' .。!?！？')
    if (not 2 <= len(value) <= 320 or _UNSAFE.search(value)
            or re.search(r'\b\d{4}\b|[;；\n]', value)
            or re.fullmatch(r'here|there|somewhere|nearby|unknown', value, re.I)
            or not re.match(r'[A-Z][A-Za-z]+|[\u3400-\u9fff]{2}|\d+\.\d+|\d+\s+[A-Z]', value)):
        return None
    return value


def _direct_facts(row, phrase, attribute, exact):
    facts, mentioned, negative = [], False, False
    for start, end in sentence_spans(row.content):
        text = row.content[start:end]
        # Subject and closed predicate checks establish the complete boundary;
        # CJK subjects do not require an invented space before 位于/在.
        for occurrence in re.finditer(re.escape(phrase),text,re.I):
            a,b=occurrence.span()
            subject = bool(re.fullmatch(r'\s*(?:(?:the|an?)\s+)?', text[:a], re.I))
            # A first-hop sentence naturally mentions its object. That alone
            # is absence of the second relation, not a malformed location.
            mentioned |= subject or bool(re.search(r'\b(?:located|based|situated|held)\b|位于|坐落|举行|举办',text,re.I))
            if not subject:
                continue
            tail = text[b:]
            if re.search(r'\b(?:not|never)\b|不|未', tail, re.I):
                negative = True
            if _DISQUALIFY.search(tail):
                continue
            if attribute == 'event_venue':
                match = re.fullmatch(r'\s+(?:was|is)\s+held\s+(?:at|in)\s+(.+?)[.!?。！？]*', tail, re.I)
                if match is None:
                    match = re.fullmatch(r'\s*在\s*(.+?)\s*(?:举行|举办)[。！？]*', tail)
            else:
                # A past location, or an arbitrary clause between the subject
                # and predicate, cannot prove the current location.
                classifier = (r'(?:(?:a|an|the)\s+(?:(?:museum|nonprofit|educational|'
                    r'center|centre|for|poetry|institution|and|cultural|arts|'
                    r'organization|organisation)\s+){1,16})?')
                match = re.fullmatch(r'\s+is\s+'+classifier+r'(?:located|based|situated)\s+(?:at|in|on)\s+(.+?)[.!?。！？]*', tail, re.I)
                if match is None:
                    match = re.fullmatch(r'\s*(?:位于|坐落于)\s*(.+?)[。！？]*', tail)
            place = _place(match[1]) if match is not None else None
            if place:
                facts.append(LocationFact(row.id, row.knowledge_base_id, row.knowledge_id,
                    row.metadata['source_version'], start, end, text, phrase, attribute, place, exact))
    return facts, mentioned, negative


def assess_location_sources(plan, sources):
    """Assess exact current-source rows supplied by the scoped readback caller.

    This checks field and span validity. The async caller separately performs
    authoritative current-source readback and scope enforcement.
    """
    exact, related, mentioned, negative = [], [], False, False
    sources = tuple(sources)
    for row in sources:
        _validate_source(row, _domain(row))
        found, seen, neg = _direct_facts(row, plan.object, plan.target_attribute, True)
        exact.extend(found)
        mentioned |= seen
        negative |= neg
        for clue in dict.fromkeys((*plan.extraction['bridge_search_phrases'],
                                   *_literal_prefix_search_clues(plan.object))):
            if clue.casefold() == plan.object.casefold():
                continue
            # Institutions may supply a location clue even when the question
            # explicitly asks where the complete event was held.
            found, _, _ = _direct_facts(row, clue, 'location', False)
            related.extend(found)
    canonical = lambda fact: re.sub(r'\s+', ' ', fact.location_description).casefold()
    if len({canonical(fact) for fact in exact}) > 1 or exact and negative:
        return BridgeAssessment(BridgeStatus.CONFLICT, 'CONFLICTING_EXACT_OBJECT_FACTS',
            'SOURCE_FIELDS_VALID', 'CONFLICT', sources, tuple(exact), q1=plan.q1)
    if exact:
        ids = {fact.source_chunk_id for fact in exact}
        return BridgeAssessment(BridgeStatus.COMPLETE, 'DIRECT_COMPLETE_OBJECT_RELATION',
            'SOURCE_FIELDS_VALID', 'DIRECT_SUPPORTED',
            tuple(row for row in sources if row.id in ids), tuple(exact), q1=plan.q1)
    if len({canonical(fact) for fact in related}) > 1:
        return BridgeAssessment(BridgeStatus.CONFLICT, 'CONFLICTING_RELATED_LOCATION_CLUES',
            'SOURCE_FIELDS_VALID', 'CONFLICT', sources, tuple(related), q1=plan.q1)
    if related and not negative:
        ids = {fact.source_chunk_id for fact in related}
        return BridgeAssessment(BridgeStatus.RELATED_ONLY,
            'INSTITUTION_LOCATION_ONLY_EVENT_VENUE_UNVERIFIED', 'SOURCE_FIELDS_VALID',
            'RELATED_ONLY', tuple(row for row in sources if row.id in ids), tuple(related), q1=plan.q1)
    if mentioned:
        return BridgeAssessment(BridgeStatus.UNVERIFIED, 'DIRECT_RELATION_UNVERIFIED',
            'SOURCE_FIELDS_VALID', 'UNVERIFIED', q1=plan.q1)
    return BridgeAssessment(BridgeStatus.NO_CANDIDATE, 'SECOND_HOP_SOURCE_NOT_IN_POOL',
        'SOURCE_FIELDS_VALID', 'NOT_FOUND_IN_SEEN', q1=plan.q1)


def _scope(plan, state, context):
    if (plan.q0 != state.query or plan.effective_query != (state.rewrite_query or state.query)
            or plan.targets != tuple(state.targets) or plan.authorization_scope != context.scope):
        raise RetrievalScopeError()
    validate_targets(state.targets, context)


async def rerank_bridge_pool(plan, pool, state, context, repository, registry):
    """One BGE call over the existing rejected pool, never a new Search."""
    _scope(plan, state, context)
    if not pool:
        return BridgeAssessment(BridgeStatus.NO_CANDIDATE, 'SECOND_HOP_SOURCE_NOT_IN_POOL',
            semantic_support='NOT_FOUND_IN_SEEN', q1=plan.q1)
    if len(pool) > CANDIDATES or len({row.id for row in pool}) != len(pool):
        return BridgeAssessment(BridgeStatus.UNVERIFIED, 'CANDIDATE_COUNT', q1=plan.q1)
    pool = await _read_originals(pool, state, context, repository)
    observed=tuple(pool)
    assessment = replace(assess_location_sources(plan, pool),observed_sources=observed)
    if assessment.status not in (BridgeStatus.COMPLETE, BridgeStatus.RELATED_ONLY):
        return replace(assessment, source_validity='CURRENT_SOURCE_VERIFIED')
    pool = [row for row in assessment.evidence if len(row.content) <= ADDED_CHARS]
    if not pool:
        return BridgeAssessment(BridgeStatus.UNVERIFIED, 'SECOND_HOP_CHUNK_BUDGET', q1=plan.q1)
    child = context.child('strategy/bridge-rerank')
    model = await registry.rerank(state.rerank_model_id, child)
    settings = model.config.reranking
    passages = tuple(row.content for row in pool)
    batches = split_batches(passages, max_items=settings.max_documents,
        max_item_chars=settings.max_document_chars, max_total_chars=settings.max_request_chars,
        fixed_chars=len(plan.q1))
    if len(batches) != 1 or settings.max_query_chars and len(plan.q1) > settings.max_query_chars:
        return BridgeAssessment(BridgeStatus.UNVERIFIED, 'SINGLE_RERANK_BATCH_REQUIRED', q1=plan.q1)
    _check(context)
    _trace(state, 'STRATEGY_SECOND_RERANK_INPUT', q1=plan.q1,
        candidate_ids=[row.id for row in pool], threshold=SECOND_THRESHOLD)
    response = await model.rerank(plan.q1, passages, child)
    _require_known_usage(response)
    _check(context)
    raw = list(response.results)
    if (len(raw) != len(pool) or {r.index for r in raw} != set(range(len(pool)))
            or any(type(r.index) is not int or type(r.score) not in (int, float)
                   or not math.isfinite(r.score) or not 0 <= r.score <= 1 for r in raw)):
        raise ValueError('BRIDGE_RERANK_RESPONSE_INVALID')
    scores = tuple((pool[result.index].id, float(result.score)) for result in raw)
    _trace(state, 'STRATEGY_SECOND_RERANK_OUTPUT', scores=[{'id':key,'score':value} for key,value in scores])
    eligible = [pool[r.index] for r in sorted(raw, key=lambda r:(-r.score,r.index))
                if r.score >= SECOND_THRESHOLD]
    admitted = assess_location_sources(plan, eligible)
    if admitted.status == BridgeStatus.NO_CANDIDATE:
        admitted = replace(admitted, reason='SECOND_HOP_NOT_ADMITTED')
    if admitted.evidence:
        best = admitted.evidence[0]
        admitted = replace(admitted, evidence=(best,), facts=tuple(
            fact for fact in admitted.facts if fact.source_chunk_id == best.id))
    return replace(admitted, scores=scores, source_validity='CURRENT_SOURCE_VERIFIED',observed_sources=observed)


def _metadata(plan, assessment, fact, score):
    exact = assessment.status == BridgeStatus.COMPLETE and fact.exact_entity
    return {'first_hop_chunk_id':plan.extraction['source_chunk_id'],
        'quote_start':plan.extraction['quote_start'], 'quote_end':plan.extraction['quote_end'],
        'first_hop_quote':plan.extraction['quote'], 'q1':plan.q1, 'object':plan.object,
        'target_attribute':plan.target_attribute, 'matched_phrase':fact.phrase,
        'second_rerank_score':score, 'location_fact':asdict(fact),
        'identity_proof':asdict(plan.identity_proof), 'contract_version':CONTRACT_VERSION,
        'entity_bridge_verified':exact, 'final_answer_supported':False,
        'semantic_support':assessment.semantic_support,
        'relation_status':('EXACT_EVENT_VENUE' if plan.target_attribute=='event_venue' else
                           'EXACT_OBJECT_LOCATION') if exact else
                          'INSTITUTION_LOCATION_ONLY_EVENT_VENUE_UNVERIFIED'}


async def _admit(plan, assessment, state, context, repository):
    """Atomically append read-back facts/identity only if both encoders fit."""
    if assessment.status not in (BridgeStatus.COMPLETE, BridgeStatus.RELATED_ONLY):
        return assessment
    from .bridge_identity import admit_identity_context, prove_identity
    from .context import into_chat_message
    from .types import context_results
    from ..generation.context import Evidence, escape_text, prepare_generation
    from ..generation.types import GenerationRequest
    _scope(plan,state,context)
    trial = copy(state)
    trial.bridge_trace = deepcopy(state.bridge_trace)
    trial.rescue_evidence = deepcopy(state.rescue_evidence)
    trial.bridge_identity_evidence = deepcopy(getattr(state,'bridge_identity_evidence',[]))
    trial.bridge_normal_qualifications = deepcopy(state.bridge_normal_qualifications)
    present = {row.id:row for row in context_results(trial)}
    scores = dict(assessment.scores)
    for source in assessment.evidence:
        fact = next(f for f in assessment.facts if f.source_chunk_id == source.id)
        details = _metadata(plan,assessment,fact,scores.get(source.id))
        details['first_rerank_score'] = getattr(state,'strategy_rerank_scores',{}).get(source.id)
        if source.id in present:
            current = present[source.id]
            if (_domain(current) != _domain(source) or current.content != source.content
                    or current.start_at != source.start_at or current.end_at != source.end_at):
                raise RetrievalScopeError()
            if not fact.exact_entity:
                trial.bridge_normal_qualifications[source.id] = {
                    'source_chunk_id':source.id,'knowledge_id':source.knowledge_id,
                    'knowledge_base_id':source.knowledge_base_id,
                    'source_version':source.metadata['source_version'],'quote':fact.quote,
                    'quote_start':fact.quote_start,'quote_end':fact.quote_end,
                    'object':plan.object,'matched_phrase':fact.phrase,
                    'location_fact':asdict(fact),'entity_bridge_verified':False,
                    'final_answer_supported':False,'relation_status':details['relation_status']}
        else:
            raw = deepcopy(source)
            raw.knowledge_description = raw.knowledge_custom_metadata = raw.context_header = ''
            raw.metadata['bridge_rescue'] = details
            trial.rescue_evidence.append(raw)
            present[source.id] = raw
    # Every admission rechecks the complete original chain, even if its name
    # anchor is already visible. A recall callback cannot stale the first hop
    # or turn the original proof into authority over another source version.
    fresh_chain=await _read_originals(plan.predecessor_chain,state,context,repository)
    current_identity=prove_identity(plan.effective_query,fresh_chain,
        selected_chunk_id=plan.extraction['source_chunk_id'],
        quote_start=plan.extraction['quote_start'],quote_end=plan.extraction['quote_end'],
        normal_results=context_results(trial))
    if (not current_identity.verified or
            current_identity.resolved_name!=plan.identity_proof.resolved_name or
            current_identity.anchor_quotes!=plan.identity_proof.anchor_quotes):
        raise RetrievalScopeError()
    if not current_identity.final_context_identity_supported:
        await admit_identity_context(trial, context, repository, plan.extraction,
                                     fresh_chain, plan.identity_proof)
    current_identity = prove_identity(plan.effective_query, fresh_chain,
        selected_chunk_id=plan.extraction['source_chunk_id'],
        quote_start=plan.extraction['quote_start'],quote_end=plan.extraction['quote_end'],
        normal_results=context_results(trial))
    supported = bool(assessment.status == BridgeStatus.COMPLETE and
                     current_identity.final_context_identity_supported)
    for row in trial.rescue_evidence:
        details = row.metadata.get('bridge_rescue',{})
        if details.get('q1') == plan.q1 and details.get('entity_bridge_verified'):
            details['final_answer_supported'] = supported
            details['identity_proof'] = asdict(current_identity)
    try:
        if state.rerank_top_k > 0 and len(context_results(trial)) > state.rerank_top_k:
            raise SourceEvidenceBudgetError('BRIDGE_GLOBAL_TOPK_BUDGET_EXCEEDED')
        finalized = await into_chat_message(trial, context, record_bridge=False)
        bridge_limit=state.bridge_context_char_limit
        if len(finalized.rendered_contexts)>bridge_limit:
            raise SourceEvidenceBudgetError('BRIDGE_CONTEXT_BUDGET_EXCEEDED')
        finalized=replace(finalized,context_char_limit=min(finalized.context_char_limit or bridge_limit,bridge_limit))
        prepared = prepare_generation(GenerationRequest(context.request_id,
            context.session_id or 'bridge-context-preview','bridge-context-preview',
            state.query,finalized,evidence=tuple(Evidence.from_result(row) for row in context_results(trial)),
            language=state.language,citations_enabled=True))
        # A source is delivered only when its exact raw body survives the real
        # generation encoder. No extracted JSON or ancestry ID substitutes it.
        wire = '\n'.join(message['content'] for message in json.loads(prepared.full_prompt))
        if not all(source.id in finalized.result_ids and escape_text(source.content) in wire
                   for source in assessment.evidence):
            raise SourceEvidenceBudgetError('BRIDGE_SOURCE_NOT_IN_GENERATION_MESSAGES')
    except SourceEvidenceBudgetError as error:
        _trace(state,'STRATEGY_BRIDGE_BUDGET_OMITTED',reason=str(error),
               source_ids=[row.id for row in assessment.evidence],q1=plan.q1)
        return replace(assessment,reason=str(error),delivered=False,final_answer_supported=False,budget_omitted=True)
    _check(context)
    state.rescue_evidence = trial.rescue_evidence
    state.bridge_identity_evidence = trial.bridge_identity_evidence
    state.bridge_normal_qualifications = trial.bridge_normal_qualifications
    state.bridge_trace = trial.bridge_trace
    required=tuple(dict.fromkeys((plan.extraction['source_chunk_id'],
        *(anchor.source_chunk_id for anchor in plan.identity_proof.anchor_quotes),
        *(row.id for row in assessment.evidence))))
    _trace(state,'STRATEGY_BRIDGE_CONTEXT_ADMITTED',source_ids=[row.id for row in assessment.evidence],
           q1=plan.q1,semantic_support=assessment.semantic_support,
           source_chain_supported=supported,required_source_ids=list(required),delivered=False)
    # This is a preview, not the actual model-send seam. The root generation
    # observer promotes delivery only after checking the actual messages.
    return replace(assessment,context_admitted=True,source_chain_supported=supported,
        required_source_ids=required,delivered=False,final_answer_supported=False)


def _audit(state, assessment):
    _trace(state,'STRATEGY_BRIDGE_ASSESSMENT',status=assessment.status.value,
        reason=assessment.reason,q1=assessment.q1,new_recall=assessment.new_recall,
        source_validity=assessment.source_validity,semantic_support=assessment.semantic_support,
        source_ids=[row.id for row in assessment.evidence],facts=[asdict(f) for f in assessment.facts],
        context_admitted=assessment.context_admitted,source_chain_supported=assessment.source_chain_supported,
        required_source_ids=list(assessment.required_source_ids),budget_omitted=assessment.budget_omitted,
        delivered=assessment.delivered,final_answer_supported=assessment.final_answer_supported)
    return assessment


def _seen_sources(*groups):
    from .bridge_rescue import _readback_equal
    seen={}
    for group in groups:
        for row in group:
            if row.id in seen:
                _readback_equal(seen[row.id],row)
            else:
                seen[row.id]=row
    return tuple(seen.values())


async def rescue_strategy_bridge(state, context, repository, registry, retrieve_once_callback):
    """Reuse one proven first hop and, at most, one new scoped Q1 retrieval."""
    if (getattr(state,'retrieval_strategy_mode','off') != 'query_structure_v1'
            or not state.needs_retrieval):
        return BridgeAssessment(BridgeStatus.UNVERIFIED,'STRATEGY_NOT_ENABLED')
    prior = getattr(state,'_strategy_bridge_request',None)
    if prior == context.request_id:
        return getattr(state,'_strategy_bridge_assessment',BridgeAssessment(
            BridgeStatus.UNVERIFIED,'BRIDGE_ALREADY_ATTEMPTED'))
    state._strategy_bridge_request = context.request_id
    assessment=BridgeAssessment(BridgeStatus.UNVERIFIED,'FIRST_HOP_NOT_PREPARED')
    try:
        plan = await prepare_verified_bridge(state,context,repository,registry)
        if isinstance(plan,BridgeRejected):
            assessment = BridgeAssessment(BridgeStatus.UNVERIFIED,plan.reason)
        else:
            attribute = _attribute(plan.q0)
            if not _question_matches_plan(plan,attribute):
                raise ValueError('QUESTION_BRIDGE_TARGET_UNVERIFIED')
            q1 = (f'Where was {plan.object} held?' if attribute == 'event_venue' else
                  f'Where is {plan.object} located?')
            if len(q1) > 192:
                raise ValueError('BRIDGE_QUERY_TOO_LONG')
            plan = replace(plan,target_attribute=attribute,q1=q1)
            _trace(state,'STRATEGY_BRIDGE_PLAN',q1=q1,object=plan.object,target_attribute=attribute,
                first_hop_chunk_id=plan.extraction['source_chunk_id'],
                identity_proof=asdict(plan.identity_proof))
            from .types import context_results
            visible = [row for row in context_results(state) if row.chunk_type == 'text']
            normal = await _read_originals(visible,state,context,repository)
            seen=tuple(normal)
            assessment = replace(assess_location_sources(plan,normal),source_validity='CURRENT_SOURCE_VERIFIED')
            _audit(state,assessment)
            if assessment.status in (BridgeStatus.COMPLETE,BridgeStatus.RELATED_ONLY):
                assessment = await _admit(plan,assessment,state,context,repository)
            if not assessment.budget_omitted and assessment.status in (BridgeStatus.NO_CANDIDATE,BridgeStatus.RELATED_ONLY):
                scores = dict(getattr(state,'strategy_rerank_scores',{}))
                accepted = set(getattr(state,'strategy_rerank_accepted_ids',()))
                if not scores:
                    assessment = BridgeAssessment(BridgeStatus.UNVERIFIED,'STRUCTURED_FIRST_RERANK_MISSING',q1=q1)
                else:
                    final_ids = {row.id for row in context_results(state)}
                    clues = (plan.object,*plan.extraction['bridge_search_phrases'],
                             *_literal_prefix_search_clues(plan.object))
                    pool = [row for row in state.search_results if row is not None and
                        row.chunk_type=='text' and row.id not in final_ids and row.id not in accepted and
                        row.id in scores and scores[row.id] < state.rerank_threshold and
                        any(any(_phrase_positions(row.content,clue)) for clue in clues)]
                    pooled = await rerank_bridge_pool(plan,pool,state,context,repository,registry)
                    seen=_seen_sources(seen,pooled.observed_sources)
                    combined=assess_location_sources(plan,seen)
                    if combined.status==BridgeStatus.CONFLICT:
                        pooled=replace(combined,source_validity='CURRENT_SOURCE_VERIFIED')
                    _audit(state,pooled)
                    if pooled.status in (BridgeStatus.COMPLETE,BridgeStatus.RELATED_ONLY):
                        pooled = await _admit(plan,pooled,state,context,repository)
                    if pooled.budget_omitted or pooled.status == BridgeStatus.COMPLETE or pooled.status in (BridgeStatus.CONFLICT,BridgeStatus.UNVERIFIED):
                        assessment = pooled
                    elif assessment.status != BridgeStatus.RELATED_ONLY:
                        assessment = pooled
                    if not assessment.budget_omitted and assessment.status in (BridgeStatus.RELATED_ONLY,BridgeStatus.NO_CANDIDATE):
                        _scope(plan,state,context)
                        _check(context)
                        recalled = await retrieve_once_callback(q1,context.child('strategy/round-1'))
                        _scope(plan,state,context)
                        _check(context)
                        if recalled.status == 'BUDGET_OMITTED':
                            assessment = replace(assessment,reason=recalled.reason,new_recall=False,budget_omitted=True)
                        elif recalled.status == 'EMPTY':
                            assessment = replace(assessment,reason='SECOND_HOP_SOURCE_NOT_IN_AUTHORIZED_RECALL',new_recall=True)
                        elif recalled.status != 'OK':
                            raise RuntimeError('BRIDGE_RECALL_INFRASTRUCTURE_ERROR')
                        else:
                            child = recalled.state
                            if (tuple(child.targets) != plan.targets or child.query != q1
                                    or child.rewrite_query != q1):
                                raise RetrievalScopeError()
                            candidates = [row for row in child.merge_results if row.chunk_type=='text']
                            validate_results(candidates,state.targets,context)
                            current = await _read_originals(candidates,state,context,repository)
                            found = replace(assess_location_sources(plan,current),
                                source_validity='CURRENT_SOURCE_VERIFIED',new_recall=True,
                                scores=tuple(getattr(child,'strategy_rerank_scores',{}).items()))
                            combined=assess_location_sources(plan,_seen_sources(seen,current))
                            if combined.status==BridgeStatus.CONFLICT:
                                found=replace(combined,source_validity='CURRENT_SOURCE_VERIFIED',new_recall=True)
                            if found.evidence:
                                chosen=found.evidence[0]
                                found=replace(found,evidence=(chosen,),facts=tuple(
                                    fact for fact in found.facts if fact.source_chunk_id==chosen.id))
                            if found.status in (BridgeStatus.COMPLETE,BridgeStatus.RELATED_ONLY):
                                found = await _admit(plan,found,state,context,repository)
                            if found.status == BridgeStatus.NO_CANDIDATE and assessment.status == BridgeStatus.RELATED_ONLY:
                                assessment = replace(assessment,new_recall=True,
                                    reason='SECOND_HOP_SOURCE_NOT_IN_AUTHORIZED_RECALL')
                            else:
                                assessment = found
    except ProviderError as error:
        # The local ledger rejected this auxiliary operation before transport.
        # Preserve any earlier verified facts and stop creating new branches;
        # UNKNOWN, scope denial and every possibly sent failure remain hard
        # stops. The legacy bridge wrapper intentionally keeps its contract.
        if error.code!='LOCAL_EXECUTION_LIMIT_DENIED' or error.sent is not False:
            raise
        assessment=replace(assessment,reason=error.code,budget_omitted=True,
            delivered=False,final_answer_supported=False)
    except ValueError as error:
        raise_if_send_denied(error)
        if getattr(error,'sent',False):
            raise
        assessment = BridgeAssessment(BridgeStatus.UNVERIFIED,str(error))
    state._strategy_bridge_assessment = _audit(state,assessment)
    return assessment
