"""Bounded directed source discovery; graph claims never become answer text.

Nodes remain chunk/document scoped. Cross-document references resolve only to a
single literal, typed document-subject anchor; fuzzy names and ambiguous anchors
are not merged. This is a conservative reference resolver, not entity omniscience.
"""
from __future__ import annotations
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
import unicodedata

from .script_boundaries import exact_surface_occurrences


class GraphRetrievalError(PermissionError):
    pass


@dataclass(frozen=True)
class GraphPath:
    node_ids: tuple[str, ...]
    edges: tuple
    sources: tuple
    reference_links: tuple[tuple[str, str], ...] = ()
    # Local token overlap for bounded selection, never a model confidence score
    # or a verdict that the full path entails an answer.
    query_relevance: tuple[float, float] = (0.0, 0.0)


def normalized(value):
    # Keep punctuation: device IDs and qualified names must not collapse.
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def names(node):
    return set(normalized(v) for v in (node.name, *node.aliases) if v.strip())


def mentioned(query, label):
    query, label = normalized(query), normalized(label)
    if len(label) < 2:
        return False
    return bool(exact_surface_occurrences(query, label))


def _terms(value):
    """Reuse the ordinary query tokenizer/dictionary, without a new NLP stack."""
    from ..retrieval.query import STOPWORDS, default_segmenter, is_han, tokenize
    value = normalized(value)
    segmenter = default_segmenter() if any(map(is_han, value)) else None
    return {token for token in tokenize(value, segmenter) if token not in STOPWORDS}


def find_paths(query, batches, context, *, tenant_id, max_hops=2, max_paths=3):
    if type(tenant_id) is not int or tenant_id <= 0:
        raise GraphRetrievalError('GRAPH_TENANT_IDENTITY_REQUIRED')
    if type(max_hops) is not int or not 1 <= max_hops <= 2 or not 1 <= max_paths <= 3:
        raise ValueError('GRAPH_PATH_BOUND_INVALID')
    nodes, sources, outgoing, anchors = {}, {}, defaultdict(list), defaultdict(set)
    for batch in batches:
        from .extraction import validate_batch_proofs
        validate_batch_proofs(batch)
        source = batch.source
        if (source.tenant_id != tenant_id or source.owner_id != context.scope.owner_id or
                source.knowledge_base_id not in context.scope.knowledge_base_ids or
                context.scope.document_ids and source.document_id not in context.scope.document_ids):
            raise GraphRetrievalError('GRAPH_SCOPE_MISMATCH')
        for node in batch.nodes:
            if node.id in nodes:
                raise GraphRetrievalError('GRAPH_NODE_IDENTITY_COLLISION')
            nodes[node.id] = node
            sources[node.id] = source
            if node.is_anchor:
                for name in names(node):
                    anchors[(source.knowledge_base_id, normalized(node.entity_type), name)].add(node.id)
        for edge in batch.edges:
            if (edge.source_id not in {n.id for n in batch.nodes} or
                    edge.target_id not in {n.id for n in batch.nodes} or
                    source.content[edge.quote_start:edge.quote_end] != edge.quote or
                    edge.verification != 'QUOTE_AND_ORDER_CHECKED_NOT_SEMANTIC_JUDGE'):
                raise GraphRetrievalError('GRAPH_EDGE_SOURCE_MISMATCH')
            outgoing[edge.source_id].append((edge, source))

    def reference(node_id):
        node, source = nodes[node_id], sources[node_id]
        candidates = set().union(*(anchors[(source.knowledge_base_id, normalized(node.entity_type), name)]
                                   for name in names(node)))
        # Ambiguity is not resolved by insertion order or lexical similarity.
        if len(candidates) != 1:
            return node_id
        candidate = next(iter(candidates))
        other = nodes[candidate]
        left, right = normalized(node.identity_qualifier), normalized(other.identity_qualifier)
        if left and right and left != right:
            return node_id
        return candidate

    matches = [node for node in nodes.values() if any(mentioned(query, name) for name in names(node))]
    # Ambiguous same-label anchors are not seeds, including explicit homonyms.
    matches = [n for n in matches if all(len(anchors[(sources[n.id].knowledge_base_id, normalized(n.entity_type), name)]) <= 1
                                       for name in names(n))]
    matches.sort(key=lambda n: (-max(map(len, names(n))), not n.is_anchor, n.id))
    seeds, seen_seed_names = [], set()
    for node in matches:
        target = reference(node.id)
        key = (sources[node.id].knowledge_base_id, node.entity_type, normalized(node.name))
        if key not in seen_seed_names:
            seeds.append(target)
            seen_seed_names.add(key)
        if len(seeds) == 3:
            break
    # The seed name occurs in every outgoing quote and cannot distinguish its
    # relations. Rank the remaining literal query terms against predicate first
    # and source quote second, before either bounded cut can discard a branch.
    seed_terms = set().union(*(_terms(name) for seed in seeds for name in names(nodes[seed])))
    query_terms = _terms(query) - seed_terms
    edge_scores = {}

    def edge_score(edge):
        from ..retrieval.query import jaccard
        if edge.id not in edge_scores:
            edge_scores[edge.id] = (
                jaccard(query_terms, _terms(edge.predicate) - seed_terms),
                jaccard(query_terms, _terms(edge.quote) - seed_terms))
        return edge_scores[edge.id]

    def path_score(edges):
        scores = [edge_score(edge) for edge in edges]
        return (max(score[0] for score in scores), sum(score[1] for score in scores))

    found = []

    def visit(current, path_nodes, path_edges, path_sources, links):
        if len(path_edges) >= max_hops:
            return
        for edge, source in sorted(outgoing[current],
                key=lambda row: (*(-v for v in edge_score(row[0])), row[0].id))[:12]:
            if edge.target_id in path_nodes:
                continue
            target = reference(edge.target_id)
            extra = ((edge.target_id, target),) if target != edge.target_id else ()
            new = GraphPath(path_nodes + (edge.target_id,), path_edges + (edge,),
                            path_sources + (source,), links + extra,
                            path_score(path_edges + (edge,)))
            found.append(new)
            if target not in path_nodes:
                visit(target, new.node_ids + ((target,) if extra else ()), new.edges, new.sources, new.reference_links)
    for seed in seeds:
        visit(seed, (seed,), (), (), ())
    # Literal relation relevance precedes path length; source diversity and
    # stable IDs only settle equal textual scores. Synonym/temporal entailment
    # is not inferred by this local selector. No BGE score is invented.
    found.sort(key=lambda p: (*(-v for v in p.query_relevance),
        -len({s.document_id for s in p.sources}), -len(p.edges), tuple(e.id for e in p.edges)))
    output, seen = [], set()
    for path in found:
        key = tuple(e.id for e in path.edges)
        if key not in seen:
            output.append(path)
            seen.add(key)
        if len(output) >= max_paths:
            break
    return tuple(output)


