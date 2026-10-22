"""Offline fixed-evidence prompt preparation. No transport, credentials or sends.

Image bodies reuse hash-verified immutable P27 image data URLs. Generation
bodies copy historical actual messages, adding a test-only system suffix.
Loaders never open scoring_only.json. Historical IDs are provenance only;
future authorized execution must allocate new IDs via existing gates.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from backend.weknora_port.image_multimodal import caption_prompt
from backend.weknora_port.models.types import ChatOptions, Message


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'docs/evaluation/evidence/s8_6_4/prompt_diagnostics'
P27 = ROOT / 'docs/evaluation/evidence/p27-live-20261017'
GENERATION_CANDIDATE_SUFFIX = (
    'Preserve source names, entities, numerical values, units and technical terms precisely.\n'
    'Do not silently translate, correct or replace an entity with a different known entity.\n'
    'Distinguish counts of people, events, sessions, percentages and dates.\n'
    "Answer the user's requested fact directly. Avoid unrelated elaboration that may introduce unsupported claims."
)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf8')


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value)).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def read_manifest(package):
    return read_json(Path(package) / 'manifest.json')


def _checked_json(package, record, directory):
    path = (Path(package) / record['file']).resolve()
    if not path.is_relative_to((Path(package) / directory).resolve()):
        raise ValueError('PROMPT_INPUT_PATH_INVALID')
    raw = path.read_bytes()
    if digest(raw) != record['file_sha256']:
        raise ValueError('PROMPT_INPUT_HASH_MISMATCH')
    value = json.loads(raw)
    if digest(value) != record['body_sha256']:
        raise ValueError('PROMPT_INPUT_HASH_MISMATCH')
    return value


def _source(record):
    path = (ROOT / record['file']).resolve()
    if not path.is_relative_to((ROOT / 'docs/evaluation/evidence/p27-live-20261017').resolve()):
        raise ValueError('IMAGE_SOURCE_PATH_INVALID')
    raw = path.read_bytes()
    if digest(raw) != record['file_sha256']:
        raise ValueError('IMAGE_SOURCE_HASH_MISMATCH')
    return json.loads(raw)


def render_image_pair(package, case_id):
    """Return complete directly reusable Provider bodies, without transport."""
    case = next(row for row in read_manifest(package)['images'] if row['case_id'] == case_id)
    old = _source(case['source_request'])['body']
    prompt = _checked_json(package, case['candidate_prompt'], 'image_prompts')['prompt']
    image = old['messages'][0]['content'][0]['image_url']['url']
    if digest(base64.b64decode(image.split(',', 1)[1], validate=True)) != case['image_sha256']:
        raise ValueError('IMAGE_BYTES_HASH_MISMATCH')
    candidate = copy.deepcopy(old)
    candidate['messages'][0]['content'][1]['text'] = prompt
    if digest(old) != case['old_body_sha256'] or digest(candidate) != case['candidate_body_sha256']:
        raise ValueError('IMAGE_BODY_HASH_MISMATCH')
    from backend.weknora_port.models import provider_config
    from backend.weknora_port.models.provider import ProviderModel
    model = ProviderModel(provider_config('deepseek', old['model']), None)
    rendered = {}
    for variant, body in (('old', old), ('candidate', candidate)):
        messages = (Message('user', body['messages'][0]['content'][1]['text'], images=(image,)),)
        options = ChatOptions(max_tokens=body['max_tokens'], temperature=body['temperature'])
        rendered[variant] = model.build_request_body(messages, options)
        if rendered[variant] != body:
            raise ValueError('IMAGE_ADAPTER_RENDER_MISMATCH')
    return rendered


def load_generation_pair(package, case_id):
    case = next(row for row in read_manifest(package)['generation'] if row['case_id'] == case_id)
    pair = {variant: _checked_json(package, case[variant], 'generation_requests')
            for variant in ('old', 'candidate')}
    old, candidate = pair['old'], pair['candidate']
    expected = copy.deepcopy(old)
    expected['messages'][0]['content'] += '\n\n' + GENERATION_CANDIDATE_SUFFIX
    if candidate != expected or digest(old['messages'][1:]) != case['fixed_user_messages_sha256']:
        raise ValueError('GENERATION_FIXED_EVIDENCE_CHANGED')
    if (set(old) - {'messages', 'model', 'temperature', 'max_tokens', 'enable_thinking', 'stream', 'stream_options'}
            or old.get('enable_thinking') is not False or old.get('max_tokens') != 2048
            or old['messages'][0]['role'] != 'system'
            or not all(set(m) == {'role', 'content'} and isinstance(m['content'], str) for m in old['messages'])):
        raise ValueError('GENERATION_FIXED_CONTRACT_CHANGED')
    return pair


def render_generation_pair(package, case_id):
    """Pure render using existing adapters; historical locale/context fixed."""
    from backend.weknora_port.models import provider_config
    from backend.weknora_port.models.provider import ProviderModel
    models = {'cheap': ProviderModel(provider_config('siliconflow', 'Qwen/Qwen3.5-4B'), None),
              'expensive': ProviderModel(provider_config('deepseek', 'deepseek-flash'), None)}
    rendered = {}
    for variant, body in load_generation_pair(package, case_id).items():
        messages = tuple(Message(m['role'], m['content']) for m in body['messages'])
        options = ChatOptions(temperature=body['temperature'], send_temperature=True,
                              max_completion_tokens=body['max_tokens'], thinking=False)
        rendered[variant] = {role: model.build_request_body(messages, options, stream=True)
                             for role, model in models.items()}
        for wire in rendered[variant].values():
            if (wire['messages'] != body['messages'] or wire.get('max_tokens') != 2048
                    or not (wire.get('enable_thinking') is False or wire.get('thinking') == {'type': 'disabled'})):
                raise ValueError('GENERATION_RENDER_CONTRACT_CHANGED')
    return rendered


def context_statistics(body):
    user = body['messages'][-1]['content']
    context = re.search(r'<retrieval\b[^>]*>.*?</retrieval>', user, re.S)
    contents = re.findall(r'<content>(.*?)</content>', user, re.S)
    return {'system_characters': len(body['messages'][0]['content']),
            'user_characters': len(user),
            'context_characters': len(context[0]) if context else 0,
            'chunks': len(contents), 'source_characters': sum(map(len, contents)),
            'exact_duplicate_content_occurrences': sum(n - 1 for n in Counter(contents).values()),
            'source_order_sha256': [digest(s.encode('utf8')) for s in contents],
            'token_counts': 'NOT_MEASURED; characters are not tokens',
            'causal_conclusion': 'NOT_ESTABLISHED; short contexts also contain misreads'}


def _write(package, relative, value):
    path = Path(package) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    return {'file': relative, 'file_sha256': digest(path.read_bytes()), 'body_sha256': digest(value)}


def prepare(package):
    """Create a new offline package; fail rather than overwrite evidence."""
    package = Path(package)
    if package.exists():
        raise ValueError('PROMPT_PACKAGE_ALREADY_EXISTS')
    analysis_path = ROOT / 'docs/evaluation/S8_6_3_SOURCE_IDENTITY_AND_FAILURE_LAYERS.results.json'
    analysis = read_json(analysis_path)
    manifest = {'schema': 's8.6.4-prompt-diagnostics/v1', 'status': 'OFFLINE_PREPARED_NEW_AUTHORIZATION_REQUIRED',
                'new_provider_calls': 0, 'new_input_tokens': 0, 'new_output_tokens': 0,
                'model_quality': 'NOT_RUN', 'database_calls': 0,
                'historical_ids': 'PROVENANCE_ONLY; never reuse IDs, grants or CLOSED allocations',
                'generation_candidate_scope': 'TEST_ONLY; production generation configuration unchanged',
                'candidate_suffix_sha256': digest(GENERATION_CANDIDATE_SUFFIX.encode()),
                'source_analysis_sha256': digest(analysis_path.read_bytes()),
                'execution': 'Pure loaders/renderers only. Future authorized calls use existing Registry/SendGate/LocalExecution; no retries or new send system.',
                'images': [], 'generation': []}
    scoring = {'schema': 's8.6.4-prompt-scoring-only/v1',
               'warning': 'OFFLINE REVIEW ONLY; not loaded by request renderers', 'images': [], 'generation': []}
    historical = read_json(P27 / 'analysis.json')['cases']
    requests = list((P27 / 'worker-evidence/provider-requests').glob('*.json'))
    for case in historical:
        receipt = next((f, read_json(f)) for f in requests
                       if read_json(f).get('wire_sha256') == case['actual_vision_wire_sha256'])
        source_path, request = receipt
        body = request['body']
        prompt = caption_prompt('Chinese (Simplified)')
        prompt_record = _write(package, f"image_prompts/{case['qid']}.json", {'prompt': prompt})
        candidate = copy.deepcopy(body)
        candidate['messages'][0]['content'][1]['text'] = prompt
        image = body['messages'][0]['content'][0]['image_url']['url']
        if digest(base64.b64decode(image.split(',', 1)[1], validate=True)) != case['asset_sha256']:
            raise ValueError('HISTORICAL_IMAGE_HASH_MISMATCH')
        response_path = P27 / 'worker-evidence/deepseek-responses' / (case['actual_vision_wire_sha256'] + '.json')
        response = read_json(response_path)['response']
        manifest['images'].append({'case_id': case['qid'],
            'source_request': {'file': source_path.relative_to(ROOT).as_posix(), 'file_sha256': digest(source_path.read_bytes())},
            'historical_response': {'file': response_path.relative_to(ROOT).as_posix(), 'file_sha256': digest(response_path.read_bytes())},
            'source_pdf_sha256': case['source_sha256'], 'physical_page': case['physical_page'],
            'image_sha256': case['asset_sha256'], 'candidate_prompt': prompt_record,
            'old_prompt_sha256': digest(body['messages'][0]['content'][1]['text'].encode()),
            'candidate_prompt_sha256': digest(prompt.encode()), 'old_body_sha256': digest(body),
            'candidate_body_sha256': digest(candidate),
            'historical_usage': {'status': 'HISTORICAL_ONLY', 'calls': 1, **response['usage']},
            'historical_model': response['model'], 'historical_finish_reason': response['choices'][0]['finish_reason'],
            'new_quality': 'NOT_RUN', 'input_isolation': 'No question or Gold added; only caption text changes'})
        scoring['images'].append({'case_id': case['qid'], 'historical_caption': case['caption'],
            'historical_assessment': case['manual_basis'], 'quality_dimensions': [
                'readable_text_accuracy', 'category_value_association', 'omissions', 'fabrication'],
            'candidate_assessment': 'NOT_RUN'})
    for index, case in enumerate(r for r in analysis['failure_layers']
                                 if r['main_layer'] == 'D' or r['case_id'] == 'miracl-zh-692002#0'):
        artifact = case['actual_generation']['request_artifact']
        source_path = Path(artifact['path'])
        source_raw = source_path.read_bytes()
        if digest(source_raw) != artifact['sha256']:
            raise ValueError('HISTORICAL_GENERATION_ARTIFACT_HASH_MISMATCH')
        body = json.loads(source_raw)['body']
        if digest(body) != case['actual_generation']['body_sha256']:
            raise ValueError('HISTORICAL_GENERATION_BODY_HASH_MISMATCH')
        candidate = copy.deepcopy(body)
        candidate['messages'][0]['content'] += '\n\n' + GENERATION_CANDIDATE_SUFFIX
        manifest['generation'].append({'case_id': case['case_id'], 'layer': case['main_layer'],
            'secondary_layers': case['secondary_layers'], 'final_input_sufficient': case['final_input_sufficient'],
            'diagnosis': case['reason'], 'target_chunk_id': case['target_chunk_id'],
            'necessary_source_presence': case['necessary_source_presence'],
            'historical_request_artifact': artifact,
            'old': _write(package, f'generation_requests/{index + 1:02d}-old.json', body),
            'candidate': _write(package, f'generation_requests/{index + 1:02d}-candidate.json', candidate),
            'fixed_user_messages_sha256': digest(body['messages'][1:]),
            'context_statistics': context_statistics(body),
            'locale': 'HISTORICAL_ENGLISH_FIXED; language changes require a separate comparison',
            'candidate_quality': 'NOT_RUN'})
        scoring['generation'].append({'case_id': case['case_id'], 'historical_answer': case['answer'],
            'historical_review': case['historical_core_review'], 'candidate_assessment': 'NOT_RUN'})
    _write(package, 'scoring_only.json', scoring)
    _write(package, 'manifest.json', manifest)
    return manifest


def validate(package):
    manifest = read_manifest(package)
    images = [{'case_id': row['case_id'], 'bodies': {variant: digest(body) for variant, body in
               render_image_pair(package, row['case_id']).items()}} for row in manifest['images']]
    generation = [{'case_id': row['case_id'], 'bodies': {variant: {role: digest(body) for role, body in models.items()}
                  for variant, models in render_generation_pair(package, row['case_id']).items()}}
                  for row in manifest['generation']]
    return {'status': 'OFFLINE_RENDER_PASS', 'new_provider_calls': 0, 'model_quality': 'NOT_RUN',
            'images': images, 'generation': generation}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true', help='Create new evidence package without sending')
    parser.add_argument('--package', type=Path, default=PACKAGE)
    args = parser.parse_args()
    if args.prepare:
        prepare(args.package)
    print(json.dumps(validate(args.package), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
