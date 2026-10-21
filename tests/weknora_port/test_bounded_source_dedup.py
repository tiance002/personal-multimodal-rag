"""Offline regressions for bounded source identity and merge boundaries."""
import asyncio
from copy import deepcopy

import pytest

from backend.weknora_port.models.types import CallContext, TrustedScope
from backend.weknora_port.retrieval.merge import merge
from backend.weknora_port.retrieval.pipeline import PipelineState
from backend.weknora_port.retrieval.search import SearchTarget, RetrievalScopeError, search
from backend.weknora_port.retrieval.types import SearchResult

MODE = 'bounded_source_v1'
CTX = CallContext('dedup', TrustedScope('owner', ('kb', 'kb2')))
TARGETS = (SearchTarget('kb'), SearchTarget('kb2'))


def row(identity, content, *, score=.8, kb='kb', doc='doc', version='v1', **kw):
    return SearchResult(identity, content, score, knowledge_base_id=kb,
                        knowledge_id=doc, metadata={'source_version': version},
                        chunk_metadata={'source_version': version}, **kw)


def state(rows=(), mode=MODE):
    return PipelineState(query='question', rewrite_query='question', targets=TARGETS,
                         enable_expansion=False, source_evidence_mode=mode,
                         rerank_results=deepcopy(list(rows)))


def merged(rows, mode=MODE):
    value = state(rows, mode)
    asyncio.run(merge(value, CTX, None))
    return value.merge_results


class Repo:
    def __init__(self, rows):
        self.rows = rows

    async def hybrid_search(self, *args):
        return deepcopy(self.rows)


@pytest.mark.parametrize('reverse', [False, True])
def test_search_keeps_original_and_summary_but_one_duplicate_identity(reverse):
    source = row('source', 'identical evidence body')
    hint = row('hint', source.content, chunk_type='summary')
    rows = [source, hint, deepcopy(source)]
    if reverse:
        rows.reverse()
    value = state()
    asyncio.run(search(value, CTX, Repo(rows)))
    assert {r.id for r in value.search_results} == {'source', 'hint'}
    assert len(value.search_results) == 2
    legacy = state(mode='off')
    asyncio.run(search(legacy, CTX, Repo(rows)))
    assert len(legacy.search_results) == 1


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('domain', [{'kb': 'kb2'}, {'doc': 'doc2'}, {'version': 'v2'}])
def test_search_and_merge_do_not_deduplicate_equal_text_across_domains(domain, reverse):
    rows = [row('first', 'identical evidence body'),
            row('second', 'identical evidence body', **domain)]
    if reverse:
        rows.reverse()
    value = state()
    asyncio.run(search(value, CTX, Repo(rows)))
    assert {r.id for r in value.search_results} == {'first', 'second'}
    assert {r.id for r in merged(rows)} == {'first', 'second'}
    assert len(merged(rows, 'off')) == 1


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('fact_a,fact_b', [
    ('100', '200'), ('2025-01-01', '2026-01-01'),
    ('is allowed', 'is not allowed'), ('increases', 'decreases'),
])
def test_high_token_overlap_preserves_different_facts(fact_a, fact_b, reverse):
    prefix = 'the current published policy for this specific project states that the value '
    suffix = ' under the standard operating conditions described here'
    rows = [row('first', prefix + fact_a + suffix, score=.9),
            row('second', prefix + fact_b + suffix, score=.8)]
    if reverse:
        rows.reverse()
    actual = merged(rows)
    assert {r.id for r in actual} == {'first', 'second'}
    assert {r.content for r in actual} == {r.content for r in rows}
    assert {r.id: r.score for r in actual} == {'first': .9, 'second': .8}
    assert len(merged(rows, 'off')) == 1


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('domain', [{'kb': 'kb2'}, {'doc': 'doc2'}, {'version': 'v2'}])
def test_sequential_never_joins_different_domains(domain, reverse):
    rows = [row('first', 'first independent evidence', chunk_index=0),
            row('second', 'second unrelated statement', chunk_index=1, **domain)]
    if reverse:
        rows.reverse()
    actual = merged(rows)
    assert {r.id for r in actual} == {'first', 'second'}
    assert all(not r.sub_chunk_ids for r in actual)
    assert {r.content for r in actual} == {r.content for r in rows}
    if 'doc' not in domain:
        assert len(merged(rows, 'off')) == 1


