"""REAL pinned tokenizer; SIMULATED transport. No model or network requests."""
import hashlib
import os
from pathlib import Path

import pytest

from backend.app.adapters.models.bge_tokenizer import (
    CLIENT_TOKEN_LIMIT, MODEL_TOKEN_LIMIT, REVISION, TOKENIZER_SHA256,
    PinnedBgeM3Tokenizer,
)
from backend.app.adapters.models.cloud import EmbeddingAdmission
from backend.app.domain.model_registry import ModelRegistry
from backend.app.ports.model_access import model_access
from backend.app.ports.providers import ProviderRequestNotSent
from backend.tests.test_model_provider_contracts import SimulatedGuard, embedding

ASSET = Path('D:/RAG-ModelAssets/bge-m3') / REVISION / 'tokenizer.json'


@pytest.fixture(scope='module')
def admission():
    # No auto-download or alternate model/tokenizer on a missing asset.
    return EmbeddingAdmission.from_bge_m3_file(ASSET)


def test_pinned_source_integrity_and_special_tokens(admission):
    assert hashlib.sha256(ASSET.read_bytes()).hexdigest() == TOKENIZER_SHA256
    counter = admission.counter
    assert type(counter) is PinnedBgeM3Tokenizer
    assert not admission.non_truncating_verified
    assert counter.provider_non_truncation == 'UNKNOWN'
    text = '中文标题\n\n金额 -7.25；表格 |  42  |'
    ids = counter._tokenizer.encode(text, add_special_tokens=True).ids
    plain = counter._tokenizer.encode(text, add_special_tokens=False).ids
    assert ids == [0, *plain, 2]
    assert counter(text) == len(plain)+2


def test_complete_header_and_body_counted_without_rewriting(admission, monkeypatch):
    monkeypatch.setenv('SILICONFLOW_API_KEY', 'SIMULATED-R4-ONLY')
    adapter, calls = embedding(ModelRegistry.frozen_defaults(), SimulatedGuard())
    adapter.admission = admission
    content = 'Root > 中文标题\n\n| 项目 | 成本 |\n| 甲 |  -7.25  |'
    with model_access('embedding', allowed=True):
        adapter.embed([content], 1)
    assert calls[0]['input'] == [content]
    assert adapter.planned_tokens == admission.counter(content)
    assert admission.counter(content) > admission.counter('| 甲 |  -7.25  |')


@pytest.mark.parametrize('extra,allowed', [(0, True), (1, False), (513, False)])
def test_real_tokenizer_client_limit_precedes_transport(admission, monkeypatch, extra, allowed):
    monkeypatch.setenv('SILICONFLOW_API_KEY', 'SIMULATED-R4-ONLY')
    # Space-separated "a" without trailing whitespace maps to one token each.
    # Trailing whitespace itself is tokenized; first-round evidence retains
    # the failed generator assumption rather than weakening the input limit.
    content = ' '.join(['a'] * (CLIENT_TOKEN_LIMIT-2+extra))
    assert admission.counter(content) == CLIENT_TOKEN_LIMIT+extra
    assert admission.counter('a '*(MODEL_TOKEN_LIMIT+20)) > MODEL_TOKEN_LIMIT
    guard = SimulatedGuard()
    adapter, calls = embedding(ModelRegistry.frozen_defaults(), guard)
    adapter.admission = admission
    with model_access('embedding', allowed=True):
        if allowed:
            adapter.embed([content], 1)
        else:
            with pytest.raises(ProviderRequestNotSent, match='TOKEN_LIMIT'):
                adapter.embed([content], 1)
    assert bool(calls) is allowed
    assert bool(guard.reservations) is allowed
    assert adapter.requests == int(allowed)


def test_hash_corruption_refuses_before_model_construction(tmp_path):
    bad = tmp_path/'tokenizer.json'
    bad.write_bytes(b'SIMULATED CORRUPT FILE')
    with pytest.raises(ValueError, match='INTEGRITY'):
        EmbeddingAdmission.from_bge_m3_file(bad)


def test_default_unknown_and_forged_bounded_counter_stay_closed():
    for instance in [EmbeddingAdmission(), EmbeddingAdmission(lambda _: 1,
            tokenizer_identity=f'BAAI/bge-m3@{REVISION}:forged')]:
        with pytest.raises(ProviderRequestNotSent, match='UNKNOWN'):
            instance.count(['short'])


@pytest.mark.parametrize('denial', ['disabled', 'scope', 'key', 'guard', 'other_role'])
def test_all_egress_gates_still_precede_count_and_send(admission, monkeypatch, denial):
    monkeypatch.setenv('SILICONFLOW_API_KEY', 'SIMULATED-R4-ONLY')
    guard = SimulatedGuard()
    adapter, calls = embedding(ModelRegistry.frozen_defaults(), guard)
    adapter.admission = admission
    if denial == 'disabled': adapter.enabled = False
    if denial == 'guard': adapter.usage_guard = None
    if denial == 'key': monkeypatch.delenv('SILICONFLOW_API_KEY')
    with model_access('chat_expensive' if denial == 'other_role' else 'embedding',
                      allowed=denial != 'scope'):
        with pytest.raises(ProviderRequestNotSent): adapter.embed(['synthetic'], 1)
    assert calls == [] and guard.reservations == []


def test_batch_count_retains_individual_special_tokens(admission):
    texts = ['中文', '甲 42.75', 'Beta']
    assert admission.count(texts) == sum(admission.counter(t) for t in texts)
    with pytest.raises(ProviderRequestNotSent): admission.count([' '])
    with pytest.raises(ProviderRequestNotSent): admission.count(['a']*33)
