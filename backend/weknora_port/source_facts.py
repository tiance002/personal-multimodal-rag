"""Immutable input evidence, distinct from SendAuthorization and actual usage."""
from dataclasses import dataclass,asdict
import hashlib
import json
import re
from .models.types import ProviderError


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
