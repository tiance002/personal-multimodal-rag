"""Semantic port of WeKnora retriever/postgres/repository.go (MIT).

Tencent/WeKnora@3e8b0bfc; see docs/weknora-port/UPSTREAM_LICENSE.md.
SQL uses ParadeDB and halfvec, not the legacy chunk_terms BM25 engine.
Connections are injected; importing this module never opens a database.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .indexing import IndexInfo, IndexBoundaryDenied


class Cursor(Protocol):
    async def fetchall(self) -> list[Mapping[str, Any]]: ...


class Connection(Protocol):
    async def execute(self, query: str, params: Sequence[Any] = ()) -> Cursor: ...
    def transaction(self) -> Any: ...


@dataclass(frozen=True)
class DatabaseIdentity:
    database: str
    system_identifier: str


@dataclass(frozen=True)
class IsolationPolicy:
    """Explicit new-instance identity; a different port alone is insufficient."""
    expected_system_identifier: str
    prohibited_system_identifiers: frozenset[str]
    database: str = "weknora_python_port_20261013"

    def validate(self, identity: DatabaseIdentity) -> None:
        if (not self.expected_system_identifier
                or not self.prohibited_system_identifiers
                or identity.database != self.database
                or identity.system_identifier != self.expected_system_identifier
                or identity.system_identifier in self.prohibited_system_identifiers):
            raise PermissionError("ISOLATED_DATABASE_IDENTITY_REQUIRED")


@dataclass(frozen=True)
class RetrieveParams:
    # These IDs must be supplied by the service's trusted Scope resolver.
    knowledge_base_ids: tuple[str, ...]
    knowledge_ids: tuple[str, ...] = ()
    tag_ids: tuple[str, ...] = ()
    query: str = ""
    embedding: tuple[float, ...] = ()
    top_k: int = 30
    threshold: float = 0.2


@dataclass(frozen=True)
class Statement:
    sql: str
    params: tuple[Any, ...]
    expanded_top_k: int = 0


_COLUMNS = "id, content, source_id, source_type, chunk_id, knowledge_id, knowledge_base_id, tag_id"


def _scope(params: RetrieveParams) -> tuple[list[str], list[Any]]:
    # Fixed source permits an empty scope internally. Our API's minimal privacy
    # boundary requires the server-resolved KB scope before reaching this adapter.
    if not params.knowledge_base_ids:
        raise PermissionError("KNOWLEDGE_BASE_SCOPE_REQUIRED")
    if params.top_k < 0:
        raise ValueError("INVALID_TOP_K")
    where, values = [], []
    for column, ids in (("knowledge_base_id", params.knowledge_base_ids),
                        ("knowledge_id", params.knowledge_ids), ("tag_id", params.tag_ids)):
        if ids:
            where.append(f"{column} IN ({', '.join(['%s'] * len(ids))})")
            values.extend(ids)
    return where, values


def keyword_statement(params: RetrieveParams) -> Statement:
    where, values = _scope(params)
    where += ["content ||| %s", "(is_enabled IS NULL OR is_enabled = %s)"]
    values += [params.query, True, params.top_k]
    return Statement(f"SELECT paradedb.score(id) AS score, {_COLUMNS} FROM embeddings "
                     f"WHERE {' AND '.join(where)} ORDER BY score DESC LIMIT %s", tuple(values))


def vector_statement(params: RetrieveParams) -> Statement:
    where, values = _scope(params)
    dimension = len(params.embedding)
    if dimension == 0 or any(not math.isfinite(value) for value in params.embedding):
        raise ValueError("INVALID_EMBEDDING")
    # Only an integer dimension is interpolated into the HNSW index expression.
    # All query contents, IDs, limits and vector values remain bound parameters.
    vector = "[" + ",".join(str(float(value)) for value in params.embedding) + "]"
    expanded = max(params.top_k, min(200, max(100, params.top_k * 2)))
    expression = f"embedding::halfvec({dimension}) <=> %s::halfvec({dimension})"
    sql = (f"SELECT {_COLUMNS}, (1 - distance) AS score FROM ("
           f"SELECT {_COLUMNS}, {expression} AS distance FROM embeddings "
           f"WHERE dimension = %s AND {' AND '.join(where)} "
           f"AND (is_enabled IS NULL OR is_enabled = %s) "
           f"ORDER BY {expression} LIMIT %s) AS candidates "
           "WHERE distance <= %s ORDER BY distance ASC LIMIT %s")
    return Statement(sql, (vector, dimension, *values, True, vector, expanded,
                           1 - params.threshold, params.top_k), expanded)


class PostgresRetriever:
    def __init__(self, connection: Connection, isolation: IsolationPolicy, *,
                 writable_knowledge_base_ids: frozenset[str] = frozenset()):
        self.connection, self.isolation = connection, isolation
        self.writable_knowledge_base_ids = writable_knowledge_base_ids
        self._verified = False

    async def verify_identity(self) -> DatabaseIdentity:
        # Read-only verification must precede any migration, index write or query.
        self._verified = False
        cursor = await self.connection.execute(
            "SELECT current_database() AS database, system_identifier::text "
            "FROM pg_control_system()")
        rows = await cursor.fetchall()
        if len(rows) != 1:
            raise PermissionError("DATABASE_IDENTITY_UNAVAILABLE")
        identity = DatabaseIdentity(rows[0]["database"], rows[0]["system_identifier"])
        self.isolation.validate(identity)
        self._verified = True
        return identity

    def require_verified(self) -> None:
        if not self._verified:
            raise PermissionError("DATABASE_IDENTITY_NOT_VERIFIED")

    async def keywords(self, params: RetrieveParams) -> list[Mapping[str, Any]]:
        self.require_verified()
        stmt = keyword_statement(params)
        return await (await self.connection.execute(stmt.sql, stmt.params)).fetchall()

    async def batch_save(self, rows: Sequence[IndexInfo],
                         embeddings: Mapping[str, Sequence[float]]) -> None:
        self.require_verified()
        if not rows:
            return
        # Write permission comes from the server's trusted KB resolver, never
        # from row metadata or a client-provided scope.
        if any(row.knowledge_base_id not in self.writable_knowledge_base_ids for row in rows):
            raise IndexBoundaryDenied("KNOWLEDGE_BASE_WRITE_SCOPE_DENIED")
        values: list[Any] = []
        for row in rows:
            vector = embeddings.get(row.source_id, ())
            if any(not math.isfinite(value) for value in vector):
                raise ValueError("INVALID_EMBEDDING")
            vector_text = "[" + ",".join(str(float(value)) for value in vector) + "]"
            # CleanInvalidUTF8 strips invalid scalars and NUL; valid U+FFFD is
            # retained, as in the Go rune decoder.
            content = row.content.encode("utf-8", errors="ignore").decode("utf-8").replace("\x00", "")
            values.extend((row.source_id,row.source_type,row.chunk_id,row.knowledge_id,
                           row.knowledge_base_id,row.tag_id,content,len(vector),
                           vector_text if vector else None,row.is_enabled))
        columns = "source_id,source_type,chunk_id,knowledge_id,knowledge_base_id,tag_id,content,dimension,embedding,is_enabled"
        placeholder = "(%s,%s,%s,%s,%s,%s,%s,%s,%s::halfvec,%s)"
        sql = f"INSERT INTO embeddings ({columns}) VALUES " + ",".join([placeholder] * len(rows))
        # Fixed source BatchSave uses OnConflict{DoNothing:true}; no silent
        # replacement of an already indexed source identity.
        async with self.connection.transaction():
            await self.connection.execute(sql + " ON CONFLICT DO NOTHING", values)

    async def vectors(self, params: RetrieveParams) -> list[Mapping[str, Any]]:
        self.require_verified()
        stmt = vector_statement(params)
        rows = []
        try:
            async with self.connection.transaction():
                await self.connection.execute(f"SET LOCAL hnsw.ef_search = {max(40, stmt.expanded_top_k)}")
                await self.connection.execute("SET LOCAL hnsw.iterative_scan = strict_order")
                rows = await (await self.connection.execute(stmt.sql, stmt.params)).fetchall()
        except Exception as exc:
            # Upstream retries only unsupported GUC errors after rollback.
            if rows or not any(guc in str(exc) for guc in ("hnsw.ef_search", "hnsw.iterative_scan")):
                raise
            rows = await (await self.connection.execute(stmt.sql, stmt.params)).fetchall()
        return rows[:params.top_k]
