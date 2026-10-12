"""Explicit ordinary-service authority, separate from frozen evaluation grants.

Quotes reserve cost; they never prove an invoice. The persistent ledger binds
the complete policy fingerprint so restarting cannot reset or enlarge a cap.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import re

from backend.app.domain.scope import Scope


@dataclass(frozen=True)
class ServiceModelPolicy:
    policy_id: str
    authorization_reference: str
    provider: str
    model: str
    roles: tuple[str, ...]
    token_limit: int | None
    input_microunits_per_million: int
    output_microunits_per_million: int
    zero_quote: bool = False

    def __post_init__(self):
        if (not isinstance(self.policy_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', self.policy_id)
                or not isinstance(self.authorization_reference, str) or not self.authorization_reference.strip()
                or type(self.roles) is not tuple or not self.roles or len(set(self.roles)) != len(self.roles)
                or not set(self.roles) <= {'embedding', 'chat_cheap', 'chat_expensive', 'vision', 'rerank'}
                or not self.provider or not self.model or type(self.zero_quote) is not bool
                or self.token_limit is not None and (type(self.token_limit) is not int or self.token_limit <= 0)
                or any(type(n) is not int or n < 0 for n in
                       (self.input_microunits_per_million, self.output_microunits_per_million))):
            raise ValueError('SERVICE_POLICY_INVALID')
        if self.zero_quote != (self.input_microunits_per_million == self.output_microunits_per_million == 0):
            raise ValueError('SERVICE_PRICE_QUOTE_REQUIRED')

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    def check_model(self, spec):
        if (spec.provider, spec.model_id) != (self.provider, self.model) or spec.role not in self.roles:
            raise ValueError('SERVICE_POLICY_MODEL_MISMATCH')

    def cost_bound(self, input_tokens, output_tokens):
        validate_tokens(input_tokens, output_tokens)
        amount = (input_tokens * self.input_microunits_per_million
                  + output_tokens * self.output_microunits_per_million)
        return (amount + 999_999) // 1_000_000


def validate_tokens(input_tokens, output_tokens):
    if (type(input_tokens) is not int or input_tokens <= 0
            or type(output_tokens) is not int or output_tokens < 0):
        raise ValueError('SERVICE_TOKEN_RESERVATION_INVALID')


def scope_fingerprint(scope):
    if (type(scope) is not Scope or not scope.knowledge_base_ids
            or any(type(x) is not str or not x for x in scope.knowledge_base_ids | scope.document_ids)):
        raise ValueError('SERVICE_SERVER_SCOPE_REQUIRED')
    value = [sorted(scope.knowledge_base_ids), sorted(scope.document_ids)]
    return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class ServiceZeroReservation:
    """Owner's explicit model quote, bound to a scoped prepared request.

    Only normal service uses this authority; it cannot extend a V6 permit.
    """
    policy: ServiceModelPolicy
    model_key: str
    role: str
    request_id: str
    scope_sha256: str
    wire_sha256: str
    planned_tokens: int
    run_id: str | None

    def validate(self, *, run_id, provider, model_name, capability, planned_tokens=None):
        if (type(self.policy) is not ServiceModelPolicy or not self.policy.zero_quote
                or (provider, model_name) != (self.policy.provider, self.policy.model)
                or capability != self.role or capability not in self.policy.roles
                or run_id != self.run_id or not self.model_key or not self.request_id
                or any(not re.fullmatch(r'[0-9a-f]{64}', x) for x in (self.scope_sha256, self.wire_sha256))
                or type(self.planned_tokens) is not int or self.planned_tokens <= 0
                or planned_tokens is not None and planned_tokens != self.planned_tokens):
            raise ValueError('SERVICE_ZERO_QUOTE_BINDING_INVALID')


@dataclass(frozen=True)
class ServiceModelBinding:
    """Composition-owned service capability; no evaluation grant or deadline."""
    policy: ServiceModelPolicy
    capacity: object
    usage_guard: object

    def validate(self, spec):
        if type(self.policy) is not ServiceModelPolicy:
            raise ValueError('SERVICE_POLICY_REQUIRED')
        self.policy.check_model(spec)
        if (getattr(self.usage_guard, 'policy', None) is not self.policy
                or getattr(self.usage_guard, 'spec', None) != spec
                or (getattr(self.capacity, 'provider', None), getattr(self.capacity, 'model', None))
                   != (spec.provider, spec.model_id)
                or not callable(getattr(self.capacity, 'measure', None))
                or not callable(getattr(self.usage_guard, 'reserve_prepared', None))
                or not callable(getattr(self.usage_guard, 'check_before_send', None))):
            raise ValueError('SERVICE_MODEL_BINDING_INVALID')
