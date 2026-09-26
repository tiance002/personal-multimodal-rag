from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from backend.app.application.evidence_accumulator import EvidenceAccumulator
from backend.app.application.knowledge_tools import KnowledgeToolGateway
from backend.app.application.langchain_agent import LangChainAgentAdapter
from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.agent_policy import AgentLimits, estimate_tokens
from backend.app.domain.models import ChunkRecord
from backend.app.domain.scope import Scope


class LimitChatModel(BaseChatModel):
    final_answer: str = "answer [chunk]"
    final_cost: int = 0
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "limit-test"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls += 1
        if self.calls == 1:
            message = AIMessage(
                content="",
                tool_calls=[
                    {"name": "search_knowledge", "args": {"query": "question"}, "id": "search", "type": "tool_call"}
                ],
            )
        else:
            message = AIMessage(content=self.final_answer, response_metadata={"cost_microunits": self.final_cost})
        return ChatResult(generations=[ChatGeneration(message=message)])


def _run(model: LimitChatModel, limits: AgentLimits):
    repository = InMemoryRetrievalRepository()
    repository.add(ChunkRecord("chunk", "kb", "doc", "version", "question evidence", {"start": 0}))
    evidence = EvidenceAccumulator()
    gateway = KnowledgeToolGateway(retriever=HybridRetriever(repository), evidence_accumulator=evidence)
    return LangChainAgentAdapter(model).run(
        "conv",
        "question",
        Scope.from_ids(["kb"]),
        run_id="run",
        gateway=gateway,
        evidence=evidence,
        limits=limits,
    )


def test_agent_rejects_zero_step_budget() -> None:
    model = LimitChatModel()
    result = _run(model, AgentLimits(max_steps=0))

    assert result.error_code == "AGENT_STEP_LIMIT"
    assert model.calls == 0


def test_agent_enforces_cost_budget() -> None:
    result = _run(LimitChatModel(final_cost=25), AgentLimits(max_cost_microunits=10))

    assert result.error_code == "AGENT_COST_LIMIT"
    assert result.cost_microunits == 25


def test_token_estimate_counts_chinese_characters() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("这是一段中文回答") == 8
    assert estimate_tokens("hello world") == 2
    assert estimate_tokens("混合 mixed 文本") >= 4


def test_agent_token_limit_is_reachable_for_a_chinese_answer() -> None:
    result = _run(LimitChatModel(final_answer="中文回答" * 500), AgentLimits(max_tokens=100))

    assert result.error_code == "AGENT_TOKEN_LIMIT"
