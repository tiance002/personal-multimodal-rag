"""Explicit, bounded graph indexing through the existing Registry and SendGate.

This operation is never started by upload, application startup or a query. It
accepts a document identity, not hand-selected chunks or caller-supplied facts.
"""
from dataclasses import asdict
import hashlib
import json
import time

from ..models.types import ChatOptions, Message, ProviderError
from ..storage.graph import PostgresGraphRepository
from ..storage.knowledge import ScopedKnowledgeRepository, _json_object
from .types import GraphSource
from .extraction import build_extraction_messages, MAX_SOURCE_CHARS
from .quote_completion import parse_with_quote_completion

MAX_DOCUMENT_CHUNKS = 12
MAX_DOCUMENT_CHARS = 32768


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


class GraphIndexError(ProviderError):
    def __init__(self, code, result):
        super().__init__(code)
        self.result = result


class GraphIndexer:
    def __init__(self, app):
        self.app = app

    async def _tenant(self, owner):
        if owner != self.app.principal.subject_id:
            raise PermissionError('GRAPH_OWNER_MISMATCH')
        return self.app.principal.tenant_id

    async def _sources(self, kb_id, document_id, context):
        app = self.app
        if (context.scope.owner_id != app.principal.subject_id or
                set(context.scope.knowledge_base_ids) != {kb_id} or
                set(context.scope.document_ids) != {document_id} or not context.request_id.strip()):
            raise PermissionError('GRAPH_INDEX_SCOPE_MISMATCH')
        async with app.leases.lease() as guard:
            documents = await (await guard.connection.execute(
                'SELECT k.id,k.tenant_id,k.knowledge_base_id,k.metadata,k.file_hash FROM knowledges k '
                'JOIN knowledge_bases kb ON kb.id=k.knowledge_base_id '
                'WHERE k.id=%s AND k.knowledge_base_id=%s AND k.tenant_id=%s AND kb.tenant_id=%s '
                "AND k.deleted_at IS NULL AND kb.deleted_at IS NULL AND k.enable_status='enabled' "
                "AND k.parse_status='completed'", (document_id, kb_id, app.principal.tenant_id,
                                                 app.principal.tenant_id))).fetchall()
            if (len(documents) != 1 or documents[0]['id'] != document_id or
                    documents[0]['tenant_id'] != app.principal.tenant_id or
                    documents[0]['knowledge_base_id'] != kb_id):
                raise PermissionError('GRAPH_DOCUMENT_NOT_CURRENT')
            version = _json_object(documents[0]['metadata']).get('source_version')
            if not isinstance(version, str) or not version:
                raise PermissionError('GRAPH_DOCUMENT_VERSION_MISSING')
            candidates = await (await guard.connection.execute(
                'SELECT id FROM chunks WHERE tenant_id=%s AND knowledge_base_id=%s AND knowledge_id=%s '
                "AND chunk_type IN ('text','image_ocr') AND is_enabled=TRUE AND deleted_at IS NULL "
                "AND metadata->>'source_version'=%s ORDER BY chunk_index,id LIMIT %s",
                (app.principal.tenant_id, kb_id, document_id, version, MAX_DOCUMENT_CHUNKS + 1))).fetchall()
            if len(candidates) > MAX_DOCUMENT_CHUNKS:
                raise ValueError('GRAPH_DOCUMENT_CHUNK_BUDGET_EXCEEDED')
            if len({row['id'] for row in candidates}) != len(candidates):
                raise PermissionError('GRAPH_SOURCE_CANDIDATE_IDENTITY_MISMATCH')
            loader = ScopedKnowledgeRepository(guard, self._tenant)
            loaded = await loader._read_chunks(tuple(row['id'] for row in candidates), context, current=True)
            if set(loaded) != {row['id'] for row in candidates}:
                raise PermissionError('GRAPH_SOURCE_CHANGED_DURING_PREFLIGHT')
            sources = []
            for candidate in candidates:
                row = loaded[candidate['id']]
                page = row.chunk_metadata.get('physical_page', row.chunk_metadata.get('page_number'))
                source = GraphSource(app.principal.tenant_id, app.principal.subject_id, kb_id, document_id,
                    version, row.id, row.content, row.start_at, row.end_at,
                    page if type(page) is int and page > 0 else None)
                PostgresGraphRepository._current(source, row)
                if not source.content.strip() or len(source.content) > MAX_SOURCE_CHARS:
                    raise ValueError('GRAPH_SOURCE_CHAR_BUDGET_EXCEEDED')
                sources.append(source)
            if sum(len(source.content) for source in sources) > MAX_DOCUMENT_CHARS:
                raise ValueError('GRAPH_DOCUMENT_CHAR_BUDGET_EXCEEDED')
            # Missing migration must fail before a model is contacted.
            tables = await (await guard.connection.execute(
                "SELECT to_regclass('port_graph_evidence') AS evidence,"
                "to_regclass('port_graph_nodes') AS nodes,to_regclass('port_graph_edges') AS edges")).fetchall()
            if len(tables) != 1 or not all(tables[0].get(key) for key in ('evidence', 'nodes', 'edges')):
                raise ValueError('GRAPH_SCHEMA_NOT_INSTALLED')
        return tuple(sources), documents[0].get('file_hash')

    async def index_document(self, kb_id, document_id, model_id, context):
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError('GRAPH_EXTRACTION_MODEL_REQUIRED')
        sources, file_sha = await self._sources(kb_id, document_id, context)
        result = {'schema': 's6-explicit-graph-index/v1', 'status': 'PREPARED',
                  'request_id': context.request_id, 'tenant_id': self.app.principal.tenant_id,
                  'knowledge_base_id': kb_id, 'document_id': document_id, 'file_sha256': file_sha,
                  'source_version': sources[0].source_version if sources else None,
                  'source_chunks': len(sources), 'source_chars': sum(len(s.content) for s in sources),
                  'provider_calls': 0, 'provider_invocations': 0, 'reported_tokens': 0,
                  'unknown_provider_calls': 0, 'unknown_usage_calls': 0,
                  'call_count_status': 'OBSERVED',
                  'requested_options': {'temperature': 0, 'max_completion_tokens': 2048, 'thinking': False},
                  'wire_parameters_status': 'VERIFY_PASSIVE_CAPTURE',
                  'temperature_note': 'Existing serializer may omit zero; requested zero does not prove wire temperature zero.',
                  'nodes': 0, 'edges': 0, 'chunks': [], 'retry': False}
        if not sources:
            result['status'] = 'EMPTY_NO_SOURCE_CHUNKS'
            return result
        registry = self.app.registry_for_context()
        try:
            model = await registry.chat(model_id, context)
            config = model.config
            if config.model_id != model_id or config.model_type not in ('KnowledgeQA', 'VLLM'):
                raise ProviderError('GRAPH_EXTRACTION_MODEL_IDENTITY_MISMATCH')
            result['model'] = {'model_id': model_id, 'provider': config.provider, 'model': config.model}
            for source in sources:
                operation = context.child('graph-extract-' + source.chunk_id)
                messages = build_extraction_messages(source)
                record = {'source': asdict(source), 'source_sha256': source.content_sha256,
                          'request_id': operation.request_id, 'prompt_sha256': _digest(messages),
                          'status': 'NOT_SENT', 'usage': None}
                result['chunks'].append(record)
                began = time.perf_counter()
                response_received = False
                try:
                    # Registry construction and this call retain the actual
                    # Gate object and any configured LocalExecution fence.
                    result['provider_invocations'] += 1
                    response = await model.chat(tuple(Message(row['role'], row['content']) for row in messages),
                        ChatOptions(temperature=0, max_completion_tokens=2048, thinking=False), operation)
                    response_received = True
                    result['provider_calls'] += 1
                    record.update(seconds=time.perf_counter() - began, raw_response=response.content,
                                  finish_reason=response.finish_reason, response_model=response.model,
                                  usage=asdict(response.usage))
                    usage = response.usage
                    if (usage.status != 'REPORTED' or type(usage.total_tokens) is not int or
                            usage.total_tokens < 0):
                        result['unknown_usage_calls'] += 1
                        raise ProviderError('GRAPH_EXTRACTION_USAGE_UNREPORTED', sent=True)
                    result['reported_tokens'] += usage.total_tokens
                    if response.model != config.model:
                        raise ProviderError('GRAPH_EXTRACTION_RESPONSE_MODEL_MISMATCH', sent=True)
                    if response.finish_reason != 'stop':
                        raise ProviderError('GRAPH_EXTRACTION_NOT_COMPLETE', sent=True)
                    batch, quote_completions = parse_with_quote_completion(source, response.content, operation.request_id)
                    response_sha = hashlib.sha256(response.content.encode('utf-8')).hexdigest()
                    record.update(raw_response_sha256=response_sha, quote_completions=list(quote_completions))
                    producer = json.dumps({'status': 'completed', 'model_id': model_id,
                        'provider': config.provider, 'model': config.model, 'prompt_sha256': record['prompt_sha256'],
                        'request_id': operation.request_id, 'finish_reason': response.finish_reason,
                        'raw_response_sha256': response_sha, 'quote_completions': list(quote_completions)},
                        ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                    async with self.app.leases.lease(writable_knowledge_base_ids=frozenset({kb_id})) as guard:
                        persisted = await PostgresGraphRepository(guard, self._tenant).replace_chunk(batch, operation, producer)
                    record.update(status=batch.status, persisted=persisted, rejected=list(batch.rejected),
                                  node_count=len(batch.nodes), edge_count=len(batch.edges))
                    result['nodes'] += len(batch.nodes)
                    result['edges'] += len(batch.edges)
                except Exception as error:
                    # Preserve a partial/unknown attempt and never automatically
                    # resend it or advance to another cloud extraction.
                    record.update(status='STOPPED', seconds=time.perf_counter() - began,
                                  error_code=getattr(error, 'code', 'GRAPH_EXTRACTION_OR_STORAGE_FAILED'),
                                  exception_type=type(error).__name__, sent=getattr(error, 'sent', None))
                    error_code = record['error_code']
                    ambiguous_settlement_or_identity = ('SETTLEMENT' in error_code or 'IDENTITY' in error_code)
                    if not response_received and (ambiguous_settlement_or_identity or
                                                  getattr(error, 'sent', None) is None):
                        # A settlement wrapper can raise a default sent=False
                        # after the transport sent/received the request. That
                        # flag alone cannot prove zero sends or zero usage.
                        result.update(provider_calls=None, unknown_provider_calls=None,
                            call_count_status='REQUIRES_LEDGER_RECONCILIATION',
                            ledger_lookup={'table': 'port_provider_calls',
                                           'request_id': operation.request_id, 'capability': 'chat'})
                        record.update(reported_error_sent=getattr(error, 'sent', None), sent=None,
                                      accounting_status='REQUIRES_LEDGER_RECONCILIATION')
                    elif not response_received and getattr(error, 'sent', None) is True:
                        result['provider_calls'] += 1
                        result['unknown_provider_calls'] += 1
                    result.update(status='STOPPED', stop_reason=record['error_code'],
                                  remaining_chunks_not_run=len(sources) - len(result['chunks']))
                    raise GraphIndexError(record['error_code'], result) from None
            result['status'] = ('PARTIAL' if any(row['status'] in ('PARTIAL', 'REJECTED') for row in result['chunks'])
                                else 'EXTRACTED' if result['edges'] else 'EMPTY_NO_EDGES')
            return result
        finally:
            await registry.close()
