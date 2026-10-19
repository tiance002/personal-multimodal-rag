"""Bounded directed source discovery; graph claims never become answer text.

Nodes remain chunk/document scoped. Cross-document references resolve only to a
single literal, typed document-subject anchor; fuzzy names and ambiguous anchors
are not merged. This is a conservative reference resolver, not entity omniscience.
"""
from __future__ import annotations
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
import re
import unicodedata


class GraphRetrievalError(PermissionError):
    pass


@dataclass(frozen=True)
class GraphPath:
    node_ids: tuple[str, ...]
    edges: tuple
    sources: tuple
    reference_links: tuple[tuple[str, str], ...] = ()


def normalized(value):
    # Keep punctuation: device IDs and qualified names must not collapse.
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def names(node):
    return set(normalized(v) for v in (node.name, *node.aliases) if v.strip())


def mentioned(query, label):
    query, label = normalized(query), normalized(label)
    if len(label) < 2:
        return False
    return any((not label[0].isascii() or not label[0].isalnum() or match.start() == 0 or
                not query[match.start() - 1].isalnum()) and
               (not label[-1].isascii() or not label[-1].isalnum() or match.end() == len(query) or
                not query[match.end()].isalnum())
               for match in re.finditer(re.escape(label), query))


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
    found = []

    def visit(current, path_nodes, path_edges, path_sources, links):
        if len(path_edges) >= max_hops:
            return
        for edge, source in sorted(outgoing[current], key=lambda row: row[0].id)[:12]:
            if edge.target_id in path_nodes:
                continue
            target = reference(edge.target_id)
            extra = ((edge.target_id, target),) if target != edge.target_id else ()
            new = GraphPath(path_nodes + (edge.target_id,), path_edges + (edge,),
                            path_sources + (source,), links + extra)
            found.append(new)
            if target not in path_nodes:
                visit(target, new.node_ids + ((target,) if extra else ()), new.edges, new.sources, new.reference_links)
    for seed in seeds:
        visit(seed, (seed,), (), (), ())
    # Prefer actual multi-document two-hop paths over isolated edges. No BGE
    # relevance score is invented or copied to a graph source.
    found.sort(key=lambda p: (-len({s.document_id for s in p.sources}), -len(p.edges), tuple(e.id for e in p.edges)))
    output, seen = [], set()
    for path in found:
        key = tuple(e.id for e in path.edges)
        if key not in seen:
            output.append(path)
            seen.add(key)
        if len(output) >= max_paths:
            break
    return tuple(output)


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
                    row.metadata.get('source_version') != source.source_version or row.content != source.content):
                raise GraphRetrievalError('GRAPH_SOURCE_IDENTITY_CHANGED')
        needed = [row for row in rows.values() if not any(row.knowledge_id == old.knowledge_id and
                  row.content in old.content for old in current)]
        chars = sum(len(row.content) for row in needed)
        trace = {'edge_ids': [e.id for e in path.edges], 'source_chunk_ids': list(source_by_id),
                 'source_versions': [s.source_version for s in path.sources],
                 'reference_links': [list(v) for v in path.reference_links],
                 'hops': len(path.edges), 'verification': 'QUOTE_CHECKED_DERIVED_PATH_NOT_ANSWER_ENTAILMENT'}
        if added + chars > added_chars or used + chars > total_chars or accepted + len(needed) > max_chunks:
            trace['status'] = 'BUDGET_DROPPED'
            state.graph_trace.append(trace)
            continue
        for original in needed:
            row = deepcopy(original)
            row.match_type, row.score = 'graph', 0.0
            row.chunk_metadata = dict(row.chunk_metadata, graph_edge_ids=[e.id for e in path.edges],
                                      graph_verification=trace['verification'])
            current.append(row)
        added, used, accepted = added + chars, used + chars, accepted + len(needed)
        trace.update(status='SOURCE_RETAINED', added_chunks=len(needed), added_chars=chars)
        state.graph_trace.append(trace)
    state.merge_results = current
    # Headroom for real source filenames/markup. The existing final renderer
    # independently enforces the complete Context limit before generation.
    state.graph_context_char_limit = 16000 if accepted else 0
    return accepted
