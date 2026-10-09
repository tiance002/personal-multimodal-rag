"""Offline cached-response diagnostic. Never constructs a provider or database."""
from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.application.rule_router_v1 import (
    GenerationEnvelope, MODELS, RouterPolicy, decide, digest, escalation_reason,
)

POSTPROCESS_FILES = ("backend/app/application/answer_validation.py",
                     "backend/app/application/answer_hardening.py",
                     "backend/app/application/final_answer_commit.py")


def postprocess_identity() -> str:
    return digest(json.dumps({p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in POSTPROCESS_FILES}, sort_keys=True))


def cache_identity(envelope: GenerationEnvelope, role: str) -> str:
    return digest(json.dumps({"envelope_identity": envelope.identity, "provider_model": MODELS[role],
                             "postprocess_identity": postprocess_identity()}, sort_keys=True))


def envelope_for(case: dict) -> GenerationEnvelope:
    # This fixture prompt is separate from the product template; old cached
    # responses cannot be used for a different template/Context/model/parameters.
    return GenerationEnvelope(case['q0'], case['q0'], case['prompt'], digest(case['context']), ('E1',),
        (('simulated-v1', case['id'], digest(case['context']), digest('{}')),),
        case['max_tokens'], 30, case['template_version'])


def candidate_check(answer: str, case: dict) -> str | None:
    from backend.app.application.knowledge_gateway import EvidenceService
    from backend.app.application.answer_hardening import HardeningPolicy
    from backend.app.application.answer_validation import AnswerValidator
    from backend.app.domain.models import EvidenceSnapshot
    snapshot = EvidenceSnapshot(label='E1', version_id='simulated-v1', chunk_id=case['id'],
                                quote=case['context'], quote_sha256=digest(case['context']), locator={})
    plan = EvidenceService().plan(case['q0'])
    error = AnswerValidator().validate(answer, (snapshot,), plan.evidence_plan)
    return HardeningPolicy().check_candidate(answer, (snapshot,), plan, error)


def net_saving(dynamic_cost: Decimal | None, expensive_cost: Decimal | None,
               auxiliary_cost: Decimal | None) -> str:
    if any(v is None or not v.is_finite() or v < 0 for v in (dynamic_cost, expensive_cost, auxiliary_cost)) or expensive_cost == 0:
        return 'NOT_AVAILABLE'
    return str(Decimal(1) - (dynamic_cost + auxiliary_cost) / expensive_cost)


def replay_case(case: dict, mode: str, *, same_b: bool = False) -> dict:
    started = time.perf_counter()
    envelope = envelope_for(case)
    policy = RouterPolicy(mode=mode)
    decision = decide(case['q0'], envelope.prompt, policy)
    role = decision.role
    attempts = []
    reason = None
    for ordinal in (1, 2):
        cache = case['responses'][role]
        if cache.get('identity') != cache_identity(envelope, role):
            raise ValueError('REPLAY_CACHE_IDENTITY_MISMATCH')
        if cache.get('execution_kind') != 'SIMULATED' or cache.get('bill_cost') is not None:
            raise ValueError('REPLAY_NOT_SIMULATED')
        error = candidate_check(cache['answer'], case)
        attempts.append({'attempt_id': digest(case['id'] + mode + role + str(ordinal)),
            'role': role, 'provider': MODELS[role][0], 'model': MODELS[role][1],
            'envelope_identity': envelope.identity, 'execution_kind': 'SIMULATED_CACHED',
            'latency_ms': cache['latency_ms'], 'latency_basis': 'SIMULATED_SCENARIO',
            'actual_tokens_or_UNKNOWN': 'UNKNOWN', 'simulated_tokens': cache['tokens'],
            'settlement_status': 'UNKNOWN', 'actual_cost': 'UNKNOWN', 'cost_provenance': 'NO_BILLING_RECEIPT',
            'result_validation': error or 'PASS'})
        b_policy = RouterPolicy(mode='DYNAMIC') if same_b else policy
        next_reason = escalation_reason(b_policy, role=role, error=error, answer=cache['answer'], provider_failed=False)
        if ordinal == 1 and next_reason:
            reason, role = next_reason, 'chat_expensive'
            continue
        break
    return decision.public(policy) | {'id': case['id'], 'context_hash': envelope.context_hash,
        'envelope_identity': envelope.identity, 'attempts': attempts, 'escalation_reason': reason,
        'result_validation': attempts[-1]['result_validation'],
        'semantic_correctness': 'NOT_REVIEWED', 'net_saving': 'NOT_AVAILABLE',
        'total_latency_ms': sum(a['latency_ms'] for a in attempts),
        'router_cpu_latency_ms': (time.perf_counter() - started) * 1000}


def replay(cases: list[dict]) -> dict:
    arms = {}
    for mode in ('CHEAP_ONLY','DYNAMIC','EXPENSIVE_ONLY','ALL_CHEAP_SAME_B'):
        rows = [replay_case(c, 'CHEAP_ONLY' if mode == 'ALL_CHEAP_SAME_B' else mode,
                            same_b=mode == 'ALL_CHEAP_SAME_B') for c in cases]
        latencies = sorted(r['total_latency_ms'] for r in rows)
        arms[mode] = {'rows':rows, 'summary':{'count':len(rows),
            'direct_expensive_count':sum(r['decision_model_role'] == 'chat_expensive' for r in rows),
            'escalation_count':sum(r['escalation_reason'] is not None for r in rows),
            'deterministic_validation_pass_count':sum(r['result_validation'] == 'PASS' for r in rows),
            'semantic_correctness': 'NOT_REVIEWED', 'net_saving':'NOT_AVAILABLE',
            'p50_latency_ms': latencies[(len(latencies)-1)//2] if latencies else None,
            'p95_latency_ms':latencies[max(0,(95*len(latencies)+99)//100-1)] if latencies else None,
            'latency_basis':'SIMULATED_SCENARIO_NOT_PROVIDER_MEASUREMENT'}}
    return {'schema_version':'p5-router-replay/v1','execution_kind':'SIMULATED_OFFLINE',
            'real_calls':0, 'dataset_version':'router-dev/v1', 'arms':arms}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, default=ROOT/'evaluations/router_dev/cases-v1.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    body = json.loads(args.fixture.read_text(encoding='utf8'))
    if body.get('schema_version') != 'router-dev/v1' or body.get('execution_kind') != 'SIMULATED':
        raise ValueError('REPLAY_FIXTURE_INVALID')
    report = replay(body['cases'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Never overwrite a prior replay, even on identical inputs.
    with args.output.open('x', encoding='utf8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({'real_calls':0, 'case_count':len(body['cases']), 'output':str(args.output)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
