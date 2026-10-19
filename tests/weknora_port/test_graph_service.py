"""Graph index service contracts: synthetic Provider/SQL doubles, no cloud."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from backend.weknora_port.graph.service import GraphIndexer, GraphIndexError
from backend.weknora_port.graph_cli import arguments
from backend.weknora_port.models.types import ChatResponse, TokenUsage, ProviderError
from backend.weknora_port.storage.postgres import PostgresRetriever, IsolationPolicy
from tests.weknora_port.test_graph_storage_pg import sample, GraphConnection, FakeCursor, source_row


def model_text():
    return json.dumps({'entities': [
        {'key': 'n1', 'name': 'Alpha Research', 'type': 'organization', 'aliases': [], 'anchor': True, 'qualifier': ''},
        {'key': 'n2', 'name': 'Birch Campus', 'type': 'place', 'aliases': [], 'anchor': False, 'qualifier': ''}],
        'relations': [{'source': 'n1', 'target': 'n2', 'predicate': 'operates at',
            'quote': 'Alpha Research operates at Birch Campus.', 'polarity': 'affirmed', 'time': ''}]})


class IndexConnection(GraphConnection):
    def __init__(self, batch):
        super().__init__(batch)
        self.candidates = [{'id': batch.source.chunk_id}]
        self.document = dict(id=batch.source.document_id, tenant_id=batch.source.tenant_id,
            knowledge_base_id=batch.source.knowledge_base_id,
            metadata={'source_version': batch.source.source_version}, file_hash='a' * 64)

    async def execute(self, query, params=()):
        if query.startswith('SELECT k.id,k.tenant_id'):
            self.calls.append((query, params))
            return FakeCursor([self.document])
        if query.startswith('SELECT id FROM chunks WHERE tenant_id'):
            self.calls.append((query, params))
            return FakeCursor(self.candidates)
        return await super().execute(query, params)


class Model:
    config = SimpleNamespace(model_id='synthetic-model', model_type='KnowledgeQA',
                             provider='synthetic-provider', model='synthetic-chat')
    def __init__(self, response=None):
        self.response = response or ChatResponse(model_text(), finish_reason='stop',
            usage=TokenUsage(100, 30, 130, 'REPORTED'), model='synthetic-chat')
        self.requests = []
        self.fail = None
        self.sent = []
        self.seen = set()

    async def chat(self, messages, options, context):
        self.requests.append((messages, options, context))
        if self.fail:
            raise self.fail
        if context.request_id in self.seen:
            raise ProviderError('MODEL_CALL_ID_REUSE_DENIED')
        self.seen.add(context.request_id)
        self.sent.append(context.request_id)
        return self.response


async def fixture(response=None):
    batch = sample()
    connection = IndexConnection(batch)
    guard = PostgresRetriever(connection, IsolationPolicy('new', frozenset({'old'})))
    await guard.verify_identity()
    class Leases:
        @asynccontextmanager
        async def lease(self, *, writable_knowledge_base_ids=frozenset()):
            old = guard.writable_knowledge_base_ids
            guard.writable_knowledge_base_ids = writable_knowledge_base_ids
            try:
                yield guard
            finally:
                guard.writable_knowledge_base_ids = old
    model = Model(response)
    class Registry:
        def __init__(self):
            self.closed = False
            self.resolutions = []
        async def chat(self, model_id, context):
            self.resolutions.append((model_id, context))
            return model
        async def close(self):
            self.closed = True
    registry = Registry()
    app = SimpleNamespace(principal=SimpleNamespace(tenant_id=batch.source.tenant_id,
        subject_id=batch.source.owner_id), leases=Leases(), registry_for_context=lambda: registry)
    from backend.weknora_port.models.types import CallContext, TrustedScope
    context = CallContext('synthetic-owner-operation', TrustedScope(batch.source.owner_id,
        (batch.source.knowledge_base_id,), (batch.source.document_id,)))
    return batch, connection, model, registry, GraphIndexer(app), context


async def index(indexer, batch, context):
    return await indexer.index_document(batch.source.knowledge_base_id, batch.source.document_id,
                                        'synthetic-model', context)


def test_index_uses_registry_existing_gate_and_current_full_source_then_persists():
    async def run():
        batch, connection, model, registry, indexer, context = await fixture()
        result = await index(indexer, batch, context)
        assert result['status'] == 'EXTRACTED' and result['edges'] == 1
        assert result['provider_calls'] == 1 and result['reported_tokens'] == 130
        assert registry.closed
        messages, options, actual = model.requests[0]
        user = json.loads(messages[1].content)
        assert user == result['chunks'][0]['source']
        assert user['content'] == batch.source.content
        assert options.thinking is False and options.temperature == 0
        assert options.max_completion_tokens == 2048
        assert actual.request_id == context.request_id + '/graph-extract-' + batch.source.chunk_id
        assert actual.scope == context.scope and len(connection.edges) == 1
        proof = next(iter(connection.evidence.values()))
        producer = json.loads(proof['producer'])
        assert producer['model_id'] == 'synthetic-model'
        assert producer['request_id'] == actual.request_id and producer['status'] == 'completed'
        assert not any(sql.startswith(('UPDATE chunks', 'UPDATE embeddings', 'INSERT INTO embeddings'))
                       for sql, _ in connection.calls)
    asyncio.run(run())


def test_index_records_original_model_quote_and_derived_source_mapping_without_extra_call():
    async def run():
        raw = json.loads(model_text())
        raw['relations'][0]['quote'] = 'operates at Birch Campus'
        text = json.dumps(raw)
        response = ChatResponse(text, finish_reason='stop',
            usage=TokenUsage(100, 30, 130, 'REPORTED'), model='synthetic-chat')
        batch, connection, model, _, indexer, context = await fixture(response)
        result = await index(indexer, batch, context)
        assert result['edges'] == 1 and len(model.requests) == result['provider_calls'] == 1
        record = result['chunks'][0]
        assert record['raw_response'] == text
        mapping, = record['quote_completions']
        assert mapping['original_quote'] == 'operates at Birch Campus'
        assert mapping['kind'] == 'SOURCE_SENTENCE_EXTENSION_NOT_LLM_QUOTE'
        assert batch.source.content[mapping['derived_quote_start']:mapping['derived_quote_end']] == mapping['derived_quote']
        producer = json.loads(next(iter(connection.evidence.values()))['producer'])
        assert producer['quote_completions'] == [mapping]
        assert producer['raw_response_sha256'] == record['raw_response_sha256']
    asyncio.run(run())


@pytest.mark.parametrize('scope_change', [('knowledge_base_ids', ('other',)), ('document_ids', ()),
                                          ('owner_id', 'other')])
def test_index_rejects_borrowed_scope_before_registry(scope_change):
    async def run():
        batch, _, model, registry, indexer, context = await fixture()
        field, value = scope_change
        with pytest.raises(PermissionError, match='INDEX_SCOPE'):
            await index(indexer, batch, replace(context, scope=replace(context.scope, **{field: value})))
        assert not registry.resolutions and not model.requests
    asyncio.run(run())


@pytest.mark.parametrize('case', ['many_chunks', 'oversized_source', 'uninstalled_schema', 'disabled_chunk', 'wrong_document'])
def test_preflight_failures_never_contact_provider(case):
    async def run():
        batch, connection, model, registry, indexer, context = await fixture()
        if case == 'many_chunks':
            connection.candidates = [{'id': str(i)} for i in range(13)]
        elif case == 'oversized_source':
            connection.source['content'] = 'x' * 16385
            connection.source['end_at'] = 16385
        elif case == 'uninstalled_schema':
            connection.schema = False
        elif case == 'disabled_chunk':
            connection.source['is_enabled'] = False
        else:
            connection.document['id'] = 'other'
        with pytest.raises((ValueError, PermissionError)):
            await index(indexer, batch, context)
        assert not model.requests and not registry.resolutions
    asyncio.run(run())


@pytest.mark.parametrize('case', ['length', 'wrong_model', 'unknown_usage', 'invalid_json', 'cloud_off', 'unknown_send'])
def test_unfinished_or_invalid_attempt_stops_without_persist_or_retry(case):
    async def run():
        batch, connection, model, registry, indexer, context = await fixture()
        if case == 'length':
            model.response = replace(model.response, finish_reason='length')
        elif case == 'wrong_model':
            model.response = replace(model.response, model='different-chat')
        elif case == 'unknown_usage':
            model.response = replace(model.response, usage=TokenUsage())
        elif case == 'invalid_json':
            model.response = replace(model.response, content='not JSON')
        elif case == 'cloud_off':
            model.fail = ProviderError('MODEL_CLOUD_DISABLED')
        else:
            model.fail = ProviderError('MODEL_CALL_TIMEOUT', sent=True)
        with pytest.raises(GraphIndexError) as caught:
            await index(indexer, batch, context)
        result = caught.value.result
        assert result['status'] == 'STOPPED' and len(model.requests) == 1
        assert not connection.evidence and not connection.nodes and not connection.edges
        assert registry.closed
        if case == 'unknown_usage':
            assert result['unknown_usage_calls'] == 1 and result['reported_tokens'] == 0
            assert result['chunks'][0]['usage']['total_tokens'] is None
        elif case == 'cloud_off':
            assert result['provider_calls'] == 0 and not model.sent
        elif case == 'unknown_send':
            assert result['provider_calls'] == 1 and result['unknown_provider_calls'] == 1
            assert result['chunks'][0]['usage'] is None
        else:
            assert result['provider_calls'] == 1 and result['reported_tokens'] == 130
    asyncio.run(run())


def test_empty_graph_is_persisted_as_empty_and_not_claimed_successful_build():
    async def run():
        response = ChatResponse('{"entities":[],"relations":[]}', finish_reason='stop',
            usage=TokenUsage(100, 3, 103, 'REPORTED'), model='synthetic-chat')
        batch, connection, _, _, indexer, context = await fixture(response)
        result = await index(indexer, batch, context)
        assert result['status'] == 'EMPTY_NO_EDGES' and result['edges'] == 0
        assert len(connection.evidence) == 1 and not connection.edges
    asyncio.run(run())


def test_settlement_default_sent_false_requires_ledger_instead_of_claiming_zero_sends():
    async def run():
        batch, connection, model, _, indexer, context = await fixture()
        # The real LocalExecution settlement wrapper uses this error with its
        # default sent=False even when the Provider transport already sent.
        model.fail = ProviderError('LOCAL_EXECUTION_SETTLEMENT_FAILED')
        with pytest.raises(GraphIndexError) as caught:
            await index(indexer, batch, context)
        result = caught.value.result
        assert result['provider_calls'] is None and result['unknown_provider_calls'] is None
        assert result['call_count_status'] == 'REQUIRES_LEDGER_RECONCILIATION'
        assert result['ledger_lookup']['request_id'] == context.request_id + '/graph-extract-' + batch.source.chunk_id
        assert result['ledger_lookup']['table'] == 'port_provider_calls'
        assert result['chunks'][0]['sent'] is None
        assert result['chunks'][0]['reported_error_sent'] is False
        assert result['chunks'][0]['usage'] is None
        assert len(model.requests) == 1 and not connection.evidence
    asyncio.run(run())


def test_repeated_operation_keeps_gate_identity_and_does_not_generate_new_request_id():
    async def run():
        batch, _, model, _, indexer, context = await fixture()
        await index(indexer, batch, context)
        with pytest.raises(GraphIndexError, match='REUSE_DENIED'):
            await index(indexer, batch, context)
        assert len(model.sent) == 1 and len(model.requests) == 2
        assert model.requests[0][2].request_id == model.requests[1][2].request_id
    asyncio.run(run())


def test_cli_requires_explicit_existing_config_output_and_operation_identity():
    args = arguments(['--config', 'E:/existing/local.json', '--output', 'E:/evidence/new.json',
        'index', '--kb', 'k', '--document', 'd', '--model-id', 'm', '--request-id', 'op'])
    assert args.request_id == 'op' and args.model_id == 'm'
    with pytest.raises(SystemExit):
        arguments(['--config', 'E:/existing/local.json', '--output', 'E:/evidence/new.json',
                   'index', '--kb', 'k', '--document', 'd', '--model-id', 'm'])
