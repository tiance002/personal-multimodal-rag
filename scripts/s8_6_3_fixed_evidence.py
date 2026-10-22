"""Load frozen messages for same-evidence comparisons; CLI is offline only.

No transport, runtime, grant or ledger is created here. A future authorized job
can pass the returned Message/ChatOptions to its existing Registry models.
The scoring file is deliberately neither opened nor accepted by this loader.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from backend.weknora_port.models.types import Message, ChatOptions


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def load_inputs(root, case_id):
    root = Path(root)
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf8'))
    case = next(row for row in manifest['cases'] if row['case_id'] == case_id)
    path = (root / case['request_file']).resolve()
    if not path.is_relative_to((root / 'requests').resolve()):
        raise ValueError('FIXED_INPUT_PATH_INVALID')
    body = json.loads(path.read_text(encoding='utf8'))
    if digest(body) != case['body_sha256'] or digest(body['messages']) != case['messages_sha256']:
        raise ValueError('FIXED_INPUT_HASH_MISMATCH')
    if set(body) - {'messages','model','temperature','max_tokens','enable_thinking','stream','stream_options'}:
        raise ValueError('FIXED_INPUT_PARAMETERS_UNSUPPORTED')
    if (body.get('enable_thinking') is not False or body.get('max_tokens') != 2048 or
            not all(set(m) == {'role','content'} and isinstance(m['content'], str)
                    for m in body['messages'])):
        raise ValueError('FIXED_INPUT_CONTRACT_CHANGED')
    messages = tuple(Message(m['role'], m['content']) for m in body['messages'])
    options = ChatOptions(temperature=body['temperature'], send_temperature=True,
                          max_completion_tokens=body['max_tokens'], thinking=False)
    return messages, options, case


def render_pair(root, case_id, models):
    """Pure render using already resolved existing ProviderModel instances."""
    if set(models) != {'cheap','expensive'}:
        raise ValueError('FIXED_PAIR_ROLES_REQUIRED')
    messages, options, case = load_inputs(root, case_id)
    bodies = {role: model.build_request_body(messages, options, stream=True)
              for role, model in models.items()}
    for body in bodies.values():
        thinking_off = (body.get('enable_thinking') is False or
                        body.get('thinking') == {'type':'disabled'})
        if (digest(body['messages']) != case['messages_sha256'] or
                body.get('max_tokens') != 2048 or not thinking_off):
            raise ValueError('FIXED_PAIR_RENDER_MISMATCH')
    return bodies


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    args = parser.parse_args()
    from backend.weknora_port.models import provider_config
    from backend.weknora_port.models.provider import ProviderModel
    models = {'cheap': ProviderModel(provider_config('siliconflow', 'Qwen/Qwen3.5-4B'), None),
              'expensive': ProviderModel(provider_config('deepseek', 'deepseek-flash'), None)}
    manifest = json.loads((args.package / 'manifest.json').read_text(encoding='utf8'))
    results = []
    for case in manifest['cases']:
        bodies = render_pair(args.package, case['case_id'], models)
        results.append({'case_id':case['case_id'], 'status':'OFFLINE_RENDER_PASS',
                        'body_sha256':{role:digest(body) for role, body in bodies.items()}})
    print(json.dumps({'status':'OFFLINE_ONLY', 'provider_calls':0, 'cases':results}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
