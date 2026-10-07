"""Offline neighbor policy; supplied metadata is NOT database authorization.

No production wiring. A future repository must bind scope and active-version
predicates in the actual neighbor query. This policy neither retrieves chunks
nor changes seed selection, retrieval hits, evidence quotes or coverage rules.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from backend.app.domain.models import ChunkRecord
from backend.app.domain.scope import Scope


@dataclass(frozen=True)
class NeighborMetadata:
    chunk: ChunkRecord
    chunk_index: int | None
    active_version_id: str | None
    index_status: str | None
    document_deleted: bool | None
    chunk_type: str | None
    boundary_kind: str | None
    section_id: str | None
    boundary_start: int | None
    boundary_end: int | None
    page: int | None


@dataclass(frozen=True)
class ExpansionReason:
    code: str
    seed_id: str | None = None
    neighbor_id: str | None = None


@dataclass(frozen=True)
class NeighborLink:
    seed_id: str
    neighbor_id: str
    version_id: str
    offset: int


@dataclass(frozen=True)
class ExpansionResult:
    seeds: tuple[ChunkRecord, ...]
    neighbors: tuple[ChunkRecord, ...] = ()
    provenance: tuple[NeighborLink, ...] = ()
    reasons: tuple[ExpansionReason, ...] = ()
    added_chars: int = 0

    @property
    def chunks(self) -> tuple[ChunkRecord, ...]:
        return self.seeds + self.neighbors


def _valid_identity(chunk: ChunkRecord) -> bool:
    return all(isinstance(value, str) and bool(value.strip()) for value in (
        chunk.chunk_id, chunk.knowledge_base_id, chunk.document_id, chunk.version_id))


def _metadata_error(meta: NeighborMetadata, scope: Scope) -> str | None:
    chunk = meta.chunk
    if not _valid_identity(chunk):
        return "INVALID_EVIDENCE"
    if not scope.contains(chunk.knowledge_base_id, chunk.document_id):
        return "OUT_OF_SCOPE"
    required = (meta.chunk_index, meta.active_version_id, meta.index_status,
                meta.document_deleted, meta.chunk_type, meta.boundary_kind,
                meta.section_id, meta.boundary_start, meta.boundary_end)
    if any(value is None or value == "" for value in required):
        return "MISSING_METADATA"
    if (type(meta.chunk_index) is not int or meta.chunk_index < 0
            or type(meta.document_deleted) is not bool
            or type(meta.boundary_start) is not int or type(meta.boundary_end) is not int
            or not 0 <= meta.boundary_start < meta.boundary_end
            or not isinstance(meta.section_id, str) or not meta.section_id.strip()
            or not isinstance(meta.active_version_id, str)):
        return "INVALID_METADATA"
    if chunk.is_current is not True or meta.active_version_id != chunk.version_id:
        return "INACTIVE_VERSION"
    if meta.index_status != "ready":
        return "INDEX_NOT_READY"
    if meta.document_deleted:
        return "DOCUMENT_DELETED"
    locator = chunk.locator
    if not isinstance(locator, dict) or not locator:
        return "INVALID_EVIDENCE"
    raw = locator.get("raw_evidence")
    for provenance in (locator, raw):
        if isinstance(provenance, dict) and (
            provenance.get("evidence_kind") == "model_generated_caption"
            or provenance.get("schema_version") == "local-caption/v1"
            or provenance.get("asset_type") == "caption"
            or provenance.get("content_type") == "image_caption"
        ):
            return "UNSUPPORTED_TYPE"
    kind = locator.get("kind")
    if not isinstance(kind, str):
        return "INVALID_EVIDENCE"
    if meta.chunk_type != "text" or kind not in {"text", "markdown", "pdf"}:
        return "UNSUPPORTED_TYPE"
    if kind == "pdf":
        if meta.page is None or locator.get("page") is None:
            return "MISSING_METADATA"
        if (meta.boundary_kind != "page" or type(meta.page) is not int or meta.page < 1
                or type(locator["page"]) is not int or locator["page"] != meta.page):
            return "INVALID_METADATA"
    elif meta.boundary_kind != "section" or meta.page is not None or locator.get("page") is not None:
        return "INVALID_METADATA"
    start, end = locator.get("start"), locator.get("end")
    if (type(start) is not int or type(end) is not int
            or not meta.boundary_start <= start < end <= meta.boundary_end
            or not isinstance(chunk.content, str) or not chunk.content.strip()
            or end - start != len(chunk.content)
            or not chunk.chunk_id or not chunk.version_id
            or locator.get("quote", chunk.content) != chunk.content
            or chunk.content_sha256 != hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()):
        return "INVALID_EVIDENCE"
    return None


def expand_context(
    scope: Scope,
    seeds: Sequence[NeighborMetadata],
    candidates: Sequence[NeighborMetadata],
    *,
    enabled: bool = False,
    remaining_items: int | None = None,
    remaining_chars: int | None = None,
    label_overheads: Sequence[int] = (),
    separator_chars: int | None = None,
) -> ExpansionResult:
    """Append at most four whole neighbors, exactly index -1/+1 per seed.

    Budgets are the caller's remaining capacity AFTER original seed selection.
    label_overheads[n] is the measured label/prefix cost for added slot n;
    separator_chars is the measured separator cost for each appended chunk.
    Neither these values nor metadata attest to DB permissions or token limits.
    Disabled mode returns original seeds without interpreting candidate metadata.
    """
    original = tuple(meta.chunk for meta in seeds)
    if type(enabled) is not bool:
        raise ValueError("enabled must be boolean")
    if not enabled:
        return ExpansionResult(original, reasons=(ExpansionReason("DISABLED"),))
    if any(type(value) is not int or value < 0
           for value in (remaining_items, remaining_chars, separator_chars)):
        raise ValueError("explicit nonnegative remaining budgets and separator cost required")
    if (len(label_overheads) < min(4, remaining_items)
            or any(type(value) is not int or value <= 0 for value in label_overheads)):
        raise ValueError("explicit positive label overhead required for each possible added slot")

    reasons: list[ExpansionReason] = []
    unique: dict[tuple[str, str], NeighborMetadata] = {}
    conflicts: set[tuple[str, str]] = set()
    for meta in (*seeds, *candidates):
        if not _valid_identity(meta.chunk):
            reasons.append(ExpansionReason("INVALID_EVIDENCE"))
            continue
        key = (meta.chunk.version_id, meta.chunk.chunk_id)
        if key in unique and unique[key] != meta:
            conflicts.add(key)
        else:
            unique[key] = meta
    selected = {(chunk.version_id, chunk.chunk_id) for chunk in original if _valid_identity(chunk)}
    admitted: list[NeighborMetadata] = []
    for meta in unique.values():
        key = (meta.chunk.version_id, meta.chunk.chunk_id)
        error = "IDENTITY_CONFLICT" if key in conflicts else _metadata_error(meta, scope)
        if error:
            reasons.append(ExpansionReason(error, neighbor_id=meta.chunk.chunk_id))
        else:
            admitted.append(meta)

    neighbors: list[ChunkRecord] = []
    links: list[NeighborLink] = []
    used = 0
    for seed in seeds:
        chunk = seed.chunk
        if (_metadata_error(seed, scope)
                or (chunk.version_id, chunk.chunk_id) in conflicts):
            continue
        for offset in (-1, 1):
            # A candidate is only related by explicit identity and index, never
            # by its input order, UUID, embedding rank or matching prose.
            matches = [meta for meta in admitted if meta.chunk_index == seed.chunk_index + offset]
            related: list[NeighborMetadata] = []
            for meta in matches:
                other = meta.chunk
                if (other.knowledge_base_id, other.document_id, other.version_id) != (
                        chunk.knowledge_base_id, chunk.document_id, chunk.version_id):
                    reasons.append(ExpansionReason("IDENTITY_MISMATCH", chunk.chunk_id, other.chunk_id))
                    continue
                if (meta.boundary_kind, meta.section_id, meta.boundary_start, meta.boundary_end, meta.page) != (
                        seed.boundary_kind, seed.section_id, seed.boundary_start, seed.boundary_end, seed.page):
                    reasons.append(ExpansionReason("BOUNDARY_MISMATCH", chunk.chunk_id, other.chunk_id))
                    continue
                before = (other.locator["start"] < chunk.locator["start"]
                          and other.locator["end"] < chunk.locator["end"])
                after = (other.locator["start"] > chunk.locator["start"]
                         and other.locator["end"] > chunk.locator["end"])
                if not (before if offset == -1 else after):
                    reasons.append(ExpansionReason("POSITION_MISMATCH", chunk.chunk_id, other.chunk_id))
                    continue
                related.append(meta)
            if not related:
                reasons.append(ExpansionReason("NO_NEIGHBOR", chunk.chunk_id))
                continue
            if len(related) != 1:
                reasons.append(ExpansionReason("AMBIGUOUS_NEIGHBOR", chunk.chunk_id))
                continue
            other = related[0].chunk
            key = (other.version_id, other.chunk_id)
            if key in selected:
                reasons.append(ExpansionReason("ALREADY_SELECTED", chunk.chunk_id, other.chunk_id))
                continue
            if len(neighbors) >= 4:
                reasons.append(ExpansionReason("GLOBAL_LIMIT", chunk.chunk_id, other.chunk_id))
                continue
            if len(neighbors) >= remaining_items:
                reasons.append(ExpansionReason("ITEM_BUDGET", chunk.chunk_id, other.chunk_id))
                continue
            cost = len(other.content) + label_overheads[len(neighbors)] + separator_chars
            if used + cost > remaining_chars:
                reasons.append(ExpansionReason("CHAR_BUDGET", chunk.chunk_id, other.chunk_id))
                continue
            neighbors.append(other)
            selected.add(key)
            links.append(NeighborLink(chunk.chunk_id, other.chunk_id, other.version_id, offset))
            used += cost
    return ExpansionResult(original, tuple(neighbors), tuple(links), tuple(reasons), used)
