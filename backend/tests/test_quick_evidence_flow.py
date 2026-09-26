from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.application.rag_orchestrator import RAGOrchestrator
from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.models import ChunkRecord
from backend.app.domain.scope import Scope


class RecordingAnswerModel:
    def __init__(self, answer: str) -> None:
        self.answer_text = answer
        self.prompts: list[str] = []

    def answer(self, prompt: str, timeout_seconds: float) -> str:
        self.prompts.append(prompt)
        return self.answer_text


class ForbiddenQueryExpander:
    def query_expand(self, question: str, timeout_seconds: float):
        raise AssertionError("simple queries must not call query expansion")


def _orchestrator(contents: list[str], model=None, top_k: int = 8) -> RAGOrchestrator:
    repository = InMemoryRetrievalRepository()
    for index, content in enumerate(contents, start=1):
        repository.add(ChunkRecord(f"c{index}", "kb", f"doc{index}", "v1", content, {"start": 0}))
    return RAGOrchestrator(
        HybridRetriever(repository, top_k=top_k), CitationService(InMemoryCitationStore()),
        local_query_gateway=ForbiddenQueryExpander(), answer_gateway=model,
    )


def test_simple_question_uses_one_retrieval_and_no_query_model():
    model = RecordingAnswerModel("A方案成本1000元 [E1]")
    orchestrator = _orchestrator(["A方案成本1000元。"], model)

    result = orchestrator.answer_query("A 的成本是多少？", Scope.from_ids(["kb"]))

    assert result.error_code is None
    assert result.citations == ("E1",)
    assert len(model.prompts) == 1
    assert result.trace.model_calls == 1


def test_compound_question_can_use_one_targeted_retrieval_then_one_answer():
    model = RecordingAnswerModel("A方案成本1000元 [E1]；B方案成本1200元 [E2]")
    orchestrator = _orchestrator(["A方案成本1000元。", "B方案成本1200元。"], model, top_k=1)
    calls: list[str] = []
    original_retrieve = orchestrator.retriever.retrieve

    def tracked(scope, question, query_plan=None):
        calls.append(question)
        return original_retrieve(scope, question, query_plan)

    orchestrator.retriever.retrieve = tracked
    result = orchestrator.answer_query("A 与 B 两种方案各自的成本是多少？", Scope.from_ids(["kb"]))

    assert result.error_code is None
    assert len(calls) == 2
    assert len(model.prompts) == 1
    assert "A方案成本1000元" in model.prompts[0]
    assert "B方案成本1200元" in model.prompts[0]


def test_missing_target_returns_supported_partial_answer_after_one_retry():
    model = RecordingAnswerModel("unsupported")
    orchestrator = _orchestrator(["A方案成本1000元。"], model)
    calls: list[str] = []
    original_retrieve = orchestrator.retriever.retrieve

    def tracked(scope, question, query_plan=None):
        calls.append(question)
        return original_retrieve(scope, question, query_plan)

    orchestrator.retriever.retrieve = tracked
    result = orchestrator.answer_query("A 与 B 两种方案各自的成本是多少？", Scope.from_ids(["kb"]))

    assert result.error_code is None
    assert len(calls) == 2
    assert model.prompts == []
    assert "A方案成本1000元" in result.answer
    assert "[E1]" in result.answer
    assert "B" in result.answer and "没有足够证据" in result.answer
    assert result.citations == ("E1",)


def test_generated_fact_without_a_reference_is_not_auto_cited():
    orchestrator = _orchestrator(["A方案成本1000元。"], RecordingAnswerModel("A方案成本1000元。"))

    result = orchestrator.answer_query("A 的成本是多少？", Scope.from_ids(["kb"]))

    assert result.error_code == "UNSUPPORTED_ANSWER"
    assert result.citations == ()