def _coverage(source_row, retained):
    """A literal body is covered only by a mapped raw source of this version.

    sub_chunk_ids are the normal Merge/neighbor ancestry mapping, not proof on
    their own: the complete re-read original must also survive in its body.
    A generated representation can never acquire original provenance by copying
    the text, the source ID or an ancestry list.
    """
    version = source_row.metadata.get('source_version')
    if (retained.chunk_type not in ('text', 'image_ocr', 'parent_text') or
            not getattr(retained, 'is_enabled', True) or not version or
            retained.knowledge_base_id != source_row.knowledge_base_id or
            retained.knowledge_id != source_row.knowledge_id or
            retained.metadata.get('source_version') != version or
            retained.chunk_metadata.get('source_version', version) != version or
            not source_row.content or source_row.content not in retained.content):
        return None
    if retained.id == source_row.id:
        basis = 'CURRENT_RAW_SOURCE_ID'
    elif source_row.id in retained.sub_chunk_ids:
        basis = 'MERGED_RAW_SUBCHUNK_AND_COMPLETE_BODY'
    elif (retained.chunk_type == 'parent_text' and source_row.parent_chunk_id and
          source_row.parent_chunk_id == retained.id):
        basis = 'CURRENT_RAW_PARENT_AND_COMPLETE_BODY'
    else:
        return None
    return {'source_chunk_id': source_row.id, 'retained_chunk_id': retained.id,
            'source_version': version, 'basis': basis}


