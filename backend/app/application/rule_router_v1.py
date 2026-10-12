"""RuleRouter V1 contracts. No retrieval, secret loading or network transports."""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
import hashlib
import json
import re
from backend.app.application.router_feature_extraction import Features, extract_features
from backend.app.ports.providers import ProviderRequestNotSent, ProviderUnavailable, TruncatedAnswer
from backend.app.ports.model_access import model_access
from backend.app.ports.model_usage import call_stage
from backend.app.domain.scope import Scope
from backend.app.ports.service_policy import ServiceModelBinding
from backend.app.application.validation_usd_budget import (
    OWNER_V6_TOKEN_GRANT, OWNER_V6_TOKEN_GRANT_R2, OWNER_V6_TOKEN_GRANT_R3, OwnerActivityTokenGrant, V6OwnerActivityGate,
)

VERSION = "rule-router-v1/draft-v0.4"
MODELS = {"chat_cheap": ("siliconflow", "XingChenAGI/Xing4.0-29B"),
          "chat_expensive": ("deepseek", "deepseek-flash")}
QUALITY_ERRORS = frozenset({"INVALID_CITATION", "UNSUPPORTED_ANSWER", "MODEL_EMPTY", "SELF_CONTRADICTION"})


class GenerationSettlementFailure(ProviderUnavailable):
    """Fixed local diagnostics, preserving the first outcome without raw errors."""
    def __init__(self, *, not_sent: bool, primary_outcome: str):
        super().__init__("MODEL_USAGE_SETTLEMENT_FAILED")
        self.not_sent = not_sent
        self.primary_outcome = primary_outcome


@dataclass(frozen=True)
class RouterPolicy:
    mode: str = "OFF"
    threshold: Decimal = Decimal("0.35")
    quality_escalation: bool = True
    abstention_escalation: bool = False
    provider_fallback: bool = False
    truncated_escalation: bool = False

    def __post_init__(self):
        if self.mode not in {"OFF", "DYNAMIC", "CHEAP_ONLY", "EXPENSIVE_ONLY"}:
            raise ValueError("ROUTER_MODE_INVALID")
        if not isinstance(self.threshold, Decimal) or not self.threshold.is_finite() or not 0 <= self.threshold <= 1:
            raise ValueError("ROUTER_THRESHOLD_INVALID")
        for name in ("quality_escalation", "abstention_escalation", "provider_fallback", "truncated_escalation"):
            if type(getattr(self, name)) is not bool:
                raise ValueError("ROUTER_POLICY_INVALID")


@dataclass(frozen=True)
class Decision:
    features: Features
    score: Decimal
    role: str
    reason: str

    def public(self, policy: RouterPolicy) -> dict:
        return {"router_version": VERSION, "threshold": str(policy.threshold), "mode": policy.mode,
                **self.features.public(), "score": str(self.score), "decision_model_role": self.role,
                "route_reason": self.reason}


def decide(question: str, prompt: str, policy: RouterPolicy, *, estimated_tokens=None, estimator="UNKNOWN", understanding=None) -> Decision:
    if policy.mode == "OFF":
        raise ValueError("ROUTER_OFF")
    features = extract_features(question, prompt, estimated_tokens=estimated_tokens, estimator=estimator)
    if understanding is not None:
        from backend.app.ports.query_understand import QueryUnderstanding
        if (not isinstance(understanding,QueryUnderstanding) or understanding.needs_clarification
            or understanding.task_status!='COMPLETE' or understanding.rewrite_query!=question):
            raise ValueError('ROUTER_QUERY_UNDERSTAND_INVALID')
        original = extract_features(understanding.q0,prompt,estimated_tokens=estimated_tokens,estimator=estimator)
        # Existing scores stay untouched. Max, never sum: conditions/tasks in
        # q0 and rewritten text describe the same work, not two sets of work.
        use_original=[a>b for a,b in zip(original.contributions,features.contributions)]
        features=replace(features,contributions=tuple(max(a,b) for a,b in zip(original.contributions,features.contributions)),
            statuses=tuple(a if choose else b for a,b,choose in zip(original.statuses,features.statuses,use_original)),
            anchors=tuple(dict.fromkeys((*original.anchors,*features.anchors))),
            task_labels=tuple(dict.fromkeys((*original.task_labels,*features.task_labels))),
            condition_count=original.condition_count if use_original[1] else features.condition_count,
            logic=original.logic if use_original[2] else features.logic,
            dependency_depth=original.dependency_depth if use_original[3] else features.dependency_depth,
            output_count=original.output_count if use_original[5] else features.output_count)
    score = Decimal(sum(features.contributions)) / 100
    role = ("chat_cheap" if policy.mode == "CHEAP_ONLY" else "chat_expensive" if policy.mode == "EXPENSIVE_ONLY"
            else "chat_expensive" if score >= policy.threshold else "chat_cheap")
    return Decision(features, score, role, "OFFLINE_ARM" if policy.mode.endswith("ONLY") else "SCORE_AT_OR_ABOVE_THRESHOLD" if role == "chat_expensive" else "SCORE_BELOW_THRESHOLD")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf8")).hexdigest()


