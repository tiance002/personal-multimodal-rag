"""Neutral pipeline data, ported from WeKnora types/chat_manage/search types."""
from dataclasses import dataclass, field
from copy import deepcopy
from typing import Any


@dataclass
class SearchResult:
    id: str
    content: str
    score: float
    knowledge_id: str = ""
    knowledge_base_id: str = ""
    chunk_type: str = "text"
    chunk_index: int = 0
    start_at: int = 0
    end_at: int = 0
    parent_chunk_id: str = ""
    pre_chunk_id: str = ""
    next_chunk_id: str = ""
    match_type: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    knowledge_source: str = ""
    knowledge_title: str = ""
    knowledge_filename: str = ""
    knowledge_description: str = ""
    knowledge_custom_metadata: str = ""
    image_info: str = ""
    chunk_metadata: dict[str, Any] = field(default_factory=dict)
    sub_chunk_ids: list[str] = field(default_factory=list)
    content_rewritten: bool = False
    content_revision: int = 0
    is_enabled: bool = True
    matched_content: str = ""
    seq: int = 0
    knowledge_channel: str = ""
    # Persisted source breadcrumb; never infer it from matched/indexed text.
    context_header: str = ""


def context_results(state):
    """Original normal order followed by independent, verified supplements."""
    normal = state.merge_results
    rescue = getattr(state, "rescue_evidence", ())
    identity = getattr(state, "bridge_identity_evidence", ())
    qualifications = getattr(state, "bridge_normal_qualifications", {})
    if qualifications:
        if getattr(state, "bridge_rescue_mode", "off") != "cheap_bridge_v1":
            raise ValueError("BRIDGE_EVIDENCE_MODE_MISMATCH")
        views = []
        for row in normal:
            matching = next((details for source_id, details in qualifications.items()
                if row is not None and row.chunk_type == 'text' and row.is_enabled and
                source_id in (row.id, *row.sub_chunk_ids) and
                row.knowledge_id == details.get('knowledge_id') and
                row.knowledge_base_id == details.get('knowledge_base_id') and
                row.metadata.get('source_version') == details.get('source_version') and
                row.chunk_metadata.get('source_version') == details.get('source_version') and
                details.get('quote') and details['quote'] in row.content), None)
            if matching is not None:
                view = deepcopy(row)
                view.metadata['bridge_normal_qualification'] = deepcopy(matching)
                views.append(view)
            else:
                views.append(row)
        normal = views
    if not rescue and not identity:
        return normal
    if getattr(state, "bridge_rescue_mode", "off") != "cheap_bridge_v1":
        raise ValueError("BRIDGE_EVIDENCE_MODE_MISMATCH")
    rows = [*normal, *identity, *rescue]
    ids = [row.id for row in rows if row is not None]
    if len(ids) != len(set(ids)):
        raise ValueError("BRIDGE_EVIDENCE_DUPLICATE")
    return rows


def bridge_context_attributes(row):
    """Program evidence qualifications shared by both Context encoders."""
    if (row.metadata or {}).get('bridge_identity'):
        return ' role="bridge_identity" supports="subject_identity"'
    details = (row.metadata or {}).get('bridge_rescue')
    if not isinstance(details, dict) or not details:
        normal = (row.metadata or {}).get('bridge_normal_qualification')
        if not isinstance(normal, dict) or not normal:
            return ''
        # A shortened-name fact already present in normal evidence is still
        # only an institution hint, never an admitted event/entity alias.
        return (' role="bridge_location_hint" supports="institution_location_only"'
                ' seminar_venue="unverified" final_answer_supported="false"')
    exact = details.get('entity_bridge_verified') is True
    supported = details.get('final_answer_supported') is True
    return (' role="bridge_supplement" supports="' +
            ('entity_location' if exact else 'institution_location_only') +
            '" seminar_venue="' + ('supported' if exact and supported else 'unverified') +
            '" final_answer_supported="' + ('true' if supported else 'false') + '"')


@dataclass
class SearchState:
    needs_retrieval: bool = True
    rerank_top_k: int = 30
    search_results: list[SearchResult | None] = field(default_factory=list)
    rerank_results: list[SearchResult | None] = field(default_factory=list)
    merge_results: list[SearchResult | None] = field(default_factory=list)
    bridge_identity_evidence: list[SearchResult] = field(default_factory=list, kw_only=True)
