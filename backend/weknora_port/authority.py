"""Shared trusted assembly. Synthetic mode is inseparable from closed HTTP.

No prices, credentials, default grants, dynamic module loaders or live recipe
claims. Approved hosted meters remain explicitly unsupported and fail closed.
"""
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone, date
from pathlib import Path
from urllib.parse import urlsplit
import hashlib
import json

from .models.types import ProviderError
from .source_facts import METER_RECIPES, BoundCallFacts
from .storage.send_gate import SendAuthorization, ContentOrigin


@dataclass(frozen=True)
class MeterAsset:
    """Verified, hash-pinned metering asset that backs a named bound recipe."""
    recipe: str
    path: str
    sha256: str


@dataclass(frozen=True)
class AuthoritySettings:
    mode: str
    config_sha: str
    tenant_id: int
    subject_id: str
    account_id: str
    expires_at: datetime
    approval_source: str
    month: date
    carry_in_tokens: int
    carry_status: str
    carry_source: str
    allowed_models: tuple[tuple[str, str], ...]
    input_limit: int
    output_limit: int
    limit_tokens: int = 1_000_000
    provider: str = 'deepseek'
    meter: MeterAsset | None = None


def load_authority_settings(path):
    if path is None:
        return None
    def unique(pairs):
        obj = {}
        for k,v in pairs:
            if k in obj: raise ProviderError('AUTHORITY_DUPLICATE_CONFIG_KEY')
            obj[k] = v
        return obj
    target = Path(path)
    if not target.is_absolute() or target.drive.upper() not in ('D:', 'E:'):
        raise ProviderError('AUTHORITY_ABSOLUTE_DE_PATH_REQUIRED')
    raw = target.read_bytes()
    obj = json.loads(raw, object_pairs_hook=unique)
    if obj=={'schema':'weknora-token-authority/v1','mode':'disabled'}:
        return None
    base = {'schema','mode','tenant_id','subject_id','account_id','expires_at','approval_source','month',
        'carry_in_tokens','carry_status','carry_source','allowed_models','input_limit','output_limit','limit_tokens'}
    if not isinstance(obj, dict) or obj.get('schema') != 'weknora-token-authority/v1':
        raise ProviderError('AUTHORITY_CONFIG_INVALID')
    if obj.get('mode') == 'approved':
        return _load_approved(obj, raw, base | {'provider','meter'})
    if obj.get('mode') != 'synthetic':raise ProviderError('AUTHORITY_MODE_INVALID')
    names = base
    if set(obj) != names:
        raise ProviderError('AUTHORITY_CONFIG_INVALID')
    for k in ('tenant_id','input_limit','output_limit','limit_tokens','carry_in_tokens'):
        if type(obj[k]) is not int or obj[k] < (0 if k=='carry_in_tokens' else 1):
            raise ProviderError('AUTHORITY_CONFIG_INVALID')
    if (obj['limit_tokens'] != 1_000_000 or obj['carry_status'] not in ('KNOWN','UNKNOWN')
            or obj['carry_status']=='UNKNOWN' and obj['carry_in_tokens']==0
            or not obj['account_id'].startswith('synthetic-')
            or not obj['subject_id'].startswith('synthetic-')
            or not obj['approval_source'].startswith('SYNTHETIC:')
            or not obj['carry_source'].startswith('SYNTHETIC:')):
        raise ProviderError('AUTHORITY_SYNTHETIC_IDENTITY_REQUIRED')
    models = obj['allowed_models']
    if not isinstance(models, list) or not models or any(not isinstance(m,list) or len(m)!=2 or any(not isinstance(s,str) or not s for s in m) for m in models):
        raise ProviderError('AUTHORITY_MODELS_REQUIRED')
    expires = datetime.fromisoformat(obj['expires_at'])
    if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
        raise ProviderError('AUTHORITY_EXPIRED')
    month = date.fromisoformat(obj['month'])
    if month.day != 1:
        raise ProviderError('TOKEN_MONTH_INVALID')
    return AuthoritySettings(obj['mode'], hashlib.sha256(raw).hexdigest(), obj['tenant_id'], obj['subject_id'],
        obj['account_id'], expires, obj['approval_source'], month, obj['carry_in_tokens'], obj['carry_status'],
        obj['carry_source'], tuple(tuple(m) for m in models), obj['input_limit'], obj['output_limit'])


