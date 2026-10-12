"""Local real recipe/tokenizer smoke; synthetic images, zero provider sends.

Run inside an isolated Linux container with the pinned wheel already installed.
No credentials, business DB/storage, URL fetcher or historical grant is used.
"""
import base64
import hashlib
import json
from io import BytesIO
from pathlib import Path
import platform

from PIL import Image

from backend.app.adapters.models.service_configuration import configured_service_capacity
from backend.app.adapters.models.service_deepseek_capacity import CAPTION_PROMPT, NATIVE_SHA256
from backend.app.config import Settings
from backend.app.domain.model_registry import ModelRegistry
from backend.app.ports.providers import ProviderRequestNotSent


def main():
    settings = Settings(deepseek_tokenizer_path=Path('/assets/deepseek-v41/tokenizer.json'))
    registry = ModelRegistry.frozen_defaults()
    text, reason = configured_service_capacity(registry.select('chat_expensive'), settings, None)
    if reason:
        raise AssertionError(reason)
    body = text.render_chat([{'role': 'user', 'content': 'Public capacity fixture: 测试。'}], 512)
    wire = json.dumps(body).encode()
    bound, output = text.measure(body, wire)
    assert bound > 0 and output == 512
    assert text.count_window({'model': text.model, 'messages': body['messages'], 'tools': []}) == bound
    vision, reason = configured_service_capacity(registry.select('vision'), settings, None)
    if reason:
        raise AssertionError(reason)
    cases = []
    for width, height in [(32, 32), (1024, 768), (2000, 2000), (4096, 64)]:
        buffer = BytesIO()
        Image.new('RGB', (width, height), (24, 80, 160)).save(buffer, format='PNG')
        raw = buffer.getvalue()
        payload = dict(model=vision.model, max_tokens=512, stream=False, thinking={'type': 'disabled'},
            messages=[{'role': 'user', 'content': [dict(type='text', text=CAPTION_PROMPT),
                dict(type='image_url', image_url=dict(url='data:image/png;base64,' + base64.b64encode(raw).decode(), detail='original'))]}])
        wire = json.dumps(payload).encode()
        measured, reserved_output = vision.measure(payload, wire)
        assert measured > 0 and reserved_output == 512
        cases.append(dict(width=width, height=height, input_bound=measured, output_reserved=reserved_output,
            image_sha256=hashlib.sha256(raw).hexdigest(), wire_sha256=hashlib.sha256(wire).hexdigest()))
    negative = 0
    oversized = BytesIO()
    Image.new('RGB', (2048, 2048), (24, 80, 160)).save(oversized, format='PNG')
    for url in ('https://example.invalid/private', 'data:image/png;base64,invalid!',
                'data:image/png;base64,' + base64.b64encode(oversized.getvalue()).decode()):
        bad = json.loads(json.dumps(payload))
        bad['messages'][0]['content'][1]['image_url']['url'] = url
        try:
            vision.measure(bad, json.dumps(bad).encode())
        except ProviderRequestNotSent:
            negative += 1
        else:
            raise AssertionError('invalid inline image admitted')
    report = dict(schema_version=1, status='CHECKS_PASSED', check_kind='LOCAL_REAL_RECIPE_SYNTHETIC_FIXTURES',
        python_version=platform.python_version(), native_module_sha256=NATIVE_SHA256,
        provider_calls=0, image_fixture_kind='SYNTHETIC', positive_cases=5, negative_cases=negative,
        hosted_equivalence='UNKNOWN', text_input_bound=bound, image_cases=cases)
    Path('/reports/deepseek-capacity-local.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
