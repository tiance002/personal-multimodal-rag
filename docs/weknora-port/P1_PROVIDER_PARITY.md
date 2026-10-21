# P1 unified Provider semantic port

Current status: **IMPLEMENTED / OFFLINE_PASS** for the listed protocols and the direct native DNS/TCP/TLS/redirect path using injected offline network fixtures. Configured proxy use remains **PROXY_PARITY_BLOCKED**. The initial protocol-only checkpoint had native networking blocked; the native-completion section below records its replacement. This report does not claim full Go transport parity, provider availability, answer quality, production integration or deployment acceptance.

Worktree: `C:\Users\22088\.codex\worktrees\weknora-python-port\RAG quention`, branch `codex/weknora-python-port`. Starting application baseline: `55372f7bae8e30a8150d16e0eb12262a038cbea2`. Upstream checkout was read only: `E:\RAG-reference\WeKnora-3e8b0bfc`, confirmed HEAD `3e8b0bfc80b845b2d4b2ed683994748741450a97`.

Read the complete supplied `01_实施计划.md`, `02_Codex完整执行Goal.md`, and `03_决策边界与源码清单.md` from `WEKNORA_PYTHON_RAG_P5_HANDOFF_20261013.zip`. This implementation follows section G2 of document 02; it replaces the old-patch direction with a new isolated model package. No legacy QuickChain, output-capacity probe, 512 contract, role-specific HTTP path, Regex Validator, grant or UNKNOWN ledger is imported.

## Implemented shape and upstream mapping

New package: `backend/weknora_port/models/`. `ProviderModel` implements neutral `ChatModel`, `EmbeddingModel`, `RerankModel` and `VisionModel` interfaces. Every capability uses the same `AsyncHTTPTransport`. SiliconFlow, DeepSeek and generic OpenAI-compatible requests differ only in `ModelConfig` / resolved compatibility settings. Final generation role selection is deliberately outside P1.

All upstream paths below are relative to the fixed checkout. Tests are in `tests/weknora_port/`. A passing Python golden fixture is source-derived evidence unless explicitly identified as the executed Go SSE oracle.

