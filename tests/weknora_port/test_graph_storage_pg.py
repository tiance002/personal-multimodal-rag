"""S6 storage boundaries: SQL doubles plus explicitly opt-in real PostgreSQL.

All extraction records here are synthetic fixtures. No Provider is invoked.
The real test requires an already-installed graph schema and rolls back its
unique fixture KB; it never installs schema or rewrites historical sources.
"""
import asyncio
from dataclasses import asdict, replace
import json
import uuid

import pytest

from backend.weknora_port.graph.types import GraphSource, GraphNode, GraphEdge, GraphBatch
from backend.weknora_port.graph.extraction import parse_extraction
from backend.weknora_port.models.types import CallContext, TrustedScope
from backend.weknora_port.storage.graph import PostgresGraphRepository, _source_id
from backend.weknora_port.storage.graph_migrate import apply_graph_schema
from backend.weknora_port.storage.postgres import IsolationPolicy, PostgresRetriever
from tests.weknora_port.test_storage import FakeConnection, FakeCursor


def sample(*, tenant=1, owner='graph-owner', kb='graph-kb', doc='graph-doc', version='v1', chunk='graph-chunk'):
    text = 'Alpha Research operates at Birch Campus.'
    source = GraphSource(tenant, owner, kb, doc, version, chunk, text, 0, len(text))
    payload = {'entities': [
        {'key': 'n1', 'name': 'Alpha Research', 'type': 'organization', 'aliases': [], 'anchor': True, 'qualifier': ''},
        {'key': 'n2', 'name': 'Birch Campus', 'type': 'place', 'aliases': [], 'anchor': False, 'qualifier': ''}],
        'relations': [{'source': 'n1', 'target': 'n2', 'predicate': 'operates at', 'quote': text,
                       'polarity': 'affirmed', 'time': ''}]}
    return parse_extraction(source, json.dumps(payload), 'synthetic-extraction-' + chunk)


def source_row(source, **updates):
    row = dict(id=source.chunk_id, tenant_id=source.tenant_id,
               knowledge_base_id=source.knowledge_base_id, knowledge_id=source.document_id,
               content=source.content, chunk_type='text', chunk_index=0,
               start_at=source.start_at, end_at=source.end_at, is_enabled=True,
               metadata={'source_version': source.source_version},
               knowledge_metadata={'source_version': source.source_version},
               knowledge_enable_status='enabled', knowledge_parse_status='completed')
    row.update(updates)
    return row


class GraphConnection(FakeConnection):
    def __init__(self, batch):
        super().__init__()
        self.source = source_row(batch.source)
        self.evidence, self.nodes, self.edges = {}, {}, {}
        self.schema = True

    async def execute(self, query, params=()):
        self.calls.append((query, params))
        if 'pg_control_system' in query:
            return FakeCursor([{'database': 'weknora_python_port_20261013', 'system_identifier': 'new'}])
        if 'to_regclass' in query:
            return FakeCursor([{key: key if self.schema else None for key in ('evidence', 'nodes', 'edges')}])
        if query.startswith('SELECT c.id FROM chunks'):
            return FakeCursor([{'id': self.source['id']}])
        if 'FROM chunks c' in query:
            return FakeCursor([self.source])
        if query.startswith('INSERT INTO port_graph_evidence'):
            names = ('id', 'tenant_id', 'owner_id', 'knowledge_base_id', 'document_id', 'source_version',
                     'chunk_id', 'content_sha256', 'source_content', 'start_at', 'end_at', 'page_number',
                     'extraction_id', 'producer', 'payload')
            row = dict(zip(names, params)); row['payload'] = json.loads(row['payload'])
            self.evidence[row['id']] = row
        elif query.startswith('DELETE FROM port_graph_edges'):
            self.edges = {key: row for key, row in self.edges.items() if row['evidence_id'] != params[0]}
        elif query.startswith('DELETE FROM port_graph_nodes'):
            self.nodes = {key: row for key, row in self.nodes.items() if row['evidence_id'] != params[0]}
        elif query.startswith('INSERT INTO port_graph_nodes'):
            self.nodes[params[0]] = {'id': params[0], 'evidence_id': params[1], 'payload': json.loads(params[-1])}
        elif query.startswith('INSERT INTO port_graph_edges'):
            self.edges[params[0]] = {'id': params[0], 'evidence_id': params[1], 'payload': json.loads(params[-1])}
        elif query.startswith('SELECT ge.*'):
            return FakeCursor(list(self.evidence.values()))
        elif query.startswith('SELECT id,evidence_id,payload FROM port_graph_nodes'):
            return FakeCursor(list(self.nodes.values()))
        elif query.startswith('SELECT id,evidence_id,payload FROM port_graph_edges'):
            return FakeCursor(list(self.edges.values()))
        return FakeCursor([])