def test_equal_foreign_text_cannot_disappear_before_scope_validation():
    rows = [row('allowed', 'identical evidence body'),
            row('foreign', 'identical evidence body', kb='outside')]
    with pytest.raises(RetrievalScopeError):
        merged(rows)


def test_same_domain_exact_dedup_and_sequential_merge_remain_enabled():
    assert len(merged([row('one', 'same body'), row('two', 'same body')])) == 1
    rows = [row('one', 'first evidence', chunk_index=0),
            row('two', 'second evidence', chunk_index=1)]
    actual = merged(rows)
    assert len(actual) == 1
    assert actual[0].content == 'first evidence\n\nsecond evidence'
    assert actual[0].sub_chunk_ids == ['two']


@pytest.mark.parametrize('version,chunk_version', [(None, None), ('v1', None),
                                                  (None, 'v1'), ('v1', 'v2')])
def test_incomplete_or_conflicting_versions_cannot_prove_source_equivalence(version, chunk_version):
    rows = [row('first', 'identical evidence body', chunk_index=0),
            row('second', 'identical evidence body', chunk_index=1)]
    for item in rows:
        item.metadata = {'source_version': version} if version else {}
        item.chunk_metadata = {'source_version': chunk_version} if chunk_version else {}
    assert {r.id for r in merged(rows)} == {'first', 'second'}
    rows[1].content = 'second independent evidence'
    assert {r.id for r in merged(rows)} == {'first', 'second'}


@pytest.mark.parametrize('kind', ['table_summary', 'image_caption'])
@pytest.mark.parametrize('reverse', [False, True])
def test_distinct_generated_origins_cannot_replace_identical_raw_text(kind, reverse):
    rows = [row('raw', 'same exact evidence'),
            row('generated', 'same exact evidence', chunk_type=kind)]
    if reverse:
        rows.reverse()
    value = state()
    asyncio.run(search(value, CTX, Repo(rows)))
    assert {r.id for r in value.search_results} == {'raw', 'generated'}
    assert {r.id for r in merged(rows)} == {'raw', 'generated'}
    assert len(merged(rows, 'off')) == 1


@pytest.mark.parametrize('domain', [{'kb': 'kb2'}, {'doc': 'doc2'}, {'version': 'v2'}])
@pytest.mark.parametrize('reverse', [False, True])
def test_duplicate_chunk_id_with_conflicting_immutable_domain_is_rejected(domain, reverse):
    rows = [row('same', 'first body'), row('same', 'second body', **domain)]
    if reverse:
        rows.reverse()
    with pytest.raises(RetrievalScopeError):
        asyncio.run(search(state(), CTX, Repo(rows)))
    with pytest.raises(RetrievalScopeError):
        merged(rows)
    assert len(merged(rows, 'off')) == 1


@pytest.mark.parametrize('body_a,body_b', [
    ('Concentration is 5 mM', 'Concentration is 5 mm'),
    ('Label    Value\nA        5', 'Label Value A 5'),
])
def test_bounded_body_equivalence_preserves_case_and_layout(body_a, body_b):
    rows = [row('first', body_a), row('second', body_b)]
    value = state()
    asyncio.run(search(value, CTX, Repo(rows)))
    assert {r.id for r in value.search_results} == {'first', 'second'}
    assert {r.id for r in merged(rows)} == {'first', 'second'}
    assert len(merged(rows, 'off')) == 1