| Fixed upstream function / observable behavior | Python implementation | Evidence / status |
|---|---|---|
| `internal/models/api/types.go`: neutral messages, tool calls, thinking levels | `types.py`, `request.py:supports_level/clamp_level` | Golden thinking dialects, malformed effort fallback, always-on reasoning; OFFLINE_PASS |
| `internal/models/api/options.go:CompletionBudget/Reasoning` | `ChatOptions.completion_budget/reasoning` | Completion alias precedence, graded effort over boolean; OFFLINE_PASS |
| `api/compat_settings.go:DefaultOpenAICompletions` | `ChatSettings` | Generic golden body; OFFLINE_PASS |
| `providers/siliconflow.go:newSiliconflowProvider` | `compat.py:provider_config` | `max_tokens`, enable_thinking, thinking budget, cache accounting, embedding batch 32; OFFLINE_PASS |
| `providers/deepseek.go:newDeepseekProvider` | `compat.py:provider_config` | thinking.type, medium->high and xhigh->max, none/auto tool choice; OFFLINE_PASS |
| `catalog/data/models.generated.json`: DeepSeek Flash capacities; BGE M3 dimension override | `compat.py:provider_config` | 1,000,000 context and 384,000 output kept separate; BGE dimensions field absent; OFFLINE_PASS |
| `types/model.go:ModelParameters/ModelSpecOverride` | `ModelConfig` | Model identity, independent capacities and resolved per-model settings; OFFLINE_PASS |
| `openaicompletions/request.go:convertMessages/contentParts` | `request.py:convert_messages` | Structured roles, multimodal parts, tool replies, reasoning replay, opaque metadata; OFFLINE_PASS |
| `api/special_tokens.go:NeutralizeMessageSpecialTokens` | `request.py:neutralize` | Outbound-only delimiter neutralization; original messages unchanged; OFFLINE_PASS |
| `openaicompletions/request.go:buildBody/applySampling/applyTools/applyThinking/finalizeBody` | `request.py:build_chat_body/apply_thinking` | One token field, sampling positive-value semantics, non-stream false omitted, tool capability filtering, thinking variants, response format; OFFLINE_PASS |
| `api/prompt_cache.go:WithSessionCacheKey/ClampPromptCacheKey/ApplyCacheControlBreakpoints/AttachSessionAffinityHeaders` | `ProviderModel._options/_affinity`, request cache helpers, transport headers | 64-character keys, long retention, instruction/tool/conversation cache markers, reserved headers protected; OFFLINE_PASS |
| `openaicompletions/client.go:parseResponse/splitThinkTags/decodeToolCalls/reasoningText` | `provider.py:parse_chat_response/split_think_tags`, `stream.py:decode_tool_calls` | Inline reasoning, missing/empty answer and length retained, reasoning priority, tool decode errors; OFFLINE_PASS |
| `api/usage.go:OpenAIUsage.ToTokenUsage`, `types/chat.go:SetPromptCacheUsage` | `usage.py:normalize_usage/accumulate_usage` | Cache field priority and subset accounting, zero cache miss versus unreported, no double-counting; OFFLINE_PASS with UNKNOWN safety deviation below |
| `api/transport.go:WithLLMTimeout/envDurationSeconds` | `transport.py:call_lifetime/env_duration` | 300 non-stream / 600 stream fallback; supplied shorter or longer deadline preserved; OFFLINE_PASS |
| `api/endpoint.go:Resolve/NewRequest/Do/PostJSON` | `transport.py:resolve_endpoint/AsyncHTTPTransport.open` | Shared JSON POST, full embedding/rerank path, query, bearer auth, reserved custom headers, safe status errors; mock OFFLINE_PASS; native dial PARITY_BLOCKED |
| `api/sse.go:SSEReader.ReadEvent` | `stream.py:sse_events/_line_event` | Optional data space, CRLF, DONE empty data, comments/other fields ignored, scanner size; actual Go oracle 5/5 plus mock stream checks PASS |
| `openaicompletions/client.go:processStream` | `ProviderModel.chat_stream` | Usage-only final chunk, JSON/tool errors, normal EOF, explicit DONE, failed read, cancellation; OFFLINE_PASS |
| `api/stream_assembler.go:Process/End/Fail/OrderedToolCalls/processToolCalls` | `stream.py:StreamAssembler` | Thinking handoff, finish delta and final usage marker, gapped tool indexes, repeated full names, early notification, incomplete error end; OFFLINE_PASS |
| `api/json_field_extractor.go:Feed` | `stream.py:JSONFieldExtractor` | Thinking tool incremental string progress; OFFLINE_PASS for tested escaped/newline strings |
| `api/images.go:IsMultimodalNotSupportedError/StripImagesFromMessages` | transport internal classification, `request.py:strip_images`, ProviderModel source fallback | Explicit image rejection strips images once; each send reauthorizes a distinct child request identity; OFFLINE_PASS |
| `api/openaiembeddings/client.go:BuildRequestBody/Embed`, `api/embeddings.go:PlaceEmbeddings` | `request.py:build_embedding_body`, `ProviderModel.embed` | Optional field config, positional missing indexes, indexed reordering, duplicates/gaps rejected; OFFLINE_PASS |
| `api/cohererank/client.go:BuildRequestBody/Rerank` | `request.py:build_rerank_body`, `ProviderModel.rerank` | Cohere shape, score alias, document string/object, index mapping, score scale; OFFLINE_PASS |
| `api/batch.go:SplitBatches` | `request.py:split_batches` | Document/query limits, contiguous batches, reject indivisible oversize, global batch offsets; OFFLINE_PASS |

The pinned generated catalog has **no XingChenAGI/Xing4.0-29B entry**. Its provider configuration is supported using the SiliconFlow overlay, but no context/output ceiling is invented. A configured per-model output ceiling remains enforced without any saturation probe. DeepSeek Flash's catalog reports 384,000 output tokens; the differing prose API comment is not substituted for that effective catalog value.

## Minimal safety boundaries and current conflicts

