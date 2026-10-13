"""SQL and isolation boundary tests. No PostgreSQL ranking parity is claimed."""
import asyncio
from contextlib import asynccontextmanager

import pytest

from backend.weknora_port.storage.postgres import (
    DatabaseIdentity, IsolationPolicy, PostgresRetriever, RetrieveParams,
    keyword_statement, vector_statement,
)
from backend.weknora_port.storage.indexing import IndexInfo, IndexBoundaryDenied


def test_keyword_scope_and_engine_are_parameterized():
    query = "资料' OR 1=1"
    stmt = keyword_statement(RetrieveParams(("kb",), ("doc",), ("tag",), query))
    assert "content ||| %s" in stmt.sql and "paradedb.score(id)" in stmt.sql
    assert "knowledge_id IN (%s)" in stmt.sql and "tag_id IN (%s)" in stmt.sql
    assert query not in stmt.sql
    assert stmt.params == ("kb", "doc", "tag", query, True, 30)
    with pytest.raises(PermissionError):
        keyword_statement(RetrieveParams(()))


@pytest.mark.parametrize("top_k,expanded", [(0,100),(30,100),(80,160),(150,200),(250,250)])
def test_hnsw_expression_candidate_budget_and_threshold(top_k, expanded):
    stmt = vector_statement(RetrieveParams(("kb",), embedding=(1.0,2.0), top_k=top_k, threshold=.7))
    assert stmt.expanded_top_k == expanded
    assert stmt.sql.count("embedding::halfvec(2) <=> %s::halfvec(2)") == 2
    assert stmt.params[-3:] == (expanded, pytest.approx(.3), top_k)
    assert stmt.sql.count("%s") == len(stmt.params)


def test_no_empty_or_nonfinite_vectors():
    for vector in ((), (float("nan"),), (float("inf"),)):
        with pytest.raises(ValueError):
            vector_statement(RetrieveParams(("kb",), embedding=vector))


def test_isolation_requires_new_instance_and_database():
    policy = IsolationPolicy("new", frozenset({"old"}))
    policy.validate(DatabaseIdentity(policy.database, "new"))
    for identity in (DatabaseIdentity("old-db", "new"), DatabaseIdentity(policy.database, "old")):
        with pytest.raises(PermissionError):
            policy.validate(identity)
    with pytest.raises(PermissionError):
        IsolationPolicy("new", frozenset()).validate(DatabaseIdentity(policy.database,"new"))


class FakeCursor:
    def __init__(self, rows): self.rows = rows
    async def fetchall(self): return self.rows


class FakeConnection:
    def __init__(self, fail=""):
        self.calls, self.fail, self.rollback = [], fail, False
    async def execute(self, query, params=()):
        self.calls.append((query, params))
        if "pg_control_system" in query:
            return FakeCursor([{"database":"weknora_python_port_20261013", "system_identifier":"new"}])
        if self.fail and query.startswith("SET LOCAL"):
            raise RuntimeError(self.fail)
        return FakeCursor([{"id":"one", "score":.8}])
    @asynccontextmanager
    async def transaction(self):
        try: yield
        except Exception:
            self.rollback = True
            raise


def test_verify_then_guc_fallback_after_rollback_only():
    async def exercise():
        conn = FakeConnection("unrecognized parameter hnsw.iterative_scan")
        repo = PostgresRetriever(conn, IsolationPolicy("new", frozenset({"old"})))
        with pytest.raises(PermissionError):
            await repo.vectors(RetrieveParams(("kb",), embedding=(1.0,)))
        assert not conn.calls
        await repo.verify_identity()
        assert await repo.vectors(RetrieveParams(("kb",), embedding=(1.0,))) == [{"id":"one", "score":.8}]
        assert conn.rollback
        assert sum("AS candidates" in call[0] for call in conn.calls) == 1
        conn.fail = "permission denied"
        with pytest.raises(RuntimeError, match="permission denied"):
            await repo.vectors(RetrieveParams(("kb",), embedding=(1.0,)))
    asyncio.run(exercise())


def test_index_write_requires_verified_identity_and_trusted_kb():
    async def exercise():
        conn = FakeConnection()
        repo = PostgresRetriever(conn,IsolationPolicy("new",frozenset({"old"})),
                                 writable_knowledge_base_ids=frozenset({"kb"}))
        row = IndexInfo("s",1,"c","d","kb","text\x00\ud800\ufffd")
        with pytest.raises(PermissionError): await repo.batch_save([row],{"s":[.5]})
        await repo.verify_identity()
        forbidden = IndexInfo("s",1,"c","d","other","text")
        with pytest.raises(IndexBoundaryDenied): await repo.batch_save([forbidden],{})
        assert not any("INSERT" in query for query,_ in conn.calls)
        await repo.batch_save([row],{"s":[.5]})
        sql,params = conn.calls[-1]
        assert sql.endswith("ON CONFLICT DO NOTHING")
        assert params[6] == "text\ufffd" and params[7:9] == [1,"[0.5]"]
        assert sql.count("%s") == len(params)
    asyncio.run(exercise())
