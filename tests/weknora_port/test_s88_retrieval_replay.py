"""Synthetic-only checks for saved-rank replay; no private case text."""
import hashlib
from copy import deepcopy

import pytest

from scripts.s88_retrieval_replay import recover_shard, replay, search_anchor_candidate, source_metrics


def test_source_deduplication_precedes_cutoff_and_zero_grade_has_no_gain():
    candidates = [{'source_id': source} for source in ['zero'] * 12 + ['a', 'a', 'b']]
    values = source_metrics(candidates, {'zero': 0, 'a': 1, 'b': 2})
    assert values['hit_at_5'] == 1 and values['recall_at_5'] == 1
    assert values['first_positive_rank'] == 2 and values['reciprocal_rank_saved'] == .5
    assert 0 < values['ndcg_at_10'] < 1


def fixture():
    documents = {'doc': {'file_hash': 'hash', 'source_version': 'v1'}}
    item = {'rank': 1, 'chunk_id': 'chunk', 'document_id': 'doc', 'source_version': 'v1',
            'source_id': 'a', 'file_hash': 'hash'}
    audit = {'rows': [{'stages': {name: {'ordered_candidates': [item]} for name in
              ['search_results', 'rerank_results', 'merge_results', 'finalized_context']}}]}
    row = {'case_id': 'synthetic', 'dataset': 'synthetic', 'original_case_index': 1,
           'question': 'Synthetic question',
           'q0_sha256': hashlib.sha256(b'Synthetic question').hexdigest(), 'qrels': {'a': 1},
           'stages': {name: {'stage_completed_observed': True, 'candidate_count': 0, 'ordered_candidates': []}
                      for name in ['search_results', 'rerank_results', 'merge_results']},
           'context': {'captured': False, 'ordered_results': []}}
    return {'candidate_documents': documents, 'rows': [row]}, audit


def test_missing_context_excluded_but_completed_empty_search_scores_zero():
    ordered, audit = fixture()
    result = replay(ordered, audit)
    search = result['aggregate']['synthetic']['search_results']
    context = result['aggregate']['synthetic']['finalized_context']
    assert search['ranked_query_denominator'] == 1 and search['hit_at_5'] == 0
    assert context['ranked_query_denominator'] == 0 and context['hit_at_5'] is None
    assert context['missing_rank_queries'] == ['synthetic']


@pytest.mark.parametrize('kind', ['version', 'order', 'question', 'duplicate'])
def test_bad_identity_saved_order_and_question_never_silently_repaired(kind):
    ordered, audit = fixture()
    raw = ordered['rows'][0]
    item = deepcopy(audit['rows'][0]['stages']['search_results']['ordered_candidates'][0])
    raw['stages']['search_results']['ordered_candidates'] = [item]
    if kind == 'version':
        item['source_version'] = 'other'
    elif kind == 'order':
        item['rank'] = 2
    elif kind == 'question':
        raw['question'] += ' changed'
    else:
        ordered['rows'].append(deepcopy(raw))
    with pytest.raises(ValueError):
        replay(ordered, audit)


def test_opt_in_candidate_preserves_slot_budget_and_can_harm_a_control():
    search = [{'source_id': 'unjudged', 'chunk_id': 'search'}]
    reranked = [{'source_id': 'relevant', 'chunk_id': 'ranked'}]
    actual = search_anchor_candidate(search, reranked)
    assert actual == search and len(actual) == len(reranked)
    assert source_metrics(actual, {'relevant': 1})['hit_at_5'] == 0
    assert reranked == [{'source_id': 'relevant', 'chunk_id': 'ranked'}]
    assert search_anchor_candidate(search, []) == []


def test_default_replay_does_not_add_candidate_stage():
    ordered, audit = fixture()
    assert 'search_anchor_v1' not in replay(ordered, audit)['rows'][0]['stages']
    assert 'search_anchor_v1' in replay(ordered, audit, candidate=True)['rows'][0]['stages']


