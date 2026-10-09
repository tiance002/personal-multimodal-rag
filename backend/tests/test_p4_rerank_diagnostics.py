"""SIMULATED HTTP only. No provider acceptance or real billing evidence."""
from io import BytesIO
import json
import socket
import ssl
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from backend.app.adapters.models import cloud
from backend.app.adapters.models.cloud import SiliconFlowRerank
from backend.app.application.provider_usage import BudgetUsageGuard
from backend.app.application.retrieval import HybridRetriever
from backend.app.domain.model_registry import ModelRegistry
from backend.app.domain.models import RankedHit
from backend.app.ports.model_access import model_access
from backend.app.ports.model_usage import capture_usage
from backend.app.ports.providers import ProviderRequestNotSent, ProviderUnavailable
from backend.tests.test_p4_rag_pipeline import SCOPE, chunk, repository


SECRET = 'SIMULATED-DIAG-KEY-SENTINEL'
PRIVATE = 'SIMULATED-DIAG-CONTENT-SENTINEL'
RAW_ERROR = 'SIMULATED-DIAG-RAW-ERROR-SENTINEL'


class Ledger:
    """SIMULATED ledger, using the production BudgetUsageGuard unchanged."""
    def __init__(self, fail_settle=False):
        self.rows = []
        self.fail_settle = fail_settle
        self.settlements = 0

    def reserve(self, **kwargs):
        self.rows.append(dict(kwargs, state='RESERVED'))
        return SimpleNamespace(reservation_id=str(len(self.rows)))

    def mark_unknown(self, reservation):
        self.settlements += 1
        if self.fail_settle:
            raise RuntimeError(RAW_ERROR)
        self.rows[int(reservation)-1]['state'] = 'UNKNOWN'

    def release(self, reservation):
        self.settlements += 1
        self.rows[int(reservation)-1]['state'] = 'RELEASED'


def valid():
    return dict(results=[dict(index=0, relevance_score=.8, document={"text": "SIMULATED_ECHO"}),
                         dict(index=1, relevance_score=.4, document={"text": "SIMULATED_ECHO"})],
                usage=dict(prompt_tokens=12, completion_tokens=2, total_tokens=14))


CASES = [
    ('dns', 'transport', 'DNS_FAILED'),
    ('dns_wrapped', 'transport', 'DNS_FAILED'),
    ('connect', 'transport', 'NETWORK_CONNECTION_FAILED'),
    ('connect_wrapped', 'transport', 'NETWORK_CONNECTION_FAILED'),
    ('tls_cert', 'transport', 'TLS_CERTIFICATE_FAILED'),
    ('tls_handshake', 'transport', 'TLS_HANDSHAKE_FAILED'),
    ('tls_wrapped', 'transport', 'TLS_HANDSHAKE_FAILED'),
    ('timeout', 'transport', 'REQUEST_TIMEOUT'),
    ('timeout_wrapped', 'transport', 'REQUEST_TIMEOUT'),
    *[(f'http_{n}', 'http_response', f'HTTP_{n}' if n < 500 else 'HTTP_5XX')
      for n in (401, 403, 404, 429, 500, 503, 599)],
    ('json', 'response_decode', 'JSON_RESPONSE_INVALID'),
    ('encoding', 'response_decode', 'JSON_RESPONSE_INVALID'),
    ('oversize', 'response_decode', 'RESPONSE_TOO_LARGE'),
    ('top_level', 'response_validation', 'RESPONSE_STRUCTURE_INVALID'),
    ('identity', 'response_validation', 'MODEL_IDENTITY_MISMATCH'),
    ('results_missing', 'rerank_validation', 'RERANK_STRUCTURE_INVALID'),
    ('results_not_array', 'rerank_validation', 'RERANK_STRUCTURE_INVALID'),
    ('result_count', 'rerank_validation', 'RERANK_STRUCTURE_INVALID'),
    ('rows', 'rerank_validation', 'RERANK_STRUCTURE_INVALID'),
    ('row_scalar', 'rerank_validation', 'RERANK_STRUCTURE_INVALID'),
    *[(name, 'rerank_validation', 'RERANK_STRUCTURE_INVALID') for name in
      ('document_missing', 'text_missing', 'text_null', 'text_other')],
    ('document_null', 'rerank_validation', 'RERANK_STRUCTURE_INVALID'),
    ('document_other', 'rerank_validation', 'RERANK_STRUCTURE_INVALID'),
    ('duplicate', 'rerank_validation', 'RERANK_MAPPING_INVALID'),
    ('range', 'rerank_validation', 'RERANK_MAPPING_INVALID'),
    ('index_bool', 'rerank_validation', 'RERANK_MAPPING_INVALID'),
    ('index_float', 'rerank_validation', 'RERANK_MAPPING_INVALID'),
    ('index_text', 'rerank_validation', 'RERANK_MAPPING_INVALID'),
    ('document_mismatch', 'rerank_validation', 'RERANK_MAPPING_INVALID'),
    *[(name, 'rerank_validation', 'RERANK_SCORE_INVALID')
      for name in ('nan', 'inf', 'bool_score', 'text_score', 'huge_score')],
    ('usage_parse', 'usage_parse', 'USAGE_PARSE_FAILED'),
    ('settlement', 'usage_settlement', 'USAGE_SETTLEMENT_FAILED'),
]


