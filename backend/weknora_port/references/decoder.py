"""Incremental split-tag decoder adapted from modelcontext/citations.go."""
from __future__ import annotations

from .registry import SourceRegistry


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
        data = self.pending + self._feed_resources(chunk)
        self.pending = ""
        out: list[str] = []
        while data:
            idx = data.find("<")
            if idx < 0:
                out.append(data)
                break
            out.append(data[:idx])
            data = data[idx:]
            lower = data.lower()
            starts = ["<ref", "<kb", "<web"]
            is_tag_start = any(lower.startswith(prefix) and (len(lower) == len(prefix) or lower[len(prefix)] in " \t\r\n>") for prefix in starts)
            is_partial = any(len(lower) <= len(prefix) and prefix.startswith(lower) for prefix in starts)
            if (is_tag_start or is_partial) and ">" not in data:
                self.pending = data
                break
            if is_tag_start:
                end = data.index(">")
                tag = data[:end+1]
                if lower.startswith("<ref"):
                    out.append(self.registry.expand_text(tag))
                # Public tags written by the model are output-only and dropped.
                data = data[end+1:]
                continue
            out.append("<")
            data = data[1:]
        return "".join(out)

    def flush(self) -> str:
        resource_tail, self.resource_pending = self.resource_pending, ""
        resource_tail = self.registry.resources.decode_text(resource_tail)
        decoded_resource_tail = self.feed(resource_tail) if resource_tail else ""
        pending, self.pending = self.pending, ""
        lower = pending.lower()
        if any(lower.startswith(prefix) or (len(lower) <= len(prefix) and prefix.startswith(lower)) for prefix in ("<ref", "<kb", "<web")):
            return decoded_resource_tail
        return decoded_resource_tail + self.registry.expand_text(pending)
