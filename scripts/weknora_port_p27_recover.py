"""Explicit recovery of the identified 2026-10 DeepSeek cap-field incident.

This operation sends nothing, grants no permissions and changes no call row or
quota. It only acknowledges a repaired cause of a month block, with immutable
before/precommit/committed receipts. A crash is reconciled against those exact
snapshots; a different incident or concurrent ledger change refuses recovery.
"""
import argparse
import asyncio
from datetime import date
import hashlib
from pathlib import Path

from scripts import weknora_port_p22_fence as evidence
from backend.weknora_port.storage.token_budget import encode

KEY = ('deepseek-owner-main', 'deepseek', date(2026, 10, 1))
WIRE = '59e5ce47d546fd2f03b671204fc576a733d5949d6e71a165006b87592b271f4f'
CALLS = '95852dfccace40e748fc1503847bc402cb237353fe96b98d7cb22362fc4f9e30'
REASON = 'USAGE_EXCEEDS_RESERVATION'
ROOT = Path('E:/WK/run/p27-gate-20261017/recovery')


def check(value, code):
    if not value:
        raise RuntimeError('P27_' + code)


def decision(before):
    """Only the pinned incident is eligible; UNKNOWN is not reconciled away."""
    check(evidence.digest(before['calls']) == CALLS, 'HISTORICAL_LEDGER_CHANGED')
    row = next((r for r in before['calls'] if r['request_id'] == 'real/answer/1/cheap'), {})
    check(row.get('wire_sha256') == WIRE and row.get('wire_bytes') == 195,
          'INCIDENT_WIRE_MISMATCH')
    check((row.get('state'), row.get('held_tokens'), row.get('actual_total'),
           row.get('reserved_output'), row.get('known_lower_bound')) ==
          ('unknown', 259, None, 64, 221), 'INCIDENT_ACCOUNTING_CHANGED')
    check(before['month']['blocked_reason'] == REASON, 'BLOCK_REASON_CHANGED')
    after = evidence.plain(before)
    after['month']['blocked_reason'] = ''
    return after


def repaired_contract():
    from backend.weknora_port.models import provider_config, build_chat_body, ChatOptions, Message
    from backend.weknora_port.models.output_contract import resolve_output_contract
    from backend.weknora_port.models.types import ProviderError
    config = provider_config('deepseek', 'deepseek-flash')
    body = build_chat_body(config, (Message('user', 'Offline contract check'),), ChatOptions(max_tokens=64))
    check(body.get('max_tokens') == 64 and 'max_completion_tokens' not in body, 'CAP_REPAIR_MISSING')
    contract = resolve_output_contract(config, body)
    check(contract.output_upper == 64 and contract.reasoning_included, 'CAP_REPAIR_INVALID')
    body['max_completion_tokens'] = body.pop('max_tokens')
    try:
        resolve_output_contract(config, body)
    except ProviderError as exc:
        check(exc.code == 'MODEL_OUTPUT_HARD_LIMIT_DENIED', 'UNEXPECTED_CONTRACT_DENIAL')
    else:
        raise RuntimeError('P27_LEGACY_WIRE_NOT_DENIED')
    return dict(correct_cap='max_tokens', output_upper=64, legacy_wire='DENIED')


async def snapshot(conn):
    month = await (await conn.execute('SELECT * FROM port_token_budget_months '
        'WHERE account_id=%s AND provider=%s AND month=%s FOR UPDATE', KEY)).fetchone()
    calls = await (await conn.execute('SELECT * FROM port_token_calls '
        'WHERE account_id=%s ORDER BY call_id', (KEY[0],))).fetchall()
    slots = await (await conn.execute('SELECT * FROM port_call_slots WHERE call_id '
        'IN (SELECT call_id FROM port_token_calls WHERE account_id=%s) ORDER BY call_id',
        (KEY[0],))).fetchall()
    check(month is not None, 'MONTH_MISSING')
    return evidence.plain(dict(month=month, calls=calls, slots=slots))


async def recover(conn, approval_text, *, root=ROOT):
    check(bool(approval_text.strip()), 'OWNER_DECISION_REQUIRED')
    proof = repaired_contract()
    root.mkdir(parents=True, exist_ok=True)
    await evidence._locked(conn)
    async with conn.transaction():
        identity = await (await conn.execute('SELECT system_identifier::text AS system_identifier, '
            'current_database() AS database FROM pg_control_system()')).fetchone()
        check(identity == dict(system_identifier=evidence.SYSTEM_ID, database=evidence.DATABASE),
              'PG_IDENTITY_MISMATCH')
        await conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',
            ('weknora-token-month:' + encode([str(v) for v in KEY]),))
        current = await snapshot(conn)
        intent = evidence._read(root, 'intent')
        committed = evidence._read(root, 'committed')
        if committed is not None:
            check(intent is not None and committed.get('commit_confirmed') is True
                  and committed.get('intent_sha256') == evidence.digest(intent), 'COMMITTED_RECEIPT_INVALID')
            check(current == intent['after'], 'RECOVERY_ALREADY_COMPLETED_STATE_CHANGED')
        if intent is None:
            intent = evidence._write(root, 'intent', dict(
                schema='p27-deepseek-cap-incident-recovery/v1',
                approval_text=approval_text,
                approval_sha256=hashlib.sha256(approval_text.encode()).hexdigest(),
                repaired_contract=proof,
                diagnosis='Wire used max_completion_tokens, not the DeepSeek max_tokens cap. '
                          'Contract validation fixed in 561e3bc8a5a6d13a7199c48f35daf40be3cd04e1.',
                effect='Clear only this repaired incident block. Preserve UNKNOWN, 259 held, '
                       'all call/slot rows and every other month field. No new spending grant.',
                before=current, after=decision(current)))
        check(intent['approval_text'] == approval_text, 'OWNER_DECISION_CHANGED')
        check(decision(intent['before']) == intent['after'], 'RECOVERY_RECEIPT_INVALID')
        if current == intent['before']:
            await conn.execute('UPDATE port_token_budget_months SET blocked_reason=%s '
                'WHERE account_id=%s AND provider=%s AND month=%s AND blocked_reason=%s',
                ('', *KEY, REASON))
        else:
            check(current == intent['after'], 'RECOVERY_STATE_CONFLICT')
        after = await snapshot(conn)
        check(after == intent['after'], 'UNEXPECTED_MUTATION')
        receipt = dict(intent_sha256=evidence.digest(intent), after=after,
                       calls_sha256=evidence.digest(after['calls']),
                       slots_sha256=evidence.digest(after['slots']))
        evidence._write(root, 'precommit', receipt)
    # Re-read after durable commit; never claim success from an inner savepoint.
    async with conn.transaction():
        check(await snapshot(conn) == intent['after'], 'POSTCOMMIT_STATE_CONFLICT')
    evidence._write(root, 'committed', dict(receipt, commit_confirmed=True))
    return dict(status='RECOVERED', historical_unknown_preserved=True, held_tokens=259,
                real_provider_calls=0, receipt=str(root / 'committed.json'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approval-file', type=Path, required=True)
    args = parser.parse_args()

    async def run():
        from scripts.weknora_port_mvp_real import connection
        conn = await connection()
        try:
            return await recover(conn, args.approval_file.read_text(encoding='utf-8-sig'))
        finally:
            await conn.close()

    try:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            print(encode(runner.run(run())))
    except Exception as exc:
        # Never print DSNs or arbitrary driver errors.
        print(encode(dict(status='FAIL', code=str(exc) if str(exc).startswith('P27_')
                          else 'P27_RECOVERY_FAILED_' + type(exc).__name__)))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
