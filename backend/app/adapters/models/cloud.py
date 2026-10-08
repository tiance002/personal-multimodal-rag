"""Four cloud capabilities over existing ports, with independent admission/usage.

No retries, URL discovery, redirects, proxies, fallback models or content logs.
Adapters stay denied without a trusted scope AND a role-specific usage guard.
"""
import base64
import json
import math
import os
import threading
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from backend.app.domain.embedding_identity import EmbeddingIdentity
from backend.app.domain.model_registry import ModelSpec
from backend.app.ports.model_access import scope_allows
from backend.app.ports.model_usage import record_provider_call
from backend.app.ports.ranking import RerankResult
from backend.app.ports.providers import (
    CaptionResult, ChatResult, EmbeddingResult, ProviderRequestNotSent,
    ProviderUnavailable, TruncatedAnswer,
)
from backend.app.adapters.models.ollama import validate_caption_image


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _send(request, timeout):
    with build_opener(_NoRedirect(), ProxyHandler({})).open(request, timeout=timeout) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ProviderUnavailable("PROVIDER_RESPONSE_TOO_LARGE")
    return json.loads(raw)


def _usage(response):
    raw = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(raw, dict):
        return None
    return {key: value if type(value) is int and value >= 0 else None
            for key, value in raw.items() if key in {"prompt_tokens", "completion_tokens", "total_tokens"}}


class EmbeddingAdmission:
    """Verified model tokenizer contract, deliberately absent by default.

    Counter counts special tokens and MUST disable tokenizer truncation. The
    pinned BGE path implements R4's explicitly bounded client policy while
    retaining UNKNOWN provider-internal non-truncation. Other counters still
    require their independently reviewed contract.
    Tests inject an explicitly SIMULATED counter, never a character estimate.
    Batch/request caps are local limits, not claims about supplier capacity.
    """
    def __init__(self, counter=None, *, tokenizer_identity="UNKNOWN", non_truncating_verified=False,
                 max_batch_items=32):
        self.counter = counter
        self.tokenizer_identity = tokenizer_identity
        self.non_truncating_verified = non_truncating_verified
        self.max_batch_items = max_batch_items
        if type(max_batch_items) is not int or not 1 <= max_batch_items <= 32:
            raise ValueError("EMBEDDING_BATCH_POLICY_INVALID")

    def count(self, texts):
        if not texts or len(texts) > self.max_batch_items or any(not isinstance(t, str) or not t.strip() for t in texts):
            raise ProviderRequestNotSent("EMBEDDING_INPUT_INVALID")
        from backend.app.adapters.models.bge_tokenizer import PinnedBgeM3Tokenizer
        bounded = (type(self.counter) is PinnedBgeM3Tokenizer
                   and self.tokenizer_identity == self.counter.identity)
        if (self.counter is None or self.tokenizer_identity == "UNKNOWN"
                or (not self.non_truncating_verified and not bounded)):
            raise ProviderRequestNotSent("EMBEDDING_CAPACITY_GUARANTEE_UNKNOWN")
        counts = [self.counter(t) for t in texts]
        limit = self.counter.max_input_tokens if bounded else 8192
        if any(type(n) is not int or not 0 < n <= limit for n in counts):
            raise ProviderRequestNotSent("EMBEDDING_TOKEN_LIMIT_EXCEEDED")
        return sum(counts)

    @classmethod
    def from_bge_m3_file(cls, path):
        """Owner-authorized bounded inputs; never claim provider proof.

        Default construction stays closed. Only a hash-verified official
        tokenizer admits this narrower client limit with explicit UNKNOWN
        supplier behavior, as permitted by P4_PRE-R4.
        """
        from backend.app.adapters.models.bge_tokenizer import PinnedBgeM3Tokenizer
        counter = PinnedBgeM3Tokenizer(path)
        return cls(counter, tokenizer_identity=counter.identity,
                   non_truncating_verified=False)


