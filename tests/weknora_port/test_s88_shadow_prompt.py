from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest

from backend.weknora_port.generation.shadow import snapshot_from_state, enrich_snapshot, shadow_decide
from backend.weknora_port.generation.prompt_candidates import apply_candidate, candidate_manifest
from backend.weknora_port.generation.features import extract_features
from backend.weknora_port.generation.context import Evidence
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.strategy import plan_query


def state():
    q = 'Compare Alpha and Beta in terms of capacity.'
    s = PipelineState(query=q, rewrite_query=q, retrieval_strategy_mode='query_structure_v1',
                      query_type='SIMPLE_COMPARE')
    s.query_trace = [{'contract': 'qu_v2', 'query_type': 'SIMPLE_COMPARE'}]
    s.strategy_plan = plan_query(q, q, 'SIMPLE_COMPARE')
    return s


def evidence():
    return tuple(Evidence('c'+str(i), 'text', 'd'+str(i), 'kb',
                          metadata={'source_version':'v1'}) for i in range(2))


def test_server_snapshot_preserves_unknown_and_never_emits_text():
    s = state()
    snap = snapshot_from_state(s, evidence())
    assert snap['query_type'] == 'SIMPLE_COMPARE'
    assert snap['explicit_object_count'] == 2
    assert snap['distinct_document_versions'] == 2
    assert snap['general_ambiguity'] is None and snap['evidence_sufficiency'] is None
    assert 'Alpha' not in json.dumps(snap) and 'kb' not in json.dumps(snap)
    s.query_trace = []
    assert snapshot_from_state(s, evidence())['query_type'] is None


def test_source_identity_incomplete_is_not_zero_or_verified():
    rows = evidence()
    snap = snapshot_from_state(state(), (rows[0], replace(rows[1], metadata={})))
    assert snap['distinct_document_versions'] is None


def test_shadow_off_and_on_do_not_mutate_input_or_dispatch():
    snap = snapshot_from_state(state(), evidence())
    before = json.dumps(snap, sort_keys=True)
    assert shadow_decide(snap, baseline_role='cheap')['candidate_role'] == 'cheap'
    decision = shadow_decide(snap, baseline_role='cheap', mode='shadow_structure_v1')
    assert decision['candidate_role'] == 'expensive' and not decision['dispatch_changed']
    assert json.dumps(snap, sort_keys=True) == before


@pytest.mark.parametrize('patch,reason', [
    ({'query_type':None}, 'QUERY_TYPE_UNAVAILABLE'),
    ({'query_type':'UNKNOWN'}, 'QUERY_TYPE_UNAVAILABLE'),
    ({'query_type':'DEPENDENT'}, 'OUTSIDE_NARROW_RULE'),
    ({'explicit_object_count':True}, 'VALIDATED_OBJECTS_UNAVAILABLE'),
    ({'distinct_document_versions':None}, 'SOURCE_IDENTITY_COUNT_UNAVAILABLE'),
    ({'bounded_reference_ambiguity':True}, 'REFERENCE_AMBIGUOUS'),
])
def test_missing_or_unsafe_features_do_not_nominate_expensive(patch, reason):
    snap = {**snapshot_from_state(state(), evidence()), **patch}
    decision = shadow_decide(snap, baseline_role='cheap', mode='shadow_structure_v1')
    assert decision['candidate_role'] == 'cheap' and decision['reason'] == reason


def test_snapshot_enrichment_drops_arbitrary_scoring_fields():
    snap = {'gold':'secret', 'cheap_answer':'secret', 'case_id':'secret'}
    prepared = SimpleNamespace(full_prompt='private unicode 值')
    out = enrich_snapshot(snap, prepared, extract_features('q','p'))
    assert not set(snap) & out.keys()
    assert out['prompt_utf8_bytes'] == len(prepared.full_prompt.encode())
    assert prepared.full_prompt not in json.dumps(out)


def test_prompt_candidate_is_versioned_single_factor_and_opt_in():
    base = 'Evidence and citation protocol. Answer in {{language}}.'
    assert apply_candidate(base, version='off') == base
    candidate = apply_candidate(base)
    assert candidate.startswith(base)
    assert candidate_manifest(base)['default_changed'] is False
    assert candidate_manifest(base)['real_quality_status'] == 'PAIRED_EVAL_PENDING_AUTH'
    with pytest.raises(ValueError, match='ALREADY_APPLIED'):
        apply_candidate(candidate)
    with pytest.raises(ValueError, match='VERSION_INVALID'):
        apply_candidate(base, version='unknown')


def test_real_coordinator_records_shadow_but_keeps_baseline_dispatch():
    from test_generation import setup, event, request, run, CTX
    co, cheap, expensive, store, committer, life = setup([
        event('supported answer <ref id="c1"/>', done=True, finish='stop', final=True)])
    req = request(routing_snapshot=snapshot_from_state(state(), evidence()),
                  shadow_routing_mode='shadow_structure_v1')
    result = run(co.run(req, CTX))
    assert result.route['shadow']['candidate_role'] == 'expensive'
    assert result.selected_role == 'cheap' and len(cheap.calls) == 1 and not expensive.calls
    assert result.route['input_snapshot']['timing'] == 'BEFORE_FINAL_GENERATION'
    completed = [e for e in store.events if e.type == 'complete']
    assert completed[-1].data['router']['shadow']['dispatch_changed'] is False


