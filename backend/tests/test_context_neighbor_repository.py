"""SIMULATED actual-method AST capture; SQL compilation/execution NOT RUN.

Full repository import is blocked under the unchanged offline guard by the
SQLAlchemy Windows platform probe. This narrower test never imports it or
changes the guard: method bodies/constants are extracted verbatim from source.
"""
import ast
import hashlib
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from backend.app.application.context_expansion import NeighborMetadata
from backend.app.domain.errors import ScopeViolation
from backend.app.domain.models import ChunkRecord
from backend.app.domain.scope import Scope
from backend.app.ports import retrieval as ports


def actual_repository_class():
    path = Path(__file__).parents[1] / "app/adapters/postgres/knowledge_repository.py"
    source = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    original = next(node for node in source.body
                    if isinstance(node, ast.ClassDef) and node.name == "PostgresKnowledgeRepository")
    names = {"_scope_filter", "_ACTIVE_VERSION_JOINS", "_CONTEXT_BOUNDARY_FILTER",
             "_project_context_row", "read_context_rows"}
    body = [node for node in original.body if (
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
        or isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in names
                                               for target in node.targets))]
    extracted = ast.ClassDef(name=original.name, bases=[], keywords=[], body=body, decorator_list=[])
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
                              extracted], type_ignores=[])
    namespace = dict(uuid=uuid, hashlib=hashlib, NeighborMetadata=NeighborMetadata,
                     ChunkRecord=ChunkRecord, Scope=Scope, text=lambda sql: sql,
                     ContextNeighborRow=getattr(ports, "ContextNeighborRow", None),
                     ContextNeighborRead=getattr(ports, "ContextNeighborRead", None),
                     ContextNeighborReason=getattr(ports, "ContextNeighborReason", None))
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[original.name]


def uid(value):
    return str(uuid.UUID(int=value))


KB, DOC, VERSION = uid(1), uid(2), uid(3)
SCOPE = Scope.from_ids([KB], [DOC])


def seed(cid=uid(4), **changes):
    content = "命中原文"
    chunk = ChunkRecord(cid, KB, DOC, VERSION, content,
                        {"kind": "text", "start": 300, "end": 304, "quote": content},
                        content_sha256=hashlib.sha256(content.encode()).hexdigest())
    return replace(chunk, **changes)


def joined_row(original, offset=0, order=0, **changes):
    content = original.content if offset == 0 else "邻接原文"
    start = (3 + offset) * 100
    locator = original.locator if offset == 0 else {
        "kind": "text", "start": start, "end": start + len(content), "quote": content}
    row = dict(seed_order=order, seed_id=original.chunk_id, seed_index=3, offset=offset,
               chunk_id=original.chunk_id if offset == 0 else uid(5 if offset == -1 else 6),
               knowledge_base_id=KB, document_id=DOC, version_id=VERSION,
               chunk_index=3 + offset, content=content, locator=locator,
               content_sha256=hashlib.sha256(content.encode()).hexdigest(),
               start_pos=start, end_pos=start+len(content), chunk_type="text",
               active_version_id=VERSION, index_status="ready", document_deleted=False,
               section_id=uid(7), section_knowledge_base_id=KB,
               section_document_id=DOC, section_version_id=VERSION,
               section_start=0, section_end=1000, section_page_start=None, section_page_end=None)
    row.update(changes)
    return row


class CaptureRows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class CaptureEngine:
    """No SQLAlchemy engine, driver or connection: records generated TextClause."""
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.connections = 0

    def connect(self):
        self.connections += 1
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, statement, params):
        self.calls.append((statement, dict(params)))
        return CaptureRows(self.rows)


def repository(rows):
    from backend.app.domain.adaptive_chunking import ChunkingConfig
    repo = object.__new__(actual_repository_class())
    repo.engine = CaptureEngine(rows)
    repo.chunking_config = ChunkingConfig()
    return repo


def read(repo, seeds, scope=SCOPE, **options):
    method = getattr(repo, "read_context_rows", None)
    assert callable(method), "optional context neighbor reader not implemented"
    return method(scope, seeds, **options)


def neighbors(result):
    return [row.metadata.chunk for row in result.rows if row.offset != 0]


def reasons(result):
    return {reason.code for reason in result.reasons}


