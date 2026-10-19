"""Conservative parsing of one chunk's generated graph extraction.

Exact quotes and surface order provide traceability, not semantic entailment.
No extraction is allowed to supply text absent from the source chunk.
"""

from __future__ import annotations

from dataclasses import asdict, replace
import json
import re

from .types import GraphBatch, GraphEdge, GraphNode, GraphSource, stable_id


MAX_SOURCE_CHARS = 16_384
MAX_OUTPUT_CHARS = 32_768
MAX_NODES = 32
MAX_EDGES = 48
MAX_SURFACE_CHARS = 256
MAX_QUOTE_CHARS = 2_048
EXTRACTION_PROMPT_VERSION = "source-quote-directed-v1"
EXTRACTION_PROMPT = """Extract only explicit affirmative directed relationships from the supplied source text.
Return one strict JSON object, without Markdown or additional fields:
{"entities":[{"key":"e1","name":"exact source surface","type":"entity type","aliases":[],"anchor":false,"qualifier":""}],"relations":[{"source":"e1","target":"e2","predicate":"exact source phrase","quote":"unique exact source substring","polarity":"affirmed","time":""}]}.
Names, aliases and any qualifier must occur verbatim at independent boundaries in this chunk. Do not invent aliases or expand pronouns. Keep identifiers and their punctuation unchanged. The qualifier, when needed to distinguish identities, must be an exact source phrase, not inferred identity.
An anchor is the document subject explicitly named at the start of the first chunk's first sentence; only an initial a/an/the before the subject is allowed. Do not mark mentions later in the text as anchors.
Each quote must occur exactly once in the source. The subject surface must precede the predicate, and the object surface must follow it. Do not reverse a relation to fit an expected answer. Skip negative, uncertain, conditional or modal claims. Preserve temporal qualifications verbatim in time; never turn a past or dated relation into a timeless assertion.
Maximum 32 entities, 48 relationships, 256 characters per name/alias and 2048 characters per quote. Empty arrays are valid when no supported relationship can be extracted. Extracted relations are retrieval hints; quote checks are not a semantic fact verdict.
"""
_ENTITY_FIELDS = {"key", "name", "type", "aliases", "anchor", "qualifier"}
_RELATION_FIELDS = {"source", "target", "predicate", "quote", "polarity", "time"}
_UNCERTAIN = re.compile(
    r"\b(?:no|not|never|neither|without|cannot|uncertain|may|might|could|possibly|perhaps|reportedly|if|unless)\b|n't\b"
    r"|不是|不在|没有|并非|未曾|未能|可能|或许|据说|如果|假如|是否|尚不明确",
    re.IGNORECASE,
)
_TEMPORAL = re.compile(
    r"\b(?:formerly|previously|currently|until|since|during|before|after|as of)\b"
    r"|\b(?:in|on|from)\s+(?:\d{4}|\d{1,2}[/.-]\d{1,2})\b|\b\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?\b"
    r"|\d{4}年|曾经|此前|目前|现任|截至|自从|期间|之前|之后",
    re.IGNORECASE,
)


class GraphExtractionError(ValueError):
    """The bounded extraction envelope is structurally invalid."""


def _strict_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise GraphExtractionError("Duplicate JSON key")
        result[key] = value
    return result


def _text(value: object, limit: int, *, empty: bool = False) -> bool:
    return isinstance(value, str) and len(value) <= limit and (empty or bool(value.strip()))


def _occurrences(text: str, surface: str) -> list[tuple[int, int]]:
    """Exact, case-sensitive surface matches, with Unicode word boundaries."""
    if not surface:
        return []
    result = []
    for match in re.finditer(re.escape(surface), text):
        start, end = match.span()
        if surface[0].isalnum() and start and (text[start - 1].isalnum() or text[start - 1] in "_-'’"):
            continue
        if surface[-1].isalnum() and end < len(text) and (text[end].isalnum() or text[end] in "_-'’"):
            continue
        result.append((start, end))
    return result


def _surrounding_statement(source_text: str, start: int, end: int) -> str:
    """Keep the enclosing sentence/line, so a quote cannot trim off 'not'."""
    delimiters = ".!?。！？\n"
    left = max((source_text.rfind(char, 0, start) for char in delimiters), default=-1) + 1
    right = end if end and source_text[end - 1] in delimiters else min(
        (position for char in delimiters if (position := source_text.find(char, end)) >= 0),
        default=len(source_text),
    )
    return source_text[left:right]


def _is_anchor(source: GraphSource, name: str) -> bool:
    # Unknown image offsets 0/0 cannot establish first-document-chunk identity.
    if source.start_at != 0 or source.end_at == 0:
        return False
    whitespace = re.match(r"\s*", source.content)
    article = re.match(r"\s*(?:a|an|the)\s+", source.content, re.IGNORECASE)
    allowed = {whitespace.end()}
    if article:
        allowed.add(article.end())
    return any(start in allowed for start, _ in _occurrences(source.content, name))