def _load_approved(obj, raw, names):
    """Approved hosted-meter config. Fail-closed.

    Requires a real (non-synthetic) identity, a verified hash-pinned meter asset
    with a known recipe, a ledger-referenced carry-in, and a known provider limit.
    Missing/unknown inputs refuse here, so approved mode is not "config only".
    """
    if set(obj) != names:
        raise ProviderError('AUTHORITY_CONFIG_INVALID')
    provider = obj['provider']
    if not isinstance(provider, str) or not provider:
        raise ProviderError('AUTHORITY_CONFIG_INVALID')
    if provider != 'deepseek' or obj['limit_tokens'] != 1_000_000:
        raise ProviderError('APPROVED_PROVIDER_LIMIT_UNKNOWN')
    for k in ('tenant_id', 'input_limit', 'output_limit', 'limit_tokens', 'carry_in_tokens'):
        if type(obj[k]) is not int or obj[k] < (0 if k == 'carry_in_tokens' else 1):
            raise ProviderError('AUTHORITY_CONFIG_INVALID')
    subject, account = obj['subject_id'], obj['account_id']
    if (not isinstance(subject, str) or not isinstance(account, str) or not subject or not account
            or subject.startswith('synthetic-') or account.startswith('synthetic-')):
        raise ProviderError('APPROVED_SYNTHETIC_IDENTITY_REJECTED')
    approval = obj['approval_source']
    if not isinstance(approval, str) or not approval or approval.startswith('SYNTHETIC:'):
        raise ProviderError('APPROVED_APPROVAL_SOURCE_REJECTED')
    if obj['carry_status'] not in ('KNOWN', 'UNKNOWN'):
        raise ProviderError('AUTHORITY_CONFIG_INVALID')
    carry = obj['carry_source']
    if not isinstance(carry, str) or not carry.startswith('LEDGER:'):
        raise ProviderError('APPROVED_CARRY_SOURCE_UNTRUSTED')
    if obj['carry_status'] == 'UNKNOWN' and obj['carry_in_tokens'] < 1:
        raise ProviderError('APPROVED_CARRY_BOUND_REQUIRED')
    meter = obj['meter']
    if not isinstance(meter, dict) or set(meter) != {'recipe', 'path', 'sha256'}:
        raise ProviderError('AUTHORITY_CONFIG_INVALID')
    if meter['recipe'] not in METER_RECIPES:
        raise ProviderError('APPROVED_METER_RECIPE_UNSUPPORTED')
    asset = Path(meter['path'])
    if not asset.is_absolute() or asset.drive.upper() not in ('D:', 'E:') or not asset.is_file():
        raise ProviderError('APPROVED_METER_ASSET_UNVERIFIED')
    digest = meter['sha256']
    if not isinstance(digest, str) or hashlib.sha256(asset.read_bytes()).hexdigest() != digest:
        raise ProviderError('APPROVED_METER_ASSET_UNVERIFIED')
    models = obj['allowed_models']
    if not isinstance(models, list) or not models or any(not isinstance(m, list) or len(m) != 2
            or any(not isinstance(s, str) or not s for s in m) for m in models):
        raise ProviderError('AUTHORITY_MODELS_REQUIRED')
    expires = datetime.fromisoformat(obj['expires_at'])
    if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
        raise ProviderError('AUTHORITY_EXPIRED')
    month = date.fromisoformat(obj['month'])
    if month.day != 1:
        raise ProviderError('TOKEN_MONTH_INVALID')
    return AuthoritySettings('approved', hashlib.sha256(raw).hexdigest(), obj['tenant_id'], subject, account,
        expires, approval, month, obj['carry_in_tokens'], obj['carry_status'], carry,
        tuple(tuple(m) for m in models), obj['input_limit'], obj['output_limit'], obj['limit_tokens'],
        provider, MeterAsset(meter['recipe'], str(asset), digest))


@dataclass(frozen=True)
class TokenMonthlyAuthority:
    account_id: str
    provider: str
    month: date
    limit_tokens: int
    carry_in_tokens: int
    carry_status: str
    carry_source: str
    approval_source: str


class ConfiguredTokenAuthority:
    def __init__(self, settings):
        if (not isinstance(settings,AuthoritySettings) or settings.mode!='synthetic'
                or settings.limit_tokens!=1_000_000 or type(settings.carry_in_tokens) is not int
                or settings.carry_in_tokens<0 or settings.carry_status not in ('KNOWN','UNKNOWN')
                or settings.carry_status=='UNKNOWN' and settings.carry_in_tokens==0
                or not settings.carry_source or not settings.account_id.startswith('synthetic-')):
            raise ProviderError('TOKEN_MONTH_AUTHORITY_REQUIRED')
        self.settings = settings

    def validate(self, call):
        cfg = self.settings
        if cfg.expires_at <= datetime.now(timezone.utc): raise ProviderError('AUTHORITY_EXPIRED')
        facts = call.bound_facts
        if (not isinstance(facts, BoundCallFacts) or facts.config_sha != cfg.config_sha
                or not facts.simulated or facts.sources.wire_sha256 != call.wire_sha256
                or call.context.scope.owner_id != cfg.subject_id
                or (call.provider, call.model) not in cfg.allowed_models
                or urlsplit(call.endpoint).hostname != 'fixture.invalid'):
            raise ProviderError('MODEL_BOUND_FACTS_REQUIRED')
        return facts

    def reserve_bound(self, call):
        bound = self.validate(call).bound
        if (type(bound.input_upper) is not int or not 0 < bound.input_upper <= self.settings.input_limit
                or type(bound.output_upper) is not int or not 0 <= bound.output_upper <= self.settings.output_limit
                or bound.output_upper != (call.output_budget or 0)
                or bound.source != 'SYNTHETIC_ONLY/closed-mock-bounded-response/v1'):
            raise ProviderError('TOKEN_BOUND_UNSUPPORTED')
        return bound

    def monthly(self, call):
        self.validate(call)
        s = self.settings
        # Synthetic fixture has ONE shared canonical bucket even when role
        # providers differ. Changing a role/alias cannot mint another quota.
        # Approved account/provider mapping is deliberately not inferred here.
        return TokenMonthlyAuthority(s.account_id, 'deepseek', s.month, s.limit_tokens,
            s.carry_in_tokens, s.carry_status, s.carry_source, s.approval_source + ':' + s.config_sha)


