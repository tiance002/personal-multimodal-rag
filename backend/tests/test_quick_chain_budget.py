from backend.app.application.budget import BudgetDenied, InMemoryBudgetGate
from backend.app.application.knowledge_gateway import KnowledgeGateway
from backend.app.application.quick_chain import LangChainQuickChain, QuickSettings
from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.models import ChunkRecord
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


class SettlementFailGate(InMemoryBudgetGate):
    def settle(self, reservation_id: str, actual_microunits: int) -> None:
        self.mark_unknown(reservation_id)
        raise BudgetDenied("ACTUAL_COST_EXCEEDS_RESERVATION")


def _chain(gateway, gate):
    repository = InMemoryRetrievalRepository()
    repository.add(ChunkRecord("c1", "kb-public", "doc", "ver", "public evidence", {"start": 0}))
    return LangChainQuickChain(
        KnowledgeGateway(HybridRetriever(repository)),
        answer_gateway=gateway,
        budget_gate=gate,
    )


def test_budget_denial_happens_before_cloud_call():
    gateway = CloudGateway()
    result = _chain(gateway, DenyingGate()).invoke(
        "public",
        Scope.from_ids(["kb-public"]),
        settings=QuickSettings(cloud_enabled=True, prefer_cloud=True),
        cloud_allowed_by_kb={"kb-public": True},
    )
    assert result.error_code == "MONTHLY_BUDGET_EXCEEDED"
    assert gateway.calls == 0


def test_cloud_call_is_settled_after_atomic_reservation():
    gateway = CloudGateway()
    gate = InMemoryBudgetGate(100)
    result = _chain(gateway, gate).invoke(
        "public",
        Scope.from_ids(["kb-public"]),
        settings=QuickSettings(cloud_enabled=True, prefer_cloud=True, cloud_cost_estimate_microunits=25),
        cloud_allowed_by_kb={"kb-public": True},
    )
    assert result.error_code is None
    assert result.cost_microunits == 25
    assert gateway.calls == 1
    assert next(iter(gate.reservations.values()))["state"] == "settled"


def test_settlement_failure_is_not_downgraded_to_a_successful_evidence_answer():
    gateway = CloudGateway()
    gate = SettlementFailGate(100)
    result = _chain(gateway, gate).invoke(
        "public",
        Scope.from_ids(["kb-public"]),
        settings=QuickSettings(cloud_enabled=True, prefer_cloud=True, cloud_cost_estimate_microunits=25),
        cloud_allowed_by_kb={"kb-public": True},
    )

    assert result.error_code == "ACTUAL_COST_EXCEEDS_RESERVATION"
    assert result.answer == ""
    assert result.citations == ()
    assert next(iter(gate.reservations.values()))["state"] == "unknown"
