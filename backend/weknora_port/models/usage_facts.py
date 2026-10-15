"""Raw-presence usage evidence; never an estimate or an invoice."""
from dataclasses import dataclass, asdict
import hashlib
import json
import math
from contextvars import ContextVar

SIMULATED_USAGE = ContextVar('weknora_simulated_usage', default=False)


@dataclass(frozen=True)
class UsageValue:
    value: int | None
    present: bool
    valid: bool
    path: str


@dataclass(frozen=True)
class ProviderUsageFacts:
    semantics: str
    fields: tuple[tuple[str, UsageValue], ...]
    numeric_raw: str
    raw_sha256: str
    derived_total: int | None
    simulated: bool = False
    observed_lower_bound: int = 0
    prior_numeric_raw: tuple[str, ...] = ()

    def get(self, name):
        return dict(self.fields)[name]

    def public(self):
        return {**asdict(self),'reported_total':asdict(self.get('total'))}

    def accounting(self):
        """OpenAI total includes input and output; cache is input breakdown."""
        inp, out, total = (self.get(k) for k in ('input', 'output', 'total'))
        cache_input = (self.get('cache_read').value or 0) + (self.get('cache_miss').value or 0)
        lower = max(self.observed_lower_bound, total.value or 0, max(inp.value or 0, cache_input,
            self.get('cache_write').value or 0) + (out.value or 0))
        valid = all(not v.present or v.valid for _, v in self.fields)
        if inp.value is not None and out.value is not None and total.value is not None:
            valid = valid and inp.value + out.value == total.value
        for k in ('cache_read', 'cache_miss'):
            v = self.get(k).value
            if v is not None and inp.value is not None and v > inp.value:
                valid = False
        settled = total.value if valid and total.present and total.valid and total.value>=lower and self.semantics=='openai-input-inclusive/v1' else None
        return settled, lower


def capture_usage(raw, *, semantics='openai-input-inclusive/v1', simulated=None):
    # Keep numeric facts only: unknown strings may echo private provider data.
    raw = raw if isinstance(raw, dict) else {}
    paths = {'input': ('prompt_tokens',), 'output': ('completion_tokens',),
             'total': ('total_tokens',), 'cache_read': ('prompt_cache_hit_tokens', 'prompt_tokens_details.cached_tokens', 'cached_tokens'),
             'cache_write': ('cache_creation_input_tokens', 'prompt_tokens_details.cache_write_tokens'),
             'cache_miss': ('prompt_cache_miss_tokens',)}
    if semantics=='openai-responses/v1':
        paths.update(input=('input_tokens',),output=('output_tokens',),cache_read=('input_tokens_details.cached_tokens',))
    elif semantics=='anthropic-messages/v1':
        paths.update(input=('input_tokens',),output=('output_tokens',),cache_read=('cache_read_input_tokens',))
    elif semantics=='google-generative-ai/v1':
        paths.update(input=('promptTokenCount',),output=('candidatesTokenCount',),total=('totalTokenCount',),cache_read=('cachedContentTokenCount',))
    elif semantics=='ollama/v1':
        paths.update(input=('prompt_eval_count',),output=('eval_count',))
    fields, numeric = [], {}
    for name, choices in paths.items():
        for path in choices:
            obj = raw
            for key in path.split('.'):
                if not isinstance(obj, dict) or key not in obj:
                    break
                obj = obj[key]
            else:
                valid = type(obj) is int and obj >= 0
                numeric[path] = obj if (type(obj) in (int,bool) or obj is None or
                    type(obj) is float and math.isfinite(obj)) else 'INVALID'
                fields.append((name, UsageValue(obj if valid else None, True, valid, path)))
                break
        else:
            fields.append((name, UsageValue(None, False, False, choices[0])))
    values = dict(fields)
    derived = values['input'].value + values['output'].value if all(values[k].value is not None for k in ('input','output')) else None
    encoded = json.dumps(numeric, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return ProviderUsageFacts(semantics, tuple(fields), encoded,
        hashlib.sha256(encoded.encode()).hexdigest(), derived,
        SIMULATED_USAGE.get() if simulated is None else simulated)


def aggregate_facts(usages):
    facts=[u.provider_usage_facts for u in usages]
    if not facts or not all(isinstance(f,ProviderUsageFacts) for f in facts):return None
    fields=[]
    for key,_ in facts[0].fields:
        values=[f.get(key) for f in facts]
        complete=all(v.present and v.valid for v in values)
        fields.append((key,UsageValue(sum(v.value for v in values) if complete else None,
            all(v.present for v in values),complete,'disjoint-physical-calls/'+key)))
    encoded=json.dumps([json.loads(f.numeric_raw) for f in facts],sort_keys=True,separators=(',',':'))
    v=dict(fields);derived=v['input'].value+v['output'].value if all(v[k].value is not None for k in ('input','output')) else None
    return ProviderUsageFacts('aggregate-disjoint-calls/v1',tuple(fields),encoded,
        hashlib.sha256(encoded.encode()).hexdigest(),derived,all(f.simulated for f in facts))
