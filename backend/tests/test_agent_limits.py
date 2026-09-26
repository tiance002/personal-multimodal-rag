from backend.app.application.agent_runtime import AgentLimits, AgentRuntime
from backend.app.application.knowledge_tools import KnowledgeToolGateway
from backend.app.application.rag_orchestrator import RAGOrchestrator
from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.domain.agent_policy import estimate_tokens
from backend.app.domain.scope import Scope
from types import SimpleNamespace


def test_agent_rejects_zero_step_budget_and_cancel_is_idempotent():
    runtime = AgentRuntime(RAGOrchestrator(HybridRetriever(InMemoryRetrievalRepository()), CitationService(InMemoryCitationStore())), KnowledgeToolGateway())
    result = runtime.run("conv", "question", Scope.from_ids(["kb"]), AgentLimits(max_steps=0))

    assert result.error_code == "AGENT_STEP_LIMIT"
    assert runtime.cancel(result.run_id).status == "cancelled"


class CostlyOrchestrator:
    def answer_query(self, question, scope, settings, *, run_id=None):
        return SimpleNamespace(answer="answer", citations=(), error_code=None, cost_microunits=25)


def test_agent_enforces_cost_budget():
    runtime = AgentRuntime(CostlyOrchestrator(), KnowledgeToolGateway())
    result = runtime.run("conv", "question", Scope.from_ids(["kb"]), AgentLimits(max_cost_microunits=10))

    assert result.error_code == "AGENT_COST_LIMIT"
    assert result.cost_microunits == 25
    assert runtime.cancel(result.run_id).status == "cancelled"


def test_token_estimate_counts_chinese_characters():
    assert estimate_tokens("") == 0
    assert estimate_tokens("这是一段中文回答") == 8
    assert estimate_tokens("hello world") == 2
    assert estimate_tokens("混合 mixed 文本") >= 4


class VerboseOrchestrator:
    def answer_query(self, question, scope, settings, *, run_id=None):
        return SimpleNamespace(answer="中文回答" * 500, citations=(), error_code=None, cost_microunits=0)


def test_agent_token_limit_is_reachable_for_a_chinese_answer():
    runtime = AgentRuntime(VerboseOrchestrator(), KnowledgeToolGateway())
    result = runtime.run("conv", "question", Scope.from_ids(["kb"]), AgentLimits(max_tokens=100))

    assert result.error_code == "AGENT_TOKEN_LIMIT"
