"""P27 approved authority composition; local assets and infrastructure doubles only."""
import asyncio
import base64
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
import json
from types import SimpleNamespace

import httpx
from PIL import Image
import pytest

from backend.weknora_port.authority import (
    ApprovedTokenAuthority, ApprovedTokenAuthorityBundle, AuthorityBundleSettings, ConfiguredTokenAuthority,
    ServerPermissionResolver, load_authority_settings, runtime_factory)
from backend.weknora_port.development import DevelopmentConfigurationError, DevelopmentSettings, assemble
from backend.weknora_port.entrypoint import create_app
from backend.weknora_port.models import provider_config
from backend.weknora_port.models.types import CallContext, PreparedCall, ProviderError, TrustedScope
from backend.weknora_port.source_facts import (
    DEEPSEEK_INLINE_IMAGE_RECIPE, bind_wire_facts, build_call_sources, sha)
from backend.weknora_port.storage.send_gate import P1SendGate


def write_json(path, obj):
    path.write_text(json.dumps(obj), encoding='utf-8')


@pytest.fixture
def pair(tmp_path):
    paths, configs = [], []
    now = datetime.now(timezone.utc)
    for provider, model, limit, hours, recipe in (
        ('deepseek', 'deepseek-flash', 750_000, 2, DEEPSEEK_INLINE_IMAGE_RECIPE),
        ('siliconflow', 'BAAI/bge-m3', 250_000, 1, 'provider-completion-cap/v2'),
    ):
        meter = tmp_path / (provider + '.meter')
        meter.write_bytes(('offline-meter-' + provider).encode())
        cfg = dict(schema='weknora-token-authority/v1', mode='approved', tenant_id=42,
            subject_id='offline-p27-owner', account_id='offline-account-' + provider, provider=provider,
            expires_at=(now + timedelta(hours=hours)).isoformat(),
            approval_source='OWNER:offline-p27-' + provider,
            month=now.date().replace(day=1).isoformat(), carry_in_tokens=12,
            carry_status='KNOWN', carry_source='LEDGER:offline-preserved-' + provider,
            limit_tokens=limit, allowed_models=[[provider, model]],
            input_limit=200_000 if provider == 'deepseek' else 100_000,
            output_limit=8192 if provider == 'deepseek' else 1024,
            meter={'recipe': recipe, 'path': str(meter), 'sha256': sha(meter.read_bytes())})
        path = tmp_path / (provider + '.json')
        write_json(path, cfg)
        paths.append(path)
        configs.append(cfg)
    path = tmp_path / 'bundle.json'
    write_json(path, {'schema': 'weknora-token-authority-bundle/v1',
                      'authorities': [str(p) for p in paths]})
    return SimpleNamespace(path=path, paths=paths, configs=configs,
                           settings=load_authority_settings(path))


def body_for(provider):
    if provider == 'siliconflow':
        return {'model': 'BAAI/bge-m3', 'input': ['Document-derived caption.']}, 'embedding'
    raw = io.BytesIO()
    Image.new('RGB', (12, 10), 'white').save(raw, format='PNG')
    url = 'data:image/png;base64,' + base64.b64encode(raw.getvalue()).decode()
    return {'model': 'deepseek-flash', 'max_tokens': 5000, 'messages': [{'role': 'user',
        'content': [{'type': 'image_url', 'image_url': {'url': url}},
                    {'type': 'text', 'text': 'Describe supported image content.'}]}]}, 'vision'


def bound_call(member, *, binder=None):
    config = provider_config(member.provider, member.allowed_models[0][1])
    body, capability = body_for(member.provider)
    wire = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    context = CallContext('p27/version/image/0/' + capability,
        TrustedScope(member.subject_id, ('kb-1',), ('doc-1',)), required_origin_kbs=('kb-origin',))
    if binder is None:
        context = replace(context, source_facts=build_call_sources(context, body, wire, capability=capability))
        facts = bind_wire_facts(config, context, body, wire, capability,
            config_sha=member.config_sha, recipe=member.meter.recipe)
    else:
        facts = binder(config, context, body, wire, capability)
    endpoint = config.base_url.rstrip('/') + ('/embeddings' if capability == 'embedding' else '/chat/completions')
    return PreparedCall(context, member.provider, config.model, config.model_id, capability,
                        endpoint, sha(wire), len(wire), body.get('max_tokens'), facts)


