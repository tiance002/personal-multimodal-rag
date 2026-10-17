"""Incremental split-tag decoder adapted from modelcontext/citations.go."""
from __future__ import annotations

import re

from .registry import SourceRegistry


_TAG_PREFIXES = ("<ref", "</ref", "<kb", "</kb", "<web", "</web")


class CitationStreamDecoder:
    def __init__(self, registry: SourceRegistry):
        self.registry = registry
        self.pending = ""
        self.resource_pending = ""

    def _feed_resources(self, chunk: str) -> str:
        combined = self.resource_pending + chunk
        self.resource_pending = ""
        hold = 0
        for handle in self.registry.resources._by_handle:
            for size in range(1, len(handle)):
                if size > hold and combined.endswith(handle[:size]):
                    hold = size
        if hold:
            self.resource_pending = combined[-hold:]
            combined = combined[:-hold]
        return self.registry.resources.decode_text(combined)

    def feed(self, chunk: str) -> str:
        return self._feed_citations(self._feed_resources(chunk))

    def _feed_citations(self, chunk: str) -> str:
        data = self.pending + chunk
        self.pending = ""
        hold_from = _unclosed_code_start(data)
        visible_end = len(data) if hold_from is None else hold_from
        visible = data[:visible_end]

        # Keep a tag-like suffix until it is complete. Unknown HTML/text
        # beginning with '<' is passed through unchanged.
        lt = visible.rfind("<")
        if lt >= 0:
            suffix = visible[lt:].lower()
            if any(prefix.startswith(suffix) for prefix in _TAG_PREFIXES) or any(
                suffix.startswith(prefix) for prefix in _TAG_PREFIXES
            ):
                if ">" not in suffix:
                    visible_end = min(visible_end, lt)
                    visible = data[:visible_end]

        # Buffer only a possible source-attribution line. Ordinary answer
        # text keeps streaming immediately; a split `Source:` line stays
        # private until its handle suffix can be classified.
        if not self.registry.citations_enabled:
            line_start = visible.rfind("\n") + 1
            if line_start < len(visible) and _could_be_source_attribution_prefix(visible[line_start:]):
                visible_end = min(visible_end, line_start)
                visible = data[:visible_end]

        self.pending = data[visible_end:]
        return self.registry.expand_text(visible) if visible else ""

    def flush(self) -> str:
        resource_tail, self.resource_pending = self.resource_pending, ""
        resource_tail = self.registry.resources.decode_text(resource_tail)
        decoded_resource_tail = self._feed_citations(resource_tail) if resource_tail else ""
        pending, self.pending = self.pending, ""
        if _is_incomplete_internal_tag(pending):
            return decoded_resource_tail
        return decoded_resource_tail + self.registry.decode_output_text(pending)


def _unclosed_code_start(text: str) -> int | None:
    """Return the opening delimiter of a code span/block with no closer yet."""
    i = 0
    while i < len(text):
        line_start = text.rfind("\n", 0, i) + 1
        if line_start == i:
            fence = re.match(r" {0,3}(`{3,}|~{3,})", text[i:])
            if fence:
                marker = fence.group(1)
                content_start = i + fence.end()
                close = re.search(
                    rf"(?m)^ {{0,3}}{re.escape(marker[0])}{{{len(marker)},}}[ \t]*$",
                    text[content_start:],
                )
                if close is None:
                    return i
                i = content_start + close.end()
                continue
        if text[i] == "`":
            end = i
            while end < len(text) and text[end] == "`":
                end += 1
            marker = text[i:end]
            close = _find_exact_backtick_run(text, marker, end)
            if close < 0:
                return i
            i = close + len(marker)
            continue
        i += 1
    return None


def _could_be_source_attribution_prefix(text: str) -> bool:
    candidate = text.lstrip(" \t").lower()
    if not candidate:
        return False
    prefixes = ("source:", "**source:", "__source:")
    return any(prefix.startswith(candidate) or candidate.startswith(prefix) for prefix in prefixes)


def _find_exact_backtick_run(text: str, marker: str, start: int) -> int:
    i = start
    while i < len(text):
        if text[i] != "`":
            i += 1
            continue
        end = i
        while end < len(text) and text[end] == "`":
            end += 1
        if end - i == len(marker):
            return i
        i = end
    return -1


def _is_incomplete_internal_tag(text: str) -> bool:
    return bool(re.fullmatch(r"</?(?:r|re|ref|k|kb|w|we|web)(?:[ \t\r\n/][^<>]*)?", text, re.I))
