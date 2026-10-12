"""Offline SIMULATED provider receipts; never an endpoint probe."""
from dataclasses import asdict, FrozenInstanceError, replace
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from backend.app.ports.output_capability import (
    OutputCapabilityEvidence, PROTOCOLS, SYNTHETIC_OUTPUT_PROBE,
    consume_output_probe, check_output_probe_payload, load_output_capabilities,
    output_probe_admission,
)
from backend.app.ports.providers import ProviderRequestNotSent
from backend.app.ports.run_lifecycle import current_attempt, current_execution, execution_owner


def evidence(cap=1024, provider='deepseek'):
    model = 'deepseek-flash' if provider == 'deepseek' else 'XingChenAGI/Xing4.0-29B'
    protocol, endpoint = PROTOCOLS[(provider, model)]
    request = dict(model=model, messages=[dict(role='user', content=SYNTHETIC_OUTPUT_PROBE)],
                   max_tokens=cap, stream=False)
    if provider == 'deepseek':
        request.update(thinking={'type': 'disabled'}, temperature=0)
    else:
        request['enable_thinking'] = False
    response = dict(model=model, usage=dict(prompt_tokens=10, completion_tokens=cap, total_tokens=10+cap),
                    choices=[dict(finish_reason='length', message=dict(role='assistant', content=' '.join(['capacity'] * cap)))])
    return OutputCapabilityEvidence(provider, model, protocol, endpoint, cap,
                                    json.dumps(request), json.dumps(response), 'run', f'attempt-{provider}-{cap}')


def test_thinking_disabled_probe_cannot_use_reasoning_tokens_as_visible_cap_proof():
    proof = evidence()
    response = json.loads(proof.response_wire)
    response['choices'][0]['message']['reasoning_content'] = 'private reasoning'
    with pytest.raises(ProviderRequestNotSent):
        replace(proof, response_wire=json.dumps(response))