def test_bundle_load_is_read_only_and_pins_root_and_independent_members(pair):
    before = {p: p.read_bytes() for p in (pair.path, *pair.paths)}
    cfg = load_authority_settings(pair.path)
    assert isinstance(cfg, AuthorityBundleSettings) and cfg.mode == 'approved'
    assert cfg.config_sha == sha(before[pair.path])
    assert [s.config_sha for s in cfg.authorities] == [sha(before[p]) for p in pair.paths]
    assert cfg.allowed_models == (('deepseek', 'deepseek-flash'), ('siliconflow', 'BAAI/bge-m3'))
    assert cfg.expires_at == cfg.for_provider('siliconflow').expires_at
    assert (cfg.tenant_id, cfg.subject_id, cfg.month) == (
        cfg.authorities[0].tenant_id, cfg.authorities[0].subject_id, cfg.authorities[0].month)
    assert all(p.read_bytes() == data for p, data in before.items())


@pytest.mark.parametrize('change', [
    {'authorities': []}, {'authorities': ['E:/only-one.json']},
    {'authorities': 'E:/member.json'}, {'authorities': [None, 'E:/member.json']},
    {'extra': True},
])
def test_bundle_rejects_invalid_schema(pair, change):
    obj = json.loads(pair.path.read_text())
    obj.update(change)
    write_json(pair.path, obj)
    with pytest.raises(ProviderError, match='AUTHORITY_BUNDLE_CONFIG_INVALID'):
        load_authority_settings(pair.path)


@pytest.mark.parametrize('member_path', ['relative.json', 'C:/member.json'])
def test_bundle_rejects_unapproved_member_paths(pair, member_path):
    write_json(pair.path, {'schema': 'weknora-token-authority-bundle/v1',
        'authorities': [str(pair.paths[0]), member_path]})
    with pytest.raises(ProviderError, match='AUTHORITY_ABSOLUTE_DE_PATH_REQUIRED'):
        load_authority_settings(pair.path)


def test_bundle_rejects_nested_members_and_duplicate_providers(pair):
    obj = {'schema': 'weknora-token-authority-bundle/v1',
           'authorities': [str(pair.paths[0]), str(pair.path)]}
    write_json(pair.path, obj)
    with pytest.raises(ProviderError, match='AUTHORITY_BUNDLE_CONFIG_INVALID'):
        load_authority_settings(pair.path)
    obj['authorities'][1] = str(pair.paths[0])
    write_json(pair.path, obj)
    with pytest.raises(ProviderError, match='AUTHORITY_BUNDLE_DUPLICATE_PROVIDER'):
        load_authority_settings(pair.path)


@pytest.mark.parametrize('field,value', [
    ('tenant_id', 43), ('subject_id', 'other-owner'), ('month', '2020-01-01'),
])
def test_bundle_requires_identical_tenant_subject_and_month(pair, field, value):
    cfg = {**pair.configs[1], field: value}
    write_json(pair.paths[1], cfg)
    with pytest.raises(ProviderError, match='AUTHORITY_BUNDLE_IDENTITY_MISMATCH'):
        load_authority_settings(pair.path)


@pytest.mark.parametrize('mode', ['disabled', 'synthetic'])
def test_bundle_cannot_mix_approved_with_disabled_or_synthetic(pair, mode):
    cfg = dict(schema='weknora-token-authority/v1', mode=mode)
    if mode == 'synthetic':
        cfg = {k: v for k, v in pair.configs[1].items() if k not in ('provider', 'meter')}
        cfg.update(mode='synthetic', subject_id='synthetic-owner', account_id='synthetic-account',
                   approval_source='SYNTHETIC:closed', carry_source='SYNTHETIC:closed', limit_tokens=1_000_000)
    write_json(pair.paths[1], cfg)
    with pytest.raises(ProviderError, match='AUTHORITY_BUNDLE_APPROVED_MEMBERS_REQUIRED'):
        load_authority_settings(pair.path)


def test_bundle_still_verifies_member_meter_hash(pair):
    cfg = pair.configs[1]
    cfg['meter']['sha256'] = '0' * 64
    write_json(pair.paths[1], cfg)
    with pytest.raises(ProviderError, match='APPROVED_METER_ASSET_UNVERIFIED'):
        load_authority_settings(pair.path)


def test_bundle_dispatch_keeps_real_provider_accounts_fences_and_carry_in(pair):
    tokens = ApprovedTokenAuthorityBundle(pair.settings)
    for member in pair.settings.authorities:
        call = bound_call(member)
        assert tokens.validate(call) is call.bound_facts
        bound = tokens.reserve_bound(call)
        month = tokens.monthly(call)
        assert (month.account_id, month.provider, month.limit_tokens) == (
            member.account_id, member.provider, member.limit_tokens)
        assert (month.carry_in_tokens, month.carry_status, month.carry_source, month.approval_source) == (
            member.carry_in_tokens, member.carry_status, member.carry_source, member.approval_source)
        assert bound.output_upper == (5000 if member.provider == 'deepseek' else 0)


