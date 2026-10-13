"""Independent ordinary stream wire, with unchanged pinned prompt framing.

The stream field controls delivery; it is not fed into either pinned chat
template. Reuse the nonstream counter ONLY for local prompt framing, while
binding the actual stream wire separately. Hosted support requires new proof.
No frozen counter, renderer, measurement, grant or old proof is rewritten.
"""
import json

from backend.app.ports.providers import ProviderRequestNotSent


class OrdinaryStreamCapacityCounter:
    def __init__(self, base, kind):
        from backend.app.adapters.models.siliconflow_xing_capacity import OrdinarySiliconFlowXingCapacityCounter
        from backend.app.adapters.models.v6_text_capacity import OrdinaryDeepseekV41TextCapacityCounter
        expected = {'xing_text': OrdinarySiliconFlowXingCapacityCounter,
                    'deepseek_text': OrdinaryDeepseekV41TextCapacityCounter}
        if kind not in expected or type(base) is not expected[kind]:
            raise ValueError('ORDINARY_STREAM_COUNTER_INVALID')
        self.base, self.kind = base, kind
        self.reviewed_output_caps = base.reviewed_output_caps
        self.identity = (base.identity if kind == 'xing_text' else base.renderer_identity) + ';ordinary-stream-frame/v4'

    def measure(self, provider, model, payload, wire_bytes):
        if (type(payload) is not dict or payload.get('stream') is not True
                or type(wire_bytes) is not bytes or json.dumps(payload).encode() != wire_bytes):
            raise ProviderRequestNotSent('ORDINARY_STREAM_FINAL_WIRE_INVALID')
        # Only delivery differs. All other fields still undergo the complete
        # existing pinned counter validation; extra flags are not invented.
        framing_body = {**payload, 'stream': False}
        if self.kind == 'xing_text':
            result = self.base.measure(provider, model, framing_body,
                scope='ORDINARY_STREAM_PINNED_PROMPT_FRAME_V4', wire_bytes=json.dumps(framing_body).encode())
        else:
            result = self.base.measure(provider, model, framing_body,
                scope='ORDINARY_STREAM_PINNED_PROMPT_FRAME_V4')
        return result.reservation_bound, result.output_reservation
