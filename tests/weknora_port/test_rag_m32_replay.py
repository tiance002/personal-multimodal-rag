"""Bounded integrity checks for the offline M3.2 evidence replay harness."""
import asyncio
from copy import deepcopy
import socket
from types import SimpleNamespace as NS

import pytest
from scripts.rag_m32_threshold_replay import (
    SavedRepository, SearchResult, disable_network, snapshot_differences,
    validate_raw_results,
)


def context(kbs=('kb',), docs=('doc',)):
    return NS(scope=NS(knowledge_base_ids=kbs, document_ids=docs))


def row(**changes):
    return SearchResult(**dict(id='chunk', content='source', score=0,
                               knowledge_base_id='kb', knowledge_id='doc', **changes))


def test_missing_snapshot_row_is_not_silently_empty():
    with pytest.raises(PermissionError, match='MISSING_SNAPSHOT_ROWS'):
        asyncio.run(SavedRepository({}).get_chunks(['missing'], context()))


@pytest.mark.parametrize('kbs,docs', [(('other',), ('doc',)), (('kb',), ('other',))])
def test_repository_rejects_kb_and_document_scope(kbs, docs):
    with pytest.raises(PermissionError, match='SNAPSHOT_SCOPE_IDENTITY_MISMATCH'):
        asyncio.run(SavedRepository({'chunk': row()}).get_chunks(['chunk'], context(kbs, docs)))


@pytest.mark.parametrize('field', ['knowledge_base_id', 'knowledge_id', 'chunk_type'])
def test_historical_identity_change_is_reported(field):
    historical = row()
    current = deepcopy(historical)
    setattr(current, field, 'changed')
    assert snapshot_differences(historical, current) == [field]


@pytest.mark.parametrize('raw,match', [
    ([{'index': -1, 'relevance_score': .2}], 'OUT_OF_RANGE'),
    ([{'index': 1, 'relevance_score': .2}], 'OUT_OF_RANGE'),
    ([{'index': 0, 'relevance_score': .2}, {'index': 0, 'relevance_score': .1}], 'DUPLICATE'),
    ([{'index': 0}], 'MISSING_RAW_SCORE'),
])
def test_invalid_raw_response_is_rejected(raw, match):
    with pytest.raises(ValueError, match=match):
        validate_raw_results(raw, 1)


def test_network_connect_is_blocked(monkeypatch):
    monkeypatch.setattr(socket.socket, 'connect', socket.socket.connect)
    monkeypatch.setattr(socket.socket, 'connect_ex', socket.socket.connect_ex)
    disable_network()
    with socket.socket() as client:
        with pytest.raises(RuntimeError, match='OFFLINE_NETWORK_FORBIDDEN'):
            client.connect(('127.0.0.1', 1))
        with pytest.raises(RuntimeError, match='OFFLINE_NETWORK_FORBIDDEN'):
            client.connect_ex(('127.0.0.1', 1))
