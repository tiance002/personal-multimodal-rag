"""One asynchronous HTTP/SSE transport for every capability and provider.

Connection controls are separate from the request lifetime. No automatic retry,
credential loading, grant lookup, mutable UNKNOWN ledger or provider role fork.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit
from typing import Callable
import httpx

from .types import (CallContext, PreparedCall, ProviderError, TokenUsage,
                    ModelCallTimeout, CallCancelled, RunDeadlineExceeded)
from .safe_network import SafeHTTPTransport


def env_duration(name: str, fallback: float) -> float:
    try:
        value = int(os.environ.get(name, "").strip())
        return float(value) if value > 0 else fallback
    except ValueError:
        return fallback


DEFAULT_CHAT_TIMEOUT = env_duration("WEKNORA_LLM_CHAT_TIMEOUT_SECONDS", 300)
DEFAULT_STREAM_TIMEOUT = env_duration("WEKNORA_LLM_STREAM_TIMEOUT_SECONDS", 600)


@dataclass
class LifetimeState:
    sent: bool = False
    phase: str = "admission"
    cancelled_phase: str = ""


class DenySendGate:
    async def authorize(self, call):
        raise ProviderError("MODEL_SEND_AUTHORIZATION_REQUIRED")

    async def finish(self, call, *, outcome, usage):
        pass


@asynccontextmanager
async def call_lifetime(context: CallContext, fallback: float):
    """WithLLMTimeout: existing deadline, including longer ones, wins."""
    if context.deadline_kind not in ("model", "run"):
        raise ProviderError("MODEL_DEADLINE_CONFIGURATION_INVALID")
    if context.cancelled is not None and context.cancelled.is_set():
        raise CallCancelled()
    loop, owner = asyncio.get_running_loop(), asyncio.current_task()
    deadline = context.deadline if context.deadline is not None else (
        loop.time() + fallback if fallback > 0 else None)
    async def watch_cancel():
        await context.cancelled.wait()
        owner.cancel()
    watcher = asyncio.create_task(watch_cancel()) if context.cancelled is not None else None
    state = LifetimeState()
    try:
        async with asyncio.timeout_at(deadline):
            yield state
    except TimeoutError:
        if context.deadline is not None and context.deadline_kind == "run":
            raise RunDeadlineExceeded(sent=state.sent) from None
        phase = state.cancelled_phase or state.phase
        if phase == "settlement":
            raise ProviderError("MODEL_USAGE_SETTLEMENT_FAILED", sent=state.sent) from None
        if not state.sent:
            raise ProviderError("MODEL_ADMISSION_TIMEOUT") from None
        raise ModelCallTimeout(sent=True) from None
    except asyncio.CancelledError:
        if context.cancelled is not None and context.cancelled.is_set():
            raise CallCancelled(sent=state.sent) from None
        raise
    finally:
        if watcher is not None:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass


def resolve_endpoint(config, path: str) -> str:
    if config.endpoint_url:
        target = config.endpoint_url
    else:
        base = config.base_url.rstrip("/")
        # Embedding/rerank accept complete endpoint URLs. Chat's source does
        # not deduplicate /chat/completions, so neither does this renderer.
        target = base if path != "/chat/completions" and base.endswith(path) else base + path
    if config.query:
        target += ("&" if "?" in target else "?") + urlencode(config.query)
    return target


def validate_endpoint(url: str, allowed: frozenset[str]):
    parts = urlsplit(url)
    if (url not in allowed or parts.scheme not in ("http", "https") or not parts.hostname or
            parts.username or parts.password or parts.fragment):
        raise ProviderError("MODEL_ENDPOINT_NOT_ALLOWED")


@dataclass
class ResponseHandle:
    response: httpx.Response
    usage: TokenUsage
    outcome: str = "UNKNOWN"

    def observed(self, usage: TokenUsage, *, outcome: str = "RESPONSE_RECEIVED"):
        self.usage, self.outcome = usage, outcome


class AsyncHTTPTransport:
    """Application injects explicit endpoints, credentials and durable send gate.

    The transport cannot be usable accidentally: default gate denies. Test HTTP
    adapters also pass through that gate. Requests have no content/key logging.
    """

    def __init__(self, *, allowed_endpoints, gate=None,
                 credential: Callable[[str], str] | None = None,
                 http_transport: httpx.AsyncBaseTransport | None = None):
        self.allowed_endpoints = frozenset(allowed_endpoints)
        self.gate, self.credential = gate or DenySendGate(), credential
        if http_transport is None:
            http_transport = SafeHTTPTransport()
        self.live_ready = type(http_transport) is SafeHTTPTransport
        self._mock_transport = isinstance(http_transport, httpx.MockTransport)
        self._safe_transport = type(http_transport) is SafeHTTPTransport
        self.client = httpx.AsyncClient(transport=http_transport, follow_redirects=False,
            trust_env=False, timeout=httpx.Timeout(None, connect=30),
            limits=httpx.Limits(max_keepalive_connections=5, keepalive_expiry=90))

    async def aclose(self):
        await self.client.aclose()

    @asynccontextmanager
    async def open(self, config, path, body, context, *, capability, stream=False, affinity="", lifetime=None):
        url = resolve_endpoint(config, path)
        validate_endpoint(url, self.allowed_endpoints)
        if not (self._mock_transport or self._safe_transport):
            raise ProviderError("PROVIDER_SAFE_DIAL_UNIMPLEMENTED")
        if not context.request_id or not context.scope.owner_id or not context.scope.knowledge_base_ids:
            raise ProviderError("MODEL_TRUSTED_SCOPE_REQUIRED")
        try:
            wire = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
                              allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            raise ProviderError("MODEL_REQUEST_SERIALIZATION_INVALID") from None
        call = PreparedCall(context, config.provider, config.model, config.model_id, capability,
            url, hashlib.sha256(wire).hexdigest(), len(wire),
            body.get("max_tokens", body.get("max_completion_tokens")))
        if context.cancelled is not None and context.cancelled.is_set():
            raise CallCancelled()
        try:
            await self.gate.authorize(call)
        except ProviderError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ProviderError("MODEL_SEND_AUTHORIZATION_FAILED") from None
        handle, response, outcome, sent = None, None, "NOT_SENT", False
        try:
            if context.cancelled is not None and context.cancelled.is_set():
                raise CallCancelled()
            if context.deadline is not None and context.deadline <= asyncio.get_running_loop().time():
                if context.deadline_kind == "run":
                    raise RunDeadlineExceeded()
                raise ProviderError("MODEL_ADMISSION_TIMEOUT")
            headers = {"Content-Type": "application/json"}
            reserved = {"authorization", "api-key", "x-api-key", "x-goog-api-key", "content-type",
                        "accept", "host", "content-length", "cookie", "proxy-authorization"}
            headers.update({key: value for key, value in config.custom_headers.items() if key.lower() not in reserved})
            try:
                key = self.credential(config.provider) if self.credential is not None else ""
                if not isinstance(key, str):
                    raise ValueError()
            except Exception:
                raise ProviderError("MODEL_CREDENTIAL_UNAVAILABLE") from None
            if config.requires_auth and not key:
                raise ProviderError("MODEL_CREDENTIAL_REQUIRED")
            if key:
                headers["Authorization"] = "Bearer " + key
            if stream:
                headers["Accept"] = "text/event-stream"
            if affinity:
                headers.update(session_id=affinity, **{"x-client-request-id": affinity, "x-session-affinity": affinity})
            try:
                req = self.client.build_request("POST", url, content=wire, headers=headers)
                req.extensions["weknora_allowed_endpoints"] = self.allowed_endpoints
            except (TypeError, ValueError):
                raise ProviderError("MODEL_REQUEST_INVALID") from None
            sent, outcome = True, "UNKNOWN"
            if lifetime is not None:
                lifetime.sent = True
                lifetime.phase = "provider"
            response = await self.client.send(req, stream=True)
            if not 200 <= response.status_code < 300:
                # Keep only source's image-rejection classification, discard
                # the vendor body: it can echo a key or private prompt.
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk[:max(0, (1 << 20)-len(raw))])
                    if len(raw) >= 1 << 20:
                        break
                text = raw.decode("utf-8", "replace").lower()
                unsupported = any(w in text for w in ("multimodal", "image", "vision")) and (
                    "not support" in text or "unsupported" in text or response.status_code == 400)
                error = ProviderError("PROVIDER_HTTP_ERROR", status_code=response.status_code, sent=True)
                error.multimodal_unsupported = unsupported
                raise error
            handle = ResponseHandle(response, TokenUsage())
            yield handle
        except ProviderError as error:
            if not sent and not error.sent:
                outcome = "NOT_SENT"
            raise
        except httpx.TimeoutException:
            if context.cancelled is not None and context.cancelled.is_set():
                raise CallCancelled(sent=sent) from None
            raise ModelCallTimeout(sent=sent) from None
        except httpx.HTTPError:
            raise ProviderError("PROVIDER_TRANSPORT_ERROR", sent=sent) from None
        finally:
            if lifetime is not None:
                if isinstance(sys.exc_info()[1], asyncio.CancelledError):
                    lifetime.cancelled_phase = lifetime.phase
                lifetime.phase = "settlement"
            if response is not None:
                await response.aclose()
            try:
                await self.gate.finish(call, outcome=handle.outcome if handle is not None else outcome,
                    usage=handle.usage if handle is not None else TokenUsage())
            except asyncio.CancelledError:
                raise
            except Exception:
                raise ProviderError("MODEL_USAGE_SETTLEMENT_FAILED", sent=sent) from None
