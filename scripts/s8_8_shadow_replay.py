"""Read saved S8.7 evidence; freeze prompt-only pairs; NEVER sends requests.

Output contains private inputs and must remain outside Git. No Gold or outcome
is supplied to shadow_decide; outcome joins happen only after the decision.
"""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from backend.weknora_port.generation.prompt_candidates import apply_candidate, candidate_manifest
from backend.weknora_port.generation.shadow import shadow_decide


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def freeze_pair(body):
    if not isinstance(body.get('messages'), list) or not body['messages']:
        raise ValueError('ORIGINAL_MESSAGES_REQUIRED')
    system = [i for i, m in enumerate(body['messages']) if m.get('role') == 'system']
    if len(system) != 1 or not isinstance(body['messages'][system[0]].get('content'), str):
        raise ValueError('EXACT_ONE_TEXT_SYSTEM_REQUIRED')
    candidate = deepcopy(body)
    old = candidate['messages'][system[0]]['content']
    candidate['messages'][system[0]]['content'] = apply_candidate(old)
    proof = deepcopy(candidate)
    proof['messages'][system[0]]['content'] = old
    if proof != body:
        raise ValueError('PROMPT_ONLY_CONTRACT_VIOLATION')
    return {'status':'PENDING_NEW_AUTHORIZATION', 'baseline_body':deepcopy(body),
            'candidate_body':candidate, 'baseline_body_sha256':digest(body),
            'candidate_body_sha256':digest(candidate), 'prompt':candidate_manifest(old),
            'new_request_identity_required':True, 'changed_fields':['messages.system.content']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.evidence_root
    report = json.loads((root/'S8_7_EXPENSIVE_COUNTERFACTUAL.results.json').read_text(encoding='utf-8'))
    manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    original = json.loads((root/'original-request-envelopes.private.json').read_text(encoding='utf-8'))
    requests = {x['request']['request_id']: x['request'] for x in original}
    replay = []
    for row in report['rows']:
        route = row.get('route') or {}
        # These fields were not captured in historical first-route state.
        # Neither semantic labels nor new conclusions can backfill them.
        snapshot = deepcopy(route.get('input_snapshot') or {})
        role = route.get('decision_model_role', 'UNVERIFIED')
        if role.startswith('chat_'):
            role = role[5:]
        decision = shadow_decide(snapshot, baseline_role=role, mode='shadow_structure_v1')
        replay.append({'case_sha256':digest(row['case_id']), 'decision':decision,
                       'outcome_for_offline_join_only':row.get('comparison_bucket'),
                       'group_for_offline_denominator_only':row['group']})
    selected = {2, 4, 13, 34, 39, 49}  # Frozen before new model results; two controls.
    pairs = []
    for case in manifest['cases']:
        if case['gold_ordinal'] not in selected:
            continue
        source = requests[case['original_request_id']]
        if source['body']['messages'] != next(r['original_messages'] for r in report['rows']
                                              if r['case_id'] == case['case_id']):
            raise ValueError('FROZEN_MESSAGES_MISMATCH')
        pairs.append({'case_id':case['case_id'], 'gold_ordinal':case['gold_ordinal'],
                      'provider':source['provider'], 'model':source['model'],
                      'scope':source['scope'], 'pair':freeze_pair(source['body'])})
    result = {'schema':'s88-offline-shadow-and-prompt/v1', 'provider_calls':0,
              'shadow_rows':replay, 'shadow_status_counts':dict(Counter(r['decision']['status'] for r in replay)),
              'historical_feature_coverage_limit':'Missing features remain missing; no claimed calibrated benefit.',
              'fixed_prompt_pairs':pairs, 'prompt_quality_status':'PAIRED_EVAL_PENDING_AUTH',
              'source_result_sha256':hashlib.sha256((root/'S8_7_EXPENSIVE_COUNTERFACTUAL.results.json').read_bytes()).hexdigest()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'rows':len(replay), 'shadow_status_counts':result['shadow_status_counts'],
                      'prompt_pairs':len(pairs), 'provider_calls':0}))


if __name__ == '__main__':
    main()
