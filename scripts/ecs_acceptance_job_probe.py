"""Zero-Provider persistence probe for the detached ECS acceptance runner.

This program does not import the application, connect to a database, or send
network requests. Its output is process/persistence evidence, never QA evidence.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time


def durable(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + '.probe-tmp')
    with temporary.open('x', encoding='utf8') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    descriptor = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--steps', type=int, default=4)
    parser.add_argument('--interval', type=float, default=10)
    args = parser.parse_args(argv)
    if not 1 <= args.steps <= 10 or not 0 < args.interval <= 30:
        parser.error('steps must be 1..10 and interval greater than0 and at most30 seconds')
    output = Path(os.environ['ECS_ACCEPTANCE_OUTPUT_DIR'])
    if not output.is_absolute() or not output.is_dir() or output.is_symlink():
        raise ValueError('existing absolute job output directory required')
    manifest = json.loads(Path(os.environ['ECS_ACCEPTANCE_MANIFEST']).read_text(encoding='utf8'))
    if manifest.get('kind') != 'zero_provider_probe':
        raise ValueError('probe requires explicit zero_provider_probe manifest')
    for step in range(1, args.steps + 1):
        checkpoint = {'kind': 'ZERO_PROVIDER_PERSISTENCE_PROBE', 'step': step,
                      'steps': args.steps, 'provider_calls': 0,
                      'updated_at': datetime.now(timezone.utc).isoformat(),
                      'pid': os.getpid(), 'session_id': os.getsid(0)}
        durable(output / 'checkpoint.json', checkpoint)
        with (output / 'progress.jsonl').open('a', encoding='utf8') as stream:
            stream.write(json.dumps(checkpoint, sort_keys=True) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        print(json.dumps(checkpoint, sort_keys=True), flush=True)
        time.sleep(args.interval)
    durable(output / 'result.json', {'kind': 'ZERO_PROVIDER_PERSISTENCE_PROBE',
            'runtime_complete': True, 'provider_calls': 0, 'unknown': 0, 'unsettled': 0,
            'steps_completed': args.steps, 'qa_acceptance': 'NOT_RUN',
            'finished_at': datetime.now(timezone.utc).isoformat()})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
