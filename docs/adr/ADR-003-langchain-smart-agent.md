# ADR 003 LangChain Smart Agent Boundary

**Date:** 2026-09-26

**Status:** accepted for V1.0 implementation

**Scope:** M4 Smart mode, tool execution, evidence and local model compatibility

## Decision

Use `langchain==1.4.2` and `langchain-ollama==1.1.0`. LangChain's
`create_agent` owns the model/tool loop and tool messages. The project exposes
one `SmartAgentPort` and a thin `LangChainAgentAdapter`.

The adapter closes four and only four read-only tools over a server-owned
execution context:

- `list_documents`
- `search_knowledge`
- `read_document`
- `query_knowledge_graph`

The model does not receive knowledge-base scope, run identity, cloud policy,
embedding profile, version authorization, filesystem or network controls.
`KnowledgeToolGateway` and the RAG Core enforce those controls. Search results
are added to a per-run `EvidenceAccumulator`, deduplicated by version/chunk
identity, frozen after the Agent stops, and validated before citations are
persisted. Quick mode remains a direct `RAGOrchestrator` path and never
depends on LangChain.

There is no cloud fallback when local Smart capability is unavailable. Smart
returns a bounded local error; Quick remains available.

## Evidence

```text
\.venv\Scripts\python.exe scripts\smoke_langchain_ollama.py --report var\reports\smoke-langchain-ollama.json
exit=0
model=ornith-1.5:9b; ollama show exit=0; capabilities=tools,thinking,completion,vision;
tool_call_count=1; tool_message_count=1; final_message_count=1; tool_args_valid=true; status=PASS
```

```text
\.venv\Scripts\python.exe scripts\smoke_m4.py --real-model --report var\reports\smoke-m4-real.json
exit=0
status=PASS; provider_mode=real local Ollama; agent_status=completed;
tool steps=list_documents,search_knowledge,read_document; citation_status=200
```

The deterministic adapter test also verifies that the bound tool set is
exactly the four names above, schemas contain no server-controlled fields,
evidence freezes to `E1`, and persistent cancellation prevents the first model
call.

## Consequences

- LangChain upgrades require re-running the deterministic adapter tests and
  the real `smoke_langchain_ollama.py` capability test.
- `ChatOllama.bind_tools()` is not treated as a security or forced-tool
  mechanism; undeclared controls remain unavailable and the adapter checks the
  actual tool loop.
- Model-generated text is not trusted as evidence. Only frozen, readable,
  server-authorized chunks can produce citation records.
- The current real-model result proves the configured local model's capability
  on this machine, not a general guarantee for every Ollama model tag.