@pytest.mark.parametrize('bad', ['provider', 'model', 'owner', 'member_sha', 'root_sha', 'simulated'])
def test_bundle_cannot_rebind_provider_model_owner_or_member_facts(pair, bad):
    tokens = ApprovedTokenAuthorityBundle(pair.settings)
    call = bound_call(pair.settings.authorities[0])
    if bad == 'provider': call = replace(call, provider='unapproved-provider')
    elif bad == 'model': call = replace(call, model='BAAI/bge-m3')
    elif bad == 'owner': call = replace(call, context=replace(call.context, scope=replace(call.context.scope, owner_id='other')))
    else:
        values = {'simulated': True} if bad == 'simulated' else {
            'config_sha': pair.settings.config_sha if bad == 'root_sha' else pair.settings.authorities[1].config_sha}
        call = replace(call, bound_facts=replace(call.bound_facts, **values))
    for method in (tokens.validate, tokens.reserve_bound, tokens.monthly):
        with pytest.raises(ProviderError, match='AUTHORITY_PROVIDER_NOT_ALLOWED|MODEL_BOUND_FACTS_REQUIRED'):
            method(call)


class OfflineLeases:
    def __init__(self): self.queries = []; self.cloud_allowed = True

    @asynccontextmanager
    async def lease(self):
        yield SimpleNamespace(connection=self)

    async def execute(self, sql, params=()):
        self.queries.append((sql, params))
        if 'to_regclass' in sql:
            row = {'principal': True, 'image': True, 'tokens': True, 'slots': True}
            async def one(): return row
            return SimpleNamespace(fetchone=one)
        assert sql.startswith('SELECT id,cloud_allowed FROM knowledge_bases')
        async def all_rows():
            return [{'id': k, 'cloud_allowed': self.cloud_allowed} for k in params[1]]
        return SimpleNamespace(fetchall=all_rows)


def test_permission_resolver_selects_member_limits_expiry_and_retains_origin_egress(pair):
    async def scenario():
        leases = OfflineLeases()
        permissions = ServerPermissionResolver(leases, pair.settings)
        gate = P1SendGate(SimpleNamespace(connection=leases), tenant_id=42,
                          permissions=permissions, token_authority=permissions.tokens)
        for member in pair.settings.authorities:
            call = bound_call(member)
            grant = await permissions.resolve(call)
            assert (grant.max_input_tokens, grant.max_output_tokens, grant.expires_at, grant.approval_source) == (
                member.input_limit, member.output_limit, member.expires_at, member.approval_source)
            assert gate._permission(call, grant) == ('kb-1', 'kb-origin')
            assert leases.queries[-1][1][0] == 42
            assert set(leases.queries[-1][1][1]) == {'kb-1', 'kb-origin'}
            leases.cloud_allowed = False
            with pytest.raises(ProviderError, match='MODEL_ORIGIN_KB_EGRESS_DENIED'):
                gate._permission(call, await permissions.resolve(call))
            leases.cloud_allowed = True
    asyncio.run(scenario())


def offline_assembly_settings(tmp_path, monkeypatch):
    from backend.weknora_port.storage import connections
    from redis.asyncio import Redis
    leases = OfflineLeases()
    monkeypatch.setattr(connections, 'IsolatedConnectionFactory', lambda *args: leases)
    class OfflineRedis:
        async def info(self, section): return {'run_id': 'a' * 40}
        async def aclose(self): pass
    monkeypatch.setattr(Redis, 'from_url', lambda *args, **kwargs: OfflineRedis())
    key = tmp_path / 'offline-secret.key'
    key.write_bytes(b'k' * 32)
    return DevelopmentSettings('postgresql://offline:offline@127.0.0.1:1/weknora_offline',
        '123456789', 'weknora_offline', 'redis://127.0.0.1:1/15', 'a' * 40,
        'weknora-python-offline', tmp_path / 'storage', key)