def recovery_fixture():
    ordered, audit = fixture()
    row = ordered['rows'][0]
    row.update(case_id='synthetic-1', inputs={'case_sha256': 'shard-hash'})
    ordered['candidate_documents']['doc']['knowledge_base_id'] = 'kb'
    # Another saved case establishes the authorized dataset KB.
    control = deepcopy(row)
    control['case_id'] = 'synthetic-control'
    control['stages']['search_results']['ordered_candidates'] = [
        deepcopy(audit['rows'][0]['stages']['search_results']['ordered_candidates'][0])]
    ordered['rows'].append(control)
    item = {'id': 'chunk', 'content': 'original fact', 'score': .5, 'knowledge_id': 'doc',
            'knowledge_base_id': 'kb', 'metadata': {'sha256': 'hash', 'source_version': 'v1'},
            'chunk_metadata': {'source_version': 'v1'}}
    state = {'search_results': [item], 'rerank_results': [item], 'merge_results': [],
             'evidence_trace': [{'stage': 'rerank_raw', 'input_ids': ['chunk'],
                                 'response': [{'index': 0, 'score': .7}]}]}
    shard = {'case_id': 'synthetic-1', 'qid': '1', 'dataset': 'synthetic',
             'question': row['question'], 'qrels': row['qrels'],
             'scope': {'knowledge_base_id': 'kb', 'document_scope': []},
             'stage_snapshots': [{'stage': name, 'status': 'completed', 'fields': {'candidate_count': 1}}
                                 for name in ['retrieval', 'rerank', 'merge']] +
                                [{'stage': 'generation', 'profile': 'formal', 'state': state}],
             'actual_provider_requests': [{'capability': 'rerank', 'body': {'documents': ['original fact']}}]}
    return ordered, audit, shard


def test_recovery_does_not_turn_missing_child_state_into_empty_rankings():
    ordered, audit, shard = recovery_fixture()
    recovered, bindings, changes = recover_shard(ordered, audit, shard,
                                                {('synthetic', 'hash'): 'a'}, shard_sha256='shard-hash')
    result = replay(recovered, bindings)
    assert result['rows'][0]['stages']['search_results']['metrics']['hit_at_5'] == 1
    assert result['rows'][0]['stages']['rerank_results']['metrics']['hit_at_5'] == 1
    assert result['rows'][0]['stages']['merge_results']['metrics'] is None
    assert changes[-1]['decision'] == 'UNAVAILABLE_COUNT_WITHOUT_PRESERVED_LIST'
    assert ordered['rows'][0]['stages']['search_results']['ordered_candidates'] == []


def test_proven_post_projection_empty_is_distinct_from_unpreserved_merge_event():
    ordered, audit, shard = recovery_fixture()
    shard['stage_snapshots'].append({'stage': 'source_projection', 'status': 'completed',
                                    'fields': {'candidate_count': 0}})
    recovered, bindings, changes = recover_shard(ordered, audit, shard,
                                                {('synthetic', 'hash'): 'a'}, shard_sha256='shard-hash')
    result = replay(recovered, bindings)
    assert result['rows'][0]['stages']['merge_results']['metrics']['hit_at_5'] == 0
    assert changes[-1]['decision'] == 'CONFIRMED_FINAL_POST_PROJECTION_EMPTY'
    assert changes[-1]['pre_projection_merge_ranking_available'] is False


@pytest.mark.parametrize('kind', ['case_id', 'qid', 'question', 'scope', 'hash', 'mapping'])
def test_recovery_rejects_wrong_case_inputs_scope_hash_and_index_mapping(kind):
    ordered, audit, shard = recovery_fixture()
    digest = 'shard-hash'
    if kind in ('case_id', 'qid', 'question'):
        shard[kind] = 'wrong'
    elif kind == 'scope':
        shard['scope']['knowledge_base_id'] = 'another-kb'
    elif kind == 'hash':
        digest = 'wrong-hash'
    else:
        shard['stage_snapshots'][-1]['state']['evidence_trace'][0]['input_ids'] = ['wrong-id']
    with pytest.raises(ValueError):
        recover_shard(ordered, audit, shard, {('synthetic', 'hash'): 'a'}, shard_sha256=digest)
