"""WeKnora 3e8b0bfc adaptive semantics, with exact normalized-source offsets.

Native table/caption admission stays in chunking.py. That module remains the
explicit legacy API; ingestion and any preview use prepare_document below.
"""
from dataclasses import asdict, dataclass, field
from bisect import bisect_right
import hashlib
import json
import re

from backend.app.domain.chunking import chunk_document
from backend.app.domain.models import ChunkDraft, NormalizedDocument, SourceLocator
from backend.app.domain.parsers import ParserError

CHUNKER_VERSION = "weknora-adaptive-parent-child/v1"
SCHEMA_VERSION = "chunk-source-context/v2"
UPSTREAM = "3e8b0bfc80b845b2d4b2ed683994748741450a97"
HEADING = re.compile(r"(?m)^(#{1,6})[ \t]+([^\r\n]+?)[ \t]*#*[ \t]*$")
NUMBERED = re.compile(r"(?m)^[ \t]*(?:\d+(?:\.\d+){1,3}\.?|(?:\d+|[IVX]{1,5})\.)[ \t]+\S.{0,200}$")
CHAPTER = re.compile(r"(?m)^[ \t]*(?:第[ \t]*[一二三四五六七八九十百千零〇0-9]+[ \t]*(?:章|节|節|部分|篇)[ \t]?.{0,200}|(?:Chapter|Section|Part|Kapitel|Abschnitt|Teil)\s+(?:[0-9]+|[IVX]{1,5})[.: ].{0,200})$")
CAPS = re.compile(r"(?m)^[ \t]*[A-ZÄÖÜ][A-ZÄÖÜ \-]{3,80}:?\s*$")
DIVIDER = re.compile(r"(?m)^[ \t]*(?:-{3,}|={3,}|\*{3,}|_{3,})[ \t]*$")
PAGE = re.compile(r"(?mi)^[ \t]*(?:Seite|Page|页码?)\s+\d+(?:\s*(?:von|of|/)\s*\d+)?[ \t]*$")
PROTECTED = [re.compile(p) for p in (
    r"(?s)\$\$.*?\$\$", r"!\[[^\]\n]{0,200}\]\([^)\n]{1,500}\)",
    r"\[[^\]\n]{1,200}\]\([^)\n]{1,500}\)",
    r"(?m)[ ]*(?:\|[^|\n]*)+\|[\r\n]+\s*(?:\|\s*:?-{3,}:?\s*)+\|[\r\n]+",
    r"(?m)[ ]*(?:\|[^|\n]*)+\|[\r\n]+",
    r"(?s)```(?:\w+)?[\r\n].*?```", r"`[^`\r\n]+`",
)]


@dataclass(frozen=True)
class ChunkingConfig:
    general_size: int = 512
    general_overlap: int = 80
    parent_size: int = 4096
    child_size: int = 384
    strategy: str = "auto"

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in (self.general_size, self.parent_size, self.child_size)):
            raise ValueError("CHUNK_SIZE_INVALID")
        if not 0 <= self.general_overlap < self.general_size:
            raise ValueError("CHUNK_OVERLAP_INVALID")
        if self.strategy not in {"auto", "heading", "heuristic", "recursive", "legacy"}:
            raise ValueError("CHUNK_STRATEGY_INVALID")

    @property
    def identity(self):
        value = {"chunker": CHUNKER_VERSION, "schema": SCHEMA_VERSION, "upstream": UPSTREAM,
                 "config": asdict(self), "context_header": "breadcrumb/trim-body-v1",
                 "native_evidence": "atomic-row-caption-v1",
                 "protected_spans": "weknora/header-row/nonoverlap-v1",
                 "embedding_input_limit": "unknown/not-derived-from-chunk-target-v1"}
        return "p3:" + hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def embedding_fingerprint(model: str, dimension: int, config: ChunkingConfig) -> str:
    # Compatibility helper for the optional local provider. Identity now also
    # covers unknown revision, distance and full header/child input semantics.
    from backend.app.domain.embedding_identity import EmbeddingIdentity
    return EmbeddingIdentity("ollama", model, "UNKNOWN", dimension, "cosine", config.identity).fingerprint


