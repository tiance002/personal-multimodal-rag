"""Reuse pinned capacity recipes for ordinary service, without old grants.

Counters verify local complete framing. Hosted equivalence remains explicitly
conditional; every final wire body is remeasured and reported usage is fenced.
Unsupported image/tool protocols remain unavailable instead of using text counts.
"""
import hashlib
import json

from backend.app.ports.context_budget import ContextDenied, ModelWindow
from backend.app.ports.providers import ProviderRequestNotSent


class ServiceCapacity:
    streaming = False
    def __init__(self, provider, model, counter, *, kind):
        from backend.app.adapters.models.ordinary_stream_capacity import OrdinaryStreamCapacityCounter
        self.streaming = type(counter) is OrdinaryStreamCapacityCounter
        checked_counter = counter.base if self.streaming else counter
        if self.streaming and (kind not in {'xing_text', 'deepseek_text'} or counter.kind != kind):
            raise ValueError('SERVICE_STREAM_PROTOCOL_INVALID')
        self.provider, self.model, self.counter, self.kind = provider, model, counter, kind
        self._output_evidence = ()
        if kind == 'embedding':
            from backend.app.adapters.models.cloud import EmbeddingAdmission, SimulatedEmbeddingCounter
            from backend.app.adapters.models.bge_tokenizer import PinnedBgeM3Tokenizer
            if (provider, model) != ('siliconflow', 'BAAI/bge-m3') or type(counter) is not EmbeddingAdmission:
                raise ValueError('SERVICE_CAPACITY_IDENTITY_INVALID')
            self.execution_kind = ('REAL' if type(counter.counter) is PinnedBgeM3Tokenizer else
                                   'SIMULATED' if type(counter.counter) is SimulatedEmbeddingCounter else 'UNKNOWN')
            self.source = counter.tokenizer_identity
            self.window_tokens = 8192
        elif kind == 'xing_text':
            from backend.app.adapters.models.siliconflow_xing_capacity import (
                SiliconFlowXingCapacityCounter, OrdinarySiliconFlowXingCapacityCounter, RENDERER_IDENTITY, OFFICIAL_CONTEXT_WINDOW,
            )
            if (provider, model) != ('siliconflow', 'XingChenAGI/Xing4.0-29B') or type(checked_counter) not in (SiliconFlowXingCapacityCounter, OrdinarySiliconFlowXingCapacityCounter):
                raise ValueError('SERVICE_CAPACITY_IDENTITY_INVALID')
            self.execution_kind, self.source, self.window_tokens = 'REAL', counter.identity, OFFICIAL_CONTEXT_WINDOW
        elif kind == 'deepseek_text':
            from backend.app.adapters.models.v6_text_capacity import (
                DeepseekV41TextCapacityCounter, OrdinaryDeepseekV41TextCapacityCounter, DeepseekV41RecipeRenderer,
                DEEPSEEK_RENDERER_IDENTITY, DEEPSEEK_CONTEXT_WINDOW,
            )
            if ((provider, model) != ('deepseek', 'deepseek-flash') or type(checked_counter) not in (DeepseekV41TextCapacityCounter, OrdinaryDeepseekV41TextCapacityCounter)
                    or type(checked_counter.renderer) is not DeepseekV41RecipeRenderer):
                raise ValueError('SERVICE_CAPACITY_IDENTITY_INVALID')
            self.execution_kind, self.source, self.window_tokens = 'REAL', (counter.identity if self.streaming else counter.renderer_identity), DEEPSEEK_CONTEXT_WINDOW
        elif kind == 'deepseek_vision':
            from backend.app.adapters.models.service_deepseek_capacity import DeepseekV41VisionCapacityCounter
            from backend.app.adapters.models.v6_text_capacity import DEEPSEEK_CONTEXT_WINDOW
            if (provider, model) != ('deepseek', 'deepseek-flash') or type(counter) is not DeepseekV41VisionCapacityCounter:
                raise ValueError('SERVICE_CAPACITY_IDENTITY_INVALID')
            self.execution_kind, self.source, self.window_tokens = 'REAL', counter.identity, DEEPSEEK_CONTEXT_WINDOW
        else:
            raise ValueError('SERVICE_CAPACITY_PROTOCOL_UNSUPPORTED')
        if self.execution_kind == 'UNKNOWN' or not self.source or self.source == 'UNKNOWN':
            raise ValueError('SERVICE_CAPACITY_SOURCE_UNKNOWN')

    @property
    def output_readiness(self):
        observed = sorted(r.output_tokens for r in self._output_evidence)
        supported = self.supported_output_caps
        return {'local_reviewed_caps': list(getattr(self.counter, 'reviewed_output_caps', (512,))),
                'delivery_protocol': 'INTERNAL_SSE_AGGREGATION_V4' if self.streaming else 'NONSTREAM_JSON_V3',
                'endpoint_output_evidence': 'PINNED_SYNTHETIC_PROVIDER_RECEIPT' if self._output_evidence else 'UNKNOWN',
                'verified_output_caps': observed,
                'observed_output_caps': observed,
                'supported_output_caps': supported,
                'inferred_lower_output_caps': [cap for cap in supported if cap > 512 and cap not in observed],
                'lower_cap_inference_basis': [r.lower_cap_inference_basis for r in self._output_evidence],
                'proof_identities': [r.identity for r in self._output_evidence],
                'hosted_recipe_equivalence': 'UNKNOWN'}

    @property
    def supported_output_caps(self):
        return sorted((set() if self.streaming else {512}) | {cap for r in self._output_evidence for cap in r.supported_output_caps})

    @property
    def output_protocol(self):
        from backend.app.ports.output_capability import PROTOCOLS, STREAM_PROTOCOLS
        return (STREAM_PROTOCOLS if self.streaming else PROTOCOLS).get((self.provider, self.model), (None,))[0]

    def bind_output_evidence(self, evidence):
        from backend.app.ports.output_capability import OutputCapabilityEvidence
        if (type(evidence) is not OutputCapabilityEvidence or
                not hasattr(self.counter, 'reviewed_output_caps') or
                (evidence.provider, evidence.model, evidence.protocol) !=
                (self.provider, self.model, self.output_protocol)):
            raise ProviderRequestNotSent('OUTPUT_CAPABILITY_EVIDENCE_INVALID')
        self._check_local_output(evidence.output_tokens)
        body = json.loads(evidence.request_wire)
        input_bound, output_bound = self.measure(body, evidence.request_wire.encode())
        usage = evidence.response['usage']
        if usage['prompt_tokens'] > input_bound or usage['completion_tokens'] > output_bound:
            raise ProviderRequestNotSent('OUTPUT_CAPABILITY_USAGE_BOUND_EXCEEDED')
        if any(r.output_tokens == evidence.output_tokens for r in self._output_evidence):
            raise ProviderRequestNotSent('OUTPUT_CAPABILITY_EVIDENCE_DUPLICATE')
        self._output_evidence += (evidence,)

    def assert_output_capacity(self, output_tokens, *, execution_kind):
        self._check_local_output(output_tokens)
        if (output_tokens > 512 or self.streaming) and execution_kind != 'SIMULATED':
            from backend.app.ports.output_capability import consume_output_probe
            # Probe admission is exact and single-use even if a cap is proven.
            if consume_output_probe(self.provider, self.model, self.output_protocol, output_tokens):
                return
            if output_tokens not in self.supported_output_caps:
                raise ProviderRequestNotSent('OUTPUT_CAPACITY_UNVERIFIED')

    def _check_local_output(self, output_tokens):
        if not hasattr(self.counter, 'reviewed_output_caps'):
            return
        if type(output_tokens) is not int or output_tokens not in self.counter.reviewed_output_caps:
            code = 'OUTPUT_CAPACITY_UNVERIFIED' if type(output_tokens) is int and output_tokens in (2048, 4096) else 'OUTPUT_CAPACITY_UNSUPPORTED'
            raise ProviderRequestNotSent(code)

    def render_chat(self, messages, output_tokens):
        self._check_local_output(output_tokens)
        from backend.app.ports.output_capability import check_output_probe_payload
        check_output_probe_payload(self.provider, self.model, self.output_protocol, output_tokens, list(messages))
        if self.kind == 'xing_text':
            return dict(model=self.model, messages=list(messages), max_tokens=output_tokens,
                        stream=self.streaming, enable_thinking=False)
        if self.kind == 'deepseek_text':
            # Preserve the exact order/protocol accepted by the pinned renderer.
            return dict(model=self.model, messages=list(messages), thinking={'type': 'disabled'},
                        max_tokens=output_tokens, temperature=0, stream=self.streaming)
        raise ProviderRequestNotSent('SERVICE_CHAT_PROTOCOL_UNSUPPORTED')

    def measure(self, payload, wire_bytes):
        if type(wire_bytes) is not bytes or json.dumps(payload).encode() != wire_bytes:
            raise ProviderRequestNotSent('MODEL_FINAL_WIRE_MISMATCH')
        if self.streaming:
            return self.counter.measure(self.provider, self.model, payload, wire_bytes)
        if self.kind == 'deepseek_vision':
            return self.counter.measure(payload, wire_bytes)
        if self.kind == 'embedding':
            if (type(payload) is not dict or set(payload) != {'model', 'input', 'encoding_format'}
                    or payload['model'] != self.model or payload['encoding_format'] != 'float'):
                raise ProviderRequestNotSent('SERVICE_EMBEDDING_PROTOCOL_UNSUPPORTED')
            return self.counter.count(payload['input']), 0
        if self.kind == 'xing_text':
            result = self.counter.measure(self.provider, self.model, payload,
                scope='NORMAL_SERVICE_FINAL_WIRE', wire_bytes=wire_bytes)
            result.assert_wire_bytes(wire_bytes)
            return result.reservation_bound, result.output_reservation
        result = self.counter.measure(self.provider, self.model, payload, scope='NORMAL_SERVICE_FINAL_WIRE')
        if result.wire_request_sha256 != hashlib.sha256(wire_bytes).hexdigest():
            raise ProviderRequestNotSent('MODEL_FINAL_WIRE_MISMATCH')
        return result.reservation_bound, result.output_reservation

    def count_window(self, envelope):
        if (type(envelope) is not dict or set(envelope) != {'model', 'messages', 'tools'}
                or envelope['model'] != self.model or envelope['tools'] != []):
            raise ContextDenied('MODEL_CONTEXT_PROTOCOL_UNSUPPORTED')
        # Reserve the largest locally reviewed ordinary cap during pre-count;
        # ModelWindow.check adds the actual selected output reserve separately.
        output = max(self.counter.reviewed_output_caps) if hasattr(self.counter, 'reviewed_output_caps') else (512 if self.kind == 'deepseek_text' else 1)
        body = self.render_chat(envelope['messages'], output)
        try:
            return self.measure(body, json.dumps(body).encode())[0]
        except ProviderRequestNotSent as error:
            if str(error) in {'XING_INPUT_LOCAL_CAP_EXCEEDED', 'XING_CONTENT_LOCAL_CAP_EXCEEDED'}:
                raise ContextDenied('MODEL_CONTEXT_WINDOW_EXCEEDED') from None
            raise ContextDenied(str(error)) from None

    def model_window(self):
        if self.kind in {'embedding', 'deepseek_vision'}:
            raise ValueError('CHAT_WINDOW_REQUIRED')
        return ModelWindow(self.provider, self.model, window_tokens=self.window_tokens,
            capacity_source=self.source + ';official-context-and-local-client-limits',
            tokenizer_source=self.source + ';LOCAL_CONDITIONAL_HOSTED_EQUIVALENCE', counter=self.count_window)