1. **UNKNOWN usage:** upstream decoding turns absent counters into integer zero. The owner-authorized safety boundary retains `None` and status `UNKNOWN` for missing/partial usage. Reported zero counters remain real zeros. Cache counters stay subsets of prompt usage. This deliberately prevents an unreported bill from being treated as free.
2. **Actual send gate:** `SendGate.authorize(PreparedCall)` is called before each mock/native send opportunity and must atomically enforce trusted owner/KB/document scope, egress, monthly cost and persistent request identity. Its immutable metadata includes hashes and byte count, not raw prompt text or credentials. P1 supplies only the port and a default-deny implementation; it does not create or alter a production ledger. `finish` receives reported or UNKNOWN usage and must preserve occupancy on incomplete outcomes. Failure to finish prevents successful return. Cancelled authorization must be handled atomically by the application gate.
3. **Model identity:** upstream `rawEnvelope` does not decode `model`, so it ignores a wrong returned model. P1 adds the explicitly authorized identity boundary: a present response model must equal the configured wire model. Missing model identity remains missing and is never fabricated. Mismatched response usage is retained without accepting its answer.
4. **Safe error text/logging:** upstream logs raw requests and surfaces provider error bodies. P1 emits stable error codes, status codes and typed errors without raw provider body, key, document or prompt logging. The image-unsupported classification is retained internally; its raw HTTP body is discarded.
5. **Timeout signal boundary:** the latest owner instruction supersedes the old no-fallback policy **only for actual model-call timeout**. P1 exports `ModelCallTimeout` for provider connect/read failure and expiry while provider I/O is active. Admission expiry is `MODEL_ADMISSION_TIMEOUT`; ledger finish expiry is `MODEL_USAGE_SETTLEMENT_FAILED`; neither is a model timeout. `CallContext.deadline_kind='run'` produces `RunDeadlineExceeded`. Explicit cancellation produces `CallCancelled`; external task/client cancellation propagates `asyncio.CancelledError`. Stream failures carry safe `error_code/error_class`. P1 implements no Cheap retry or Expensive selection. Those decisions belong to P5.
6. **Initial native HTTP security parity conflict (checkpoint `ab8c4f9`, historical):** pinned Go uses `SSRFSafeDialContext` (connection-time DNS/rebinding protections), TLS handshake controls, an environment proxy and up to 10 SSRF-safe redirects. Static endpoint allowlisting plus httpx's default DNS dialing does **not** reproduce that protection. The initial checkpoint therefore blocked native sends with `PROVIDER_SAFE_DIAL_UNIMPLEMENTED`. The direct native backend implemented below replaces that block without a DNS precheck or boolean proof bypass. Configured proxies continue to fail closed.
7. **Stored image resolution:** an application image resolver can be injected. P1 does not fall back to arbitrary filesystem reads or old storage paths. HTTPS/data image URLs retain source request shape. Actual isolated storage resolution belongs to ingestion/storage integration.
8. **Scope of protocols:** P1 implements OpenAI Chat Completions, OpenAI-shaped embeddings and Cohere-shaped rerank, including the requested SiliconFlow/DeepSeek combinations. Other upstream native protocols, sandbox mutation progress and vendor-specific SDKs are not silently approximated or claimed implemented. Generic tool delta assembly and the thinking-tool text path are present.

All real model requests, real billing, deployed API/Worker changes, old DB/Redis/volume/UNKNOWN/Grant writes, push, tag and deployment: **NOT RUN**. The send gate still defaults to deny; arbitrary unaudited transport injection is still blocked. Only the implemented safe native backend or explicit mock transport can reach the gate-controlled send path.

## Source identities

These Git blob identities were read from the fixed checkout, in addition to HEAD:

| Upstream path | Git blob SHA |
|---|---|
| `internal/models/api/openaicompletions/request.go` | `a949891f5e8f854a0eb659a9df67a731f7c5d234` |
| `internal/models/api/openaicompletions/client.go` | `062e46fda28097545c0a1924b09350be0c39c01a` |
| `internal/models/api/transport.go` | `603a01714d02fb13c4a772cee5b4d105eb242d3a` |
| `internal/models/api/usage.go` | `8abb31f4c734947ac7966a07af4cbef483dd3292` |
| `internal/models/api/sse.go` | `909edf3b6819aecd500191f12307b773cd1b19cf` |
| `internal/models/api/stream_assembler.go` | `1874426d900b34ceb4b70ebd1e8b841811abbc8d` |
| `internal/models/providers/siliconflow.go` | `4d036f5df819500bd2d2c9927d9b4abbc61a2c98` |
| `internal/models/providers/deepseek.go` | `4db3f986209883f019dc9f66bcb3b3d88054dcd0` |
| `internal/types/model.go` | `69fb38b86b59a04f2032bd927dff0c9f2ba0e67c` |