def outcome(name, monkeypatch):
    errors = {
        'dns': socket.gaierror(RAW_ERROR),
        'dns_wrapped': URLError(socket.gaierror(RAW_ERROR)),
        'connect': ConnectionRefusedError(RAW_ERROR),
        'connect_wrapped': URLError(OSError(RAW_ERROR)),
        'tls_cert': ssl.SSLCertVerificationError(RAW_ERROR),
        'tls_handshake': ssl.SSLError(RAW_ERROR),
        'tls_wrapped': URLError(ssl.SSLError(RAW_ERROR)),
        'timeout': TimeoutError(RAW_ERROR),
        'timeout_wrapped': URLError(TimeoutError(RAW_ERROR)),
    }
    if name in errors:
        return errors[name]
    if name.startswith('http_'):
        return HTTPError('https://invalid.test/' + PRIVATE, int(name[5:]), RAW_ERROR,
                         {'X-Simulated-Secret': SECRET}, BytesIO(PRIVATE.encode()))
    if name == 'top_level': return [PRIVATE]
    result = valid()
    if name == 'identity': result['model'] = PRIVATE
    if name == 'results_missing': result.pop('results')
    if name == 'results_not_array': result['results'] = {'private': PRIVATE}
    if name == 'result_count': result['results'].pop()
    if name == 'rows': result['results'] = {'private': PRIVATE}
    if name == 'row_scalar': result['results'][0] = PRIVATE
    if name == 'document_missing': result['results'][0].pop('document')
    if name == 'text_missing': result['results'][0]['document'] = {}
    if name == 'text_null': result['results'][0]['document']['text'] = None
    if name == 'text_other': result['results'][0]['document']['text'] = 42
    if name == 'document_null': result['results'][0]['document'] = None
    if name == 'document_other': result['results'][0]['document'] = PRIVATE
    if name == 'duplicate': result['results'][1]['index'] = 0
    if name == 'range': result['results'][0]['index'] = 99
    if name == 'document_mismatch': result['results'][0]['document'] = {'text': PRIVATE}
    if name == 'index_bool': result['results'][0]['index'] = True
    if name == 'index_float': result['results'][0]['index'] = 0.0
    if name == 'index_text': result['results'][0]['index'] = '0'
    scores = dict(nan=float('nan'), inf=float('inf'), bool_score=True,
                  text_score=PRIVATE, huge_score=10**1000)
    if name in scores: result['results'][0]['relevance_score'] = scores[name]
    if name == 'usage_parse':
        def fail_usage(_): raise RuntimeError(RAW_ERROR)
        monkeypatch.setattr(cloud, '_rerank_usage', fail_usage)
    return result


