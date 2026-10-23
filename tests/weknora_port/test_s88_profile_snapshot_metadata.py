"""Lossless private snapshot metadata deduplication; synthetic source data."""
from copy import deepcopy
from types import SimpleNamespace
import json

import pytest

from scripts import s8_6_1_api_acceptance as profile
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.strategy_executor import RoundResult
from backend.weknora_port.retrieval.types import SearchResult


def rich_state():
    metadata = {'source_version': 'v1', 'original': 'SYNTHETIC locator payload. ' * 2000}
    rows = [SearchResult(str(i), 'Exact selected content ' + str(i), .8,
        knowledge_base_id='kb', knowledge_id='doc', start_at=0, end_at=24,
        metadata=deepcopy(metadata), chunk_metadata=deepcopy(metadata)) for i in range(12)]
    state = PipelineState(query='q0 unchanged', rewrite_query='same effective query',
        search_results=rows, rerank_results=deepcopy(rows), merge_results=deepcopy(rows))
    child = deepcopy(state)
    state.strategy_rounds = [RoundResult(child)]
    return state


def test_repeated_metadata_is_written_once_and_snapshot_restores_exactly(tmp_path):
    state = rich_state()
    legacy = profile.state_snapshot(state)
    raw = json.dumps(legacy)
    assert raw.count('SYNTHETIC locator payload.') == 2000 * 12 * 2 * 5
    store = profile.SnapshotMetadataStore(tmp_path)
    small = profile.state_snapshot(state, metadata_store=store)
    again = profile.state_snapshot(state, metadata_store=store)
    blobs = list((tmp_path / 'snapshot-metadata').glob('*.json'))
    assert len(blobs) == 1
    assert small == again
    assert len(json.dumps(small)) + sum(p.stat().st_size for p in blobs) < len(raw) // 8
    assert small['query'] == state.query
    assert small['merge_results'][0]['content'] == state.merge_results[0].content
    assert small['merge_results'][0]['metadata']['source_version'] == 'v1'
    assert profile.restore_state_snapshot(small, tmp_path) == legacy
    assert profile.state_snapshot(state) == legacy


def test_mutated_metadata_gets_new_blob_without_overwriting_old(tmp_path):
    state = rich_state()
    store = profile.SnapshotMetadataStore(tmp_path)
    before = profile.state_snapshot(state, metadata_store=store)
    old = profile.restore_state_snapshot(before, tmp_path)
    state.merge_results[0].metadata['selection'] = 'new provenance'
    after = profile.state_snapshot(state, metadata_store=store)
    assert len(list((tmp_path / 'snapshot-metadata').glob('*.json'))) == 2
    assert profile.restore_state_snapshot(before, tmp_path) == old
    assert profile.restore_state_snapshot(after, tmp_path) == profile.state_snapshot(state)


@pytest.mark.parametrize('damage', ['missing', 'modified'])
def test_missing_or_corrupt_metadata_cannot_restore_as_complete(tmp_path, damage):
    store = profile.SnapshotMetadataStore(tmp_path)
    snapshot = profile.state_snapshot(rich_state(), metadata_store=store)
    path = next((tmp_path / 'snapshot-metadata').glob('*.json'))
    if damage == 'missing':
        path.unlink()
    else:
        path.write_text('{}', encoding='utf8')
    with pytest.raises((ValueError, FileNotFoundError)):
        profile.restore_state_snapshot(snapshot, tmp_path)


def test_install_profile_uses_explicit_versioned_snapshot(tmp_path):
    import asyncio
    from backend.weknora_port.service import TurnConfiguration
    state = rich_state()
    async def configuration(turn):
        return TurnConfiguration(state, {})
    runtime = SimpleNamespace(ordinary=SimpleNamespace(configuration=configuration))
    profile.install_profile(runtime, tmp_path)
    asyncio.run(runtime.ordinary.configuration(SimpleNamespace(run_id='run')))
    saved = json.loads(next(tmp_path.glob('*.profile.json')).read_text(encoding='utf8'))
    assert saved['state']['snapshot_encoding'] == 'metadata-blobs/v1'
    restored = profile.restore_state_snapshot(saved['state'], tmp_path)
    assert restored['query'] == state.query
    assert restored['search_results'][0]['metadata'] == state.search_results[0].metadata
