"""SIMULATED fixture/SQL responses; real PDF, parser, chunks and citations.

No live database, OCR executable, VLM, embedding or generation is invoked.
Absent tessdata deliberately exercises the real OCR_UNAVAILABLE branch.
"""
from contextlib import nullcontext
import hashlib
import io
import json
from pathlib import Path

import fitz
import pytest

from backend.app.adapters.parsers import ParserRegistry
from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.adapters.storage import ContentAddressedStorage
from backend.app.domain.chunking import chunk_document
from backend.tests.pdf_closure_fixtures import complex_span_pdf, page_failure_pdf
from backend.tests.test_pdf_table_evidence import chain
from backend.tests.test_version_source_contract import SQLRecorder

FROZEN = Path(__file__).parent / 'fixtures/p2_pdf_closure'


def test_frozen_fixture_hashes_and_reproducible_complex_pdf():
    provenance = json.loads((FROZEN / 'provenance.json').read_text(encoding='utf-8'))
    for entry in provenance['files']:
        raw = (FROZEN / entry['name']).read_bytes()
        assert len(raw) == entry['bytes']
        assert hashlib.sha256(raw).hexdigest() == entry['sha256']
        assert entry['original_poc_matches'] is True
    with fitz.open(FROZEN / 'tables.pdf') as pdf:
        assert len(pdf) == 5
    assert complex_span_pdf() == complex_span_pdf()


def test_real_pdf_unconfirmed_overlap_cannot_become_table_evidence(tmp_path):
    path = tmp_path / 'nonrectangular.pdf'
    path.write_bytes(complex_span_pdf())
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    with fitz.open(path) as pdf:
        candidates = pdf[0].find_tables(strategy='lines').tables
        assert len(candidates) == 1  # Actual extraction, not an injected table.
        assert candidates[0].rows[1].cells[0] == (50.0, 116.0, 290.0, 188.0)
        assert candidates[0].rows[2].cells[0] == (50.0, 152.0, 170.0, 188.0)
        raw_text = pdf[0].get_text('text')
    document = ParserRegistry(tessdata=tmp_path / 'absent').parse(
        path, 'application/pdf', 'doc', 'v1')
    assert not document.tables and not document.table_row_proofs
    assert document.parse_status == 'partial'
    assert 'PDF_TABLE_SPAN_UNCONFIRMED:page=1' in document.parse_warnings
    locator = next(l for l in document.source_locators if l.kind == 'pdf')
    assert locator.raw_text == raw_text and locator.quote == raw_text
    assert locator.raw_evidence['table_candidates']
    drafts = chunk_document(document, max_chars=1800)
    assert drafts and all(c.chunk_type == 'text' for c in drafts)
    assert all(document.markdown_content[c.start:c.end] == c.content for c in drafts)
    _, citation = chain(document, drafts[0], '21 31', 'real-complex-span')
    assert citation['page'] == 1 and citation['kind'] == 'pdf'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == source_hash


@pytest.mark.parametrize('mode', ['empty', 'scan'])
def test_page_failure_retains_native_content_and_exact_page_citation(tmp_path, mode):
    raw = page_failure_pdf(mode, (FROZEN / 'scan_table.png').read_bytes(), with_text=True)
    path = tmp_path / f'{mode}-mixed.pdf'
    path.write_bytes(raw)
    document = ParserRegistry(tessdata=tmp_path / 'absent').parse(
        path, 'application/pdf', 'doc', 'v1')
    assert document.parse_status == 'partial'
    assert 'PDF_TABLE_STRUCTURE_UNSUPPORTED:page=1' in document.parse_warnings
    assert not document.tables
    if mode == 'empty':
        assert not document.assets  # Fixed P2 classifier: no artificial blank-page OCR.
    else:
        source = next(a for a in document.assets if a.asset_type == 'scanned_page')
        failed = next(a for a in document.assets if a.asset_type == 'ocr_text')
        assert source.status == 'ready' and source.page_no == 1
        assert failed.status == 'failed' and failed.error_code == 'OCR_UNAVAILABLE'
        assert failed.derived_from_asset_id == source.asset_id and failed.page_no == 1
    drafts = chunk_document(document)
    assert drafts and all(c.source_locator.page == 2 for c in drafts)
    assert '-7.25' in document.markdown_content
    context, citation = chain(document, drafts[0], 'Retained source amount', f'{mode}-retained')
    assert '-7.25' in context and citation['page'] == 2
    assert citation['quote'] == drafts[0].content


@pytest.mark.parametrize('mode,code', [('empty', 'EMPTY_TEXT'), ('scan', 'OCR_UNAVAILABLE')])
def test_production_job_rejects_no_content_preserves_failed_assets(tmp_path, monkeypatch, mode, code):
    raw = page_failure_pdf(mode, (FROZEN / 'scan_table.png').read_bytes())
    storage = ContentAddressedStorage(tmp_path / 'cas')
    stored = storage.put_stream(io.BytesIO(raw))
    version = dict(id='v1', document_id='doc', knowledge_base_id='kb', version_no=1,
                   file_name=f'{mode}.pdf', media_type='application/pdf',
                   storage_key=stored.storage_key, source_sha256=stored.sha256,
                   original_size=stored.size)

    def handler(sql, params):
        if sql.startswith('SELECT * FROM ingestion_jobs'):
            return {'version_id': 'v1'}
        if sql.startswith('SELECT dv.*'):
            return version
        if sql.startswith('SELECT 1 FROM ingestion_jobs'):
            return (1,)

    engine = SQLRecorder(handler)  # SIMULATED DB only; real production process_job.
    repo = PostgresKnowledgeRepository(engine, storage,
        parsers=ParserRegistry(tessdata=tmp_path / 'absent'))
    monkeypatch.setattr(repo, 'renew_job', lambda *a, **kw: True)
    monkeypatch.setattr(repo, 'update_job_progress', lambda *a, **kw: None)
    monkeypatch.setattr(repo, '_lease_heartbeat', lambda *a, **kw: nullcontext())
    monkeypatch.setattr(repo, 'get_job', lambda *a: {})
    repo.process_job('job', worker_id='worker', claim_token='claim')
    failures = [(sql, params) for sql, params in engine.calls if "status='failed'" in sql]
    assert len(failures) == 2 and all(p['code'] == code for _, p in failures)
    assert not any(sql.startswith('INSERT INTO chunks') or "index_status='ready'" in sql
                   or sql.startswith('UPDATE documents SET active_version_id') for sql, _ in engine.calls)
    assets = [p for sql, p in engine.calls if sql.startswith('INSERT INTO document_assets')]
    if mode == 'empty':
        assert not assets
    else:
        source = next(a for a in assets if a['asset_type'] == 'scanned_page')
        failed = next(a for a in assets if a['asset_type'] == 'ocr_text')
        assert source['status'] == 'ready' and source['page_no'] == 1
        assert failed['status'] == 'failed' and failed['error_code'] == code
        assert failed['derived_from'] == source['id'] and failed['page_no'] == 1
    assert storage.path_for(stored.storage_key).read_bytes() == raw