class CloudAdapter:
    provider_kind = "cloud"

    def __init__(self, spec: ModelSpec, *, enabled=False, usage_guard=None, transport=None,
                 max_requests=10, max_planned_tokens=20000):
        if type(max_requests) is not int or not 1 <= max_requests <= 10:
            raise ValueError("PROVIDER_REQUEST_CAP_INVALID")
        if type(max_planned_tokens) is not int or not 1 <= max_planned_tokens <= 20000:
            raise ValueError("PROVIDER_TOKEN_CAP_INVALID")
        self.spec, self.enabled, self.usage_guard = spec, enabled, usage_guard
        self.provider_name = spec.provider
        self.transport = transport or _send
        self.max_requests, self.max_planned_tokens = max_requests, max_planned_tokens
        self.requests = self.planned_tokens = 0
        self.receipts = []
        self._lock = threading.Lock()

    def preflight(self, timeout_seconds):
        if not self.enabled or not self.spec.enabled or not scope_allows(self.spec.role):
            raise ProviderRequestNotSent("MODEL_ROLE_EGRESS_DENIED")
        if self.usage_guard is None:
            raise ProviderRequestNotSent("MODEL_USAGE_GUARD_REQUIRED")
        if not os.environ.get(self.spec.api_key_env, "").strip():
            raise ProviderRequestNotSent("MODEL_CREDENTIAL_MISSING")
        if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 120:
            raise ProviderRequestNotSent("MODEL_TIMEOUT_INVALID")

    def _request(self, path, payload, timeout, validate, planned_tokens=None):
        self.preflight(timeout)
        # ModelSpec validates exact base URL, provider, model and key-env pair.
        request = Request(self.spec.base_url + path, method="POST", headers={
            "Authorization": "Bearer " + os.environ[self.spec.api_key_env],
            "Content-Type": "application/json"}, data=json.dumps(payload).encode())
        if len(request.data) > 2_000_000:
            raise ProviderRequestNotSent("MODEL_REQUEST_TOO_LARGE")
        with self._lock:
            if self.requests >= self.max_requests or (planned_tokens is not None
                    and self.planned_tokens + planned_tokens > self.max_planned_tokens):
                raise ProviderRequestNotSent("MODEL_REQUEST_BUDGET_EXCEEDED")
            try:
                reservation = self.usage_guard.reserve(model_key=self.spec.model_key,
                    role=self.spec.role, model_id=self.spec.model_id, planned_tokens=planned_tokens)
            except Exception:
                raise ProviderRequestNotSent("MODEL_BUDGET_DENIED") from None
            self.requests += 1
            self.planned_tokens += planned_tokens or 0
        started = time.perf_counter()
        response, status, observed_usage = None, "error", None
        try:
            response = self.transport(request, timeout)
            if not isinstance(response, dict):
                raise ProviderUnavailable("MODEL_RESPONSE_INVALID")
            reported = response.get("model")
            if reported is not None and reported != self.spec.model_id:
                raise ProviderUnavailable("MODEL_RESPONSE_IDENTITY_MISMATCH")
            value = validate(response)
            observed_usage = _usage(response)
            status = "ok"
            return value, observed_usage, (time.perf_counter()-started)*1000
        except HTTPError as exc:
            status = "http_" + str(exc.code)
            raise ProviderUnavailable("MODEL_HTTP_" + str(exc.code)) from None
        except TruncatedAnswer:
            status = "truncated"
            raise
        except ProviderUnavailable:
            raise
        except Exception:
            raise ProviderUnavailable("MODEL_REQUEST_FAILED") from None
        finally:
            receipt = dict(model_key=self.spec.model_key, role=self.spec.role,
                provider=self.spec.provider, model_id=self.spec.model_id, status=status,
                latency_ms=(time.perf_counter()-started)*1000, planned_tokens=planned_tokens,
                usage_actual=observed_usage, settlement="UNKNOWN")
            # No data, key, provider exception strings, or imagined zero cost.
            self.receipts.append(receipt)
            record_provider_call(model_key=self.spec.model_key, capability=self.spec.role,
                provider=self.spec.provider, model=self.spec.model_id, status=status,
                usage=observed_usage, latency_ms=receipt['latency_ms'])
            try:
                self.usage_guard.settle(reservation, observed_usage, sent=True)
            except Exception:
                raise ProviderUnavailable("MODEL_USAGE_SETTLEMENT_FAILED") from None