def adapter_for(name, monkeypatch):
    monkeypatch.setenv('SILICONFLOW_API_KEY', SECRET)
    spec = ModelRegistry.frozen_defaults().select('rerank')
    ledger = Ledger(fail_settle=name == 'settlement')
    guard = BudgetUsageGuard(ledger, spec, estimate_microunits=100)
    calls = []
    result = outcome(name, monkeypatch)
    def transport(request, timeout):
        # Count only a safe model identifier, never retain the request/header/body.
        calls.append(json.loads(request.data)['model'])
        assert request.full_url == spec.base_url + '/rerank' and timeout == 30
        if name in {'json', 'encoding', 'oversize'}:
            return cloud._send(request, timeout)
        if isinstance(result, Exception): raise result
        # SIMULATED server echo: only the explicit sentinel is substituted.
        payload = json.loads(request.data)
        assert payload['return_documents'] is True
        if isinstance(result, dict) and isinstance(result.get('results'), list):
            for row in result['results']:
                if not isinstance(row, dict): continue
                doc, index = row.get('document'), row.get('index')
                if (isinstance(doc, dict) and doc.get('text') == 'SIMULATED_ECHO'
                        and type(index) is int and 0 <= index < len(payload['documents'])):
                    doc['text'] = payload['documents'][index]
        return result
    if name in {'json', 'encoding', 'oversize'}:
        body = {'json': ('{' + PRIVATE).encode(), 'encoding': b'\xff',
                'oversize': b'x' * 2_000_001}[name]
        class Response(BytesIO):
            def getcode(self): return 200
        class Opener:
            def open(self, request, timeout): return Response(body)
        monkeypatch.setattr(cloud, 'build_opener', lambda *handlers: Opener())
    return SiliconFlowRerank(spec, enabled=True, usage_guard=guard, transport=transport), ledger, calls


