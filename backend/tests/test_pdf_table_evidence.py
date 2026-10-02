"""SIMULATED, offline PDF -> table -> retrieval -> context -> citation tests."""
import hashlib
import json
import os
from pathlib import Path

import fitz
import pytest

from backend.app.adapters.parsers import ParserRegistry, PdfParser
from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.application.context_builder import ContextBuilder
from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.chunking import chunk_document
from backend.app.domain.models import ChunkRecord, NormalizedDocument
from backend.app.domain.parsers import ParserError
from backend.app.domain.scope import Scope


@pytest.fixture
def frozen():
    root = os.getenv('RAG_PDF_TEST_FIXTURES')
    if not root:
        pytest.skip('explicit frozen SIMULATED PDF fixture directory required')
    return Path(root)


def chain(document, row, query, tag):
    locator = json.loads(row.source_locator.model_dump_json())
    repo = InMemoryRetrievalRepository()
    repo.add(ChunkRecord('row', 'kb', document.document_id, document.version_id, row.content, locator))
    repo.add(ChunkRecord('private', 'other', document.document_id, document.version_id, row.content, locator))
    result = HybridRetriever(repo).retrieve(Scope.from_ids(['kb']), query)
    assert [i.chunk.chunk_id for i in result.items] == ['row']
    service = CitationService(InMemoryCitationStore())
    context, labels = ContextBuilder().build(tag, result.items, service)
    detail = service.resolve(tag, labels[0])
    assert detail.locator == locator and detail.quote == row.content and row.content in context
    output = os.getenv('RAG_PDF_EVIDENCE_DIR')
    if output:
        (Path(output) / f'{tag}.json').write_text(json.dumps({
            'fixture_kind': 'SIMULATED', 'normalized': document.model_dump(mode='json'),
            'chunks': [c.model_dump(mode='json') for c in chunk_document(document, max_chars=1800)],
            'question': query, 'context': context, 'citation': detail.__dict__,
            'answer_generation': 'NOT_RUN'}, ensure_ascii=False, indent=2), encoding='utf-8')
    return context, locator


