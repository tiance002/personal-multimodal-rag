from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from backend.app.application.evidence_accumulator import EvidenceAccumulator
from backend.app.application.knowledge_tools import KnowledgeToolGateway
from backend.app.domain.agent_policy import AgentLimits, AgentStep
from backend.app.domain.models import EvidenceSnapshot
from backend.app.domain.scope import Scope


@dataclass(frozen=True)
class SmartAgentResult:
    run_id: str
    status: str
    answer: str = ""
    steps: tuple[AgentStep, ...] = ()
    citations: tuple[str, ...] = ()
    evidence: tuple[EvidenceSnapshot, ...] = ()
    error_code: str | None = None
    cost_microunits: int = 0
    model_calls: int = 0


class SmartAgentPort(Protocol):
    def run(
        self,
        conversation_id: str,
        question: str,
        scope: Scope,
        *,
        run_id: str,
        gateway: KnowledgeToolGateway,
        evidence: EvidenceAccumulator,
        graph_enabled: bool = False,
        trace_store: Any | None = None,
        limits: AgentLimits | None = None,
    ) -> SmartAgentResult: ...


__all__ = ["SmartAgentPort", "SmartAgentResult"]
