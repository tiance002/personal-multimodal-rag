import asyncio
import copy
import hashlib
import json

import pytest
from scripts import rag_m40_preflight as preflight


@pytest.fixture
def plan(tmp_path):
    source = tmp_path / 'source.md'
    source.write_text('frozen source', encoding='utf8')
    gold = tmp_path / 'gold.json'
    gold.write_text(json.dumps({'datasets': [{'name': 'native', 'dataset_version': 'fixture-v1',
        'cases': [{'qid': 'q1', 'question': 'a question', 'qrels': {'source1': 1}, 'answers': []}]}]}), encoding='utf8')
    ref = lambda p: {'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
    bindings = tmp_path / 'source-bindings.json'
    bindings.write_text(json.dumps({'documents': [{'id': 'doc1', 'tenant_id': 332,
        'knowledge_base_id': 'kb1', 'source_version': 'v1', 'file_hash': ref(source)['sha256']}]}), encoding='utf8')
    return {'schema': 'rag-m4-plan/v1', 'baseline_sha': '5099efe', 'overall_authorization': None,
            'input_files': [ref(source), ref(bindings)], 'cases': [{'case_id': 'c1', 'dataset': 'native', 'qid': 'q1',
            'question': 'a question', 'tenant_id': 332, 'subject_id': 'historical', 'knowledge_base_id': 'kb1',
            'document_scope': [], 'expected_documents': [{'id': 'doc1', 'source_version': 'v1',
                'file_sha256': ref(source)['sha256'], 'file_path': str(source), 'source_id': 'source1'}],
            'gold': {**ref(gold), 'type': 'native-qrels', 'metrics': ['recall']}}]}


def test_frozen_native_gold_and_source_hashes(plan):
    assert preflight.validate_plan(plan) == plan['cases']
    plan['cases'][0]['expected_documents'][0]['file_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='FILE_HASH_MISMATCH'):
        preflight.validate_plan(plan)


def test_empty_duplicate_missing_bindings_fail(plan):
    empty = {**plan, 'cases': []}
    with pytest.raises(ValueError, match='CASES_REQUIRED'):
        preflight.validate_plan(empty)
    duplicate = {**plan, 'cases': plan['cases'] * 2}
    with pytest.raises(ValueError, match='DUPLICATE_CASE_ID'):
        preflight.validate_plan(duplicate)
    broken = copy.deepcopy(plan)
    del broken['cases'][0]['expected_documents'][0]['source_version']
    with pytest.raises(ValueError, match='DOCUMENT_BINDING_REQUIRED'):
        preflight.validate_plan(broken)


def test_database_is_explicit_local_only():
    env = {'WEKNORA_PORT_DATABASE_URL': 'postgresql://u:p@127.0.0.1:5555/db',
           'WEKNORA_PORT_DATABASE_SYSTEM_ID': '123', 'WEKNORA_PORT_DATABASE_NAME': 'db'}
    assert preflight.database_config({'environment': env})['DATABASE_NAME'] == 'db'
    for url in ('postgresql://u:p@example.com:5555/db', 'postgresql://u:p@127.0.0.1:5555/db?host=example.com'):
        with pytest.raises(ValueError, match='LOCAL_DATABASE_URL_REQUIRED'):
            preflight.database_config({'environment': {**env, 'WEKNORA_PORT_DATABASE_URL': url}})


def test_wrong_native_qid_rejected(plan):
    plan['cases'][0]['qid'] = 'missing'
    with pytest.raises(ValueError, match='NATIVE_GOLD_QID_NOT_FOUND'):
        preflight.validate_plan(plan)


def test_changed_native_question_rejected(plan):
    plan['cases'][0]['question'] = 'edited question'
    with pytest.raises(ValueError, match='NATIVE_GOLD_QUESTION_MISMATCH'):
        preflight.validate_plan(plan)


class Connection:
    def __init__(self, source_hash, identity='123', version='v1'):
        self.calls, self.source_hash, self.identity, self.version = [], source_hash, identity, version

    async def execute(self, sql, args=()):
        self.calls.append((sql, args))
        if 'pg_control_system' in sql:
            self.result = [{'system_id': self.identity, 'database': 'db'}]
        elif 'port_local_principal' in sql:
            self.result = [{'tenant_id': 999, 'subject_id': 'current'}]
        elif 'port_model_configuration' in sql:
            self.result = [{'role': 'cheap', 'provider': 'deepseek', 'model': 'safe-name'}]
        elif 'FROM knowledge_bases' in sql:
            self.result = [{'id': 'kb1', 'cloud_allowed': False, 'embedding_model_id': 'embed1'}]
        elif 'FROM knowledges' in sql:
            self.result = [{'id': 'doc1', 'file_hash': self.source_hash, 'source_version': 'v1',
                            'parse_status': 'completed', 'enable_status': 'enabled'}]
        elif 'FROM chunks' in sql:
            self.result = [{'id': 'chunk1', 'chunk_type': 'text', 'index_status': 'ready', 'is_enabled': True,
                            'source_version': self.version, 'index_count': 1, 'dimensions': [1024], 'vectors_valid': True}]
        else:
            self.result = []
        return self

    async def fetchall(self):
        return self.result


def test_readonly_data_ready_is_not_api_or_quality_pass(plan):
    conn = Connection(plan['cases'][0]['expected_documents'][0]['file_sha256'])
    result = asyncio.run(preflight.inspect_database(conn, plan, {'DATABASE_SYSTEM_ID': '123', 'DATABASE_NAME': 'db'}))
    case = result['cases'][0]
    assert case['data_readiness'] == 'READY'
    assert case['api_binding'] == 'LOCAL_PRINCIPAL_MISMATCH'
    assert case['authorization'] == 'BLOCKED_AUTHORIZATION'
    assert case['execution'] == 'NOT_RUN' and case['real_metrics'] is None
    assert conn.calls[0][0].endswith('READ ONLY') and conn.calls[-1][0] == 'ROLLBACK'
    assert all(sql.startswith(('BEGIN ', 'SELECT ', 'ROLLBACK')) for sql, _ in conn.calls)
    assert (332, 'kb1', 'doc1') in [args for _, args in conn.calls]
    assert not any('api_key' in sql or 'SELECT roles' in sql or 'SELECT *' in sql for sql, _ in conn.calls)


def test_mismatched_database_rolls_back_before_data_read(plan):
    conn = Connection('unused', identity='wrong')
    with pytest.raises(ValueError, match='POSTGRES_IDENTITY_MISMATCH'):
        asyncio.run(preflight.inspect_database(conn, plan, {'DATABASE_SYSTEM_ID': '123', 'DATABASE_NAME': 'db'}))
    assert conn.calls[-1][0] == 'ROLLBACK'
    assert len(conn.calls) == 3


def test_missing_chunk_version_never_counts_as_valid(plan):
    conn = Connection(plan['cases'][0]['expected_documents'][0]['file_sha256'], version=None)
    result = asyncio.run(preflight.inspect_database(conn, plan, {'DATABASE_SYSTEM_ID': '123', 'DATABASE_NAME': 'db'}))
    assert result['cases'][0]['data_readiness'] == 'BLOCKED'


def test_summary_uses_verified_document_version_but_rejects_explicit_stale_version(plan):
    class SummaryConnection(Connection):
        async def execute(self, sql, args=()):
            result = await super().execute(sql, args)
            if 'FROM chunks' in sql:
                self.result[0]['chunk_type'] = 'summary'
            return result
    expected = {'DATABASE_SYSTEM_ID': '123', 'DATABASE_NAME': 'db'}
    source_hash = plan['cases'][0]['expected_documents'][0]['file_sha256']
    conn = SummaryConnection(source_hash, version=None)
    document = asyncio.run(preflight.inspect_database(conn, plan, expected))['cases'][0]['documents'][0]
    assert document['index_ready'] is True
    assert document['version_binding'] == 'DOCUMENT_JOIN_CURRENT_VERSION'
    assert document['summary_missing_own_version_ids'] == ['chunk1']
    stale = SummaryConnection(source_hash, version='old-version')
    assert asyncio.run(preflight.inspect_database(stale, plan, expected))['cases'][0]['data_readiness'] == 'BLOCKED'
