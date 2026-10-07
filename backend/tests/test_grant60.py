"""SIMULATED grant60 only: no real ledger, provider, credentials or DB."""
import dataclasses
import hashlib
import importlib
import importlib.util
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import MappingProxyType, SimpleNamespace

import pytest

from backend.app.application.session_attempts import SessionAttemptGate
from backend.app.application.validation_usd_budget import ValidationAttemptGate
from backend.app.ports.session_attempts import AttemptDenied, request_identity
from backend.tests.test_ledger_history_compatibility import history, save


def test_grant60_module_is_available():
    assert importlib.util.find_spec("backend.app.application.reviewed_grant60") is not None


@pytest.fixture
def prepare(tmp_path, monkeypatch):
    grant = importlib.import_module("backend.app.application.reviewed_grant60")
    from backend.app.application import reviewed_immutable_batch as batch
    from backend.app.application import reviewed_product_request as product

    def make(*, legacy_tokens=65569, other_hold=0, output_cap=512, activation_allowed=True,
             hours=24, count=68):
        path, data = history(tmp_path)
        for row in data["attempts"]:
            row["validation_tokens"].update(input=0, output=0)
        data["attempts"][0]["validation_tokens"].update(
            input=legacy_tokens, reserved_input=max(2048, legacy_tokens),
        )
        save(path, data)
        raw = path.read_bytes()
        prompt = "x" * 87
        question = "SIMULATED original question"
        issued = datetime(2026, 10, 4, 10, 20, tzinfo=timezone.utc)
        clock = {"now": issued + timedelta(minutes=1)}
        specs = []
        for i in range(count):
            specs.append(product.ReviewedProductRequestSpec(
                f"SIMULATED-scope-{i:03}", str(uuid.uuid5(uuid.NAMESPACE_URL, f"SIMULATED-run-{i}")),
                ("SIMULATED-kb",), ("SIMULATED-doc",),
                hashlib.sha256(question.encode()).hexdigest(), hashlib.sha256(prompt.encode()).hexdigest(),
                343, output_cap, grant_epoch_id="SIMULATED-grant60",
            ))
        spec = grant.Grant60Spec(
            epoch_id="SIMULATED-grant60", owner_thread_id="SIMULATED-owner-thread",
            owner_message_ids=("SIMULATED-60-approval", "SIMULATED-tests-approval"),
            approval_sha256="a" * 64, issued_at=issued.isoformat(),
            expires_at=(issued + timedelta(hours=hours)).isoformat(),
            baseline_sha256=hashlib.sha256(raw).hexdigest(), baseline_json_sha256=grant.digest(data),
            product_bindings=tuple((s.scope_id, grant.digest(dataclasses.asdict(s))) for s in specs),
            question_set_sha256="b" * 64, material_set_sha256="c" * 64,
            other_hold_tokens=other_hold, activation_allowed=activation_allowed,
        )
        monkeypatch.setattr(grant, "CANONICAL_LEDGER", path)
        monkeypatch.setattr(batch, "CANONICAL_LEDGER", path)
        monkeypatch.setattr(grant, "utc_now", lambda: clock["now"])
        monkeypatch.setattr(grant, "_GRANT60_SPECS", MappingProxyType({spec.epoch_id: spec}))
        monkeypatch.setattr(grant, "_GRANT60_DIGESTS", MappingProxyType({spec.epoch_id: grant.digest(dataclasses.asdict(spec))}))
        monkeypatch.setattr(product, "_TRUSTED_PRODUCT_REQUESTS", MappingProxyType({s.scope_id: s for s in specs}))
        monkeypatch.setattr(product, "_PRODUCT_SPEC_DIGESTS", MappingProxyType({s.scope_id: grant.digest(dataclasses.asdict(s)) for s in specs}))

        def gate(i=0):
            return product.ReviewedProductRequestGate(path, scope_id=specs[i].scope_id)

        def migrate():
            return grant.migrate_disabled_v1(path, spec.epoch_id)

        def register(i=0):
            g = gate(i)
            g.register_disabled_scope()
            return g

        def enable():
            d = json.loads(path.read_text())
            d["calls_allowed_in_this_task"] = True
            save(path, d)

        def run(i=0, status="ok"):
            g = register(i)
            enable()
            with g.execution_scope():
                attempt = g.reserve(request_id=request_identity(specs[i].run_id, "quick.answer", 1))
                g.validate_before_send(attempt)
                g.finish_with_usage(attempt, status, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
            return g, attempt

        return SimpleNamespace(path=path, legacy=data, raw=raw, specs=specs, spec=spec, grant=grant,
                               product=product, clock=clock, issued=issued, gate=gate, migrate=migrate,
                               register=register, enable=enable, run=run, prompt=prompt, question=question)
    return make


def test_default_registry_is_empty_and_ledger_cannot_supply_trust():
    grant = importlib.import_module("backend.app.application.reviewed_grant60")
    assert not grant._GRANT60_SPECS
    with pytest.raises(AttemptDenied, match="GRANT60_UNTRUSTED"):
        grant.reviewed_grant60("SIMULATED-unapproved")


def test_disabled_migration_preserves_53_history_and_old_limit_and_exact_rollback(prepare):
    e = prepare()
    receipt = e.migrate()
    d = e.gate()._read()
    assert d["schema_version"] == 2 and d["authorized_limit"] == 53
    assert d["consumed_attempts"] == len(d["attempts"]) == 53
    assert d["attempts"] == e.legacy["attempts"] and d["legacy_v1"] == e.legacy
    assert d["grant60"]["consumed_attempts"] == 0 and d["grant60"]["enabled"] is False
    assert d["calls_allowed_in_this_task"] is False
    assert e.gate()._used(d) == 118595
    assert e.grant.rollback_disabled_v1(e.path, receipt) == "V1_RESTORED"
    assert e.path.read_bytes() == e.raw


@pytest.mark.parametrize("mutation", ["enabled", "owner", "pending", "post_hash"])
def test_rollback_zero_attempts_requires_disabled_no_owner_no_reserve_exact_hash(prepare, mutation):
    e = prepare()
    receipt = e.migrate()
    d = json.loads(e.path.read_text())
    if mutation == "enabled":
        d["calls_allowed_in_this_task"] = True
    elif mutation == "owner":
        d["grant60"]["execution_owner"] = "SIMULATED-owner"
    elif mutation == "pending":
        d["reserved_attempts"] = 1
    else:
        d["SIMULATED-added-metadata"] = True
    save(e.path, d)
    before = e.path.read_bytes()
    with pytest.raises(AttemptDenied):
        e.grant.rollback_disabled_v1(e.path, receipt)
    assert e.path.read_bytes() == before


def test_rollback_after_one_unknown_attempt_only_disables_and_keeps_v2(prepare):
    e = prepare()
    receipt = e.migrate()
    g, _ = e.run(status="unknown")
    before = g._read()
    assert e.grant.rollback_disabled_v1(e.path, receipt) == "V2_DISABLED_HAS_ATTEMPTS"
    after = g._read()
    assert after["schema_version"] == 2 and after["attempts"] == before["attempts"]
    assert after["consumed_attempts"] == 54 and after["grant60"]["consumed_attempts"] == 1
    assert after["calls_allowed_in_this_task"] is False and after["grant60"]["enabled"] is False
    assert g._used(after) == 119450


@pytest.mark.parametrize("kind", ["session", "synthetic", "static", "reviewed", "unbound_product"])
def test_old_readers_reject_v2_without_downgrade_or_write(prepare, kind, monkeypatch):
    e = prepare()
    e.migrate()
    if kind == "session":
        g = SessionAttemptGate(e.path)
    elif kind == "synthetic":
        g = ValidationAttemptGate(e.path)
    elif kind in ("static", "reviewed"):
        from backend.app.application.static_pair_validation import StaticPairValidationGate
        from backend.app.application.reviewed_immutable_batch import ReviewedImmutableBatchGate
        gate_type = StaticPairValidationGate if kind == "static" else ReviewedImmutableBatchGate
        g = object.__new__(gate_type)
        ValidationAttemptGate.__init__(g, e.path, max_output_tokens=256 if kind == "static" else 512)
    else:
        s = dataclasses.replace(e.specs[0], grant_epoch_id=None)
        monkeypatch.setattr(e.product, "_TRUSTED_PRODUCT_REQUESTS", MappingProxyType({s.scope_id: s}))
        monkeypatch.setattr(e.product, "_PRODUCT_SPEC_DIGESTS", MappingProxyType({s.scope_id: e.grant.digest(dataclasses.asdict(s))}))
        g = e.product.ReviewedProductRequestGate(e.path, scope_id=s.scope_id)
    before = e.path.read_bytes()
    with pytest.raises(AttemptDenied, match="SESSION_LEDGER_INVALID"):
        g.reserve(request_id="SIMULATED-bypass")
    assert e.path.read_bytes() == before


@pytest.mark.parametrize("field,value", [
    ("issued_at", None), ("expires_at", None), ("expires_at", "2026-10-06T10:20:00+00:00"),
    ("expires_at", "2026-10-04T10:20:00"), ("max_attempts", 61), ("max_attempts", True),
    ("global_token_limit", 1000001), ("manual_hold_tokens", 0),
    ("other_hold_tokens", -1), ("other_hold_tokens", float("nan")),
    ("other_hold_tokens", float("inf")), ("other_hold_tokens", True),
])
def test_invalid_trusted_spec_fields_are_still_rejected(prepare, monkeypatch, field, value):
    e = prepare()
    bad = dataclasses.replace(e.spec, **{field: value})
    monkeypatch.setattr(e.grant, "_GRANT60_SPECS", MappingProxyType({bad.epoch_id: bad}))
    monkeypatch.setattr(e.grant, "_GRANT60_DIGESTS", MappingProxyType({bad.epoch_id: e.grant.digest(dataclasses.asdict(bad))}))
    before = e.path.read_bytes()
    with pytest.raises(AttemptDenied, match="GRANT60_SPEC_INVALID"):
        e.migrate()
    assert e.path.read_bytes() == before


@pytest.mark.parametrize("other_hold,allowed", [(None, True), (0, False)])
def test_unknown_external_usage_or_disabled_source_cannot_activate(prepare, other_hold, allowed):
    e = prepare(other_hold=other_hold, activation_allowed=allowed)
    e.migrate()
    g = e.register()
    e.enable()
    before = e.path.read_bytes()
    with pytest.raises(AttemptDenied, match="GRANT60_EXTERNAL_USAGE_UNKNOWN|GRANT60_DISABLED"):
        with g.execution_scope():
            pytest.fail("SIMULATED forbidden activation")
    assert e.path.read_bytes() == before


def test_sixty_is_cumulative_and_zero_usage_does_not_return_attempts(prepare):
    e = prepare()
    e.migrate()
    for i in range(60):
        g, _ = e.run(i)
        assert g._read()['grant60']['consumed_attempts'] == i + 1
    d = g._read()
    assert d["consumed_attempts"] == len(d["attempts"]) == 113
    assert d["authorized_limit"] == 53 and d["grant60"]["consumed_attempts"] == 60
    assert d["attempts"][:53] == e.legacy["attempts"] and g._used(d) == 118595
    next_gate = e.register(60)
    e.enable()
    before = e.path.read_bytes()
    with pytest.raises(AttemptDenied, match="GRANT60_CALL_LIMIT"):
        with next_gate.execution_scope():
            pytest.fail("SIMULATED 61st activation")
    assert e.path.read_bytes() == before


def test_duplicate_and_concurrent_reservations_cannot_refresh_epoch(prepare):
    e = prepare()
    e.migrate()
    g = e.register()
    e.enable()
    rid = request_identity(e.specs[0].run_id, "quick.answer", 1)
    with g.execution_scope():
        def reserve(_):
            try:
                return g.reserve(request_id=rid)
            except AttemptDenied:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reserve, range(16)))
        assert sum(r is not None for r in results) == 1
        attempt = next(r for r in results if r is not None)
        g.finish(attempt, "unknown")
    assert g._read()["grant60"]["consumed_attempts"] == 1
    assert g._used(g._read()) == 119450
    e.enable()
    before = e.path.read_bytes()
    with pytest.raises(AttemptDenied):
        with e.gate().execution_scope():
            pytest.fail("SIMULATED replay after restart")
    assert e.path.read_bytes() == before


