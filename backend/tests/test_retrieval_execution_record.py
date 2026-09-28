import pytest

from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.models import ChunkRecord
from backend.app.domain.scope import Scope


def repository():
    repo = InMemoryRetrievalRepository()
    for n in range(3):
        repo.add(ChunkRecord(f'c{n}', 'kb', 'doc', 'version', 'evidence '*(3-n), {}))
    return repo


def test_runtime_record_is_read_from_actual_instance_and_retains_candidates():
    retriever = HybridRetriever(repository(), top_k=1, candidate_k=3)
    result = retriever.retrieve(Scope.from_ids(['kb']), 'evidence')
    assert hasattr(result, 'effective_config'), 'actual retrieval configuration is missing'
    assert result.effective_config == {'top_k':1, 'candidate_k':3, 'rrf_k':60}
    assert len(result.candidate_rankings['keyword']) == 3
    assert len(result.fused_ranking) == 3
    assert len(result.items) == 1
    assert result.latency_ms >= 0
    retriever.top_k = 2
    changed = retriever.retrieve(Scope.from_ids(['kb']), 'evidence')
    assert changed.effective_config['top_k'] == 2
    assert len(changed.items) == 2
    assert result.effective_config['top_k'] == 1


def test_rrf_parameter_is_used_by_actual_business_fusion():
    retriever = HybridRetriever(repository())
    assert hasattr(retriever, 'rrf_k'), 'RRF runtime parameter is missing'
    retriever.rrf_k = 20
    result = retriever.retrieve(Scope.from_ids(['kb']), 'evidence')
    assert result.effective_config['rrf_k'] == 20
    assert result.items[0].hit.fused_score == pytest.approx(1/21)


def test_vector_failures_are_recorded_without_breaking_keyword_fallback():
    class FailedEmbedding:
        def embed(self, *args, **kwargs):
            raise RuntimeError('private provider detail must not leak')
    result = HybridRetriever(repository(), embedding_provider=FailedEmbedding()).retrieve(Scope.from_ids(['kb']), 'evidence')
    assert result.items
    assert hasattr(result, 'degradation_flags'), 'embedding fallback is not recorded'
    assert result.degradation_flags == ('VECTOR_UNAVAILABLE',)
    assert result.sources == ('keyword',)


def test_empty_scope_still_records_effective_configuration():
    result = HybridRetriever(repository(), top_k=2).retrieve(Scope.from_ids([]), 'evidence')
    assert hasattr(result, 'effective_config'), 'empty scopes lack execution metadata'
    assert result.effective_config['top_k'] == 2
    assert result.candidate_rankings == {}
def test_retrieval_stage_timings_are_actual_or_unavailable():
    from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
    from backend.app.domain.scope import Scope
    result=HybridRetriever(InMemoryRetrievalRepository()).retrieve(Scope.from_ids(['kb']), 'query')
    assert result.stage_latency_ms['keyword_retrieval_ms']>=0
    assert result.stage_latency_ms['fusion_ms']>=0
    assert result.stage_latency_ms['vector_retrieval_ms'] is None
    assert result.stage_latency_ms['embedding_ms'] is None
