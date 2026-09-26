from backend.app.application.knowledge_gateway import KnowledgeGateway
from backend.app.application.quick_chain import LangChainQuickChain
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


def _chain(contents: list[str], model=None, top_k: int = 8) -> LangChainQuickChain:
    repository = InMemoryRetrievalRepository()
    for index, content in enumerate(contents, start=1):
        repository.add(ChunkRecord(f"c{index}", "kb", f"doc{index}", "v1", content, {"start": 0}))
    return LangChainQuickChain(KnowledgeGateway(HybridRetriever(repository, top_k=top_k)), answer_gateway=model)


def test_simple_question_uses_one_retrieval_and_no_query_model():
    model = RecordingAnswerModel("A方案成本1000元 [E1]")
    result = _chain(["A方案成本1000元。"], model).invoke("A 的成本是多少？", Scope.from_ids(["kb"]))

    assert result.error_code is None
    assert result.citations == ("E1",)
    assert len(model.prompts) == 1
    assert result.trace.model_calls == 1


def test_compound_question_uses_shared_targeted_retrieval_before_answer():
    model = RecordingAnswerModel("A方案成本1000元 [E1]；B方案成本1200元 [E2]")
    chain = _chain(["A方案成本1000元。", "B方案成本1200元。"], model, top_k=1)
    calls: list[str] = []
    original_retrieve = chain.knowledge_gateway.retriever.retrieve

    def tracked(scope, question, query_plan=None):
        calls.append(question)
        return original_retrieve(scope, question, query_plan)

    chain.knowledge_gateway.retriever.retrieve = tracked
    result = chain.invoke("A 与 B 两种方案各自的成本是多少？", Scope.from_ids(["kb"]))

    assert result.error_code is None
    assert len(calls) == 2
    assert len(model.prompts) == 1
    assert "A方案成本1000元" in model.prompts[0]
    assert "B方案成本1200元" in model.prompts[0]


def test_missing_target_returns_supported_partial_answer_without_model_call():
    model = RecordingAnswerModel("unused")
    result = _chain(["A方案成本1000元。"], model).invoke(
        "A 与 B 两种方案各自的成本是多少？",
        Scope.from_ids(["kb"]),
    )

    assert result.error_code is None
    assert model.prompts == []
    assert "A方案成本1000元" in result.answer
    assert "B" in result.answer and "没有足够证据" in result.answer
    assert result.citations == ("E1",)


def test_generated_fact_without_a_reference_is_not_auto_cited():
    result = _chain(["A方案成本1000元。"], RecordingAnswerModel("A方案成本1000元。")).invoke(
        "A 的成本是多少？",
        Scope.from_ids(["kb"]),
    )

    assert result.error_code == "UNSUPPORTED_ANSWER"
    assert result.citations == ()
