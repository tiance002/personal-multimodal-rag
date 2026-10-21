"""Uncertain dispatch cannot release a budget reservation."""
import asyncio
import pytest
from backend.weknora_port.models import Message, ChatOptions, ProviderError
from tests.weknora_port.test_provider_transport import setup, context


def test_default_error_sent_flag_cannot_override_transport_dispatch_observation():
    async def run():
        def dispatch(request):
            raise ProviderError('SYNTHETIC_AFTER_DISPATCH')
        model,transport,gate = setup(dispatch)
        try:
            with pytest.raises(ProviderError,match='SYNTHETIC_AFTER_DISPATCH'):
                await model.chat((Message('user','fixture'),),ChatOptions(),context('occupancy'))
            assert len(gate.finishes) == 1
            assert gate.finishes[0][1] == 'UNKNOWN'
            assert gate.finishes[0][2].total_tokens is None
        finally:
            await transport.aclose()
    asyncio.run(run())
