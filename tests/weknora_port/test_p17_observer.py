import asyncio
import json
from types import SimpleNamespace
import pytest
from scripts.weknora_port_p17_observed_api import RawResponse, decoder_observation, ATTEMPT
from backend.weknora_port.references import SourceRegistry, ChunkReference, CitationStreamDecoder


def test_raw_bytes_no_prefetch_no_double_consumption(tmp_path):
    class Response:
        count = 0
        async def aiter_bytes(self):
            for part in [b'data: {"a":', b'1}\n\n', b'data: [DONE]\n\n']:
                self.count += 1
                yield part
    async def run():
        source = Response(); wrapped = RawResponse(source, tmp_path, 'run/answer/1/cheap', 'chat')
        assert source.count == 0
        chunks = [part async for part in wrapped.aiter_bytes()]
        assert source.count == 3 and wrapped.path.read_bytes() == b''.join(chunks)
        records = [json.loads(line) for p in (tmp_path/'diagnostics').glob('*') for line in p.read_text().splitlines()]
        assert records[0]['complete'] and not records[0]['headers_saved']
    asyncio.run(run())


def test_cap_only_limits_recording_never_modifies_stream(tmp_path, monkeypatch):
    import scripts.weknora_port_p17_observed_api as m
    monkeypatch.setattr(m, 'MAX_RAW_BYTES', 3)
    class Response:
        async def aread(self): return b'123456'
    async def run():
        wrapper = RawResponse(Response(), tmp_path, 'rank', 'rerank')
        assert await wrapper.aread() == b'123456' and wrapper.path.read_bytes() == b'123'
        records = [json.loads(line) for p in (tmp_path/'diagnostics').glob('*') for line in p.read_text().splitlines()]
        assert not records[0]['complete'] and records[0]['seen_bytes'] == 6
    asyncio.run(run())


def test_shared_answer_guard_denies_third_send_and_duplicate(tmp_path):
    import httpx
    from scripts.weknora_port_p17_observed_api import install_model
    from backend.weknora_port.models import provider_config, ProviderError, Message
    from tests.weknora_port.test_provider_transport import setup, context, response
    async def run():
        cfg = provider_config('siliconflow', 'XingChenAGI/Xing4.0-29B')
        sent=[]
        def handler(req):
            sent.append(req)
            return httpx.Response(200,json=response(cfg))
        model, transport, gate=setup(handler,config=cfg)
        claims=set(); install_model(model,tmp_path,claims)
        try:
            for identity in ['run1/answer/1/cheap','run2/answer/1/cheap']:
                await model.chat([Message('user','q')],None,context(identity))
            for identity in ['run3/answer/1/cheap','run1/answer/1/cheap']:
                with pytest.raises(ProviderError,match='P17_ANSWER_CAP_OR_IDENTITY_DENIED'):
                    await model.chat([Message('user','q')],None,context(identity))
            assert len(sent)==2
        finally: await transport.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('text', ['claim <ref id="c1"/>', 'claim', 'claim <ref id="c999"/>', '<kb chunk_id="forged"/>'])
def test_actual_decoder_observation_preserves_all_split_outputs(tmp_path, text):
    from backend.weknora_port.generation import coordinator
    def registry():
        r=SourceRegistry(); r.register_chunk(ChunkReference('real-source', knowledge_base_id='kb'))
        return r
    async def run():
        original = coordinator.CitationStreamDecoder
        async with decoder_observation(tmp_path):
            for split in range(len(text)+1):
                baseline = CitationStreamDecoder(registry())
                expected = baseline.feed(text[:split])+baseline.feed(text[split:])+baseline.flush()
                token=ATTEMPT.set({'id':'run/answer/1/cheap', 'decoders':0})
                try:
                    observed = coordinator.CitationStreamDecoder(registry())
                    actual = observed.feed(text[:split])+observed.feed(text[split:])+observed.flush()
                    assert actual == expected
                    assert observed.registry.used_handles == baseline.registry.used_handles
                    assert observed.registry.invalid_count == baseline.registry.invalid_count
                finally: ATTEMPT.reset(token)
        assert coordinator.CitationStreamDecoder is original
    asyncio.run(run())


def test_actual_coordinator_attempt_is_correlated_to_decoder_and_final(tmp_path):
    from tests.weknora_port.test_generation import setup, event, request, CTX
    async def run():
        co, *_ = setup([event('claim <ref id="c1"/>', done=True, finish='stop', final=True)])
        async with decoder_observation(tmp_path):
            await co.run(request(mode='CHEAP_ONLY'), CTX)
        rows=[json.loads(line) for p in (tmp_path/'diagnostics').glob('*') for line in p.read_text(encoding='utf-8').splitlines()]
        final=next(r for r in rows if r['kind']=='attempt_result')
        assert final['finish_reason']=='stop' and '<kb ' in final['answer']
        assert any(r['kind']=='decoder_feed' and r['channel']=='answer' and 'c1' in r['used_handles'] for r in rows)
    asyncio.run(run())
