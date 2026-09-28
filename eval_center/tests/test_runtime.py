from types import SimpleNamespace

import pytest

from backend.app.domain.models import NormalizedDocument
from backend.app.domain.chunking import chunk_document
from eval_center.runtime import effective_configuration, verify_indexed_chunking, index_fingerprint
from eval_center.verification import ExperimentInvalidError


def test_effective_configuration_reads_actual_instances_and_rejects_false_top_k():
    retriever=SimpleNamespace(effective_config=lambda:{'top_k':3,'candidate_k':16,'rrf_k':70})
    store=SimpleNamespace(max_chunk_chars=400,chunk_overlap=40)
    context=SimpleNamespace(max_chars=6000)
    actual=effective_configuration(retriever,store,context)
    assert actual=={'top_k':3,'candidate_k':16,'rrf_k':70,'chunk_size':400,
                    'chunk_overlap':40,'context_budget_chars':6000}
    declared={**actual,'top_k':10}
    with pytest.raises(ExperimentInvalidError,match='effective_config_mismatch'):
        effective_configuration(retriever,store,context,declared=declared)


def test_existing_index_must_reproduce_actual_ingester_configuration():
    document=NormalizedDocument(document_id='doc',version_id='version',title='x',media_type='text/plain',
        markdown_content='a'*1100,content_sha256='a'*64,parser_version='text/v1')
    stored=[chunk.model_dump() for chunk in chunk_document(document,max_chars=400,overlap=40)]
    store=SimpleNamespace(max_chunk_chars=400,chunk_overlap=40)
    verify_indexed_chunking(document,stored,store)
    store.max_chunk_chars=700
    with pytest.raises(ExperimentInvalidError,match='indexed_chunking_mismatch'):
        verify_indexed_chunking(document,stored,store)


def test_index_fingerprint_tracks_embedding_and_locator_not_transient_chunk_uuid():
    rows=[{'document_id':'source-A','source_version':'a'*64,'normalized_sha256':'b'*64,
           'start':0,'end':3,'content_sha256':'c'*64,'locator':{'kind':'text','start':0,'end':3},
           'embedding_digest':'d'*64,'embedding_profile':'bge-m3', 'chunk_id':'random-uuid'}]
    first=index_fingerprint(rows)
    rows[0]['chunk_id']='another-uuid'
    assert index_fingerprint(rows)==first
    rows[0]['embedding_digest']='e'*64
    assert index_fingerprint(rows)!=first
