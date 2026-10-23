"""Replay saved source rankings without retrieval, model calls or database access.

The ordered extract supplies rankings/qrels; the earlier identity audit supplies
document-to-source bindings only. Its stored scores are never used as metrics.
The optional search-anchor experiment replaces the last reranked chunk within
the *same saved chunk-slot budget*. It is not enabled in the application.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tarfile

from eval_center.metrics import ranking_metrics

STAGES = ('search_results', 'rerank_results', 'merge_results', 'finalized_context')


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fold_sources(candidates: list[dict]) -> list[str]:
    """Keep first occurrence in observed order; deduplicate before top-k."""
    return list(dict.fromkeys(row['source_id'] for row in candidates))


def source_metrics(candidates: list[dict], qrels: dict[str, int]) -> dict:
    sources = fold_sources(candidates)
    positives = {source for source, grade in qrels.items() if grade > 0}
    if not positives:
        raise ValueError('POSITIVE_QREL_REQUIRED')
    first = next((rank for rank, source in enumerate(sources, 1) if source in positives), None)
    result = {'positive_source_denominator': len(positives), 'first_positive_rank': first,
              'reciprocal_rank_saved': 1 / first if first else 0.0}
    for k in (5, 10):
        metrics = ranking_metrics(sources, qrels, k=k)
        result.update({f'hit_at_{k}': metrics['hit_at_k'],
                       f'recall_at_{k}': metrics['recall_at_k'],
                       f'relevant_retrieved_at_{k}': len(set(sources[:k]) & positives)})
        if k == 10:
            result.update(reciprocal_rank_at_10=metrics['mrr_at_k'], ndcg_at_10=metrics['ndcg_at_k'])
    return result


def aggregate(rows: list[dict], stage: str) -> dict:
    values = [row['stages'][stage]['metrics'] for row in rows
              if row['stages'][stage]['ranking_available']]
    count = len(values)
    total = sum(value['positive_source_denominator'] for value in values)
    result = {'case_scope': len(rows), 'ranked_query_denominator': count,
              'missing_rank_queries': [row['case_id'] for row in rows
                                       if not row['stages'][stage]['ranking_available']],
              'positive_source_judgments_in_ranked_queries': total}
    for k in (5, 10):
        hits = sum(value[f'hit_at_{k}'] for value in values)
        relevant = sum(value[f'relevant_retrieved_at_{k}'] for value in values)
        result.update({f'hit_count_at_{k}': hits, f'hit_at_{k}': hits / count if count else None,
                       f'recall_at_{k}': sum(value[f'recall_at_{k}'] for value in values) / count if count else None,
                       f'relevant_retrieved_at_{k}': relevant,
                       f'micro_recall_at_{k}': relevant / total if total else None})
    for metric, key in [('reciprocal_rank_saved', 'mrr_saved'),
                        ('reciprocal_rank_at_10', 'mrr_at_10'), ('ndcg_at_10', 'ndcg_at_10')]:
        result[key] = sum(value[metric] for value in values) / count if count else None
    return result


def search_anchor_candidate(search: list[dict], reranked: list[dict]) -> list[dict]:
    """Opt-in experiment; no qrels, question, labels or answer may enter it.

    Preserve a Search-leading source if Rerank removed it, replacing the final
    saved rerank slot. This can discard a useful source and must be evaluated
    on all controls. It does not claim that source supports the answer.
    """
    result = deepcopy(reranked)
    if search and result and search[0]['source_id'] not in {row['source_id'] for row in result}:
        result[-1] = deepcopy(search[0])
    return result


def identity_bindings(identity_audit: dict, documents: dict) -> dict[str, str]:
    """Reuse source identities but independently check hash/version consistency."""
    bindings = {}
    for row in identity_audit['rows']:
        for stage in STAGES:
            for candidate in row['stages'][stage]['ordered_candidates']:
                document_id = candidate['document_id']
                document = documents[document_id]
                if (candidate['file_hash'] != document['file_hash'] or
                        candidate['source_version'] != document['source_version']):
                    raise ValueError('SOURCE_IDENTITY_MISMATCH')
                source = candidate['source_id']
                if not source or bindings.setdefault(document_id, source) != source:
                    raise ValueError('SOURCE_IDENTITY_CONFLICT')
    return bindings


def recover_shard(ordered: dict, identity_audit: dict, shard: dict, source_hashes: dict,
                  *, shard_sha256: str) -> tuple[dict, dict, list[dict]]:
    """Versioned recovery of a root observer which missed strategy child state.

    Never infer stage ranks from counts or use another case's snapshot. Search
    and Rerank require preserved lists plus a matching raw input-ID trace. A
    Merge count without the actual list is unavailable, rather than empty.
    """
    ordered, identity_audit = deepcopy(ordered), deepcopy(identity_audit)
    matches = [row for row in ordered['rows'] if row['case_id'] == shard.get('case_id')]
    if len(matches) != 1:
        raise ValueError('RECOVERY_CASE_ID_MISMATCH')
    row = matches[0]
    if (row['dataset'] != shard.get('dataset') or
            str(shard.get('qid')) != row['case_id'].removeprefix(row['dataset'] + '-') or
            row['question'] != shard.get('question') or row['qrels'] != shard.get('qrels') or
            row['inputs']['case_sha256'] != shard_sha256):
        raise ValueError('RECOVERY_INPUT_IDENTITY_MISMATCH')
    expected_kbs = {ordered['candidate_documents'][item['document_id']]['knowledge_base_id']
                    for other in ordered['rows'] if other['dataset'] == row['dataset']
                    for saved in other['stages'].values() for item in saved['ordered_candidates']}
    kb = shard.get('scope', {}).get('knowledge_base_id')
    if expected_kbs != {kb} or shard.get('scope', {}).get('document_scope') != []:
        raise ValueError('RECOVERY_SCOPE_MISMATCH')
    snapshots = [snapshot['state'] for snapshot in shard.get('stage_snapshots', [])
                 if snapshot.get('profile') and snapshot.get('stage') == 'generation'
                 and (snapshot.get('state') or {}).get('search_results')]
    if not snapshots:
        raise ValueError('RECOVERY_SUBSTANTIVE_SNAPSHOT_MISSING')
    state = snapshots[-1]
    traces = [trace for trace in state.get('evidence_trace', []) if trace.get('stage') == 'rerank_raw']
    if len(traces) != 1 or traces[0]['input_ids'] != [item['id'] for item in state['search_results']]:
        raise ValueError('RECOVERY_RERANK_MAPPING_UNVERIFIED')
    requests = [request for request in shard['actual_provider_requests'] if request['capability'] == 'rerank']
    if len(requests) != 1 or len(requests[0]['body']['documents']) != len(state['search_results']):
        raise ValueError('RECOVERY_BATCH_LAYOUT_UNVERIFIED')
    additions, changes = [], []
    for stage, event in [('search_results', 'retrieval'), ('rerank_results', 'rerank'), ('merge_results', 'merge')]:
        saved = row['stages'][stage]
        observed_counts = {snapshot.get('fields', {}).get('candidate_count')
                           for snapshot in shard['stage_snapshots']
                           if snapshot.get('stage') == event and snapshot.get('status') == 'completed'}
        observed_counts.discard(None)
        if not observed_counts or len(observed_counts) != 1:
            raise ValueError('RECOVERY_STAGE_COUNT_UNVERIFIED')
        expected_count = next(iter(observed_counts))
        if saved['candidate_count'] == expected_count:
            continue
        preserved = state.get(stage, [])
        if len(preserved) != expected_count:
            projected_counts = {snapshot.get('fields', {}).get('candidate_count')
                                for snapshot in shard['stage_snapshots']
                                if snapshot.get('stage') == 'source_projection'
                                and snapshot.get('status') == 'completed'}
            if stage == 'merge_results' and projected_counts == {0} and not preserved:
                # The historical field is the final selected list *after*
                # projection. Its proven empty result remains a valid zero.
                # The earlier Merge-event list is a distinct unavailable fact.
                saved.update(stage_completed_observed=True, candidate_count=0, ordered_candidates=[])
                changes.append({'case_id': row['case_id'], 'stage': stage,
                                'decision': 'CONFIRMED_FINAL_POST_PROJECTION_EMPTY',
                                'pre_projection_merge_count': expected_count,
                                'pre_projection_merge_ranking_available': False})
                continue
            # Later source projection may have removed a Merge candidate. Its
            # original Merge list cannot be reconstructed from Rerank alone.
            saved.update(stage_completed_observed=False, candidate_count=0, ordered_candidates=[])
            changes.append({'case_id': row['case_id'], 'stage': stage,
                            'decision': 'UNAVAILABLE_COUNT_WITHOUT_PRESERVED_LIST',
                            'observed_count': expected_count})
            continue
        candidates = []
        for rank, item in enumerate(preserved, 1):
            metadata = item.get('metadata') or {}
            file_hash = metadata.get('sha256')
            version = metadata.get('source_version')
            source = source_hashes.get((row['dataset'], file_hash))
            chunk_version = item.get('chunk_metadata', {}).get('source_version')
            if (not source or not version or item.get('knowledge_base_id') != kb or
                    chunk_version is not None and chunk_version != version or
                    item.get('chunk_type') in ('text', 'parent_text', 'image_ocr', 'image_caption')
                    and chunk_version != version):
                raise ValueError('RECOVERY_SOURCE_BINDING_MISSING')
            document_id = item['knowledge_id']
            document = {'id': document_id, 'knowledge_base_id': kb, 'file_hash': file_hash,
                        'source_version': version, 'file_name': item.get('knowledge_filename', '')}
            old = ordered['candidate_documents'].get(document_id)
            if old and any(old.get(key) != document[key] for key in
                           ('knowledge_base_id', 'file_hash', 'source_version')):
                raise ValueError('RECOVERY_DOCUMENT_IDENTITY_CONFLICT')
            ordered['candidate_documents'].setdefault(document_id, document)
            candidate = {'rank': rank, 'chunk_id': item['id'], 'document_id': document_id,
                         'source_version': version, 'score': item['score']}
            candidates.append(candidate)
            additions.append({**candidate, 'source_id': source, 'file_hash': file_hash})
        saved.update(stage_completed_observed=True, candidate_count=len(candidates), ordered_candidates=candidates)
        changes.append({'case_id': row['case_id'], 'stage': stage,
                        'decision': 'RECOVERED_FORMAL_STRATEGY_SNAPSHOT', 'candidate_count': len(candidates)})
    identity_audit['rows'].append({'stages': {stage: {'ordered_candidates': additions if stage == STAGES[0] else []}
                                             for stage in STAGES}})
    ordered['schema'] = 'ecs118-ordered-retrieval-extract/v2-recovered'
    return ordered, identity_audit, changes


def load_source_hashes(source_map: Path, ingestion_tar: Path) -> dict:
    """Read existing source aliases without extracting or mutating the archive."""
    bindings = {}
    source = json.loads(source_map.read_text(encoding='utf-8-sig'))
    pairs = [(dataset, value['sha256'], str(source_id))
             for dataset, group in source.items() for source_id, value in group['units'].items()]
    with tarfile.open(ingestion_tar, 'r:gz') as archive:
        plan = json.loads(archive.extractfile('config/ingestion-plan.json').read())
    pairs.extend((item['dataset'], item['source_sha256'], str(item['source_document_id']))
                 for item in plan['sources'])
    for dataset, digest, source_id in pairs:
        if bindings.setdefault((dataset, digest), source_id) != source_id:
            raise ValueError('SOURCE_HASH_ALIAS_CONFLICT')
    return bindings


def replay(ordered: dict, identity_audit: dict, *, candidate: bool = False) -> dict:
    documents = ordered['candidate_documents']
    bindings = identity_bindings(identity_audit, documents)
    rows = []
    seen_cases = set()
    for raw in ordered['rows']:
        case_id = raw['case_id']
        if case_id in seen_cases:
            raise ValueError('DUPLICATE_CASE_ID')
        seen_cases.add(case_id)
        if hashlib.sha256(raw['question'].encode('utf-8')).hexdigest() != raw['q0_sha256']:
            raise ValueError('QUESTION_HASH_MISMATCH')
        qrels = raw['qrels']
        row = {'case_id': case_id, 'dataset': raw['dataset'],
               'http_index_one_based': raw['original_case_index'],
               'q0_sha256': raw['q0_sha256'], 'qrels_exact_grades': qrels, 'stages': {}}
        for stage in STAGES:
            context = stage == 'finalized_context'
            saved = raw['context'] if context else raw['stages'][stage]
            available = saved['captured'] if context else saved['stage_completed_observed']
            if type(available) is not bool:
                raise ValueError('STAGE_AVAILABILITY_INVALID')
            candidates = deepcopy(saved['ordered_results'] if context else saved['ordered_candidates'])
            if not available and candidates:
                raise ValueError('UNOBSERVED_STAGE_HAS_RANKING')
            for rank, item in enumerate(candidates, 1):
                if item['rank'] != rank:
                    raise ValueError('SAVED_ORDER_RANK_MISMATCH')
                document = documents[item['document_id']]
                if item['source_version'] != document['source_version']:
                    raise ValueError('CANDIDATE_VERSION_MISMATCH')
                item['source_id'] = bindings[item['document_id']]
                if context and not (item['candidate_found'] is True and
                                    item['exact_candidate_in_user_content'] is True):
                    raise ValueError('CONTEXT_TEXT_NOT_CONFIRMED')
            if context:
                positions = [item['user_content_position'] for item in candidates]
                if positions != sorted(positions):
                    raise ValueError('CONTEXT_ORDER_MISMATCH')
            row['stages'][stage] = {'ranking_available': available, 'ordered_candidates': candidates,
                                    'metrics': source_metrics(candidates, qrels) if available else None}
        if candidate:
            search = row['stages']['search_results']
            reranked = row['stages']['rerank_results']
            available = search['ranking_available'] and reranked['ranking_available']
            values = search_anchor_candidate(search['ordered_candidates'], reranked['ordered_candidates'])
            row['stages']['search_anchor_v1'] = {
                'ranking_available': available, 'ordered_candidates': values,
                'metrics': source_metrics(values, qrels) if available else None}
        rows.append(row)
    stages = (*STAGES, 'search_anchor_v1') if candidate else STAGES
    datasets = sorted({row['dataset'] for row in rows})
    result = {'schema': 's88-saved-source-ranking-replay/v1', 'case_count': len(rows),
              'status': 'OFFLINE_SAVED_RANKS_RECOMPUTED',
              'aggregate': {dataset: {stage: aggregate([row for row in rows if row['dataset'] == dataset], stage)
                                      for stage in stages} for dataset in datasets},
              'rows': rows, 'provider_calls': 0, 'database_calls': 0, 'runtime_default_changes': False,
              'limits': ['qrel source relevance is not QA correctness or necessary-fact sufficiency',
                         'missing Context excluded; completed empty ranking scored zero',
                         'merge_results is saved final selection after projection, not necessarily the preprojection Merge event',
                         'candidate ranking is not actual Merge, packed Context or Provider delivery']}
    if candidate:
        changes = defaultdict(list)
        for row in rows:
            baseline = row['stages']['rerank_results']['metrics']
            experimental = row['stages']['search_anchor_v1']['metrics']
            if baseline is None or experimental is None:
                changes['unavailable'].append(row['case_id'])
                continue
            better = any(experimental[key] > baseline[key] for key in
                         ('recall_at_5', 'recall_at_10', 'reciprocal_rank_saved', 'ndcg_at_10'))
            worse = any(experimental[key] < baseline[key] for key in
                        ('recall_at_5', 'recall_at_10', 'reciprocal_rank_saved', 'ndcg_at_10'))
            changes['mixed' if better and worse else 'improved' if better else 'regressed' if worse else 'unchanged'].append(row['case_id'])
        result['candidate_comparison'] = {'policy': 'search_anchor_v1', 'same_saved_chunk_slot_budget': True,
                                          'by_case': dict(changes),
                                          'counts': dict(Counter({key: len(value) for key, value in changes.items()})),
                                          'promotion': 'NOT_PROMOTED_OFFLINE_RELEVANCE_ONLY'}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ordered', type=Path, required=True)
    parser.add_argument('--identity-audit', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--candidate', choices=['off', 'search_anchor_v1'], default='off')
    parser.add_argument('--recover-shard', type=Path, action='append', default=[])
    parser.add_argument('--source-map', type=Path)
    parser.add_argument('--ingestion-tar', type=Path)
    args = parser.parse_args()
    inputs = [args.ordered, args.identity_audit, *args.recover_shard,
              *([args.source_map] if args.source_map else []),
              *([args.ingestion_tar] if args.ingestion_tar else [])]
    if args.output.resolve() in {path.resolve() for path in inputs}:
        raise ValueError('OUTPUT_MUST_NOT_OVERWRITE_INPUT')
    ordered = json.loads(args.ordered.read_text(encoding='utf-8-sig'))
    audit = json.loads(args.identity_audit.read_text(encoding='utf-8-sig'))
    if audit['provenance']['ordered_extract_sha256'] != sha256(args.ordered):
        raise ValueError('ORDERED_EXTRACT_HASH_MISMATCH')
    legacy = replay(ordered, audit, candidate=args.candidate != 'off')
    recovery = []
    if args.recover_shard:
        if not args.source_map or not args.ingestion_tar:
            raise ValueError('RECOVERY_SOURCE_BINDINGS_REQUIRED')
        if sha256(args.source_map) != ordered['source_map_sha256']:
            raise ValueError('RECOVERY_SOURCE_MAP_HASH_MISMATCH')
        if sha256(args.ingestion_tar) != audit['provenance']['full_corpus_tar_sha256']:
            raise ValueError('RECOVERY_INGESTION_TAR_HASH_MISMATCH')
        sources = load_source_hashes(args.source_map, args.ingestion_tar)
        for path in args.recover_shard:
            ordered, audit, changes = recover_shard(ordered, audit,
                json.loads(path.read_text(encoding='utf-8-sig')), sources, shard_sha256=sha256(path))
            recovery.extend(changes)
    result = replay(ordered, audit, candidate=args.candidate != 'off')
    if recovery:
        result.update(legacy_aggregate=legacy['aggregate'], recovery=recovery,
                      legacy_candidate_comparison=legacy.get('candidate_comparison'),
                      recovered_shards={path.name: sha256(path) for path in args.recover_shard})
    result['provenance'] = {'ordered_sha256': sha256(args.ordered),
                            'identity_audit_sha256': sha256(args.identity_audit),
                            'scoring_sha256': ordered['scoring_sha256'],
                            'configuration_sha256': ordered['configuration_sha256']}
    # Compare independently recomputed values, never copy stored metric values.
    result['legacy_reference_aggregate_exact_match'] = all(
        legacy['aggregate'][dataset][stage] == value
        for dataset, stages in audit['aggregate'].items() for stage, value in stages.items())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({key: result[key] for key in ('status', 'case_count', 'legacy_reference_aggregate_exact_match',
                                                   'provider_calls', 'database_calls')}))
    return 0 if result['legacy_reference_aggregate_exact_match'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
