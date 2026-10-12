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
    def __init__(self, provider, model, counter, *, kind):
        self.provider, self.model, self.counter, self.kind = provider, model, counter, kind
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
                SiliconFlowXingCapacityCounter, RENDERER_IDENTITY, OFFICIAL_CONTEXT_WINDOW,
            )
            if (provider, model) != ('siliconflow', 'XingChenAGI/Xing4.0-29B') or type(counter) is not SiliconFlowXingCapacityCounter:
                raise ValueError('SERVICE_CAPACITY_IDENTITY_INVALID')
            self.execution_kind, self.source, self.window_tokens = 'REAL', counter.identity, OFFICIAL_CONTEXT_WINDOW
        elif kind == 'deepseek_text':
            from backend.app.adapters.models.v6_text_capacity import (
                DeepseekV41TextCapacityCounter, DeepseekV41RecipeRenderer,
                DEEPSEEK_RENDERER_IDENTITY, DEEPSEEK_CONTEXT_WINDOW,
            )
            if ((provider, model) != ('deepseek', 'deepseek-flash') or type(counter) is not DeepseekV41TextCapacityCounter
                    or type(counter.renderer) is not DeepseekV41RecipeRenderer):
                raise ValueError('SERVICE_CAPACITY_IDENTITY_INVALID')
            self.execution_kind, self.source, self.window_tokens = 'REAL', DEEPSEEK_RENDERER_IDENTITY, DEEPSEEK_CONTEXT_WINDOW
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

    def render_chat(self, messages, output_tokens):
        if self.kind == 'xing_text':
            return dict(model=self.model, messages=list(messages), max_tokens=output_tokens,
                        stream=False, enable_thinking=False)
        if self.kind == 'deepseek_text':
            # Preserve the exact order/protocol accepted by the pinned renderer.
            return dict(model=self.model, messages=list(messages), thinking={'type': 'disabled'},
                        max_tokens=output_tokens, temperature=0, stream=False)
        raise ProviderRequestNotSent('SERVICE_CHAT_PROTOCOL_UNSUPPORTED')

    def measure(self, payload, wire_bytes):
        if type(wire_bytes) is not bytes or json.dumps(payload).encode() != wire_bytes:
            raise ProviderRequestNotSent('MODEL_FINAL_WIRE_MISMATCH')
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
        # DeepSeek's audited recipe has an exact 512 output contract; Xing's
        # input framing is independent of the reserved output length.
        output = 512 if self.kind == 'deepseek_text' else 1
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
