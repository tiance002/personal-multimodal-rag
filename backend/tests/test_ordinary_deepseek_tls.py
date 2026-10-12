"""OFFLINE/SIMULATED sockets: certificate/hostname guards and no-send proof."""
import http.client
import ssl
from types import SimpleNamespace

import pytest

from backend.app.adapters.models import cloud
from backend.app.domain.model_registry import ModelRegistry
from backend.app.domain.adaptive_chunking import ChunkingConfig
from backend.app.domain.scope import Scope
from backend.app.ports.model_access import model_access
from backend.app.ports.providers import ProviderRequestNotSent, ProviderUnavailable
from backend.app.application.run_metrics import collect_metrics
from backend.tests.test_model_provider_contracts import SimulatedGuard
from backend.app.config import Settings


def test_connect_eof_before_http_is_typed_not_sent(monkeypatch):
    sends=[]
    def failed_connect(self):
        raise ssl.SSLEOFError(8, 'SIMULATED_PRIVATE_ERROR')
    monkeypatch.setattr(http.client.HTTPSConnection, 'connect', failed_connect)
    monkeypatch.setattr(http.client.HTTPConnection, 'send', lambda *args: sends.append(args))
    connection=cloud._ObservedHTTPSConnection('example.invalid')
    with pytest.raises(ProviderRequestNotSent, match='MODEL_CONNECTION_NOT_SENT') as caught:
        connection.connect()
    assert type(caught.value) is cloud._ConnectionNotSent
    assert caught.value.stage=='connection_setup'
    assert caught.value.code=='TLS_EOF_BEFORE_REQUEST'
    assert not sends and 'SIMULATED_PRIVATE_ERROR' not in str(caught.value)


def test_normal_context_keeps_certificate_checks_and_legacy_default(monkeypatch):
    handlers=[]
    class Response:
        headers={}
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def getcode(self):return 200
        def read(self,limit):return b'{"ok":true}'
    def opener(*args):
        handlers.append(args)
        return SimpleNamespace(open=lambda *args,**kw:Response())
    monkeypatch.setattr(cloud,'build_opener',opener)
    from urllib.request import Request
    request=Request('https://example.invalid/models')
    cloud._send(request,5)
    cloud._send(request,5,ordinary_deepseek=True)
    assert len(handlers[0])==2
    handler=handlers[1][-1]
    assert type(handler) is cloud._ObservedHTTPSHandler
    assert handler._context.verify_mode==ssl.CERT_REQUIRED
    assert handler._context.check_hostname is True
    assert handler._context.minimum_version>=ssl.TLSVersion.TLSv1_2


def test_handler_supports_modern_context_only_hostname_check(monkeypatch):
    handler=cloud._ObservedHTTPSHandler(context=ssl.create_default_context())
    if hasattr(handler,'_check_hostname'):del handler._check_hostname
    observed=[]
    monkeypatch.setattr(handler,'do_open',lambda *args,**kwargs:observed.append(kwargs))
    handler.https_open(object())
    assert 'check_hostname' not in observed[0]
    assert observed[0]['context'].check_hostname is True


def test_only_trusted_connection_failure_releases_current_reservation(monkeypatch):
    monkeypatch.setenv('SILICONFLOW_API_KEY','SIMULATED_KEY')
    spec=ModelRegistry.frozen_defaults().select('embedding')
    guard=SimulatedGuard()
    def transport(request,timeout):
        raise cloud._ConnectionNotSent(ssl.SSLEOFError(8,'PRIVATE'))
    adapter=cloud.SiliconFlowEmbedding(spec,enabled=True,usage_guard=guard,transport=transport,
        chunking_index_identity=ChunkingConfig().identity,
        admission=cloud.EmbeddingAdmission(lambda text:3,tokenizer_identity='SIMULATED',non_truncating_verified=True))
    with collect_metrics('SIMULATED') as metrics:
        with model_access('embedding',allowed=True,scope=Scope.from_ids(['kb'])):
            with pytest.raises(ProviderRequestNotSent):adapter.embed(['PUBLIC'],5)
        report=metrics.snapshot(citations=[],error='MODEL_UNAVAILABLE')
    assert guard.settlements==[('1',None,False)]
    assert adapter.receipts[-1]['settlement']=='NOT_SENT'
    assert report['provider_diagnostics'][0]['reason_code']=='TLS_EOF_BEFORE_REQUEST'


def test_late_tls_error_stays_unknown_not_not_sent(monkeypatch):
    monkeypatch.setenv('SILICONFLOW_API_KEY','SIMULATED_KEY')
    guard=SimulatedGuard()
    def late_failure(request,timeout):raise ssl.SSLEOFError(8,'PRIVATE_AFTER_WRITE')
    adapter=cloud.SiliconFlowEmbedding(ModelRegistry.frozen_defaults().select('embedding'),
        enabled=True,usage_guard=guard,transport=late_failure,chunking_index_identity=ChunkingConfig().identity,
        admission=cloud.EmbeddingAdmission(lambda text:3,tokenizer_identity='SIMULATED',non_truncating_verified=True))
    with model_access('embedding',allowed=True,scope=Scope.from_ids(['kb'])):
        with pytest.raises(ProviderUnavailable):adapter.embed(['PUBLIC'],5)
    assert guard.settlements==[('1',None,True)]
    assert adapter.receipts[-1]['settlement']=='UNKNOWN'


@pytest.mark.parametrize('value',[0,-1,121,True,30.5])
def test_ordinary_timeout_is_positive_bounded_integer(value):
    with pytest.raises(ValueError,match='SERVICE_CHAT_TIMEOUT_INVALID'):
        Settings(service_chat_timeout_seconds=value)


def test_api_timeout_changes_only_normal_service_not_frozen_mode(monkeypatch):
    from backend.app.api import routes
    container=SimpleNamespace(knowledge_gateway=None,quick_chain=None,store=None,
        graph=SimpleNamespace(query_graph=None),agent=None,smart_agent=None,ollama=None,
        follow_up_enabled=False,langfuse=None)
    container.store=SimpleNamespace(get_document_content=None,list_documents=None,get_document_access=None)
    monkeypatch.setattr(routes,'AnswerService',lambda **kwargs:kwargs['quick_settings'])
    for ordinary,expected in [(False,30.0),(True,90)]:
        settings=Settings(service_model_policy_json='{"schema_version":1,"policies":[]}' if ordinary else None)
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(container=container,settings=settings)))
        assert routes._answer_service(request).answer_timeout_seconds==expected
