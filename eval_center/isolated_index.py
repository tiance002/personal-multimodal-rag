"""Read actual local PostgreSQL indexes, guarded against normal business DBs."""
from __future__ import annotations

import hashlib
import re

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from eval_center.gold import SourceSpan
from eval_center.runtime import index_fingerprint, verify_indexed_chunking
from eval_center.verification import ExperimentInvalidError

_DATABASE=re.compile(r'rag_eval_trust_[a-z0-9_]{1,40}\Z')


def guarded_database_url(database_url):
    url=make_url(database_url)
    if url.get_backend_name()!='postgresql' or url.host not in ('localhost','127.0.0.1','::1') or not _DATABASE.fullmatch(url.database or ''):
        raise ExperimentInvalidError('nonisolated_database')
    return url


def create_isolated_database(admin_url,name):
    """Create a dedicated database; never drop, truncate or replace anything."""
    url=make_url(admin_url)
    target=url.set(database=name)
    guarded_database_url(target)
    if url.database!='postgres': raise ExperimentInvalidError('admin_database_must_be_postgres')
    engine=create_engine(url,isolation_level='AUTOCOMMIT')
    try:
        with engine.connect() as connection:
            if connection.execute(text('SELECT current_database()')).scalar_one()!='postgres':
                raise ExperimentInvalidError('admin_database_mismatch')
            exists=connection.execute(text('SELECT 1 FROM pg_database WHERE datname=:name'),{'name':name}).first()
            if exists is None: connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally: engine.dispose()
    return target.render_as_string(hide_password=False)


def read_index_snapshot(repository,kb_id,bindings,embedding_model):
    """Bindings come from upload receipts and frozen source hashes, not titles."""
    url=guarded_database_url(repository.engine.url)
    with repository.engine.connect() as connection:
        if connection.execute(text('SELECT current_database()')).scalar_one()!=url.database:
            raise ExperimentInvalidError('actual_database_mismatch')
        documents=[dict(row) for row in connection.execute(text('''
            SELECT d.id::text AS document_id,d.media_type,v.id::text AS version_id,v.source_sha256,
                   v.storage_key,v.normalized_content_sha256,v.index_status
            FROM documents d JOIN document_versions v ON v.id=d.active_version_id
            WHERE d.knowledge_base_id=CAST(:kb AS uuid) AND d.deleted_at IS NULL
            ORDER BY d.id'''),{'kb':kb_id}).mappings()]
        chunks=[dict(row) for row in connection.execute(text('''
            SELECT c.id::text AS chunk_id,c.document_id::text AS document_id,c.version_id::text AS version_id,
                   c.chunk_index,c.content,c.content_sha256,c.start_pos AS start,c.end_pos AS end,c.locator,
                   ce.embedding::text AS embedding,p.model_name,p.dimension,p.fingerprint
            FROM chunks c JOIN documents d ON d.id=c.document_id AND d.active_version_id=c.version_id
            LEFT JOIN chunk_embeddings ce ON ce.chunk_id=c.id
            LEFT JOIN embedding_profiles p ON p.id=ce.profile_id
            WHERE c.knowledge_base_id=CAST(:kb AS uuid) AND d.deleted_at IS NULL
            ORDER BY c.document_id,c.chunk_index'''),{'kb':kb_id}).mappings()]
    if set(bindings)!={row['document_id'] for row in documents} or not chunks:
        raise ExperimentInvalidError('scope_index_binding_mismatch')
    if len({row['chunk_id'] for row in chunks})!=len(chunks):
        raise ExperimentInvalidError('ambiguous_embedding_profile')
    by_document={row['document_id']:row for row in documents}
    spans={}
    fingerprints=[]
    embedding_count=0
    for document in documents:
        binding=bindings[document['document_id']]
        raw=repository.storage.path_for(document['storage_key']).read_bytes()
        if document['index_status']!='ready' or hashlib.sha256(raw).hexdigest()!=binding['source_version'] or document['source_sha256']!=binding['source_version']:
            raise ExperimentInvalidError('source_version_binding_mismatch')
        normalized=repository.parsers.parse(repository.storage.path_for(document['storage_key']),
            document['media_type'],document['document_id'],document['version_id'])
        if document['normalized_content_sha256']!=normalized.content_sha256 or normalized.markdown_content!=binding['text']:
            raise ExperimentInvalidError('source_coordinates_mismatch')
        verify_indexed_chunking(normalized,[row for row in chunks if row['document_id']==document['document_id']],repository)
    for chunk in chunks:
        if chunk['embedding'] is None or chunk['model_name']!=embedding_model or chunk['dimension']!=1024:
            raise ExperimentInvalidError('incomplete_actual_embedding_index')
        embedding_count+=1
        document=by_document[chunk['document_id']]
        binding=bindings[chunk['document_id']]
        locator=chunk['locator']
        spans[chunk['chunk_id']]=SourceSpan(binding['document_id'],binding['source_version'],chunk['start'],chunk['end'],
            kind=locator['kind'],page=locator.get('page'),asset_id=locator.get('asset_id'))
        fingerprints.append({'document_id':binding['document_id'],'source_version':binding['source_version'],
            'normalized_sha256':document['normalized_content_sha256'],'start':chunk['start'],'end':chunk['end'],
            'content_sha256':chunk['content_sha256'],'locator':{key:locator.get(key) for key in ('kind','page','start','end','asset_id')},
            'embedding_digest':hashlib.sha256(chunk['embedding'].encode()).hexdigest(),
            'embedding_profile':{'model':chunk['model_name'],'dimension':chunk['dimension'],'fingerprint':chunk['fingerprint']}})
    return {'spans':spans,'chunks':chunks,'index_version':index_fingerprint(fingerprints),
            'index_counts':{'documents':len(documents),'chunks':len(chunks),'embeddings':embedding_count}}
