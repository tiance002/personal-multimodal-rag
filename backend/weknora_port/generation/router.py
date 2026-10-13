"""Frozen P5 pure routing port; no legacy runtime imports or role adapters."""
from dataclasses import dataclass, replace
from decimal import Decimal
from .features import Features, extract_features
VERSION = 'rule-router-v1/draft-v0.4'
MODELS = {'cheap': ('siliconflow', 'XingChenAGI/Xing4.0-29B'), 'expensive': ('deepseek', 'deepseek-flash')}

@dataclass(frozen=True)
class RouterPolicy:
    mode: str = 'OFF'
    threshold: Decimal = Decimal('0.35')
    quality_escalation: bool = True
    truncated_escalation: bool = True
    abstention_escalation: bool = False
    provider_fallback: bool = False

    def __post_init__(self):
        if self.mode not in ('OFF', 'DYNAMIC', 'CHEAP_ONLY', 'EXPENSIVE_ONLY'):
            raise ValueError('ROUTER_MODE_INVALID')
        if self.threshold != Decimal('0.35') or not isinstance(self.threshold, Decimal):
            raise ValueError('ROUTER_THRESHOLD_FROZEN')
        if any(type(value) is not bool for value in (self.quality_escalation,self.truncated_escalation,self.abstention_escalation,self.provider_fallback)):
            raise ValueError('ROUTER_POLICY_FROZEN')
        if (self.quality_escalation, self.truncated_escalation, self.abstention_escalation, self.provider_fallback) != (True, True, False, False):
            raise ValueError('ROUTER_POLICY_FROZEN')

@dataclass(frozen=True)
class ValidatedRewrite:
    q0: str
    rewrite_query: str
    task_status: str = 'COMPLETE'
    needs_clarification: bool = False

@dataclass(frozen=True)
class Decision:
    features: Features
    score: Decimal
    role: str
    reason: str

    def public(self, policy):
        return {'router_version': VERSION, 'threshold': str(policy.threshold), 'mode': policy.mode, **self.features.public(), 'score': str(self.score), 'decision_model_role': 'chat_' + self.role, 'route_reason': self.reason}

def decide(question, prompt, policy, *, estimated_tokens=None, estimator='UNKNOWN', understanding=None):
    if policy.mode == 'OFF':
        raise ValueError('ROUTER_OFF')
    features = extract_features(question, prompt, estimated_tokens=estimated_tokens, estimator=estimator)
    if understanding is not None:
        if not isinstance(understanding, ValidatedRewrite) or understanding.needs_clarification or understanding.task_status != 'COMPLETE' or (understanding.rewrite_query != question):
            raise ValueError('ROUTER_QUERY_UNDERSTAND_INVALID')
        original = extract_features(understanding.q0, prompt, estimated_tokens=estimated_tokens, estimator=estimator)
        use_original = [a > b for a, b in zip(original.contributions, features.contributions)]
        features = replace(features, contributions=tuple((max(a, b) for a, b in zip(original.contributions, features.contributions))), statuses=tuple((a if choose else b for a, b, choose in zip(original.statuses, features.statuses, use_original))), anchors=tuple(dict.fromkeys((*original.anchors, *features.anchors))), task_labels=tuple(dict.fromkeys((*original.task_labels, *features.task_labels))), condition_count=original.condition_count if use_original[1] else features.condition_count, logic=original.logic if use_original[2] else features.logic, dependency_depth=original.dependency_depth if use_original[3] else features.dependency_depth, output_count=original.output_count if use_original[5] else features.output_count)
    score = Decimal(sum(features.contributions)) / 100
    role = 'cheap' if policy.mode == 'CHEAP_ONLY' else 'expensive' if policy.mode == 'EXPENSIVE_ONLY' else 'expensive' if score >= policy.threshold else 'cheap'
    return Decision(features, score, role, 'OFFLINE_ARM' if policy.mode.endswith('ONLY') else 'SCORE_AT_OR_ABOVE_THRESHOLD' if role == 'expensive' else 'SCORE_BELOW_THRESHOLD')
