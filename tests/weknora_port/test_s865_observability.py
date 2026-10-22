"""Formal tracing contracts; fake SDK/SQL/HTTP only, no external sends."""
import asyncio
import base64
import json
import threading
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from backend.weknora_port.observability import LangfuseObservability, ObservabilitySettings, digest
from backend.weknora_port.models import provider_config, ProviderModel, AsyncHTTPTransport, Message, ChatOptions
from backend.weknora_port.models.transport import endpoint_urls
from backend.weknora_port.models.types import TokenUsage, PreparedCall, CallContext, TrustedScope, ProviderError
from backend.weknora_port.runtime import ApplicationRuntime, BoundOrdinaryService, LeasedSendGate
from test_p31_provider_calls import DB
from test_service import REQ, runtime as ordinary_runtime


class Span:
    def __init__(self, number, fields):
        self.id = f'{number:016x}'
        self.fields = fields
        self.updates = []
        self.ends = 0
        self.fail = False
    def update(self, **fields):
        self.updates.append(fields)
        if self.fail: raise RuntimeError('PRIVATE failure sk-lf-never-log')
    def end(self):
        self.ends += 1
        if self.fail: raise RuntimeError('PRIVATE failure sk-lf-never-log')


class SDK:
    def __init__(self, **options):
        self.options = options
        self.spans = []
        self.closed = []
        self.fail = False
    def start_observation(self, **fields):
        if self.fail: raise RuntimeError('PRIVATE failure sk-lf-never-log')
        span = Span(len(self.spans) + 1, fields)
        self.spans.append(span)
        return span
    def flush(self): self.closed.append(('flush', threading.get_ident()))
    def shutdown(self): self.closed.append(('shutdown', threading.get_ident()))


def observer(**options):
    sdk = SDK()
    cfg = ObservabilitySettings(True, True, public_key='synthetic-public', secret_key='synthetic-secret', **options)
    def factory(**kwargs): sdk.options = kwargs; return sdk
    return LangfuseObservability(cfg, client_factory=factory), sdk


def turn():
    return SimpleNamespace(run_id='00000000-0000-0000-0000-000000000001', session_id='private-session',
        client_request_id='private-client-request', replay_only=False,
        request=SimpleNamespace(content='PRIVATE prompt', request_id='server-request'),
        context=CallContext('server-request', TrustedScope('owner', ('kb',))))


def call(request='provider-operation', capability='chat'):
    return PreparedCall(CallContext(request, TrustedScope('owner', ('kb',))),
        'deepseek', 'deepseek-flash', 'model-id', capability, 'https://fixture.invalid', 'a' * 64, 64, 64)


@pytest.mark.parametrize('enabled,egress,consented', [(False, True, True), (True, False, True), (True, True, False)])
def test_disabled_or_unconsented_never_initializes_sdk(enabled, egress, consented):
    def forbidden(**kwargs): raise AssertionError('must not initialize SDK')
    obs = LangfuseObservability(ObservabilitySettings(enabled, egress), client_factory=forbidden)
    assert obs.begin_turn(turn(), cloud_consented=consented) is None
    obs.provider_started(call(), cloud_consented=consented)
    assert obs._client is None and not obs._calls


