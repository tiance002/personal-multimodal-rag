"""Opt-in graph persistence; only current, scoped source chunks are readable.

Graph contributions never rewrite source chunks, embeddings, model scores or
historical ledgers. A graph edge is a source-grounded extracted claim, not an
independent fact or a semantic verification verdict.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json

from ..graph.types import GraphSource, GraphNode, GraphEdge, GraphBatch
from .knowledge import ScopedKnowledgeRepository, _json_object


MAX_SOURCES = 2000
MAX_NODES = 4000
MAX_EDGES = 2000
SOURCE_TYPES = frozenset(('text', 'image_ocr'))
VERIFICATION = 'QUOTE_AND_ORDER_CHECKED_NOT_SEMANTIC_JUDGE'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _source_id(source):
    identity = [source.tenant_id, source.owner_id, source.knowledge_base_id,
                source.document_id, source.source_version, source.chunk_id]
    return hashlib.sha256(_json(identity).encode('utf-8')).hexdigest()


def _payload(value):
    return _json_object(value)


class PostgresGraphRepository:
    def __init__(self, guard, resolve_tenant):
        self.guard = guard
        self.sources = ScopedKnowledgeRepository(guard, resolve_tenant)
        self.last_status = 'NOT_READ'

    async def _tenant(self, context):
        self.guard.require_verified()
        return await self.sources._tenant(context)

    @staticmethod
    def _scope(source, context, tenant):
        scope = context.scope
        if (source.tenant_id != tenant or source.owner_id != scope.owner_id or
                source.knowledge_base_id not in scope.knowledge_base_ids or
                scope.document_ids and source.document_id not in scope.document_ids):
            raise PermissionError('GRAPH_SOURCE_SCOPE_MISMATCH')
        if (not source.source_version or not source.chunk_id or not source.document_id or
                not source.content or type(source.start_at) is not int or
                type(source.end_at) is not int or source.start_at < 0 or
                source.end_at < source.start_at):
            raise ValueError('GRAPH_SOURCE_IDENTITY_INVALID')

    @staticmethod
    def _current(source, row):
        if (row is None or row.chunk_type not in SOURCE_TYPES or
                row.knowledge_base_id != source.knowledge_base_id or
                row.knowledge_id != source.document_id or row.id != source.chunk_id or
                row.metadata.get('source_version') != source.source_version or
                row.chunk_metadata.get('source_version') != source.source_version or
                not row.is_enabled or row.content_rewritten or row.content_revision or
                row.content != source.content or
                hashlib.sha256(row.content.encode('utf-8')).hexdigest() != source.content_sha256 or
                (row.start_at, row.end_at) != (source.start_at, source.end_at)):
            raise PermissionError('GRAPH_SOURCE_NOT_CURRENT_OR_SHA_MISMATCH')
        # Zero/zero image offsets remain zero/zero. They must never be changed
        # into a pretend ordinary source-text interval for graph evidence.
        if source.page_number is not None:
            if type(source.page_number) is not int or source.page_number <= 0:
                raise ValueError('GRAPH_SOURCE_PAGE_INVALID')
            metadata = row.chunk_metadata
            known = [metadata.get(key) for key in ('page_number', 'physical_page')]
            if source.page_number not in known:
                raise PermissionError('GRAPH_SOURCE_PAGE_UNVERIFIED')

    @staticmethod
    def _validate_batch(batch):
        from ..graph.extraction import validate_batch_proofs
        try:
            validate_batch_proofs(batch)
        except ValueError:
            raise ValueError('GRAPH_SOURCE_PROOF_REPLAY_REJECTED') from None
        if not isinstance(batch.extraction_id, str) or not batch.extraction_id.strip():
            raise ValueError('GRAPH_EXTRACTION_ID_REQUIRED')
        if len(batch.nodes) > MAX_NODES or len(batch.edges) > MAX_EDGES:
            raise ValueError('GRAPH_CONTRIBUTION_BUDGET_EXCEEDED')
        nodes = {node.id: node for node in batch.nodes}
        if len(nodes) != len(batch.nodes) or len({node.local_key for node in batch.nodes}) != len(batch.nodes):
            raise ValueError('GRAPH_DUPLICATE_NODE_ID')
        if len({edge.id for edge in batch.edges}) != len(batch.edges):
            raise ValueError('GRAPH_DUPLICATE_EDGE_ID')
        for node in batch.nodes:
            if (not node.id or not node.local_key or not node.name.strip() or
                    not node.entity_type.strip() or not isinstance(node.is_anchor, bool) or
                    any(not isinstance(alias, str) or not alias.strip() for alias in node.aliases)):
                raise ValueError('GRAPH_NODE_INVALID')
        for edge in batch.edges:
            if (not edge.id or edge.source_id not in nodes or edge.target_id not in nodes or
                    not edge.predicate.strip() or type(edge.quote_start) is not int or
                    type(edge.quote_end) is not int or edge.quote_start < 0 or
                    edge.quote_end <= edge.quote_start or edge.quote_end > len(batch.source.content) or
                    batch.source.content[edge.quote_start:edge.quote_end] != edge.quote or
                    edge.verification != VERIFICATION):
                raise ValueError('GRAPH_EDGE_SOURCE_PROOF_INVALID')

    async def replace_chunk(self, batch, context, producer):
        """Replace only this chunk/version contribution, in a source-locked tx."""
        tenant = await self._tenant(context)
        source = batch.source
        self._scope(source, context, tenant)
        if source.knowledge_base_id not in self.guard.writable_knowledge_base_ids:
            raise PermissionError('GRAPH_WRITE_SCOPE_DENIED')
        if not isinstance(producer, str) or not producer.strip():
            raise ValueError('GRAPH_PRODUCER_REQUIRED')
        self._validate_batch(batch)
        key = _source_id(source)
        connection = self.guard.connection
        async with connection.transaction():
            # Document and source edits cannot race a successful validation and
            # graph commit. No source row is modified by this lock or this API.
            locks = await (await connection.execute(
                'SELECT c.id FROM chunks c JOIN knowledges k ON k.id=c.knowledge_id '
                'AND k.knowledge_base_id=c.knowledge_base_id JOIN knowledge_bases kb ON kb.id=c.knowledge_base_id '
                'WHERE c.id=%s AND c.tenant_id=%s AND k.tenant_id=%s AND kb.tenant_id=%s '
                'AND c.knowledge_base_id=%s AND c.knowledge_id=%s AND c.deleted_at IS NULL '
                'AND k.deleted_at IS NULL AND kb.deleted_at IS NULL FOR SHARE OF c,k,kb',
                (source.chunk_id, tenant, tenant, tenant, source.knowledge_base_id, source.document_id))).fetchall()
            if len(locks) != 1 or locks[0]['id'] != source.chunk_id:
                raise PermissionError('GRAPH_SOURCE_UNAVAILABLE')
            rows = await self.sources._read_chunks((source.chunk_id,), context, current=True)
            self._current(source, rows.get(source.chunk_id))
            await connection.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('weknora-graph:' + key,))
            values = (key, tenant, source.owner_id, source.knowledge_base_id, source.document_id,
                      source.source_version, source.chunk_id, source.content_sha256, source.content,
                      source.start_at, source.end_at, source.page_number, batch.extraction_id, producer,
                      _json({'source': asdict(source), 'rejected': list(batch.rejected)}))
            await connection.execute(
                'INSERT INTO port_graph_evidence(id,tenant_id,owner_id,knowledge_base_id,document_id,source_version,'
                'chunk_id,content_sha256,source_content,start_at,end_at,page_number,extraction_id,producer,payload) '
                'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb) '
                'ON CONFLICT(id) DO UPDATE SET content_sha256=EXCLUDED.content_sha256,source_content=EXCLUDED.source_content,'
                'start_at=EXCLUDED.start_at,end_at=EXCLUDED.end_at,page_number=EXCLUDED.page_number,'
                'extraction_id=EXCLUDED.extraction_id,producer=EXCLUDED.producer,payload=EXCLUDED.payload,updated_at=now()', values)
            await connection.execute('DELETE FROM port_graph_edges WHERE evidence_id=%s', (key,))
            await connection.execute('DELETE FROM port_graph_nodes WHERE evidence_id=%s', (key,))
            for node in batch.nodes:
                await connection.execute(
                    'INSERT INTO port_graph_nodes(id,evidence_id,local_key,name,entity_type,aliases,is_anchor,identity_qualifier,payload) '
                    'VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb)',
                    (node.id, key, node.local_key, node.name, node.entity_type, _json(node.aliases),
                     node.is_anchor, node.identity_qualifier, _json(asdict(node))))
            for edge in batch.edges:
                await connection.execute(
                    'INSERT INTO port_graph_edges(id,evidence_id,source_node_id,target_node_id,predicate,quote,quote_start,quote_end,'
                    'temporal_text,verification,payload) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)',
                    (edge.id, key, edge.source_id, edge.target_id, edge.predicate, edge.quote,
                     edge.quote_start, edge.quote_end, edge.temporal_text, edge.verification, _json(asdict(edge))))
        return {'evidence_id': key, 'nodes': len(batch.nodes), 'edges': len(batch.edges)}

    async def load_graph(self, context):
        """Return bounded current-source batches; obsolete rows remain historical."""
        tenant = await self._tenant(context)
        tables = await (await self.guard.connection.execute(
            "SELECT to_regclass('port_graph_evidence') AS evidence,"
            "to_regclass('port_graph_nodes') AS nodes,to_regclass('port_graph_edges') AS edges")).fetchall()
        if len(tables) != 1:
            raise ValueError('GRAPH_SCHEMA_IDENTITY_UNAVAILABLE')
        if not all(tables[0].get(name) for name in ('evidence', 'nodes', 'edges')):
            self.last_status = 'GRAPH_SCHEMA_UNAVAILABLE'
            return ()
        scope = context.scope
        where = ['ge.tenant_id=%s', 'ge.owner_id=%s', 'ge.knowledge_base_id=ANY(%s)',
                 'c.tenant_id=ge.tenant_id', 'k.tenant_id=ge.tenant_id', 'kb.tenant_id=ge.tenant_id',
                 'c.knowledge_base_id=ge.knowledge_base_id', 'c.knowledge_id=ge.document_id',
                 'k.knowledge_base_id=ge.knowledge_base_id', 'c.deleted_at IS NULL',
                 'k.deleted_at IS NULL', 'kb.deleted_at IS NULL', 'c.is_enabled=TRUE',
                 "k.enable_status='enabled'", "k.parse_status='completed'",
                 "c.metadata->>'source_version'=ge.source_version",
                 "k.metadata->>'source_version'=ge.source_version"]
        values = [tenant, scope.owner_id, list(scope.knowledge_base_ids)]
        if scope.document_ids:
            where.append('ge.document_id=ANY(%s)')
            values.append(list(scope.document_ids))
        values.append(MAX_SOURCES + 1)
        rows = await (await self.guard.connection.execute(
            'SELECT ge.* FROM port_graph_evidence ge JOIN chunks c ON c.id=ge.chunk_id '
            'JOIN knowledges k ON k.id=ge.document_id JOIN knowledge_bases kb ON kb.id=ge.knowledge_base_id '
            'WHERE ' + ' AND '.join(where) + ' ORDER BY ge.id LIMIT %s', values)).fetchall()
        if len(rows) > MAX_SOURCES:
            raise ValueError('GRAPH_SOURCE_READ_BUDGET_EXCEEDED')
        sources, valid_ids = [], []
        current = await self.sources._read_chunks(tuple(row['chunk_id'] for row in rows), context, current=True)
        for row in rows:
            source = GraphSource(**_payload(row['payload'])['source'])
            self._scope(source, context, tenant)
            if (row['id'] != _source_id(source) or row['chunk_id'] != source.chunk_id or
                    row['source_version'] != source.source_version or row['content_sha256'].strip() != source.content_sha256 or
                    row['source_content'] != source.content or row['tenant_id'] != tenant or
                    row['owner_id'] != source.owner_id or row['knowledge_base_id'] != source.knowledge_base_id or
                    row['document_id'] != source.document_id):
                raise PermissionError('GRAPH_STORED_SOURCE_IDENTITY_MISMATCH')
            loaded = current.get(source.chunk_id)
            if loaded is None:
                continue  # Concurrent disable/delete/version transition: never return stale proof.
            self._current(source, loaded)
            sources.append(source)
            valid_ids.append(row['id'])
        if not valid_ids:
            self.last_status = 'EMPTY_CURRENT_GRAPH'
            return ()
        node_rows = await (await self.guard.connection.execute(
            'SELECT id,evidence_id,payload FROM port_graph_nodes WHERE evidence_id=ANY(%s) ORDER BY id LIMIT %s',
            (valid_ids, MAX_NODES + 1))).fetchall()
        edge_rows = await (await self.guard.connection.execute(
            'SELECT id,evidence_id,payload FROM port_graph_edges WHERE evidence_id=ANY(%s) ORDER BY id LIMIT %s',
            (valid_ids, MAX_EDGES + 1))).fetchall()
        if len(node_rows) > MAX_NODES or len(edge_rows) > MAX_EDGES:
            raise ValueError('GRAPH_READ_BUDGET_EXCEEDED')
        nodes, edges = {}, {}
        node_domains = {}
        for row in node_rows:
            payload = _payload(row['payload'])
            payload['aliases'] = tuple(payload['aliases'])
            node = GraphNode(**payload)
            if row['evidence_id'] not in valid_ids or row['id'] != node.id:
                raise PermissionError('GRAPH_STORED_NODE_IDENTITY_MISMATCH')
            nodes.setdefault(row['evidence_id'], []).append(node)
            node_domains[node.id] = row['evidence_id']
        by_source = {key: source for key, source in zip(valid_ids, sources)}
        for row in edge_rows:
            edge = GraphEdge(**_payload(row['payload']))
            if (row['evidence_id'] not in valid_ids or row['id'] != edge.id or
                    node_domains.get(edge.source_id) != row['evidence_id'] or
                    node_domains.get(edge.target_id) != row['evidence_id']):
                raise PermissionError('GRAPH_STORED_EDGE_IDENTITY_MISMATCH')
            source = by_source[row['evidence_id']]
            if (edge.quote_start < 0 or edge.quote_end <= edge.quote_start or
                    source.content[edge.quote_start:edge.quote_end] != edge.quote or
                    edge.verification != VERIFICATION):
                raise PermissionError('GRAPH_STORED_EDGE_PROOF_MISMATCH')
            edges.setdefault(row['evidence_id'], []).append(edge)
        by_row = {row['id']: row for row in rows}
        batches = []
        for key, source in zip(valid_ids, sources):
            row = by_row[key]
            batch = GraphBatch(source, tuple(nodes.get(key, ())), tuple(edges.get(key, ())),
                               tuple(_payload(row['payload']).get('rejected', ())), row['extraction_id'])
            self._validate_batch(batch)
            batches.append(batch)
        self.last_status = 'CURRENT_GRAPH_LOADED'
        return tuple(batches)
