"""Generated summary source versions are verified through their original parent."""
import asyncio
import copy

import pytest

from tests.weknora_port.test_documents import make_service, PRINCIPAL


def summary_row():
    return dict(chunk_id='summary', document_id='doc', knowledge_base_id='kb',
        content='# Summary\nModel summary', source_content='# Summary\nModel summary',
        chunk_index=1, start_at=0, end_at=0, filename='source.txt',
        parse_status='completed', chunk_type='summary', image_info='',
        chunk_metadata={'origin': 'model_generated_summary', 'upstream_parent_link': True},
        document_metadata={'source_version': 'v1'},
        source_parent_id='text-parent', source_parent_metadata={'source_version': 'v1'})


def read(tmp_path, row):
    service, _ = make_service(tmp_path)
    async def rows(query, params):
        assert params == ('summary', PRINCIPAL.tenant_id, PRINCIPAL.tenant_id)
        return [copy.deepcopy(row)]
    service._rows = rows
    return asyncio.run(service.read_source_chunk(PRINCIPAL, 'summary'))


def test_generated_summary_keeps_content_and_identity_with_parent_verified_version(tmp_path):
    row = summary_row()
    source = read(tmp_path, row)
    assert source['chunk_id'] == 'summary' and source['chunk_type'] == 'summary'
    assert source['text'] == row['content'] and source['source_version'] == 'v1'
    assert 'source_version' not in row['chunk_metadata']  # No fabricated persisted version.


@pytest.mark.parametrize('change', [
    {'source_parent_id': None},
    {'source_parent_metadata': {}},
    {'source_parent_metadata': {'source_version': 'old'}},
    {'document_metadata': {'source_version': 'new'}},
    {'parse_status': 'processing'},
    {'parse_status': 'pending'},
    {'chunk_metadata': {}},
    {'chunk_metadata': {'origin': 'model_generated_summary', 'upstream_parent_link': False}},
    {'chunk_metadata': {'origin': 'model_generated_summary', 'upstream_parent_link': True, 'source_version': 'old'}},
    {'chunk_metadata': {'origin': 'model_generated_summary', 'upstream_parent_link': True, 'source_version': None}},
    {'chunk_type': 'text'},
])
def test_summary_binding_and_ordinary_missing_version_fail_closed(tmp_path, change):
    row = summary_row(); row.update(change)
    with pytest.raises(LookupError, match='SOURCE_CHUNK_UNAVAILABLE'):
        read(tmp_path, row)
