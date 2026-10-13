"""Identity-verified connection leases for parallel source retrieval stages.

No module-level connections, environment reading, or production fallback.
Each lease has its own transaction/GUC scope; a guard never survives reconnect.
"""
from contextlib import asynccontextmanager
from typing import Awaitable, Callable

from .postgres import Connection, IsolationPolicy, PostgresRetriever


class IsolatedConnectionFactory:
    def __init__(self, acquire: Callable[[], Awaitable[Connection]],
                 release: Callable[[Connection], Awaitable[None]], isolation: IsolationPolicy):
        self.acquire, self.release, self.isolation = acquire, release, isolation

    @asynccontextmanager
    async def lease(self, *, writable_knowledge_base_ids=frozenset()):
        connection = await self.acquire()
        repository = PostgresRetriever(connection, self.isolation,
                                        writable_knowledge_base_ids=writable_knowledge_base_ids)
        try:
            await repository.verify_identity()
            yield repository
        finally:
            repository._verified = False
            await self.release(connection)
