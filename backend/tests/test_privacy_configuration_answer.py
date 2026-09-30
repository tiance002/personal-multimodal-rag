from backend.app.application.knowledge_gateway import KnowledgeGateway
from backend.app.application.quick_chain import LangChainQuickChain
from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.models import ChunkRecord
from backend.app.domain.scope import Scope


def test_privacy_configuration_quotes_source_without_model_advice():
    class UnsafeAdviceModel:
        calls = 0
        def answer(self, prompt, timeout_seconds):
            self.calls += 1
            return "正文采集必须设为 true [E1]"
    repo = InMemoryRetrievalRepository()
    repo.add(ChunkRecord("privacy", "kb", "doc", "v1", "Langfuse 正文采集 RAG_LANGFUSE_CAPTURE_CONTENT=false；不导出正文。", {"start": 0}))
    model = UnsafeAdviceModel()
    chain = LangChainQuickChain(KnowledgeGateway(HybridRetriever(repo)), answer_gateway=model)
    result = chain.invoke("Langfuse 正文采集是否必须开启？", Scope.from_ids(["kb"]))
    assert result.error_code is None
    assert model.calls == 0
    assert "RAG_LANGFUSE_CAPTURE_CONTENT=false" in result.answer
    assert "必须设为 true" not in result.answer
    assert result.citations == ("E1",)
    assert result.trace.degradation_code == "PRIVACY_CONFIG_EVIDENCE_ONLY"


def test_ordinary_questions_remain_local_model_path():
    assert not LangChainQuickChain._privacy_configuration("资料中的主动回忆如何练习？")


def test_smart_privacy_question_uses_shared_evidence_only_path():
    from backend.app.application.answer_service import AnswerService
    from backend.tests.test_answer_service import RecordingRunStore
    class ForbiddenAgent:
        def run(self, *args, **kwargs):
            raise AssertionError("privacy configuration must not enter agent generation")
    repo = InMemoryRetrievalRepository()
    repo.add(ChunkRecord("privacy", "kb", "doc", "v1", "Langfuse 正文采集默认 false。", {"start": 0}))
    result = AnswerService(retriever=HybridRetriever(repo), runs=RecordingRunStore(), smart_agent=ForbiddenAgent()).answer(
        {"id": "conv", "knowledge_base_scope": ["kb"], "document_scope": []}, "Langfuse 正文采集是否必须开启？", "smart")
    assert result.error_code is None
    assert result.trace["execution_mode"] == "evidence_only"
    assert result.trace["metrics"]["total_tokens"] == 0
