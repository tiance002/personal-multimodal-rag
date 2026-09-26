from backend.app.domain.fusion import rrf_fuse
from backend.app.domain.models import RankedHit


def _hit(chunk_id: str, rank: int) -> RankedHit:
    return RankedHit(chunk_id=chunk_id, rank=rank, raw_score=1.0 / rank, sources=("seed",))


def test_rrf_fusion_retains_sources_and_stable_order():
    fused = rrf_fuse(
        {
            "keyword": [_hit("b", 1), _hit("a", 2)],
            "vector": [_hit("a", 1)],
        }
    )

    assert [hit.chunk_id for hit in fused[:2]] == ["a", "b"]
    assert fused[0].sources == ("keyword", "vector")
    assert fused[0].rank == 1