async def fuse_paths(state, context, repository, paths, *, added_chars=4096, total_chars=14000, max_chunks=4):
    """Append only complete verified raw-source paths after ordinary TopK.

    Ordinary candidates/thresholds/MMR are untouched. All source chunks in a
    path are admitted atomically; no partial quote, guessed parent or graph
    description can masquerade as source evidence.
    """
    from ..retrieval.search import validate_results
    current = list(state.merge_results)
    used, added, accepted = sum(len(r.content) for r in current), 0, 0
    state.graph_trace = []
    for path in paths:
        if not 1 <= len(path.edges) <= 2 or len(path.edges) != len(path.sources):
            raise GraphRetrievalError('GRAPH_PATH_PROOF_MISMATCH')
        for edge, source in zip(path.edges, path.sources, strict=True):
            if (source.owner_id != context.scope.owner_id or
                    source.knowledge_base_id not in context.scope.knowledge_base_ids or
                    context.scope.document_ids and source.document_id not in context.scope.document_ids or
                    not 0 <= edge.quote_start < edge.quote_end <= len(source.content) or
                    source.content[edge.quote_start:edge.quote_end] != edge.quote or
                    edge.predicate not in edge.quote or
                    edge.verification != 'QUOTE_AND_ORDER_CHECKED_NOT_SEMANTIC_JUDGE'):
                raise GraphRetrievalError('GRAPH_PATH_SOURCE_PROOF_MISMATCH')
        source_by_id = {s.chunk_id: s for s in path.sources}
        if any(source_by_id[s.chunk_id] != s for s in path.sources):
            raise GraphRetrievalError('GRAPH_PATH_SOURCE_IDENTITY_COLLISION')
        rows = await repository.get_graph_chunks(tuple(source_by_id), context)
        validate_results(rows.values(), state.targets, context)
        if set(rows) != set(source_by_id):
            raise GraphRetrievalError('GRAPH_SOURCE_NO_LONGER_CURRENT')
        for identifier, source in source_by_id.items():
            row = rows[identifier]
            if (row.knowledge_id != source.document_id or row.knowledge_base_id != source.knowledge_base_id or
                    row.metadata.get('source_version') != source.source_version or row.content != source.content or
                    row.chunk_type not in ('text', 'image_ocr') or not getattr(row, 'is_enabled', True) or
                    row.chunk_metadata.get('source_version', source.source_version) != source.source_version):
                raise GraphRetrievalError('GRAPH_SOURCE_IDENTITY_CHANGED')
        needed, covered = [], []
        for row in rows.values():
            proof = next((proof for old in current if (proof := _coverage(row, old))), None)
            if proof:
                covered.append(proof)
            else:
                needed.append(row)
        chars = sum(len(row.content) for row in needed)
        trace = {'edge_ids': [e.id for e in path.edges], 'source_chunk_ids': list(source_by_id),
                 'source_versions': [s.source_version for s in path.sources],
                 'reference_links': [list(v) for v in path.reference_links],
                 'hops': len(path.edges), 'verification': 'QUOTE_CHECKED_DERIVED_PATH_NOT_ANSWER_ENTAILMENT',
                 'selection': 'QUERY_TOKEN_JACCARD_NOT_SEMANTIC_ENTAILMENT',
                 'query_relevance': list(path.query_relevance), 'covered_sources': covered,
                 'added_source_chunk_ids': [], 'budget_dropped_source_chunk_ids': []}
        if added + chars > added_chars or used + chars > total_chars or accepted + len(needed) > max_chunks:
            trace.update(status='BUDGET_DROPPED',
                         budget_dropped_source_chunk_ids=[row.id for row in needed])
            state.graph_trace.append(trace)
            continue
        for original in needed:
            row = deepcopy(original)
            row.match_type, row.score = 'graph', 0.0
            row.chunk_metadata = dict(row.chunk_metadata, graph_edge_ids=[e.id for e in path.edges],
                                      graph_verification=trace['verification'])
            current.append(row)
        added, used, accepted = added + chars, used + chars, accepted + len(needed)
        trace.update(status='SOURCE_RETAINED', added_chunks=len(needed), added_chars=chars,
                     added_source_chunk_ids=[row.id for row in needed])
        state.graph_trace.append(trace)
    state.merge_results = current
    # Headroom for real source filenames/markup. The existing final renderer
    # independently enforces the complete Context limit before generation.
    state.graph_context_char_limit = 16000 if accepted else 0
    return accepted