class ApprovedTokenAuthority:
    """Approved hosted Token authority: real account/provider month, a verified
    meter-backed bound, and real (non-simulated) per-source facts. Fail-closed.
    The account/provider mapping is explicit here and never inferred from roles.
    """
    def __init__(self, settings):
        if (not isinstance(settings, AuthoritySettings) or settings.mode != 'approved'
                or settings.provider != 'deepseek' or settings.limit_tokens != 1_000_000
                or type(settings.carry_in_tokens) is not int or settings.carry_in_tokens < 0
                or settings.carry_status not in ('KNOWN', 'UNKNOWN')
                or settings.carry_status == 'UNKNOWN' and settings.carry_in_tokens == 0
                or not isinstance(settings.meter, MeterAsset) or settings.meter.recipe not in METER_RECIPES
                or not settings.account_id or settings.account_id.startswith('synthetic-')
                or not isinstance(settings.carry_source, str) or not settings.carry_source.startswith('LEDGER:')):
            raise ProviderError('TOKEN_MONTH_AUTHORITY_REQUIRED')
        self.settings = settings

    def validate(self, call):
        cfg = self.settings
        if cfg.expires_at <= datetime.now(timezone.utc):
            raise ProviderError('AUTHORITY_EXPIRED')
        facts = call.bound_facts
        if (not isinstance(facts, BoundCallFacts) or facts.config_sha != cfg.config_sha
                or facts.simulated or facts.sources.wire_sha256 != call.wire_sha256
                or call.context.scope.owner_id != cfg.subject_id
                or (call.provider, call.model) not in cfg.allowed_models):
            raise ProviderError('MODEL_BOUND_FACTS_REQUIRED')
        return facts

    def reserve_bound(self, call):
        bound = self.validate(call).bound
        if (type(bound.input_upper) is not int or not 0 < bound.input_upper <= self.settings.input_limit
                or type(bound.output_upper) is not int or not 0 <= bound.output_upper <= self.settings.output_limit
                or bound.output_upper != (call.output_budget or 0)
                or bound.source != self.settings.meter.recipe):
            raise ProviderError('TOKEN_BOUND_UNSUPPORTED')
        return bound

    def monthly(self, call):
        self.validate(call)
        s = self.settings
        return TokenMonthlyAuthority(s.account_id, s.provider, s.month, s.limit_tokens,
            s.carry_in_tokens, s.carry_status, s.carry_source, s.approval_source + ':' + s.config_sha)


class ServerPermissionResolver:
    def __init__(self, leases, settings, tokens=None):
        self.leases, self.settings = leases, settings
        self.tokens = tokens or (ConfiguredTokenAuthority(settings) if settings.mode == 'synthetic'
                                 else ApprovedTokenAuthority(settings))

    async def resolve(self, call):
        facts = self.tokens.validate(call)
        cfg = self.settings
        kbs = set(call.context.scope.knowledge_base_ids) | set(call.context.required_origin_kbs)
        origins = []
        for src in facts.sources.slices:
            kbs.update(src.origin_kbs)
            origins.append(ContentOrigin(src.kind, src.sent_value_sha256, src.origin_kbs, True))
        async with self.leases.lease() as guard:
            rows = await (await guard.connection.execute('SELECT id,cloud_allowed FROM knowledge_bases WHERE tenant_id=%s AND id=ANY(%s) AND deleted_at IS NULL', (cfg.tenant_id,list(kbs)))).fetchall()
        readable = frozenset(r['id'] for r in rows)
        allowed = frozenset(r['id'] for r in rows if r['cloud_allowed'])
        return SendAuthorization(call.context.request_id, cfg.subject_id, call.provider, call.model,
            call.model_id, call.endpoint, call.capability, call.wire_sha256, tuple(origins), readable, allowed,
            cfg.approval_source, True, True, True, cfg.input_limit, cfg.output_limit, cfg.expires_at)


def runtime_factory(path=None):
    cfg = load_authority_settings(path)
    @asynccontextmanager
    async def factory(settings):
        from .development import assemble
        async with assemble(settings, authority_settings=cfg) as app:
            yield app
    return factory
