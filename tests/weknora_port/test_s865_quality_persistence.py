"""Offline SQL contract: quality survives replay retention and never rewrites history."""
import asyncio
import json
from dataclasses import replace

import pytest

from backend.weknora_port.streaming.replay import FinalCommit, project_final_quality
from tests.weknora_port.test_history_storage import HistoryConnection, adapter


QUALITY = {'citation_quality': {'status': 'MISSING', 'valid_handle_count': 0,
    'invalid_handle_count': 0, 'usable_evidence_count': 11, 'assessment_complete': True,
    'semantic_support': 'NOT_ASSESSED'}, 'generation_issues': ['MISSING_CITATIONS']}


def test_new_final_commit_persists_quality_with_answer_in_single_scoped_update():
    conn = HistoryConnection({'is_completed': False, 'agent_steps': [{'type': 'existing'}]})
    commit = FinalCommit('session', 'assistant', 'run', 'answer', [], {'total_tokens': 7}, quality=QUALITY)
    asyncio.run(adapter(conn).commit_final(commit))
    updates = [(sql, args) for sql, args in conn.calls if sql.startswith('UPDATE')]
    assert len(updates) == 1
    sql, args = updates[0]
    assert 'agent_steps=' in sql and 'is_completed=true' in sql and 'usage=' in sql
    saved = next(json.loads(v) for v in args if isinstance(v, str) and 'generation_quality' in v)
    assert saved == [{'type': 'existing'}, {'type': 'generation_quality', 'request_id': 'run', **QUALITY}]
    assert args[-2:] == (10000, 'local')


def test_quality_commit_is_idempotent_and_does_not_backfill_old_completed_answers():
    saved = {'type': 'generation_quality', 'request_id': 'run', **QUALITY}
    row = {'is_completed': True, 'content': 'answer', 'knowledge_references': [],
           'usage': None, 'agent_steps': [saved]}
    conn = HistoryConnection(row)
    commit = FinalCommit('session', 'assistant', 'run', 'answer', [], quality=QUALITY)
    asyncio.run(adapter(conn).commit_final(commit))
    assert not any(sql.startswith('UPDATE') for sql, _ in conn.calls)
    with pytest.raises(ValueError, match='FINAL_COMMIT_CONFLICT'):
        asyncio.run(adapter(conn).commit_final(replace(commit, quality={**QUALITY, 'generation_issues': []})))
    row['agent_steps'] = []  # historical answer without the new observation
    with pytest.raises(ValueError, match='FINAL_COMMIT_CONFLICT'):
        asyncio.run(adapter(conn).commit_final(commit))


def test_quality_metadata_rejects_content_and_unverified_semantic_claims_before_write():
    for quality in ({**QUALITY, 'answer': 'sensitive'},
                    {**QUALITY, 'citation_quality': {**QUALITY['citation_quality'], 'semantic_support': 'PASS'}}):
        conn = HistoryConnection({'is_completed': False})
        commit = FinalCommit('session', 'assistant', 'run', 'answer', [], quality=quality)
        with pytest.raises(ValueError, match='FINAL_QUALITY_INVALID'):
            asyncio.run(adapter(conn).commit_final(commit))
        assert not any(sql.startswith('UPDATE') for sql, _ in conn.calls)


def test_history_quality_requires_exact_completed_assistant_run_and_preserves_old_absence():
    step = {'type':'generation_quality','request_id':'run',**QUALITY}
    row = {'role':'assistant','is_completed':True,'request_id':'run','agent_steps':[step],
           'generation_issues':[]}
    assert project_final_quality(row)['generation_issues'] == ['MISSING_CITATIONS']
    assert project_final_quality(row)['citation_quality'] == QUALITY['citation_quality']
    for changes in ({'role':'user'},{'request_id':'other'},{'is_completed':False},
                    {'turn_status':'cancelled'},{'agent_steps':[]},{'agent_steps':[step,step]}):
        unchanged = {**row, **changes}
        assert project_final_quality(unchanged) == unchanged
