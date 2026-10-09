"""Four cloud capabilities over existing ports, with independent admission/usage.

No retries, URL discovery, redirects, proxies, fallback models or content logs.
Adapters stay denied without a trusted scope AND a role-specific usage guard.
"""
import base64
import json
import math
import os
import socket
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
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


class _DiagnosticFailure(ProviderUnavailable):
    """Internal fixed codes only; callers still receive ProviderUnavailable."""
    def __init__(self, public_code, stage, code, response_diagnostics=None):
        super().__init__(public_code)
        self.public_code, self.stage, self.code = public_code, stage, code
        self.response_diagnostics = response_diagnostics


class _ReceivedResponse:
    """Decoded response plus safe transport metadata; never contains raw bytes."""
    __slots__ = ('payload', 'http_status_code', 'response_bytes', 'content_type')

    def __init__(self, payload, http_status_code, response_bytes, content_type=None):
        self.payload = payload
        self.http_status_code = http_status_code
        self.response_bytes = response_bytes
        self.content_type = content_type


def _safe_content_type(value):
    if value is None: return None
    if not isinstance(value, str): return 'other'
    mime = value.split(';', 1)[0].strip().lower()
    return mime if mime in {'application/json', 'text/plain', 'text/html',
                           'application/octet-stream'} else 'other'


def _json_type(value):
    if value is None: return 'null'
    if type(value) is bool: return 'boolean'
    if type(value) in (int, float): return 'number'
    if isinstance(value, str): return 'string'
    if isinstance(value, list): return 'array'
    if isinstance(value, dict): return 'object'
    return 'other'


def _response_diagnostics(payload=None, *, received=False, http_status_code=None,
                          response_bytes=None, content_type=None, expected_results=None):
    top_type = _json_type(payload) if received else 'unavailable'
    if isinstance(payload, dict):
        fields = {name: {'present': name in payload,
                         'type': _json_type(payload[name]) if name in payload else 'missing'}
                  for name in ('results', 'model', 'error', 'data')}
        rows = payload.get('results')
        actual_results = len(rows) if isinstance(rows, list) else None
    else:
        fields = {name: {'present': False, 'type': 'unavailable'}
                  for name in ('results', 'model', 'error', 'data')}
        actual_results = None
    document_type = 'unavailable'
    if isinstance(payload, dict) and isinstance(payload.get('results'), list) and payload['results']:
        first = payload['results'][0]
        if isinstance(first, dict):
            document_type = _json_type(first['document']) if 'document' in first else 'missing'
    return dict(http_status_code=http_status_code if type(http_status_code) is int
                    and 100 <= http_status_code <= 599 else None,
        response_bytes=response_bytes if type(response_bytes) is int
                    and 0 <= response_bytes <= 2_000_001 else None,
        content_type=_safe_content_type(content_type),
        top_level_type=top_type, fields=fields, expected_results=expected_results,
        actual_results=actual_results, first_failure_row=None,
        failure_reason=None, document_type=document_type)


def _merge_response_diagnostics(transport, detail, expected_results):
    # Structure detail wins only for fixed structural fields. Observed transport
    # metadata wins when present; an absent value never invents a status/length.
    base = transport or _response_diagnostics(expected_results=expected_results)
    detail = detail or {}
    merged = {key: detail.get(key, value) for key, value in base.items()}
    for key in ('http_status_code', 'response_bytes', 'content_type'):
        if base.get(key) is not None:
            merged[key] = base[key]
    merged['expected_results'] = expected_results
    return merged


def _transport_error_code(error):
    # urllib wraps socket/TLS exceptions in URLError.reason. Never classify
    # by text, retain the exception, or export URLs/headers/provider messages.
    for _ in range(4):
        if isinstance(error, URLError) and isinstance(error.reason, BaseException):
            error = error.reason
        else:
            break
    if isinstance(error, socket.gaierror): return 'DNS_FAILED'
    if isinstance(error, ssl.SSLCertVerificationError): return 'TLS_CERTIFICATE_FAILED'
    if isinstance(error, ssl.SSLError): return 'TLS_HANDSHAKE_FAILED'
    if isinstance(error, TimeoutError): return 'REQUEST_TIMEOUT'
    if isinstance(error, (OSError, URLError)): return 'NETWORK_CONNECTION_FAILED'
    return 'TRANSPORT_FAILED'


