"""REAL Clean-slate PostgreSQL/pgvector; SIMULATED model HTTP; rollback only.

Fixtures use unchanged P3 prepare_document then insert its exact drafts, without
claiming this is an upload/worker or real Embedding run (Preflight owns those).
"""
from io import BytesIO
import hashlib
import json
import uuid

import pytest
from sqlalchemy import text

from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.adapters.models.cloud import SiliconFlowRerank
from backend.app.application.retrieval import HybridRetriever
from backend.app.application.context_builder import ContextBuilder
from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.domain.adaptive_chunking import prepare_document
from backend.app.domain.model_registry import ModelRegistry
from backend.app.domain.models import NormalizedDocument
from backend.app.domain.scope import Scope
from backend.app.domain.text_normalization import term_frequencies
from backend.tests.test_clean_slate_model_profiles import isolated_database
from backend.tests.test_model_provider_contracts import SimulatedGuard, embedding


def seed_p3_fixture(repo, connection, *, allowed=True):
    """Registered synthetic source, exact P3 drafts, no provider request."""
    body = ''.join(f'Synthetic record {n:02}: cost 42.75, total 17.25.\n' for n in range(30))
    kb = repo.create_knowledge_base('p4-synthetic-'+uuid.uuid4().hex, cloud_allowed=allowed)
    stored = repo.storage.put_stream(BytesIO(body.encode()))
    receipt = repo.create_upload(kb['id'], 'p4-synthetic.txt', 'text/plain', stored)
    document = NormalizedDocument(document_id=receipt['document_id'],version_id=receipt['version_id'],
        title='SYNTHETIC P4',media_type='text/plain',markdown_content=body,
        content_sha256=stored.sha256,parser_version='SIMULATED-NORMALIZATION/p4')
    prepared = prepare_document(document, repo.chunking_config)
    parent_ids = [str(uuid.uuid4()) for _ in prepared.parents]
    section = str(uuid.uuid4())
    common = dict(kb=kb['id'],doc=receipt['document_id'],version=receipt['version_id'],
                  identity=repo.chunking_config.identity,section=section)
    connection.execute(text('''INSERT INTO document_sections
        (id,knowledge_base_id,document_id,version_id,start_pos,end_pos)
        VALUES (:section,:kb,:doc,:version,0,:end)'''),{**common,'end':len(body)})
    child_ids=[]
    for n,draft in enumerate(prepared.parents+prepared.children):
        parent=n<len(prepared.parents)
        id=parent_ids[n] if parent else str(uuid.uuid4())
        params={**common,'id':id,'index':n,'body':draft.content,'hash':draft.content_sha256,
            'start':draft.start,'end':draft.end,'locator':json.dumps(draft.source_locator.model_dump(mode='json')),
            'header':draft.context_header,'role':'parent' if parent else 'child',
            'parent':parent_ids[draft.parent_index] if draft.parent_index is not None else None,
            'type':draft.chunk_type}
        connection.execute(text('''INSERT INTO chunks
            (id,knowledge_base_id,document_id,version_id,section_id,chunk_index,content,content_sha256,
             start_pos,end_pos,locator,context_header,chunk_role,parent_id,index_identity,chunk_type)
            VALUES (:id,:kb,:doc,:version,:section,:index,:body,:hash,:start,:end,CAST(:locator AS jsonb),
                    :header,:role,:parent,:identity,:type)'''),params)
        if not parent:
            child_ids.append(id)
            for term,count in term_frequencies(draft.content).items():
                connection.execute(text('INSERT INTO chunk_terms(chunk_id,term,term_frequency) VALUES (:id,:term,:tf)'),
                    {'id':id,'term':term,'tf':count})
    connection.execute(text("UPDATE document_versions SET index_status='ready',index_identity=:identity WHERE id=:version"),common)
    connection.execute(text('UPDATE documents SET active_version_id=:version WHERE id=:doc'),common)
    return kb,receipt,body,child_ids,parent_ids


