"""Immutable input evidence, distinct from SendAuthorization and actual usage."""
from dataclasses import dataclass,asdict
import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit
from .models.types import ProviderError
from .models.output_contract import resolve_output_contract


def sha(value):
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class OperationSlot:
    operation_id: str
    stage: str
    batch: int = 0
    allowed_attempt: int = 0

    def identity(self):
        if (not self.operation_id or not self.stage or type(self.batch) is not int or self.batch < 0
                or type(self.allowed_attempt) is not int or self.allowed_attempt < 0):
            raise ProviderError('MODEL_OPERATION_SLOT_REQUIRED')
        return sha(json.dumps([self.operation_id, self.stage, self.batch, self.allowed_attempt],
            separators=(',', ':')).encode())


def operation_slot(request_id, capability):
    batch = re.fullmatch(r'(.+)/(embedding|rerank)-(\d+)', request_id)
    if batch and batch[2]==capability:
        return OperationSlot(batch[1],capability,int(batch[3]))
    answer = re.fullmatch(r'(.+)/answer/([12])/(baseline|cheap|expensive)(/.*)?', request_id)
    if answer:
        return OperationSlot(answer[1], 'answer'+(answer[4] or ''), 0, int(answer[2]))
    return OperationSlot(request_id, capability)


def historical_ids(request_id):
    answer = re.fullmatch(r'(.+)/answer/([12])/(baseline|cheap|expensive)(/.*)?', request_id)
    if answer:return [f'{answer[1]}/answer/{answer[2]}/{r}'+(answer[4] or '') for r in ('baseline','cheap','expensive')]
    batch = re.fullmatch(r'(.+)/(embedding|rerank)-0', request_id)
    if batch:return [request_id,batch[1]]
    return [request_id,request_id+'/embedding-0',request_id+'/rerank-0']


@dataclass(frozen=True)
class SourceSlice:
    kind: str
    original_identity: str
    original_sha256: str
    sent_value_sha256: str
    origin_kbs: tuple[str, ...]
    transform_recipe: str
    wire_locator: str


@dataclass(frozen=True)
class SourceFacts:
    slot: OperationSlot
    slices: tuple[SourceSlice, ...]
    wire_sha256: str


@dataclass(frozen=True)
class TokenReservationBound:
    input_upper: int
    output_upper: int
    source: str


@dataclass(frozen=True)
class BoundCallFacts:
    sources: SourceFacts
    bound: TokenReservationBound
    config_sha: str
    model_fingerprint: str
    simulated: bool
    # Immutable, recomputable output-budget contract for approved calls only.
    # Synthetic fixtures and legacy direct constructions leave it None.
    output_contract: Any = None


def bind_synthetic(config, context, body, wire, config_sha, *, capability):
    """Closed mock recipe: input wire bytes+64, mock output capped by request.

    This recipe makes no claim about hosted tokenization. It is callable only
    by the synthetic transport wired by authority.py, never approved mode.
    """
    slot = operation_slot(context.request_id, capability)
    scope = tuple(sorted(set(context.scope.knowledge_base_ids + context.required_origin_kbs)))
    source = SourceSlice('question', 'SYNTHETIC:' + slot.identity(), sha(wire), sha(wire), scope,
        'closed-fixture-whole-wire/v1', '/')
    facts = SourceFacts(slot, (source,), sha(wire))
    output = body.get('max_tokens', body.get('max_completion_tokens', 0))
    if type(output) is not int or output < 0:
        raise ProviderError('MODEL_OUTPUT_HARD_LIMIT_DENIED')
    fingerprint = sha(json.dumps(asdict(config),sort_keys=True,separators=(',', ':'),
        allow_nan=False).encode())
    return BoundCallFacts(facts, TokenReservationBound(len(wire) + 64, output,
        'SYNTHETIC_ONLY/closed-mock-bounded-response/v1'), config_sha, fingerprint, True)


# Approved-mode meter recipes. A recipe is a named, code-owned conservative
# bound; it is never a hosted actual count and never a price/money estimate.
# v1 keeps its historical name and, for a contract-compliant request, its
# historical reserved numbers (= the provider-declared completion cap); it now
# additionally fails closed on a request whose wire field is not the provider's
# declared cap field. v2 records the full recomputable output-budget contract.
# Historical ledger rows keep their original recipe and reserved values.
DEEPSEEK_INLINE_IMAGE_RECIPE = 'deepseek-inline-image-1024/v1'
METER_RECIPES = frozenset({'wire-bytes-upper-bound/v1', 'provider-completion-cap/v2',
                          DEEPSEEK_INLINE_IMAGE_RECIPE})


