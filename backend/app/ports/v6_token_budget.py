"""Canonical token-unit contracts; no second reservation or monetary permit."""
from dataclasses import dataclass, field, asdict
from pathlib import Path
import hashlib
import json


@dataclass(frozen=True)
class V6CanonicalTokenBudgetBinding:
    activity_gate: object = field(repr=False)
    grant: object
    authority: object = field(repr=False)
    execution_lease: object
    ledger_identity: str
    server_scope: object

    def validate(self, gateway):
        from backend.app.application.validation_usd_budget import (
            V6OwnerActivityGate, OwnerActivityTokenGrant, V6ExecutionLease, OWNER_V6_TOKEN_GRANT_R3,
        )
        from backend.app.ports.deepseek_gate_types import V6LedgerAuthority
        from backend.app.ports.providers import ProviderRequestNotSent
        from backend.app.domain.scope import Scope
        gate = self.activity_gate
        if (type(self) is not V6CanonicalTokenBudgetBinding or type(gate) is not V6OwnerActivityGate
                or type(self.grant) is not OwnerActivityTokenGrant or self.grant is not gate.grant
                or self.grant.version != 3 or self.grant.provider != 'deepseek'
                or self.grant.model != 'deepseek-flash' or self.grant.output_token_cap != 512
                or getattr(gateway, 'attempt_gate', None) is not gate
                or getattr(gateway, 'provider_name', None) != self.grant.provider
                or getattr(gateway, 'chat_model', None) != self.grant.model
                or self.authority is not gate.authority
                or type(self.execution_lease) is not V6ExecutionLease
                or self.execution_lease is not gate.execution_lease
                or type(self.ledger_identity) is not str
                or self.ledger_identity != str(gate.path.resolve())
                or type(self.server_scope) is not Scope
                or type(self.server_scope.knowledge_base_ids) is not frozenset
                or type(self.server_scope.document_ids) is not frozenset
                or not self.server_scope.knowledge_base_ids
                or any(type(x) is not str or not x for x in
                       self.server_scope.knowledge_base_ids | self.server_scope.document_ids)
                or self.grant.execution_kind != getattr(gateway, 'execution_kind', None)):
            raise ProviderRequestNotSent('V6_CANONICAL_TOKEN_BINDING_INVALID')
        self.execution_lease.validate()
        if self.grant.execution_kind == 'REAL':
            if (self.grant is not OWNER_V6_TOKEN_GRANT_R3 or type(self.authority) is not V6LedgerAuthority
                    or Path(self.authority.ledger_path).resolve() != gate.path.resolve()):
                raise ProviderRequestNotSent('V6_CANONICAL_TOKEN_AUTHORITY_REQUIRED')
        elif self.grant.execution_kind != 'SIMULATED':
            raise ProviderRequestNotSent('V6_CANONICAL_TOKEN_BINDING_INVALID')


@dataclass(frozen=True)
class V6TokenBudgetRequest:
    binding: V6CanonicalTokenBudgetBinding = field(repr=False)
    usage_guard: object = field(repr=False)
    prepared_request: object = field(repr=False)
    request_id: str
    run_id: str
    owner: str
    session_id: str
    product_attempt_id: str
    envelope_sha256: str
    server_scope: object


@dataclass(frozen=True)
class V6TokenBudgetReceipt:
    """Reference to one canonical attempt, suitable for PG diagnostics, not money FK."""
    request_id: str
    native_token_attempt_id: str
    product_attempt_id: str
    run_id: str
    owner: str
    session_id: str
    knowledge_base_ids: tuple[str, ...]
    document_ids: tuple[str, ...]
    envelope_sha256: str
    grant_id: str
    activity_id: str
    authority_id: str | None
    authority_epoch: int | None
    lease_id: str
    ledger_identity_sha256: str
    wire_request_sha256: str
    reserved_input_tokens: int
    reserved_output_tokens: int
    status: str
    token_state: str

    def diagnostics(self):
        data = {'schema': 'v6-canonical-token-budget-receipt/v1', **asdict(self)}
        data['receipt_identity_sha256'] = hashlib.sha256(json.dumps(data, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        return data