@pytest.mark.parametrize('mode',['keyword','vector','hybrid'])
def test_pg_pipeline_scope_profile_parent_context_citations(isolated_database,monkeypatch,tmp_path,mode):
    engine,c=isolated_database
    adapter,_=embedding(ModelRegistry.frozen_defaults(),SimulatedGuard())
    monkeypatch.setenv('SILICONFLOW_API_KEY','SIMULATED-P4-POSTGRES')
    repo=PostgresKnowledgeRepository(engine,ContentAddressedStorage(tmp_path),embedding_provider=adapter)
    kb,receipt,body,children,parents=seed_p3_fixture(repo,c)
    profile=repo._get_or_create_embedding_profile(c,repo.embedding_identity())
    for id in children:
        c.execute(text('INSERT INTO chunk_embeddings(chunk_id,profile_id,embedding) VALUES (:id,:profile,CAST(:v AS vector))'),
            {'id':id,'profile':profile,'v':'['+','.join(['1']*1024)+']'})
    ranker=SiliconFlowRerank(ModelRegistry.frozen_defaults().select('rerank'),enabled=True,
        usage_guard=SimulatedGuard(),transport=lambda request,timeout: dict(results=[
            {'index':i,'relevance_score':1.-i/100.,'document':{'text':json.loads(request.data)['documents'][i]}} for i in reversed(range(len(json.loads(request.data)['documents'])))]))
    result=HybridRetriever(repo,adapter,mode=mode,ranker=ranker,top_k=1).retrieve(Scope.from_ids([kb['id']]),'Synthetic cost')
    assert result.items and not result.degradation_flags
    assert all(i.chunk.chunk_id in children for i in result.items)
    assert len({i.context_passage.key for i in result.items})==1
    assert result.retrieval_stats['parent_count']>=1
    service=CitationService(InMemoryCitationStore())
    context,labels=ContextBuilder().build('p4',result.items,service)
    assert '辅助上下文，不是引用证据' in context
    assert len(labels)==len(result.items)
    for item,label in zip(result.items,labels,strict=True):
        snap=service.snapshots['p4',label]
        assert snap.quote==body[snap.locator['start']:snap.locator['end']]==item.chunk.content
        assert snap.quote_sha256==hashlib.sha256(snap.quote.encode()).hexdigest()
    assert repo.get_retrieval_chunks(Scope.from_ids([str(uuid.uuid4())]),children)=={}
    assert repo.vector_candidates(Scope.from_ids([kb['id']]),[1.]*1024,32,profile_id=str(uuid.uuid4()))==[]
    c.execute(text('UPDATE document_versions SET index_identity=\'legacy/v1\' WHERE id=:v'),{'v':receipt['version_id']})
    assert repo.get_retrieval_chunks(Scope.from_ids([kb['id']]),children)=={}
    assert repo.read_parent_contexts(Scope.from_ids([kb['id']]),[i.chunk for i in result.items])=={}
    # Historical citation readback remains available after index replacement.
    assert repo.get_chunk(children[0]).version_id==receipt['version_id']


def test_pg_parent_rejects_cross_kb_link(isolated_database,tmp_path):
    engine,c=isolated_database
    repo=PostgresKnowledgeRepository(engine,ContentAddressedStorage(tmp_path))
    kb,receipt,body,children,parents=seed_p3_fixture(repo,c)
    other,_,_,_,_=seed_p3_fixture(repo,c)
    scope=Scope.from_ids([kb['id']])
    seeds=list(repo.get_retrieval_chunks(scope,children).values())
    assert repo.read_parent_contexts(scope,seeds)
    # FK permits same-version rows; application must additionally bind KB/doc.
    c.execute(text('UPDATE chunks SET knowledge_base_id=:kb WHERE id=:id'),{'kb':other['id'],'id':parents[0]})
    assert all(parent.chunk_id!=parents[0] for parent in repo.read_parent_contexts(scope,seeds).values())


def test_pg_neighbor_read_proves_current_child_identity(isolated_database,tmp_path):
    engine,c=isolated_database
    repo=PostgresKnowledgeRepository(engine,ContentAddressedStorage(tmp_path))
    kb,receipt,body,children,_=seed_p3_fixture(repo,c)
    scope=Scope.from_ids([kb['id']])
    seeds=repo.get_retrieval_chunks(scope,children)
    original=seeds[children[1]]
    rows=repo.read_context_rows(scope,[original])
    assert {r.offset for r in rows.rows}=={-1,0,1}
    assert all(r.metadata.chunk.index_identity==repo.chunking_config.identity for r in rows.rows)
    c.execute(text("UPDATE chunks SET index_identity='legacy/v1' WHERE id=:id"),{'id':children[2]})
    rows=repo.read_context_rows(scope,[original])
    assert 1 not in {r.offset for r in rows.rows}