def test_frozen_prompt_pair_preserves_all_other_fields():
    from scripts.s8_8_shadow_replay import freeze_pair
    body = {'messages':[{'role':'system','content':'citation rules'},
                        {'role':'user','content':'current original evidence'}],
            'model':'same-model','temperature':0.7,'max_tokens':2048,'enable_thinking':False}
    pair = freeze_pair(body)
    assert pair['baseline_body'] == body
    candidate = pair['candidate_body']
    assert candidate['messages'][1:] == body['messages'][1:]
    assert {k:v for k,v in candidate.items() if k!='messages'} == {k:v for k,v in body.items() if k!='messages'}
    assert pair['new_request_identity_required'] is True


@pytest.mark.parametrize('version,expected_hash,ends_with_newline', [
    ('s88-source-precision/v2',
     '08d01e2d146515439aa31ca344599ade04fae042f90c2a5bc02110c6654409ba', False),
    ('s88-source-precision/v3',
     '7c4a091438eab40f4c73e814af052185cb6dab29d282828c7d514bec3506e450', True),
])
def test_tested_candidate_suffix_matches_frozen_wire(version, expected_hash, ends_with_newline):
    # Hashes pin the exact UTF-8 suffix in evaluated system messages, including
    # the marker and final LF, independently of local text-file line endings.
    from backend.weknora_port.generation.prompt_candidates import (
        CANDIDATE_SUFFIXES, CANDIDATE_SUFFIX_SHA256)
    base = 'Synthetic citation protocol.\n'
    suffix = CANDIDATE_SUFFIXES[version]
    assert suffix.startswith('System answer precision [' + version + ']:\n')
    assert suffix.endswith('\n') is ends_with_newline
    assert hashlib.sha256(suffix.encode()).hexdigest() == expected_hash
    assert CANDIDATE_SUFFIX_SHA256[version] == expected_hash
    assert apply_candidate(base, version=version) == base + '\n\n' + suffix
    manifest = candidate_manifest(base, version=version)
    assert manifest['version'] == version and manifest['suffix_sha256'] == expected_hash
    assert manifest['candidate_sha256'] == hashlib.sha256(
        apply_candidate(base, version=version).encode()).hexdigest()
    assert manifest['real_quality_status'] == 'DEVELOPMENT_SET_EVALUATED'
    assert manifest['default_changed'] is False


def test_candidate_catalog_is_immutable_and_v1_default_is_unchanged():
    from backend.weknora_port.generation.prompt_candidates import (
        CANDIDATE_VERSION, CANDIDATE_RULES, CANDIDATE_SUFFIXES, CANDIDATE_SUFFIX_SHA256)
    assert CANDIDATE_VERSION == 's88-source-precision/v1'
    expected_rules = (
        "Keep procedures from different sources separate when their steps are incompatible; preserve each procedure's conditions and exceptions.\n"
        'Make the opening conclusion agree with the final explanation. Bind each date to its specific event, such as founding, renaming, or merger.\n'
        'Distinguish conflicting sources explicitly; do not resolve a conflict by inventing identifiers, aliases, or explanations.\n'
        'People and cases described in sources are not the current user; do not attribute their experiences to the user.'
    )
    assert CANDIDATE_RULES == expected_rules
    base = 'Synthetic base'
    expected = base + '\n\nSystem answer precision [s88-source-precision/v1]:\n' + expected_rules
    assert apply_candidate(base) == expected
    assert candidate_manifest(base) == {
        'version': CANDIDATE_VERSION,
        'base_sha256': hashlib.sha256(base.encode()).hexdigest(),
        'candidate_sha256': hashlib.sha256(expected.encode()).hexdigest(),
        'added_utf8_bytes': len(expected.encode()) - len(base.encode()),
        'real_quality_status': 'PAIRED_EVAL_PENDING_AUTH',
        'default_changed': False,
    }
    assert apply_candidate(base, version='off') == base
    for catalog in (CANDIDATE_SUFFIXES, CANDIDATE_SUFFIX_SHA256):
        assert tuple(catalog) == tuple('s88-source-precision/' + v for v in ('v1', 'v2', 'v3'))
        with pytest.raises(TypeError):
            catalog[CANDIDATE_VERSION] = 'changed'


@pytest.mark.parametrize('first', ['v1', 'v2', 'v3'])
@pytest.mark.parametrize('second', ['v1', 'v2', 'v3'])
def test_candidate_versions_cannot_be_stacked(first, second):
    prompt = apply_candidate('Synthetic base', version='s88-source-precision/' + first)
    with pytest.raises(ValueError, match='ALREADY_APPLIED'):
        apply_candidate(prompt, version='s88-source-precision/' + second)


def test_explicit_candidate_manifest_off_and_invalid_version():
    manifest = candidate_manifest('Synthetic base', version='off')
    assert manifest['version'] == 'off' and manifest['added_utf8_bytes'] == 0
    assert manifest['base_sha256'] == manifest['candidate_sha256']
    assert manifest['real_quality_status'] == 'OFF' and not manifest['default_changed']
    with pytest.raises(ValueError, match='VERSION_INVALID'):
        candidate_manifest('Synthetic base', version='unknown')
