"""Bounded identity proof over independently authorized original sources.

This accepts a narrow biography/surname/pronoun grammar, not general
coreference.  A model cannot establish names, aliases, offsets or continuity.
The caller authorizes each source through the scoped current-source reader.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re

from .search import RetrievalScopeError
from .source_evidence import _domain, _validate_source


@dataclass(frozen=True)
class IdentityQuote:
    source_chunk_id: str
    quote_start: int
    quote_end: int
    quote: str


@dataclass(frozen=True)
class IdentityProof:
    verified: bool
    resolved_name: str | None
    anchor_quotes: tuple[IdentityQuote, ...]
    final_context_identity_supported: bool
    reason: str


_NAME = r"[A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)?"
_FULL_SUBJECT = re.compile(rf'(?P<name>{_NAME}(?:[ \t]+{_NAME}){{1,3}})\s+(?P<tail>.+)', re.S)
_HUMAN_ROLE = re.compile(
    r'(?:[Ii]s|[Ww]as)\s+(?:(?:an?|the)\s+)?'
    r'(?:(?:[A-Z][A-Za-z-]+|well-known|award-winning|retired|former|contemporary|local)\s+){0,2}'
    r'(?i:poet|author|writer|novelist|editor|co-editor|teacher|professor|educator|artist|'
    r'scientist|researcher|engineer|physician|historian|director|politician)\b')
_PERSON_ACTION = re.compile(
    r'(?:directed|led|headed|served as\s+(?:the\s+)?(?:director|head|chair|president|dean))\b', re.I)
_DIRECT = re.compile(r'(?:directed|led|headed|served as|was|is)\b', re.I)
_CONTINUATION = re.compile(
    r'(?:was\s+(?:a\s+|the\s+)?judge\s+(?:for|of)\b|'
    r'grew up\s+in\b|moved\s+to\b|lived\s+(?:in|at)\b|'
    r'earned\s+(?:her|his)\b|(?:has\s+|had\s+)?taught\s+(?:at|in)\b|'
    r'(?:regularly\s+)?teaches\s+at\b|directed\b|led\b|headed\b|'
    r'served as\s+(?:the\s+)?(?:director|head|chair|president|dean)\b|'
    r'serves on\s+(?:the\s+)?faculty\b|'
    r'was\s+(?:a\s+|the\s+)?co-editor\s+of\b)', re.I)
_RELATIVE_OR_SPEECH = re.compile(r'\b(?:who|whom|whose|which|while|said|says|told|replied)\b', re.I)
_PERSON_ROLE = re.compile(
    r'\b(?:colleague|sister|brother|mother|father|wife|husband|daughter|son|'
    r'friend|woman|man|person|technician)\b', re.I)
_ORG_SUFFIX = (r'(?:Seminar|Workshop|Program|University|College|Award|City|School|'
               r'Academy|Institute|Center|Centre|Review|Writing|Y)')
_ORGANIZATION = re.compile(
    rf'\b(?:[A-Z][A-Za-z\d\'-]*\s+){{0,8}}{_ORG_SUFFIX}\b')


def _unverified(reason):
    return IdentityProof(False, None, (), False, reason)


def _whole(text, phrase):
    return bool(re.search(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', text, re.I))


def _validate_chain(sources, selected_chunk_id):
    if not sources or len(sources) > 4 or sources[-1].id != selected_chunk_id:
        raise ValueError('BRIDGE_IDENTITY_CHAIN_INVALID')
    if len({row.id for row in sources}) != len(sources):
        raise RetrievalScopeError()
    domain = _domain(sources[0])
    if not all(domain):
        raise RetrievalScopeError()
    optional = {name: (name in sources[0].metadata, sources[0].metadata.get(name))
                for name in ('tenant_id', 'owner_id')}
    for row in sources:
        _validate_source(row, domain)
        if type(row.chunk_index) is not int or row.chunk_index < 0:
            raise RetrievalScopeError()
        if any((name in row.metadata, row.metadata.get(name)) != identity
               for name, identity in optional.items()):
            raise RetrievalScopeError()
    overlaps = [0]
    for left, right in zip(sources, sources[1:]):
        if (left.next_chunk_id != right.id or right.pre_chunk_id != left.id or
                left.chunk_index + 1 != right.chunk_index or
                right.start_at < left.start_at or right.start_at > left.end_at or
                right.end_at <= left.end_at):
            raise RetrievalScopeError()
        overlap = left.end_at - right.start_at
        if overlap and (overlap > len(left.content) or overlap > len(right.content) or
                        left.content[-overlap:] != right.content[:overlap]):
            raise ValueError('BRIDGE_IDENTITY_OVERLAP_MISMATCH')
        overlaps.append(overlap)
    return overlaps


def _sentences(sources, overlaps):
    # The bridge owns the one shared splitter; defer the import to avoid a
    # module cycle when bridge_rescue calls this helper.
    from .bridge_rescue import sentence_spans

    sentences = []
    for row, overlap in zip(sources, overlaps):
        for start, end in sentence_spans(row.content):
            if end <= overlap:
                continue
            if start < overlap:
                # An unsupported cross-chunk sentence must not silently lose
                # its beginning or acquire a fabricated single-source quote.
                raise ValueError('BRIDGE_IDENTITY_SENTENCE_CROSSES_OVERLAP')
            sentences.append(IdentityQuote(row.id, start, end, row.content[start:end]))
    return sentences


def _full_subject(quote):
    match = _FULL_SUBJECT.fullmatch(quote)
    if match and (_HUMAN_ROLE.match(match['tail']) or _PERSON_ACTION.match(match['tail'])):
        return match['name']
    return None


def _has_foreign_person(text, name):
    if _RELATIVE_OR_SPEECH.search(text) or _PERSON_ROLE.search(text):
        return True
    # Quoted publication titles are admissible only in the supported
    # co-editor construction. Quoted discourse is never an identity proof.
    if re.search(r'\bco-editor\s+of\s+["“]', text, re.I):
        text = re.sub(r'["“][^"”\n]+["”]', '', text)
    elif re.search(r'["“”]', text):
        return True
    text = re.sub(r'(?<!\w)' + re.escape(name) + r'(?!\w)', '', text)
    surname = name.rsplit(' ', 1)[-1]
    text = re.sub(r'(?<!\w)' + re.escape(surname) + r'(?!\w)', '', text)
    text = re.sub(r'\b(?:[A-Z]\.){2,}', '', text)
    text = _ORGANIZATION.sub('', text)
    text = re.sub(r'\b[A-Z][a-z]+\s+area\b', '', text)
    text = re.sub(r'\bin\s+Writing\b', '', text)
    allowed = {'She', 'He', 'Her', 'His', 'Director', 'Head', 'Chair', 'President', 'Dean'}
    return any(word not in allowed for word in re.findall(r'\b[A-Z][A-Za-z]*\b', text))


def _reset(quote, name):
    for subject in (name, name.rsplit(' ', 1)[-1]):
        match = re.match(re.escape(subject) + r'\s+(.+)', quote, re.S)
        if match and _DIRECT.match(match[1]):
            return match[1]
    return None


def _safe_after_reset(quote, name, pronoun):
    if _has_foreign_person(quote, name):
        return False
    body = _reset(quote, name)
    if body is not None:
        return bool(_HUMAN_ROLE.match(body) or _CONTINUATION.match(body))
    match = re.match(r'(She|He)\s+(.+)', quote, re.S)
    return bool(match and match[1] == pronoun and _CONTINUATION.match(match[2]))


def _present_in_normal(anchor, original, normal_results):
    for row in normal_results:
        if (row is not None and row.chunk_type == 'text' and row.is_enabled and
                anchor.source_chunk_id in (row.id, *row.sub_chunk_ids) and
                _domain(row) == _domain(original) and
                row.chunk_metadata.get('source_version') == original.metadata.get('source_version') and
                all((key in row.metadata, row.metadata.get(key)) ==
                    (key in original.metadata, original.metadata.get(key))
                    for key in ('tenant_id', 'owner_id')) and
                anchor.quote in row.content):
            return True
    return False


def prove_identity(question, sources, *, selected_chunk_id, quote_start, quote_end,
                   normal_results=()):
    """Return source identity proof separately from final-context support.

    ``sources`` is oldest-to-selected and includes at most three actual
    predecessors. The selected sentence uses program-computed, original
    per-Chunk Unicode offsets. No source or normal result is modified.
    """
    if not isinstance(question, str) or not question:
        raise ValueError('BRIDGE_IDENTITY_QUESTION_INVALID')
    sources = tuple(sources)
    normal_results = tuple(normal_results)
    overlaps = _validate_chain(sources, selected_chunk_id)
    selected = sources[-1]
    if (type(quote_start) is not int or type(quote_end) is not int or
            not 0 <= quote_start < quote_end <= len(selected.content)):
        raise ValueError('BRIDGE_IDENTITY_SPAN_INVALID')
    sentences = _sentences(sources, overlaps)
    selected_quote = next((quote for quote in sentences if
        quote.source_chunk_id == selected_chunk_id and
        (quote.quote_start, quote.quote_end) == (quote_start, quote_end)), None)
    if selected_quote is None:
        raise ValueError('BRIDGE_IDENTITY_SPAN_INVALID')
    sentences = sentences[:sentences.index(selected_quote) + 1]
    targets = [(index, name) for index, quote in enumerate(sentences)
               if (name := _full_subject(quote.quote)) and _whole(question, name)]
    names = {name for _, name in targets}
    if len(names) != 1:
        return _unverified('NAMED_SOURCE_ANCHOR_MISSING_OR_AMBIGUOUS')
    name = next(iter(names))
    first_anchor = targets[0][0]
    surname = name.rsplit(' ', 1)[-1]
    same_surname = re.compile(rf'\b{_NAME}(?:[ \t]+{_NAME}){{0,2}}[ \t]+{re.escape(surname)}\b')
    if any(match[0] != name for quote in sentences
           for match in same_surname.finditer(quote.quote)):
        return _unverified('SURNAME_AMBIGUOUS')
    resets = [index for index in range(first_anchor, len(sentences))
              if _reset(sentences[index].quote, name) is not None]
    if not resets:
        return _unverified('SOURCE_SUBJECT_REESTABLISHMENT_MISSING')
    reset = resets[-1]
    target_direct = _reset(selected_quote.quote, name) is not None
    pronoun_match = re.match(r'(She|He)\s+', selected_quote.quote)
    if not target_direct and pronoun_match is None:
        return _unverified('SELECTED_SUBJECT_UNSUPPORTED')
    pronoun = pronoun_match[1] if pronoun_match else None
    if not target_direct and not all(_safe_after_reset(quote.quote, name, pronoun)
                                     for quote in sentences[reset:]):
        return _unverified('SOURCE_DISCOURSE_CONTINUITY_UNVERIFIED')
    anchors = tuple(dict.fromkeys((sentences[first_anchor], sentences[reset])))
    originals = {row.id: row for row in sources}
    required = tuple(dict.fromkeys((*anchors, selected_quote)))
    supported = all(_present_in_normal(quote, originals[quote.source_chunk_id], normal_results)
                    for quote in required)
    return IdentityProof(True, name, anchors, supported, 'BOUNDED_SOURCE_IDENTITY_VERIFIED')


async def admit_identity_context(state, context, repository, data, chain, proof):
    """Admit only missing proven original anchors after an accepted location hop.

    The historical extraction proof is not changed. This is a separate current
    source projection with both existing Context encoders' budgets enforced.
    """
    if not proof.verified or proof.final_context_identity_supported:
        return
    from copy import copy, deepcopy
    from .bridge_rescue import _check, _read_originals, _trace
    from .context import into_chat_message
    from .types import ContextBudgetError
    from .types import context_results
    from ..generation.context import Evidence, prepare_generation
    from ..generation.types import GenerationRequest

    _check(context)
    current = await _read_originals(chain, state, context, repository)
    _check(context)
    fresh = prove_identity(state.rewrite_query or state.query, current,
        selected_chunk_id=data['source_chunk_id'], quote_start=data['quote_start'],
        quote_end=data['quote_end'], normal_results=state.merge_results)
    if fresh != proof:
        raise RetrievalScopeError()
    originals = {row.id:row for row in current}
    missing = [anchor for anchor in fresh.anchor_quotes
               if not _present_in_normal(anchor, originals[anchor.source_chunk_id], state.merge_results)]
    additions = []
    for anchor in missing:
        row = originals[anchor.source_chunk_id]
        if any(existing.id == row.id for existing in context_results(state)):
            raise RetrievalScopeError()
        if row.id not in {item.id for item in additions}:
            raw = deepcopy(row)
            raw.knowledge_description = raw.knowledge_custom_metadata = raw.context_header = ''
            raw.metadata['bridge_identity'] = {'first_hop_chunk_id':data['source_chunk_id'],
                'source_chunk_id':row.id, 'quote_start':anchor.quote_start,
                'quote_end':anchor.quote_end, 'quote':anchor.quote}
            additions.append(raw)
    admitted = prove_identity(state.rewrite_query or state.query, current,
        selected_chunk_id=data['source_chunk_id'], quote_start=data['quote_start'],
        quote_end=data['quote_end'], normal_results=[*state.merge_results,*additions])
    if not additions or not admitted.final_context_identity_supported:
        _trace(state,'IDENTITY_CONTEXT_SKIPPED',reason='IDENTITY_ANCHOR_NOT_ADMISSIBLE',
               final_context_identity_supported=False)
        return
    trial = copy(state)
    trial.bridge_identity_evidence = additions
    try:
        finalized = await into_chat_message(trial,context,record_bridge=False)
        # The final encoder adds handles and source wrappers. It is the same
        # encoder used before real generation, not a raw text length estimate.
        prepare_generation(GenerationRequest(context.request_id,context.session_id or 'identity-context-preview',
            'identity-context-preview',state.query,finalized,
            evidence=tuple(Evidence.from_result(row) for row in context_results(trial)),
            language=state.language,citations_enabled=True))
    except ContextBudgetError:
        _trace(state,'IDENTITY_CONTEXT_SKIPPED',reason='IDENTITY_CONTEXT_BUDGET_EXCEEDED',
               source_ids=[row.id for row in additions],final_context_identity_supported=False)
        return
    _check(context)
    state.bridge_identity_evidence = additions
    for row in state.rescue_evidence:
        details = row.metadata.get('bridge_rescue',{})
        if details.get('first_hop_chunk_id') == data['source_chunk_id']:
            details['identity_proof'] = asdict(admitted)
            details['identity_context_admission'] = 'CURRENT_ORIGINAL_ANCHOR_ADMITTED'
    _trace(state,'IDENTITY_CONTEXT_ADMITTED',source_ids=[row.id for row in additions],
        initial_final_context_identity_supported=proof.final_context_identity_supported,
        final_context_identity_supported=True,identity_proof=asdict(admitted))