def test_optional_contract_does_not_expand_required_retrieval_protocol():
    assert not hasattr(ports.RetrievalRepository, "read_context_rows")
    assert hasattr(ports, "ContextNeighborReader"), "optional reader protocol missing"
    assert hasattr(ports.ContextNeighborReader, "read_context_rows")


def test_bound_query_scopes_seeds_and_neighbors_and_preserves_seed_object():
    original = seed()
    repo = repository([joined_row(original, 1), joined_row(original), joined_row(original, -1)])
    result = read(repo, [original])
    assert [row.offset for row in result.rows] == [-1, 0, 1]
    assert result.rows[1].metadata.chunk is original
    assert result.rows[1].metadata.chunk.locator is original.locator
    assert [chunk.content for chunk in neighbors(result)] == ["邻接原文", "邻接原文"]
    assert all(row.metadata.chunk.is_current is True for row in result.rows)
    statement, params = repo.engine.calls[0]
    sql = str(statement)
    assert sql.lstrip().startswith("WITH requested")  # Real generated text; no SQL compilation claim.
    assert sql.count("d.active_version_id = c.version_id") == 2
    assert sql.count("dv.index_status='ready'") == 2
    assert sql.count("d.deleted_at IS NULL") == 2
    assert sql.count("d.knowledge_base_id = c.knowledge_base_id") == 2
    assert sql.count("c.knowledge_base_id IN (:kb_0)") == 2
    assert sql.count("c.document_id IN (:doc_0)") == 2
    assert "kb.deleted_at IS NULL" in sql
    assert "c.chunk_index = eligible.seed_index + offsets.offset" in sql
    assert "VALUES (-1), (0), (1)" in sql
    assert 'AS offsets("offset")' in sql  # OFFSET is a PostgreSQL reserved keyword.
    assert "section.document_id = c.document_id" in sql
    assert "section.version_id = c.version_id" in sql
    assert "section.knowledge_base_id = c.knowledge_base_id" in sql
    assert sql.count("c.start_pos >= section.start_pos") == 2
    assert sql.count("c.end_pos <= section.end_pos") == 2
    assert "c.section_id = eligible.section_id" in sql
    assert "c.locator->'start' = to_jsonb(c.start_pos)" in sql
    assert "LIMIT :row_limit" in sql
    assert params["row_limit"] == 3
    assert params["seed_0_id"] == original.chunk_id
    assert params["seed_0_version"] == VERSION
    assert params["seed_0_hash"] == original.content_sha256
    assert params["kb_0"] == KB and params["doc_0"] == DOC
    assert original.content not in sql and original.content not in params.values()


@pytest.mark.parametrize("changes,expected", [
    ({"knowledge_base_id": uid(20)}, "IDENTITY_MISMATCH"),
    ({"document_id": uid(21)}, "IDENTITY_MISMATCH"),
    ({"version_id": uid(22)}, "IDENTITY_MISMATCH"),
    ({"active_version_id": uid(23)}, "INACTIVE_VERSION"),
    ({"index_status": "processing"}, "INDEX_NOT_READY"),
    ({"document_deleted": True}, "DOCUMENT_DELETED"),
    ({"document_deleted": None}, "MISSING_METADATA"),
    ({"section_id": None}, "MISSING_METADATA"),
    ({"section_id": " "}, "INVALID_METADATA"),
    ({"section_document_id": uid(25)}, "SECTION_IDENTITY_MISMATCH"),
    ({"section_version_id": uid(26)}, "SECTION_IDENTITY_MISMATCH"),
    ({"section_knowledge_base_id": uid(27)}, "SECTION_IDENTITY_MISMATCH"),
    ({"section_end": 402}, "UNRELIABLE_BOUNDARY"),
    ({"start_pos": 399}, "UNRELIABLE_BOUNDARY"),
    ({"chunk_index": 5}, "NOT_ADJACENT"),
    ({"chunk_type": "table"}, "UNSUPPORTED_TYPE"),
    ({"chunk_type": "image_ocr"}, "UNSUPPORTED_TYPE"),
    ({"content_sha256": "0" * 64}, "INVALID_EVIDENCE"),
    ({"locator": {}}, "INVALID_EVIDENCE"),
])
def test_projection_rejects_unsafe_joined_neighbor_without_seed_loss(changes, expected):
    original = seed()
    repo = repository([joined_row(original), joined_row(original, 1, **changes)])
    result = read(repo, [original])
    assert neighbors(result) == []
    assert expected in reasons(result)
    assert result.rows[0].metadata.chunk is original


