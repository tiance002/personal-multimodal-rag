import asyncio

import pytest

from backend.weknora_port.storage.indexing import (
    HybridIndexer, IndexBoundaryDenied, IndexInfo, batch_embed_with_backoff,
    sanitize_for_embedding,
)


class FakeRepository:
    def __init__(self): self.batches, self.active, self.peak = [], 0, 0
    async def batch_save(self, rows, embeddings):
        self.active += 1
        self.peak = max(self.peak,self.active)
        await asyncio.sleep(0)
        self.batches.append((rows,embeddings))
        self.active -= 1


class FakeEmbedder:
    def __init__(self, errors=0, failure=RuntimeError, malformed=False):
        self.errors, self.failure, self.malformed, self.calls = errors,failure,malformed,[]
    async def batch_embed_with_pool(self, texts, *, attempt):
        self.calls.append((texts,attempt))
        if len(self.calls) <= self.errors: raise self.failure("fixture")
        return [[float(index)] for index in range(len(texts) - int(self.malformed))]


def test_embedding_scrubbing_and_rune_limit_do_not_modify_evidence():
    text = '正文 ![原图](data:image/png;base64,' + 'a' * 300 + ') ' + '中' * 22000
    sanitized = sanitize_for_embedding(text)
    assert sanitized.startswith('正文 [image] ')
    assert len(sanitized) == 20000 and 'base64,' in text


def test_exact_retry_budget_no_retry_for_admission_or_cancel():
    async def exercise():
        delays=[]
        async def sleep(delay): delays.append(delay)
        embedder=FakeEmbedder(errors=4)
        assert await batch_embed_with_backoff(embedder,["one"],sleep=sleep) == [[0.0]]
        assert delays == [.2,.4,.8,1.6]
        assert [attempt for _,attempt in embedder.calls] == list(range(5))
        for failure in (IndexBoundaryDenied,asyncio.CancelledError):
            e=FakeEmbedder(errors=9,failure=failure)
            with pytest.raises(failure): await batch_embed_with_backoff(e,["one"],sleep=sleep)
            assert len(e.calls)==1
        exhausted=FakeEmbedder(errors=9)
        with pytest.raises(RuntimeError): await batch_embed_with_backoff(exhausted,["one"],sleep=sleep)
        assert len(exhausted.calls)==5
    asyncio.run(exercise())


@pytest.mark.parametrize('vector,count,size', [(True,241,40),(False,61,10)])
def test_batch_association_and_maximum_five_concurrent_saves(vector,count,size):
    async def exercise():
        rows=[IndexInfo(str(i),1,str(i),"doc","kb",str(i)) for i in range(count)]
        repo,embedder=FakeRepository(),FakeEmbedder()
        await HybridIndexer(repo).batch_index(rows,vector_enabled=vector,embedder=embedder)
        assert repo.peak <= 5
        assert sorted(len(batch) for batch,_ in repo.batches)==sorted([size]*6+[1])
        for batch,embeddings in repo.batches:
            if vector:
                assert all(embeddings[row.source_id]==[float(row.source_id)] for row in batch)
            else: assert embeddings=={}
        assert len(embedder.calls)==int(vector)
    asyncio.run(exercise())


def test_malformed_embedding_batch_never_writes_index():
    async def exercise():
        repo=FakeRepository()
        with pytest.raises(ValueError,match="IDENTITY_MISMATCH"):
            await HybridIndexer(repo).batch_index([IndexInfo("s",1,"c","d","k","x")],
                vector_enabled=True,embedder=FakeEmbedder(malformed=True))
        assert repo.batches==[]
    asyncio.run(exercise())