def measure_final_input(recipe, wire):
    """Conservative input upper bound for one final wire payload.

    `wire-bytes-upper-bound/v1` returns the serialized wire byte length. This is
    a strict upper bound for byte-level BPE tokenizers (every token spans at least
    one byte), so it never under-reserves; it is deliberately loose and must not
    be reported as a provider actual token count. Unknown recipes are refused.
    """
    if recipe not in METER_RECIPES:
        raise ProviderError('TOKEN_BOUND_RECIPE_UNSUPPORTED')
    if recipe == DEEPSEEK_INLINE_IMAGE_RECIPE:
        # Media requires the provider/model/body checks below, not wire length alone.
        raise ProviderError('MODEL_INPUT_BOUND_NOT_APPLICABLE')
    if not isinstance(wire, (bytes, bytearray)) or not wire:
        raise ProviderError('MODEL_WIRE_REQUIRED')
    return len(wire)


def _unbounded_input_present(body):
    """Sent message parts whose provider-side token count the wire-bytes input
    recipe cannot bound.

    The recipe's strict upper bound holds only while every input token consumes
    at least one wire byte. A hosted image/video/audio part is tokenized by the
    vendor from pixels/frames bytes the wire never carries (it carries a URL or a
    base64 string), so the bound is not proven for it. Text-only wires are fine.
    """
    if not isinstance(body, dict):
        return False
    for message in list(body.get('messages') or []) + list(body.get('contents') or []):
        if not isinstance(message, dict):
            continue
        content = message.get('content')
        parts = content if isinstance(content, list) else (message.get('parts') or [])
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            if (part.get('type') in ('image_url', 'input_image', 'video_url', 'input_video', 'audio_url')
                    or any(key in part for key in ('image_url', 'inlineData', 'inline_data', 'fileData', 'file_data'))):
                return True
    return False