def _send(request, timeout):
    with build_opener(_NoRedirect(), ProxyHandler({})).open(request, timeout=timeout) as response:
        status = response.getcode()
        if type(status) is not int or not 100 <= status <= 599:
            status = None
        headers = getattr(response, 'headers', None)
        content_type = _safe_content_type(headers.get('Content-Type') if headers is not None else None)
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise _DiagnosticFailure('PROVIDER_RESPONSE_TOO_LARGE', 'response_decode', 'RESPONSE_TOO_LARGE',
            _response_diagnostics(http_status_code=status, response_bytes=len(raw), content_type=content_type))
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise _DiagnosticFailure('MODEL_REQUEST_FAILED', 'response_decode', 'JSON_RESPONSE_INVALID',
            _response_diagnostics(http_status_code=status, response_bytes=len(raw), content_type=content_type)) from None
    return _ReceivedResponse(payload, status, len(raw), content_type)


def _usage(response):
    raw = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(raw, dict):
        return None
    return {key: value if type(value) is int and value >= 0 else None
            for key, value in raw.items() if key in {"prompt_tokens", "completion_tokens", "total_tokens"}}


def _rerank_usage(response):
    observed = _usage(response)
    if observed:
        return observed
    meta = response.get('meta')
    tokens = meta.get('tokens') if isinstance(meta, dict) else None
    if not isinstance(tokens, dict):
        return None
    counts = [tokens.get('input_tokens'), tokens.get('output_tokens')]
    counts = [n if type(n) is int and n >= 0 else None for n in counts]
    return dict(prompt_tokens=counts[0], completion_tokens=counts[1],
                total_tokens=sum(counts) if all(n is not None for n in counts) else None)


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

    def _request(self, path, payload, timeout, validate, planned_tokens=None, *, usage_parser=_usage,
                 response_diagnostics=False, expected_results=None):
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
        safe_response_diagnostics = None
        stage, error_code, sent = 'transport', 'NONE', True
        try:
            response = self.transport(request, timeout)
            if isinstance(response, _ReceivedResponse):
                safe_response_diagnostics = _response_diagnostics(response.payload, received=True,
                    http_status_code=response.http_status_code, response_bytes=response.response_bytes,
                    content_type=response.content_type,
                    expected_results=expected_results) if response_diagnostics else None
                response = response.payload
            elif response_diagnostics:
                safe_response_diagnostics = _response_diagnostics(response, received=True,
                    expected_results=expected_results)
            stage = 'response_validation'
            if not isinstance(response, dict):
                raise _DiagnosticFailure('MODEL_RESPONSE_INVALID', stage, 'RESPONSE_STRUCTURE_INVALID',
                    safe_response_diagnostics)
            reported = response.get("model")
            if reported is not None and reported != self.spec.model_id:
                raise _DiagnosticFailure('MODEL_RESPONSE_IDENTITY_MISMATCH', stage, 'MODEL_IDENTITY_MISMATCH',
                    safe_response_diagnostics)
            value = validate(response)
            stage = 'usage_parse'
            observed_usage = usage_parser(response)
            status = "ok"
            stage = 'complete'
            return value, observed_usage, (time.perf_counter()-started)*1000
        except HTTPError as exc:
            number = exc.code if type(exc.code) is int and 100 <= exc.code <= 599 else None
            stage = 'http_response'
            if response_diagnostics:
                safe_response_diagnostics = _response_diagnostics(http_status_code=number,
                    content_type=exc.headers.get('Content-Type') if exc.headers is not None else None,
                    expected_results=expected_results)
            error_code = ('HTTP_5XX' if number is not None and number >= 500 else
                          'HTTP_' + str(number) if number in {401, 403, 404, 429} else 'HTTP_OTHER')
            status = 'http_' + str(number) if number is not None else 'error'
            raise ProviderUnavailable('MODEL_HTTP_' + str(number) if number is not None
                                      else 'MODEL_REQUEST_FAILED') from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            stage, error_code = 'response_decode', 'JSON_RESPONSE_INVALID'
            if response_diagnostics:
                safe_response_diagnostics = _response_diagnostics(expected_results=expected_results)
            raise ProviderUnavailable('MODEL_REQUEST_FAILED') from None
        except _DiagnosticFailure as exc:
            stage, error_code = exc.stage, exc.code
            if response_diagnostics:
                safe_response_diagnostics = _merge_response_diagnostics(
                    safe_response_diagnostics, exc.response_diagnostics, expected_results)
            raise ProviderUnavailable(exc.public_code) from None
        except ProviderRequestNotSent:
            # A trusted transport may reject its local allowlist before I/O.
            # Release only explicitly proven not-sent reservations.
            stage, error_code, status, sent = 'admission', 'REQUEST_NOT_SENT', 'not_sent', False
            raise
        except TruncatedAnswer:
            status = "truncated"
            error_code = 'RESPONSE_TRUNCATED'
            raise
        except ProviderUnavailable:
            error_code = 'PROVIDER_UNAVAILABLE' if stage == 'transport' else 'RESPONSE_VALIDATION_FAILED'
            raise
        except Exception as exc:
            error_code = (_transport_error_code(exc) if stage == 'transport' else
                          'USAGE_PARSE_FAILED' if stage == 'usage_parse' else 'RESPONSE_VALIDATION_FAILED')
            raise ProviderUnavailable("MODEL_REQUEST_FAILED") from None
        finally:
            receipt = dict(model_key=self.spec.model_key, role=self.spec.role,
                provider=self.spec.provider, model_id=self.spec.model_id, status=status,
                latency_ms=(time.perf_counter()-started)*1000, planned_tokens=planned_tokens,
                usage_actual=observed_usage, settlement='UNKNOWN',
                diagnostic_schema='provider-diagnostic/v1', diagnostic_stage=stage, error_code=error_code)
            if response_diagnostics:
                receipt['response_diagnostics'] = (safe_response_diagnostics or
                    _response_diagnostics(expected_results=expected_results))
            settlement_failed = False
            try:
                self.usage_guard.settle(reservation, observed_usage, sent=sent)
                if not sent:
                    receipt['settlement'] = 'NOT_SENT'
            except Exception:
                # Keep the primary safe classification if settlement also fails.
                if error_code != 'NONE':
                    receipt.update(request_diagnostic_stage=stage, request_error_code=error_code)
                receipt.update(status='error', diagnostic_stage='usage_settlement',
                               error_code='USAGE_SETTLEMENT_FAILED')
                settlement_failed = True
            # No data, key, provider exception strings, or imagined zero cost.
            self.receipts.append(receipt)
            try:
                record_provider_call(model_key=self.spec.model_key, capability=self.spec.role,
                    provider=self.spec.provider, model=self.spec.model_id, status=receipt['status'],
                    usage=observed_usage, latency_ms=receipt['latency_ms'])
            except Exception:
                if self.spec.role == 'rerank':
                    # Local diagnostics must not retroactively alter rerank settlement.
                    receipt['telemetry_error_code'] = 'USAGE_CAPTURE_FAILED'
                else:
                    raise
            if settlement_failed:
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
        self.circuit_open = False

    def rank(self, question, hits, chunks):
        self.last_result = None
        if self.circuit_open:
            raise ProviderRequestNotSent('RERANK_CIRCUIT_OPEN')
        hits = tuple(hits)
        if not question or not hits or len({h.chunk_id for h in hits}) != len(hits) or any(h.chunk_id not in chunks for h in hits):
            raise ProviderRequestNotSent("RERANK_INPUT_INVALID")
        documents = [chunks[h.chunk_id].content for h in hits]

        def validate(response):
            diagnostics = _response_diagnostics(response, received=True, expected_results=len(hits))
            def fail(public_code, safe_code, reason, row=None, document_type='unavailable'):
                details = dict(diagnostics, first_failure_row=row,
                               failure_reason=reason, document_type=document_type)
                raise _DiagnosticFailure(public_code, 'rerank_validation', safe_code, details)
            if 'results' not in response:
                fail('RERANK_RESPONSE_INVALID', 'RERANK_STRUCTURE_INVALID', 'RESULTS_MISSING')
            rows = response['results']
            if not isinstance(rows, list):
                fail('RERANK_RESPONSE_INVALID', 'RERANK_STRUCTURE_INVALID', 'RESULTS_NOT_ARRAY')
            if len(rows) != len(hits):
                fail('RERANK_RESPONSE_INVALID', 'RERANK_STRUCTURE_INVALID', 'RESULT_COUNT_MISMATCH')
            seen, result, scores = set(), [], []
            for row_position, row in enumerate(rows):
                if not isinstance(row, dict):
                    fail('RERANK_RESPONSE_INVALID', 'RERANK_STRUCTURE_INVALID', 'RESULT_ROW_NOT_OBJECT', row_position)
                document_type = _json_type(row['document']) if 'document' in row else 'missing'
                if 'document' not in row:
                    fail('RERANK_RESPONSE_INVALID', 'RERANK_STRUCTURE_INVALID', 'DOCUMENT_MISSING',
                         row_position, document_type)
                if not isinstance(row['document'], dict):
                    fail('RERANK_RESPONSE_INVALID', 'RERANK_STRUCTURE_INVALID', 'DOCUMENT_TYPE_INVALID',
                         row_position, document_type)
                if not isinstance(row['document'].get('text'), str):
                    fail('RERANK_RESPONSE_INVALID', 'RERANK_STRUCTURE_INVALID', 'DOCUMENT_TEXT_INVALID',
                         row_position, document_type)
                index, score = row.get("index"), row.get("relevance_score")
                if type(index) is not int:
                    fail('RERANK_MAPPING_INVALID', 'RERANK_MAPPING_INVALID', 'INDEX_NOT_INTEGER', row_position, document_type)
                if index in seen:
                    fail('RERANK_MAPPING_INVALID', 'RERANK_MAPPING_INVALID', 'INDEX_DUPLICATE', row_position, document_type)
                if not 0 <= index < len(hits):
                    fail('RERANK_MAPPING_INVALID', 'RERANK_MAPPING_INVALID', 'INDEX_OUT_OF_RANGE', row_position, document_type)
                if row['document']['text'] != documents[index]:
                    fail('RERANK_MAPPING_INVALID', 'RERANK_MAPPING_INVALID', 'DOCUMENT_TEXT_MISMATCH',
                         row_position, document_type)
                try:
                    valid_score = type(score) in (int, float) and math.isfinite(score)
                except (OverflowError, TypeError):
                    valid_score = False
                if not valid_score:
                    fail('RERANK_MAPPING_INVALID', 'RERANK_SCORE_INVALID', 'SCORE_INVALID',
                         row_position, document_type)
                seen.add(index)
                # Preserve original rank, scores, sources and chunk identity.
                result.append(hits[index])
                scores.append(float(score))
            return tuple(result), tuple(scores)

        try:
            result, usage, latency = self._request("/rerank", dict(model=self.spec.model_id, query=question,
            documents=documents, top_n=len(hits), return_documents=True), 30, validate,
                usage_parser=_rerank_usage, response_diagnostics=True, expected_results=len(hits))
        except ProviderRequestNotSent:
            # A local permission/credential/budget denial sent nothing and must
            # not poison unrelated authorized KB requests on this instance.
            raise
        except ProviderUnavailable:
            # No automatic retry/probe after an uncertain or rejected send.
            self.circuit_open = True
            raise
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
