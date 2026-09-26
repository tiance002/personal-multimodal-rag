from backend.app.application.knowledge_gateway import KnowledgeGateway
from backend.app.application.quick_chain import LangChainQuickChain, QuickSettings
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


class QueryExpander:
    def __init__(self) -> None:
        self.calls = 0

    def query_expand(self, question: str, timeout_seconds: float):
        self.calls += 1
        return {"expansions": ["扩展词"]}


def _gateway(*contents: str) -> KnowledgeGateway:
    repository = InMemoryRetrievalRepository()
    for index, content in enumerate(contents, start=1):
        repository.add(ChunkRecord(f"c{index}", "kb", f"doc{index}", "v1", content, {"start": 0}))
    return KnowledgeGateway(HybridRetriever(repository, top_k=1))


def test_quick_chain_uses_langchain_runnable_and_shared_gateway() -> None:
    model = RecordingAnswerModel("A方案成本1000元 [E1]")
    hits: list[str] = []
    chain = LangChainQuickChain(_gateway("A方案成本1000元。"), answer_gateway=model)

    result = chain.invoke(
        "A 的成本是多少？",
        Scope.from_ids(["kb"]),
        run_id="run-1",
        on_retrieval=lambda _run_id, items: hits.extend(item.chunk.chunk_id for item in items),
    )

    assert result.error_code is None
    assert result.citations == ("E1",)
    assert result.answer == "A方案成本1000元 [E1]"
    assert hits == ["c1"]
    assert len(model.prompts) == 1


def test_quick_chain_accepts_an_independent_query_gateway() -> None:
    expander = QueryExpander()
    answer_model = RecordingAnswerModel("扩展词证据 [E1]")
    chain = LangChainQuickChain(_gateway("扩展词证据"), answer_gateway=answer_model)

    result = chain.invoke(
        "原始问题",
        Scope.from_ids(["kb"]),
        settings=QuickSettings(local_query_enabled=True),
        local_query_gateway=expander,
    )

    assert result.error_code is None
    assert expander.calls == 1
    assert answer_model.prompts


def test_quick_chain_reuses_core_for_targeted_retrieval_and_partial_answer() -> None:
    chain = LangChainQuickChain(_gateway("A方案成本1000元。"), answer_gateway=RecordingAnswerModel("unused"))

    result = chain.invoke("A 与 B 两种方案各自的成本是多少？", Scope.from_ids(["kb"]), run_id="run-2")

    assert result.error_code is None
    assert "A方案成本1000元" in result.answer
    assert "B" in result.answer
    assert result.citations == ("E1",)
