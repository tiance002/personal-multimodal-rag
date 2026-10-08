"""Explicit P4_PRE-R4 synthetic-only SiliconFlow validation; no default send.

Uses the identity-checked Clean-slate database, real worker/repository/pgvector,
fixed BGE tokenizer and the existing PostgreSQL BudgetUsageGuard. A one-shot
receipt prevents this entry from silently receiving a fresh quota on restart.
Real model_calls audit rows retain UNKNOWN occupancy; synthetic business rows
alone are precisely cleaned. No service, Chat, Vision or Rerank is started.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text

from backend.app.adapters.models.cloud import EmbeddingAdmission
from backend.app.adapters.models.factory import ProviderFactory
from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.application.budget import PostgresBudgetGate
from backend.app.application.provider_usage import BudgetUsageGuard
from backend.app.domain.adaptive_chunking import ChunkingConfig, prepare_document
from backend.app.domain.model_registry import ModelRegistry
from backend.app.domain.scope import Scope
from backend.app.domain.version_source import VersionSource
from backend.app.ports.ingestion import parse_version_source
from backend.app.ports.model_access import model_access, scope_allows
from backend.app.ports.providers import ProviderRequestNotSent
from backend.app.workers.ingestion import run_once
from scripts.with_clean_slate import resolve_environment, verify_database

OUT = ROOT/'var/reports/p4-pre-r4'


def save(name, value):
    """Only explicitly assembled metadata; never serialize environment/request."""
    path = OUT/name
    temporary = path.with_suffix(path.suffix+'.tmp')
    with temporary.open('w', encoding='utf-8') as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def table_counts(connection):
    tables = connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename!='alembic_version' ORDER BY tablename")).scalars()
    return {t:connection.execute(text('SELECT count(*) FROM "'+t+'"')).scalar_one() for t in tables}


class TrialBudgetGuard(BudgetUsageGuard):
    def __init__(self, engine, spec, record):
        # Positive floor for a publicly free model. This is accounting occupancy,
        # not a claim of actual charge or permission to use a paid model.
        super().__init__(PostgresBudgetGate(engine, 10), spec, 1)
        self.record = record

    def reserve(self, **kwargs):
        planned = kwargs['planned_tokens']
        attempts = self.record['attempts']
        if (len(attempts) >= 10 or type(planned) is not int or planned <= 0
                or sum(a['planned_tokens'] for a in attempts)+planned > 20000):
            raise ValueError('R4_AUTHORIZATION_CAP_EXCEEDED')
        attempt = dict(number=len(attempts)+1, planned_tokens=planned,
                       state='reserved_before_transport', settlement='UNKNOWN')
        attempts.append(attempt)
        save('live-receipt.json', self.record)  # consumed even on uncertain send
        reservation = super().reserve(**kwargs)
        attempt['reservation_id'] = reservation
        save('live-receipt.json', self.record)
        return reservation


def synthetic_source():
    paragraph = ('合成测试项目甲的成本为 42.75 元，项目乙的成本为 -7.25 元。'
                 '本段仅用于验证完整原文、标题、父子分块及引用偏移。')
    return ('# 合成验收资料\n\n## 成本口径\n\n'
            + '\n\n'.join(paragraph*5 for _ in range(6))
            + '\n\n## 结构化明细\n\n| 项目 | 成本 |\n| --- | --- |\n| 甲 |  42.75  |\n| 乙 |  -7.25  |\n'
            + '\n# 独立结束\n\n完整的独立短段落。\n').encode('utf-8')


def exact_cleanup(engine, record):
    """Only registered IDs and verified FK owners; no global deletion."""
    cleanup = []
    with engine.begin() as c:
        for kb in record['objects']['knowledge_bases']:
            row = c.execute(text('SELECT id,name FROM knowledge_bases WHERE id=:id FOR UPDATE'), kb).mappings().one()
            assert row['name'] == kb['name']
            docs = c.execute(text('SELECT id FROM documents WHERE knowledge_base_id=:id'), kb).scalars().all()
            registered = {uuid.UUID(r['document_id']) for r in record['objects']['uploads'] if r['kb_id']==kb['id']}
            assert set(docs) == registered
            for doc in docs:
                versions = c.execute(text('SELECT id FROM document_versions WHERE document_id=:id'), {'id':doc}).scalars().all()
                expected = {uuid.UUID(r['version_id']) for r in record['objects']['uploads'] if r['document_id']==str(doc)}
                assert set(versions) == expected
                c.execute(text('UPDATE documents SET active_version_id=NULL WHERE id=:id'), {'id':doc})
                for v in versions:
                    params={'v':v,'doc':doc,'kb':kb['id']}
                    # Each DELETE is limited by the exact immutable version.
                    for table in ['chunk_assets','chunk_embeddings','chunk_terms']:
                        clause = 'version_id=:v' if table=='chunk_assets' else 'chunk_id IN (SELECT id FROM chunks WHERE version_id=:v AND document_id=:doc AND knowledge_base_id=:kb)'
                        n=c.execute(text('DELETE FROM '+table+' WHERE '+clause),params).rowcount
                        cleanup.append(dict(table=table,version_id=str(v),rows=n))
                    for role in ['child','parent']:
                        n=c.execute(text('DELETE FROM chunks WHERE version_id=:v AND document_id=:doc AND knowledge_base_id=:kb AND chunk_role=:role'),{**params,'role':role}).rowcount
                        cleanup.append(dict(table='chunks',role=role,version_id=str(v),rows=n))
                    # The frozen Markdown fixture has no asset lineage or graph.
                    assert c.execute(text('SELECT count(*) FROM document_assets WHERE version_id=:v'),params).scalar_one()==0
                    for table in ['document_sections','ingestion_jobs']:
                        n=c.execute(text('DELETE FROM '+table+' WHERE version_id=:v'),params).rowcount
                        cleanup.append(dict(table=table,version_id=str(v),rows=n))
                    n=c.execute(text('DELETE FROM document_versions WHERE id=:v AND document_id=:doc'),params).rowcount
                    assert n==1
                assert c.execute(text('DELETE FROM documents WHERE id=:id AND knowledge_base_id=:kb'),{'id':doc,'kb':kb['id']}).rowcount==1
            assert c.execute(text('DELETE FROM knowledge_bases WHERE id=:id AND name=:name'),kb).rowcount==1
        for profile in record['objects']['profiles']:
            row=c.execute(text('SELECT * FROM embedding_profiles WHERE id=:id FOR UPDATE'),profile).mappings().one()
            assert all(str(row[k])==str(profile[k]) for k in profile)
            assert c.execute(text('SELECT count(*) FROM chunk_embeddings WHERE profile_id=:id'),profile).scalar_one()==0
            assert c.execute(text('DELETE FROM embedding_profiles WHERE id=:id AND fingerprint=:fingerprint'),profile).rowcount==1
            cleanup.append(dict(table='embedding_profiles',id=profile['id'],rows=1))
    with engine.connect() as c:
        after=table_counts(c)
        audits=[dict(r) for r in c.execute(text('SELECT id,provider,model_name,purpose,reservation_state,reserved_cost_microunits FROM model_calls ORDER BY id')).mappings()]
    # Real request audit is retained, not reset into fictitious zero occupancy.
    assert all(after[t]==record['counts_before'][t] for t in after if t!='model_calls')
    assert after['model_calls']==len(record['attempts'])
    record.update(cleanup='PASS',cleanup_rows=cleanup,counts_after=after,
                  retained_model_call_audit=[{**r,'id':str(r['id'])} for r in audits])


def run(tokenizer_path):
    OUT.mkdir(parents=True, exist_ok=True)
    # Never automatically retry a partly completed run with a new allowance.
    with (OUT/'live-attempt.lock').open('x',encoding='utf-8') as handle:
        handle.write('P4_PRE-R4: one explicitly authorized execution; do not reset\n')
    record=dict(task='P4_PRE-R4', status='P4_PRE_PARTIAL',execution='MANUALLY_SUPERVISED_TRIAL',
        provider='siliconflow',model='BAAI/bge-m3', endpoint='https://api.siliconflow.cn/v1/embeddings',
        attempts=[],objects=dict(knowledge_bases=[],uploads=[],profiles=[]),
        pricing_source='https://siliconflow.cn/pricing',pricing='publicly_free_checked_2026-10-08',
        actual_charge='UNKNOWN',provider_internal_non_truncation='UNKNOWN',
        other_role_requests=dict(deepseek=0,chat=0,vision=0,rerank=0), stages={})
    save('live-receipt.json',record)
    engine=None
    try:
        profile=json.loads((ROOT/'deploy/clean-slate/profile.json').read_text(encoding='utf-8'))
        env=resolve_environment(profile,os.environ.copy())
        record['database_identity']=verify_database(profile,env)
        if not env.get('SILICONFLOW_API_KEY','').strip():
            raise ProviderRequestNotSent('MODEL_CREDENTIAL_MISSING')
        # This process receives only the project SF key. No global Windows edit.
        os.environ['SILICONFLOW_API_KEY']=env['SILICONFLOW_API_KEY']
        os.environ.pop('DEEPSEEK_API_KEY',None)
        admission=EmbeddingAdmission.from_bge_m3_file(tokenizer_path)
        record['tokenizer_identity']=admission.tokenizer_identity
        record['client_limit']=admission.counter.max_input_tokens
        engine=create_engine(env['RAG_DATABASE_URL'],hide_parameters=True,
            connect_args={'application_name':'p4-pre-r4-synthetic','options':'-c statement_timeout=10000'})
        with engine.connect() as c:
            record['counts_before']=table_counts(c)
            assert not any(record['counts_before'].values()), 'CLEAN_SLATE_NOT_EMPTY'
            others=c.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND backend_type='client backend' AND pid!=pg_backend_pid()")).scalar_one()
            assert others==0, 'UNREGISTERED_DATABASE_CONSUMER'
        registry=ModelRegistry.from_dict(json.loads((ROOT/'deploy/clean-slate/models.json').read_text()))
        spec=registry.select('embedding')
        guard=TrialBudgetGuard(engine,spec,record)
        factory=ProviderFactory(registry,enabled_roles=['embedding'],usage_guards={'embedding':guard},embedding_admission=admission)
        adapter=factory.build('embedding')
        record['egress_policy']=dict(global_product_cloud=False,enabled_roles=['embedding'],other_roles_enabled=False)
        assert all(not factory.build(r).enabled for r in ['chat_cheap','chat_expensive','rerank','vision'])
        # Explicit role/global-equivalent refusal, before any real API call.
        adapter.enabled=False
        with model_access('embedding',allowed=True):
            try: adapter.embed(['P4 R4 synthetic access probe'],10)
            except ProviderRequestNotSent: pass
            else: raise AssertionError('EGRESS_DENIAL_FAILED')
        assert adapter.requests==0 and not record['attempts']
        record['disabled_before_send']='PASS'
        adapter.enabled=True
        repo=PostgresKnowledgeRepository(engine,ContentAddressedStorage(OUT/'synthetic-storage'),embedding_provider=adapter)
        marker='P4-R4-SYNTHETIC-'+uuid.uuid4().hex
        denied=repo.create_knowledge_base(marker+'-denied',cloud_allowed=False)
        record['objects']['knowledge_bases'].append(dict(id=denied['id'],name=denied['name']))
        save('live-receipt.json',record)
        denied_source=repo.storage.put_stream(BytesIO('# 合成禁用\n完整内容 42.75。'.encode()))
        denied_upload=repo.create_upload(denied['id'],'denied.md','text/markdown',denied_source)
        record['objects']['uploads'].append({**denied_upload,'kb_id':denied['id']})
        save('live-receipt.json',record)
        denied_result=run_once(repo,worker_id=marker+'-denial',lease_seconds=120)
        assert denied_result['status']=='failed' and adapter.requests==0
        assert repo.get_document(denied_upload['document_id'])['active_version_id'] is None
        record['kb_denial_before_send']='PASS'
        # Account/model eligibility is established by this first authorized
        # short embedding probe, not a call to another unapproved endpoint.
        t=time.perf_counter()
        with model_access('embedding',allowed=True):
            probe=adapter.embed(['P4 R4 synthetic access probe'],30)
        assert probe.dimensions==1024 and len(probe.vectors)==1
        record['stages']['account_probe_ms']=(time.perf_counter()-t)*1000
        record['account_model_access']='PASS'
        kb=repo.create_knowledge_base(marker+'-allowed',cloud_allowed=True)
        record['objects']['knowledge_bases'].append(dict(id=kb['id'],name=kb['name']))
        save('live-receipt.json',record)
        source=synthetic_source()
        stored=repo.storage.put_stream(BytesIO(source))
        receipt=repo.create_upload(kb['id'],'synthetic.md','text/markdown',stored)
        record['objects']['uploads'].append({**receipt,'kb_id':kb['id']})
        save('live-receipt.json',record)
        with engine.connect() as c:
            row=c.execute(text('SELECT * FROM document_versions WHERE id=:v'),{'v':receipt['version_id']}).mappings().one()
            normalized=parse_version_source(repo.parsers,repo.storage,VersionSource.from_row(row))
        prepared=prepare_document(normalized,ChunkingConfig())
        inputs=[child.embedding_content for child in prepared.children]
        assert prepared.parents and len(inputs)>1 and len(inputs)<=32
        assert any(ch.parent_index is not None for ch in prepared.children)
        assert any(ch.parent_index is None for ch in prepared.children)
        assert '| 甲 |  42.75  |' in normalized.markdown_content
        record['local_input_tokens']=[admission.counter(t) for t in inputs]
        assert sum(record['local_input_tokens'])+sum(a['planned_tokens'] for a in record['attempts'])+256<=20000
        t=time.perf_counter()
        job=run_once(repo,worker_id=marker+'-worker',lease_seconds=120)
        record['stages']['worker_ingestion_ms']=(time.perf_counter()-t)*1000
        assert str(job['id'])==receipt['job_id'] and job['status']=='succeeded'
        assert str(repo.get_document(receipt['document_id'])['active_version_id'])==receipt['version_id']
        with engine.connect() as c:
            rows=[dict(r) for r in c.execute(text('SELECT c.*,ce.profile_id,vector_dims(ce.embedding) AS dims FROM chunks c LEFT JOIN chunk_embeddings ce ON ce.chunk_id=c.id WHERE c.version_id=:v ORDER BY c.chunk_index'),{'v':receipt['version_id']}).mappings()]
            profile_id=repo.get_embedding_profile_id(spec.model_id,1024)
            profiles=[dict(r) for r in c.execute(text('SELECT * FROM embedding_profiles ORDER BY id')).mappings()]
            for p in profiles:
                record['objects']['profiles'].append({k:str(p[k]) if k=='id' else p[k] for k in ['id','provider','model_name','model_revision','dimension','distance','fingerprint']})
            save('live-receipt.json',record)
            v=c.execute(text('SELECT * FROM document_versions WHERE id=:v'),{'v':receipt['version_id']}).mappings().one()
            assert v['index_status']=='ready' and v['index_identity']==ChunkingConfig().identity
            assert v['source_sha256']==hashlib.sha256(source).hexdigest()
            raw_normalized=repo.storage.read(v['normalized_content_key'])
            assert hashlib.sha256(raw_normalized).hexdigest()==v['normalized_content_sha256']
            assert raw_normalized.decode()==normalized.markdown_content
            assert c.execute(text("SELECT count(*) FROM chunk_terms t JOIN chunks c ON c.id=t.chunk_id WHERE c.chunk_role='parent' AND c.version_id=:v"),{'v':receipt['version_id']}).scalar_one()==0
        parents=[r for r in rows if r['chunk_role']=='parent']
        children=[r for r in rows if r['chunk_role']=='child']
        parent_by_id={r['id']:r for r in parents}
        assert len(parents)==len(prepared.parents) and len(children)==len(prepared.children)
        assert all(r['profile_id'] is None for r in parents)
        assert all(str(r['profile_id'])==profile_id and r['dims']==1024 for r in children)
        for row in rows:
            body=normalized.markdown_content[row['start_pos']:row['end_pos']]
            assert body==row['content']==row['locator']['quote']
            assert row['locator']['start']==row['start_pos'] and row['locator']['end']==row['end_pos']
            assert hashlib.sha256(body.encode()).hexdigest()==row['content_sha256']
            assert row['index_identity']==ChunkingConfig().identity
            if row['parent_id'] is not None:
                parent=parent_by_id[row['parent_id']]
                assert parent['version_id']==row['version_id']
                assert parent['start_pos']<=row['start_pos']<row['end_pos']<=parent['end_pos']
        assert len(profiles)==1
        assert profiles[0]['provider']=='siliconflow' and profiles[0]['model_name']==spec.model_id
        assert profiles[0]['fingerprint']==repo.embedding_identity().fingerprint
        query='合成测试项目甲的成本是多少？'
        assert repo.embedding_scope_allowed(Scope.from_ids([kb['id']]))
        with model_access('embedding',allowed=True):
            query_result=adapter.embed([query],30)
        repo.validate_embedding_result(query_result)
        assert query_result.identity_fingerprint==repo.embedding_identity().fingerprint
        t=time.perf_counter()
        hits=repo.vector_candidates(Scope.from_ids([kb['id']]),query_result.vectors[0],100,profile_id=profile_id)
        record['stages']['pgvector_query_ms']=(time.perf_counter()-t)*1000
        assert {h.chunk_id for h in hits}=={str(r['id']) for r in children}
        assert not repo.vector_candidates(Scope.from_ids([denied['id']]),query_result.vectors[0],100,profile_id=profile_id)
        with engine.begin() as c:
            wrong=replace(repo.embedding_identity(),embedding_input_semantics_version='P4-R4-SYNTHETIC-wrong-space')
            wrong_id=repo._get_or_create_embedding_profile(c,wrong)
            p=c.execute(text('SELECT * FROM embedding_profiles WHERE id=:id'),{'id':wrong_id}).mappings().one()
            record['objects']['profiles'].append({k:str(p[k]) if k=='id' else p[k] for k in ['id','provider','model_name','model_revision','dimension','distance','fingerprint']})
        save('live-receipt.json',record)
        assert not repo.vector_candidates(Scope.from_ids([kb['id']]),query_result.vectors[0],100,profile_id=str(wrong_id))
        assert not any(scope_allows(r) for r in registry.defaults)
        record.update(chain='PASS',parent_count=len(parents),child_count=len(children),
            embedding_count=len(children),profile_count=2,query_hit_ids=[h.chunk_id for h in hits],
            source_sha256=stored.sha256,index_identity=ChunkingConfig().identity,
            actual_request_count=adapter.requests,planned_tokens=adapter.planned_tokens)
    except Exception as exc:
        # No exception message, traceback, response body, header, or DSN.
        record['failure_type']=type(exc).__name__
        if isinstance(exc,AssertionError): record['status']='P4_PRE_BLOCKED'
    finally:
        if 'adapter' in locals():
            record['provider_receipts']=adapter.receipts
            record['actual_request_count']=adapter.requests
            record['planned_tokens']=adapter.planned_tokens
        if engine is not None and 'counts_before' in record:
            try:
                exact_cleanup(engine,record)
            except Exception as exc:
                record.update(cleanup='CLEANUP_BLOCKED',cleanup_error_type=type(exc).__name__,status='P4_PRE_BLOCKED')
            engine.dispose()
        save('live-receipt.json',record)
    if record.get('chain')=='PASS' and record.get('cleanup')=='PASS':
        record['status']='P4_PRE_PASS'
        save('live-receipt.json',record)
        return 0
    return 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-real-siliconflow',action='store_true')
    parser.add_argument('--tokenizer',type=Path,required=True)
    args=parser.parse_args()
    if not args.allow_real_siliconflow:
        print('REAL_PROVIDER_NOT_AUTHORIZED')
        return 2
    try:
        result=run(args.tokenizer)
    except Exception as exc:
        print(json.dumps(dict(status='P4_PRE_PARTIAL',error_type=type(exc).__name__)))
        return 1
    print((OUT/'live-receipt.json').read_text(encoding='utf-8'))
    return result


if __name__=='__main__':
    raise SystemExit(main())