@pytest.mark.parametrize('page_no', [1, 2])
def test_frozen_bordered_geometry_rawtext_context_and_page_bbox(frozen, tmp_path, page_no):
    path = tmp_path / 'one.pdf'
    with fitz.open(frozen / 'tables.pdf') as src, fitz.open() as dst:
        dst.insert_pdf(src, from_page=page_no-1, to_page=page_no-1)
        dst.save(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    gold = json.loads((frozen / 'gold.json').read_text(encoding='utf-8'))['cases'][page_no-1]
    d = ParserRegistry(tessdata=tmp_path/'absent').parse(path, 'application/octet-stream', 'doc', 'v1')
    assert len(d.tables) == 1
    t = d.tables[0]
    assert t.page == 1 and list(t.bbox) == gold['designed_table_bbox']
    assert t.cell_range == 'R1C1:R5C3' and t.source_format == 'pdf'
    assert t.header_detection == 'pymupdf-inferred-semantic-UNKNOWN'
    assert all(c.column_header is None for c in t.cells)
    assert t.raw_evidence['rows'] != gold['rows']  # Existing mature API collapses double spaces.
    assert [[None if v is None else ' '.join(v.split()) for v in r] for r in t.raw_evidence['rows']] == [
        [None if v is None else ' '.join(v.split()) for v in r] for r in gold['rows']]
    assert t.raw_evidence['row_cells'] == gold['cells']
    assert any('Two  spaces' in c.native_locator['raw_text'] for c in t.cells)
    raw = next(l.raw_text for l in d.source_locators if l.kind == 'pdf')
    with fitz.open(path) as src:
        assert raw == src[0].get_text('text')
    assert 'Two  spaces' in raw
    assert NormalizedDocument.model_validate_json(d.model_dump_json()).tables == d.tables
    assert d.parse_status == 'partial' and 'PDF_HEADERS_INFERRED_UNKNOWN:page=1' in d.parse_warnings
    drafts = chunk_document(d, max_chars=1800)
    assert len(drafts) == 5 and all(c.chunk_type == 'table' for c in drafts)
    assert all(d.markdown_content[c.start:c.end] == c.content for c in drafts)
    beta = next(c for c in drafts if '"Beta"' in c.content)
    context, locator = chain(d, beta, 'Beta Amount', f'bordered-{page_no}')
    assert 'Beta' in context and '-7.25' in context and 'INFERRED: Amount' in context
    expected_row = 3 if page_no == 1 else 4
    assert locator['page'] == 1 and locator['bbox'] == [50.0, 80.0+36*(expected_row-1), 410.0, 80.0+36*expected_row]
    assert locator['table_bbox'] == gold['designed_table_bbox']
    blank = next(c for c in locator['cells'] if c['coordinate'] == f'R{expected_row}C2')
    assert blank['value'] == '' and blank['bbox']
    if page_no == 2:
        merged = next(c for c in t.cells if c.coordinate == 'R1C1')
        assert merged.column_span == 2 and merged.value == 'Inventory'
        assert t.merged_ranges == ('R1C1:R1C2',)
        assert not any(c.coordinate == 'R1C2' for c in t.cells)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_borderless_scan_status_and_raw_candidates_are_not_complete(frozen, tmp_path):
    d = PdfParser(tessdata=tmp_path/'absent').parse(frozen/'tables.pdf', 'doc', 'v1')
    assert [t.page for t in d.tables] == [1, 2]
    assert d.parse_status == 'partial'
    for page in (3, 4):
        loc = next(l for l in d.source_locators if l.kind == 'pdf' and l.page == page)
        assert loc.raw_evidence['table_status'] == 'partial'
        candidates = loc.raw_evidence['table_candidates']
        assert len(candidates) == 1 and candidates[0]['strategy'] == 'text'
        assert len(candidates[0]['rows']) == 9  # Keep the API's extra empty rows, no gold rewrite.
        assert loc.raw_text and loc.quote == loc.raw_text
    scan = next(l for l in d.source_locators if l.kind == 'pdf' and l.page == 5)
    assert scan.raw_evidence['table_status'] == 'unsupported'
    assert 'PDF_TABLE_STRUCTURE_UNSUPPORTED:page=5' in d.parse_warnings
    assert any(a.asset_type == 'ocr_text' and a.status == 'failed' and a.error_code == 'OCR_UNAVAILABLE' for a in d.assets)
    drafts = chunk_document(d, max_chars=1800)
    assert all(c.source_locator.page for c in drafts)
    assert not any(c.chunk_type == 'table' and c.source_locator.page in (3,4,5) for c in drafts)


def test_cross_page_mixed_text_table_dedup_and_citations(frozen, tmp_path):
    path = tmp_path/'mixed.pdf'
    with fitz.open(frozen/'tables.pdf') as src, fitz.open() as dst:
        dst.insert_pdf(src, from_page=0, to_page=0)
        dst.insert_pdf(src, from_page=0, to_page=0)
        for i, page in enumerate(dst, 1):
            page.insert_text((35, 35), f'Before page {i}: Beta is discussed in prose.')
            page.insert_text((35, 295), f'After page {i}: source notes.')
        dst.save(path)
    d = PdfParser(tessdata=tmp_path/'absent').parse(path, 'doc', 'v1')
    drafts = chunk_document(d, max_chars=1800)
    assert len(d.tables) == 2 and len({t.table_id for t in d.tables}) == 2
    assert ''.join(c.content for c in drafts) == d.markdown_content
    texts = [c for c in drafts if c.chunk_type == 'text']
    assert len(texts) == 4
    assert all('1,234.50' not in c.content and 'Two spaces' not in c.content for c in texts)
    assert {c.source_locator.page for c in texts} == {1,2}
    assert all(c.source_locator.kind == 'pdf' for c in texts)
    assert all(f'Before page {i}: Beta is discussed in prose.' in d.markdown_content for i in (1,2))
    row = next(c for c in drafts if c.chunk_type == 'table' and c.source_locator.page == 2 and '"Beta"' in c.content)
    _, locator = chain(d, row, 'Beta Amount', 'cross-page')
    assert locator['page'] == 2 and locator['bbox'] == [50.0,152.0,410.0,188.0]


def test_pdf_vertical_merge_reuses_origin_on_continuation(tmp_path):
    path = tmp_path/'vertical.pdf'
    with fitz.open() as pdf:
        p = pdf.new_page(width=400,height=300)
        for y in (60,100,140,180):
            p.draw_line((40,y),(340,y)) if y != 140 else p.draw_line((190,y),(340,y))
        for x in (40,190,340):
            p.draw_line((x,60),(x,180))
        for xy,text in [((48,85),'Product'),((198,85),'Quarter'),((48,125),'Widget'),((198,125),'Q1=10'),((198,165),'Q2=20')]:
            p.insert_text(xy,text)
        pdf.save(path)
    d = PdfParser(tessdata=tmp_path/'absent').parse(path,'doc','v1')
    origin = next(c for c in d.tables[0].cells if c.value == 'Widget')
    assert origin.row == 2 and origin.row_span == 2
    assert len([c for c in d.tables[0].cells if c.value == 'Widget']) == 1
    row = chunk_document(d,max_chars=1800)[-1]
    context,locator = chain(d,row,'Widget Q2','vertical-origin')
    assert 'Widget' in context and 'Q2=20' in context and 'Q1=10' not in context
    cell = next(c for c in locator['cells'] if c['value'] == 'Widget')
    assert cell == origin.model_dump(mode='json') and locator['cell_range'] == 'R3C1:R3C2'
    assert locator['bbox'] == [40.0,100.0,340.0,180.0]  # Union includes actual origin extent.


@pytest.mark.parametrize('mode',['empty','scan'])
def test_empty_and_scan_keep_failed_ocr_visible(frozen,tmp_path,mode):
    path = tmp_path/f'{mode}.pdf'
    with fitz.open() as pdf:
        page = pdf.new_page(width=480,height=320)
        if mode == 'scan':
            page.insert_image(page.rect, stream=(frozen/'scan_table.png').read_bytes())
        pdf.save(path)
    d = PdfParser(tessdata=tmp_path/'absent').parse(path,'d','v')
    assert not d.tables and not chunk_document(d)
    assert d.parse_status == 'partial' and 'PDF_TABLE_STRUCTURE_UNSUPPORTED:page=1' in d.parse_warnings
    assert any(a.status == 'failed' and a.error_code == 'OCR_UNAVAILABLE' for a in d.assets)


@pytest.mark.parametrize('raw,code',[(b'not a PDF','PDF_MAGIC_MISMATCH'),(b'%PDF-1.7\ncorrupt','PDF_OPEN_FAILED')])
def test_corrupt_pdf_sanitized_error(tmp_path,raw,code):
    path=tmp_path/'bad.pdf';path.write_bytes(raw)
    with pytest.raises(ParserError,match=f'^{code}$'):
        PdfParser(tessdata=tmp_path/'absent').parse(path,'d','v')
    assert path.read_bytes() == raw


def test_unconfirmed_complex_span_does_not_fake_success(monkeypatch,frozen,tmp_path):
    from types import SimpleNamespace
    import backend.app.adapters.parsers.pdf_tables as helper
    raw = SimpleNamespace(row_count=2,col_count=2,bbox=(0,0,100,100),
        rows=[SimpleNamespace(cells=[(0,0,100,50),None]),SimpleNamespace(cells=[None,None])],
        header=SimpleNamespace(names=['x',None],external=False,bbox=(0,0,100,50),cells=[]),
        extract=lambda:[['x',None],[None,None]])
    monkeypatch.setattr(fitz.Page,'find_tables',lambda *a,**k:SimpleNamespace(tables=[raw]))
    d=PdfParser(tessdata=tmp_path/'absent').parse(frozen/'tables.pdf','d','v')
    assert not d.tables and d.parse_status == 'partial'
    assert any('PDF_TABLE_SPAN_UNCONFIRMED' in w for w in d.parse_warnings)
    assert d.source_locators[0].quote == d.source_locators[0].raw_text
