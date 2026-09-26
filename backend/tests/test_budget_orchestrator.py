from backend.app.application.budget import BudgetDenied, InMemoryBudgetGate
from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.application.rag_orchestrator import RAGOrchestrator, RagSettings
from backend.app.application.retrieval import ChunkRecord, HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.scope import Scope


class CloudGateway:
    def __init__(self):
        self.calls = 0

    def answer(self, prompt: str, timeout_seconds: float) -> str:
        self.calls += 1
        return "cloud answer [E1]"


class DenyingGate:
    def reserve(self, **kwargs):
        raise BudgetDenied("MONTHLY_BUDGET_EXCEEDED")


def _orchestrator(gateway, gate):
    repository = InMemoryRetrievalRepository()
    repository.add(ChunkRecord("c1", "kb-public", "doc", "ver", "public evidence", {"start": 0}))
    return RAGOrchestrator(
        HybridRetriever(repository),
        CitationService(InMemoryCitationStore()),
        answer_gateway=gateway,
        cloud_allowed_by_kb={"kb-public": True},
        budget_gate=gate,
    )


def test_budget_denial_happens_before_cloud_call():
    gateway = CloudGateway()
    result = _orchestrator(gateway, DenyingGate()).answer_query(
        "public", Scope.from_ids(["kb-public"]), RagSettings(cloud_enabled=True, prefer_cloud=True)
    )
    assert result.error_code == "MONTHLY_BUDGET_EXCEEDED"
    assert gateway.calls == 0


def test_cloud_call_is_settled_after_atomic_reservation():
    gateway = CloudGateway()
    gate = InMemoryBudgetGate(100)
    result = _orchestrator(gateway, gate).answer_query(
        "public", Scope.from_ids(["kb-public"]), RagSettings(cloud_enabled=True, prefer_cloud=True, cloud_cost_estimate_microunits=25)
    )
    assert result.error_code is None
    assert result.cost_microunits == 25
    assert gateway.calls == 1
    assert next(iter(gate.reservations.values()))["state"] == "settled"
