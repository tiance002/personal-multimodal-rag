# ADR 001 Local Model Capabilities and Fallback

**Date:** 2026-09-26  
**Status:** accepted for the current M0 baseline  
**Scope:** local Chat, query understanding, vision capability discovery, and embedding profile

## Decision

Use the locally installed `ornith-1.5:9b` as the optional L1 Chat/query-understanding candidate and `bge-m3:latest` as the independent embedding candidate. L1 calls use schema-constrained JSON with `think=false`, a bounded token limit, and a timeout. A timeout, invalid response, missing model, or provider error preserves the original q0 and continues deterministic retrieval. The bge-m3 profile is dimension 1024 and is not interchangeable with a different embedding profile.

Cloud providers remain disabled by default. No cloud provider is used as a silent fallback. `cloud_allowed` and the global setting must be checked before any future cloud request.

## Evidence

Commands and observed results from the M0 probe:

```text
E:\ollama\ollama.exe list
exit=0
NAME             ID              SIZE      MODIFIED
bge-m3:latest    790764642607    1.2 GB    4 hours ago
ornith-1.5:9b    e5df7dcdd8a2    6.6 GB    7 hours ago
```

```text
E:\ollama\ollama.exe show ornith-1.5:9b
exit=0
architecture qwen35; parameters 9.0B; context length 262144;
quantization Q4_K_M; capabilities tools, thinking, completion, vision;
projector architecture clip; projector embedding length 1152; dimensions 4096
```

```text
E:\ollama\ollama.exe show bge-m3:latest
exit=0
architecture bert; parameters 566.70M; context length 8192;
embedding length 1024; quantization F16; capability embedding
```

```text
GET http://127.0.0.1:11434/api/version
{"version":"0.34.4"}
```

Embedding smoke: `POST /api/embed` with model `bge-m3:latest` and synthetic text returned one vector with dimension 1024 in approximately 15,854 ms.

Structured Chat smoke: `POST /api/chat` with model `ornith-1.5:9b`, `format=json`, `think=false`, `temperature=0`, and `num_predict=64` returned HTTP 200 in approximately 939 ms with content `{"answer": 1, "ok": true}`. A preceding probe with default thinking and `num_predict=32` returned HTTP 200 but no content with `done_reason=length`; this is why L1 uses `think=false` and a larger bounded output budget.

Repeated release-gate probe on 2026-09-26 also passed: structured Chat 688.9 ms and Embedding 14.2 ms on a warm local service. An earlier cold Chat call measured 54.5–59.2 s, so no latency SLO is claimed; all callers keep bounded timeouts and the retrieval-only fallback.

## Consequences

- M0 can use real local Chat and Embedding adapters; the exact embedding profile is fixed at provider `ollama`, model `bge-m3:latest`, dimension `1024`, distance `cosine` until a later measured ADR changes it.
- L1 is an enhancement, not a prerequisite for q0 retrieval.
- Chat latency and the first-load cost must be recorded in M1/M3 reports; no performance target is claimed from one smoke.
- The model's vision capability is discovered but is not evidence that a separate OCR/VLM workflow is ready; M2 must run its own image/PDF capability probe.
- The initial `ollama list` attempt failed while starting a server because the default model directory/log location was not writable. Starting the server with `OLLAMA_MODELS=E:\llm_load`, `OLLAMA_HOST=127.0.0.1:11434`, and `OLLAMA_NO_CLOUD=1` produced the successful evidence above.
