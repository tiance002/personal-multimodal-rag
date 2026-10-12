"""Read the owner-created test cluster identity; never discover another DB."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def isolated_target():
    target = json.loads((ROOT/'var/reports/p5-redis-lifecycle-r1/isolated-target.json').read_text(encoding='utf-8'))
    assert (target['database'], target['user'], target['host'], target['database_oid'],
            target['system_identifier'], target['kind']) == (
        'p55a_lifecycle_test', 'p55a', '127.0.0.1', 16384, '7694635764170571814', 'ISOLATED_TEST_ONLY')
    assert target['pg_container'] == '4db80024010c3c48b68684bb1ff28e471f5a9e72fd90f7b460a54091e425718f'
    assert target['redis_container'] == 'cdb838c5c05f0d5fa1cf3c5a290078c33dbd90a84983deffc8e3146c6b95e4bf'
    for field in ('pg_port', 'redis_port'):
        assert type(target[field]) is int and 1024 <= target[field] <= 65535
    assert target['pg_port'] != target['redis_port']
    return target


def database_url(target):
    return f"postgresql+psycopg://p55a:SIMULATED_TEST_ONLY@127.0.0.1:{target['pg_port']}/p55a_lifecycle_test"
