from pathlib import Path
from types import SimpleNamespace
from starlette.requests import Request


def test_declared_layering_has_no_violations():
    from scripts.contract_test import _layering_violations
    assert _layering_violations(Path('backend/app')) == []


def test_model_usage_exports_share_capture():
    from backend.app.application import model_usage as app
    from backend.app.adapters.models import usage as adapter
    assert app.capture_usage is adapter.capture_usage
    with app.capture_usage() as result, app.call_stage('answer'):
        adapter.record_call(model='synthetic', status='ok', latency_ms=1,
                            response={'prompt_eval_count':2,'eval_count':3})
    assert result.summary()['business_total_tokens']==5


def test_preview_uses_injected_port_and_cleanup(tmp_path):
    from backend.app.api.routes import get_document_preview
    from backend.app.ports.office_preview import OfficePreviewFile
    path=tmp_path/'synthetic.pdf'
    path.write_bytes(b'%PDF-synthetic')
    calls=[]
    preview=OfficePreviewFile(path, tmp_path, 'synthetic.pdf')
    office=SimpleNamespace(is_office_document=lambda *a:True,
                           convert_office_to_pdf=lambda *a:preview,
                           cleanup_office_preview=lambda item:calls.append(item))
    source={'path':path,'file_name':'synthetic.docx','media_type':'application/test'}
    container=SimpleNamespace(store=SimpleNamespace(get_document_source=lambda _:source),office_preview=office)
    request=Request({'type':'http','app':SimpleNamespace(state=SimpleNamespace(container=container))})
    response=get_document_preview('synthetic', request)
    assert response.media_type=='application/pdf'
    assert response.background.func is office.cleanup_office_preview
    response.background.func(*response.background.args)
    assert calls==[preview]