def test_metadata_only_hierarchy_cross_task_settlement_and_no_duplicate_end():
    async def run():
        obs, sdk = observer()
        accepted = turn()
        root = obs.begin_turn(accepted, cloud_consented=True)
        obs.stage(accepted.run_id, 'retrieval', 'started', {'candidate_count': 4, 'prompt': 'PRIVATE'})
        obs.provider_started(call(), cloud_consented=True)
        obs.provider_started(call(), cloud_consented=True)
        assert len(sdk.spans) == 3
        provider = sdk.spans[-1]
        assert provider.fields['metadata']['stage'] == 'retrieval' and provider.fields['metadata']['role'] == 'retrieval'
        assert provider.fields['metadata']['cost_status'] == 'UNPRICED'
        assert provider.fields['trace_context'] == {'trace_id': root.trace_id, 'parent_span_id': sdk.spans[1].id}
        async def settle():
            obs.provider_finished(call(), outcome='RESPONSE_RECEIVED', usage=TokenUsage(10, None, 10, 'REPORTED'), settlement_success=False)
        await asyncio.create_task(settle())
        obs.provider_finished(call(), outcome='UNKNOWN', usage=TokenUsage(), settlement_success=True)
        assert provider.updates[-1]['usage_details'] == {'input': 10, 'total': 10}
        assert provider.updates[-1]['metadata']['settlement_success'] is False
        obs.stage(accepted.run_id, 'retrieval', 'completed', {'candidate_count': 4})
        obs.finish_turn(root, status='completed')
        obs.finish_turn(root, status='failed')
        assert all(span.ends == 1 for span in sdk.spans)
        raw = json.dumps([(span.fields, span.updates) for span in sdk.spans])
        assert 'PRIVATE' not in raw and 'private-client-request' not in raw and 'private-session' not in raw
        assert accepted.run_id in raw and digest(accepted.client_request_id) in raw
        assert all('input' not in span.fields and 'output' not in span.fields for span in sdk.spans)
        obs.provider_started(call('background', 'vision'), cloud_consented=True)
        bg, vision = sdk.spans[-2:]
        assert bg.fields['name'] == 'weknora.background-provider'
        assert vision.fields['trace_context']['parent_span_id'] == bg.id
        assert vision.fields['trace_context']['trace_id'] != root.trace_id
        obs.provider_finished(call('background', 'vision'), outcome='UNKNOWN', usage=TokenUsage(), settlement_success=True)
        assert 'usage_details' not in vision.updates[-1] and vision.ends == bg.ends == 1
        main_thread = threading.get_ident()
        await obs.shutdown(); await obs.shutdown()
        assert [name for name, _ in sdk.closed] == ['flush', 'shutdown']
        assert all(thread != main_thread for _, thread in sdk.closed)
    asyncio.run(run())


def test_opt_in_content_is_redacted_before_sdk_or_media_processing():
    obs, sdk = observer(capture_content=True)
    accepted = turn()
    accepted.request.content = 'someone@example.com Bearer TOKEN api_key=SECRET sk-lf-xxx data:image/png;base64,AAAA ![x](https://private/image.png) synthetic-secret {"api_key": "HIDDENJSON"} SK-UPPERSECRET'
    root = obs.begin_turn(accepted, cloud_consented=True)
    value = sdk.spans[0].fields['input']
    assert 'example.com' not in value and 'TOKEN' not in value and 'SECRET' not in value
    assert 'AAAA' not in value and 'image.png' not in value and 'synthetic-secret' not in value
    assert 'HIDDENJSON' not in value and 'UPPERSECRET' not in value
    assert sdk.options['mask'](data={'authorization': 'PRIVATE'}) == {'authorization': '[REDACTED]'}
    obs.finish_turn(root, status='cancelled')


def test_sdk_failures_are_isolated_and_safe_diagnostics(caplog):
    obs, sdk = observer()
    sdk.fail = True
    assert obs.begin_turn(turn(), cloud_consented=True) is None
    sdk.fail = False
    root = obs.begin_turn(turn(), cloud_consented=True)
    root.span.fail = True
    obs.finish_turn(root, status='failed')
    assert root.closed and root.span.ends == 1
    def fail(): raise RuntimeError('PRIVATE sk-lf-never-log')
    sdk.flush = fail; sdk.shutdown = fail
    asyncio.run(obs.shutdown())
    assert 'LANGFUSE_START_FAILED' in caplog.text and 'LANGFUSE_FLUSH_FAILED' in caplog.text
    assert 'PRIVATE' not in caplog.text and 'never-log' not in caplog.text


def test_resource_shutdown_closes_pending_root_stage_and_background_observations_once():
    async def run():
        obs, sdk = observer()
        accepted = turn()
        root = obs.begin_turn(accepted, cloud_consented=True)
        obs.stage(accepted.run_id, 'generation', 'started', {'role': 'cheap'})
        obs.provider_started(call(), cloud_consented=True)
        await obs.shutdown()
        obs.finish_turn(root)
        assert all(span.ends == 1 for span in sdk.spans)
        assert not obs._turns and not obs._calls
    asyncio.run(run())


