from backend.app.application.model_policy import ModelPolicy
from backend.app.ports.providers import QueryGatewayResult


class TimeoutGateway:
    def query_expand(self, question: str, timeout_seconds: float) -> QueryGatewayResult:
        raise TimeoutError("local model timed out")


def test_local_query_timeout_keeps_q0_and_uses_deterministic_terms():
    plan, degradation = ModelPolicy().build_query_plan("原始问题", TimeoutGateway(), enabled=True, timeout_seconds=0.01)

    assert plan.q0 == "原始问题"
    assert "原始" in plan.terms
    assert degradation == "LOCAL_QUERY_FALLBACK"
