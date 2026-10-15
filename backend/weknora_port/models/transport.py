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
from dataclasses import dataclass, replace
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


def configured_endpoint(config, capability="chat", *, stream=False):
    """Exact source protocol URL; calculation never reads a key or sends HTTP."""
    from dataclasses import replace
    base = config.base_url.rstrip("/")
    if capability in ("chat", "vision"):
        if config.api == "anthropic-messages":
            path = urlsplit(base).path.rstrip("/")
            return resolve_endpoint(config, "/messages" if path.endswith(("/v1","/v1beta")) else "/v1/messages") if not path.endswith("/messages") else resolve_endpoint(replace(config,endpoint_url=config.endpoint_url or base), "")
        if config.api == "google-generative-ai":
            endpoint = config.endpoint_url
            if not endpoint:
                prefix = config.protocol_settings.get(config.api,{}).get("api_version_prefix", "/v1beta")
                if urlsplit(base).path in ("", "/") and prefix:
                    base += "/"+prefix.strip("/")
                endpoint = base + "/models/" + config.model + (":streamGenerateContent" if stream else ":generateContent")
            elif stream and endpoint.endswith(":generateContent"):
                endpoint = endpoint[:-len(":generateContent")]+":streamGenerateContent"
            query = {**config.query, **({"alt":"sse"} if stream else {})}
            return resolve_endpoint(replace(config,endpoint_url=endpoint,query=query), "")
        return resolve_endpoint(config, {"openai-responses":"/responses", "ollama":"/api/chat"}.get(config.api,"/chat/completions"))
    if capability == "embedding":
        if config.embedding_api == "google-embeddings":
            endpoint = config.endpoint_url or base.removesuffix("/openai")+"/models/"+config.model.removeprefix("models/")+":batchEmbedContents"
            return resolve_endpoint(replace(config,endpoint_url=endpoint), "")
        return resolve_endpoint(config, config.embedding.path or "/embeddings")
    if capability == "rerank":
        if config.rerank_api == "tencent-lkeap":
            return "https://lkeap.tencentcloudapi.com/"
        if config.rerank_api == "volcengine-knowledge":
            parts=urlsplit(base)
            return parts.scheme+"://"+parts.netloc+"/api/knowledge/service/rerank"
        return resolve_endpoint(config, config.reranking.path or "/rerank")
    if capability=="local_model_check" and config.source=="local" and config.api=="ollama":
        return resolve_endpoint(config,"/api/tags")
    if capability=="local_model_pull" and config.source=="local" and config.api=="ollama" and config.extra_config.get("local_allow_pull")=="true":
        return resolve_endpoint(config,"/api/pull")
    raise ProviderError("MODEL_CAPABILITY_UNSUPPORTED")


def endpoint_urls(config):
    """Closed endpoint set for this model's configured source capability."""
    capability = {"Embedding":"embedding", "Rerank":"rerank"}.get(config.model_type,"chat")
    urls = [configured_endpoint(config,capability)]
    if capability == "chat":
        urls.append(configured_endpoint(config,capability,stream=True))
    if config.source=="local":
        urls.append(configured_endpoint(config,"local_model_check"))
        if config.extra_config.get("local_allow_pull")=="true":urls.append(configured_endpoint(config,"local_model_pull"))
    return tuple(dict.fromkeys(urls))


def validate_endpoint(url: str, allowed: frozenset[str]):
    parts = urlsplit(url)
    if (url not in allowed or parts.scheme not in ("http", "https") or not parts.hostname or
            parts.username or parts.password or parts.fragment):
        raise ProviderError("MODEL_ENDPOINT_NOT_ALLOWED")


