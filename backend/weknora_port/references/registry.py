"""Request-local citation registry, adapted from WeKnora modelcontext/sources.go.

Upstream Tencent/WeKnora@3e8b0bfc, MIT; attribution: docs/weknora-port/UPSTREAM_LICENSE.md.
Handles are ephemeral and this module has no persistence or retrieval authority.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, replace
from urllib.parse import urlsplit, urlunsplit

_HANDLE = re.compile(r"(?i)^[cdbw][1-9][0-9]*$")
_REF = re.compile(r"(?is)<ref(?:\s|$)[^>]*(?:>|$)")
_REF_VALID = re.compile(r'(?i)<ref\s+id\s*=\s*"([^"]+)"\s*/?>')
_KB = re.compile(r"(?is)<kb(?:\s|$)[^>]*(?:>|$)")
_WEB = re.compile(r"(?is)<web(?:\s|$)[^>]*(?:>|$)")
_PUBLIC_KB = re.compile(r"(?is)<kb\b[^>]*>")
_PUBLIC_WEB = re.compile(r"(?is)<web\b[^>]*>")
_ATTR = {
    name: re.compile(rf'(?i)\b{re.escape(name)}\s*=\s*"([^"]*)"')
    for name in ("doc", "chunk_id", "kb_id", "url", "title")
}
_STORED_RESOURCE = re.compile(
    r"resource://[0-9A-Za-z_-]{22}|"
    r"(?:storage://[0-9A-Za-z_-]+/)?(?:local|minio|cos|tos|s3|oss|ks3|obs)://[^\s)\]>\"']+|"
    r"summary/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_RESOURCE_HANDLE = re.compile(r"res://\d+")

_SOURCE_PROTOCOL_COMMON = '''
## Source handling protocol (system-owned)
Retrieved content uses request-local source handles: cN identifies a knowledge chunk, wN a web page, dN a document, and bN a knowledge base.
- Use dN and bN only as tool arguments when a tool requests a document or knowledge base.
- Never reveal raw chunk IDs, knowledge IDs, knowledge-base IDs, or private source handles in user-visible output. This does not change separate instructions to preserve retrieved Markdown image URLs.
'''
_CITATION_ENABLED_PROMPT = '''
- Source citations are enabled for this answer. Cite a knowledge chunk with exactly <ref id="cN"/> and a web page with exactly <ref id="wN"/>.
- Cite only cN/wN handles backed by tool results for the current task, and only when that source supports the adjacent claim. Never cite dN/bN.
- Handles in historical answers, tool arguments, or the bound knowledge-base directory are for navigation, not current evidence. Retrieve the relevant source before citing it.
- If a source has no citation handle, use its exact supplied HTTP(S) URL as a Markdown link when available. If neither is available, omit the citation; never invent or borrow a source.
- Never output <kb> or <web> tags yourself; the system expands valid <ref/> tags after generation.
- Keep each <ref/> inline on the same line as the claim it supports. Do not group citations at the end. For a requested exact output format, use citations only where the format permits them; do not break a required schema to add citations.
- These rules supersede earlier, saved, or custom prompt instructions about citation syntax.
'''
_RESOURCE_PROTOCOL_PROMPT = '''

## Resource handle protocol (system-owned)
Some durable resources and high-entropy Wiki slugs are represented by request-local res://NNNN handles. Copy supplied handles exactly in links, images, and tool arguments; they refer only to the supplied resource versions. Never reveal or invent a resource handle.
'''

_CITATION_DISABLED_PROMPT = '''
- Source citations are disabled for this answer. Do not add <ref>, <kb>, <web>, or source attribution links to the answer. This does not prohibit a URL explicitly requested by the user, Wiki navigation links, downloadable deliverables, or relevant image URLs.
- These rules supersede earlier, saved, or custom prompt instructions that require source citation syntax.
'''


@dataclass
class ChunkReference:
    chunk_id: str
    knowledge_id: str = ""
    knowledge_base_id: str = ""
    document_title: str = ""
    chunk_index: int = 0
    chunk_type: str = ""


@dataclass(frozen=True)
class RegistrySnapshot:
    """Immutable mapping snapshot; contains no mutable per-attempt observations."""
    chunks: tuple[tuple[str, tuple[str, str, str, str, int, str]], ...]
    documents: tuple[tuple[str, str], ...]
    knowledge_bases: tuple[tuple[str, str], ...]
    webs: tuple[tuple[str, str, str], ...]
    citable: frozenset[str]
    resources: tuple[tuple[str, str], ...]


class ResourceRegistry:
    """Request-local `res://NNNN` map from modelcontext/resources.go."""

    def __init__(self):
        self._by_handle: dict[str, str] = {}
        self._by_value: dict[str, str] = {}

    def register(self, value: str) -> str:
        handle = self._by_value.get(value)
        if handle is None:
            handle = f"res://{len(self._by_handle)+1:04d}"
            self._by_handle[handle] = value
            self._by_value[value] = handle
        return handle

    def encode_text(self, value: str) -> str:
        return _STORED_RESOURCE.sub(lambda match: self.register(match.group(0)), value)

    def decode_text(self, value: str) -> str:
        for handle, resource in sorted(self._by_handle.items(), key=lambda item: -len(item[0])):
            value = value.replace(handle, resource)
        return value

    def clone(self) -> "ResourceRegistry":
        clone = ResourceRegistry()
        clone._by_handle = self._by_handle.copy()
        clone._by_value = self._by_value.copy()
        return clone


class SourceRegistry:
    """One response's handles, separating current evidence from navigation history."""

    def __init__(self, citations_enabled: bool = True):
        self.citations_enabled = citations_enabled
        self._chunks: dict[str, ChunkReference] = {}
        self._chunk_keys: dict[str, str] = {}
        self._docs: dict[str, str] = {}
        self._kbs: dict[str, str] = {}
        self._webs: dict[str, tuple[str, str]] = {}  # handle -> (raw URL, title)
        self._web_keys: dict[str, str] = {}
        self._citable: set[str] = set()
        self.resources = ResourceRegistry()
        # Per-candidate observations; never copied by clone_for_attempt().
        self.invalid_handles: list[str] = []  # telemetry only; does not reject answer
        self.used_handles: set[str] = set()

    @property
    def invalid_count(self) -> int:
        return len(self.invalid_handles)

    @property
    def protocol_prompt(self) -> str:
        """Exact fixed upstream Registry.ProtocolPrompt; no MCP execution authority."""
        common = '\n\n## Source handling protocol (system-owned)\nRetrieved content uses request-local source handles: cN identifies a knowledge chunk, wN a web page, dN a document, and bN a knowledge base.\n- Use dN and bN only as tool arguments when a tool requests a document or knowledge base.\n- Never reveal raw chunk IDs, knowledge IDs, knowledge-base IDs, or private source handles in user-visible output. This does not change separate instructions to preserve retrieved Markdown image URLs.'
        enabled = '\n- Source citations are enabled for this answer. Cite a knowledge chunk with exactly <ref id="cN"/> and a web page with exactly <ref id="wN"/>.\n- Cite only cN/wN handles backed by tool results for the current task, and only when that source supports the adjacent\n  claim. Never cite dN/bN.\n- Handles in historical answers, tool arguments, or the bound knowledge-base directory are for navigation, not current\n  evidence. Retrieve the relevant source before citing it.\n- MCP results are external sources. Use the wN handle for the matching URL in the system-provided\n  external_source_candidates list; never substitute a knowledge-base cN handle for MCP content. Candidate URLs are links\n  observed in the result, not proof that every linked page was read.\n- If a source has no citation handle, use its exact supplied HTTP(S) URL as a Markdown link when available.\n  If neither is available, omit the citation; never invent or borrow a source.\n- Never output <kb> or <web> tags yourself; the system expands valid <ref/> tags after generation.\n- Keep each <ref/> inline on the same line as the claim it supports. Do not group citations at the end. For a requested exact output format, use citations only where the format permits them; do not break a required schema to add citations.\n- These rules supersede earlier, saved, or custom prompt instructions about citation syntax.'
        disabled = '\n- Source citations are disabled for this answer. Do not add <ref>, <kb>, <web>, or source attribution links to the answer. This does not prohibit a URL explicitly requested by the user, Wiki navigation links, downloadable deliverables, or relevant image URLs.\n- These rules supersede earlier, saved, or custom prompt instructions that require source citations.'
        resources = '\n\n## Resource handle protocol (system-owned)\nSome durable resources and high-entropy Wiki slugs are represented by request-local res://NNNN handles. Wiki issues may use iN handles.\n- Copy supplied handles exactly in links, images, and tool arguments; they refer only to the supplied resource versions.\n- For downloadable deliverables generated in the session workspace, use sandbox:<file name>; never reuse or invent a resource handle. This download convention does not apply to editing installed skill files.'
        routing = '\nMCP routing uses request-local msN server IDs and mtN tool references. Copy them exactly from the directory or describe result; never invent them.\n'
        return common + (enabled if self.citations_enabled else disabled) + resources + routing

    def snapshot(self) -> RegistrySnapshot:
        return RegistrySnapshot(
            tuple((h, (ref.chunk_id, ref.knowledge_id, ref.knowledge_base_id,
                      ref.document_title, ref.chunk_index, ref.chunk_type))
                  for h, ref in self._chunks.items()),
            tuple(self._docs.items()), tuple(self._kbs.items()),
            tuple((h, url, title) for h, (url, title) in self._webs.items()),
            frozenset(self._citable), tuple(self.resources._by_handle.items()),
        )

    def clone_for_attempt(self) -> "SourceRegistry":
        """Copy stable handle/evidence maps and start fresh candidate observations.

        Use once for each generation candidate. Registrations or invalid/use
        observations made by one attempt cannot mutate the next attempt.
        """
        clone = SourceRegistry(self.citations_enabled)
        clone._chunks = {h: replace(ref) for h, ref in self._chunks.items()}
        clone._chunk_keys = self._chunk_keys.copy()
        clone._docs = self._docs.copy()
        clone._kbs = self._kbs.copy()
        clone._webs = self._webs.copy()
        clone._web_keys = self._web_keys.copy()
        clone._citable = self._citable.copy()
        clone.resources = self.resources.clone()
        return clone

    def reset_attempt_observations(self) -> None:
        """Clear candidate-only use/invalid telemetry without changing mappings."""
        self.used_handles.clear()
        self.invalid_handles.clear()

    def _known(self, value: str, table: dict) -> str:
        handle = value.strip().lower()
        return handle if handle in table else ""

    def register_chunk(self, ref: ChunkReference, *, evidence: bool = True) -> str:
        source_id = ref.chunk_id.strip()
        if not source_id:
            return ""
        if _HANDLE.fullmatch(source_id):
            return self._known(source_id, self._chunks)
        handle = self._chunk_keys.get(source_id)
        if handle is None:
            handle = f"c{len(self._chunks)+1}"
            self._chunks[handle] = ChunkReference(source_id)
            self._chunk_keys[source_id] = handle
        old = self._chunks[handle]
        for attr in ("knowledge_id", "knowledge_base_id", "document_title", "chunk_index", "chunk_type"):
            if not getattr(old, attr):
                setattr(old, attr, getattr(ref, attr))
        if evidence:
            self._citable.add(handle)
        return handle

    def register_document(self, durable_id: str) -> str:
        return self._register_navigation(durable_id, "d", self._docs)

    def register_knowledge_base(self, durable_id: str) -> str:
        return self._register_navigation(durable_id, "b", self._kbs)

    def _register_navigation(self, durable_id: str, prefix: str, table: dict[str, str]) -> str:
        value = durable_id.strip()
        if not value:
            return ""
        if _HANDLE.fullmatch(value):
            return self._known(value, table)
        if value not in table.values():
            table[f"{prefix}{len(table)+1}"] = value
        return next((h for h, item in table.items() if item == value), "")

    def register_web(self, url: str, title: str = "", *, evidence: bool = True) -> str:
        raw = url.strip()
        if not raw:
            return ""
        if _HANDLE.fullmatch(raw):
            return self._known(raw, self._webs)
        parts = urlsplit(raw)
        canonical = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, "")) if parts.scheme and parts.netloc else raw
        handle = self._web_keys.get(canonical)
        if handle is None:
            handle = f"w{len(self._webs)+1}"
            self._webs[handle] = (raw, title)
            self._web_keys[canonical] = handle
        elif not self._webs[handle][1] and title:
            self._webs[handle] = (self._webs[handle][0], title)
        if evidence:
            self._citable.add(handle)
        return handle

    def register_search_results(self, results) -> None:
        for row in results:
            if row is None:
                continue
            get = row.get if isinstance(row, dict) else lambda key, default=None: getattr(row, key, default)
            self.register_document(str(get("knowledge_id", "") or ""))
            self.register_knowledge_base(str(get("knowledge_base_id", "") or ""))
            self.register_chunk(ChunkReference(
                chunk_id=str(get("id", "") or ""),
                knowledge_id=str(get("knowledge_id", "") or ""),
                knowledge_base_id=str(get("knowledge_base_id", "") or ""),
                document_title=str(get("knowledge_title", "") or get("knowledge_filename", "") or ""),
                chunk_index=int(get("chunk_index", 0) or 0),
                chunk_type=str(get("chunk_type", "") or ""),
            ))

    @staticmethod
    def _public_attr(pattern: re.Pattern, tag: str) -> str:
        match = pattern.search(tag)
        return html.unescape(match.group(1)) if match else ""

    def compact_public_citations(self, text: str, *, evidence: bool = False) -> str:
        """Map replayed public citation tags to private handles.

        Historical assistant/tool messages use the default `evidence=False`;
        only a caller registering an actual current tool result should pass true.
        """
        def compact_chunk(tag: str) -> str:
            tag = tag.group(0)
            chunk_id = self._public_attr(_ATTR["chunk_id"], tag)
            if not chunk_id:
                return tag
            handle = self.register_chunk(ChunkReference(
                chunk_id=chunk_id,
                knowledge_base_id=self._public_attr(_ATTR["kb_id"], tag),
                document_title=self._public_attr(_ATTR["doc"], tag),
            ), evidence=evidence)
            return f'<ref id="{handle}"/>' if handle else tag

        def compact_web(tag: str) -> str:
            tag = tag.group(0)
            url = self._public_attr(_ATTR["url"], tag)
            if not url:
                return tag
            handle = self.register_web(url, self._public_attr(_ATTR["title"], tag), evidence=evidence)
            return f'<ref id="{handle}"/>' if handle else tag

        return _PUBLIC_WEB.sub(compact_web, _PUBLIC_KB.sub(compact_chunk, text))

    def expand_text(self, text: str) -> str:
        text = _KB.sub("", text)
        text = _WEB.sub("", text)
        if not self.citations_enabled:
            return _REF.sub("", text)

        def replace(match: re.Match) -> str:
            tag = match.group(0)
            parsed = _REF_VALID.fullmatch(tag)
            if not parsed:
                self.invalid_handles.append("<malformed-ref>")
                return ""
            handle = parsed.group(1).lower()
            if handle not in self._citable:
                self.invalid_handles.append(handle)
                return ""
            self.used_handles.add(handle)
            chunk = self._chunks.get(handle)
            if chunk:
                attrs = f'doc="{html.escape(chunk.document_title, quote=True)}" chunk_id="{html.escape(chunk.chunk_id, quote=True)}"'
                if chunk.knowledge_base_id:
                    attrs += f' kb_id="{html.escape(chunk.knowledge_base_id, quote=True)}"'
                return f"<kb {attrs} />"
            web = self._webs.get(handle)
            if web:
                return f'<web url="{html.escape(web[0], quote=True)}" title="{html.escape(web[1], quote=True)}" />'
            self.invalid_handles.append(handle)
            return ""

        return _REF.sub(replace, text)

    def encode_resource_text(self, text: str) -> str:
        return self.resources.encode_text(text)

    def decode_output_text(self, text: str) -> str:
        # Upstream order restores durable resource refs before citation expansion.
        text = self.resources.decode_text(text)
        text = self.expand_text(text)
        return _RESOURCE_HANDLE.sub("", text)