def test_missing_seed_readback_never_authorizes_a_neighbor():
    original = seed()
    result = read(repository([joined_row(original, 1)]), [original])
    assert result.rows == ()
    assert "SEED_NOT_ELIGIBLE_OR_BOUNDARY_UNKNOWN" in reasons(result)


@pytest.mark.parametrize("changes", [
    {"content": "变化原文"}, {"locator": {"kind": "text", "start": 300, "end": 304}},
    {"content_sha256": "0" * 64}, {"active_version_id": uid(30)},
])
def test_changed_or_inactive_seed_readback_blocks_entire_group(changes):
    original = seed()
    result = read(repository([joined_row(original, **changes), joined_row(original, 1)]), [original])
    assert result.rows == ()
    assert reasons(result)


def test_missing_neighbors_is_explicit_no_fallback():
    original = seed()
    result = read(repository([joined_row(original)]), [original])
    assert result.rows[0].metadata.chunk is original
    assert neighbors(result) == []
    assert "NO_ELIGIBLE_NEIGHBOR" in reasons(result)


@pytest.mark.parametrize("scope", [Scope.from_ids([uid(40)]), Scope.from_ids([KB], [uid(41)])])
def test_foreign_seed_scope_rejected_before_capture_connection(scope):
    repo = repository([])
    with pytest.raises(ScopeViolation):
        read(repo, [seed()], scope)
    assert repo.engine.connections == 0


def test_noncurrent_seed_input_is_not_permission_to_read():
    repo = repository([])
    result = read(repo, [seed(is_current=False)])
    assert result.rows == () and "INACTIVE_SEED_INPUT" in reasons(result)
    assert repo.engine.connections == 0


@pytest.mark.parametrize("limit", [0, -1, True, 11, "10", 1.5])
def test_invalid_explicit_seed_limit_rejected_before_connection(limit):
    repo = repository([])
    with pytest.raises(ValueError):
        read(repo, [seed()], max_seeds=limit)
    assert repo.engine.connections == 0


def test_input_bound_and_duplicate_identity_rejected_before_connection():
    repo = repository([])
    with pytest.raises(ValueError):
        read(repo, [seed(uid(50+i)) for i in range(11)])
    with pytest.raises(ValueError):
        read(repo, [seed(), seed()])
    assert repo.engine.connections == 0


def test_empty_request_does_not_open_connection():
    repo = repository([])
    result = read(repo, [])
    assert result.rows == ()
    assert repo.engine.connections == 0


def test_impossible_row_overflow_fails_closed():
    original = seed()
    repo = repository([joined_row(original)] * 4)
    with pytest.raises(ValueError, match="row limit"):
        read(repo, [original])


def test_duplicate_offset_blocks_ambiguous_group():
    original = seed()
    result = read(repository([joined_row(original), joined_row(original, 1), joined_row(original, 1)]), [original])
    assert result.rows == ()
    assert "AMBIGUOUS_ROWS" in reasons(result)


def test_pdf_single_page_projection_and_unknown_or_cross_page_skip():
    original = seed()
    original = replace(original, locator={**original.locator, "kind": "pdf", "page": 1})
    base = dict(section_page_start=1, section_page_end=1)
    nrow = joined_row(original, 1, **base)
    nrow["locator"] = {**nrow["locator"], "kind": "pdf", "page": 1}
    result = read(repository([joined_row(original, **base), nrow]), [original])
    assert result.rows[0].metadata.page == 1
    assert neighbors(result)
    for changes in ({"section_page_end": 2}, {"section_page_end": None}):
        result = read(repository([joined_row(original, **base), {**nrow, **changes}]), [original])
        assert neighbors(result) == []
        assert "UNRELIABLE_PAGE_BOUNDARY" in reasons(result)


def test_query_error_is_propagated_without_unscoped_fallback():
    class RejectingEngine(CaptureEngine):
        def execute(self, statement, params):
            raise PermissionError("SIMULATED query denied")
    repo = repository([])
    repo.engine = RejectingEngine([])
    with pytest.raises(PermissionError, match="SIMULATED query denied"):
        read(repo, [seed()])
    assert repo.engine.connections == 1