def test_settings_snapshot_files_legacy_basic_and_model_cloud_are_independent(tmp_path, monkeypatch):
    from backend.weknora_port.development import DevelopmentSettings
    from test_development_entry import settings, development_environment
    env = development_environment(settings(tmp_path))
    monkeypatch.setenv('RAG_LANGFUSE_ENABLED', 'true')
    assert not DevelopmentSettings.from_environment(env).observability.enabled
    auth = tmp_path / 'auth'
    auth.write_text('Basic ' + base64.b64encode(b'public-synthetic:secret-synthetic').decode())
    env.update(RAG_LANGFUSE_ENABLED='true', RAG_LANGFUSE_EGRESS_ENABLED='true',
        LANGFUSE_AUTHORIZATION_FILE=str(auth), LANGFUSE_BASE_URL='https://example.invalid',
        LANGFUSE_TRACING_ENVIRONMENT='synthetic-test', LANGFUSE_RELEASE='synthetic-release')
    cfg = DevelopmentSettings.from_environment(env)
    assert cfg.observability.secret_key == 'secret-synthetic' and cfg.observability.public_key == 'public-synthetic'
    assert not cfg.cloud_allowed and not cfg.observability.capture_content
    assert cfg.observability.environment == 'synthetic-test' and cfg.observability.release == 'synthetic-release'
    assert 'secret-synthetic' not in repr(cfg)
    auth.write_text('Bearer not-basic')
    assert not DevelopmentSettings.from_environment(env).observability.secret_key
    env['RAG_LANGFUSE_EGRESS_ENABLED'] = 'false'
    auth.unlink()
    assert not DevelopmentSettings.from_environment(env).observability.secret_key


def test_malformed_final_annotations_still_close_root_stages_and_reset_context(caplog):
    obs, sdk = observer()
    accepted = turn()
    root = obs.begin_turn(accepted, cloud_consented=True)
    obs.stage(accepted.run_id, 'generation', 'started', {'role': 'cheap', 'attempt_id': 'server/answer/1/cheap'})
    obs.finish_turn(root, result=SimpleNamespace(), status='completed')
    assert root.closed and all(span.ends == 1 for span in sdk.spans)
    obs.provider_started(call(), cloud_consented=True)
    assert sdk.spans[-2].fields['name'] == 'weknora.background-provider'
    assert 'LANGFUSE_ROOT_UPDATE_FAILED' in caplog.text
    obs.provider_finished(call(), outcome='UNKNOWN', usage=None, settlement_success=False)


def test_zero_attempt_known_no_evidence_quality_reaches_root_and_quality_span():
    from backend.weknora_port.generation.types import CitationQuality
    obs, sdk = observer()
    root = obs.begin_turn(turn(), cloud_consented=True)
    quality = CitationQuality('EVIDENCE_INSUFFICIENT', 0, 0, 0, True)
    result = SimpleNamespace(selected_role='fallback', selected_attempt_id='', attempts=(),
        route=None, finish_reason='stop', truncated=False, upgraded=False, references=(),
        answer='PRIVATE', citation_quality=quality)
    obs.finish_turn(root, result=result, status='completed')
    expected = {'citation_status': 'EVIDENCE_INSUFFICIENT', 'semantic_support': 'NOT_ASSESSED',
                'usable_evidence_count': 0, 'assessment_complete': True}
    metadata = sdk.spans[0].updates[-1]['metadata']
    assert {key: metadata[key] for key in expected} == expected
    check = next(span for span in sdk.spans if span.fields['name'] == 'weknora.quality-check')
    assert {key: check.fields['metadata'][key] for key in expected} == expected
    assert all(span.ends == 1 for span in sdk.spans)


