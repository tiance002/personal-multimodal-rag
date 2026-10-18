"""Configured upload dispatch only; no OCR/model/subprocess is run here."""
from pathlib import Path
import fitz
from backend.weknora_port.ingestion.core import DocumentRequest, ParsedDocument, ParserRegistry, ParseFailure
from backend.weknora_port.ingestion.mineru import MinerUSettings, register_mineru


def pdf_bytes(*, native=False, mixed=False):
    doc=fitz.open()
    page=doc.new_page()
    if native:
        page.insert_text((40,40),'Preserve real native text')
    if mixed:
        doc.new_page()
    raw=doc.tobytes()
    doc.close()
    return raw


def configured(tmp_path,monkeypatch,*,failure=False):
    import backend.weknora_port.ingestion.mineru as module
    calls=[]
    def factory(settings):
        def parse(request):
            calls.append(request.filename)
            return ParsedDocument('' if failure else 'local derived evidence',
                failures=(ParseFailure('parse','LOCAL_OCR_FAILED','RuntimeError','local failure'),) if failure else ())
        return parse
    monkeypatch.setattr(module,'LocalMinerUParser',factory)
    registry=ParserRegistry()
    registry.register('pdf',('.pdf',),lambda r:ParsedDocument('native extraction'))
    settings=MinerUSettings(tmp_path/'python.exe',tmp_path/'models',tmp_path/'tmp')
    register_mineru(registry,settings,default=True)
    return registry,calls


def test_configured_default_images_and_scanned_pdf_use_mineru(tmp_path,monkeypatch):
    registry,calls=configured(tmp_path,monkeypatch)
    for name,data in [('scan.png',b'fixture-only'),('scan.pdf',pdf_bytes())]:
        engine,result=registry.parse(DocumentRequest(name,data))
        assert engine=='mineru-basic' and result.content=='local derived evidence'
    assert calls==['scan.png','scan.pdf']


def test_native_and_mixed_pdf_keep_native_extraction(tmp_path,monkeypatch):
    registry,calls=configured(tmp_path,monkeypatch)
    for mixed in (False,True):
        engine,result=registry.parse(DocumentRequest('native.pdf',pdf_bytes(native=True,mixed=mixed)))
        assert engine=='pdf' and result.content=='native extraction'
    assert calls==[]


def test_missing_or_failed_local_ocr_has_no_parser_fallback(tmp_path,monkeypatch):
    registry,calls=configured(tmp_path,monkeypatch,failure=True)
    engine,result=registry.parse(DocumentRequest('scan.pdf',pdf_bytes()))
    assert engine=='mineru-basic' and not result.content
    assert result.failures[0].code=='LOCAL_OCR_FAILED' and calls==['scan.pdf']
