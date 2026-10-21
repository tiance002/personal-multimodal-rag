"""Redirect fixed repo-evidence writes from selected integration modules into this
run's OUT directory. Tracked evidence files under docs/weknora-port/evidence are
never overwritten; identity run_id is supplied by the explicit env convention.
"""
import os
from pathlib import Path

TARGETS = {
    'test_runtime_pg.py',
    'test_affinity_kb_http_pg.py',
    'test_cancel_partial_http_pg.py',
    'test_stream_error_http_pg.py',
}
OUT = Path(os.environ['WP_EVIDENCE_OUT'])


def pytest_collection_modifyitems(session, config, items):
    redirects = []
    for module in {item.module for item in items}:
        if Path(module.__file__).name not in TARGETS:
            continue

        def evidence_path(value='.', *args, _module=module):
            path = Path(value, *args)
            prefix = Path('docs/weknora-port/evidence')
            if not path.is_absolute() and path.is_relative_to(prefix):
                target = OUT / path.relative_to(prefix)
                target.parent.mkdir(parents=True, exist_ok=True)
                redirects.append({'original': str(path), 'actual': str(target)})
                return target
            return path

        module.Path = evidence_path
    config._wp_redirects = redirects


def pytest_sessionfinish(session, exitstatus):
    redirects = getattr(session.config, '_wp_redirects', [])
    path = OUT / 'redirects.json'
    import json
    path.write_text(json.dumps(redirects, indent=2) + '\n', encoding='utf-8')