def test_final_route_attempt_quality_and_citation_count_are_metadata_only():
    from backend.weknora_port.generation.types import AttemptRecord, CitationQuality
    obs, sdk = observer()
    accepted = turn()
    root = obs.begin_turn(accepted, cloud_consented=True)
    record = AttemptRecord('run/answer/1/cheap', 1, 'cheap', 'deepseek', 'model', 'https://PRIVATE',
        'a' * 64, 'b' * 64, 'c' * 64,
        citation_quality=CitationQuality('VALID', 1, 0, 1, True, 'NOT_ASSESSED'))
    result = SimpleNamespace(selected_role='cheap', selected_attempt_id=record.attempt_id, attempts=(record,),
        route={'score': '0.2', 'route_reason': 'SCORE_BELOW_THRESHOLD', 'question': 'PRIVATE'},
        finish_reason='stop', truncated=False, upgraded=False, references=({'PRIVATE': 'not exported'},), answer='PRIVATE')
    obs.finish_turn(root, result=result, status='completed')
    fields = sdk.spans[0].updates[-1]['metadata']
    assert fields['citation_count'] == 1 and fields['semantic_support'] == 'NOT_ASSESSED'
    assert fields['cost_status'] == 'UNPRICED' and fields['score'] == '0.2'
    assert {span.fields['name'] for span in sdk.spans} >= {'weknora.attempt-result', 'weknora.quality-check'}
    assert all(span.ends == 1 for span in sdk.spans)
    assert 'PRIVATE' not in json.dumps([(span.fields, span.updates) for span in sdk.spans])


class ClaimDB(DB):
    def __init__(self): super().__init__(); self.in_transaction = False; self.local = False
    @asynccontextmanager
    async def transaction(self):
        self.in_transaction = True
        try: yield
        finally: self.in_transaction = False
    async def execute(self, sql, args=()):
        result = await super().execute(sql, args)
        if sql.startswith('UPDATE port_provider_calls') and not self.fail_usage:
            async def fetchall(): return [{'request_id': 'synthetic'}]
            return SimpleNamespace(fetchall=fetchall)
        if self.local and sql.startswith('SELECT source FROM models'):
            async def fetchall(): return [{'source': 'local'}]
            return SimpleNamespace(fetchall=fetchall)
        return result


@pytest.mark.parametrize('provider', ['siliconflow', 'deepseek'])
@pytest.mark.parametrize('usage', [None, {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}])
def test_streaming_actual_provider_usage_and_post_commit_claim_correlate(provider, usage):
    async def run():
        db = ClaimDB()
        @asynccontextmanager
        async def lease(): yield SimpleNamespace(connection=db)
        obs, sdk = observer()
        app = SimpleNamespace(principal=SimpleNamespace(tenant_id=1, subject_id='owner'),
            leases=SimpleNamespace(lease=lease), cloud_allowed=True, observability=obs)
        cfg = provider_config(provider, 'test')
        sent = []
        def handler(request):
            assert not db.in_transaction
            assert len(sdk.spans) == 2  # observation begins only after the durable claim transaction exits
            sent.append(request)
            row = {'model': cfg.model, 'choices': [{'delta': {'content': 'PRIVATE answer'}, 'finish_reason': 'stop'}]}
            if usage: row['usage'] = usage
            return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                content='data: '+json.dumps(row)+'\n\ndata: [DONE]\n\n')
        transport = AsyncHTTPTransport(allowed_endpoints=endpoint_urls(cfg), gate=LeasedSendGate(app),
            credential=lambda _: 'SYNTHETIC', http_transport=httpx.MockTransport(handler))
        model = ProviderModel(cfg, transport)
        ctx = CallContext('stream', TrustedScope('owner', ('kb',)))
        try:
            assert [item async for item in model.chat_stream((Message('user', 'PRIVATE prompt'),), ChatOptions(max_tokens=64), ctx)]
            span = sdk.spans[-1]
            assert span.ends == 1 and span.fields['metadata']['provider'] == provider
            assert span.updates[-1]['metadata']['settlement_success'] is True
            if usage: assert span.updates[-1]['usage_details'] == {'input': 10, 'output': 5, 'total': 15}
            else: assert 'usage_details' not in span.updates[-1]
            with pytest.raises(ProviderError, match='MODEL_CALL_ID_REUSE_DENIED'):
                await model.chat((Message('user', 'PRIVATE prompt'),), ChatOptions(max_tokens=64), ctx)
            assert len(sent) == 1 and len(sdk.spans) == 2
        finally: await transport.aclose(); await obs.shutdown()
    asyncio.run(run())