@pytest.mark.parametrize("extra", [0, 1])
def test_global_million_includes_manual_hold_and_unknown_reservation(prepare, extra):
    e = prepare(legacy_tokens=946119 + extra)
    e.migrate()
    g = e.register()
    e.enable()
    before = e.path.read_bytes()
    if extra:
        with pytest.raises(AttemptDenied, match="VALIDATION_TOKEN_CAP"):
            with g.execution_scope():
                pytest.fail("SIMULATED over million")
        assert e.path.read_bytes() == before
    else:
        with g.execution_scope():
            a = g.reserve(request_id=request_identity(e.specs[0].run_id, "quick.answer", 1))
            g.finish(a, "unknown")
        assert g._used(g._read()) == 1000000


def test_expiry_between_reserve_and_send_keeps_attempt_and_full_bound(prepare):
    e = prepare()
    e.migrate()
    g = e.register()
    e.enable()
    with g.execution_scope():
        a = g.reserve(request_id=request_identity(e.specs[0].run_id, "quick.answer", 1))
        e.clock["now"] = e.issued + timedelta(hours=24)
        before = e.path.read_bytes()
        with pytest.raises(AttemptDenied, match="GRANT60_EXPIRED"):
            g.validate_before_send(a)
        assert e.path.read_bytes() == before
        g.finish(a, "unknown")
    d = g._read()
    assert d["grant60"]["consumed_attempts"] == 1 and g._used(d) == 119450
    assert d["grant60"]["enabled"] is False and "execution_owner" not in d["grant60"]


