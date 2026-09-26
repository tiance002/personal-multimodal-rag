from backend.app.application.agent_runtime import AgentLimits, AgentRuntime
from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.application.knowledge_tools import KnowledgeToolGateway
from backend.app.application.rag_orchestrator import RAGOrchestrator
from backend.app.application.retrieval import ChunkRecord, HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.scope import Scope


def test_agent_uses_shared_rag_orchestrator_and_records_redacted_steps():
    repository = InMemoryRetrievalRepository()
    repository.add(ChunkRecord("c", "kb", "doc", "ver", "evidence", {}))
    runtime = AgentRuntime(RAGOrchestrator(HybridRetriever(repository), CitationService(InMemoryCitationStore())), KnowledgeToolGateway())

    result = runtime.run("conv", "evidence", Scope.from_ids(["kb"]), AgentLimits(max_steps=3))

    assert result.status == "completed"
    assert [step.tool_name for step in result.steps] == ["search_knowledge"]
    assert all("evidence" not in step.input_summary for step in result.steps)