def test_local_model_can_run_while_kb_denies_all_tracing_and_settlement_failure_preserves_usage():
    async def run():
        db = ClaimDB(); db.local = True
        @asynccontextmanager
        async def lease(): yield SimpleNamespace(connection=db)
        obs, sdk = observer()
        app = SimpleNamespace(principal=SimpleNamespace(tenant_id=1, subject_id='owner'),
            leases=SimpleNamespace(lease=lease), cloud_allowed=False, observability=obs)
        gate = LeasedSendGate(app)
        db.cloud = False
        await gate.authorize(call('denied-tracing'))
        assert not sdk.spans
        db.cloud = True
        c = call('usage-write-failure')
        await gate.authorize(c)
        db.fail_usage = True
        assert await gate.finish(c, outcome='RESPONSE_RECEIVED', usage=TokenUsage(3, 2, 5, 'REPORTED')) is False
        span = sdk.spans[-1]
        assert span.updates[-1]['usage_details']['total'] == 5 and span.ends == 1
        assert span.updates[-1]['metadata']['settlement_error'] == 'PERSISTENCE_FAILED'
    asyncio.run(run())


def test_bound_lifecycle_replay_concurrent_duplicate_cancel_and_stage_closure():
    async def run():
        release = asyncio.Event()
        service, repo, _, _, _, _, _, _, _ = ordinary_runtime(blocked=release)
        obs, sdk = observer()
        app = SimpleNamespace(observability=obs, _runs={}, memory_hook_errors=[],
            principal=SimpleNamespace(tenant_id=1, subject_id='owner'))
        async def start(accepted): return obs.begin_turn(accepted, cloud_consented=True)
        async def rows(*args): return [{'status': 'cancelled', 'error_code': 'MODEL_CALL_CANCELLED'}]
        async def memory(*args): raise AssertionError('cancelled turn must not write memory')
        app._start_turn_trace = start; app._rows = rows; app.record_turn_memory = memory
        bound = BoundOrdinaryService(app, repo, service.store, service.history_service, service.memory_loader,
            configuration=service.configuration, pipeline_factory=service.pipeline_factory, generation_factory=service.generation_factory)
        accepted = await repo.accept(REQ, 'owner')
        running = asyncio.create_task(bound.run_turn(accepted))
        while not bound._active: await asyncio.sleep(0)
        assert await bound.run_turn(accepted) is None
        assert await bound.run_turn(replace(accepted, is_new=False, replay_only=True)) is None
        running.cancel()
        await running
        roots = [span for span in sdk.spans if span.fields['name'] == 'weknora.ordinary-turn']
        assert len(roots) == 1 and roots[0].updates[-1]['metadata']['status'] == 'cancelled'
        assert all(span.ends == 1 for span in sdk.spans)
        assert not bound._bound_active and not app._runs
    asyncio.run(run())


def test_current_scope_permission_failure_is_tracing_only():
    async def run():
        obs, sdk = observer()
        @asynccontextmanager
        async def denied(): raise RuntimeError('PRIVATE permission unavailable'); yield
        app = SimpleNamespace(observability=obs, leases=SimpleNamespace(lease=denied))
        assert await ApplicationRuntime._start_turn_trace(app, turn()) is None
        assert not sdk.spans
    asyncio.run(run())


@pytest.mark.parametrize('consents', [[{'id': 'kb', 'cloud_allowed': True}],
    [{'id': 'kb', 'cloud_allowed': False}], [{'id': 'foreign', 'cloud_allowed': True}]])
def test_root_trace_checks_exact_current_accepted_kb_consent(consents):
    async def run():
        obs, sdk = observer()
        async def execute(sql, args):
            assert args == (1, ['kb']) and 'deleted_at IS NULL' in sql
            async def fetchall(): return consents
            return SimpleNamespace(fetchall=fetchall)
        @asynccontextmanager
        async def lease(): yield SimpleNamespace(connection=SimpleNamespace(execute=execute))
        checked = []
        async def session(*args): checked.append('session')
        async def resources(*args): checked.append('resources')
        app = SimpleNamespace(observability=obs, leases=SimpleNamespace(lease=lease),
            principal=SimpleNamespace(tenant_id=1), turns=SimpleNamespace(_session=session, _resources=resources))
        root = await ApplicationRuntime._start_turn_trace(app, turn())
        assert bool(root) == (consents == [{'id': 'kb', 'cloud_allowed': True}])
        assert checked == ['session', 'resources']
        obs.finish_turn(root)
    asyncio.run(run())