@pytest.mark.parametrize('name,stage,code', CASES, ids=[r[0] for r in CASES])
def test_safe_classification_circuit_usage_and_fusion(name, stage, code, monkeypatch, caplog, capsys, request):
    adapter, ledger, calls = adapter_for(name, monkeypatch)
    # Production retrieval sees only synthetic inputs and falls back to Fusion.
    repo = repository(chunk('a', 'synthetic ' + PRIVATE),
                      chunk('b', 'synthetic sample', document_id='doc2'))
    # Explicit permission on this synthetic repository, matching production's
    # server-side KB gate. Without this, the transport is correctly never run.
    repo.embedding_scope_allowed = lambda scope: scope == SCOPE
    expected = HybridRetriever(repo, mode='keyword').retrieve(SCOPE, 'synthetic')
    with capture_usage() as capture:
        actual = HybridRetriever(repo, mode='keyword', ranker=adapter).retrieve(SCOPE, 'synthetic')
        again = HybridRetriever(repo, mode='keyword', ranker=adapter).retrieve(SCOPE, 'synthetic')
    receipt = adapter.receipts[0]
    request.node.user_properties.extend((('receipt_status', receipt['status']),
        ('error_code', receipt.get('error_code', 'ABSENT')),
        ('ledger_state', ledger.rows[0]['state']), ('transport_calls', len(calls))))
    assert receipt['diagnostic_schema'] == 'provider-diagnostic/v1'
    assert (receipt['diagnostic_stage'], receipt['error_code']) == (stage, code)
    assert receipt['status'] != 'ok' and receipt['settlement'] == 'UNKNOWN'
    assert actual.fused_ranking == again.fused_ranking == expected.fused_ranking
    assert actual.items == again.items == expected.items
    assert 'RANKER_UNAVAILABLE' in actual.degradation_flags
    assert 'RANKER_UNAVAILABLE' in again.degradation_flags
    assert adapter.circuit_open and adapter.last_result is None
    assert calls == ['BAAI/bge-reranker-v2-m3']
    assert len(ledger.rows) == ledger.settlements == adapter.requests == len(adapter.receipts) == 1
    assert ledger.rows[0]['estimate_microunits'] == 100
    assert ledger.rows[0]['state'] == ('RESERVED' if name == 'settlement' else 'UNKNOWN')
    assert len(capture.calls) == 1 and capture.calls[0].status == receipt['status']
    assert receipt['usage_actual'] == (valid()['usage'] if name == 'settlement' else None)
    diag = receipt['response_diagnostics']
    assert set(diag['fields']) == {'results', 'model', 'error', 'data'}
    assert all(set(field) == {'present', 'type'} for field in diag['fields'].values())
    assert all(marker not in json.dumps(diag) for marker in (SECRET, PRIVATE, RAW_ERROR))
    if name in {'results_missing', 'results_not_array', 'result_count', 'rows', 'row_scalar',
                'document_null', 'document_other'}:
        assert diag['top_level_type'] == 'object' and diag['expected_results'] == 2
    if name == 'results_missing':
        assert diag['fields']['results'] == {'present': False, 'type': 'missing'}
        assert diag['actual_results'] is None and diag['failure_reason'] == 'RESULTS_MISSING'
    if name in {'results_not_array', 'rows'}:
        assert diag['fields']['results']['type'] == 'object'
        assert diag['actual_results'] is None and diag['failure_reason'] == 'RESULTS_NOT_ARRAY'
    if name == 'result_count':
        assert diag['fields']['results']['type'] == 'array'
        assert diag['actual_results'] == 1 and diag['failure_reason'] == 'RESULT_COUNT_MISMATCH'
        assert diag['first_failure_row'] is None
    if name == 'row_scalar':
        assert diag['failure_reason'] == 'RESULT_ROW_NOT_OBJECT' and diag['first_failure_row'] == 0
    if name in {'document_null', 'document_other'}:
        assert diag['failure_reason'] == 'DOCUMENT_TYPE_INVALID' and diag['first_failure_row'] == 0
        assert diag['document_type'] == ('null' if name == 'document_null' else 'string')
    if name == 'document_missing':
        assert diag['failure_reason'] == 'DOCUMENT_MISSING' and diag['document_type'] == 'missing'
    if name in {'text_missing', 'text_null', 'text_other'}:
        assert diag['failure_reason'] == 'DOCUMENT_TEXT_INVALID' and diag['first_failure_row'] == 0
    if name.startswith('http_'):
        assert diag['http_status_code'] == int(name[5:]) and diag['response_bytes'] is None
    if name in {'json', 'encoding', 'oversize'}:
        assert diag['http_status_code'] == 200
        assert diag['response_bytes'] == (2_000_001 if name == 'oversize' else
            1 + len(PRIVATE.encode()) if name == 'json' else 1)
        assert diag['top_level_type'] == 'unavailable'
    logged = capsys.readouterr()
    safe_output = json.dumps(adapter.receipts) + json.dumps(capture.to_dict()) + caplog.text + logged.out + logged.err
    assert all(marker not in safe_output for marker in (SECRET, PRIVATE, RAW_ERROR))


@pytest.mark.parametrize('name,public', [('row_scalar', 'RERANK_RESPONSE_INVALID'),
    ('document_null', 'RERANK_RESPONSE_INVALID'), ('huge_score', 'RERANK_MAPPING_INVALID'),
    ('timeout', 'MODEL_REQUEST_FAILED'), ('http_401', 'MODEL_HTTP_401'),
    ('settlement', 'MODEL_USAGE_SETTLEMENT_FAILED')])
