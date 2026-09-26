from __future__ import annotations

from dataclasses import dataclass


# This closed set is the complete V1 smart-mode surface.  It intentionally
# contains no generic filesystem, shell, network, or provider tool.
ALLOWED_READ_TOOLS = frozenset({"list_documents", "search_knowledge", "read_document", "query_knowledge_graph"})


@dataclass(frozen=True)
class AgentLimits:
    max_steps: int = 8
    max_tokens: int = 4000
    max_seconds: float = 30.0
    max_cost_microunits: int = 0