@pytest.mark.parametrize('mode', ['bundle', 'single'])
def test_production_assembly_and_shared_factory_bind_each_provider_to_its_member(pair, tmp_path, monkeypatch, mode):
    settings = offline_assembly_settings(tmp_path, monkeypatch)
    async def scenario():
        # Standard API/worker factory loads the same root; infrastructure is doubled.
        path = pair.path if mode == 'bundle' else pair.paths[0]
        cfg = load_authority_settings(path)
        members = cfg.authorities if mode == 'bundle' else (cfg,)
        async with runtime_factory(path)(settings) as runtime:
            assert isinstance(runtime.token_authority, ApprovedTokenAuthorityBundle if mode == 'bundle' else ApprovedTokenAuthority)
            assert runtime.permissions.tokens is runtime.token_authority
            assert runtime.authority_config_sha == cfg.config_sha
            for member in members:
                config = provider_config(member.provider, member.allowed_models[0][1])
                model = runtime.model_factory(config, 'offline-delivered-credential', None)
                try:
                    call = bound_call(member, binder=model.transport.bind_facts)
                    assert call.bound_facts.config_sha == member.config_sha
                    assert call.bound_facts.bound.source == member.meter.recipe
                    runtime.token_authority.reserve_bound(call)
                    assert model.transport.facts_mock_only is False
                    assert model.transport.credential(None) == 'offline-delivered-credential'
                finally:
                    await model.transport.aclose()
            with pytest.raises(DevelopmentConfigurationError, match='AUTHORITY_MODEL_NOT_ALLOWED'):
                runtime.model_factory(provider_config('deepseek', 'deepseek-v4-pro'), '', None)
    asyncio.run(scenario())


def test_synthetic_single_config_keeps_closed_transport_and_shared_bucket(pair, tmp_path, monkeypatch):
    cfg = {k: v for k, v in pair.configs[0].items() if k not in ('provider', 'meter')}
    cfg.update(mode='synthetic', subject_id='synthetic-owner', account_id='synthetic-account',
        approval_source='SYNTHETIC:closed', carry_source='SYNTHETIC:closed', limit_tokens=1_000_000,
        allowed_models=[['deepseek', 'deepseek-flash'], ['siliconflow', 'BAAI/bge-m3']])
    path = tmp_path / 'synthetic.json'
    write_json(path, cfg)
    authority_settings = load_authority_settings(path)
    settings = offline_assembly_settings(tmp_path, monkeypatch)
    async def scenario():
        async with assemble(settings, authority_settings=authority_settings) as runtime:
            assert isinstance(runtime.token_authority, ConfiguredTokenAuthority)
            for provider, model_name in authority_settings.allowed_models:
                config = provider_config(provider, model_name)
                with pytest.raises(DevelopmentConfigurationError, match='SYNTHETIC_ENDPOINT_REQUIRED'):
                    runtime.model_factory(config, 'ignored', None)
                config = replace(config, base_url='https://fixture.invalid/v1')
                model = runtime.model_factory(config, 'ignored', None)
                try:
                    body = {'max_tokens': 64}
                    wire = b'{"max_tokens":64}'
                    context = CallContext('synthetic/op', TrustedScope(authority_settings.subject_id, ('kb-1',)))
                    facts = model.transport.bind_facts(config, context, body, wire, 'chat')
                    call = PreparedCall(context, provider, model_name, 'mid', 'chat',
                        'https://fixture.invalid/v1/chat/completions', sha(wire), len(wire), 64, facts)
                    assert facts.simulated and model.transport._mock_transport
                    assert not model.transport.live_ready
                    runtime.token_authority.reserve_bound(call)
                    assert runtime.token_authority.monthly(call).provider == 'deepseek'
                finally:
                    await model.transport.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['none', 'expired_member', 'wrong_month', 'wrong_subject',
                                     'wrong_tenant', 'unrelated_permissions', 'placeholder_token'])
def test_readyz_requires_valid_aggregate_and_matching_runtime_authorities(pair, tmp_path, monkeypatch, failure):
    settings = offline_assembly_settings(tmp_path, monkeypatch)
    async def scenario():
        async with assemble(settings, authority_settings=pair.settings) as runtime:
            cfg = pair.settings
            if failure in ('expired_member', 'wrong_month'):
                members = tuple(replace(s, month=s.month.replace(year=2020)) for s in cfg.authorities) if failure == 'wrong_month' else (
                    cfg.authorities[0], replace(cfg.authorities[1], expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
                runtime.token_authority.settings = replace(cfg, authorities=members)
                runtime.permissions.settings = runtime.token_authority.settings
            elif failure == 'wrong_subject': runtime.principal = replace(runtime.principal, subject_id='other-owner')
            elif failure == 'wrong_tenant': runtime.principal = replace(runtime.principal, tenant_id=43)
            elif failure == 'unrelated_permissions': runtime.permissions = ServerPermissionResolver(OfflineLeases(), cfg)
            elif failure == 'placeholder_token': runtime.token_authority = SimpleNamespace(settings=cfg)
            @asynccontextmanager
            async def factory(_): yield runtime
            app = create_app(settings=settings, runtime_factory=factory)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://offline') as client:
                    response = await client.get('/readyz')
            assert response.status_code == (200 if failure == 'none' else 503)
            assert response.json()['authority_config_sha'] == cfg.config_sha
    asyncio.run(scenario())
