from datetime import datetime, timedelta, timezone

import pytest

from scripts.s3_product_check import validate_before_upload, validate_upload_snapshot


def _snapshot(*, call_overrides=None, document_overrides=None, task_overrides=None):
    observed_at = datetime.now(timezone.utc)
    call = {
        'outcome': 'UNKNOWN',
        'finished_at': None,
        'tenant_id': 'tenant-1',
        'subject_id': 'worker-1',
        'request_id': 'version-1/image/0/caption',
    }
    call.update(call_overrides or {})
    document = {
        'id': 'document-1',
        'parse_status': 'processing',
        'metadata': {'source_version': 'version-1'},
    }
    document.update(document_overrides or {})
    task = {
        'id': 'task-1',
        'knowledge_id': 'document-1',
        'source_version': 'version-1',
        'state': 'processing',
        'claim_token': 'claim-1',
        'lease_until': observed_at + timedelta(minutes=1),
        'tenant_id': 'tenant-1',
        'subject_id': 'worker-1',
    }
    task.update(task_overrides or {})
    return {
        'observed_at': observed_at,
        'calls': [call],
        'documents': [document],
        'tasks': [task],
        'historical_calls_unchanged': True,
    }


def test_scoped_unfinished_unknown_is_in_flight_while_owned_worker_processes_upload():
    result = _snapshot()

    assert validate_upload_snapshot(result, 'document-1', worker_active=True) == 'in_flight'


def test_finished_unknown_is_terminal_even_when_document_and_task_are_processing():
    result = _snapshot(call_overrides={'finished_at': datetime.now(timezone.utc)})

    with pytest.raises(RuntimeError, match='UNRESOLVED_PROVIDER_UNKNOWN_STOP'):
        validate_upload_snapshot(result, 'document-1', worker_active=True)


@pytest.mark.parametrize(
    ('call_overrides', 'task_overrides', 'worker_active'),
    [
        ({'subject_id': 'other-worker'}, {}, True),
        ({}, {'claim_token': None}, True),
        ({}, {}, False),
    ],
)
def test_unknown_stops_when_scope_claim_or_worker_does_not_match(
    call_overrides, task_overrides, worker_active
):
    result = _snapshot(call_overrides=call_overrides, task_overrides=task_overrides)

    with pytest.raises(RuntimeError, match='UNRESOLVED_PROVIDER_UNKNOWN_STOP'):
        validate_upload_snapshot(result, 'document-1', worker_active=worker_active)


def test_unknown_image_task_is_terminal():
    result = _snapshot(task_overrides={'state': 'unknown'})

    with pytest.raises(RuntimeError, match='IMAGE_TASK_UNKNOWN_STOP'):
        validate_upload_snapshot(result, 'document-1', worker_active=True)


def test_future_upload_is_blocked_by_any_unsettled_unknown():
    result = _snapshot()

    with pytest.raises(RuntimeError, match='UNSETTLED_PROVIDER_CALL_STOP'):
        validate_before_upload(result)


def test_future_upload_is_allowed_after_calls_are_settled():
    result = _snapshot()
    result['calls'][0].update(outcome='RESPONSE_RECEIVED', finished_at=datetime.now(timezone.utc))

    assert validate_before_upload(result) is None
