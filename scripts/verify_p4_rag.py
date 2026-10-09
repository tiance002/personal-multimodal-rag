"""Explicit P4 synthetic-only Rerank validation, at most 3 sends / 20,000 bytes.

Default invocation never sends. No retries, tokenizer downloads or Chat/model
fallback. Synthetic SQL rows roll back; real budget audit rows stay UNKNOWN.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
OUT = ROOT/'var/reports/p4-r1/live'


class AuthorizedTransport:
    """Persist consumed quota BEFORE any uncertain send, including errors."""
    def __init__(self, record, save, send, approved_documents):
        self.record,self.save,self.send = record,save,send
        self.approved_documents = approved_documents

    def __call__(self, request, timeout):
        from backend.app.ports.providers import ProviderRequestNotSent
        payload=json.loads(request.data)
        if (request.full_url!='https://api.siliconflow.cn/v1/rerank'
                or payload.get('model')!='BAAI/bge-reranker-v2-m3'
                or payload.get('query') not in {'Which synthetic document states cost 42.75?', 'Synthetic cost'}
                or payload.get('return_documents') is not True
                or not payload.get('documents')
                or any(body not in self.approved_documents for body in payload['documents'])):
            raise ProviderRequestNotSent('P4_SYNTHETIC_ALLOWLIST_DENIED')
        attempts=self.record['attempts']
        if len(attempts)>=3 or sum(a['request_body_bytes'] for a in attempts)+len(request.data)>20000:
            raise ProviderRequestNotSent('P4_AUTHORIZATION_CAP_EXCEEDED')
        attempts.append(dict(number=len(attempts)+1,request_body_bytes=len(request.data),
            request_body_sha256=hashlib.sha256(request.data).hexdigest(),state='consumed_before_transport'))
        self.save()
        return self.send(request,timeout)


def table_counts(connection):
    from sqlalchemy import text
    names=connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename!='alembic_version' ORDER BY tablename")).scalars().all()
    return {t:connection.execute(text('SELECT count(*) FROM "'+t+'"')).scalar_one() for t in names}


def run():
    """Historical combined real entry is permanently disabled, even without files."""
    raise ValueError('HISTORICAL_COMBINED_ENTRY_DISABLED')



@contextmanager
def pg_only_factory(output):
    """One joint retrieval call after a separately verified probe; no probe here.

    Only the unchanged registered synthetic P3 fixture is admitted. Business
    SQL is rolled back; the normal provider budget uses its own connection.
    """
    from scripts import verify_p4_rerank_once as once
    from backend.app.domain.scope import Scope
    from backend.app.application.citations import CitationService, InMemoryCitationStore
    from backend.tests.test_p4_rag_postgres import seed_p3_fixture
    with once.live_container(once.PG_REQUEST_ID, output) as (container, identity):
        ranker = container.knowledge_gateway.retriever.ranker
        engine = container.engine
        with engine.connect() as connection:
            before = table_counts(connection)
        if any(n for name, n in before.items() if name != 'model_calls'):
            raise ValueError('CLEAN_SLATE_UNEXPECTED_BUSINESS_DATA')
        repo = container.store
        original_engine = repo.engine
        with engine.connect() as connection:
            transaction = connection.begin()
            class TransactionEngine:
                @contextmanager
                def connect(self): yield connection
                @contextmanager
                def begin(self):
                    with connection.begin_nested(): yield connection
            try:
                repo.engine = TransactionEngine()
                kb, upload, body, children, parents = seed_p3_fixture(repo, connection)
                if body != once.PG_CORPUS or hashlib.sha256(body.encode()).hexdigest() != once.PG_CORPUS_SHA256:
                    raise ValueError('PG_SYNTHETIC_CORPUS_CHANGED')
                scope = Scope.from_ids([kb['id']])
                seeds = repo.get_retrieval_chunks(scope, children)
                if set(seeds) != set(children):
                    raise ValueError('PG_SYNTHETIC_SCOPE_INCOMPLETE')
                for seed in seeds.values():
                    if (seed.content != body[seed.locator['start']:seed.locator['end']]
                            or seed.content_sha256 != hashlib.sha256(seed.content.encode()).hexdigest()):
                        raise ValueError('PG_SYNTHETIC_SOURCE_MISMATCH')
                approved = frozenset(seed.content for seed in seeds.values())
                def invoke(record):
                    # Exactly this normal pipeline calls the protected ranker.
                    retrieval = container.knowledge_gateway.retriever.retrieve(scope, once.PG_QUERY)
                    if retrieval.degradation_flags or len(ranker.receipts) != 1:
                        raise ValueError('PG_ONLY_PIPELINE_DEGRADED')
                    citations = CitationService(InMemoryCitationStore())
                    service = container.knowledge_gateway.evidence
                    bundle = service.with_context(service.bundle(service.plan(once.PG_QUERY), retrieval),
                                                  once.PG_REQUEST_ID, citations)
                    if not bundle.snapshots or bundle.retrieval.retrieval_stats['parent_count'] <= 0:
                        raise ValueError('PG_ONLY_EVIDENCE_MISSING')
                    for snap in bundle.snapshots:
                        if (snap.chunk_id not in children
                                or snap.quote != body[snap.locator['start']:snap.locator['end']]
                                or snap.quote_sha256 != hashlib.sha256(snap.quote.encode()).hexdigest()):
                            raise ValueError('PG_ONLY_CITATION_SOURCE_MISMATCH')
                    record['pipeline'] = dict(status='PASS', retrieval_stats=retrieval.retrieval_stats,
                        evidence_stats=bundle.evidence_stats, labels=bundle.labels,
                        snapshot_hashes=[snap.quote_sha256 for snap in bundle.snapshots],
                        verification='REAL_POSTGRESQL_REAL_RERANK; SYNTHETIC_P3_FIXTURE; NO_EMBEDDING_OR_CHAT_SEND')
                yield ranker, identity, invoke, approved
            finally:
                transaction.rollback()
                repo.engine = original_engine
        with engine.connect() as connection:
            after = table_counts(connection)
        if (any(after[name] != before[name] for name in before if name != 'model_calls')
                or after['model_calls'] != before['model_calls'] + len(ranker.receipts)):
            raise ValueError('PG_ONLY_ROLLBACK_OR_LEDGER_MISMATCH')


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if '--pg-only' in argv:
        from scripts.verify_p4_rerank_once import main as once_main
        argv.remove('--pg-only')
        return once_main(argv, pg_only=True)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-owner-authorized',action='store_true')
    args=parser.parse_args(argv)
    if not args.execute_owner_authorized:
        print('NOT RUN: explicit owner authorization flag required')
        return 0
    print('BLOCKED: HISTORICAL_COMBINED_ENTRY_DISABLED; use the coordinator-assigned --pg-only entry')
    return 1


if __name__=='__main__':
    raise SystemExit(main())