def test_public_provider_contract(name, public, monkeypatch):
    adapter, _, calls = adapter_for(name, monkeypatch)
    chunks = {id: chunk(id) for id in ('a', 'b')}
    hits = [RankedHit(chunk_id=id, rank=n) for n, id in enumerate(chunks, 1)]
    with model_access('rerank', allowed=True):
        with pytest.raises(ProviderUnavailable) as raised: adapter.rank('synthetic', hits, chunks)
        assert type(raised.value) is ProviderUnavailable
        assert str(raised.value) == public and raised.value.__suppress_context__
        with pytest.raises(ProviderRequestNotSent, match='RERANK_CIRCUIT_OPEN'):
            adapter.rank('synthetic', hits, chunks)
    assert adapter.requests == 1


def test_combined_failure_keeps_primary_safe_code(monkeypatch):
    adapter, ledger, _ = adapter_for('timeout', monkeypatch)
    ledger.fail_settle = True
    with model_access('rerank', allowed=True):
        with pytest.raises(ProviderUnavailable, match='MODEL_USAGE_SETTLEMENT_FAILED'):
            adapter.rank('synthetic', [RankedHit(chunk_id='a', rank=1)], {'a': chunk('a')})
    receipt = adapter.receipts[0]
    assert receipt['error_code'] == 'USAGE_SETTLEMENT_FAILED'
    assert receipt['request_error_code'] == 'REQUEST_TIMEOUT'
    assert receipt['request_diagnostic_stage'] == 'transport'
    assert receipt['status'] == 'error' and ledger.rows[0]['state'] == 'RESERVED'


def test_transport_proven_not_sent_releases_without_poisoning_circuit(monkeypatch):
    adapter, ledger, _ = adapter_for('ok', monkeypatch)
    def deny(*args): raise ProviderRequestNotSent('SIMULATED_LOCAL_DENIAL')
    adapter.transport = deny
    with model_access('rerank', allowed=True):
        with pytest.raises(ProviderRequestNotSent):
            adapter.rank('synthetic', [RankedHit(chunk_id='a', rank=1)], {'a': chunk('a')})
    assert not adapter.circuit_open and ledger.rows[0]['state'] == 'RELEASED'
    receipt = adapter.receipts[0]
    assert (receipt['diagnostic_stage'], receipt['error_code']) == ('admission', 'REQUEST_NOT_SENT')
    assert receipt['settlement'] == 'NOT_SENT' and receipt['status'] == 'not_sent'


def test_success_missing_usage_stays_unknown_and_preserves_metadata(monkeypatch):
    adapter, ledger, _ = adapter_for('ok', monkeypatch)
    adapter.transport = lambda *args: dict(results=[dict(index=0, relevance_score=.8, document={"text": chunk("a").content})])
    hit = RankedHit(chunk_id='a', rank=7, sources=('keyword',), fused_score=.25)
    with model_access('rerank', allowed=True):
        assert adapter.rank('synthetic', [hit], {'a': chunk('a')}) == (hit,)
    assert adapter.last_result.hits[0] is hit and adapter.last_result.usage_actual is None
    assert ledger.rows[0]['state'] == 'UNKNOWN'
    receipt = adapter.receipts[0]
    assert (receipt['diagnostic_stage'], receipt['error_code']) == ('complete', 'NONE')
    assert receipt['settlement'] == 'UNKNOWN' and receipt['status'] == 'ok'
    assert receipt['response_diagnostics']['document_type'] == 'object'
    assert receipt['response_diagnostics']['expected_results'] == 1
    assert receipt['response_diagnostics']['actual_results'] == 1


def test_document_object_remains_accepted_only_when_text_matches(monkeypatch):
    adapter, ledger, calls = adapter_for('ok', monkeypatch)
    chunks = {'a': chunk('a', 'document A'), 'b': chunk('b', 'document B')}
    hits = [RankedHit(chunk_id='a', rank=1), RankedHit(chunk_id='b', rank=2)]
    adapter.transport = lambda *args: dict(results=[
        dict(index=0, relevance_score=.8, document={'text': 'document A'}),
        dict(index=1, relevance_score=.4, document={'text': 'document B'})])
    with model_access('rerank', allowed=True):
        assert adapter.rank('synthetic', hits, chunks) == tuple(hits)
    assert adapter.receipts[0]['response_diagnostics']['document_type'] == 'object'
    assert adapter.receipts[0]['settlement'] == 'UNKNOWN' and ledger.rows[0]['state'] == 'UNKNOWN'
    assert adapter.requests == 1


