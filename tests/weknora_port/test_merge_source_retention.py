"""Source/Summary identity survives Merge only in bounded source mode."""
import asyncio
from copy import deepcopy

import pytest

from backend.weknora_port.models.types import CallContext, TrustedScope
from backend.weknora_port.retrieval.merge import merge
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.query import default_segmenter
from backend.weknora_port.retrieval.search import SearchTarget, RetrievalScopeError, deduplicate, partial_overlap
from backend.weknora_port.retrieval.types import SearchResult


CTX = CallContext('retention', TrustedScope('owner', ('kb',)))
MODE = 'bounded_source_v1'


def row(id, content, score, kind='text', doc='doc', kb='kb'):
    return SearchResult(id, content, score, knowledge_id=doc, knowledge_base_id=kb,
                        chunk_type=kind)


def merged(rows, mode=MODE, search=()):
    state = PipelineState(query='question', targets=(SearchTarget('kb'),),
                          rerank_results=deepcopy(rows), search_results=list(search),
                          source_evidence_mode=mode)
    asyncio.run(merge(state, CTX, None, segmenter=default_segmenter()))
    return state.merge_results


def test_miracl_91294_shape_retains_passed_source_and_summary_without_score_changes():
    source = row('3f6a7679', '# 人马座\n\n人马座是夏季夜空中最大、最显著的星座之一，它西接天蝎座、東連摩羯座，北面是蛇夫座、盾牌座和巨蛇座，南边则是一系列小型星座，如望远镜座、显微镜座、南冕座等。', .6969)
    summary = row('6d65929f', '# Summary\n人马座的星座概述及其周边星座分布。\n\n人马座是夏季夜空中最大、最显著的星座之一，西接天蝎座、东连摩羯座，北面是蛇夫座、盾牌座和巨蛇座，南边是一系列小型星座如望远镜座、显微镜座、南冕座等。', .7026, 'summary')
    rejected = row('search-only', 'not admitted by rerank', .99, doc='other')
    assert [r.id for r in merged([summary, source], 'off')] == [summary.id]
    actual = merged([summary, source], search=[rejected])
    assert [(r.id, r.chunk_type, r.score) for r in actual] == [
        (summary.id, 'summary', .7026), (source.id, 'text', .6969)]
    assert actual[1].content == source.content


@pytest.mark.parametrize('reverse', [False, True])
def test_exact_text_across_origins_retains_both_independent_ids(reverse):
    rows = [row('hint', 'identical evidence body', .9, 'summary'),
            row('source', 'identical evidence body', .8)]
    if reverse:
        rows = list(reversed(rows))
    actual = merged(rows)
    assert {r.id for r in actual} == {'hint', 'source'}
    assert [(r.id, r.score) for r in actual] == [('hint', .9), ('source', .8)]
    assert len(merged(rows, 'off')) == 1


def test_source_only_exact_and_partial_deduplication_stays_unchanged():
    rows = [row('a', 'alpha beta gamma', .9, doc='one'),
            row('exact', 'alpha beta gamma', .8, doc='two'),
            row('overlap', 'alpha beta gamma delta', .7, doc='three'),
            row('distinct', 'other independent evidence', .6, doc='four')]
    assert [(r.id, r.content, r.score) for r in merged(rows)] == [
        (r.id, r.content, r.score) for r in merged(rows, 'off')]
    assert [r.id for r in merged(rows)] == ['a', 'distinct']


def test_one_identity_cannot_survive_twice_by_claiming_different_origins():
    rows = [row('same', 'first source body', .9),
            row('same', 'different generated body', .8, 'summary')]
    actual = merged(rows)
    assert [(r.id, r.content) for r in actual] == [('same', 'first source body')]


def test_off_and_unrecognized_modes_keep_existing_cross_origin_deduplication():
    rows = [row('hint', 'alpha beta gamma', .9, 'summary'),
            row('source', 'alpha beta gamma delta', .8)]
    expected = [r.id for r in partial_overlap(deduplicate(rows))]
    assert [r.id for r in merged(rows, 'off')] == expected
    assert [r.id for r in merged(rows, 'future-mode')] == expected


@pytest.mark.parametrize('kind', ['text', 'summary'])
def test_cross_kb_result_is_rejected_by_existing_scope_path(kind):
    rows = [row('allowed', 'allowed body', .9),
            row('foreign', 'different foreign body', .8, kind, kb='foreign')]
    with pytest.raises(RetrievalScopeError):
        merged(rows)
