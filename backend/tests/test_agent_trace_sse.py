from __future__ import annotations

from backend.app.application.evidence_accumulator import EvidenceAccumulator
from backend.app.application.knowledge_tools import KnowledgeToolGateway
from backend.app.application.langchain_agent import LangChainAgentAdapter
from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.models import ChunkRecord
from backend.app.domain.scope import Scope
from backend.tests.test_langchain_agent import ScriptedChatModel


def test_langchain_agent_records_redacted_tool_steps_and_evidence() -> None:
    repository = InMemoryRetrievalRepository()
    repository.add(ChunkRecord("c", "kb", "doc", "ver", "question evidence", {"start": 0}))
    evidence = EvidenceAccumulator()
    gateway = KnowledgeToolGateway(retriever=HybridRetriever(repository), evidence_accumulator=evidence)
    result = LangChainAgentAdapter(ScriptedChatModel(final_answer="基于证据回答 [c]")).run(
        "conv",
        "question",
        Scope.from_ids(["kb"]),
        run_id="run-1",
        gateway=gateway,
        evidence=evidence,
    )

    assert result.status == "completed"
    assert [step.tool_name for step in result.steps] == ["search_knowledge"]
    assert all("question" not in step.input_summary for step in result.steps)
    assert result.citations == ("E1",)
