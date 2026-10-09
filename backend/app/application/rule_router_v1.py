"""RuleRouter V1 contracts. No retrieval, secret loading or network transports."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import re
from backend.app.application.router_feature_extraction import Features, extract_features
from backend.app.ports.providers import ProviderRequestNotSent, ProviderUnavailable, TruncatedAnswer
from backend.app.ports.model_access import model_access
from backend.app.ports.model_usage import call_stage

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


def decide(question: str, prompt: str, policy: RouterPolicy, *, estimated_tokens=None, estimator="UNKNOWN") -> Decision:
    if policy.mode == "OFF":
        raise ValueError("ROUTER_OFF")
    features = extract_features(question, prompt, estimated_tokens=estimated_tokens, estimator=estimator)
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

    @property
    def identity(self) -> str:
        return digest(json.dumps({"q0_hash": digest(self.q0), "resolved_hash": digest(self.resolved_query),
            "prompt_hash": digest(self.prompt), "context_hash": self.context_hash, "labels": self.labels,
            "snapshot_identities": self.snapshot_identities, "max_tokens": self.max_tokens,
            "timeout_seconds": self.timeout_seconds, "template_version": self.template_version,
            "estimated_input_tokens": self.estimated_input_tokens}, sort_keys=True))


@dataclass(frozen=True)
class GenerationRole:
    """Trusted composition-root binding; real execution stays blocked in P5.

    No booleans/config keys can turn a simulated binding into an authorized real
    provider. Real adapters remain reachable only behind a future reviewed grant.
    """
    role: str
    gateway: object
    simulated: bool = False
    usage_guard: object | None = None
    simulated_input_limit: int | None = None

    @property
    def provider(self) -> str | None:
        return getattr(self.gateway, "provider_name", None)

    @property
    def model(self) -> str | None:
        spec = getattr(self.gateway, "spec", None)
        return spec.model_id if spec is not None else getattr(self.gateway, "chat_model", None)

    def check(self) -> None:
        expected = MODELS.get(self.role)
        actual = (self.provider, self.model)
        if expected is None or actual != expected:
            raise ProviderRequestNotSent("ROUTER_MODEL_IDENTITY_MISMATCH")
        if not self.simulated or getattr(self.gateway, "execution_kind", None) != "SIMULATED":
            raise ProviderRequestNotSent("P5_BLOCKED_REAL_AUTHORIZATION_AND_CAPACITY")

    def invoke(self, envelope: GenerationEnvelope, *, request_id: str, run_id: str, scope):
        self.check()
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
                result = self.gateway.generate([{"role": "user", "content": envelope.prompt}],
                    timeout_seconds=envelope.timeout_seconds, max_tokens=envelope.max_tokens)
                if result.model_requested != MODELS[self.role][1] or result.model_reported != MODELS[self.role][1]:
                    raise ProviderUnavailable("ROUTER_MODEL_RESPONSE_MISMATCH")
                if result.finish_reason != "stop":
                    raise ValueError("ROUTER_FINISH_INVALID")
                return result.text
            # DeepSeek continues through its existing closed gate and durable
            # receipts. The separate monthly guard is used exactly once here.
            if self.usage_guard is None or not callable(getattr(self.gateway, "answer_with_product_scope", None)):
                raise ProviderRequestNotSent("DEEPSEEK_GATED_ROLE_REQUIRED")
            try:
                reservation = self.usage_guard.reserve(model_key="chat_expensive_default", role=self.role,
                    model_id=MODELS[self.role][1], planned_tokens=envelope.max_tokens)
            except Exception:
                raise ProviderRequestNotSent("MODEL_BUDGET_DENIED") from None
            sent = True  # conservatively unknown unless explicitly not sent
            primary_outcome = "COMPLETED"
            try:
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
