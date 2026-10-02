from types import SimpleNamespace
from starlette.requests import Request

def test_preview_openapi_describes_binary_and_failure_statuses():
    from backend.app.main import create_app
    paths=create_app().openapi()['paths']
    for suffix in ('source','preview'):
        responses=paths[f'/api/v1/documents/{{document_id}}/{suffix}']['get']['responses']
        assert responses['200']['content']['*/*']['schema']=={'type':'string','format':'binary'}
        assert 'application/json' not in responses['200']['content']
        assert '404' in responses
    assert '503' in paths['/api/v1/documents/{document_id}/preview']['get']['responses']

def test_source_is_inline_and_missing_source_returns_404(tmp_path):
    from backend.app.api.routes import get_document_source
    p=tmp_path/'synthetic.txt'
    p.write_text('synthetic',encoding='utf-8')
    store=SimpleNamespace(get_document_source=lambda _:dict(path=p,file_name='synthetic.txt',media_type='text/plain'))
    request=Request({'type':'http','app':SimpleNamespace(state=SimpleNamespace(container=SimpleNamespace(store=store)))})
    response=get_document_source('test',request)
    assert response.media_type=='text/plain'
    assert response.headers['content-disposition'].startswith('inline;')
    def missing(_):raise LookupError('synthetic')
    store.get_document_source=missing
    assert get_document_source('test',request).status_code==404
