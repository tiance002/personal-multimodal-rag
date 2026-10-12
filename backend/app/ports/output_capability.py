"""Pinned public synthetic receipts proving ordinary requested output caps.

These receipts require observed generation reaching a requested cap, not hosted tokenization,
actual model weights, invoice settlement, or successful long-form generation.
The owner-controlled deployment pins the exact receipt file SHA256.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from backend.app.ports.providers import ProviderRequestNotSent
from backend.app.ports.run_lifecycle import current_execution, current_attempt, execution_owner

OUTPUT_PROBE_PROMPT_VERSION = 3
SYNTHETIC_OUTPUT_PROBE = (
    'Write a comprehensive educational tutorial in English about designing a '
    'personal local document library. Use twelve numbered sections: goals and '
    'scope; folder organization; file naming; metadata; document versions; '
    'text extraction; search indexes; keyword search; semantic search; source '
    'citations; backups and recovery; and testing. Develop every section with '
    'detailed explanations, concrete fictional examples, practical tradeoffs, '
    'and step-by-step exercises. Aim for at least 6000 words overall, with '
    'roughly 500 words per section. Begin directly with section 1, explain '
    'each topic fully before moving on, and avoid an abbreviated outline.'
)
OUTPUT_CAPS = (512, 1024, 2048, 4096)
PROTOCOLS = {
    ('deepseek', 'deepseek-flash'): ('deepseek-text-thinking-disabled-output-reach/v3', 'https://api.deepseek.com/chat/completions'),
    ('siliconflow', 'XingChenAGI/Xing4.0-29B'): ('siliconflow-xing-text-thinking-disabled-output-reach/v3', 'https://api.siliconflow.cn/v1/chat/completions'),
}
VENDOR_OUTPUT_CONTRACTS = {
    'deepseek': 'https://api-docs.deepseek.com/api/create-chat-completion/',
    'siliconflow': 'https://docs.siliconflow.cn/docs/api/chat-completions-post',
}
VENDOR_CONTRACT_REVIEW_DATE = '2026-10-12'


def _require(condition):
    if not condition:
        raise ProviderRequestNotSent('OUTPUT_CAPABILITY_EVIDENCE_INVALID')


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class OutputCapabilityEvidence:
    provider: str
    model: str
    protocol: str
    endpoint: str
    output_tokens: int
    request_wire: str
    response_wire: str
    run_id: str
    attempt_id: str

    def __post_init__(self):
        _require(PROTOCOLS.get((self.provider, self.model)) == (self.protocol, self.endpoint))
        _require(type(self.output_tokens) is int and self.output_tokens in OUTPUT_CAPS)
        _require(all(type(v) is str and v.strip() for v in
                     (self.request_wire, self.response_wire, self.run_id, self.attempt_id)))
        try:
            body, response = json.loads(self.request_wire), json.loads(self.response_wire)
        except (ValueError, TypeError):
            raise ProviderRequestNotSent('OUTPUT_CAPABILITY_EVIDENCE_INVALID') from None
        expected = dict(model=self.model, messages=[dict(role='user', content=SYNTHETIC_OUTPUT_PROBE)],
                        max_tokens=self.output_tokens, stream=False)
        if self.provider == 'deepseek':
            expected.update(thinking={'type': 'disabled'}, temperature=0)
        else:
            expected['enable_thinking'] = False
        _require(body == expected and type(body) is dict and
                 all(type(body[k]) is type(v) for k, v in expected.items()))
        _require(type(response) is dict and response.get('model') == self.model)
        usage = response.get('usage')
        _require(type(usage) is dict and all(type(usage.get(k)) is int for k in
                                           ('prompt_tokens', 'completion_tokens', 'total_tokens')))
        _require(usage['prompt_tokens'] > 0 and usage['completion_tokens'] == self.output_tokens
                 and usage['total_tokens'] == usage['prompt_tokens'] + usage['completion_tokens'])
        choices = response.get('choices')
        _require(type(choices) is list and len(choices) == 1 and type(choices[0]) is dict)
        choice = choices[0]
        message = choice.get('message')
        _require(choice.get('finish_reason') == 'length' and type(message) is dict
                 and type(message.get('content')) is str and bool(message['content'].strip())
                 and not message.get('refusal') and not message.get('tool_calls')
                 and not message.get('reasoning_content'))

    @property
    def identity(self):
        return f'{self.protocol};cap={self.output_tokens};request={_hash(self.request_wire.encode())};response={_hash(self.response_wire.encode())}'

    @property
    def supported_output_caps(self):
        """Lower integer maxima inferred from vendor contract, not observed runs."""
        return tuple(cap for cap in OUTPUT_CAPS if cap <= self.output_tokens)

    @property
    def lower_cap_inference_basis(self):
        return dict(kind='CURRENT_VENDOR_MAX_TOKENS_UPPER_BOUND_CONTRACT',
                    source=VENDOR_OUTPUT_CONTRACTS[self.provider],
                    reviewed_date=VENDOR_CONTRACT_REVIEW_DATE,
                    observed_output_ceiling=self.output_tokens,
                    lower_caps_individually_observed=False)


def load_output_capabilities(path, expected_sha256):
    try:
        _require(type(expected_sha256) is str and re.fullmatch('[0-9a-f]{64}', expected_sha256))
        raw = Path(path).read_bytes()
        _require(_hash(raw) == expected_sha256)
        data = json.loads(raw)
        _require(type(data) is dict and set(data) == {'schema_version', 'receipts'}
                 and type(data['schema_version']) is int and data['schema_version'] == 3
                 and type(data['receipts']) is list and bool(data['receipts']))
        receipts = tuple(OutputCapabilityEvidence(**row) for row in data['receipts'])
        keys = [(r.provider, r.model, r.protocol, r.output_tokens) for r in receipts]
        _require(len(set(keys)) == len(keys))
        _require(len({r.attempt_id for r in receipts}) == len(receipts))
        return receipts
    except (OSError, ValueError, TypeError, KeyError):
        raise ProviderRequestNotSent('OUTPUT_CAPABILITY_EVIDENCE_INVALID') from None


@dataclass
class _ProbeAdmission:
    provider: str
    model: str
    protocol: str
    output_tokens: int
    run_id: str
    attempt_id: str
    consumed: bool = False


_probe = ContextVar('ordinary_synthetic_output_probe', default=None)


def _matching_probe(provider, model, protocol, output_tokens):
    admission = _probe.get()
    context = current_execution.get()
    if (admission is None or context is None or
            (admission.provider, admission.model, admission.protocol, admission.output_tokens) !=
            (provider, model, protocol, output_tokens) or current_attempt.get() != admission.attempt_id or
            context[0].run_id != admission.run_id or
            execution_owner.get() != (admission.run_id, context[0].owner)):
        return None
    return admission


def consume_output_probe(provider, model, protocol, output_tokens):
    admission = _matching_probe(provider, model, protocol, output_tokens)
    if admission is None:
        return False
    if admission.consumed:
        raise ProviderRequestNotSent('OUTPUT_PROBE_ALREADY_CONSUMED')
    admission.consumed = True
    return True


def check_output_probe_payload(provider, model, protocol, output_tokens, messages):
    admission = _matching_probe(provider, model, protocol, output_tokens)
    if admission is not None and (not admission.consumed or messages !=
            [dict(role='user', content=SYNTHETIC_OUTPUT_PROBE)]):
        raise ProviderRequestNotSent('OUTPUT_PROBE_SYNTHETIC_PAYLOAD_REQUIRED')


@contextmanager
def output_probe_admission(*, provider, model, protocol, output_tokens, run_id, attempt_id):
    """CLI-only owner-authorized synthetic probe, still subject to all send gates.

The caller durably claims its bounded owner grant before entering. This context
only unlocks one exact output-cap check; it grants no scope, budget or transport.
"""
    _require(PROTOCOLS.get((provider, model), (None,))[0] == protocol
             and type(output_tokens) is int and output_tokens in (1024, 2048, 4096)
             and all(type(v) is str and v for v in (run_id, attempt_id)) and _probe.get() is None)
    admission = _ProbeAdmission(provider, model, protocol, output_tokens, run_id, attempt_id)
    token = _probe.set(admission)
    try:
        _require(_matching_probe(provider, model, protocol, output_tokens) is admission)
        yield
    finally:
        _probe.reset(token)