Semantic adaptations preserve Tencent's MIT copyright and permission notice in `models/NOTICE`. No third-party component from the upstream repository was vendored into this package.

## Executed evidence

Commands below ran from the isolated worktree. Existing root venv was used; no dependency installation or secret loading occurred.

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -c "import httpx; print(httpx.__version__)"
```

Exit 0: `0.28.1`, the existing locked dependency. Package import smoke also returned exit 0 with `IMPORT_OK httpx=0.28.1`.

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest -q tests/weknora_port/test_provider_request.py tests/weknora_port/test_provider_transport.py tests/weknora_port/test_provider_stream.py tests/weknora_port/test_provider_usage.py --tb=short -p no:cacheprovider
```

Final focused run: **exit 0, 65 passed in 0.73s**, no skipped tests. Earlier focused runs were exit 0 at 59, 63 and 64 checks before additional safety cases were added. These tests use mock HTTP/SSE and no legacy conftest.

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest -q tests/weknora_port/test_upstream_go_oracle.py --tb=short -p no:cacheprovider
```

Exit 0: **5 passed in 0.10s** against the root agent's fixture captured from the unchanged pinned Go SSE reader. Root's initial comparison had pytest exit 1, **2 failed / 3 passed**: Python's DONE event incorrectly retained the sentinel bytes while Go returns empty Data. Corrected `_line_event` to `(True, b'')`; the fixture was not altered. Root executed that Go oracle with Go 1.26.0. This worker reran its Python comparison; request/usage/assembler fixtures remain source-derived rather than an executed full Go provider oracle.

No local commit was independently made by this worker. Root coordinates review, staging and the phase commit. Next required transport action is the verified SSRF-safe native backend; isolated RAG/provider integration can continue using these mock interfaces. There is no claim of `REAL_PROVIDER_PASS` or complete `SOURCE_PARITY` for unexecuted/native paths.

Additional executed checks: scoped `git diff --check` returned exit 0; since the new package/tests were still untracked, an inline Python AST and whitespace check also inspected all 13 owned Python/document files and returned exit 0 with `AST_AND_WHITESPACE_OK files=13`. Root's P0 work advanced the shared branch to `a6ddb167fe4794eade4f5a7401cd97bbb52ef3ed` before this worker finished; that SHA is not a P1 commit or a deployed revision.

未实现的命令不得报告通过。

## Native direct-network completion after checkpoint `ab8c4f9`

Edited paths: `backend/weknora_port/models/safe_network.py` (new), `models/transport.py`, `tests/weknora_port/test_provider_safe_network.py` (new), the former native-block test in `test_provider_transport.py`, and this report. No ingestion, storage, retrieval, fusion or unrelated root-agent changes were modified.

### Source and installed API evidence

Read fixed `internal/utils/security.go` functions `isRestrictedIP`, `isSSRFSafeURL`, `ValidateURLForSSRF`, `SSRFValidatingRoundTripper.RoundTrip`, `newSSRFCheckRedirect`, `NewSSRFSafeHTTPClientWithTransport`, `SSRFSafeDialContext`, whitelist functions and `IsSystemProxy`. Read `internal/utils/ssrf_outbound_cache.go`'s origin cache/60-second TTL and `internal/ipclass/ipclass.go`'s complete classifier. Rechecked `internal/models/api/transport.go`'s TCP/TLS/keepalive controls.

Fixed Git blobs: `security.go=efb64534b223e06c853df75361b325cc66a180d1`, `ssrf_outbound_cache.go=d7ffdbba835760d31a50ffcdb84941877528fbaa`, `ipclass.go=d21e917ed99f03afa63e3b6f4c280cf73800456d`.

Verified installed imports **httpx 0.28.1 / httpcore 1.0.9** before using public `httpcore.AsyncConnectionPool(network_backend=...)`, `AsyncNetworkBackend`, `AsyncNetworkStream`, `AnyIOBackend`, `Request`, `URL`, response/exception interfaces and `httpx.AsyncBaseTransport`. Inspected the installed connection pool, connection, AnyIO backend and HTTPX transport source to confirm origin-host SNI/certificate handling. No package installation, ALPN workaround, insecure TLS or runtime Go dependency was introduced.

### Implemented behavior

- `SSRFPolicy` implements the pinned hostname/suffix/port and IP-like URL rejection sets. `is_public_ip` ports the exact upstream classifier including IPv4-mapped addresses, CGNAT, documentation IPv4 ranges, deprecated site-local IPv6, local NAT64, Teredo and restricted IPv4 embedded in 6to4/NAT64/IPv4-compatible IPv6. This intentionally uses the source's classification rather than Python's different `ipaddress.is_global` result.
- Named/wildcard/IP/CIDR admin whitelist semantics and whitelist-only refusal before DNS are supported. Default policy uses the upstream named SSRF environment controls; an isolated application policy can be injected. It never reads a legacy DB whitelist. Authorized whitelist targets are still numerically pinned, strengthening the source's trusted-whitelist dial path without changing its admission decision. Policy changes must replace the transport/policy generation so prior cached decisions and pooled connections are discarded.
- URL/origin validation caches successful and failed decisions for 60 seconds. That cache never authorizes a socket: every new TCP connection independently resolves the name once, validates **all** answers before the first dial, and gives the underlying backend only those numeric IPs. A public preflight followed by private/mixed DNS at connection time is rejected. Failed dialing may advance to the next already validated address, matching source behavior, with no hostname re-resolution or HTTP retry.
- One public httpcore transport adapter keeps the request origin host intact. A stream wrapper requires the same original hostname for TLS SNI, `CERT_REQUIRED` and hostname checking. DNS uses the enclosing call deadline; each TCP address gets the source 30-second dialing limit and TLS gets the source 10-second handshake limit. Pools are per origin, with up to five idle connections and 90-second keepalive expiry. No provider/role-specific transport is created.
- Same-origin 301/302/303 convert original POST to GET and discard its body; 307/308 retain method/body. Up to nine redirect follows are allowed; a tenth redirect response is rejected, matching Go's `len(via) >= 10`. Small redirect bodies are consumed with a 2KiB bound before close so the HTTP parser sees EndOfMessage and can reuse the connection. Original request identity/Scope and exact configured endpoint permission survive every hop. Cross-origin redirects of the original POST are rejected even if the redirected method would have become GET, matching the source replay boundary; credentials/payload never reach the other origin.
- `AsyncHTTPTransport` now creates `SafeHTTPTransport` by default, while its business send gate remains default-deny. Native direct HTTP is executable when a real application gate authorizes it; tests replace only DNS/TCP/TLS with fakes. Arbitrary non-mock/non-safe adapters remain blocked. HTTPS/data safety, credentials and UNKNOWN accounting remain on the same unified path.

### Remaining precisely bounded proxy difference

Upstream `ProxyFromEnvironment` can use an administrator-configured HTTP/HTTPS proxy and explicitly trusts the configured proxy endpoint at dial time. Reproducing a remote CONNECT destination's DNS/rebinding behavior requires a separately verified proxy path. The new adapter checks applicable HTTP/HTTPS proxy environment and `NO_PROXY` exclusions, and rejects configured proxy use with **`PROVIDER_PROXY_PARITY_UNIMPLEMENTED`** instead of silently sending directly or claiming a local DNS precheck controls remote proxy resolution. Direct configurations and excluded `NO_PROXY` endpoints use the verified direct backend. Full proxy parity is **NOT IMPLEMENTED / BLOCKED**; all native real-provider acceptance remains **NOT RUN**.

### Actual commands and outcomes

From the isolated worktree:

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest -q tests/weknora_port/test_provider_safe_network.py --tb=short -p no:cacheprovider
```

