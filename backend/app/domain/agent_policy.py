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


@dataclass(frozen=True)
class AgentStep:
    """One persisted, redacted agent step."""

    seq: int
    tool_name: str
    status: str
    input_summary: str
    output_summary: str
    token_count: int = 0
    cost_microunits: int = 0


def estimate_tokens(text: str) -> int:
    """Approximate a token count for a mixed CJK/Latin answer.

    A whitespace split under-counts Chinese to roughly one "token" per answer,
    which made `AgentLimits.max_tokens` unreachable.  CJK characters are close
    to one token each; Latin words average well under one, so count CJK
    codepoints and whitespace-separated Latin runs separately.
    """
    cjk = sum(1 for char in text if "\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff")
    latin = sum(1 for word in text.split() if not any("\u3400" <= char <= "\u9fff" for char in word))
    return cjk + latin
