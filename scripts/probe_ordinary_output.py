"""Explicit owner-approved public probes; existing lifecycle and ledgers remain authoritative.

Run one role/cap at a time inside the configured service container. Never retries.
Only synthetic prompt/response bytes go into the local public capability artifact.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import uuid

from sqlalchemy import text
from backend.app.config import Settings
from backend.app.bootstrap import build_container
from backend.app.adapters.models.user_settings import apply_config
from backend.app.adapters.models.cloud import CloudChat
from backend.app.domain.scope import Scope
from backend.app.ports.model_access import model_access
from backend.app.ports.run_lifecycle import current_attempt
from backend.app.ports.session_attempts import request_identity
from backend.app.application.run_metrics import collect_metrics
from backend.app.ports.output_capability import (
    output_probe_admission, SYNTHETIC_OUTPUT_PROBE, PROTOCOLS, OutputCapabilityEvidence,
    OUTPUT_PROBE_PROMPT_VERSION,
)

SESSION = '313909f3-34d9-4f26-8778-3d5bd27f2c60'
KB = 'd78f3434-0e8f-40bf-afcb-f1c2c311ff51'
DOC = '1fd5a928-d3d7-4bae-aa00-fc74701f61e5'
ACKNOWLEDGED_UNKNOWN = '046c3327-5dd9-4d44-a6e6-876897da41ab'
FOLLOWUP_UNKNOWN = '72917fff-bd37-4607-b3db-7c7e402d3fbf'
REPAIRED_TRANSPORT_UNKNOWN = '33a016bc-41d1-4340-a072-5dd50b594af1'
OWNER_BATCH_ROOT = Path('/data/storage/weknora-probes-20261012')
PROMPT = SYNTHETIC_OUTPUT_PROBE


def failure_state(error, known_response, provider_receipt=None):
    from backend.app.ports.providers import ProviderRequestNotSent
    if known_response is not None:
        return 'NOT_PROVEN' if provider_receipt and provider_receipt.get('token_settlement') == 'settled' else 'UNKNOWN'
    return 'NOT_SENT' if isinstance(error, ProviderRequestNotSent) else 'UNKNOWN'


def save(path, value):
    temp = path.with_suffix('.tmp')
    with temp.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def prior_allows_continuation(row, acknowledged_unknown=False, acknowledge_followup=False,
                             acknowledge_repaired_transport=False):
    if row['state'] in {'VERIFIED', 'NOT_SENT'}:
        return True
    if row['state'] == 'NOT_PROVEN':
        return row.get('provider_diagnostic', {}).get('token_settlement') == 'settled'
    return bool(row['state'] == 'UNKNOWN' and (
        acknowledged_unknown and row.get('run_id') == ACKNOWLEDGED_UNKNOWN or
        acknowledge_followup and row.get('run_id') == FOLLOWUP_UNKNOWN or
        acknowledge_repaired_transport and row.get('run_id') == REPAIRED_TRANSPORT_UNKNOWN))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', choices=['chat_cheap', 'chat_expensive'], required=True)
    parser.add_argument('--cap', type=int, choices=[1024, 2048, 4096], required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--acknowledge-unknown', choices=[ACKNOWLEDGED_UNKNOWN])
    # Explicit operator opt-in only after a fresh owner decision. This does not
    # release/retry either acknowledged identity or increase the seven-call cap.
    parser.add_argument('--acknowledge-followup-unknown', choices=[FOLLOWUP_UNKNOWN])
    parser.add_argument('--acknowledge-repaired-transport-unknown', choices=[REPAIRED_TRANSPORT_UNKNOWN])
    parser.add_argument('--new-probe-label', choices=['owner-followup-1', 'owner-followup-2'])
    args = parser.parse_args()
    if args.root.resolve() != OWNER_BATCH_ROOT:
        raise RuntimeError('OWNER_BATCH_ROOT_REQUIRED')
    if args.new_probe_label and (not args.acknowledge_followup_unknown or
            (args.role, args.cap) != ('chat_expensive', 4096)):
        raise RuntimeError('FRESH_FOLLOWUP_PROBE_REQUIRED')
    if args.new_probe_label == 'owner-followup-2' and not args.acknowledge_repaired_transport_unknown:
        raise RuntimeError('FRESH_POST_REPAIR_OWNER_DECISION_REQUIRED')
    args.root.mkdir(parents=True, exist_ok=True)
    suffix = '-' + args.new_probe_label if args.new_probe_label else ''
    receipt = args.root / f'probe-v{OUTPUT_PROBE_PROMPT_VERSION}-{args.role}-{args.cap}{suffix}.json'
    if receipt.exists():
        raise RuntimeError('EXISTING_PROBE_RECEIPT_NO_RESEND')
    prior = [json.loads(p.read_text()) for p in args.root.glob('probe-*.json')]
    limit = 7 if args.acknowledge_unknown else 6
    if len(prior) >= limit or any(not prior_allows_continuation(p, args.acknowledge_unknown,
            args.acknowledge_followup_unknown, args.acknowledge_repaired_transport_unknown)
                                 for p in prior):
        raise RuntimeError('PROBE_BATCH_STOP_REQUIRED')
    c = build_container(apply_config(Settings.from_env()))
    conv = c.store.get_conversation(SESSION)
    assert conv['knowledge_base_scope'] == [KB] and conv['document_scope'] == [DOC]
    assert c.settings.cloud_enabled and c.store.get_knowledge_base(KB)['cloud_allowed']
    role = c.quick_chain.generation_roles[args.role]
    chat = role.gateway
    cap = role.service_binding.capacity
    scope = Scope.from_ids([KB], [DOC])
    messages = [dict(role='user', content=PROMPT)]
    body = cap.render_chat(messages, args.cap)
    wire = json.dumps(body).encode()
    input_bound, output_bound = cap.measure(body, wire)
    occupied = sum(p['input_reserved'] + p['output_reserved'] for p in prior)
    if occupied + input_bound + output_bound > 75000:
        raise RuntimeError('OWNER_BATCH_TOKEN_LIMIT')
    run = str(uuid.uuid4())
    attempt = request_identity(run, 'quick.router.' + args.role, 1)
    row = dict(state='PREPARED', role=args.role, provider=role.provider, model=role.model,
               run_id=run, attempt_id=attempt, cap=args.cap, input_reserved=input_bound,
               output_reserved=output_bound, wire_sha256=hashlib.sha256(wire).hexdigest())
    save(receipt, row)
    captured = {}
    def validate(response):
        captured['response'] = response
        return CloudChat._validate(response)
    chat._validate = validate
    claim = c.lifecycle.repository.claim_run(SESSION, [KB], [DOC], PROMPT, 'quick', run)
    assert claim.created
    with c.lifecycle.executing(claim, SESSION), collect_metrics(run):
        c.lifecycle.repository.claim_attempt(run_id=run, owner=claim.owner, attempt_id=attempt,
            role=args.role, ordinal=1, envelope_hash=row['wire_sha256'],
            provider=role.provider, model=role.model)
        token = current_attempt.set(attempt)
        try:
            row['state'] = 'DISPATCH_PENDING'
            save(receipt, row)
            with output_probe_admission(provider=role.provider, model=role.model,
                    protocol=PROTOCOLS[(role.provider, role.model)][0], output_tokens=args.cap, run_id=run, attempt_id=attempt), \
                 model_access(args.role, allowed=True, scope=scope):
                try:
                    chat.generate_with_service_scope(messages, timeout_seconds=90,
                        max_tokens=args.cap, scope=scope, request_id=attempt)
                except Exception:
                    # Known length is an accepted wire protocol, never a successful RAG answer.
                    from backend.app.ports.providers import TruncatedAnswer
                    import sys
                    if not isinstance(sys.exc_info()[1], TruncatedAnswer):
                        raise
            response = captured['response']
            usage = response['usage']
            assert response['model'] == role.model
            assert 0 < usage['completion_tokens'] <= args.cap
            assert 0 < usage['prompt_tokens'] <= input_bound
            assert response['choices'][0]['finish_reason'] in {'stop', 'length'}
            with c.engine.connect() as con:
                saved = con.execute(text('SELECT state,wire_sha256,actual_input,actual_output '
                    'FROM service_token_reservations WHERE request_id=:id'), {'id': attempt}).mappings().one()
                assert saved['state'] == 'settled' and saved['wire_sha256'] == row['wire_sha256']
            proof = OutputCapabilityEvidence(provider=role.provider, model=role.model,
                protocol=PROTOCOLS[(role.provider, role.model)][0],
                endpoint=chat.spec.base_url + '/chat/completions', output_tokens=args.cap,
                request_wire=wire.decode(), response_wire=json.dumps(response),
                run_id=run, attempt_id=attempt)
            row.update(state='VERIFIED', request_wire=wire.decode(), response_wire=json.dumps(response),
                       endpoint=chat.spec.base_url + '/chat/completions', protocol=PROTOCOLS[(role.provider, role.model)][0],
                       usage=usage, finish_reason=response['choices'][0]['finish_reason'])
            save(receipt, row)
            c.lifecycle.repository.finish_attempt(run, claim.owner, attempt, 'COMPLETED',
                dict(execution_mode='capacity_probe', output_cap=args.cap, finish_reason=row['finish_reason']))
        except Exception as exc:
            # A proof-validation denial after receiving a response is not an unsent call.
            known_response = captured.get('response')
            row.update(state=failure_state(exc, known_response, chat.receipts[-1] if chat.receipts else None),
                       error_class=type(exc).__name__)
            # Retain fixed adapter codes without exception text, credentials or private payloads.
            if chat.receipts:
                latest = chat.receipts[-1]
                row['provider_diagnostic'] = {k: latest.get(k) for k in
                    ('status', 'diagnostic_stage', 'error_code', 'settlement', 'token_settlement')}
            if known_response is not None and row['state'] == 'NOT_PROVEN':
                # This fixture is public; retain its known wire without altering older receipts.
                row.update(request_wire=wire.decode(), response_wire=json.dumps(known_response),
                           usage=known_response.get('usage'),
                           endpoint=chat.spec.base_url + '/chat/completions',
                           protocol=PROTOCOLS[(role.provider, role.model)][0])
            save(receipt, row)
            c.lifecycle.repository.finish_attempt(run, claim.owner, attempt,
                'NOT_SENT' if row['state'] == 'NOT_SENT' else
                'COMPLETED' if row['state'] == 'NOT_PROVEN' else 'UNKNOWN',
                dict(execution_mode='capacity_probe', error_class=row['error_class']))
            raise RuntimeError('PROBE_STOP_READ_RECEIPT') from None
        finally:
            current_attempt.reset(token)
            # No assistant/message/evidence row. The probe is not a RAG answer.
            c.lifecycle.repository.fail_run(run, claim.owner)
    print(json.dumps({k: row[k] for k in ('state', 'role', 'cap', 'run_id', 'usage', 'finish_reason')}))


if __name__ == '__main__':
    main()