def protected_spans(text):
    spans = sorted((m.start(), m.end()) for pattern in PROTECTED for m in pattern.finditer(text))
    # The upstream line walker excludes headings until a closing fence, even
    # when OCR leaves a fence unterminated. Regex-only detection misses that.
    fence_start, offset = None, 0
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            if fence_start is None:
                fence_start = offset
            else:
                spans.append((fence_start, offset+len(line)))
                fence_start = None
        offset += len(line)
    if fence_start is not None:
        spans.append((fence_start,len(text)))
    # Fixed upstream: longest match wins at the same start; skip overlaps.
    # Adjacent table rows remain separate protection units, never a whole table.
    spans.sort(key=lambda span: (span[0], -(span[1]-span[0])))
    selected = []
    for start, end in spans:
        if not selected or start >= selected[-1][1]:
            selected.append((start, end))
    return selected


def profile_text(text):
    spans = protected_spans(text)
    eligible = lambda m: not any(a <= m.start() < b for a, b in spans)
    headings = [m for m in HEADING.finditer(text) if eligible(m)]
    markers = [m for p in (NUMBERED, CHAPTER, CAPS, DIVIDER) for m in p.finditer(text) if eligible(m)]
    counts = {level: sum(len(m[1]) == level for m in headings) for level in range(1, 7)}
    dominant = next((level for level, count in counts.items() if count >= 3),
                    max((level for level, count in counts.items() if count), default=0))
    return dict(chars=len(text), lines=len(text.split("\n")), headings=len(headings),
                heading_density=len(headings) / max(1, len(text.split("\n"))), dominant=dominant,
                markers=len(markers) + text.count("\f"), form_feeds=text.count("\f"),
                chapters=sum(eligible(m) for m in CHAPTER.finditer(text)))


def select_tiers(profile, strategy):
    if strategy in {"recursive", "legacy"}:
        return ["legacy"]
    if strategy != "auto":
        return [strategy, "legacy"]
    tiers = []
    if profile["headings"] >= 3 and profile["heading_density"] > .005 and profile["dominant"]:
        tiers.append("heading")
    if profile["markers"] >= 5 or profile["form_feeds"] or profile["chapters"]:
        tiers.append("heuristic")
    return tiers + ["legacy"]


def validate_ranges(text, ranges, size):
    """Source/coverage/hard budgets are mandatory; upstream quality is advisory."""
    if not ranges:
        return "no chunks produced"
    covered = 0
    for start, end in ranges:
        if not 0 <= start < end <= len(text) or start > covered:
            return "invalid source coverage"
        if end - start > size:
            return "chunk exceeds hard size budget"
        covered = max(covered, end)
    if covered != len(text):
        return "invalid source coverage"
    lengths = [b-a for a,b in ranges]
    if len(ranges) == 1 and len(text) > 2*size:
        return "single chunk for large document"
    tiny = sum(n < 50 for n in lengths[:-1])
    if tiny > len(ranges)//4 and tiny > 2:
        return "too many tiny chunks"
    if max(lengths) < size//4 and len(text) > size:
        return "all chunks far below target size"
    return None


