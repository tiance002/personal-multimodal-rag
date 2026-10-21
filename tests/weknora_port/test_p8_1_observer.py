"""Offline observation safety checks; tiny fakes are not real model evidence."""
import asyncio
from dataclasses import asdict
import hashlib
import json
from types import SimpleNamespace

import pytest

from backend.weknora_port.models.types import CallContext, TrustedScope, RerankResponse, RerankResult
from backend.weknora_port.retrieval.types import SearchResult
from scripts.weknora_port_p8_1_observed_api import RerankRegistryObserver, rows


def test_rerank_observer_forwards_once_preserves_identity_and_records_raw_scores(tmp_path):
    query = 'original query'
    documents = ['raw first', 'raw second']
    context = CallContext('server-run/rerank', TrustedScope('owner', ('kb',)))
    response = RerankResponse((RerankResult(1, .123456789), RerankResult(0, .987654321)))
    calls, lookups = [], []

    async def rerank_model(given_query, given_documents, given_context):
        calls.append((given_query, given_documents, given_context))
        assert given_query is query
        assert given_documents is documents
        assert given_context is context
        return response

    original_model = SimpleNamespace(rerank=rerank_model)

    async def lookup(model_id, supplied_context):
        lookups.append((model_id, supplied_context))
        return original_model

    observer = RerankRegistryObserver(SimpleNamespace(rerank=lookup), tmp_path, 'server-run')

    async def invoke():
        model = await observer.rerank('model-id', context)
        return await model.rerank(query, documents, context)

    result = asyncio.run(invoke())
    assert result is response
    assert len(calls) == len(lookups) == 1
    assert lookups[0][1] is context
    filename = hashlib.sha256(b'server-run').hexdigest() + '.json'
    evidence = json.loads((tmp_path / 'rerank' / filename).read_text(encoding='utf-8'))
    assert evidence['calls'][0]['query'] == query
    assert evidence['calls'][0]['documents'] == documents
    assert evidence['calls'][0]['results'] == [asdict(row) for row in response.results]
    assert evidence['calls'][0]['status'] == 'NORMAL_RESPONSE_RECEIVED'


def test_rerank_observer_propagates_same_exception_without_retry(tmp_path):
    failure = RuntimeError('offline sentinel')
    calls = []
    context = CallContext('failed-run/rerank', TrustedScope('owner', ('kb',)))

    async def model_call(*args):
        calls.append(args)
        raise failure

    async def lookup(*args):
        return SimpleNamespace(rerank=model_call)

    observer = RerankRegistryObserver(SimpleNamespace(rerank=lookup), tmp_path, 'failed-run')

    async def invoke():
        model = await observer.rerank('model-id', context)
        return await model.rerank('query', ['document'], context)

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(invoke())
    assert caught.value is failure
    assert len(calls) == 1
    path = tmp_path / 'rerank' / (hashlib.sha256(b'failed-run').hexdigest() + '.json')
    evidence = json.loads(path.read_text(encoding='utf-8'))
    assert len(evidence['calls']) == 1
    assert evidence['calls'][0]['status'] == 'NORMAL_INVOCATION_ERROR'
    assert evidence['calls'][0]['error_type'] == 'RuntimeError'


def test_stage_row_snapshot_is_detached_from_original_nested_metadata():
    original = SearchResult('chunk', 'actual original', .7, knowledge_base_id='kb', knowledge_id='doc',
        metadata={'origin': 'original', 'nested': {'value': ['unchanged']}},
        chunk_metadata={'source_version': 'v1'}, sub_chunk_ids=['child'])
    before = asdict(original)
    snapshot = rows([original, None])
    assert snapshot == [before]
    assert asdict(original) == before
    snapshot[0]['metadata']['nested']['value'].append('snapshot-only')
    snapshot[0]['sub_chunk_ids'].clear()
    snapshot[0]['chunk_metadata']['source_version'] = 'snapshot-only'
    assert asdict(original) == before
