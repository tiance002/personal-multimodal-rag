"""Stable source evidence; all offsets refer to a frozen, versioned source text.

PDF/OCR offsets use the frozen extracted text for that page/asset, never bytes
in a PDF container. A source version is the SHA256 of the immutable original
artifact. Production bindings must also retain the extraction fingerprint.
No cloud dependencies and no changes to the business index are made here.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

_HASH = re.compile(r'[0-9a-f]{64}\Z')
_CELL = re.compile(r'[A-Z]{1,4}[1-9][0-9]{0,6}\Z')
_KINDS = {'text', 'markdown', 'pdf', 'image', 'sheet'}


@dataclass(frozen=True)
class SourceSpan:
    document_id: str
    source_version: str
    start: int
    end: int
    kind: str = 'text'
    page: int | None = None
    asset_id: str | None = None
    sheet: str | None = None
    cells: tuple[str, ...] = ()
    coordinate_space: str = 'normalized_source_text'

    def __post_init__(self) -> None:
        if not isinstance(self.document_id, str) or not self.document_id:
            raise ValueError('document identity required')
        if not isinstance(self.source_version, str) or not _HASH.fullmatch(self.source_version):
            raise ValueError('immutable source SHA256 required')
        if type(self.start) is not int or type(self.end) is not int or not 0 <= self.start < self.end:
            raise ValueError('source range must be a nonempty half-open interval')
        if self.kind not in _KINDS or not self.coordinate_space:
            raise ValueError('invalid source coordinates')
        if self.page is not None and (type(self.page) is not int or self.page < 1):
            raise ValueError('invalid page')
        if self.kind == 'pdf' and self.page is None:
            raise ValueError('PDF evidence needs a page')
        if self.kind == 'image' and not self.asset_id:
            raise ValueError('OCR evidence needs an immutable asset binding')
        if self.kind == 'sheet':
            if not self.sheet or not self.cells or len(self.cells) > 10000:
                raise ValueError('sheet and explicit cell coordinates required')
            if any(not isinstance(cell, str) or not _CELL.fullmatch(cell) for cell in self.cells):
                raise ValueError('invalid cell coordinate')
            if len(set(self.cells)) != len(self.cells):
                raise ValueError('duplicate cell coordinate')
        elif self.sheet is not None or self.cells:
            raise ValueError('cell coordinates only apply to sheets')

    @property
    def identity(self) -> tuple[object, ...]:
        return (self.document_id, self.source_version, self.coordinate_space,
                self.kind, self.page, self.asset_id, self.sheet)

    def locator(self) -> dict[str, Any]:
        return {'kind': self.kind, 'start': self.start, 'end': self.end,
                'page': self.page, 'asset_id': self.asset_id, 'sheet': self.sheet,
                'cells': list(self.cells), 'coordinate_space': self.coordinate_space}


@dataclass(frozen=True)
class GoldEvidence:
    evidence_id: str
    span: SourceSpan
    grade: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_id, str) or not self.evidence_id:
            raise ValueError('stable evidence identity required')
        if type(self.grade) is not int or not 1 <= self.grade <= 3:
            raise ValueError('relevance grade must be 1, 2 or 3')


def union_length(intervals: Iterable[tuple[int, int]]) -> int:
    ordered = sorted(intervals)
    total = 0
    left = right = None
    for start, end in ordered:
        if type(start) is not int or type(end) is not int or not 0 <= start < end:
            raise ValueError('invalid coverage interval')
        if left is None:
            left, right = start, end
        elif start > right:
            total += right - left
            left, right = start, end
        else:
            right = max(right, end)
    return total + (right - left if left is not None else 0)


def coverage(gold: list[GoldEvidence], candidates: Iterable[SourceSpan], *, threshold: float = 0.8) -> dict[str, Any]:
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 < threshold <= 1:
        raise ValueError('coverage threshold must be in (0,1]')
    if len({g.evidence_id for g in gold}) != len(gold):
        raise ValueError('duplicate Gold identity')
    spans = list(candidates)
    fractions: dict[str, float] = {}
    intersections: dict[str, list[list[int]]] = {}
    for evidence in gold:
        reference = evidence.span
        matches = [candidate for candidate in spans if candidate.identity == reference.identity]
        if reference.kind == 'sheet':
            cells = {cell for candidate in matches for cell in candidate.cells}
            fraction = len(cells & set(reference.cells)) / len(reference.cells)
            intersections[evidence.evidence_id] = []
        else:
            intervals = [(max(candidate.start, reference.start), min(candidate.end, reference.end))
                         for candidate in matches
                         if max(candidate.start, reference.start) < min(candidate.end, reference.end)]
            fraction = union_length(intervals) / (reference.end - reference.start)
            intersections[evidence.evidence_id] = [[start-reference.start, end-reference.start]
                                                   for start, end in sorted(set(intervals))]
        fractions[evidence.evidence_id] = fraction
    covered = sorted(key for key, fraction in fractions.items() if fraction + 1e-12 >= threshold)
    return {'covered_ids': covered, 'fractions': fractions, 'intersections': intersections,
            'context_recall': len(covered)/len(gold) if gold else None,
            'evidence_coverage': sum(fractions.values())/len(gold) if gold else None}


def evidence_from_dict(value: dict[str, Any]) -> GoldEvidence:
    locator = dict(value['locator'])
    locator['cells'] = tuple(locator.get('cells', ()))
    return GoldEvidence(value['evidence_id'], SourceSpan(value['document_id'], value['source_version'],
                        **locator), value.get('grade', 1))


def convert_legacy_fixture(cases: list[dict[str, Any]], fixture: list[dict[str, Any]], *, dataset_version: str) -> dict[str, Any]:
    """Explicitly freeze fixture text; never pretend it is an original PDF/source.

    Retains all legacy case fields. Result documents provide a new immutable
    fixture-only coordinate space. Real-source datasets must supply their own
    reviewed locators instead of applying this conversion to live chunk IDs.
    """
    chunks = sorted(fixture, key=lambda chunk: chunk['chunk_id'])
    if len({chunk['chunk_id'] for chunk in chunks}) != len(chunks):
        raise ValueError('duplicate legacy chunk ID')
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for chunk in chunks:
        groups.setdefault((chunk['document_id'], chunk['version_id']), []).append(chunk)
    sources = []
    bindings: dict[str, dict[str, Any]] = {}
    for (document, legacy_version), members in sorted(groups.items()):
        source_text = '\n\n'.join(member['content'] for member in members)
        source_version = hashlib.sha256(source_text.encode('utf-8')).hexdigest()
        sources.append({'document_id': document, 'source_version': source_version,
                        'legacy_version': legacy_version, 'text': source_text})
        offset = 0
        for member in members:
            locator = {'kind': 'text', 'start': offset, 'end': offset+len(member['content']),
                       'coordinate_space': 'frozen_fixture_text'}
            canonical = json.dumps([document, source_version, locator], sort_keys=True, separators=(',', ':'))
            evidence_id = 'gold_' + hashlib.sha256(canonical.encode()).hexdigest()[:24]
            bindings[member['chunk_id']] = {'evidence_id': evidence_id, 'document_id': document,
                                            'source_version': source_version, 'locator': locator, 'grade': 1}
            offset += len(member['content']) + 2
    converted = copy.deepcopy(cases)
    for case in converted:
        expected = case['expected_chunk_ids']
        if any(chunk_id not in bindings for chunk_id in expected):
            raise ValueError('legacy Gold refers to an unknown chunk')
        case['gold_evidence'] = [copy.deepcopy(bindings[chunk_id]) for chunk_id in sorted(set(expected))]
        case['dataset_version'] = dataset_version
    return {'schema_version': 2, 'dataset_version': dataset_version,
            'coordinate_space': 'frozen_fixture_text', 'documents': sources, 'cases': converted}