def _deepseek_inline_image_input(config, body, wire, capability):
    """Official Flash inline JPEG/PNG bound: wire bytes + 1024 per image.

    DeepSeek's Vision guide documents at most 1024 input tokens per image:
    https://api-docs.deepseek.com/guides/vision/#token-usage (checked 2026-10-17).
    Keeping ALL wire bytes, including base64, deliberately over-reserves the
    text/framing contribution. This is not provider actual usage. Remote images,
    files, animated media and other provider/model/protocol identities refuse.
    """
    import base64
    import io
    from PIL import Image

    def deny():
        raise ProviderError('MODEL_INPUT_BOUND_NOT_APPLICABLE')

    endpoint = urlsplit(config.endpoint_url or config.base_url)
    if (config.provider != 'deepseek' or config.model != 'deepseek-flash'
            or config.api != 'openai-completions' or not config.supports_vision
            or capability != 'vision' or endpoint.scheme != 'https'
            or endpoint.netloc not in ('api.deepseek.com', 'api.deepseek.com:443')
            or body.get('model') != config.model):
        deny()
    try:
        if json.loads(wire) != body:
            deny()
    except (ValueError, TypeError):
        deny()
    messages = body.get('messages')
    if not isinstance(messages, list) or not messages or body.get('contents') or body.get('input'):
        deny()
    images = 0
    for message in messages:
        if not isinstance(message, dict):
            deny()
        parts = message.get('content')
        if not isinstance(parts, list):
            if parts is not None and not isinstance(parts, str):
                deny()
            continue
        for part in parts:
            if (isinstance(part, dict) and part.get('type') == 'text'
                    and set(part) <= {'type', 'text'} and isinstance(part.get('text'), str)):
                continue
            if (not isinstance(part, dict) or set(part) != {'type', 'image_url'}
                    or part.get('type') != 'image_url' or message.get('role') != 'user'):
                deny()
            image = part['image_url']
            if (not isinstance(image, dict) or set(image) - {'url', 'detail'}
                    or image.get('detail', 'auto') not in ('low', 'high', 'original', 'auto')):
                deny()
            match = re.fullmatch(r'data:image/(jpeg|png);base64,([A-Za-z0-9+/=]+)', str(image.get('url', '')))
            if match is None or len(match[2]) > 4 * ((32 * 1024 * 1024 + 2) // 3):
                deny()
            try:
                raw = base64.b64decode(match[2], validate=True)
                if not raw or len(raw) > 32 * 1024 * 1024:
                    deny()
                with Image.open(io.BytesIO(raw)) as opened:
                    if (opened.format != {'jpeg': 'JPEG', 'png': 'PNG'}[match[1]]
                            or getattr(opened, 'n_frames', 1) != 1
                            or max(opened.size) > 8192):
                        deny()
                    opened.verify()
            except (ValueError, OSError, SyntaxError, Image.DecompressionBombError):
                deny()
            images += 1
    if not 1 <= images <= 600:
        deny()
    return len(wire) + images * 1024


def bind_wire_facts(config, context, body, wire, capability="chat", *, config_sha, recipe):
    """Approved-mode facts from real per-source evidence; never a synthetic slice.

    Requires the server-resolved `CallContext.source_facts` to already hold real
    SourceFacts for this call (question/history/evidence/memory/document/image/
    system-template). Missing or mismatched evidence refuses the send; a whole-wire
    synthetic slice is not accepted here.

    The output reservation upper bound is recomputed from the same immutable
    output-budget contract (`resolve_output_contract`) instead of trusting the
    request cap blindly: a wire whose generation-cap field is not the provider's
    declared cap field (or whose combination has no verifiable hard upper bound)
    fails closed. For a contract-compliant request v1 keeps its historical
    reserved value (= the declared cap).
    """
    sources = getattr(context, 'source_facts', None)
    if not isinstance(sources, SourceFacts):
        raise ProviderError('SOURCE_FACTS_REQUIRED')
    if sources.wire_sha256 != sha(wire):
        raise ProviderError('WIRE_FACTS_MISMATCH')
    if recipe not in METER_RECIPES:
        raise ProviderError('TOKEN_BOUND_RECIPE_UNSUPPORTED')
    if recipe == DEEPSEEK_INLINE_IMAGE_RECIPE:
        input_upper = _deepseek_inline_image_input(config, body, wire, capability)
    elif _unbounded_input_present(body):
        # Fail closed rather than add an unproven constant for hosted media input.
        raise ProviderError('MODEL_INPUT_BOUND_NOT_APPLICABLE')
    else:
        input_upper = measure_final_input(recipe, wire)
    contract = resolve_output_contract(config, body, capability=capability)
    fingerprint = sha(json.dumps(asdict(config), sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode())
    return BoundCallFacts(sources, TokenReservationBound(input_upper,
        contract.output_upper, recipe), config_sha, fingerprint, False, contract)


def build_call_sources(context, body, wire, *, capability):
    """Per-call SourceFacts from the real serialized wire and the server-resolved scope.

    One slice per sent element (chat message, embedding input, rerank document). `kind`
    comes from the element role/shape; origins are the server-resolved KB set
    (`context.scope` union `context.required_origin_kbs`). This binds real sent-value
    hashes and real origins; it is a conservative per-element binding, not a
    per-sentence provenance proof, and it refuses when there is nothing to bind.
    """
    scope = tuple(sorted(set(context.scope.knowledge_base_ids + context.required_origin_kbs)))
    slices = []
    if capability in ('chat', 'vision'):
        for index, message in enumerate(body.get('messages') or []):
            role = str((message or {}).get('role', '') or '')
            kind = {'system': 'system', 'assistant': 'history'}.get(role, 'question' if index <= 1 else 'history')
            sent = sha(json.dumps(message, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode())
            slices.append(SourceSlice(kind, f'WIRE:message:{index}', sent, sent, scope,
                'wire-element/v1', f'/messages/{index}'))
    elif capability == 'embedding':
        for index, text in enumerate(body.get('input') or []):
            sent = sha(str(text).encode())
            slices.append(SourceSlice('document', f'WIRE:embedding:{index}', sent, sent, scope,
                'wire-element/v1', f'/input/{index}'))
    elif capability == 'rerank':
        # The query is a first-class sent element with its own origin; without it
        # only the documents would be provenance-bound while the query rides the
        # wire unrecorded. Non-string protocols keep the document slices only.
        query = body.get('query')
        if isinstance(query, str) and query:
            slices.append(SourceSlice('question', 'WIRE:query', sha(query.encode()), sha(query.encode()),
                scope, 'wire-element/v1', '/query'))
        for index, text in enumerate(body.get('documents') or []):
            sent = sha(str(text).encode())
            slices.append(SourceSlice('evidence', f'WIRE:document:{index}', sent, sent, scope,
                'wire-element/v1', f'/documents/{index}'))
    if not slices:
        raise ProviderError('SOURCE_FACTS_REQUIRED')
    return SourceFacts(operation_slot(context.request_id, capability), tuple(slices), sha(wire))