@pytest.mark.parametrize('swapped', [False, True])
def test_unordered_echo_maps_by_index_and_rejects_swapped_text(monkeypatch, swapped):
    adapter, ledger, calls = adapter_for('ok', monkeypatch)
    chunks = {'a': chunk('a', ' A\n原文 '), 'b': chunk('b', 'B\t42.75')}
    hits = [RankedHit(chunk_id='a', rank=1), RankedHit(chunk_id='b', rank=2)]
    sent = []
    def send(request, timeout):
        payload = json.loads(request.data)
        sent.append(payload['return_documents'])
        return dict(results=[dict(index=i, relevance_score=score,
            document={'text': payload['documents'][1-i if swapped else i]})
            for i, score in [(1, .9), (0, .1)]])
    adapter.transport = send
    with model_access('rerank', allowed=True):
        if swapped:
            with pytest.raises(ProviderUnavailable, match='RERANK_MAPPING_INVALID'):
                adapter.rank('synthetic', hits, chunks)
        else:
            ranked = adapter.rank('synthetic', hits, chunks)
            assert ranked == (hits[1], hits[0]) and ranked[0] is hits[1]
            assert adapter.last_result.relevance_scores == (.9, .1)
    assert sent == [True] and adapter.requests == 1
    assert ledger.rows[0]['state'] == 'UNKNOWN'


def test_diagnostic_capture_failure_does_not_refund_unknown_usage(monkeypatch):
    adapter, ledger, calls = adapter_for('ok', monkeypatch)
    monkeypatch.setattr(cloud, 'record_provider_call',
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError(RAW_ERROR)))
    adapter.transport = lambda *args: dict(results=[dict(index=0, relevance_score=.8, document={"text": chunk("a").content})])
    hit = RankedHit(chunk_id='a', rank=1)
    with model_access('rerank', allowed=True):
        assert adapter.rank('synthetic', [hit], {'a': chunk('a')}) == (hit,)
    receipt = adapter.receipts[0]
    assert receipt['telemetry_error_code'] == 'USAGE_CAPTURE_FAILED'
    assert receipt['settlement'] == 'UNKNOWN' and ledger.rows[0]['state'] == 'UNKNOWN'
    assert adapter.requests == len(ledger.rows) == ledger.settlements == 1
    assert RAW_ERROR not in json.dumps(receipt)


def test_default_transport_has_no_proxy_redirect_or_tls_downgrade(monkeypatch):
    from urllib.request import HTTPSHandler, ProxyHandler, build_opener as real_opener
    seen = []
    class Opener:
        def open(self, request, timeout):
            seen.append('open')
            class Response(BytesIO):
                def getcode(self): return 200
            return Response(b'{"results":[]}')
    def fake_opener(*handlers):
        assert len(handlers) == 2
        assert isinstance(handlers[0], cloud._NoRedirect)
        assert handlers[0].redirect_request(None, None, None, None, None, None) is None
        assert isinstance(handlers[1], ProxyHandler) and handlers[1].proxies == {}
        # Constructing urllib's default handler performs no network operation.
        default = real_opener(*handlers)
        https = next(h for h in default.handlers if isinstance(h, HTTPSHandler))
        context = getattr(https, '_context', None) or ssl.create_default_context()
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        return Opener()
    monkeypatch.setattr(cloud, 'build_opener', fake_opener)
    response = cloud._send(cloud.Request('https://invalid.test'), 30)
    assert response.payload == {'results': []}
    assert response.http_status_code == 200 and response.response_bytes == len(b'{"results":[]}')
    assert seen == ['open']
