"""M4 offline manifest and local PostgreSQL READ ONLY readiness; never runs RAG."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def require(value, code):
    if not value:
        raise ValueError(code)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def local_path(value):
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def verify_file(ref):
    require(isinstance(ref, dict) and ref.get('path') and
            re.fullmatch(r'[0-9a-f]{64}', ref.get('sha256', '')), 'FILE_BINDING_REQUIRED')
    path = local_path(ref['path'])
    require(path.is_file(), 'FILE_MISSING')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == ref['sha256'], 'FILE_HASH_MISMATCH')
    return raw


def validate_native_gold(case, gold):
    if case['dataset'] == 'omnidocbench':
        pages = gold.get('omnidocbench_parsing_gold', {}).get('pages', [])
        require(sum(p.get('page_id') == case['qid'] for p in pages) == 1, 'NATIVE_GOLD_PAGE_NOT_FOUND')
        require(not case.get('qrels') and not case.get('answer_points'), 'PARSING_GOLD_HAS_NO_QA_ANSWERS')
        return
    datasets = [d for d in gold.get('datasets', []) if d.get('name') == case['dataset']]
    require(len(datasets) == 1, 'NATIVE_GOLD_DATASET_NOT_FOUND')
    rows = [r for r in datasets[0].get('cases', []) if r.get('qid') == case['qid']]
    require(len(rows) == 1, 'NATIVE_GOLD_QID_NOT_FOUND')
    require(rows[0].get('question') == case['question'], 'NATIVE_GOLD_QUESTION_MISMATCH')
    for key, native in (('qrels', 'qrels'), ('answer_points', 'answers')):
        if key in case:
            require(case[key] == rows[0].get(native, {} if key == 'qrels' else []), 'NATIVE_GOLD_' + key.upper() + '_MISMATCH')


def validate_plan(plan):
    require(plan.get('schema') == 'rag-m4-plan/v1', 'PLAN_SCHEMA_INVALID')
    require(plan.get('overall_authorization', 'MISSING') is None, 'OFFLINE_AUTHORIZATION_MUST_BE_NULL')
    require(re.fullmatch(r'[0-9a-f]{7,40}', plan.get('baseline_sha', '')), 'BASELINE_SHA_REQUIRED')
    cases = plan.get('cases')
    require(isinstance(cases, list) and cases, 'CASES_REQUIRED')
    require(isinstance(plan.get('input_files'), list) and plan['input_files'], 'INPUT_FILES_REQUIRED')
    for path, digest in plan.get('source_fingerprints', {}).items():
        verify_file({'path': path, 'sha256': digest})
    frozen_documents = []
    for ref in plan['input_files']:
        raw = verify_file(ref)
        if ref['path'].lower().endswith('.json'):
            value = json.loads(raw.decode('utf-8-sig'))
            if isinstance(value, dict) and isinstance(value.get('documents'), list):
                frozen_documents.extend(value['documents'])
    seen = set()
    for case in cases:
        for key in ('case_id', 'dataset', 'qid', 'question', 'subject_id', 'knowledge_base_id'):
            require(isinstance(case.get(key), str) and case[key].strip(), 'CASE_BINDING_REQUIRED_' + key)
        require(case['case_id'] not in seen, 'DUPLICATE_CASE_ID')
        seen.add(case['case_id'])
        require(type(case.get('tenant_id')) is int and case['tenant_id'] > 0, 'TENANT_REQUIRED')
        require(isinstance(case.get('document_scope'), list) and
                all(isinstance(x, str) and x for x in case['document_scope']), 'DOCUMENT_SCOPE_REQUIRED')
        require(isinstance(case.get('expected_chunk_ids', []), list) and
                all(isinstance(x, str) and x for x in case.get('expected_chunk_ids', [])), 'CHUNK_BINDING_INVALID')
        docs = case.get('expected_documents')
        require(isinstance(docs, list) and docs, 'EXPECTED_DOCUMENTS_REQUIRED')
        ids = set()
        for doc in docs:
            require(all(isinstance(doc.get(k), str) and doc[k] for k in
                        ('id', 'source_version', 'file_sha256', 'source_id', 'file_path')), 'DOCUMENT_BINDING_REQUIRED')
            require(doc['id'] not in ids, 'DUPLICATE_DOCUMENT_ID')
            ids.add(doc['id'])
            verify_file({'path': doc['file_path'], 'sha256': doc['file_sha256']})
            require(any((d.get('id'), d.get('tenant_id'), d.get('knowledge_base_id'),
                         d.get('source_version'), d.get('file_hash')) ==
                        (doc['id'], case['tenant_id'], case['knowledge_base_id'],
                         doc['source_version'], doc['file_sha256']) for d in frozen_documents),
                    'FROZEN_DOCUMENT_BINDING_MISSING')
        require(not case['document_scope'] or ids <= set(case['document_scope']), 'EXPECTED_DOCUMENT_OUTSIDE_SCOPE')
        gold = case.get('gold', {})
        require(gold.get('type') and isinstance(gold.get('metrics'), list) and gold['metrics'], 'GOLD_CONTRACT_REQUIRED')
        raw = verify_file(gold)
        # Gold stays offline. There is deliberately no HTTP payload or provider adapter.
        decoded = raw.decode('utf-8-sig')
        if gold['path'].lower().endswith('.jsonl'):
            require(any(x.strip() for x in decoded.splitlines()), 'GOLD_EMPTY')
            for line in decoded.splitlines():
                if line.strip():
                    json.loads(line)
        elif gold['path'].lower().endswith('.json'):
            native_gold = json.loads(decoded)
            require(bool(native_gold), 'GOLD_EMPTY')
            validate_native_gold(case, native_gold)
        else:
            require(bool(decoded.strip()), 'GOLD_EMPTY')
    return cases


def database_config(config):
    env = config.get('environment', {})
    names = ('DATABASE_URL', 'DATABASE_SYSTEM_ID', 'DATABASE_NAME')
    values = {k: env.get('WEKNORA_PORT_' + k) for k in names}
    require(all(isinstance(x, str) and x for x in values.values()), 'EXPLICIT_DATABASE_IDENTITY_REQUIRED')
    url = urlsplit(values['DATABASE_URL'])
    require(url.scheme in ('postgresql', 'postgresql+psycopg') and url.hostname == '127.0.0.1'
            and url.port and not url.query and not url.fragment and
            url.path == '/' + values['DATABASE_NAME'], 'LOCAL_DATABASE_URL_REQUIRED')
    values['DATABASE_URL'] = values['DATABASE_URL'].replace('postgresql+psycopg://', 'postgresql://', 1)
    return values


async def inspect_database(conn, plan, expected):
    async def rows(sql, args=()):
        return await (await conn.execute(sql, args)).fetchall()
    await conn.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    try:
        identity = (await rows('SELECT system_identifier::text AS system_id,current_database() AS database FROM pg_control_system()'))[0]
        require(identity['system_id'] == expected['DATABASE_SYSTEM_ID'] and
                identity['database'] == expected['DATABASE_NAME'], 'POSTGRES_IDENTITY_MISMATCH')
        principals = await rows("SELECT tenant_id,subject_id FROM port_local_principal WHERE slot='local'")
        require(len(principals) == 1, 'LOCAL_PRINCIPAL_UNAVAILABLE')
        principal = principals[0]
        models, results = {}, []
        for case in plan['cases']:
            tenant, kb = case['tenant_id'], case['knowledge_base_id']
            owner = (tenant, case['subject_id'])
            if owner not in models:
                # Project individual safe fields in SQL; never fetch roles/secrets/full parameters.
                models[owner] = await rows("SELECT r.key AS role,r.value->>'provider' AS provider,r.value->>'model' AS model "
                    "FROM port_model_configuration c CROSS JOIN LATERAL jsonb_each(c.roles) r "
                    "WHERE c.tenant_id=%s AND c.subject_id=%s ORDER BY r.key", owner)
            kbs = await rows('SELECT id,cloud_allowed,embedding_model_id FROM knowledge_bases WHERE tenant_id=%s AND id=%s AND deleted_at IS NULL', (tenant, kb))
            issues, documents, current_chunk_ids = [], [], set()
            if len(kbs) != 1:
                issues.append('KNOWLEDGE_BASE_UNAVAILABLE')
            for doc in case['expected_documents']:
                found = await rows("SELECT id,file_hash,metadata->>'source_version' AS source_version,parse_status,enable_status "
                    'FROM knowledges WHERE tenant_id=%s AND knowledge_base_id=%s AND id=%s AND deleted_at IS NULL', (tenant, kb, doc['id']))
                good = len(found) == 1 and found[0]['source_version'] == doc['source_version'] and found[0]['file_hash'] == doc['file_sha256'] and found[0]['parse_status'] == 'completed' and found[0]['enable_status'] == 'enabled'
                chunks = await rows("SELECT c.id,c.chunk_type,c.index_status,c.is_enabled,c.metadata->>'source_version' AS source_version,"
                    'count(e.id)::int AS index_count,array_agg(DISTINCT e.dimension) FILTER(WHERE e.id IS NOT NULL) AS dimensions,'
                    'bool_and(e.embedding IS NOT NULL AND vector_dims(e.embedding)=e.dimension AND e.dimension>0) AS vectors_valid '
                    'FROM chunks c LEFT JOIN embeddings e ON e.knowledge_id=c.knowledge_id AND e.knowledge_base_id=c.knowledge_base_id '
                    'AND e.chunk_id=c.id AND e.source_id=c.id AND e.source_type=0 AND e.is_enabled=TRUE '
                    'WHERE c.tenant_id=%s AND c.knowledge_base_id=%s AND c.knowledge_id=%s AND c.deleted_at IS NULL '
                    'GROUP BY c.id', (tenant, kb, doc['id']))
                # Match ScopedKnowledgeRepository searchable types. Persisted
                # parent_text rows provide context and need no vector index.
                searchable = [r for r in chunks if r['chunk_type'] in
                    ('text', 'summary', 'table_column', 'table_summary', 'faq', 'image_ocr', 'image_caption')]
                def version_matches(row):
                    return row['source_version'] == doc['source_version'] or (
                        good and row['chunk_type'] == 'summary' and row['source_version'] is None)
                summary_missing = [r['id'] for r in searchable if r['chunk_type'] == 'summary' and r['source_version'] is None]
                current_chunk_ids.update(r['id'] for r in searchable if version_matches(r))
                valid = bool(searchable) and all(version_matches(r) and
                    r['is_enabled'] is True and r['index_status'] == 'ready' and r['index_count'] > 0 and
                    r['vectors_valid'] is True and r['dimensions'] and len(r['dimensions']) == 1 for r in searchable)
                dimensions = {d for r in searchable for d in (r['dimensions'] or [])}
                valid = valid and len(dimensions) == 1 and all(version_matches(r) for r in chunks)
                if not good or not valid:
                    issues.append('DOCUMENT_OR_INDEX_NOT_READY:' + doc['id'])
                documents.append({'id': doc['id'], 'source_id': doc['source_id'], 'binding': 'EXTERNAL_BINDING_BY_FROZEN_FILE_HASH',
                    'document_matches': good, 'chunks': len(chunks), 'searchable_chunks': len(searchable),
                    'index_rows': sum(r['index_count'] for r in chunks), 'dimensions': sorted(dimensions), 'index_ready': bool(valid),
                    'version_binding': 'DOCUMENT_JOIN_CURRENT_VERSION' if summary_missing else 'EXPLICIT_CHUNK_SOURCE_VERSION',
                    'summary_missing_own_version_ids': summary_missing})
            if not set(case.get('expected_chunk_ids', [])) <= current_chunk_ids:
                issues.append('EXPECTED_CHUNK_NOT_CURRENT_IN_SCOPE')
            binding = (principal['tenant_id'], principal['subject_id']) == owner
            results.append({'case_id': case['case_id'], 'data_readiness': 'READY' if not issues else 'BLOCKED',
                'issues': issues, 'documents': documents, 'api_binding': 'MATCH' if binding else 'LOCAL_PRINCIPAL_MISMATCH',
                'cloud_allowed': kbs[0]['cloud_allowed'] if len(kbs) == 1 else None,
                'execution': 'NOT_RUN', 'authorization': 'BLOCKED_AUTHORIZATION', 'real_metrics': None})
        return {'database': identity, 'local_principal': principal, 'cases': results,
                'model_identity': [{'tenant_id': t, 'subject_id': s, 'roles': v} for (t, s), v in models.items()]}
    finally:
        await conn.execute('ROLLBACK')


async def inspect(plan, config):
    import psycopg
    from psycopg.rows import dict_row
    expected = database_config(config)
    conn = await psycopg.AsyncConnection.connect(expected['DATABASE_URL'], row_factory=dict_row, autocommit=True, connect_timeout=5)
    try:
        return await inspect_database(conn, plan, expected)
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('plan', 'config', 'output'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    path = Path(args.output)
    plan = read(args.plan)
    protected = [args.plan, args.config] + [r['path'] for r in plan.get('input_files', [])]
    for case in plan.get('cases', []):
        protected.extend(d['file_path'] for d in case.get('expected_documents', []))
        if case.get('gold', {}).get('path'):
            protected.append(case['gold']['path'])
    require(path.resolve() not in {local_path(p).resolve() for p in protected}, 'OUTPUT_MUST_NOT_OVERWRITE_INPUT')
    result = {'schema': 'rag-m4-preflight/v1', 'quality_result': 'NOT_RUN', 'provider_calls': 0, 'real_metrics': None}
    try:
        validate_plan(plan)
        result['offline_validation'] = 'PASS'
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            result.update(runner.run(inspect(plan, read(args.config))))
        ready = all(c['data_readiness'] == 'READY' for c in result['cases'])
        result['data_preflight_result'] = 'PASS' if ready else 'BLOCKED'
        result['preflight_result'] = 'OFFLINE_DATA_READY_ONLY' if ready else 'DATA_BLOCKED'
        result['execution_readiness'] = 'BLOCKED_AUTHORIZATION'
        result['normal_api_scope'] = ('MATCH' if all(c['api_binding'] == 'MATCH' for c in result['cases'])
                                      else 'BLOCKED_PRINCIPAL_MISMATCH')
        code = 0 if ready else 2
    except Exception as exc:
        result.update(preflight_result='FAIL', error_code=str(exc) if isinstance(exc, (ValueError, KeyError)) else type(exc).__name__)
        code = 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
    print(json.dumps({'preflight_result': result['preflight_result'], 'quality_result': 'NOT_RUN', 'provider_calls': 0}))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
