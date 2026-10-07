import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.tests.test_stable_run_requests import chain_fixture


def fixture(tmp_path, monkeypatch):
    invoke, path, requests = chain_fixture(tmp_path, monkeypatch)
    chain = next(cell.cell_contents for cell in invoke.__closure__
                 if hasattr(cell.cell_contents, 'budget_gate'))
    return invoke, chain, path, requests


def assert_budget(chain, states, used):
    gate = chain.budget_gate
    rows = list(gate.reservations.values())
    assert sorted(row['state'] for row in rows) == sorted(states)
    assert gate._used(rows[0]['month']) == used


def test_duplicate_releases_only_its_new_reservation(tmp_path, monkeypatch):
    invoke, chain, path, requests = fixture(tmp_path, monkeypatch)
    invoke()
    invoke()
    assert len(requests) == 1
    assert json.loads(path.read_text())['consumed_attempts'] == 1
    assert_budget(chain, ['settled', 'released'], 1)


def test_concurrent_duplicates_do_not_occupy_extra_budget(tmp_path, monkeypatch):
    invoke, chain, path, requests = fixture(tmp_path, monkeypatch)
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: invoke(), range(6)))
    assert len(requests) == 1
    assert json.loads(path.read_text())['consumed_attempts'] == 1
    assert_budget(chain, ['settled'] + ['released'] * 5, 1)


@pytest.mark.parametrize('denial', ['disabled', 'cap', 'provider_disabled'])
def test_known_pretransport_denial_releases_budget(tmp_path, monkeypatch, denial):
    invoke, chain, path, requests = fixture(tmp_path, monkeypatch)
    data = json.loads(path.read_text())
    if denial == 'disabled':
        data['calls_allowed_in_this_task'] = False
    elif denial == 'cap':
        from backend.app.application.session_attempts import SessionAttemptGate
        data['authorized_limit'] = 1
        path.write_text(json.dumps(data))
        gate = SessionAttemptGate(path)
        gate.finish(gate.reserve(request_id='prior-legal-stage'), 'ok')
        data = json.loads(path.read_text())
    else:
        chain.cloud_answer_gateway.cloud_enabled = False
    path.write_text(json.dumps(data))
    before = data['consumed_attempts']
    invoke()
    assert requests == []
    assert json.loads(path.read_text())['consumed_attempts'] == before
    assert_budget(chain, ['released'], 0)


@pytest.mark.parametrize('failure', ['timeout', 'misleading_denial_text'])
def test_transport_unknown_is_retained_even_after_duplicate(tmp_path, monkeypatch, failure):
    from backend.app.adapters.models import deepseek
    from backend.app.ports.providers import ProviderUnavailable
    invoke, chain, path, requests = fixture(tmp_path, monkeypatch)
    class FailingTransport:
        def open(self, request, timeout):
            requests.append(request)
            if failure == 'timeout':
                raise TimeoutError('synthetic timeout')
            raise ProviderUnavailable('SESSION_CALLS_DISABLED')
    monkeypatch.setattr(deepseek, 'build_opener', lambda *args: FailingTransport())
    invoke()
    assert_budget(chain, ['unknown'], 1)
    invoke()
    assert len(requests) == 1
    assert json.loads(path.read_text())['consumed_attempts'] == 1
    assert_budget(chain, ['unknown', 'released'], 1)


def test_success_settles_one_reservation(tmp_path, monkeypatch):
    invoke, chain, path, requests = fixture(tmp_path, monkeypatch)
    invoke()
    assert len(requests) == 1
    assert json.loads(path.read_text())['consumed_attempts'] == 1
    assert_budget(chain, ['settled'], 1)


def test_legacy_untyped_provider_failure_remains_unknown(tmp_path, monkeypatch):
    from backend.app.ports.providers import ProviderUnavailable
    invoke, chain, path, requests = fixture(tmp_path, monkeypatch)
    class LegacyCloud:
        provider_kind = 'cloud'
        provider_name = 'legacy'
        def answer_with_budget(self, prompt, timeout, max_tokens):
            raise ProviderUnavailable('SESSION_REQUEST_ALREADY_RESERVED')
    chain.cloud_answer_gateway = LegacyCloud()
    invoke()
    assert requests == []
    assert json.loads(path.read_text())['consumed_attempts'] == 0
    assert_budget(chain, ['unknown'], 1)
