"""Fixed WeKnora 3e8b0bfc merge semantics, with immutable citation units.

The merged body is context only. Never manufacture a ChunkRecord containing a
parent/neighbor body under a child's ID. No history injection or model routing.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib

from backend.app.domain.models import ChunkRecord, RankedHit
from backend.app.domain.scope import Scope


MIN_OVERLAP = 12  # fixed searchutil/chunkmerge.go
SEARCH_SPAN = 400
SHORT_CONTEXT = 350  # fixed chat_pipeline/merge_expand.go
NEIGHBOR_MAX_CHARS = 850


@dataclass(frozen=True)
class ContextPassage:
    key: tuple[str, ...]
    content: str
    unit_ids: tuple[str, ...]
    parent_ids: tuple[str, ...] = ()
    neighbor_ids: tuple[str, ...] = ()


def contains_body(container: str, body: str) -> bool:
    return bool(body) and (container == body or len(body) >= MIN_OVERLAP and body in container)


def join_body(left: str, right: str) -> str:
    if not left or contains_body(right, left):
        return right
    if not right or contains_body(left, right):
        return left
    for n in range(min(len(left), len(right), SEARCH_SPAN), MIN_OVERLAP - 1, -1):
        if left[-n:] == right[:n]:
            return left + right[n:]
    return left + "\n\n" + right


def source_key(chunk: ChunkRecord) -> tuple[str, ...]:
    return (chunk.knowledge_base_id, chunk.document_id, chunk.version_id, chunk.chunk_type,
            str(chunk.index_identity),
            str(chunk.locator.get("kind", "text")), str(chunk.locator.get("page", "")),
            str(chunk.locator.get("sheet", "")), str(chunk.locator.get("table_id", "")))


def valid_context(seed: ChunkRecord, other: ChunkRecord, scope: Scope, *, parent=False) -> bool:
    return (other.is_current is True and scope.contains(other.knowledge_base_id, other.document_id)
            and (other.knowledge_base_id, other.document_id, other.version_id) ==
                (seed.knowledge_base_id, seed.document_id, seed.version_id)
            and other.index_identity == seed.index_identity
            and isinstance(other.content, str) and bool(other.content.strip())
            and other.content_sha256 == hashlib.sha256(other.content.encode()).hexdigest()
            and (not parent or other.chunk_role == "parent" and other.chunk_id == seed.parent_id))


def _range(chunk: ChunkRecord):
    start, end = chunk.locator.get("start"), chunk.locator.get("end")
    if type(start) is int and type(end) is int and 0 <= start < end and end - start == len(chunk.content):
        return start, end
    return None


def merge_passages(scope: Scope, candidates: list[tuple[ChunkRecord, RankedHit]], parents: dict,
                   neighbors: dict) -> list[tuple[ContextPassage, list[tuple[ChunkRecord, RankedHit]]]]:
    """Parent fill, same-source grouping, bounded expansion, then global rank.

    Trusted plain text uses exact source overlap, retaining periodic text. Other
    current bodies use upstream containment/suffix matching and sequentiality.
    Structured rows remain separate so their native proofs are never rewritten.
    """
    buckets: dict[tuple, list] = {}
    for position, (chunk, hit) in enumerate(candidates):
        key = source_key(chunk)
        if (chunk.chunk_type != "text" or chunk.locator.get("kind") not in {None, "text", "markdown", "pdf"}
                or chunk.chunk_index is None and chunk.parent_id is None):
            key += (chunk.chunk_id,)
        parent = parents.get(chunk.chunk_id)
        if parent is not None and not valid_context(chunk, parent, scope, parent=True):
            parent = None
        body = join_body(parent.content, chunk.content) if parent else chunk.content
        buckets.setdefault(key, []).append((position, chunk, hit, parent, body))
    groups = []
    for boundary, rows in buckets.items():
        rows.sort(key=lambda r: (r[1].chunk_index is None,
                                 r[1].chunk_index if r[1].chunk_index is not None else r[0], r[1].chunk_id))
        local = []
        for position, chunk, hit, parent, body in rows:
            current_range = _range(chunk) if parent is None else None
            previous = local[-1] if local else None
            join = False
            merged = body
            span = current_range
            if previous:
                same_parent = parent is not None and parent.chunk_id in previous['parents']
                old_range = previous['span']
                if old_range and current_range:
                    a, b = old_range
                    c, d = current_range
                    join = a <= c <= b
                    if join and d > b:
                        overlap = b - c
                        if overlap == 0 or previous['body'][-overlap:] == body[:overlap]:
                            merged = previous['body'] + body[overlap:]
                            span = (a, d)
                        else:
                            merged, span = join_body(previous['body'], body), None
                    elif join:
                        merged = join_body(previous['body'], body)
                        span = old_range if merged == previous['body'] else None
                else:
                    sequential = (type(chunk.chunk_index) is int and
                                  type(previous['last_index']) is int and
                                  chunk.chunk_index == previous['last_index'] + 1)
                    join = same_parent or sequential or contains_body(previous['body'], body) or contains_body(body, previous['body'])
                    if join:
                        merged, span = join_body(previous['body'], body), None
            if join:
                group = previous
                group['body'], group['span'] = merged, span
                group['units'].append((chunk, hit))
                group['position'] = min(group['position'], position)
                group['last_index'] = chunk.chunk_index
            else:
                group = dict(body=body, span=span, units=[(chunk, hit)], position=position,
                             last_index=chunk.chunk_index, parents=set())
                local.append(group)
            if parent:
                group['parents'].add(parent.chunk_id)
        for group in local:
            neighbor_ids = []
            if len(group['body']) < SHORT_CONTEXT:
                # Reuse the existing repository's proved -1/+1 boundary read.
                # Whole-body admission replaces upstream's 850-character slice.
                for seed, _ in group['units']:
                    for offset, neighbor in sorted(neighbors.get(seed.chunk_id, ()), key=lambda row: row[0]):
                        if offset not in (-1, 1) or not valid_context(seed, neighbor, scope):
                            continue
                        if source_key(seed) != source_key(neighbor) or neighbor.chunk_role != 'child':
                            continue
                        if neighbor.chunk_id in neighbor_ids:
                            continue
                        merged = (join_body(neighbor.content, group['body']) if offset == -1 else
                                  join_body(group['body'], neighbor.content))
                        if len(merged) <= NEIGHBOR_MAX_CHARS:
                            group['body'] = merged
                            neighbor_ids.append(neighbor.chunk_id)
                        if len(group['body']) >= SHORT_CONTEXT:
                            break
                    if len(group['body']) >= SHORT_CONTEXT:
                        break
            units = sorted(group['units'], key=lambda row: row[1].rank)
            ids = tuple(row[0].chunk_id for row in units)
            passage = ContextPassage(boundary + (min(ids),), group['body'], ids,
                                     tuple(sorted(group['parents'])), tuple(neighbor_ids))
            groups.append((group['position'], passage, units))
    groups.sort(key=lambda row: (row[0], row[1].key))
    deduplicated = []
    for position, passage, units in groups:
        match = next((n for n, (_, old, _) in enumerate(deduplicated)
                      if old.key[:-1] == passage.key[:-1] and
                      (contains_body(old.content, passage.content) or
                       contains_body(passage.content, old.content))), None)
        if match is None:
            deduplicated.append((position, passage, units))
            continue
        old_position, old, old_units = deduplicated[match]
        existing = {c.chunk_id for c, _ in old_units}
        combined = old_units + [row for row in units if row[0].chunk_id not in existing]
        merged = ContextPassage(old.key, join_body(old.content, passage.content),
            tuple(c.chunk_id for c, _ in combined), tuple(sorted(set(old.parent_ids + passage.parent_ids))),
            tuple(sorted(set(old.neighbor_ids + passage.neighbor_ids))))
        deduplicated[match] = (old_position, merged, combined)
    return [(passage, units) for _, passage, units in deduplicated]
