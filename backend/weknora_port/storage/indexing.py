"""Port of keywords_vector_hybrid_indexer.go, fixed WeKnora 3e8b0bfc (MIT).

See docs/weknora-port/UPSTREAM_LICENSE.md. Embedding and persistence are injected;
the caller remains responsible for trusted scope and per-attempt egress/cost gates.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class IndexInfo:
    source_id: str
    source_type: int
    chunk_id: str
    knowledge_id: str
    knowledge_base_id: str
    content: str
    tag_id: str = ""
    is_enabled: bool = True


class IndexRepository(Protocol):
    async def batch_save(self, rows: Sequence[IndexInfo],
                         embeddings: Mapping[str, Sequence[float]]) -> None: ...


class Embedder(Protocol):
    async def batch_embed_with_pool(self, texts: Sequence[str], *, attempt: int) -> Sequence[Sequence[float]]: ...


class IndexBoundaryDenied(Exception):
    """Nonretryable privacy/budget/identity admission failure, before any send."""


_PATTERNS = tuple(re.compile(pattern, flags) for pattern, flags in (
    (r'''<img\b[^>]*\bsrc=["']\s*data:image/[a-z0-9.+-]+;base64,[^"']+["'][^>]*>''', re.I | re.S),
    (r'''!\[[^\]]*\]\(\s*data:image/[a-z0-9.+-]+;base64,[^)]+\)''', re.I | re.S),
    (r'''data:image/[a-z0-9.+-]+;base64,[a-z0-9+/=]{200,}''', re.I),
    (r'''data:[a-z0-9.+/-]+;base64,[a-z0-9+/=]{200,}''', re.I),
))


def sanitize_for_embedding(content: str) -> str:
    if "base64," in content:
        for pattern in _PATTERNS:
            content = pattern.sub("[image]", content)
    return content[:20000]


async def batch_embed_with_backoff(embedder: Embedder, texts: Sequence[str], *,
                                   sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
                                   ) -> Sequence[Sequence[float]]:
    # The fixed source retries ordinary embedding errors, not only a transient
    # classifier. Cancellation and the minimal admission boundary are excluded.
    for attempt in range(5):
        try:
            return await embedder.batch_embed_with_pool(texts, attempt=attempt)
        except IndexBoundaryDenied:
            raise
        except Exception:
            if attempt == 4:
                raise
            await sleep(.2 * 2 ** attempt)
    raise AssertionError("unreachable")


class HybridIndexer:
    def __init__(self, repository: IndexRepository):
        self.repository = repository

    async def batch_index(self, rows: Sequence[IndexInfo], *, vector_enabled: bool,
                          embedder: Embedder | None = None,
                          sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        if not rows:
            return
        vectors: Sequence[Sequence[float]] = ()
        if vector_enabled:
            if embedder is None:
                raise ValueError("EMBEDDING_MODEL_REQUIRED")
            vectors = await batch_embed_with_backoff(
                embedder, [sanitize_for_embedding(row.content) for row in rows], sleep=sleep)
            if len(vectors) != len(rows):
                # Fail before any index write. A malformed provider batch must
                # never associate another chunk's embedding with this source.
                raise ValueError("EMBEDDING_BATCH_IDENTITY_MISMATCH")
        batch_size = 40 if vector_enabled else 10
        semaphore = asyncio.Semaphore(5)

        async def save(start: int) -> None:
            async with semaphore:
                batch = rows[start:start + batch_size]
                embedding_map = {row.source_id: vectors[start + index]
                                 for index, row in enumerate(batch)} if vector_enabled else {}
                await self.repository.batch_save(batch, embedding_map)

        # TaskGroup mirrors errgroup cancellation of sibling batch writes on
        # failure; successful writes may remain, as in upstream. Ready status
        # must be set only after this method returns successfully.
        async with asyncio.TaskGroup() as group:
            for start in range(0, len(rows), batch_size):
                group.create_task(save(start))
