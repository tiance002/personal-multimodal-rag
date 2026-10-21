"""Deterministic Python SSE behavior against executed unchanged upstream Go."""
import asyncio
import json
from pathlib import Path

import pytest
from backend.weknora_port.models.stream import sse_events

ORACLE = json.loads((Path(__file__).parent / 'fixtures/sse_go_oracle.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', ORACLE['cases'])
def test_python_reader_matches_actual_fixed_go_reader(case):
    class Fragmented:
        async def aiter_bytes(self):
            body = case['input'].encode('utf-8')
            for offset in range(0, len(body), 3):
                yield body[offset:offset+3]
    async def read():
        return [dict(done=done, data=data.decode('utf-8')) async for done, data in sse_events(Fragmented())]
    assert asyncio.run(read()) == case['events']
    assert ORACLE['upstream'] == '3e8b0bfc80b845b2d4b2ed683994748741450a97'
    assert ORACLE['exit_code'] == 0
