"""Bind the reported regression to actual old/current reader functions, offline."""
import ast
import asyncio
import hashlib
import json
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
import backend.weknora_port.documents as module
from tests.weknora_port.test_documents import PRINCIPAL, make_service
from tests.weknora_port.test_summary_source_readback import summary_row

out = Path(__file__).resolve().parent
assert not (out / 'reader-comparison.json').exists()
row = summary_row()
service, _ = make_service(Path('E:/WK/run/summary-readback-20261015/reader-control'))
async def rows(query, params):
    assert params == ('summary', PRINCIPAL.tenant_id, PRINCIPAL.tenant_id)
    return [row]
service._rows = rows
results = []
async def run():
    for revision in ('341dec08c7e550a0433bda9f18f9b2331bdad377', '4f3aedc2e18a12920ed73ef7d6cbb566ff38f6f0', 'working-source'):
        raw = (ROOT / 'backend/weknora_port/documents.py').read_bytes() if revision == 'working-source' else subprocess.check_output([
            'git', '-C', str(ROOT), 'show', revision + ':backend/weknora_port/documents.py'])
        tree = ast.parse(raw.decode('utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'DocumentLifecycleService')
        fn = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'read_source_chunk')
        ns = dict(module.__dict__)
        exec(compile(ast.Module([fn], type_ignores=[]), '<reader-' + revision + '>', 'exec'), ns)
        try:
            value = await types.MethodType(ns['read_source_chunk'], service)(PRINCIPAL, 'summary')
            assert value['text'] == row['content'] and value['chunk_id'] == 'summary'
            outcome = {'status': 'SUCCESS', 'source_version': value.get('source_version')}
        except LookupError as error:
            outcome = {'status': 'REJECTED', 'error': str(error)}
        results.append({'revision': revision, 'documents_sha256': hashlib.sha256(raw).hexdigest(), **outcome})
asyncio.run(run())
assert [r['status'] for r in results] == ['SUCCESS', 'REJECTED', 'SUCCESS']
report = {'kind': 'OFFLINE_ACTUAL_READER_FUNCTIONS_WITH_SAME_ROW_DRIVER', 'results': results,
    'summary_metadata': row['chunk_metadata'], 'parent_metadata': row['source_parent_metadata'],
    'real_provider_calls': 0, 'database_operations': 0}
(out / 'reader-comparison.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report, indent=2))