async def mock_store(batch=None, *, writable=True):
    batch = batch or sample()
    connection = GraphConnection(batch)
    guard = PostgresRetriever(connection, IsolationPolicy('new', frozenset({'old'})),
        writable_knowledge_base_ids=frozenset({batch.source.knowledge_base_id}) if writable else frozenset())
    await guard.verify_identity()
    async def tenant(owner):
        if owner != batch.source.owner_id:
            raise PermissionError('SYNTHETIC_OWNER_REJECTED')
        return batch.source.tenant_id
    context = CallContext('synthetic-graph', TrustedScope(batch.source.owner_id,
        (batch.source.knowledge_base_id,), (batch.source.document_id,)))
    return batch, connection, PostgresGraphRepository(guard, tenant), context


def test_mock_storage_roundtrip_replaces_contribution_without_source_writes():
    async def run():
        batch, connection, repo, context = await mock_store()
        await repo.replace_chunk(batch, context, 'synthetic-fixture')
        assert await repo.load_graph(context) == (batch,)
        await repo.replace_chunk(replace(batch, edges=()), context, 'synthetic-fixture')
        result = await repo.load_graph(context)
        assert result[0].edges == () and result[0].nodes == batch.nodes
        forbidden = ('INSERT INTO chunks', 'UPDATE chunks', 'DELETE FROM chunks',
                     'INSERT INTO embeddings', 'UPDATE embeddings', 'DELETE FROM embeddings')
        assert not any(sql.startswith(forbidden) for sql, _ in connection.calls)
        for sql, params in connection.calls:
            assert sql.count('%s') == len(params)
    asyncio.run(run())


@pytest.mark.parametrize('field,value', [('tenant_id', 2), ('owner_id', 'other'),
    ('knowledge_base_id', 'other'), ('document_id', 'other')])
def test_mock_write_rejects_wrong_source_scope_before_graph_mutation(field, value):
    async def run():
        batch, connection, repo, context = await mock_store()
        with pytest.raises(PermissionError, match='SCOPE_MISMATCH'):
            await repo.replace_chunk(replace(batch, source=replace(batch.source, **{field: value})), context, 'test')
        assert not any(sql.startswith(('INSERT', 'DELETE')) for sql, _ in connection.calls)
    asyncio.run(run())


@pytest.mark.parametrize('change', [
    {'is_enabled': False}, {'deleted_at': 'deleted'}, {'knowledge_enable_status': 'disabled'},
    {'knowledge_parse_status': 'failed'}, {'knowledge_metadata': {'source_version': 'new-version'}},
    {'content': 'Changed body'}, {'chunk_type': 'summary'}, {'content_revision': 1}])
def test_mock_current_source_is_required_for_write(change):
    async def run():
        batch, connection, repo, context = await mock_store()
        connection.source.update(change)
        with pytest.raises(PermissionError):
            await repo.replace_chunk(batch, context, 'test')
        assert not any(sql.startswith('INSERT INTO port_graph') for sql, _ in connection.calls)
    asyncio.run(run())


def test_mock_forged_quote_page_and_nonwritable_scope_are_rejected():
    async def run():
        batch, connection, repo, context = await mock_store()
        edge = replace(batch.edges[0], quote='Not a source quote')
        with pytest.raises(ValueError, match='SOURCE_PROOF'):
            await repo.replace_chunk(replace(batch, edges=(edge,)), context, 'test')
        with pytest.raises(PermissionError, match='PAGE_UNVERIFIED'):
            await repo.replace_chunk(replace(batch, source=replace(batch.source, page_number=9)), context, 'test')
        _, _, readonly, _ = await mock_store(writable=False)
        with pytest.raises(PermissionError, match='WRITE_SCOPE'):
            await readonly.replace_chunk(batch, context, 'test')
    asyncio.run(run())


def test_mock_uninstalled_schema_is_empty_but_corrupt_source_is_not_silenced():
    async def run():
        batch, connection, repo, context = await mock_store()
        connection.schema = False
        assert await repo.load_graph(context) == ()
        assert repo.last_status == 'GRAPH_SCHEMA_UNAVAILABLE'
        connection.schema = True
        await repo.replace_chunk(batch, context, 'test')
        connection.evidence[_source_id(batch.source)]['content_sha256'] = '0' * 64
        with pytest.raises(PermissionError, match='STORED_SOURCE_IDENTITY'):
            await repo.load_graph(context)
    asyncio.run(run())


def test_mock_current_loader_rejects_stale_body_and_excludes_concurrently_disabled_source():
    async def run():
        batch, connection, repo, context = await mock_store()
        await repo.replace_chunk(batch, context, 'test')
        connection.source['content'] = 'Different source under same version'
        with pytest.raises(PermissionError, match='SHA_MISMATCH'):
            await repo.load_graph(context)
        connection.source = source_row(batch.source, is_enabled=False)
        assert await repo.load_graph(context) == ()
        assert len(connection.evidence) == 1  # historical evidence retained
    asyncio.run(run())


