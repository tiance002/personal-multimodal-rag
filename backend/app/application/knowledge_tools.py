from __future__ import annotations

from dataclasses import dataclass
import hashlib
import threading
from typing import Any, Callable

from backend.app.application.evidence_accumulator import EvidenceAccumulator
from backend.app.application.knowledge_gateway import KnowledgeGateway
from backend.app.application.retrieval import HybridRetriever
from backend.app.domain.agent_policy import ALLOWED_READ_TOOLS
from backend.app.domain.errors import ScopeViolation
from backend.app.domain.scope import Scope

class ToolDenied(PermissionError):
    pass


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    data: Any


class KnowledgeToolGateway:
    """Closed set of read-only knowledge tools, wired with explicit dependencies.

    Each capability is injected rather than discovered at runtime, so the
    supported surface is visible from the constructor and a missing capability
    fails loudly instead of silently returning an empty result.
    """

    def __init__(
        self,
        *,
        retriever: HybridRetriever | None = None,
        knowledge_gateway: KnowledgeGateway | None = None,
        content_reader: Callable[[str], str] | None = None,
        document_lister: Callable[[str], list[dict[str, Any]]] | None = None,
        document_resolver: Callable[[str], dict[str, Any] | None] | None = None,
        graph_query: Callable[[Scope, str, int], Any] | None = None,
        evidence_accumulator: EvidenceAccumulator | None = None,
        on_retrieval: Callable[[Any], None] | None = None,
        max_document_chars: int = 8_000,
        document_view_reader: Callable[..., dict[str, Any]] | None = None,
        max_read_calls: int = 8,
        max_read_chars: int = 24_000,
        max_read_tokens: int = 8_000,
        query_understanding: Any = None,
    ) -> None:
        self.knowledge_gateway = knowledge_gateway or (KnowledgeGateway(retriever) if retriever is not None else None)
        self.retriever = retriever or (self.knowledge_gateway.retriever if self.knowledge_gateway is not None else None)
        self.content_reader = content_reader
        self.document_lister = document_lister
        self.document_resolver = document_resolver
        self.graph_query = graph_query
        self.evidence_accumulator = evidence_accumulator
        self.on_retrieval = on_retrieval
        self.query_understanding = query_understanding
        self.max_document_chars = max(1, max_document_chars)
        self.document_view_reader = document_view_reader
        self.max_read_calls,self.max_read_chars,self.max_read_tokens=max_read_calls,max_read_chars,max_read_tokens
        if any(type(n) is not int or n<1 for n in (max_read_calls,max_read_chars,max_read_tokens)):
            raise ValueError('DOCUMENT_READ_LIMIT_INVALID')
        self._read_lock=threading.RLock()
        self._read_bindings={};self._read_ranges={};self._read_calls=0;self._read_chars=0;self._read_tokens=0
        self._read_counter=None

    def bind_read_token_counter(self, counter):
        # A gateway is created per Smart Run, not shared as permanent history.
        # Actual model protocol counter; no character/token heuristic fallback.
        self._read_counter=counter

    def bind_read_run(self, run_id):
        if getattr(self,'_read_run_id',None)!=run_id:
            self._read_bindings={};self._read_ranges={};self._read_digests={}
            self._read_calls=0;self._read_chars=0;self._read_tokens=0
            self._read_run_id=run_id

    def invoke(self, tool_name: str, args: dict[str, Any], scope: Scope) -> ToolResult:
        if tool_name not in ALLOWED_READ_TOOLS:
            raise ToolDenied("TOOL_NOT_ALLOWED")
        try:
            if tool_name == "list_documents":
                return ToolResult(tool_name, {"items": self._list_documents(args, scope)})
            if tool_name == "search_knowledge":
                return ToolResult(tool_name, {"items": self._search_knowledge(args, scope)})
            if tool_name == "read_document":
                return ToolResult(tool_name, self._read_document(args, scope))
            if tool_name == "query_knowledge_graph":
                return ToolResult(tool_name, {"items": self._query_graph(args, scope)})
        except ScopeViolation as exc:
            raise ToolDenied("SCOPE_VIOLATION") from exc
        raise ToolDenied("TOOL_NOT_ALLOWED")

    def _list_documents(self, args: dict[str, Any], scope: Scope) -> list[dict[str, Any]]:
        if self.document_lister is None:
            raise ToolDenied("DOCUMENT_LIST_UNAVAILABLE")
        limit = max(1, min(int(args.get("limit", 50)), 100))
        items: list[dict[str, Any]] = []
        for knowledge_base_id in sorted(scope.knowledge_base_ids):
            items.extend(self.document_lister(knowledge_base_id))
        if scope.document_ids:
            items = [item for item in items if str(item.get("id")) in scope.document_ids]
        return items[:limit]

    def _search_knowledge(self, args: dict[str, Any], scope: Scope) -> list[dict[str, Any]]:
        if self.knowledge_gateway is None:
            return []
        query = str(args.get("query", args.get("question", "")))
        understanding=self.query_understanding
        if understanding is not None and query not in {understanding.q0,understanding.rewrite_query}:
            understanding=None
        plan, result = self.knowledge_gateway.retrieve_question(scope, query,understanding=understanding)
        selected = list(self.knowledge_gateway.evidence.bundle(plan, result).selected)
        if self.on_retrieval is not None:
            self.on_retrieval(selected)
        if self.evidence_accumulator is not None:
            self.evidence_accumulator.add(item.chunk for item in selected)
        return [
            {
                "chunk_id": item.chunk.chunk_id,
                "document_id": item.chunk.document_id,
                "version_id": item.chunk.version_id,
                "locator": item.chunk.locator,
                "quote": item.chunk.content,
                **({"citation_label": self.evidence_accumulator.label_for(item.chunk)} if self.evidence_accumulator is not None else {}),
            }
            for item in selected
        ]

    def _read_document(self, args: dict[str, Any], scope: Scope) -> dict[str, Any]:
        with self._read_lock:
            return self._read_page(args,scope)

    def _read_page(self, args: dict[str, Any], scope: Scope) -> dict[str, Any]:
        document_id = str(args.get("document_id", ""))
        if self.content_reader is None:
            raise ToolDenied("DOCUMENT_NOT_AVAILABLE")
        if not document_id:
            raise ToolDenied("DOCUMENT_NOT_AVAILABLE")
        if self.document_resolver is None:
            supplied_kb = str(args.get("knowledge_base_id", ""))
            scope.assert_contains(supplied_kb, document_id)
            raise ToolDenied("DOCUMENT_NOT_AVAILABLE")
        document = self.document_resolver(document_id)
        if not document or document.get("deleted_at") is not None:
            raise ToolDenied("DOCUMENT_NOT_AVAILABLE")
        knowledge_base_id = str(document.get("knowledge_base_id", ""))
        supplied_kb = str(args.get("knowledge_base_id", ""))
        if supplied_kb and supplied_kb != knowledge_base_id:
            raise ToolDenied("SCOPE_VIOLATION")
        scope.assert_contains(knowledge_base_id, document_id)
        active_version_id = str(document.get("active_version_id", ""))
        if not active_version_id or document.get("index_status") != "ready":
            raise ToolDenied("DOCUMENT_NOT_READY")
        requested_version_id = str(args.get("version_id", ""))
        if requested_version_id and requested_version_id != active_version_id:
            raise ToolDenied("VERSION_NOT_AUTHORIZED")
        offset=args.get('offset',0);length=args.get('length',self.max_document_chars)
        if type(offset) is not int or offset<0 or type(length) is not int or not 1<=length<=self.max_document_chars:
            raise ToolDenied('DOCUMENT_POSITION_INVALID')
        binding=(active_version_id,tuple(sorted(scope.knowledge_base_ids)),tuple(sorted(scope.document_ids)))
        if document_id in self._read_bindings and self._read_bindings[document_id]!=binding:
            raise ToolDenied('DOCUMENT_READING_IDENTITY_CHANGED')
        view=(self.document_view_reader(scope,document_id,active_version_id) if self.document_view_reader else
              {'version_id':active_version_id,'content':self.content_reader(document_id),'protected_spans':[]})
        if not view or view['version_id']!=active_version_id:
            raise ToolDenied('DOCUMENT_READING_IDENTITY_CHANGED')
        content=view['content']
        # A version switch/deletion during reading cannot release stale text.
        after=self.document_resolver(document_id)
        if (not after or after.get('deleted_at') is not None or str(after.get('active_version_id',''))!=active_version_id
                or str(after.get('knowledge_base_id',''))!=knowledge_base_id or after.get('index_status')!='ready'):
            raise ToolDenied('DOCUMENT_READING_IDENTITY_CHANGED')
        if offset>=len(content) and offset!=0:raise ToolDenied('DOCUMENT_POSITION_INVALID')
        end=min(len(content),offset+length)
        # Keep atomic evidence context whole; an oversized atomic unit is denied
        # rather than silently chopped into misleading table/asset proof.
        for start,stop in view.get('protected_spans',()):
            if start<offset<stop:raise ToolDenied('DOCUMENT_ATOMIC_OFFSET_INVALID')
            if start<end<stop:end=start
        if end<=offset:raise ToolDenied('DOCUMENT_ATOMIC_PAGE_TOO_LARGE')
        # Prefer a paragraph/line boundary while guaranteeing forward progress.
        boundary=content.rfind('\n',offset,end)
        if end<len(content) and boundary>=offset and not any(a<boundary+1<b for a,b in view.get('protected_spans',())):
            end=boundary+1
        if any(offset<old_end and end>old_start for old_start,old_end in self._read_ranges.get(document_id,())):
            raise ToolDenied('DOCUMENT_RANGE_ALREADY_READ')
        if self._read_calls>=self.max_read_calls or self._read_chars+end-offset>self.max_read_chars:
            raise ToolDenied('DOCUMENT_RUN_READ_LIMIT')
        result={
            "document_id": document_id,
            "version_id": active_version_id,
            "content": content[offset:end],
            "truncated": end<len(content),
            "start_offset":offset,"end_offset":end,"has_more":end<len(content),
            "next_offset":end if end<len(content) else None,
            "offset_basis":"child-order-reading-projection/characters",
            "evidence_status":"READING_CONTEXT_ONLY_SEARCH_CHILD_FOR_CITATION",
            "read_calls":self._read_calls+1,"read_characters":self._read_chars+end-offset,
            "token_budget_status":"MODEL_BOUND_COUNTER" if self._read_counter is not None else 'UNKNOWN',
        }
        token_count=None
        if self._read_counter is not None:
            token_count=self._read_counter(result)
            if type(token_count) is not int or token_count<0 or self._read_tokens+token_count>self.max_read_tokens:
                raise ToolDenied('DOCUMENT_RUN_TOKEN_LIMIT')
        elif offset:
            raise ToolDenied('DOCUMENT_TOKEN_CAPACITY_UNKNOWN')
        # Immutable version alone normally suffices; injected legacy readers
        # also get a content digest fence against changing same-version text.
        digest=hashlib.sha256(content.encode('utf8')).hexdigest()
        if document_id in self._read_bindings and self._read_bindings[document_id]!=binding:
            raise ToolDenied('DOCUMENT_READING_IDENTITY_CHANGED')
        old_digest=getattr(self,'_read_digests',{}).get(document_id)
        if old_digest and old_digest!=digest:raise ToolDenied('DOCUMENT_READING_CONTENT_CHANGED')
        if not hasattr(self,'_read_digests'):self._read_digests={}
        self._read_bindings[document_id]=binding;self._read_digests[document_id]=digest
        self._read_ranges.setdefault(document_id,[]).append((offset,end))
        self._read_calls+=1;self._read_chars+=end-offset;self._read_tokens+=token_count or 0
        return result

    def _query_graph(self, args: dict[str, Any], scope: Scope) -> list[Any]:
        if self.graph_query is None:
            raise ToolDenied("GRAPH_UNAVAILABLE")
        result = self.graph_query(scope, str(args.get("entity_name", "")), int(args.get("depth", 1)))
        return [getattr(edge, "__dict__", edge) for edge in result]