@dataclass(frozen=True)
class GenerationEnvelope:
    q0: str
    resolved_query: str
    prompt: str
    context_hash: str
    labels: tuple[str, ...]
    snapshot_identities: tuple[tuple[str, str, str, str], ...]
    max_tokens: int
    timeout_seconds: float
    template_version: str = "quick-evidence-prompt/v1"
    estimated_input_tokens: int | None = None
    attempt_id: str | None = None

    @property
    def identity(self) -> str:
        data = {"q0_hash": digest(self.q0), "resolved_hash": digest(self.resolved_query),
            "prompt_hash": digest(self.prompt), "context_hash": self.context_hash, "labels": self.labels,
            "snapshot_identities": self.snapshot_identities, "max_tokens": self.max_tokens,
            "timeout_seconds": self.timeout_seconds, "template_version": self.template_version,
            "estimated_input_tokens": self.estimated_input_tokens}
        if self.attempt_id is not None:
            data["attempt_id"] = self.attempt_id
        return digest(json.dumps(data, sort_keys=True))


@dataclass(frozen=True)
class V6GenerationBinding:
    """Explicit owner evaluation injection; not populated from settings/bools."""
    grant: OwnerActivityTokenGrant
    activity_gate: V6OwnerActivityGate
    capacity_counter: object
    server_scope: Scope | None = None
    send_claim_callback: object | None = None
    token_budget_binding: object | None = None


@dataclass(frozen=True)
class V6XingGenerationBinding:
    """Independent SF scoped injection; local capacity is not hosted proof."""
    grant: object
    capacity_counter: object
    authorized_transport: object
    request_window: object | None = None