First run: **exit 1, 6 failed / 70 passed**. The redirect response was closed without consuming its HTTP EndOfMessage, discarding the reusable connection. After adding the source-style bounded redirect-body drain: **exit 1, 5 failed / 71 passed**; retained auth headers had been normalized to lowercase by a dictionary copy. Changed to a case-preserving `Headers.copy()`; no expected source behavior or oracle fixture was altered.

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest -q tests/weknora_port/test_provider_safe_network.py tests/weknora_port/test_provider_request.py tests/weknora_port/test_provider_transport.py tests/weknora_port/test_provider_stream.py tests/weknora_port/test_provider_usage.py tests/weknora_port/test_upstream_go_oracle.py --tb=short -p no:cacheprovider
```

Final native/protocol regression: **exit 0, 146 passed in 4.97s**, no skips. The 76 native cases use fake resolver/socket/TLS operations while exercising the real HTTPX/httpcore/h11 request-response path; no public connection or model request occurs. Five SSE cases retain the actual upstream Go oracle. Native Go transport execution is **NOT RUN**; IP/security branches are source-derived fixture evidence. The separate root attempt at a full Go Provider oracle was blocked by Windows CGO/libpg_query, and is not claimed PASS.

Additional checks returned exit 0: scoped `git diff --check` and inline Python AST/whitespace inspection of all five edited native-phase files (`NATIVE_AST_WHITESPACE_OK files=5`). New untracked files were covered by that explicit Python check as well.

Real API credential loading, real network/provider calls, production DB/Redis/UNKNOWN/Grant changes and deployment: **NOT RUN**. This worker made no commit; root coordinates the native phase commit after review. The next integration work can use the unified native default behind application authorization or fake network backends, with proxy configurations visibly blocked.


## Root independent verification and actual Go limits

Root final combined command (exit0,70passed in0.71s):

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' -m pytest --noconftest -q tests/weknora_port/test_provider_request.py tests/weknora_port/test_provider_transport.py tests/weknora_port/test_provider_stream.py tests/weknora_port/test_provider_usage.py tests/weknora_port/test_upstream_go_oracle.py --tb=short -p no:cacheprovider
```

