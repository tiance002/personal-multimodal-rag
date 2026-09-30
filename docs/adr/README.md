# Architecture Decision Records

This directory records decisions that change model routing, storage/versioning, retrieval, privacy, costs, or public contracts. Each decision includes the command evidence that supports it. A document or plan alone is not implementation evidence.

V1.0 decisions:

- ADR-001 records the actual local model capabilities and the L1 fallback policy.
- ADR-002 records budget and public contract decisions.
- ADR-003 records the LangChain Smart Agent boundary and local tool-calling evidence.
- ADR-004 records Qwen3.5 4B routing, deterministic evidence coverage and adaptive chunking.
- ADR-005 records the project-owner-requested, opt-in Langfuse tracing and its fail-closed egress/content-capture policy.

- [2026-09-30 V1 retrieval freeze](../decisions/v1-retrieval-decision.md): adaptive Vector default, conditional Hybrid, bounded evidence and future ranker boundary.
