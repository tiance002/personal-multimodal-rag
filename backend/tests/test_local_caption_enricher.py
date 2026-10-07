"""SIMULATED image/provider fixtures; no real generation or ingestion DB."""
import hashlib
import io
import threading

import pytest
from PIL import Image

from backend.app.domain.chunking import chunk_document
from backend.app.domain.models import DocumentAsset,DocumentBlock,DocumentSection,NormalizedDocument
from backend.app.ports.providers import ProviderUnavailable


def document(count=1,size=(8,8)):
    stream=io.BytesIO();Image.new('1',size).save(stream,format='PNG');raw=stream.getvalue()
    assets=[DocumentAsset(asset_id=f'image-{i}',asset_type='source_image',page_no=i+1,source_bytes=raw,
        source_locator={'sha256':hashlib.sha256(raw).hexdigest(),'page':i+1,'bbox':(10,20,30,40),
            'coordinate_basis':'pdf-points-top-left-unrotated'}) for i in range(count)]
    text='原生文字与OCR保持原样。\n'
    return NormalizedDocument(document_id='doc',version_id='v',title='SIMULATED',media_type='application/pdf',
        markdown_content=text,sections=[DocumentSection(section_id='native',heading='',level=0,start=0,end=len(text),page_start=1,page_end=1)],
        blocks=[DocumentBlock(block_id='native',kind='text',start=0,end=len(text))],assets=assets,
        content_sha256=hashlib.sha256(text.encode()).hexdigest(),parser_version='SIMULATED/v1')


class FakeProvider:
    provider_kind='local';chat_model='SIMULATED-requested-model'
    def __init__(self):self.calls=[];self.preflights=[];self.error=None;self.after=None;self.preflight_error=None
    def caption_preflight(self,timeout_seconds):
        self.preflights.append(timeout_seconds)
        if self.preflight_error:raise self.preflight_error
    def caption_image(self,image_bytes,timeout_seconds):
        from backend.app.ports.providers import CaptionResult
        self.calls.append((image_bytes,timeout_seconds))
        if self.error:raise self.error
        if self.after:self.after()
        return CaptionResult(text='SIMULATED 曲线先升后降。',model_requested=self.chat_model,
            model_reported='SIMULATED-observed-model',model_digest=None,
            input_image_sha256=hashlib.sha256(image_bytes).hexdigest(),finish_reason='stop',
            usage_actual={'input_tokens':11,'output_tokens':8},latency_ms=1)


def enrich(provider,doc,**kwargs):
    from backend.app.application.local_caption import LocalCaptionEnricher
    return LocalCaptionEnricher(provider,enabled=kwargs.pop('enabled',True),clock=kwargs.pop('clock',None)).enrich(doc,**kwargs)


def test_disabled_and_plain_text_are_exact_noop_without_transport():
    p=FakeProvider();doc=document()
    assert enrich(p,doc,enabled=False) is doc
    text=doc.model_copy(update={'assets':[]})
    assert enrich(p,text) is text
    assert p.calls==[] and p.preflights==[]


def test_caption_appends_derivative_and_reuses_A_source_contract_without_changing_original():
    p=FakeProvider();doc=document(2);before=doc.model_dump_json()
    result=enrich(p,doc)
    assert doc.model_dump_json()==before and result.markdown_content.startswith(doc.markdown_content)
    assert result.sections[:len(doc.sections)]==doc.sections
    captions=[a for a in result.assets if a.asset_type=='caption']
    assert len(captions)==2 and len(p.calls)==2 and len(p.preflights)==1
    assert [a.derived_from_asset_id for a in captions]==['image-0','image-1']
    for asset in captions:
        assert asset.status=='ready' and asset.source_locator['model_reported']=='SIMULATED-observed-model'
        assert asset.source_locator['model_digest']=='UNKNOWN'
        assert asset.source_locator['usage_actual']=={'input_tokens':11,'output_tokens':8}
    chunks=chunk_document(result)
    assert len([c for c in chunks if c.chunk_type=='image_caption'])==2
    assert chunks[0].content==doc.markdown_content


