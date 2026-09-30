# Local-first Model Resource Routing

**Observation date:** 2026-09-30  
**Purpose:** Record a conservative local inference profile for the 8 GiB GPU, separating supplied measurements from planning estimates and unmeasured product behavior.

## Observed baseline — MEASURED

These local observations were supplied for the 2026-09-30 audit. They describe the observed host and installed model metadata, not a product request under load.

| Item | Observation |
|---|---|
| Host | Intel Core i7-13650HX; 32 GB system RAM |
| GPU at observation | RTX 4060 Laptop; 8,188 MiB total, 0 MiB used, idle |
| Ollama residency at observation | `ollama ps` was empty; neither model was resident |
| BGE-M3 | 566.70M parameters, F16, **1.2 GB DISK**, embedding dimension 1024, reported maximum context 8,192 |
| Qwen3.5:4b | 4.7B parameters, Q4_K_M, **3.4 GB DISK**, reported maximum context 262,144 |

The model file sizes above are disk measurements. They are **not** VRAM measurements and cannot be used as a proxy for inference memory. The Qwen tag's reported 262,144-token maximum is a model capability ceiling; it is not a feasible context target for this 8 GiB GPU profile.

## Inferred budget and unknown residency

**INFERRED only — rough planning estimates:** BGE-M3 may require about 1.5–2 GiB while resident; Qwen3.5:4b may require about 4–6 GiB at an 8,192-token context with one parallel request. These estimates require a real product-run check and do not guarantee that either model, or both models together, will fit alongside KV cache, runtime overhead, other GPU users, and temporary allocations. Prefer keeping one model resident at a time.

**At the initial audit:** Actual product residency and peak memory had not been measured. See the appended serial-run measurements below; isolated per-model peak, controlled cold-start comparisons and concurrency remain NOT_EVALUATED. The empty `ollama ps` output only establishes that no model was resident at the observation time.

## PROJECT_ADAPTATION — conservative local profile

For a controlled project launch or request profile, use:

| Setting | Value | Classification and purpose |
|---|---:|---|
| `OLLAMA_MAX_LOADED_MODELS` | `1` | **PROJECT_ADAPTATION.** Limit residency to one model while validating the 8 GiB budget. |
| `OLLAMA_NUM_PARALLEL` | `1` | **PROJECT_ADAPTATION.** Keep inference serial; concurrent requests multiply context memory. |
| Qwen Chat `options.num_ctx` | `8192` | **PROJECT_ADAPTATION.** Bound answer-generation context per request. The implemented Quick/Smart composition now sets num_ctx=8192; this is distinct from the model capability ceiling. |
| Resident reranker | None | No GPU budget is reserved for a reranker in this profile. |

Keep query embedding and answer generation single-flight: BGE-M3 serves embeddings and Qwen serves local generation, with one model preferred in memory at a time. Ollama's documented default keep-alive is five minutes, so sequential use may retain the first model temporarily or cause a load/unload transition under a one-model limit. Treat the resulting cold-start and switching latency as a measured product cost.

These values are recommendations for a controlled launch or request profile only. **No startup settings, system environment variables, or Ollama configuration were changed for this document.** The application's local-first policy has no automatic cloud fallback; a local resource or timeout failure must remain an explicit local failure or the existing evidence-only behavior.

## Resource risks

- **KV cache and context:** memory grows with context length; do not use the model's 262,144-token capability as the product context setting.
- **Concurrency:** Ollama documents that required RAM scales with `OLLAMA_NUM_PARALLEL × OLLAMA_CONTEXT_LENGTH`; concurrent requests can multiply context memory.
- **Model residency and switching:** if a new model cannot fit, Ollama queues requests and may unload idle models to make room. A one-model limit can therefore trade residency pressure for model-switch and cold-start latency.
- **VRAM oversubscription:** Ollama's documented default maximum loaded-model count is three times the GPU count. For one GPU, that default is not the proposed project budget; use the one-model adaptation while validating.
- **Optional local CPU path:** CPU execution or offload may be considered only as an explicit on-demand local option after measurement. It is **NOT_EVALUATED** here and may increase latency enough to hit request timeouts. It must not silently fall back to a cloud model.

Ollama also documents that models are kept in memory for five minutes by default and that insufficient memory for another model can queue requests. These runtime behaviors and settings are described in the [Ollama FAQ](https://docs.ollama.com/faq). The FAQ shows `ollama ps` as the way to inspect loaded-model residency and `num_ctx` as an API option for setting request context.

## Product and local resource measurement notes

For a later controlled Quick or Smart product run, record local resource signals alongside the run result. These instructions do not assert that the measurements have already been taken.

| Signal | Read-only check | Record |
|---|---|---|
| Model residency | Run `ollama ps` before, during, and after one product request. | Model name, processor split, resident size, and unload/keep-alive time. |
| GPU allocation | If `nvidia-smi` is available, sample `nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu --format=csv` during the request. | Total, used/free, and observed peak memory; note sampling interval and other GPU work. |
| Product path | Record one request's `run_id`, Quick/Smart mode, model tags, configured `num_ctx`, parallelism, cold/warm state, retrieval/embedding/generation latency, total latency, and outcome. | Separate startup/model-load time from warm request time where available; do not infer VRAM from model disk size. |

### Product residency result

### MEASURED — serial real-material run

32 requests, 33 answer-generation calls (one Smart request used two calls), 32 query embedding calls, zero cloud calls, 146.209 s elapsed. Genuine answer usage: 58,238 input + 6,068 output = 64,306 tokens. Request total latency nearest-rank p50=3,932.634 ms; p95=10,056.963 ms. Cold/warm states were not deliberately randomized or stratified, so these are descriptive timings.

136 host samples at roughly one-second intervals observed GPU memory peak **4,816 MiB** of 8,188 MiB. Host RAM peak **27,032.49 MiB**, minimum available **5,459.38 MiB**. These totals include other running apps/services; they are not per-model allocations or guaranteed microsecond peaks. No resource exhaustion was observed in this serial run. RAM headroom warrants caution before concurrency.

After a separate zero-generation privacy guard request, read-only ollama ps showed BGE-M3 664 MB, 100% GPU, context8192; nvidia-smi showed total host GPU use755 MiB. Those two values have different accounting, not an exact BGE VRAM attribution. Qwen/BGE residency transitions were not continuously sampled per process. No global Ollama environment was changed to force the proposed one-model policy.

CPU offload, concurrent load, controlled cold-start/model-switch cost and isolated model peak remain NOT_EVALUATED. No resident reranker is installed. Evidence-only fallback and explicit local failure preserve the no-automatic-cloud boundary. Private resource samples and original manifest remain under var/reports/real-materials-20260930; the sanitized aggregate is docs/reviews/real-material-verification-20260930.json.