def build_extraction_payload(source: GraphSource) -> dict:
    """Build model messages without rewriting or truncating source content."""
    if not source.content.strip() or len(source.content) > MAX_SOURCE_CHARS:
        raise GraphExtractionError("Empty or oversized graph source")
    return {"messages": [
        {"role": "system", "content": EXTRACTION_PROMPT},
        {"role": "user", "content": json.dumps(asdict(source), ensure_ascii=False)},
    ]}


def build_extraction_messages(source: GraphSource) -> list[dict[str, str]]:
    return build_extraction_payload(source)["messages"]


def parse_extraction(source: GraphSource, text: str, extraction_id: str) -> GraphBatch:
    if not _text(extraction_id, 512) or not isinstance(text, str) or len(text) > MAX_OUTPUT_CHARS:
        raise GraphExtractionError("Invalid extraction identity or output length")
    if len(source.content) > MAX_SOURCE_CHARS:
        raise GraphExtractionError("Oversized graph source")
    try:
        payload = json.loads(text, object_pairs_hook=_strict_object,
                             parse_constant=lambda _: (_ for _ in ()).throw(GraphExtractionError("Non-finite JSON value")))
    except (json.JSONDecodeError, RecursionError) as exc:
        raise GraphExtractionError("Invalid extraction JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"entities", "relations"}:
        raise GraphExtractionError("Expected entities and relations only")
    entities, relations = payload["entities"], payload["relations"]
    if not isinstance(entities, list) or not isinstance(relations, list) or len(entities) > MAX_NODES or len(relations) > MAX_EDGES:
        raise GraphExtractionError("Invalid or oversized entity/relation lists")

    rejected: list[dict] = []
    nodes: dict[str, GraphNode] = {}
    by_key: dict[str, GraphNode] = {}
    scope = (source.tenant_id, source.owner_id, source.knowledge_base_id, source.document_id, source.source_version, source.chunk_id)
    for index, entity in enumerate(entities):
        reason = None
        if not isinstance(entity, dict) or set(entity) != _ENTITY_FIELDS:
            reason = "ENTITY_FIELDS"
        elif not all(_text(entity[k], MAX_SURFACE_CHARS, empty=k == "qualifier") for k in ("key", "name", "type", "qualifier")):
            reason = "ENTITY_TEXT"
        elif type(entity["anchor"]) is not bool or not isinstance(entity["aliases"], list) or len(entity["aliases"]) > 4 or not all(_text(v, MAX_SURFACE_CHARS) for v in entity["aliases"]):
            reason = "ENTITY_TYPES"
        elif entity["key"] in by_key:
            reason = "DUPLICATE_KEY"
        elif not all(_occurrences(source.content, value) for value in (entity["name"], *entity["aliases"], *([entity["qualifier"]] if entity["qualifier"] else []))):
            reason = "SURFACE_NOT_IN_SOURCE"
        if reason:
            rejected.append({"kind": "entity", "index": index, "reason": reason})
            continue
        aliases = tuple(dict.fromkeys(entity["aliases"]))
        node_id = stable_id("gn", *scope, entity["name"], entity["type"], entity["qualifier"])
        node = GraphNode(node_id, entity["key"], entity["name"], entity["type"], aliases,
                         entity["anchor"] and _is_anchor(source, entity["name"]), entity["qualifier"])
        previous = nodes.get(node_id)
        if previous is not None:
            node = replace(previous, aliases=tuple(dict.fromkeys((*previous.aliases, *aliases))),
                           is_anchor=previous.is_anchor or node.is_anchor)
        nodes[node_id] = node
        by_key[entity["key"]] = node

    edges: dict[str, GraphEdge] = {}
    for index, relation in enumerate(relations):
        reason = None
        if not isinstance(relation, dict) or set(relation) != _RELATION_FIELDS:
            reason = "RELATION_FIELDS"
        elif not all(_text(relation[k], MAX_QUOTE_CHARS if k == "quote" else MAX_SURFACE_CHARS, empty=k == "time") for k in _RELATION_FIELDS):
            reason = "RELATION_TEXT"
        elif relation["source"] not in by_key or relation["target"] not in by_key:
            reason = "UNKNOWN_ENDPOINT"
        elif by_key[relation["source"]].id == by_key[relation["target"]].id:
            reason = "SELF_RELATION"
        elif relation["polarity"] != "affirmed":
            reason = "NOT_AFFIRMED"
        elif _UNCERTAIN.search(relation["quote"]):
            reason = "NEGATED_UNCERTAIN_OR_CONDITIONAL"
        elif source.content.count(relation["quote"]) != 1:
            reason = "QUOTE_NOT_UNIQUE_IN_SOURCE"
        elif relation["time"] and relation["time"] not in relation["quote"]:
            reason = "TIME_NOT_IN_QUOTE"
        elif _TEMPORAL.search(relation["quote"]) and not relation["time"]:
            reason = "TIME_QUALIFIER_MISSING"
        if reason:
            rejected.append({"kind": "relation", "index": index, "reason": reason})
            continue
        subject, target = by_key[relation["source"]], by_key[relation["target"]]
        quote, predicate = relation["quote"], relation["predicate"]
        start = source.content.index(quote)
        statement = _surrounding_statement(source.content, start, start + len(quote))
        if _UNCERTAIN.search(statement):
            rejected.append({"kind": "relation", "index": index, "reason": "SURROUNDING_NEGATION_OR_UNCERTAINTY"})
            continue
        if _TEMPORAL.search(statement) and not _TEMPORAL.search(quote):
            rejected.append({"kind": "relation", "index": index, "reason": "TEMPORAL_SCOPE_OUTSIDE_QUOTE"})
            continue
        if relation["time"]:
            time_spans = [(match.start(), match.end()) for match in re.finditer(re.escape(relation["time"]), quote)]
            if any(not any(start <= cue.start() and cue.end() <= end for start, end in time_spans)
                   for cue in _TEMPORAL.finditer(quote)):
                rejected.append({"kind": "relation", "index": index, "reason": "INCOMPLETE_TIME_QUALIFIER"})
                continue
        predicates = _occurrences(quote, predicate)
        subjects = [span for surface in (subject.name, *subject.aliases) for span in _occurrences(quote, surface)]
        objects = [span for surface in (target.name, *target.aliases) for span in _occurrences(quote, surface)]
        if not any(s_end <= p_start and p_end <= o_start
                   for _, s_end in subjects for p_start, p_end in predicates for o_start, _ in objects):
            rejected.append({"kind": "relation", "index": index, "reason": "DIRECTION_OR_PREDICATE_NOT_IN_QUOTE"})
            continue
        edge_id = stable_id("ge", *scope, subject.id, target.id, predicate, quote, relation["time"])
        edges[edge_id] = GraphEdge(edge_id, subject.id, target.id, predicate, quote, start,
                                   start + len(quote), relation["time"])
    return GraphBatch(source, tuple(nodes.values()), tuple(edges.values()), tuple(rejected), extraction_id)


def validate_batch_proofs(batch: GraphBatch) -> None:
    """Re-run the same source checks for retained records at a storage boundary.

Historical rejected suggestions are intentionally ignored: they are not retained
graph evidence. Stable IDs, labels, direction, offsets and the verification label
must all match a fresh parse of the retained subset.
"""
    if not isinstance(batch, GraphBatch) or not isinstance(batch.source, GraphSource):
        raise GraphExtractionError("Invalid graph batch/source type")
    if any(not isinstance(node, GraphNode) for node in batch.nodes) or any(not isinstance(edge, GraphEdge) for edge in batch.edges):
        raise GraphExtractionError("Invalid retained graph record type")
    node_ids = [node.id for node in batch.nodes]
    edge_ids = [edge.id for edge in batch.edges]
    if len(set(node_ids)) != len(node_ids) or len(set(edge_ids)) != len(edge_ids):
        raise GraphExtractionError("Duplicate retained node/edge identity")
    local_keys = {node.id: f"e{index}" for index, node in enumerate(batch.nodes)}
    if any(edge.source_id not in local_keys or edge.target_id not in local_keys for edge in batch.edges):
        raise GraphExtractionError("Retained graph endpoint is missing")
    payload = {
        "entities": [
            {"key": local_keys[node.id], "name": node.name, "type": node.entity_type,
             "aliases": list(node.aliases), "anchor": node.is_anchor, "qualifier": node.identity_qualifier}
            for node in batch.nodes
        ],
        "relations": [
            {"source": local_keys[edge.source_id], "target": local_keys[edge.target_id],
             "predicate": edge.predicate, "quote": edge.quote, "polarity": "affirmed", "time": edge.temporal_text}
            for edge in batch.edges
        ],
    }
    replay = parse_extraction(batch.source, json.dumps(payload, ensure_ascii=False), batch.extraction_id)
    if replay.rejected:
        raise GraphExtractionError("Retained graph records failed source proof replay")
    expected_nodes = {node.id: {key: value for key, value in asdict(node).items() if key != "local_key"} for node in batch.nodes}
    actual_nodes = {node.id: {key: value for key, value in asdict(node).items() if key != "local_key"} for node in replay.nodes}
    if expected_nodes != actual_nodes:
        raise GraphExtractionError("Retained node identity or source fields changed on replay")
    if {edge.id: asdict(edge) for edge in batch.edges} != {edge.id: asdict(edge) for edge in replay.edges}:
        raise GraphExtractionError("Retained edge identity, offsets or verification changed on replay")