def test_document_image_and_call_caps_are_four_with_explicit_partial_status():
    p=FakeProvider();result=enrich(p,document(6))
    assert len(p.calls)==4 and len(p.preflights)==1
    assert len([a for a in result.assets if a.asset_type=='caption'])==4
    assert any('CAPTION_DOC_LIMIT' in w for w in result.parse_warnings)


@pytest.mark.parametrize('fault',['hash','bytes','pixels','missing_bytes'])
def test_bad_input_is_not_sent_to_provider(fault):
    doc=document(size=(2001,2000) if fault=='pixels' else (8,8));source=doc.assets[0]
    if fault=='hash':source=source.model_copy(update={'source_locator':{**source.source_locator,'sha256':'0'*64}})
    if fault=='bytes':source=source.model_copy(update={'source_bytes':b'x'*(4*1024*1024+1)})
    if fault=='missing_bytes':source=source.model_copy(update={'source_bytes':None})
    p=FakeProvider();result=enrich(p,doc.model_copy(update={'assets':[source]}))
    assert not p.calls and not p.preflights
    assert next(a for a in result.assets if a.asset_type=='caption').status=='failed'


@pytest.mark.parametrize('failure',['timeout','busy','unavailable'])
def test_errors_never_make_ready_caption_or_retry(failure):
    p=FakeProvider()
    if failure=='timeout':p.error=TimeoutError()
    elif failure=='busy':p.preflight_error=ProviderUnavailable('CAPTION_RESOURCE_BUSY')
    else:p.error=ProviderUnavailable('untrusted diagnostic must not be copied')
    result=enrich(p,document())
    failed=next(a for a in result.assets if a.asset_type=='caption')
    assert failed.status=='failed' and failed.text_content is None
    assert failed.error_code.startswith('CAPTION_') and 'untrusted' not in str(failed.source_locator)
    assert len(p.calls)<=1
    assert not any(s.content_type=='image_caption' for s in result.sections)


@pytest.mark.parametrize('moment',['before','after'])
def test_cancellation_stops_new_calls_and_rejects_late_result(moment):
    p=FakeProvider();cancelled=[moment=='before']
    p.after=lambda:cancelled.__setitem__(0,True)
    result=enrich(p,document(2),should_continue=lambda:not cancelled[0])
    assert len(p.calls)==(0 if moment=='before' else 1)
    assert not any(a.asset_type=='caption' and a.status=='ready' for a in result.assets)


@pytest.mark.parametrize('elapsed,code',[(46,'CAPTION_TIMEOUT'),(91,'CAPTION_DEADLINE')])
def test_request_and_document_deadlines_reject_late_responses(elapsed,code):
    p=FakeProvider();clock=[0.0];p.after=lambda:clock.__setitem__(0,float(elapsed))
    result=enrich(p,document(2),clock=lambda:clock[0])
    assert len(p.calls)==1 and p.calls[0][1]<=45
    assert not any(a.asset_type=='caption' and a.status=='ready' for a in result.assets)
    assert any(a.error_code==code for a in result.assets if a.asset_type=='caption')


def test_process_local_caption_calls_are_serial():
    entered=threading.Event();release=threading.Event();second_entered=threading.Event()
    p=FakeProvider();original=p.caption_image
    def call(data,timeout):
        if not entered.is_set():entered.set();assert release.wait(2)
        else:second_entered.set()
        return original(data,timeout)
    p.caption_image=call
    results=[]
    first=threading.Thread(target=lambda:results.append(enrich(p,document())))
    second=threading.Thread(target=lambda:results.append(enrich(p,document())))
    first.start();assert entered.wait(2);second.start()
    try:assert not second_entered.wait(.1)
    finally:release.set();first.join(2);second.join(2)
    assert not first.is_alive() and not second.is_alive() and len(results)==2