def test_cancel_during_optional_final_status_read_propagates_and_closes_trace(monkeypatch):
    from backend.weknora_port.service import RuntimeService
    async def run():
        service, repo, *_ = ordinary_runtime()
        obs, sdk = observer()
        accepted = await repo.accept(REQ, 'owner')
        app = SimpleNamespace(observability=obs, _runs={}, memory_hook_errors=[],
            principal=SimpleNamespace(tenant_id=1, subject_id='owner'))
        async def start(current): return obs.begin_turn(current, cloud_consented=True)
        async def rows(*args): raise asyncio.CancelledError()
        async def no_result(self, current): return None
        app._start_turn_trace = start; app._rows = rows
        monkeypatch.setattr(RuntimeService, 'run_turn', no_result)
        bound = BoundOrdinaryService(app, repo, service.store, service.history_service, service.memory_loader,
            configuration=service.configuration, pipeline_factory=service.pipeline_factory, generation_factory=service.generation_factory)
        with pytest.raises(asyncio.CancelledError): await bound.run_turn(accepted)
        assert len(sdk.spans) == 1 and sdk.spans[0].ends == 1 and not obs._turns and not bound._bound_active
    asyncio.run(run())


@pytest.mark.parametrize('error', [ProviderError('PROVIDER_HTTP_ERROR', sent=True), asyncio.CancelledError(), TimeoutError(), RuntimeError('PRIVATE')])
def test_transport_error_hook_records_safe_type_without_changing_settlement(error):
    async def run():
        db = ClaimDB()
        @asynccontextmanager
        async def lease(): yield SimpleNamespace(connection=db)
        obs, sdk = observer()
        app = SimpleNamespace(principal=SimpleNamespace(tenant_id=1, subject_id='owner'),
            leases=SimpleNamespace(lease=lease), cloud_allowed=True, observability=obs)
        cfg = provider_config('deepseek', 'test')
        transport = AsyncHTTPTransport(allowed_endpoints=endpoint_urls(cfg), gate=LeasedSendGate(app),
            credential=lambda _: 'SYNTHETIC', http_transport=httpx.MockTransport(lambda req: httpx.Response(200, json={})))
        try:
            with pytest.raises(type(error)):
                async with transport.open(cfg, '/chat/completions', {'model': 'test', 'max_tokens': 64},
                                          call().context, capability='chat'):
                    raise error
            span = sdk.spans[-1]
            assert span.ends == 1 and span.updates[-1]['metadata']['settlement_success'] is True
            assert span.updates[-1]['metadata']['error_code'] in ('PROVIDER_HTTP_ERROR', 'CANCELLED', 'TIMEOUT', 'UNEXPECTED_ERROR')
            assert 'PRIVATE' not in json.dumps(span.updates)
        finally: await transport.aclose(); await obs.shutdown()
    asyncio.run(run())


def test_installed_sdk_manual_trace_api_with_in_memory_exporter_only():
    from langfuse import Langfuse
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    def factory(**options): return Langfuse(**options, tracer_provider=provider, span_exporter=exporter)
    obs = LangfuseObservability(ObservabilitySettings(True, True,
        public_key='synthetic-sdk-signature-test', secret_key='synthetic-only'), client_factory=factory)
    root = obs.begin_turn(turn(), cloud_consented=True)
    assert root is not None
    obs.provider_started(call(), cloud_consented=True)
    obs.provider_finished(call(), outcome='RESPONSE_RECEIVED', usage=TokenUsage(2, 1, 3, 'REPORTED'), settlement_success=True)
    obs.finish_turn(root, status='completed')
    asyncio.run(obs.shutdown())
    spans = exporter.get_finished_spans()
    assert len(spans) == 2
    assert {f'{span.context.trace_id:032x}' for span in spans} == {root.trace_id}
    child = next(span for span in spans if span.name == 'weknora.provider-chat')
    assert f'{child.parent.span_id:016x}' == root.span.id
    assert not any('PRIVATE' in str(span.attributes) for span in spans)
    assert any(digest(turn().session_id) == value for span in spans for key, value in span.attributes.items() if 'session' in key)