@pytest.mark.parametrize('provider', ['deepseek', 'siliconflow'])
@pytest.mark.parametrize('cap', [1024, 2048, 4096])
def test_pinned_receipt_round_trip_is_immutable(tmp_path, provider, cap):
    proof = evidence(cap, provider)
    path = tmp_path / 'public.json'
    raw = json.dumps(dict(schema_version=3, receipts=[asdict(proof)])).encode()
    path.write_bytes(raw)
    assert load_output_capabilities(path, hashlib.sha256(raw).hexdigest()) == (proof,)
    assert f'cap={cap}' in proof.identity
    with pytest.raises(FrozenInstanceError):
        proof.output_tokens = 4096
    path.write_bytes(raw + b' ')
    with pytest.raises(ProviderRequestNotSent):
        load_output_capabilities(path, hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize('mutation', ['model', 'endpoint', 'protocol', 'cap', 'usage', 'sum', 'finish', 'private'])
def test_receipt_identity_usage_and_exact_synthetic_protocol_fail_closed(mutation):
    proof = evidence()
    fields = asdict(proof)
    response = json.loads(proof.response_wire)
    if mutation in ('model', 'endpoint', 'protocol'):
        fields[mutation] += '-different'
    elif mutation == 'cap':
        fields['output_tokens'] = 2048
    elif mutation == 'usage':
        response['usage']['completion_tokens'] = 1025
    elif mutation == 'sum':
        response['usage']['total_tokens'] = 999
    elif mutation == 'finish':
        response['choices'][0]['finish_reason'] = 'tool_calls'
    else:
        request = json.loads(proof.request_wire)
        request['messages'][0]['content'] = 'Private corpus data'
        fields['request_wire'] = json.dumps(request)
    fields['response_wire'] = json.dumps(response)
    with pytest.raises(ProviderRequestNotSent):
        OutputCapabilityEvidence(**fields)


@pytest.mark.parametrize('cap', [1024, 2048, 4096])
@pytest.mark.parametrize('finish', ['stop', 'length'])
def test_short_output_cannot_prove_a_requested_cap_even_when_request_was_accepted(cap, finish):
    proof = evidence(cap)
    response = json.loads(proof.response_wire)
    response['choices'][0].update(finish_reason=finish, message=dict(role='assistant', content='OK'))
    response['usage'].update(completion_tokens=1, total_tokens=11)
    with pytest.raises(ProviderRequestNotSent):
        replace(proof, response_wire=json.dumps(response))


def test_old_prompt_and_proof_schema_are_not_promoted(tmp_path):
    proof = evidence(4096)
    request = json.loads(proof.request_wire)
    request['messages'][0]['content'] = 'Reply with exactly OK.'
    with pytest.raises(ProviderRequestNotSent):
        replace(proof, request_wire=json.dumps(request))
    raw = json.dumps(dict(schema_version=1, receipts=[asdict(proof)])).encode()
    path = tmp_path / 'old-proof.json'
    path.write_bytes(raw)
    with pytest.raises(ProviderRequestNotSent):
        load_output_capabilities(path, hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize('mutation', ['silent_512_clamp', 'early_stop'])
def test_requested_4096_requires_observed_4096_length_termination(mutation):
    proof = evidence(4096)
    response = json.loads(proof.response_wire)
    if mutation == 'silent_512_clamp':
        response['usage'].update(completion_tokens=512, total_tokens=522)
        response['choices'][0]['message']['content'] = ' '.join(['capacity'] * 512)
    else:
        response['choices'][0]['finish_reason'] = 'stop'
    with pytest.raises(ProviderRequestNotSent):
        replace(proof, response_wire=json.dumps(response))


@pytest.mark.parametrize('cap,supported', [(1024, (512, 1024)), (2048, (512, 1024, 2048)),
                                         (4096, (512, 1024, 2048, 4096))])
@pytest.mark.parametrize('provider', ['deepseek', 'siliconflow'])
def test_reached_ceiling_admits_only_documented_lower_integer_caps(cap, supported, provider):
    proof = evidence(cap, provider)
    assert proof.supported_output_caps == supported
    basis = proof.lower_cap_inference_basis
    assert basis['observed_output_ceiling'] == cap
    assert basis['lower_caps_individually_observed'] is False
    assert basis['source'].startswith('https://api-docs.deepseek.com/' if provider == 'deepseek'
                                      else 'https://docs.siliconflow.cn/')


def test_probe_requires_active_matching_lifecycle_and_exact_single_synthetic_send():
    proof = evidence(4096)
    args = dict(provider=proof.provider, model=proof.model, protocol=proof.protocol,
                output_tokens=4096, run_id='run', attempt_id='attempt')
    with pytest.raises(ProviderRequestNotSent):
        with output_probe_admission(**args):
            pass
    claim = NS(run_id='run', owner='worker')
    tokens = [current_execution.set((claim, NS())), execution_owner.set(('run', 'worker')),
              current_attempt.set('attempt')]
    try:
        with output_probe_admission(**args):
            assert not consume_output_probe(proof.provider, proof.model, proof.protocol, 2048)
            assert consume_output_probe(proof.provider, proof.model, proof.protocol, 4096)
            check_output_probe_payload(proof.provider, proof.model, proof.protocol, 4096,
                                       [dict(role='user', content=SYNTHETIC_OUTPUT_PROBE)])
            with pytest.raises(ProviderRequestNotSent, match='SYNTHETIC'):
                check_output_probe_payload(proof.provider, proof.model, proof.protocol, 4096,
                                           [dict(role='user', content='private')])
            with pytest.raises(ProviderRequestNotSent, match='ALREADY_CONSUMED'):
                consume_output_probe(proof.provider, proof.model, proof.protocol, 4096)
            changed = current_attempt.set('different')
            try:
                assert not consume_output_probe(proof.provider, proof.model, proof.protocol, 4096)
            finally:
                current_attempt.reset(changed)
        assert not consume_output_probe(proof.provider, proof.model, proof.protocol, 4096)
    finally:
        current_attempt.reset(tokens[2])
        execution_owner.reset(tokens[1])
        current_execution.reset(tokens[0])