def numeric_table_document(count=1, *, parse_status='complete'):
    """SIMULATED mixed normalization, not a claim about PDF table extraction."""
    from backend.app.domain.models import DocumentTable, TableCell
    from backend.app.domain.table_evidence import table_row_texts
    doc = document(count)
    headers = ['\u95e8\u5e97', '\u7edf\u8ba1\u6708\u4efd', '\u6536\u5165\uff08\u5143\uff09']
    values = ['SIMULATED-store', '2025-02', '80']
    cells = []
    for row, data in [(1, headers), (2, values)]:
        for column, value in enumerate(data, 1):
            cells.append(TableCell(coordinate=f'R{row}C{column}', row=row, column=column,
                value=value, value_type='string', display=value, column_headers=(headers[column-1],),
                column_header=row == 1))
    start = len(doc.markdown_content)
    table = DocumentTable(table_id='SIMULATED-table', cell_range='R1C1:R2C3', header_rows=(1,),
        source_format='html', header_detection='html-source-policy-v1', cells=cells, start=start, end=start)
    rendered = ''.join(content for _, content in table_row_texts(table))
    end = start + len(rendered)
    return doc.model_copy(update={'tables':[table.model_copy(update={'end':end})],
        'markdown_content':doc.markdown_content + rendered,
        'blocks':[*doc.blocks, DocumentBlock(block_id='table', kind='table', table_id=table.table_id, start=start, end=end)],
        'content_sha256':hashlib.sha256((doc.markdown_content + rendered).encode()).hexdigest(),
        'parse_status':parse_status, 'parse_warnings':('SIMULATED-original-warning',)})


def numeric_support(doc):
    from backend.app.application.query_router import EvidenceTarget
    from backend.app.application.structured_evidence import row_facts
    target = EvidenceTarget('SIMULATED-store', '\u6536\u5165', '2025-02', '\u5143')
    return tuple(fact for chunk in chunk_document(doc) if chunk.chunk_type == 'table'
        for fact in row_facts(chunk.content, chunk.source_locator.model_dump(mode='json'), target,
            chunk.content_sha256))


@pytest.mark.parametrize('outcome',['success','failure','limit'])
def test_caption_outcomes_preserve_valid_native_numeric_support(outcome):
    doc = numeric_table_document(6 if outcome == 'limit' else 1)
    before = doc.model_dump_json()
    supported = numeric_support(doc)
    assert len(supported) == 1 and str(supported[0].value) == '80'
    p = FakeProvider()
    if outcome == 'failure': p.error = ProviderUnavailable('CAPTION_OUTPUT_INVALID')
    result = enrich(p, doc)
    assert doc.model_dump_json() == before
    assert result.parse_status == doc.parse_status
    assert numeric_support(result) == supported
    if outcome != 'failure':
        assert any(a.asset_type == 'caption' and a.status == 'ready' for a in result.assets)
    else:
        assert any(a.asset_type == 'caption' and a.status == 'failed' for a in result.assets)


def test_enrichment_does_not_promote_incomplete_original_evidence():
    doc = numeric_table_document(parse_status='partial')
    assert numeric_support(doc) == ()
    result = enrich(FakeProvider(), doc)
    assert result.parse_status == 'partial' and numeric_support(result) == ()


@pytest.mark.parametrize('outcome',['success','failure','limit'])
def test_repeated_enrichment_is_exact_noop_with_no_extra_calls(outcome):
    p = FakeProvider()
    if outcome == 'failure': p.error = ProviderUnavailable('CAPTION_OUTPUT_INVALID')
    first = enrich(p, document(6 if outcome == 'limit' else 1))
    before = first.model_dump_json()
    calls, preflights = len(p.calls), len(p.preflights)
    second = enrich(p, first)
    assert second is first and second.model_dump_json() == before
    assert len(p.calls) == calls and len(p.preflights) == preflights


def test_wrapped_transport_timeout_halts_remaining_images(monkeypatch):
    from backend.app.adapters.models import ollama
    from backend.tests.test_local_caption_gateway import transport
    calls, _ = transport(monkeypatch, error=ollama.URLError(TimeoutError()))
    gateway = ollama.OllamaGateway('http://127.0.0.1:11434', 'qwen3.5:4b', 'bge-m3:latest')
    result = enrich(gateway, document(2))
    assert len([request for request, _ in calls if request.full_url.endswith('/api/chat')]) == 1
    captions = [a for a in result.assets if a.asset_type == 'caption']
    assert len(captions) == 2 and all(a.status == 'failed' and a.error_code == 'CAPTION_TIMEOUT' for a in captions)