The executed fixed Go SSEReader oracle command (exit0,5cases, no providercalls):

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' scripts/weknora_port_go_oracle.py --upstream 'E:\RAG-reference\WeKnora-3e8b0bfc' --go 'E:\RAG-reference\toolchains\go1.26.0\go\bin\go.exe' --output tests/weknora_port/fixtures/sse_go_oracle.json
```

Only internal/models/api/sse.go is compiled unchanged for that oracle; it is not full upstream Provider execution. Go1.26.0 portable archive SHA2569bbe0fc64236b2b51f6255c05c4232532b8ecc0e6d2e00950bd3021d8a4d07d4 verified against official [Go releases](https://go.dev/dl/), no system installation.

Full BuildRequestBody oracle attempted against an ignored exact-source copy (exit1):

```powershell
& 'E:\RAG quention\.venv\Scripts\python.exe' scripts/weknora_port_provider_oracle.py --upstream 'E:\RAG-reference\WeKnora-3e8b0bfc' --go 'E:\RAG-reference\toolchains\go1.26.0\go\bin\go.exe' --output tests/weknora_port/fixtures/provider_go_oracle.json
```

PARITY_ORACLE_BLOCKED: GoCGO_ENABLED0 and no gcc/clang; upstream utils/inject.go depends on libpg_query Parse/Deparse symbols not built in this Windows configuration. Build log: ignored var/weknora-port/oracle/provider/provider-oracle.log. No renderer output captured; do not label full Go wire comparison passed. Source-derived request fixtures remain useful but narrower evidence. Dependency downloads used fixed go.mod/go.sum; no upstream checkout changes or model requests.

The earlier native DNS/SSRF/redirect blocker is resolved for the implemented direct path by the native follow-up above. The default business gate remains closed. Native direct behavior has fake-network evidence; actual providers and full proxy parity remain unverified/pending, and P1 is not declared fully complete.


## 2026-10-15 current-code addendum

Earlier configured-proxy BLOCKED paragraphs describe historical checkpoints. Current default transport implements HTTP/HTTPS/SOCKS. Scoped environment/trust/deadline repairs and actual Go/protocol evidence are in PROXY_BOUNDARY_REPORT_20261015.md. Two stricter acceptance differences remain pending Owner; real proxies/providers/cutover remain NOT RUN. Historical text/evidence is preserved.