@pytest.mark.parametrize("checkpoint", ["request", "reserve"])
def test_expiry_is_rechecked_before_request_and_atomic_reserve(prepare, checkpoint):
    e = prepare()
    e.migrate()
    g = e.register()
    e.enable()
    with g.execution_scope():
        e.clock["now"] = e.issued + timedelta(hours=24)
        before = e.path.read_bytes()
        with pytest.raises(AttemptDenied, match="GRANT60_EXPIRED"):
            if checkpoint == "request":
                g.validate_request(e.prompt, 30, 512, model="deepseek-flash")
            else:
                g.reserve(request_id=request_identity(e.specs[0].run_id, "quick.answer", 1))
        assert e.path.read_bytes() == before


def test_late_receipt_and_verified_usage_can_be_saved_after_expiry(prepare):
    e = prepare()
    e.migrate()
    g = e.register()
    sink = e.product.ProductReceiptFileSink(e.path.parent / "SIMULATED-receipt.json", scope_id=e.specs[0].scope_id)
    rid = request_identity(e.specs[0].run_id, "quick.answer", 1)
    e.enable()
    with g.execution_scope():
        payload = json.dumps({"model": "deepseek-flash", "messages": [{"role": "user", "content": e.prompt}]}).encode()
        sink.prepare(gate=g, request_id=rid, wire_request_payload=payload)
        a = g.reserve(request_id=rid)
        g.validate_before_send(a)
        e.clock["now"] = e.issued + timedelta(hours=25)
        binding = json.loads(sink.path.read_text())["binding"]
        receipt = dict(binding, attempt_id=a, product_scope_id=e.specs[0].scope_id,
                       provider_usage={"prompt_tokens": 1, "completion_tokens": 1}, raw_answer="SIMULATED")
        assert sink.persist(gate=g, attempt_id=a, receipt=receipt)
        g.finish_with_usage(a, "ok", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
    assert g._used(g._read()) == 118597 and g._read()["grant60"]["consumed_attempts"] == 1


def test_ledger_only_grant_or_refreshed_epoch_cannot_self_authorize(prepare, monkeypatch):
    e = prepare()
    e.migrate()
    e.run()
    before = e.path.read_bytes()
    with pytest.raises(AttemptDenied):
        e.migrate()
    assert e.path.read_bytes() == before
    changed = dataclasses.replace(e.spec, epoch_id="SIMULATED-new-epoch")
    monkeypatch.setattr(e.grant, "_GRANT60_SPECS", MappingProxyType({changed.epoch_id: changed}))
    monkeypatch.setattr(e.grant, "_GRANT60_DIGESTS", MappingProxyType({changed.epoch_id: e.grant.digest(dataclasses.asdict(changed))}))
    with pytest.raises(AttemptDenied, match="GRANT60_UNTRUSTED"):
        e.gate()._read()
    assert e.path.read_bytes() == before


@pytest.mark.parametrize("field,value", [
    ("consumed_attempts", 0), ("consumed_attempts", True), ("consumed_attempts", 61),
    ("enabled", 1), ("policy_sha256", "f" * 64),
])
def test_grant_record_tamper_fails_closed_without_rewriting(prepare, field, value):
    e = prepare()
    e.migrate()
    g, _ = e.run()
    d = json.loads(e.path.read_text())
    d["grant60"][field] = value
    save(e.path, d)
    before = e.path.read_bytes()
    with pytest.raises(AttemptDenied):
        g._read()
    assert e.path.read_bytes() == before


@pytest.mark.parametrize('status', ['unknown', 'truncated', 'unverified_ok'])
@pytest.mark.parametrize('output_cap', [512, 896])
def test_every_uncertain_result_keeps_full_bound_and_consumed_attempt(prepare, status, output_cap):
    e = prepare(output_cap=output_cap)
    e.migrate()
    g = e.register()
    e.enable()
    with g.execution_scope():
        a = g.reserve(request_id=request_identity(e.specs[0].run_id, 'quick.answer', 1))
        g.validate_before_send(a)
        if status == 'unverified_ok':
            with pytest.raises(AttemptDenied, match='VALIDATION_USAGE_UNVERIFIED'):
                g.finish_with_usage(a, 'ok', {})
        else:
            g.finish(a, status)
    d = g._read()
    assert d['grant60']['consumed_attempts'] == 1 and d['reserved_attempts'] == 0
    assert g._used(d) == 118595 + 343 + output_cap
    assert d['attempts'][-1]['validation_tokens']['state'] == 'unknown'
    assert d['grant60']['enabled'] is False and 'execution_owner' not in d['grant60']


def test_other_trusted_hold_is_counted_at_exact_million_boundary(prepare):
    e = prepare(legacy_tokens=946119, other_hold=1)
    e.migrate()
    g = e.register()
    e.enable()
    before = e.path.read_bytes()
    with pytest.raises(AttemptDenied, match='VALIDATION_TOKEN_CAP'):
        with g.execution_scope():
            pytest.fail('SIMULATED ignored other usage hold')
    assert e.path.read_bytes() == before


def test_non_owner_cannot_send_or_close_another_active_scope(prepare):
    e = prepare()
    e.migrate()
    g = e.register()
    e.enable()
    with g.execution_scope():
        a = g.reserve(request_id=request_identity(e.specs[0].run_id, 'quick.answer', 1))
        foreign = e.gate()
        before = e.path.read_bytes()
        with pytest.raises(AttemptDenied, match='GRANT60_EXECUTION_OWNER_REQUIRED'):
            foreign.validate_before_send(a)
        foreign._close_execution(foreign._read(), e.specs[0].scope_id, 'SIMULATED-wrong-owner')
        assert e.path.read_bytes() == before
        g.validate_before_send(a)
        g.finish(a, 'unknown')


@pytest.mark.parametrize('mutation', ['history', 'schema', 'old_limit', 'row_epoch'])
def test_v2_prefix_and_epoch_cannot_be_reinterpreted(prepare, mutation):
    e = prepare()
    e.migrate()
    g, _ = e.run()
    d = g._read()
    if mutation == 'history':
        d['attempts'][0]['validation_tokens']['input'] = 0
    elif mutation == 'schema':
        d['schema_version'] = True
    elif mutation == 'old_limit':
        d['authorized_limit'] = 113
    else:
        d['attempts'][-1]['grant_epoch_id'] = 'SIMULATED-other-epoch'
    save(e.path, d)
    before = e.path.read_bytes()
    with pytest.raises(AttemptDenied, match='GRANT60_LEDGER_INVALID'):
        g._read()
    assert e.path.read_bytes() == before


def test_same_epoch_new_expiry_cannot_refresh_consumed_grant(prepare, monkeypatch):
    e = prepare()
    e.migrate()
    e.run()
    refreshed = dataclasses.replace(e.spec,
        issued_at=(e.issued + timedelta(hours=24)).isoformat(),
        expires_at=(e.issued + timedelta(hours=48)).isoformat())
    monkeypatch.setattr(e.grant, '_GRANT60_SPECS', MappingProxyType({refreshed.epoch_id: refreshed}))
    monkeypatch.setattr(e.grant, '_GRANT60_DIGESTS', MappingProxyType({refreshed.epoch_id: e.grant.digest(dataclasses.asdict(refreshed))}))
    before = e.path.read_bytes()
    with pytest.raises(AttemptDenied, match='GRANT60_LEDGER_INVALID'):
        e.gate()._read()
    assert e.path.read_bytes() == before


def test_expired_pending_attempt_rollback_only_disables_then_owner_can_finish(prepare):
    e = prepare()
    receipt = e.migrate()
    g = e.register()
    e.enable()
    with g.execution_scope():
        a = g.reserve(request_id=request_identity(e.specs[0].run_id, 'quick.answer', 1))
        e.clock['now'] = e.issued + timedelta(hours=25)
        assert e.grant.rollback_disabled_v1(e.path, receipt) == 'V2_DISABLED_HAS_ATTEMPTS'
        assert g._read()['reserved_attempts'] == 1
        assert g._used(g._read()) == 119450
        g.finish(a, 'unknown')
    d = g._read()
    assert d['schema_version'] == 2 and d['consumed_attempts'] == 54
    assert d['reserved_attempts'] == 0 and d['grant60']['consumed_attempts'] == 1
    assert d['calls_allowed_in_this_task'] is False and 'execution_owner' not in d['grant60']


@pytest.fixture
def adapter(prepare, tmp_path, monkeypatch):
    """Real adapter/gate/receipt sink; only provider transport is SIMULATED."""
    from backend.app.adapters.models import deepseek
    from backend.app.application.deepseek_gate_types import DEEPSEEK_GATE_TYPES
    from backend.app.domain.scope import Scope
    from backend.app.ports.model_usage import capture_usage
    from backend.tests.test_deepseek_safety import Response

    e = prepare()
    e.migrate()
    e.g = e.register()
    e.enable()
    e.sent, e.on_build, e.on_send = [], None, None
    e.response = {
        'model': 'actual-SIMULATED-grant60',
        'choices': [{'index': 0, 'finish_reason': 'stop',
                     'message': {'role': 'assistant', 'content': ' SIMULATED answer [E1] '}}],
        'usage': {'prompt_tokens': 30, 'completion_tokens': 6, 'total_tokens': 36},
    }

    class Opener:
        def open(self, request, timeout):
            d = e.g._read()
            assert d['consumed_attempts'] == 54 and d['reserved_attempts'] == 1
            assert d['grant60']['consumed_attempts'] == 1
            assert request.full_url == deepseek.ENDPOINT and timeout == 30
            assert json.loads(request.data) == {
                'model': 'deepseek-flash', 'messages': [{'role': 'user', 'content': e.prompt}],
                'thinking': {'type': 'disabled'}, 'max_tokens': 512,
                'temperature': 0, 'stream': False,
            }
            e.sent.append(request)
            if e.on_send is not None:
                e.on_send()
            return Response(e.response)

    def build(*handlers):
        assert e.g._read()['reserved_attempts'] == 1
        if e.on_build is not None:
            e.on_build()
        return Opener()

    monkeypatch.setattr(deepseek, 'build_opener', build)

    def client(name='SIMULATED-provider-receipt.json'):
        sink = e.product.ProductReceiptFileSink(tmp_path / name, scope_id=e.specs[0].scope_id)
        return deepseek.DeepSeekGateway(api_key='SIMULATED-no-real-key', cloud_enabled=True,
            attempt_gate=e.g, receipt_sink=sink, gate_types=DEEPSEEK_GATE_TYPES)

    def invoke(model=None):
        return (model or e.model).answer_with_product_scope(e.prompt, 30, 512,
            run_id=e.specs[0].run_id, scope=Scope.from_ids(('SIMULATED-kb',), ('SIMULATED-doc',)),
            question=e.question, cloud_authorized=True,
            request_id=request_identity(e.specs[0].run_id, 'quick.answer', 1))

    e.client, e.invoke, e.model = client, invoke, client()
    with capture_usage() as usage:
        e.usage = usage
        yield e


def _assert_adapter_closed(e, *, status='unknown', used=119450, pending=0):
    d = e.g._read()
    assert d['attempts'][:53] == e.legacy['attempts']
    assert d['consumed_attempts'] == len(d['attempts']) == 54
    assert d['grant60']['consumed_attempts'] == 1 and d['reserved_attempts'] == pending
    assert d['attempts'][-1]['status'] == status and e.g._used(d) == used
    assert d['calls_allowed_in_this_task'] is False and d['grant60']['enabled'] is False
    assert 'execution_owner' not in d['grant60']
    entry = d['reviewed_immutable_tasks'][e.specs[0].scope_id]['batches'][e.specs[0].scope_id]
    assert entry['enabled'] is False and 'execution_owner' not in entry
    assert e.model.last_receipt['retry_count'] == 0
    if e.model.last_receipt['attempt_status'] == 'not_sent':
        assert e.usage.calls == []
        summary = e.usage.summary()['answer']
        assert summary['call_count'] == summary['error_count'] == 0
        assert summary['availability'] == 'not_run'
        assert summary['input_tokens'] is None and summary['output_tokens'] is None


@pytest.mark.parametrize('hours', [24, 25])
def test_adapter_expiry_during_opener_construction_prevents_send(adapter, hours):
    from backend.app.ports.providers import ProviderRequestNotSent
    e = adapter
    e.on_build = lambda: e.clock.update(now=e.issued + timedelta(hours=hours))
    with e.g.execution_scope():
        with pytest.raises(ProviderRequestNotSent, match='GRANT60_EXPIRED'):
            e.invoke()
    assert e.sent == []
    _assert_adapter_closed(e)
    receipt = json.loads(e.model.receipt_sink.path.read_text())['receipt']
    assert receipt['attempt_status'] == e.model.last_receipt['attempt_status'] == 'not_sent'
    assert receipt['provider_usage'] == {} and receipt['raw_answer'] is None
    assert receipt['attempt_id'] == e.g._read()['attempts'][-1]['attempt_id']


@pytest.mark.parametrize('denial,code', [
    ('owner', 'GRANT60_EXECUTION_OWNER_REQUIRED'),
    ('budget_block', 'VALIDATION_USAGE_BOUND_EXCEEDED'),
    ('disabled', 'GRANT60_PRESEND_DENIED'),
])
def test_adapter_presend_owner_and_budget_denials_prevent_transport(adapter, denial, code):
    from backend.app.ports.providers import ProviderRequestNotSent
    e = adapter

    def deny():
        if denial == 'owner':
            e.g._grant_owner = None
        else:
            d = e.g._read()
            if denial == 'budget_block':
                d['validation_blocked_reason'] = 'VALIDATION_USAGE_BOUND_EXCEEDED'
            else:
                d['calls_allowed_in_this_task'] = False
            save(e.path, d)

    e.on_build = deny
    with e.g.execution_scope():
        with pytest.raises(ProviderRequestNotSent, match=code):
            e.invoke()
    assert e.sent == [] and e.model.last_receipt['attempt_status'] == 'not_sent'
    _assert_adapter_closed(e)


def test_adapter_presend_denial_stays_not_sent_when_receipt_write_fails(adapter, monkeypatch):
    from pathlib import Path
    from backend.app.ports.providers import ProviderRequestNotSent
    e = adapter
    e.on_build = lambda: e.clock.update(now=e.issued + timedelta(hours=25))
    replace = e.product.os.replace

    def fail_receipt(src, dst):
        if Path(dst) == e.model.receipt_sink.path:
            raise OSError('SIMULATED receipt persistence failure')
        return replace(src, dst)

    monkeypatch.setattr(e.product.os, 'replace', fail_receipt)
    with e.g.execution_scope():
        with pytest.raises(ProviderRequestNotSent, match='GRANT60_EXPIRED'):
            e.invoke()
    assert e.sent == [] and e.model.last_receipt['attempt_status'] == 'not_sent'
    assert json.loads(e.model.receipt_sink.path.read_text())['state'] == 'prepared_not_sent'
    _assert_adapter_closed(e)


def test_adapter_presend_denial_stays_not_sent_when_settlement_is_unavailable(adapter, monkeypatch):
    from backend.app.ports.providers import ProviderRequestNotSent
    e = adapter
    e.on_build = lambda: e.clock.update(now=e.issued + timedelta(hours=25))

    def unavailable(*args):
        raise AttemptDenied('SIMULATED_SETTLEMENT_UNAVAILABLE')

    monkeypatch.setattr(e.g, 'finish_with_usage', unavailable)
    with e.g.execution_scope():
        with pytest.raises(ProviderRequestNotSent, match='GRANT60_EXPIRED'):
            e.invoke()
    assert e.sent == [] and e.model.last_receipt['attempt_status'] == 'not_sent'
    _assert_adapter_closed(e, status='reserved', pending=1)
    assert json.loads(e.model.receipt_sink.path.read_text())['receipt']['attempt_status'] == 'not_sent'


@pytest.mark.parametrize('result', ['ok', 'truncated', 'transport_error'])
def test_adapter_expiry_after_send_still_settles_and_closes_owner(adapter, result):
    from backend.app.ports.providers import ProviderUnavailable, TruncatedAnswer
    e = adapter

    def expire_after_send():
        e.clock['now'] = e.issued + timedelta(hours=25)
        if result == 'transport_error':
            raise OSError('SIMULATED transport failure')

    e.on_send = expire_after_send
    if result == 'truncated':
        e.response['choices'][0]['finish_reason'] = 'length'
    with e.g.execution_scope():
        if result == 'ok':
            assert e.invoke() == 'SIMULATED answer [E1]'
        elif result == 'truncated':
            with pytest.raises(TruncatedAnswer):
                e.invoke()
        else:
            with pytest.raises(ProviderUnavailable, match='DEEPSEEK_REQUEST_FAILED'):
                e.invoke()
    assert len(e.sent) == 1
    assert len(e.usage.calls) == 1
    summary = e.usage.summary()['answer']
    assert summary['call_count'] == 1
    assert summary['error_count'] == (0 if result == 'ok' else 1)
    if result == 'transport_error':
        assert e.usage.calls[0].status == 'error'
        assert summary['availability'] == 'unavailable'
        assert summary['input_tokens'] is None and summary['output_tokens'] is None
    status = 'unknown' if result == 'transport_error' else result
    _assert_adapter_closed(e, status=status, used=118631 if result == 'ok' else 119450)
    receipt = json.loads(e.model.receipt_sink.path.read_text())
    assert receipt['state'] == 'receipt_persisted' and receipt['receipt']['attempt_status'] == status


@pytest.mark.parametrize('stage', ['replace', 'readback'])
def test_adapter_receipt_failure_after_send_keeps_full_bound_without_retry(adapter, monkeypatch, stage):
    from pathlib import Path
    from backend.app.ports.providers import ProviderRequestNotSent, ProviderUnavailable
    e = adapter
    replace, read_bytes = e.product.os.replace, Path.read_bytes

    def fail_replace(src, dst):
        if stage == 'replace' and Path(dst) == e.model.receipt_sink.path:
            raise OSError('SIMULATED receipt persistence failure')
        return replace(src, dst)

    def fail_readback(path):
        data = read_bytes(path)
        if stage == 'readback' and path == e.model.receipt_sink.path and b'receipt_persisted' in data:
            raise OSError('SIMULATED receipt acknowledgement failure')
        return data

    with monkeypatch.context() as patch:
        patch.setattr(e.product.os, 'replace', fail_replace)
        patch.setattr(Path, 'read_bytes', fail_readback)
        with e.g.execution_scope():
            with pytest.raises(ProviderUnavailable, match='PRODUCT_RECEIPT_PERSISTENCE_FAILED'):
                e.invoke()
            assert len(e.sent) == 1 and e.model.last_receipt['attempt_status'] == 'unknown'
            with pytest.raises(ProviderRequestNotSent):
                e.invoke(e.client('SIMULATED-replay-receipt.json'))
    assert len(e.sent) == 1
    _assert_adapter_closed(e)