def test_mock_migration_verifies_identity_and_does_not_edit_prior_versions():
    async def run():
        batch, connection, repo, context = await mock_store()
        digest = await apply_graph_schema(repo.guard)
        assert len(digest) == 64
        sql = '\n'.join(query for query, _ in connection.calls)
        assert 'CREATE TABLE IF NOT EXISTS port_graph_evidence' in sql
        assert 'UPDATE chunks' not in sql and 'port_token_calls' not in sql
        assert ('016_graph', digest) in [params for _, params in connection.calls]
    asyncio.run(run())


def test_actual_pg_graph_source_lifecycle_and_scope_rollback():
    """REAL PostgreSQL, synthetic extracted edges, zero cloud calls."""
    from tests.weknora_port.test_memory_storage_pg import approved_dsn, connection, actual_pg_run
    dsn = approved_dsn()
    async def run():
        conn, initial_guard = await connection(dsn)
        try:
            tables = await (await conn.execute("SELECT to_regclass('port_graph_evidence') AS graph")).fetchall()
            if not tables[0]['graph']:
                pytest.skip('S6 graph migration must be explicitly installed by owner-approved runner first')
            kb, doc, chunk = (str(uuid.uuid4()) for _ in range(3))
            batch = sample(tenant=943621, owner='s6-synthetic-owner-' + kb, kb=kb, doc=doc, chunk=chunk)
            source = batch.source
            guard = PostgresRetriever(conn, initial_guard.isolation, writable_knowledge_base_ids=frozenset({kb}))
            await guard.verify_identity()
            async def tenant(owner):
                if owner != source.owner_id:
                    raise PermissionError('SYNTHETIC_OWNER_REJECTED')
                return source.tenant_id
            repo = PostgresGraphRepository(guard, tenant)
            context = CallContext('synthetic-s6-' + kb, TrustedScope(source.owner_id, (kb,), (doc,)))
            await conn.execute('BEGIN')
            try:
                await conn.execute('INSERT INTO knowledge_bases(id,name,tenant_id,embedding_model_id,summary_model_id,rerank_model_id) '
                    "VALUES(%s,'S6 synthetic graph fixture',%s,'','','')", (kb, source.tenant_id))
                await conn.execute("INSERT INTO knowledges(id,tenant_id,knowledge_base_id,type,title,source,parse_status,metadata) "
                    "VALUES(%s,%s,%s,'document','S6 synthetic source','synthetic.txt','completed',%s::jsonb)",
                    (doc, source.tenant_id, kb, _json_version(source.source_version)))
                await conn.execute('INSERT INTO chunks(id,tenant_id,knowledge_base_id,knowledge_id,content,chunk_index,start_at,end_at,metadata) '
                    'VALUES(%s,%s,%s,%s,%s,0,0,%s,%s::jsonb)',
                    (chunk, source.tenant_id, kb, doc, source.content, len(source.content), _json_version(source.source_version)))
                await repo.replace_chunk(batch, context, 'synthetic-pg-regression')
                assert await repo.load_graph(context) == (batch,)
                other = replace(context, scope=replace(context.scope, knowledge_base_ids=(str(uuid.uuid4()),)))
                assert await repo.load_graph(other) == ()
                other = replace(context, scope=replace(context.scope, document_ids=(str(uuid.uuid4()),)))
                assert await repo.load_graph(other) == ()
                async def other_tenant(owner):
                    return source.tenant_id + 1
                assert await PostgresGraphRepository(guard, other_tenant).load_graph(context) == ()
                for sql, values, restore_sql, restore_values in (
                    ('UPDATE chunks SET is_enabled=false WHERE id=%s', (chunk,), 'UPDATE chunks SET is_enabled=true WHERE id=%s', (chunk,)),
                    ("UPDATE knowledges SET enable_status='disabled' WHERE id=%s", (doc,), "UPDATE knowledges SET enable_status='enabled' WHERE id=%s", (doc,)),
                    ('UPDATE knowledges SET deleted_at=now() WHERE id=%s', (doc,), 'UPDATE knowledges SET deleted_at=NULL WHERE id=%s', (doc,)),
                    ('UPDATE knowledges SET metadata=%s::jsonb WHERE id=%s', (_json_version('v2'), doc),
                     'UPDATE knowledges SET metadata=%s::jsonb WHERE id=%s', (_json_version(source.source_version), doc))):
                    await conn.execute(sql, values)
                    assert await repo.load_graph(context) == ()
                    await conn.execute(restore_sql, restore_values)
                with pytest.raises(PermissionError, match='SHA_MISMATCH'):
                    await repo.replace_chunk(replace(batch, source=replace(source, content=source.content + ' forged')), context, 'test')
                assert await repo.load_graph(context) == (batch,)
                count = await (await conn.execute('SELECT count(*) AS count FROM port_graph_evidence WHERE chunk_id=%s', (chunk,))).fetchall()
                assert count[0]['count'] == 1
            finally:
                await conn.execute('ROLLBACK')
            assert not (await (await conn.execute('SELECT id FROM knowledge_bases WHERE id=%s', (kb,))).fetchall())
        finally:
            await conn.close()
    actual_pg_run(run())


def _json_version(version):
    return json.dumps({'source_version': version})