class SiliconFlowEmbedding(CloudAdapter):
    def __init__(self, spec, *, chunking_index_identity, admission=None, **kwargs):
        if spec.role != "embedding" or spec.provider != "siliconflow":
            raise ValueError("EMBEDDING_CAPABILITY_REQUIRED")
        super().__init__(spec, **kwargs)
        self.embedding_model, self.embedding_dimension = spec.model_id, spec.dimension
        self.resolved_revision = spec.resolved_revision
        self.embedding_input_semantics_version = spec.input_semantics_version
        self.identity = EmbeddingIdentity(spec.provider, spec.model_id, spec.resolved_revision,
            spec.dimension, "cosine", chunking_index_identity, spec.input_semantics_version)
        self.admission = admission or EmbeddingAdmission()

    def embed(self, texts, timeout_seconds):
        self.preflight(timeout_seconds)
        inputs = list(texts)
        planned = self.admission.count(inputs)

        def validate(response):
            rows = response.get("data")
            if response.get("model") != self.spec.model_id or not isinstance(rows, list) or len(rows) != len(inputs):
                raise ProviderUnavailable("EMBEDDING_RESPONSE_INVALID")
            vectors = [None] * len(inputs)
            for row in rows:
                index, vector = row.get("index"), row.get("embedding")
                if (type(index) is not int or not 0 <= index < len(inputs) or vectors[index] is not None
                        or not isinstance(vector, list) or len(vector) != self.embedding_dimension):
                    raise ProviderUnavailable("EMBEDDING_MAPPING_INVALID")
                for number in vector:
                    try:
                        valid = type(number) in (int, float) and math.isfinite(number) and abs(number) <= 3.4028234663852886e38
                    except (OverflowError, TypeError):
                        valid = False
                    if not valid:
                        raise ProviderUnavailable("EMBEDDING_VALUE_INVALID")
                if not any(vector):
                    raise ProviderUnavailable("EMBEDDING_ZERO_VECTOR")
                vectors[index] = vector
            return vectors

        vectors, usage, latency = self._request("/embeddings", {"model": self.spec.model_id,
            "input": inputs, "encoding_format": "float"}, timeout_seconds, validate, planned)
        return EmbeddingResult(vectors, self.spec.model_id, self.embedding_dimension, latency,
                               identity_fingerprint=self.identity.fingerprint, usage_actual=usage)


class CloudChat(CloudAdapter):
    def __init__(self, spec, **kwargs):
        if spec.role not in {"chat_cheap", "chat_expensive"}:
            raise ValueError("CHAT_CAPABILITY_REQUIRED")
        super().__init__(spec, **kwargs)

    @staticmethod
    def _validate(response):
        choice = response["choices"][0]
        message, finish = choice["message"], choice["finish_reason"]
        text = message.get("content")
        if message.get("refusal") or not isinstance(text, str) or not text.strip():
            raise ProviderUnavailable("CHAT_REFUSED_OR_INVALID")
        if finish == "length":
            raise TruncatedAnswer(text)
        if finish != "stop":
            raise ProviderUnavailable("CHAT_FINISH_INVALID")
        return text, response.get("model", "UNKNOWN")

    def generate(self, messages, *, timeout_seconds, max_tokens=512):
        if (type(max_tokens) is not int or not 1 <= max_tokens <= 1280 or not messages
                or any(set(m) != {"role", "content"} or m["role"] not in {"system", "user", "assistant"}
                       or not isinstance(m["content"], str) for m in messages)):
            raise ProviderRequestNotSent("CHAT_INPUT_INVALID")
        payload = dict(model=self.spec.model_id, messages=list(messages), max_tokens=max_tokens, stream=False)
        if self.spec.provider == "deepseek":
            payload["thinking"] = {"type": "disabled"}
        observed, usage, latency = self._request("/chat/completions", payload, timeout_seconds, self._validate)
        return ChatResult(observed[0], self.spec.model_id, observed[1], "stop", usage, latency)

    def answer(self, prompt, timeout_seconds):
        return self.generate([{"role": "user", "content": prompt}], timeout_seconds=timeout_seconds).text


