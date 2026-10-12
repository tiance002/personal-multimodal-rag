"""Real Linux pinned DeepSeek recipe; public fixtures, zero provider calls."""
import argparse
import hashlib
import json
from pathlib import Path

from backend.app.adapters.models.service_capacity import ServiceCapacity
from backend.app.adapters.models.v6_text_capacity import (
    DeepseekV41RecipeRenderer, DeepseekV41TextCapacityCounter,
    OrdinaryDeepseekV41TextCapacityCounter, CapacityCounterUnavailable,
    DEEPSEEK_RECIPE_VERSION, DEEPSEEK_TOKENIZER_SHA256,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tokenizer', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    renderer = DeepseekV41RecipeRenderer(args.tokenizer)
    ordinary = OrdinaryDeepseekV41TextCapacityCounter(renderer)
    capacity = ServiceCapacity('deepseek', 'deepseek-flash', ordinary, kind='deepseek_text')
    frozen = DeepseekV41TextCapacityCounter(renderer)
    cases = []
    for cap in (1024, 2048, 4096):
        body = capacity.render_chat([dict(role='user', content='Public capacity fixture: 比较 cafe and emoji 🧪.')], cap)
        wire = json.dumps(body).encode()
        bound, reserved = capacity.measure(body, wire)
        assert renderer.last_token_count > 0 and renderer.last_token_count <= bound
        assert reserved == cap and bound + reserved <= capacity.window_tokens
        assert capacity.count_window(dict(model=capacity.model, messages=body['messages'], tools=[])) == bound
        try:
            frozen.measure('deepseek', 'deepseek-flash', body, scope='PUBLIC_SYNTHETIC')
        except CapacityCounterUnavailable as exc:
            assert str(exc) == 'OUTPUT_RESERVATION_UNSUPPORTED'
        else:
            raise AssertionError('frozen output contract widened')
        cases.append(dict(output_cap=cap, input_bound=bound, local_tokens=renderer.last_token_count,
                          wire_sha256=hashlib.sha256(wire).hexdigest()))
    report = dict(schema_version=1, status='CHECKS_PASSED', provider_calls=0,
                  check_kind='LOCAL_REAL_PINNED_RECIPE_SYNTHETIC_FIXTURES',
                  recipe_version=DEEPSEEK_RECIPE_VERSION, tokenizer_sha256=DEEPSEEK_TOKENIZER_SHA256,
                  hosted_output_evidence='UNKNOWN', frozen_512_unchanged=True, cases=cases)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
