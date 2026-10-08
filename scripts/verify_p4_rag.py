"""Explicit P4 synthetic-only Rerank validation, at most 3 sends / 20,000 bytes.

Default invocation never sends. No retries, tokenizer downloads or Chat/model
fallback. Synthetic SQL rows roll back; real budget audit rows stay UNKNOWN.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import os
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
    from sqlalchemy import create_engine
    from backend.app.adapters.models.cloud import _send
    from backend.app.application.budget import PostgresBudgetGate
    from backend.app.application.provider_usage import BudgetUsageGuard
    from backend.app.application.citations import CitationService,InMemoryCitationStore
    from backend.app.bootstrap import build_container
    from backend.app.config import Settings
    from backend.app.domain.models import ChunkRecord,RankedHit
    from backend.app.domain.model_registry import ModelRegistry
    from backend.app.domain.scope import Scope
    from backend.app.ports.model_access import model_access
    from scripts.with_clean_slate import resolve_environment,verify_database
    from backend.tests.test_p4_rag_postgres import seed_p3_fixture

    OUT.mkdir(parents=True,exist_ok=True)
    receipt=OUT/'live-receipt.json'
    record=dict(status='STARTED',authorization='P4-R1; max 3 HTTP sends; no retries; synthetic only; total body <=20000 UTF-8 bytes',
        attempts=[],settlement='UNKNOWN',pricing_source='https://siliconflow.cn/pricing',
        pricing_observation='BAAI/bge-reranker-v2-m3 public free listing; not account billing proof')
    # Never reset or reuse a consumed authorization on another invocation.
    with receipt.open('x',encoding='utf-8') as f:json.dump(record,f,indent=2)
    def save():
        temporary=receipt.with_suffix('.tmp')
        with temporary.open('w',encoding='utf-8') as f:
            json.dump(record,f,indent=2,ensure_ascii=False);f.flush();os.fsync(f.fileno())
        os.replace(temporary,receipt)
    engine=container=None
    try:
        profile=json.loads((ROOT/'deploy/clean-slate/profile.json').read_text(encoding='utf-8'))
        env=resolve_environment(profile,os.environ.copy())
        record['database_identity']=verify_database(profile,env)
        key=env.get('SILICONFLOW_API_KEY','').strip()
        if not key:raise ValueError('MODEL_CREDENTIAL_MISSING')
        # Only this project process; never Windows user/system environment.
        os.environ['SILICONFLOW_API_KEY']=key
        engine=create_engine(env['RAG_DATABASE_URL'],hide_parameters=True,
            connect_args={'application_name':'p4-authorized-synthetic-rerank'})
        with engine.connect() as c:before=table_counts(c)
        if any(n for t,n in before.items() if t!='model_calls'):
            raise ValueError('CLEAN_SLATE_UNEXPECTED_BUSINESS_DATA')
        record['counts_before']=before
        spec=ModelRegistry.frozen_defaults().select('rerank')
        guard=BudgetUsageGuard(PostgresBudgetGate(engine,10),spec,1)
        settings=Settings(database_url=env['RAG_DATABASE_URL'],storage_root=OUT/'synthetic-storage',
            cloud_enabled=True,rerank_egress_enabled=True,rerank_enabled=True,retrieval_mode='keyword',
            local_answer_enabled=False)
        container=build_container(settings,model=None,agent_model=None,cloud_model=None,
            embedding_provider=None,model_usage_guards={'rerank':guard})
        ranker=container.knowledge_gateway.retriever.ranker
        approved={'Synthetic cost is 42.75.','Synthetic gardening note has no cost information.'}
        ranker.transport=AuthorizedTransport(record,save,_send,approved)
        probe=[ChunkRecord('probe-'+str(n),'synthetic-kb','synthetic-doc','synthetic-version',body)
               for n,body in enumerate(sorted(approved))]
        hits=[RankedHit(chunk_id=c.chunk_id,rank=n) for n,c in enumerate(probe,1)]
        with model_access('rerank',allowed=True):ranker.rank('Which synthetic document states cost 42.75?',hits,{c.chunk_id:c for c in probe})
        record['probe']=dict(status='PASS',returned_count=len(ranker.last_result.hits),
            scores=ranker.last_result.scores,usage_actual=ranker.last_result.usage_actual,
            expected_top_match=next(c.content for c in probe if c.chunk_id==ranker.last_result.hits[0].chunk_id)
                == 'Synthetic cost is 42.75.')
        assert record['probe']['expected_top_match']
        save()
        with engine.connect() as c:
            tx=c.begin()
            class TransactionEngine:
                @contextmanager
                def connect(self):yield c
                @contextmanager
                def begin(self):
                    with c.begin_nested():yield c
            try:
                repo=container.store
                repo.engine=TransactionEngine()
                kb,upload,body,children,parents=seed_p3_fixture(repo,c)
                scope=Scope.from_ids([kb['id']])
                seeds=repo.get_retrieval_chunks(scope,children)
                approved.update(s.content for s in seeds.values())
                retrieval=container.knowledge_gateway.retriever.retrieve(scope,'Synthetic cost')
                if retrieval.degradation_flags or len(ranker.receipts)!=2:
                    raise ValueError('P4_LIVE_PIPELINE_DEGRADED')
                citations=CitationService(InMemoryCitationStore())
                service=container.knowledge_gateway.evidence
                bundle=service.with_context(service.bundle(service.plan('Synthetic cost'),retrieval),'p4-live',citations)
                assert bundle.snapshots and bundle.retrieval.retrieval_stats['parent_count']>0
                for snap in bundle.snapshots:
                    assert snap.chunk_id in children and snap.quote==body[snap.locator['start']:snap.locator['end']]
                    assert snap.quote_sha256==hashlib.sha256(snap.quote.encode()).hexdigest()
                record['pipeline']=dict(status='PASS',retrieval_stats=retrieval.retrieval_stats,
                    evidence_stats=bundle.evidence_stats,labels=bundle.labels,
                    snapshot_hashes=[s.quote_sha256 for s in bundle.snapshots],
                    verification='REAL_POSTGRESQL_REAL_RERANK; SYNTHETIC_P3_FIXTURE; NO_EMBEDDING_OR_CHAT_SEND')
            finally:tx.rollback()
        with engine.connect() as c:after=table_counts(c)
        record['counts_after']=after
        assert all(after[t]==before[t] for t in before if t!='model_calls')
        assert after['model_calls']==before['model_calls']+len(record['attempts'])
        record.update(status='PASS',synthetic_sql_rollback='PASS',receipts=ranker.receipts,
            total_request_body_bytes=sum(a['request_body_bytes'] for a in record['attempts']))
    except Exception as exc:
        record.update(status='BLOCKED',error_type=type(exc).__name__)
        # No provider exception body, traceback, DSN or key in evidence.
        if container is not None:
            record['receipts']=container.knowledge_gateway.retriever.ranker.receipts
        if engine is not None:
            with engine.connect() as c:record['counts_after']=table_counts(c)
        save()
        return 1
    finally:
        if container is not None:container.engine.dispose()
        if engine is not None:engine.dispose()
    save()
    return 0


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-owner-authorized',action='store_true')
    args=parser.parse_args()
    if not args.execute_owner_authorized:
        print('NOT RUN: explicit owner authorization flag required')
        raise SystemExit(0)
    code=run()
    print(json.dumps({'exit_code':code,'report':str(OUT/'live-receipt.json')}))
    raise SystemExit(code)