class SiliconFlowRerank(CloudAdapter):
    def __init__(self, spec, **kwargs):
        if spec.role != "rerank" or spec.provider != "siliconflow":
            raise ValueError("RERANK_CAPABILITY_REQUIRED")
        super().__init__(spec, **kwargs)
        self.last_result = None

    def rank(self, question, hits, chunks):
        self.last_result = None
        hits = tuple(hits)
        if not question or not hits or len({h.chunk_id for h in hits}) != len(hits) or any(h.chunk_id not in chunks for h in hits):
            raise ProviderRequestNotSent("RERANK_INPUT_INVALID")
        documents = [chunks[h.chunk_id].content for h in hits]

        def validate(response):
            rows = response.get("results")
            if not isinstance(rows, list) or len(rows) != len(hits):
                raise ProviderUnavailable("RERANK_RESPONSE_INVALID")
            seen, result, scores = set(), [], []
            for row in rows:
                index, score = row.get("index"), row.get("relevance_score")
                if (type(index) is not int or not 0 <= index < len(hits) or index in seen
                        or type(score) not in (int, float) or not math.isfinite(score)
                        or ("document" in row and row["document"].get("text") != documents[index])):
                    raise ProviderUnavailable("RERANK_MAPPING_INVALID")
                seen.add(index)
                # Preserve original rank, scores, sources and chunk identity.
                result.append(hits[index])
                scores.append(float(score))
            return tuple(result), tuple(scores)

        result, usage, latency = self._request("/rerank", dict(model=self.spec.model_id, query=question,
            documents=documents, top_n=len(hits), return_documents=False), 30, validate)
        self.last_result = RerankResult(result[0], result[1], latency, usage)
        return self.last_result.hits


class DeepSeekVision(CloudAdapter):
    def __init__(self, spec, **kwargs):
        if spec.role != "vision" or spec.provider != "deepseek":
            raise ValueError("VISION_CAPABILITY_REQUIRED")
        super().__init__(spec, **kwargs)

    def caption_preflight(self, timeout_seconds):
        self.preflight(timeout_seconds)

    def caption_image(self, image_bytes, timeout_seconds):
        digest = validate_caption_image(image_bytes)
        # Actual bytes validated above, never forward caller-supplied URLs/MIME.
        from PIL import Image
        from io import BytesIO
        with Image.open(BytesIO(image_bytes)) as image:
            mime = {"PNG": "image/png", "JPEG": "image/jpeg"}.get(image.format)
        if mime is None:
            raise ProviderRequestNotSent("VISION_IMAGE_FORMAT_UNSUPPORTED")
        payload = dict(model=self.spec.model_id, max_tokens=512, stream=False,
            thinking={"type": "disabled"}, messages=[{"role": "user", "content": [
                {"type": "text", "text": "Describe this figure briefly. Do not infer precise table values."},
                {"type": "image_url", "image_url": {"url": "data:" + mime + ";base64," +
                    base64.b64encode(image_bytes).decode(), "detail": "original"}}]}])
        observed, usage, latency = self._request("/chat/completions", payload, timeout_seconds, CloudChat._validate)
        return CaptionResult(observed[0], self.spec.model_id, observed[1], None, digest,
            "stop", usage, latency, prompt_version="deepseek-figure-caption/v1")