def _recursive(text, size, overlap):
    spans = protected_spans(text)
    ranges, cursor = [], 0
    while cursor < len(text):
        end = min(cursor+size, len(text))
        if end < len(text):
            for a,b in spans:
                if a < end < b and b-a <= size:
                    end = a if a > cursor else b
                    break
            else:
                window = text[cursor+size//2:end]
                for sep in ("\n\n", "\n", "。", "！", "？", "；", ". ", "! ", "? ", "; ", " "):
                    offset = window.rfind(sep)
                    candidate = cursor+size//2+offset+len(sep)
                    if offset >= 0 and not any(a < candidate < b for a,b in spans):
                        end = candidate
                        break
        ranges.append((cursor, end))
        if end == len(text):
            break
        # Upstream semantic suffix overlap: no arbitrary mid-word suffix.
        candidates = [m.end() for m in re.finditer(r"\n|[。！？；]|[.!?;] ", text[max(cursor+1,end-overlap):end])]
        next_start = max(cursor+1,end-overlap)+min(candidates) if candidates else end
        if next_start < end and any(a < next_start < b for a,b in spans):
            next_start = end
        cursor = max(cursor+1, next_start)
    return ranges


def _tier_ranges(text, size, overlap, tier, profile):
    if tier == "legacy":
        return _recursive(text, size, overlap)
    spans = protected_spans(text)
    matches = ([m for m in HEADING.finditer(text) if len(m[1]) <= profile["dominant"]]
               if tier == "heading" else
               [m for p in (NUMBERED, CHAPTER, CAPS, DIVIDER, PAGE, re.compile(r"\f")) for m in p.finditer(text)])
    positions = {m.start() for m in matches}
    if tier == "heuristic":
        positions.update(m.end() for m in re.finditer(r"\n{3,}", text))
    boundaries = sorted({0, len(text)} | {p for p in positions if not any(a < p < b for a,b in spans)})
    if tier == "heuristic":
        return _bin_pack(text, size, overlap, boundaries)
    ranges = []
    for start,end in zip(boundaries,boundaries[1:]):
        ranges.extend((start+a,start+b) for a,b in _recursive(text[start:end],size,overlap))
    # Port the upstream tiny-section coalescing, preserving exact adjacent spans.
    merged = []
    target = max(200, size//2) if tier == "heading" else max(50,size//4)
    events = _header_events(text)
    offsets = [offset for offset, _ in events]
    def header(start):
        index = bisect_right(offsets, start) - 1
        return events[index][1] if index >= 0 else ""
    for start,end in ranges:
        shared = _common_header(header(merged[-1][0]), header(start)) if merged else ""
        if shared and merged[-1][1] == start and merged[-1][1]-merged[-1][0] < target and end-merged[-1][0] <= size:
            merged[-1] = (merged[-1][0],end)
        else:
            merged.append((start,end))
    return merged


def _bin_pack(text, size, overlap, boundaries):
    """Fixed upstream heuristic greedy packing and aligned overlap semantics."""
    ranges = []
    start = end = 0
    minimum = max(50, size//4)
    for next_end in boundaries[1:]:
        if next_end-end > size:
            if end > start:
                ranges.append((start,end))
            ranges.extend((end+a,end+b) for a,b in _recursive(text[end:next_end],size,overlap))
            start = end = next_end
            continue
        if next_end-start > size and end-start >= minimum:
            ranges.append((start,end))
            window_start = max(0,end-2*overlap)
            aligned = [p for p in boundaries if window_start <= p < end]
            if overlap and aligned:
                start = max(aligned)
            elif overlap:
                target = max(0,end-overlap)
                newline = text.rfind("\n", window_start+1, target+1)
                start = newline+1 if newline >= 0 else target
            else:
                start = end
        end = next_end
    if end > start:
        ranges.append((start,end))
    return ranges


def _common_header(left, right):
    lines = []
    for a,b in zip(left.splitlines(),right.splitlines()):
        if a != b:
            break
        lines.append(a)
    return "\n".join(lines)


def split_text(text, size, overlap, strategy="auto"):
    profile = profile_text(text)
    diagnostics = dict(profile=profile, chain=select_tiers(profile,strategy), rejected=[])
    for tier in diagnostics["chain"]:
        ranges = _tier_ranges(text,size,min(overlap,size//2),tier,profile)
        reason = validate_ranges(text,ranges,size)
        if reason:
            diagnostics["rejected"].append(dict(tier=tier,reason=reason))
            if tier != "legacy":
                continue
            # Final recursive tier cannot waive source or hard size invariants.
            if reason in {"no chunks produced","invalid source coverage","chunk exceeds hard size budget"}:
                raise ParserError("CHUNK_VALIDATION_FAILED")
        diagnostics["selected"] = tier
        return ranges, diagnostics
    raise ParserError("CHUNK_VALIDATION_FAILED")


def _header_events(text):
    """Track nested headings once; do not repeatedly rescan the document."""
    spans = protected_spans(text)
    events, stack = [], {}
    for match in HEADING.finditer(text):
        if any(a <= match.start() < b for a, b in spans):
            continue
        level = len(match[1])
        stack = {k: v for k, v in stack.items() if k < level}
        stack[level] = match[1] + " " + match[2].strip()
        events.append((match.start(), "\n".join(stack.values())))
    return events


@dataclass
class PreparedChunks:
    parents: list[ChunkDraft] = field(default_factory=list)
    children: list[ChunkDraft] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)
    config: ChunkingConfig = field(default_factory=ChunkingConfig)


def prepare_document(document: NormalizedDocument, config: ChunkingConfig | None = None) -> PreparedChunks:
    """One source/config/split entry for ingestion and optional offline preview."""
    config = config or ChunkingConfig()
    text = document.markdown_content
    if not text.strip():
        raise ParserError("EMPTY_TEXT")
    result = PreparedChunks(config=config)
    events = _header_events(text)
    event_offsets = [offset for offset, _ in events]

    def breadcrumb(offset, inherited):
        index = bisect_right(event_offsets, offset) - 1
        return events[index][1] if index >= 0 else "\n".join("#"*(i+1)+" "+h for i,h in enumerate(inherited))
    # Preserve native proofs and page/asset boundaries using the existing gate.
    if document.tables or any(s.content_type == "image_caption" for s in document.sections):
        regions = chunk_document(document,max_chars=config.parent_size,overlap=0)
    elif document.media_type == "application/pdf" or document.media_type.startswith("image/"):
        regions = chunk_document(document,max_chars=max(config.parent_size,len(text)),overlap=0)
    else:
        regions = [ChunkDraft(chunk_index=0,content=text,start=0,end=len(text),content_sha256=hashlib.sha256(text.encode()).hexdigest(),
            heading_path=document.sections[0].heading_path if len(document.sections)==1 else (),
            source_locator=SourceLocator(kind="markdown" if document.media_type=="text/markdown" else "text",start=0,end=len(text),quote=text,
                conversion_lineage=document.conversion_lineage,parse_status=document.parse_status,parse_warnings=document.parse_warnings))]

    def draft(region,start,end,*,parent_index=None,parent=False):
        content=text[start:end]
        header=breadcrumb(start,region.heading_path)
        # Coalesced sibling sections must carry their shared breadcrumb;
        # source heading lines themselves remain untouched in content.
        for offset, nested in events:
            if start < offset < end:
                header = _common_header(header,nested)
        return region.model_copy(update=dict(chunk_index=len(result.parents) if parent else len(result.children),
            content=content,start=start,end=end,context_header=header,parent_index=parent_index,
            heading_path=tuple(line.lstrip("# ") for line in header.splitlines()),
            chunk_type="parent_text" if parent else region.chunk_type,content_sha256=hashlib.sha256(content.encode()).hexdigest(),
            source_locator=region.source_locator.model_copy(update=dict(start=start,end=end,quote=content))))

    for region in regions:
        if region.chunk_type in {"table","image_caption"}:
            item=draft(region,region.start,region.end)
            result.children.append(item)
            continue
        parents,diag=split_text(region.content,config.parent_size,config.general_overlap,config.strategy)
        result.diagnostics.append(diag)
        for a,b in parents:
            pstart,pend=region.start+a,region.start+b
            # SourceContent targets do not establish model input capacity.
            # Keep the full breadcrumb and native evidence; the provider contract
            # currently exposes no verified input limit or tokenizer.
            subs,child_diag=split_text(text[pstart:pend],config.child_size,config.child_size//5,config.strategy)
            nonblank = [(c,e) for c,e in subs if text[pstart+c:pstart+e].strip()]
            child_diag["skipped_whitespace"] = len(subs)-len(nonblank)
            subs = nonblank
            result.diagnostics.append(child_diag)
            if not subs:
                continue
            parent_index=None
            if len(subs)>1 or subs[0]!=(0,pend-pstart):
                parent_index=len(result.parents)
                result.parents.append(draft(region,pstart,pend,parent=True))
            for c,e in subs:
                item=draft(region,pstart+c,pstart+e,parent_index=parent_index)
                result.children.append(item)
    if not result.children:
        raise ParserError("EMPTY_TEXT")
    return result


def preview_document_chunks(document, config=None):
    return prepare_document(document,config)