@dataclass(frozen=True)
class GenerationRole:
    """Trusted binding; real execution requires the exact V6 evaluation grant."""
    role: str
    gateway: object
    simulated: bool = False
    usage_guard: object | None = None
    simulated_input_limit: int | None = None
    v6_binding: V6GenerationBinding | None = None
    xing_binding: V6XingGenerationBinding | None = None
    service_binding: ServiceModelBinding | None = None

    @property
    def provider(self) -> str | None:
        return getattr(self.gateway, "provider_name", None)

    @property
    def model(self) -> str | None:
        spec = getattr(self.gateway, "spec", None)
        return spec.model_id if spec is not None else getattr(self.gateway, "chat_model", None)

    @property
    def execution_kind(self) -> str:
        """Observed gateway/typed transport identity, never a policy/bool label."""
        observed = getattr(self.gateway, 'execution_kind', 'UNKNOWN')
        if self.service_binding is not None:
            return observed if (type(self.service_binding) is ServiceModelBinding
                and getattr(self.gateway, 'service_binding', None) is self.service_binding
                and observed in ('REAL', 'SIMULATED')) else 'UNKNOWN'
        if self.xing_binding is not None:
            if type(self.xing_binding) is not V6XingGenerationBinding:
                return 'UNKNOWN'
            grant = self.xing_binding.grant
            transport = self.xing_binding.authorized_transport
            return observed if observed in ('REAL', 'SIMULATED') and observed == getattr(grant, 'execution_kind', 'UNKNOWN') == getattr(
                transport, 'execution_kind', 'UNKNOWN') else 'UNKNOWN'
        if self.v6_binding is not None:
            if type(self.v6_binding) is not V6GenerationBinding:
                return 'UNKNOWN'
            return observed if observed in ('REAL', 'SIMULATED') and observed == getattr(self.v6_binding.grant, 'execution_kind', 'UNKNOWN') else 'UNKNOWN'
        return 'SIMULATED' if self.simulated and observed == 'SIMULATED' else 'UNKNOWN'

    def check(self) -> None:
        if self.service_binding is not None:
            if (type(self.service_binding) is not ServiceModelBinding or self.v6_binding is not None
                    or self.xing_binding is not None or self.simulated
                    or getattr(self.gateway, 'service_binding', None) is not self.service_binding
                    or self.role != getattr(getattr(self.gateway, 'spec', None), 'role', None)
                    or self.execution_kind not in ('REAL', 'SIMULATED')):
                raise ProviderRequestNotSent('SERVICE_MODEL_BINDING_INVALID')
            try:
                self.service_binding.validate(self.gateway.spec)
            except ValueError:
                raise ProviderRequestNotSent('SERVICE_MODEL_BINDING_INVALID') from None
            return
        expected = MODELS.get(self.role)
        actual = (self.provider, self.model)
        if expected is None or actual != expected:
            raise ProviderRequestNotSent("ROUTER_MODEL_IDENTITY_MISMATCH")
        if self.xing_binding is not None:
            if (type(self.xing_binding) is not V6XingGenerationBinding or self.role != 'chat_cheap'
                    or self.v6_binding is not None
                    or not callable(getattr(self.gateway, 'check_xing_binding', None))):
                raise ProviderRequestNotSent('XING_BINDING_INVALID')
            self.gateway.check_xing_binding(self.xing_binding)
            return
        if self.v6_binding is not None:
            binding = self.v6_binding
            if (type(binding) is not V6GenerationBinding or self.role != 'chat_expensive'
                    or type(binding.grant) is not OwnerActivityTokenGrant
                    or type(binding.activity_gate) is not V6OwnerActivityGate
                    or binding.grant is not binding.activity_gate.grant
                    or getattr(self.gateway, 'attempt_gate', None) is not binding.activity_gate
                    or binding.grant.execution_kind not in ('REAL', 'SIMULATED')
                    or binding.grant.execution_kind == 'REAL' and binding.grant is not OWNER_V6_TOKEN_GRANT and binding.grant is not OWNER_V6_TOKEN_GRANT_R2 and binding.grant is not OWNER_V6_TOKEN_GRANT_R3
                    or self.usage_guard is None
                    or not callable(getattr(self.gateway, 'prepare_v6_request', None))
                    or not callable(getattr(self.gateway, 'answer_with_v6_scope', None))):
                raise ProviderRequestNotSent('V6_GENERATION_BINDING_INVALID')
            if binding.server_scope is not None and (type(binding.server_scope) is not Scope
                    or type(binding.server_scope.knowledge_base_ids) is not frozenset
                    or type(binding.server_scope.document_ids) is not frozenset
                    or not binding.server_scope.knowledge_base_ids
                    or any(type(x) is not str or not x for x in
                           binding.server_scope.knowledge_base_ids | binding.server_scope.document_ids)):
                raise ProviderRequestNotSent('V6_EVALUATION_SERVER_SCOPE_INVALID')
            from backend.app.application.provider_usage import CanonicalTokenUsageGuard
            from backend.app.ports.v6_token_budget import V6CanonicalTokenBudgetBinding
            if binding.token_budget_binding is not None:
                if (type(binding.token_budget_binding) is not V6CanonicalTokenBudgetBinding
                        or type(self.usage_guard) is not CanonicalTokenUsageGuard
                        or self.usage_guard.binding is not binding.token_budget_binding
                        or self.usage_guard.gateway is not self.gateway
                        or binding.server_scope != binding.token_budget_binding.server_scope):
                    raise ProviderRequestNotSent('V6_CANONICAL_TOKEN_BINDING_REQUIRED')
                binding.token_budget_binding.validate(self.gateway)
            elif binding.grant is OWNER_V6_TOKEN_GRANT_R3:
                raise ProviderRequestNotSent('V6_CANONICAL_TOKEN_BINDING_REQUIRED')
            return
        if not self.simulated or getattr(self.gateway, "execution_kind", None) != "SIMULATED":
            raise ProviderRequestNotSent("P5_BLOCKED_REAL_AUTHORIZATION_AND_CAPACITY")

    def invoke(self, envelope: GenerationEnvelope, *, request_id: str, run_id: str, scope):
        self.check()
        if self.service_binding is not None:
            with call_stage('answer'), model_access(self.role, allowed=True, scope=scope):
                result = self.gateway.generate_with_service_scope([{'role': 'user', 'content': envelope.prompt}],
                    timeout_seconds=envelope.timeout_seconds, max_tokens=envelope.max_tokens,
                    scope=scope, request_id=request_id)
            if (result.model_requested != self.model or result.model_reported != self.model
                    or result.finish_reason != 'stop'):
                raise ProviderUnavailable('ROUTER_MODEL_RESPONSE_MISMATCH')
            return result.text
        prepared = None
        if self.v6_binding is not None:
            binding = self.v6_binding
            if binding.server_scope is not None:
                if type(scope) is not Scope or scope != binding.server_scope:
                    raise ProviderRequestNotSent('V6_EVALUATION_SCOPE_REQUIRED')
                evaluation_scope = binding.grant.scope
            elif type(scope) is str and scope == binding.grant.scope:
                evaluation_scope = scope
            else:
                raise ProviderRequestNotSent('V6_EVALUATION_SCOPE_REQUIRED')
            prepared = self.gateway.prepare_v6_request(envelope.prompt, envelope.timeout_seconds,
                envelope.max_tokens, grant=binding.grant, scope=evaluation_scope, capacity_counter=binding.capacity_counter)
        if self.simulated_input_limit is not None:
            if type(self.simulated_input_limit) is not int or self.simulated_input_limit <= 0:
                raise ProviderRequestNotSent("SIMULATED_CAPACITY_INVALID")
            if envelope.estimated_input_tokens is None:
                raise ProviderRequestNotSent("MODEL_INPUT_CAPACITY_UNKNOWN")
            if envelope.estimated_input_tokens > self.simulated_input_limit:
                raise ProviderRequestNotSent("MODEL_INPUT_CAPACITY_EXCEEDED")
        with call_stage("answer"), model_access(self.role, allowed=True):
            if self.role == "chat_cheap":
                # The adapter owns its single normal reserve/settle path.
                if getattr(self.gateway, "usage_guard", None) is None:
                    raise ProviderRequestNotSent("MODEL_USAGE_GUARD_REQUIRED")
                if self.xing_binding is not None:
                    result = self.gateway.generate_with_xing_scope([{'role': 'user', 'content': envelope.prompt}],
                        timeout_seconds=envelope.timeout_seconds, max_tokens=envelope.max_tokens,
                        scope=scope, purpose='answer', request_id=request_id)
                else:
                    result = self.gateway.generate([{"role": "user", "content": envelope.prompt}],
                        timeout_seconds=envelope.timeout_seconds, max_tokens=envelope.max_tokens)
                if result.model_requested != MODELS[self.role][1] or result.model_reported != MODELS[self.role][1]:
                    raise ProviderUnavailable("ROUTER_MODEL_RESPONSE_MISMATCH")
                if result.finish_reason != "stop":
                    raise ValueError("ROUTER_FINISH_INVALID")
                return result.text
            if prepared is not None and self.v6_binding.token_budget_binding is not None:
                permit = self.usage_guard.admit(prepared, request_id=request_id, run_id=run_id,
                    scope=scope, envelope_sha256=envelope.identity)
                not_sent = False
                try:
                    return self.gateway.answer_with_v6_scope(envelope.prompt, envelope.timeout_seconds,
                        envelope.max_tokens, prepared_request=prepared, scope=evaluation_scope,
                        request_id=request_id, cloud_authorized=True, token_budget_request=permit)
                except ProviderRequestNotSent:
                    not_sent = True
                    raise
                finally:
                    self.usage_guard.verify_terminal(permit, not_sent=not_sent)
            # Legacy DeepSeek continues through its existing closed gate and durable
            # receipts. The separate monthly guard is used exactly once here.
            if self.usage_guard is None or (prepared is None and not callable(getattr(self.gateway, "answer_with_product_scope", None))):
                raise ProviderRequestNotSent("DEEPSEEK_GATED_ROLE_REQUIRED")
            try:
                reservation = self.usage_guard.reserve(model_key="chat_expensive_default", role=self.role,
                    model_id=MODELS[self.role][1], planned_tokens=(
                        prepared.budget.input_token_bound + prepared.budget.safety_tokens + prepared.budget.output_token_bound
                        if prepared is not None else envelope.max_tokens))
            except Exception:
                raise ProviderRequestNotSent("MODEL_BUDGET_DENIED") from None
            sent = True  # conservatively unknown unless explicitly not sent
            primary_outcome = "COMPLETED"
            try:
                if prepared is not None:
                    return self.gateway.answer_with_v6_scope(envelope.prompt, envelope.timeout_seconds,
                        envelope.max_tokens, prepared_request=prepared, scope=evaluation_scope,
                        request_id=request_id, cloud_authorized=True)
                return self.gateway.answer_with_product_scope(envelope.prompt, envelope.timeout_seconds,
                    envelope.max_tokens, cloud_authorized=True, request_id=request_id,
                    run_id=run_id, scope=scope, question=envelope.q0)
            except ProviderRequestNotSent:
                sent = False
                primary_outcome = "NOT_SENT"
                raise
            except TruncatedAnswer:
                primary_outcome = "TRUNCATED"
                raise
            except Exception:
                primary_outcome = "PROVIDER_FAILURE"
                raise
            finally:
                try:
                    self.usage_guard.settle(reservation, None, sent=sent)
                except Exception:
                    raise GenerationSettlementFailure(not_sent=not sent,
                        primary_outcome=primary_outcome) from None

    def invoke_auxiliary(self, messages, *, purpose, request_id, run_id, scope,
                         timeout_seconds=20, max_tokens=256):
        """Explicit Cheap-only helper entry; does not turn on product services."""
        self.check()
        if (self.role != 'chat_cheap' or self.xing_binding is None
                or purpose not in ('understand', 'rewrite', 'expansion')):
            raise ProviderRequestNotSent('XING_AUXILIARY_PURPOSE_DENIED')
        with call_stage('query_understand' if purpose == 'understand' else 'query'), model_access('chat_cheap', allowed=True):
            return self.gateway.generate_with_xing_scope(messages, timeout_seconds=timeout_seconds,
                max_tokens=max_tokens, scope=scope, purpose=purpose, request_id=request_id)


def whole_abstention(answer: str) -> bool:
    # Whole-answer grammar only; quoted and partial refusals never match.
    return bool(re.fullmatch(r"\s*(?:资料不足|证据不足|无法回答|没有足够证据)(?:，无法回答)?[。.!！]?\s*", answer))


def escalation_reason(policy: RouterPolicy, *, role: str, error: str | None,
                      answer: str, provider_failed: bool) -> str | None:
    if role != "chat_cheap" or policy.mode in {"CHEAP_ONLY", "EXPENSIVE_ONLY"}:
        return None
    if provider_failed:
        return "PROVIDER_FALLBACK" if policy.provider_fallback else None
    if whole_abstention(answer):
        return "ABSTENTION_ESCALATION" if policy.abstention_escalation else None
    if policy.quality_escalation and (error in QUALITY_ERRORS or policy.truncated_escalation and error == "MODEL_OUTPUT_TRUNCATED"):
        return "QUALITY_ESCALATION"
    return None
