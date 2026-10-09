"""One synthetic continuation only. Default does not load keys, DB or providers.

The execution flag is not authorization by itself: the coordinator must assign
an exact execution contract under the owner's existing approved scope, including
verified zero-CNY model pricing and UNKNOWN-account validation for this request.
No PostgreSQL retrieval fixture, second request, retry or historical refund.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
REQUEST_ID = 'p4-rerank-echo-r1'
PG_REQUEST_ID = 'p4-rerank-echo-pg-r1'
MODEL = 'BAAI/bge-reranker-v2-m3'
QUERY = 'Which synthetic document states cost 42.75?'
DOCUMENTS = ('Synthetic cost is 42.75.', 'Synthetic gardening note has no cost information.')
LEGACY_BODY = json.dumps(dict(model=MODEL, query=QUERY, documents=list(DOCUMENTS),
                       top_n=2, return_documents=False)).encode('utf-8')
LEGACY_BODY_SHA256 = '5b3c083dc324871e201ff270013015a99a3f08974ad43a68aeba9f0caf8fdbd0'
BODY = json.dumps(dict(model=MODEL, query=QUERY, documents=list(DOCUMENTS),
                       top_n=2, return_documents=True)).encode('utf-8')
BODY_BYTES = 226
BODY_SHA256 = '5d8d6e14e7da5719799d993d46e67a2b4c3e9997a0642a002b59fbc606a74378'
HISTORY = ROOT / 'var/reports/p4-r1/live/live-receipt.json'
HISTORY_SHA256 = 'e9ba3e3bfde543f9b18acd2a83ce1578aaa57828163df3bb4e79035ef0ac0147'
CONTINUATION = ROOT / 'var/reports/p4-rerank-once-r1/live-receipt.json'
CONTINUATION_SHA256 = '7e570a54d2cbddb723d1af11b2e938730a3c031bcbe68d76a1b0b2a207e22cfa'
DIAGNOSTIC = ROOT / 'var/reports/p4-rerank-diagnostic-r2/live-receipt.json'
DIAGNOSTIC_SHA256 = 'ccd9940db1c1e161a994454134faad2a9704e614b1e0dc27736ea062fa78b46a'
OUT = ROOT / 'var/reports' / REQUEST_ID
PG_OUT = ROOT / 'var/reports' / PG_REQUEST_ID
PG_QUERY = 'Synthetic cost'
PG_CORPUS = ''.join(f'Synthetic record {n:02}: cost 42.75, total 17.25.\n' for n in range(30))
PG_CORPUS_SHA256 = hashlib.sha256(PG_CORPUS.encode()).hexdigest()
TOTAL_BODY_LIMIT = 20_000
PRICING_SOURCE = 'https://siliconflow.cn/pricing'


def plan(pg_only=False):
    return dict(status='NOT_RUN', request_id=PG_REQUEST_ID if pg_only else REQUEST_ID, provider='siliconflow',
                model=MODEL, max_requests=1, request_body_bytes=None if pg_only else len(BODY),
                request_body_sha256=None if pg_only else BODY_SHA256, timeout_seconds=30, retries=0,
                max_cost_cny='0', return_documents=True, pricing_verification='REQUIRED', account_initial_state='UNKNOWN',
                real_postgres_pipeline='NOT_RUN', historical_usage='UNKNOWN',
                validation_stage='pg_only' if pg_only else 'synthetic',
                output_dir=str(PG_OUT if pg_only else OUT),
                historical_attempts=4 if pg_only else 3, historical_body_bytes=907 if pg_only else 681,
                total_body_bytes_limit=TOTAL_BODY_LIMIT)


def validate_approval(value, now=None, *, pg_only=False):
    expected = dict(approved=True, request_id=PG_REQUEST_ID if pg_only else REQUEST_ID, provider='siliconflow',
                    model=MODEL, request_body_bytes=BODY_BYTES, request_body_sha256=BODY_SHA256,
                    return_documents=True,
                    max_requests=1, timeout_seconds=30, retries=0, synthetic_only=True,
                    stop_after_success=True, stop_after_failure=True, currency='CNY',
                    max_cost='0', pricing_source=PRICING_SOURCE,
                    free_model_price_verified=True, owner_call_authorized=True,
                    account_initial_state='UNKNOWN', single_request_account_validation_authorized=True)
    extra = {'owner_reference', 'pricing_verified_at'}
    if pg_only:
        expected.pop('request_body_bytes')
        expected.pop('request_body_sha256')
        expected.update(validation_stage='pg_only', synthetic_corpus_sha256=PG_CORPUS_SHA256,
                        cumulative_body_bytes_limit=TOTAL_BODY_LIMIT)
        extra.add('successful_probe_sha256')
    if not isinstance(value, dict) or set(value) != set(expected) | extra:
        raise ValueError('EXACT_OWNER_APPROVAL_REQUIRED')
    if any(type(value[key]) is not type(wanted) or value[key] != wanted
           for key, wanted in expected.items()):
        raise ValueError('EXACT_OWNER_APPROVAL_REQUIRED')
    reference = value['owner_reference']
    if not isinstance(reference, str) or not reference.strip() or len(reference) > 200:
        raise ValueError('OWNER_REFERENCE_REQUIRED')
    try:
        verified = datetime.fromisoformat(value['pricing_verified_at'].replace('Z', '+00:00'))
        age = (now or datetime.now(timezone.utc)) - verified
    except (AttributeError, TypeError, ValueError):
        raise ValueError('CURRENT_ZERO_PRICE_VERIFICATION_REQUIRED') from None
    if verified.tzinfo is None or not timedelta(0) <= age <= timedelta(hours=24):
        raise ValueError('CURRENT_ZERO_PRICE_VERIFICATION_REQUIRED')
    if len(BODY) != BODY_BYTES or hashlib.sha256(BODY).hexdigest() != BODY_SHA256:
        raise ValueError('SYNTHETIC_BODY_IDENTITY_CHANGED')
    if pg_only and (not isinstance(value['successful_probe_sha256'], str)
                   or len(value['successful_probe_sha256']) != 64
                   or any(c not in '0123456789abcdef' for c in value['successful_probe_sha256'])):
        raise ValueError('SUCCESSFUL_PROBE_HASH_REQUIRED')


def read_history(path, continuation=CONTINUATION, diagnostic=DIAGNOSTIC):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != HISTORY_SHA256:
        raise ValueError('HISTORICAL_RECEIPT_CHANGED')
    value = json.loads(raw)
    attempts = value.get('attempts', [])
    if (len(attempts) != 1 or attempts[0].get('state') != 'consumed_before_transport'
            or attempts[0].get('request_body_bytes') != 227
            or attempts[0].get('request_body_sha256') != LEGACY_BODY_SHA256
            or value.get('settlement') != 'UNKNOWN'):
        raise ValueError('HISTORICAL_ATTEMPT_IDENTITY_CHANGED')
    raw_continuation = continuation.read_bytes()
    if hashlib.sha256(raw_continuation).hexdigest() != CONTINUATION_SHA256:
        raise ValueError('HISTORICAL_CONTINUATION_CHANGED')
    second = json.loads(raw_continuation)
    attempts2 = second.get('attempts', [])
    if (second.get('request_id') != 'p4-rerank-continuation-r1'
            or len(attempts2) != 1 or attempts2[0].get('state') != 'consumed_before_transport'
            or attempts2[0].get('request_body_bytes') != 227
            or attempts2[0].get('request_body_sha256') != LEGACY_BODY_SHA256
            or second.get('settlement') != 'UNKNOWN'
            or second.get('cumulative_attempts') != 2 or second.get('cumulative_body_bytes') != 454):
        raise ValueError('HISTORICAL_CONTINUATION_IDENTITY_CHANGED')
    raw_diagnostic = diagnostic.read_bytes()
    if hashlib.sha256(raw_diagnostic).hexdigest() != DIAGNOSTIC_SHA256:
        raise ValueError('HISTORICAL_DIAGNOSTIC_CHANGED')
    third = json.loads(raw_diagnostic)
    attempts3 = third.get('attempts', [])
    if (third.get('request_id') != 'p4-rerank-diagnostic-r2'
            or len(attempts3) != 1 or attempts3[0].get('state') != 'consumed_before_transport'
            or attempts3[0].get('request_body_bytes') != 227
            or attempts3[0].get('request_body_sha256') != LEGACY_BODY_SHA256
            or third.get('settlement') != 'UNKNOWN'
            or third.get('cumulative_attempts') != 3 or third.get('cumulative_body_bytes') != 681):
        raise ValueError('HISTORICAL_DIAGNOSTIC_IDENTITY_CHANGED')
    return dict(attempts=3, body_bytes=681, settlement='UNKNOWN', sha256=HISTORY_SHA256,
                continuation_sha256=CONTINUATION_SHA256,
                diagnostic_sha256=DIAGNOSTIC_SHA256,
                consumed_request_ids=['p4-r1', 'p4-rerank-continuation-r1', 'p4-rerank-diagnostic-r2'])


def read_successful_probe(path, approval, previous, *, allow_simulated=False):
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != approval['successful_probe_sha256']:
        raise ValueError('SUCCESSFUL_PROBE_CHANGED')
    probe = json.loads(raw)
    attempts = probe.get('attempts', [])
    if (probe.get('evidence_kind') not in ({'REAL', 'SIMULATED'} if allow_simulated else {'REAL'})
            or probe.get('request_id') != REQUEST_ID or probe.get('status') != 'PROBE_PASS'
            or probe.get('model') != MODEL or len(attempts) != 1
            or attempts[0].get('state') != 'consumed_before_transport'
            or attempts[0].get('request_body_bytes') != BODY_BYTES
            or attempts[0].get('request_body_sha256') != BODY_SHA256
            or probe.get('cumulative_attempts') != 4 or probe.get('cumulative_body_bytes') != 907
            or probe.get('settlement') != 'UNKNOWN'
            or probe.get('history') != previous
            or len(probe.get('receipts', [])) != 1
            or probe['receipts'][0].get('status') != 'ok'):
        raise ValueError('SUCCESSFUL_PROBE_REQUIRED')
    return dict(previous, attempts=4, body_bytes=907, successful_probe_sha256=digest,
                consumed_request_ids=previous['consumed_request_ids'] + [REQUEST_ID])


class OnceTransport:
    """A stricter wrapper around the existing P4 AuthorizedTransport."""
    def __init__(self, record, save, send, approved_documents=DOCUMENTS, *, pg_only=False):
        from scripts.verify_p4_rag import AuthorizedTransport
        self.record, self.lock = record, threading.Lock()
        self.attempted = False
        self.pg_only = pg_only
        self.authorized = AuthorizedTransport(record, save, send, frozenset(approved_documents))

    def __call__(self, request, timeout):
        from backend.app.ports.providers import ProviderRequestNotSent
        with self.lock:
            if self.attempted or self.record['attempts']:
                raise ProviderRequestNotSent('CONTINUATION_ALREADY_CONSUMED')
            if timeout != 30 or (not self.pg_only and request.data != BODY):
                raise ProviderRequestNotSent('CONTINUATION_EXACT_BODY_REQUIRED')
            if self.pg_only:
                payload = json.loads(request.data)
                if (set(payload) != {'model', 'query', 'documents', 'top_n', 'return_documents'}
                        or payload['model'] != MODEL or payload['query'] != PG_QUERY
                        or not isinstance(payload['documents'], list) or not payload['documents']
                        or type(payload['top_n']) is not int or payload['top_n'] != len(payload['documents'])
                        or payload['return_documents'] is not True):
                    raise ProviderRequestNotSent('PG_ONLY_EXACT_PAYLOAD_REQUIRED')
            if self.record.get('history', {}).get('body_bytes', 0) + len(request.data) > TOTAL_BODY_LIMIT:
                raise ProviderRequestNotSent('CUMULATIVE_BODY_LIMIT_EXCEEDED')
            # Existing wrapper verifies exact endpoint/model and persists the
            # consumed attempt before uncertain I/O. Never retry a save/send.
            self.attempted = True
            return self.authorized(request, timeout)


@contextmanager
def live_container(request_id=REQUEST_ID, output=OUT, *, retrieval_mode='keyword', embedding_admission=None):
    """Only entered after exact approval; reuse normal composition and ledger."""
    from sqlalchemy import create_engine
    from backend.app.application.budget import PostgresBudgetGate
    from backend.app.application.provider_usage import BudgetUsageGuard
    from backend.app.bootstrap import build_container
    from backend.app.config import Settings
    from backend.app.domain.model_registry import ModelRegistry
    from backend.app.adapters.models.factory import ProviderFactory
    from scripts.with_clean_slate import resolve_environment, verify_database

    profile = json.loads((ROOT / 'deploy/clean-slate/profile.json').read_text(encoding='utf-8'))
    env = resolve_environment(profile, os.environ.copy())
    identity = verify_database(profile, env)
    key = env.get('SILICONFLOW_API_KEY', '').strip()
    if not key:
        raise ValueError('MODEL_CREDENTIAL_MISSING')
    old = os.environ.get('SILICONFLOW_API_KEY')
    engine = container = None
    try:
        os.environ['SILICONFLOW_API_KEY'] = key
        engine = create_engine(env['RAG_DATABASE_URL'], hide_parameters=True,
                               connect_args={'connect_timeout': 3,
                                             'application_name': request_id})
        registry = ModelRegistry.frozen_defaults()
        spec = registry.select('rerank')
        # Same existing P4 cap and conservative reservation, never an increase.
        guard = BudgetUsageGuard(PostgresBudgetGate(engine, 10), spec, 1)
        guards = {'rerank': guard}
        embedding = None
        if retrieval_mode == 'hybrid':
            if embedding_admission is None:
                raise ValueError('HYBRID_VERIFIED_ADMISSION_REQUIRED')
            guards['embedding'] = BudgetUsageGuard(PostgresBudgetGate(engine, 10), registry.select('embedding'), 1)
            embedding = ProviderFactory(registry, enabled_roles=['embedding'], usage_guards=guards,
                                        embedding_admission=embedding_admission).build('embedding')
        elif retrieval_mode != 'keyword':
            raise ValueError('VALIDATION_RETRIEVAL_MODE_INVALID')
        settings = Settings(database_url=env['RAG_DATABASE_URL'], storage_root=output/'runtime',
                            cloud_enabled=True, rerank_egress_enabled=True, rerank_enabled=True,
                            retrieval_mode=retrieval_mode, embedding_egress_enabled=embedding is not None,
                            local_answer_enabled=False,
                            inline_ingestion_enabled=False)
        container = build_container(settings, model=None, agent_model=None, cloud_model=None,
                                    embedding_provider=embedding, embedding_admission=embedding_admission,
                                    model_usage_guards=guards)
        ranker = container.knowledge_gateway.retriever.ranker
        ranker.max_requests = 1
        yield container, identity
    finally:
        if container is not None:
            container.engine.dispose()
        if engine is not None:
            engine.dispose()
        if old is None:
            os.environ.pop('SILICONFLOW_API_KEY', None)
        else:
            os.environ['SILICONFLOW_API_KEY'] = old


@contextmanager
def live_ranker():
    with live_container() as (container, identity):
        yield container.knowledge_gateway.retriever.ranker, identity


class ReceiptPersistenceFailure(OSError):
    """Fixed, non-secret error; retain the first failure and consumed identity."""
    error_code = 'RECEIPT_PERSISTENCE_FAILED'

    def __init__(self, stage, operation, original_error_type):
        super().__init__(self.error_code)
        self.stage, self.operation = stage, operation
        self.original_error_type = original_error_type
        self.record = None


def run_once(approval, *, output=None, request_id=None, pg_only=False):
    """Real core entry: no alternate history, factory or receipt test seams."""
    validate_approval(approval, pg_only=pg_only)
    expected_id, expected_out = (PG_REQUEST_ID, PG_OUT) if pg_only else (REQUEST_ID, OUT)
    output = Path(output) if output is not None else expected_out
    if ((request_id if request_id is not None else approval['request_id']) != expected_id
            or output.resolve() != expected_out.resolve()):
        raise ValueError('REGISTERED_REQUEST_OUTPUT_REQUIRED')
    if pg_only:
        from scripts.verify_p4_rag import pg_only_factory
        factory = lambda: pg_only_factory(output)
    else:
        factory = live_ranker
    return _execute_once(approval, output=output, history=HISTORY, continuation=CONTINUATION,
                         factory=factory, pg_only=pg_only, probe_receipt=OUT/'live-receipt.json',
                         evidence_kind='REAL')


def run_simulated_once(approval, *, output, factory, history=HISTORY,
                       continuation=CONTINUATION, pg_only=False, probe_receipt=None):
    """Explicit SIMULATED seam; requires a fake factory, never selects live I/O.

    Execute only under the existing offline runner's HTTP/DB/secret guards.
    Python callbacks are test code, not a sandbox or an authorization boundary.
    """
    if factory is None or factory is live_ranker:
        raise ValueError('SIMULATED_FACTORY_REQUIRED')
    output = Path(output)
    if output.resolve() in {OUT.resolve(), PG_OUT.resolve()}:
        raise ValueError('SIMULATED_OUTPUT_MUST_NOT_CONSUME_REAL_IDENTITY')
    return _execute_once(approval, output=output, history=history, continuation=continuation,
                         factory=factory, pg_only=pg_only, probe_receipt=probe_receipt,
                         evidence_kind='SIMULATED')


def _execute_once(approval, *, output, history, continuation, factory,
                  pg_only, probe_receipt, evidence_kind):
    # No factory/key/DB access before all admission and exclusive identity checks.
    validate_approval(approval, pg_only=pg_only)
    previous = read_history(history, continuation)
    if pg_only:
        probe_receipt = probe_receipt or OUT/'live-receipt.json'
        previous = read_successful_probe(probe_receipt, approval, previous,
                                         allow_simulated=evidence_kind == 'SIMULATED')
    output.mkdir(parents=True, exist_ok=False)
    receipt = output / 'live-receipt.json'
    record = dict(plan(pg_only), status='STARTED', attempts=[], history=previous,
                  approval_sha256=hashlib.sha256(json.dumps(approval, sort_keys=True).encode()).hexdigest(),
                  authorization={key: approval[key] for key in (
                      'free_model_price_verified', 'owner_call_authorized',
                      'account_initial_state', 'single_request_account_validation_authorized')},
                  settlement='UNKNOWN', actual_cost='UNKNOWN', evidence_kind=evidence_kind)
    if pg_only:
        record.update(request_body_bytes=None, request_body_sha256=None,
                      synthetic_corpus_sha256=PG_CORPUS_SHA256,
                      successful_probe_sha256=approval['successful_probe_sha256'])
    with receipt.open('x', encoding='utf-8') as stream:
        json.dump(record, stream, indent=2)

    persistence_failure = None

    def save(stage):
        nonlocal persistence_failure
        record['cumulative_attempts'] = previous['attempts'] + len(record['attempts'])
        record['cumulative_body_bytes'] = previous['body_bytes'] + sum(
            item['request_body_bytes'] for item in record['attempts'])
        temporary = receipt.with_suffix('.tmp')
        operation = 'write'
        try:
            with temporary.open('x', encoding='utf-8') as stream:
                json.dump(record, stream, indent=2)
                stream.flush()
                operation = 'fsync'
                os.fsync(stream.fileno())
            operation = 'replace'
            os.replace(temporary, receipt)
        except Exception as exc:
            persistence_failure = ReceiptPersistenceFailure(stage, operation, type(exc).__name__)
            raise persistence_failure from exc

    ranker = None
    try:
        with factory() as context:
            ranker, identity = context[:2]
            if evidence_kind == 'SIMULATED' and identity.get('verification') != 'SIMULATED_NO_DB':
                raise ValueError('SIMULATED_FACTORY_IDENTITY_REQUIRED')
            record['database_identity'] = identity
            ranker.transport = OnceTransport(record, lambda: save('before_transport'), ranker.transport,
                context[3] if pg_only else DOCUMENTS, pg_only=pg_only)
            if pg_only:
                context[2](record)
                if len(record['attempts']) != 1 or ranker.last_result is None or len(ranker.receipts) != 1:
                    raise ValueError('PG_ONLY_EXACTLY_ONE_CALL_REQUIRED')
                record.update(status='PG_ONLY_PASS', real_postgres_pipeline='PASS',
                              returned_count=len(ranker.last_result.hits),
                              scores=ranker.last_result.relevance_scores,
                              usage_actual=ranker.last_result.usage_actual)
            else:
                _run_probe(ranker, record)
        read_history(history, continuation)
        if pg_only:
            read_successful_probe(probe_receipt, approval, read_history(history, continuation),
                                  allow_simulated=evidence_kind == 'SIMULATED')
    except Exception as exc:
        record.update(status='BLOCKED', error_type=type(exc).__name__)
    finally:
        if ranker is not None:
            record['receipts'] = ranker.receipts
        if persistence_failure is None:
            try:
                save('final_receipt')
            except ReceiptPersistenceFailure:
                pass  # No retry, alternate file, refund or second send.
        if persistence_failure is not None:
            record['outcome_before_persistence_failure'] = dict(
                status=record['status'], error_type=record.get('error_type'),
                receipt_error_codes=[item.get('error_code') for item in record.get('receipts', [])])
            record.update(status='BLOCKED', error_type='ReceiptPersistenceFailure',
                          error_code=persistence_failure.error_code,
                          persistence_stage=persistence_failure.stage,
                          persistence_operation=persistence_failure.operation)
            persistence_failure.record = record
            raise persistence_failure
    return 0 if record['status'] in {'PROBE_PASS', 'PG_ONLY_PASS'} else 1


def _run_probe(ranker, record):
    from backend.app.domain.models import ChunkRecord, RankedHit
    from backend.app.ports.model_access import model_access
    chunks = [ChunkRecord('probe-'+str(n), 'synthetic-kb', 'synthetic-doc',
                          'synthetic-version', body) for n, body in enumerate(DOCUMENTS)]
    hits = [RankedHit(chunk_id=chunk.chunk_id, rank=n) for n, chunk in enumerate(chunks, 1)]
    with model_access('rerank', allowed=True):
        ranker.rank(QUERY, hits, {chunk.chunk_id: chunk for chunk in chunks})
    if (len(record['attempts']) != 1 or ranker.last_result is None
            or ranker.last_result.hits[0].chunk_id != 'probe-0'):
        raise ValueError('SYNTHETIC_EXPECTED_TOP_MISMATCH')
    record.update(status='PROBE_PASS', returned_count=len(ranker.last_result.hits),
                  scores=ranker.last_result.relevance_scores, usage_actual=ranker.last_result.usage_actual)


def main(argv=None, *, pg_only=False):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-owner-authorized-once', action='store_true')
    parser.add_argument('--authorization-file', type=Path)
    parser.add_argument('--request-id', default=PG_REQUEST_ID if pg_only else REQUEST_ID)
    parser.add_argument('--output-dir', type=Path, default=PG_OUT if pg_only else OUT)
    args = parser.parse_args(argv)
    if not args.execute_owner_authorized_once:
        print(json.dumps(plan(pg_only)))
        return 0
    try:
        if args.authorization_file is None:
            raise ValueError('EXACT_OWNER_APPROVAL_REQUIRED')
        approval = json.loads(args.authorization_file.read_text(encoding='utf-8-sig'))
        expected_id, expected_out = (PG_REQUEST_ID, PG_OUT) if pg_only else (REQUEST_ID, OUT)
        if args.request_id != expected_id or args.output_dir.resolve() != expected_out.resolve():
            raise ValueError('REGISTERED_REQUEST_OUTPUT_REQUIRED')
        code = run_once(approval, output=args.output_dir, request_id=args.request_id, pg_only=pg_only)
    except Exception as exc:
        # Never export messages/tracebacks, DSNs, keys or provider payloads.
        result = dict(status='BLOCKED', error_type=type(exc).__name__)
        if isinstance(exc, ReceiptPersistenceFailure):
            result.update(error_code=exc.error_code, persistence_stage=exc.stage,
                          persistence_operation=exc.operation)
        print(json.dumps(result))
        return 1
    print(json.dumps(dict(exit_code=code, receipt=str((PG_OUT if pg_only else OUT)/'live-receipt.json'))))
    return code


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