class ResponseHandle:
    def __init__(self,response,usage):
        self.response,self._usage,self.outcome=response,usage,'UNKNOWN'

    @property
    def usage(self):return self._usage

    @usage.setter
    def usage(self,value):
        old=self._usage.provider_usage_facts
        new=value.provider_usage_facts
        if old is not None:
            if new is None:
                value=replace(value,provider_usage_facts=old)
            else:
                history=old.prior_numeric_raw
                if old.numeric_raw!=new.numeric_raw:history=(*history,old.numeric_raw)
                new=replace(new,observed_lower_bound=max(old.accounting()[1],new.accounting()[1]),prior_numeric_raw=history)
                value=replace(value,provider_usage_facts=new)
        self._usage=value

    def observed(self, usage: TokenUsage, *, outcome: str = "RESPONSE_RECEIVED"):
        self.usage, self.outcome = usage, outcome

    def capture(self, raw):
        from .usage_facts import capture_usage
        self.usage=replace(self.usage,provider_usage_facts=capture_usage(raw))


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
        # Synthetic fact binding stays closed-fixture only; approved wiring clears this.
        self.facts_mock_only = True
        self.client = httpx.AsyncClient(transport=http_transport, follow_redirects=False,
            trust_env=False, timeout=httpx.Timeout(None, connect=30),
            limits=httpx.Limits(max_keepalive_connections=5, keepalive_expiry=90))

    async def aclose(self):
        await self.client.aclose()

    @asynccontextmanager
    async def open(self, config, path, body, context, *, capability, stream=False, affinity="", lifetime=None, method="POST"):
        if method!="POST" and not (method=="GET" and capability=="local_model_check" and config.source=="local"):
            raise ProviderError("MODEL_HTTP_METHOD_NOT_ALLOWED")
        url = configured_endpoint(config, capability, stream=stream)
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
            body.get("max_tokens", body.get("max_completion_tokens", body.get("max_output_tokens",
                (body.get("generationConfig") or {}).get("maxOutputTokens", (body.get("options") or {}).get("num_predict") if config.source=="local" else None)))))
        binder=getattr(self,'bind_facts',None)
        if binder is not None:
            # The synthetic binder is closed-fixture only; an approved binder binds
            # real per-source facts and is allowed on the safe (real) transport.
            if getattr(self,'facts_mock_only',True) and not self._mock_transport:
                raise ProviderError('SYNTHETIC_TRANSPORT_REQUIRED')
            call=replace(call,bound_facts=binder(config,context,body,wire,capability))
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
        from .usage_facts import SIMULATED_USAGE
        simulation_token=SIMULATED_USAGE.set(self._mock_transport)
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
                key = self.credential(config.provider) if config.source!="local" and self.credential is not None else ""
                if not isinstance(key, str):
                    raise ValueError()
            except Exception:
                raise ProviderError("MODEL_CREDENTIAL_UNAVAILABLE") from None
            if config.requires_auth and config.auth_style != "signed" and not key:
                raise ProviderError("MODEL_CREDENTIAL_REQUIRED")
            if capability == "rerank" and config.rerank_api in ("tencent-lkeap","volcengine-knowledge"):
                from .signing import tencent_lkeap_headers,volcengine_headers
                secret=config.extra_config.get("app_secret","").strip() or config.extra_config.get("secret_key","")
                if config.rerank_api=="tencent-lkeap":
                    headers.update(tencent_lkeap_headers(key,secret,wire,region=config.extra_config.get("region","") or "ap-guangzhou"))
                else:
                    headers.update(volcengine_headers(key,secret,wire,url,headers,region=config.extra_config.get("region","") or "cn-beijing"))
            elif config.auth_style == "signed":
                from .signing import weknora_cloud_headers
                headers.update(weknora_cloud_headers(config.extra_config.get("app_id",""),config.extra_config.get("app_secret","") or key,wire))
            elif key and config.auth_style in ("api-key","x-api-key","x-goog-api-key"):
                headers[config.auth_style]=key
            elif key and config.auth_style != "none":
                headers["Authorization"] = "Bearer " + key
            if capability in ("chat","vision") and config.api == "anthropic-messages":
                settings=config.protocol_settings.get(config.api,{})
                headers["anthropic-version"]=settings.get("version") or "2023-06-01"
                betas=[]
                values=list(settings.get("beta_headers",[]))
                if body.get("thinking",{}).get("type")=="enabled" and body.get("tools"):
                    values.append(settings.get("interleaved_thinking_beta", ""))
                values.append(headers.get("anthropic-beta", ""))
                for value in values:
                    for beta in value.split(","):
                        if beta.strip() and beta.strip() not in betas: betas.append(beta.strip())
                if betas: headers["anthropic-beta"]=",".join(betas)
            if stream:
                headers["Accept"] = "application/x-ndjson" if config.source=="local" else "text/event-stream"
            if affinity:
                headers.update(session_id=affinity, **{"x-client-request-id": affinity, "x-session-affinity": affinity})
            try:
                req = self.client.build_request(method, url, content=wire, headers=headers)
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
            SIMULATED_USAGE.reset(simulation_token)
            if lifetime is not None:
                if isinstance(sys.exc_info()[1], asyncio.CancelledError):
                    lifetime.cancelled_phase = lifetime.phase
                lifetime.phase = "settlement"
            if response is not None:
                await response.aclose()
            try:
                usage=handle.usage if handle is not None else TokenUsage()
                if usage.provider_usage_facts is not None and self._mock_transport:
                    usage=replace(usage,provider_usage_facts=replace(usage.provider_usage_facts,simulated=True))
                await self.gate.finish(call, outcome=handle.outcome if handle is not None else outcome,usage=usage)
            except asyncio.CancelledError:
                raise
            except Exception:
                raise ProviderError("MODEL_USAGE_SETTLEMENT_FAILED", sent=sent) from None
