"""Real isolated PostgreSQL quota checks with SIMULATED policies, zero sends."""
import json
import multiprocessing
import os
from dataclasses import replace

from sqlalchemy import create_engine

from backend.app.adapters.postgres.service_usage import PostgresServiceTokenLedger, ServiceQuotaDenied
from backend.app.ports.service_policy import ServiceModelPolicy


def policy():
    return ServiceModelPolicy('SIMULATED-PG-concurrency', 'SIMULATED isolated fixture',
        'deepseek', 'deepseek-flash', ('chat_expensive', 'vision'), 100, 1, 1)


def reserve(ledger, identity, role='chat_expensive'):
    return ledger.reserve(policy(), request_id=identity, role=role,
        scope_sha256='a'*64, wire_sha256='b'*64, input_tokens=30, output_tokens=10)


def attempt(number, gate, queue):
    engine = create_engine(os.environ['RAG_DATABASE_URL'])
    try:
        gate.wait(timeout=20)
        identity = reserve(PostgresServiceTokenLedger(engine), str(number),
            'vision' if number % 2 else 'chat_expensive')
        queue.put(('reserved', identity))
    except ServiceQuotaDenied as error:
        queue.put(('denied', str(error)))
    except Exception as error:
        queue.put(('failed', type(error).__name__))
    finally:
        engine.dispose()


def denied(action, reason):
    try:
        action()
    except ServiceQuotaDenied as error:
        assert reason in str(error)
    else:
        raise AssertionError('EXPECTED_DENIAL_MISSING')


def main():
    context = multiprocessing.get_context('spawn')
    gate, queue = context.Event(), context.Queue()
    processes = [context.Process(target=attempt, args=(n, gate, queue)) for n in range(6)]
    for process in processes:
        process.start()
    gate.set()
    results = [queue.get(timeout=30) for _ in processes]
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    assert sum(state == 'reserved' for state, _ in results) == 2
    assert sum(state == 'denied' and 'SERVICE_TOKEN_LIMIT_EXCEEDED' in value
        for state, value in results) == 4
    engine = create_engine(os.environ['RAG_DATABASE_URL'])
    ledger = PostgresServiceTokenLedger(engine)
    reservations = [identity for state, identity in results if state == 'reserved']
    first, waiting = reservations
    assert ledger.finish(first, None, sent=True) == 'unknown'
    restarted = PostgresServiceTokenLedger(create_engine(os.environ['RAG_DATABASE_URL']))
    assert restarted.finish(first, None, sent=False) == 'unknown'
    denied(lambda: reserve(restarted, 'restart'), 'SERVICE_TOKEN_LIMIT_EXCEEDED')
    denied(lambda: restarted.reserve(replace(policy(), token_limit=101), request_id='enlarge',
        role='vision', scope_sha256='a'*64, wire_sha256='b'*64, input_tokens=1, output_tokens=0),
        'NEW_AUTHORIZATION')
    ledger.link_cost(waiting, 'SIMULATED-cost')
    denied(lambda: ledger.finish(waiting,
        {'prompt_tokens':35, 'completion_tokens':10, 'total_tokens':45}, sent=True),
        'EXCEEDED_RESERVATION')
    denied(lambda: reserve(restarted, 'after-breach', 'vision'), 'SERVICE_POLICY_SUSPENDED')
    denied(lambda: ledger.check_before_send(policy(), waiting), 'SERVICE_DISPATCH_FENCE_DENIED')
    restarted.engine.dispose()
    engine.dispose()
    print(json.dumps(dict(status='CHECKS_PASSED', check_kind='REAL_ISOLATED_POSTGRESQL',
        policy_kind='SIMULATED', processes=6, admitted=2, denied=4,
        unknown_restart_preserved=True, enlargement_denied=True, breach_suspended=True,
        provider_calls=0)))


if __name__ == '__main__':
    main()
