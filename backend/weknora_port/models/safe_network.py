"""Pinned WeKnora SSRF policy + numeric-IP dialing with original-host TLS.

Uses the installed, verified httpx 0.28.1/httpcore 1.0.9 protocol interfaces.
DNS is checked at the TCP sink, never merely checked then dialed by name.
Tencent/WeKnora MIT semantic adaptation; see NOTICE and P1 parity report.
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
import ssl
import time
from collections.abc import Mapping
from urllib.parse import urlsplit, urljoin
from urllib.request import getproxies_environment, proxy_bypass_environment

import httpcore
import httpx

from .types import ProviderError

RESTRICTED_HOSTS = frozenset(("localhost", "127.0.0.1", "::1", "0.0.0.0",
    "metadata.google.internal", "metadata.tencentyun.com", "metadata.aws.internal",
    "host.docker.internal", "gateway.docker.internal", "kubernetes.docker.internal",
    "kubernetes", "kubernetes.default", "kubernetes.default.svc", "kubernetes.default.svc.cluster.local"))
RESTRICTED_SUFFIXES = (".local", ".localhost", ".internal", ".corp", ".lan", ".home", ".localdomain",
                       ".svc.cluster.local", ".pod.cluster.local")
RESTRICTED_PORTS = frozenset((22,23,25,445,3389,5432,3306,6379,27017,9200,2379,2380,8500,4001))
IP_LIKE = tuple(re.compile(pattern, re.I) for pattern in (
    r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", r"^\d{8,10}$", r"^0[0-7]+\.",
    r"^0x[0-9a-f]+\.", r"^0x[0-9a-f]{6,8}$", r"^[0-9a-f:]+::[0-9a-f:]*$",
    r"^[0-9a-f]{1,4}(:[0-9a-f]{1,4}){7}$", r"^::ffff:\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$",
    r"^\[[0-9a-f:]+\]$"))
V4_RESTRICTED = tuple(ipaddress.ip_network(cidr) for cidr in (
    "10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","127.0.0.0/8","169.254.0.0/16",
    "224.0.0.0/4","100.64.0.0/10","0.0.0.0/8","240.0.0.0/4","198.18.0.0/15",
    "192.0.0.0/24","192.0.2.0/24","198.51.100.0/24","203.0.113.0/24"))


def is_public_ip(raw) -> bool:
    """Exact ipclass.IsPublic categories, not Python's different is_global."""
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv4Address):
        return not any(ip in network for network in V4_RESTRICTED)
    if ip.ipv4_mapped is not None:
        return is_public_ip(str(ip.ipv4_mapped))
    data = ip.packed
    if (ip.is_unspecified or ip.is_loopback or data[0] == 0xff or
        data[0] & 0xfe == 0xfc or (data[0] == 0xfe and data[1] & 0xc0 in (0x80,0xc0))):
        return False
    if data[:6] == bytes.fromhex("0064ff9b0001") or data[:4] == bytes.fromhex("20010000"):
        return False  # local NAT64 / Teredo
    embedded = None
    if data[:2] == bytes.fromhex("2002"):
        embedded = data[2:6]
    elif data[:4] == bytes.fromhex("0064ff9b") and data[4:12] == b"\0"*8:
        embedded = data[12:16]
    elif data[:12] == b"\0"*12:
        embedded = data[12:16]
    return embedded is None or is_public_ip(str(ipaddress.IPv4Address(embedded)))


async def system_resolve(host):
    try:
        entries = await asyncio.get_running_loop().getaddrinfo(host, None, family=socket.AF_UNSPEC,
                                                              type=socket.SOCK_STREAM)
    except OSError:
        raise ProviderError("PROVIDER_DNS_RESOLUTION_FAILED") from None
    return tuple(dict.fromkeys(entry[4][0] for entry in entries))


class SSRFPolicy:
    """Strict public default, explicit upstream admin whitelist semantics.

    Application settings may inject a policy; the standalone default uses
    upstream's named SSRF environment controls, never an old database setting.
    """
    def __init__(self, *, resolver=system_resolve, whitelist=(), whitelist_only=False, clock=time.monotonic):
        self.resolver, self.whitelist_only, self.clock = resolver, whitelist_only, clock
        self.exact, self.suffixes, self.networks = set(), [], []
        self.cache = {}
        for raw in whitelist:
            item = raw.strip().lower()
            if not item:
                continue
            if "/" in item:
                try:
                    self.networks.append(ipaddress.ip_network(item, strict=False))
                except ValueError:
                    continue  # upstream drops malformed whitelist entries
            elif item.startswith("*.") and len(item)>2:
                self.suffixes.append(item[1:])
            elif "*" not in item:
                self.exact.add(item)

    @classmethod
    def from_environment(cls):
        raw = os.environ.get("SSRF_DNS_WHITELIST_ONLY", "").strip()
        enabled = bool(raw) and raw not in ("0","f","F","FALSE","false","False")
        entries = (os.environ.get("SSRF_WHITELIST", "")+","+
                   os.environ.get("SSRF_WHITELIST_EXTRA", "")).split(",")
        return cls(whitelist=entries,whitelist_only=enabled)

    def whitelisted_name(self, host):
        host = host.lower()
        if host in self.exact or any(host.endswith(s) or host == s[1:] for s in self.suffixes):
            return True
        try:
            ip = ipaddress.ip_address(host)
            return any(ip in network for network in self.networks)
        except ValueError:
            return False

    def validate_name(self, host, port, *, direct_url=False):
        if self.whitelisted_name(host):
            return
        if self.whitelist_only:
            raise ProviderError("PROVIDER_HOST_NOT_WHITELISTED")
        if host.lower() in RESTRICTED_HOSTS or host.lower().endswith(RESTRICTED_SUFFIXES):
            raise ProviderError("PROVIDER_SSRF_HOST_BLOCKED")
        if port in RESTRICTED_PORTS:
            raise ProviderError("PROVIDER_SSRF_PORT_BLOCKED")
        if direct_url:
            try:
                ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                raise ProviderError("PROVIDER_SSRF_DIRECT_IP_BLOCKED")
            if any(pattern.search(host) for pattern in IP_LIKE):
                raise ProviderError("PROVIDER_SSRF_DIRECT_IP_BLOCKED")

    async def resolved_addresses(self, host, port, *, direct_url=False):
        if not self.networks or self.whitelist_only:
            self.validate_name(host,port,direct_url=direct_url)
        try:
            answers = (str(ipaddress.ip_address(host)),)
        except ValueError:
            try:
                answers = tuple(await self.resolver(host))
            except asyncio.CancelledError:
                raise
            except ProviderError:
                raise
            except Exception:
                raise ProviderError("PROVIDER_DNS_RESOLUTION_FAILED") from None
        if not answers:
            raise ProviderError("PROVIDER_DNS_EMPTY")
        try:
            normalized = tuple(str(ipaddress.ip_address(answer)) for answer in answers)
        except ValueError:
            raise ProviderError("PROVIDER_DNS_INVALID") from None
        whitelist = self.whitelisted_name(host)
        if not whitelist and not self.whitelist_only and self.networks:
            whitelist = any(ipaddress.ip_address(ip) in network for ip in normalized for network in self.networks)
        if not whitelist:
            self.validate_name(host,port,direct_url=direct_url)
        if not whitelist and not all(is_public_ip(ip) for ip in normalized):
            raise ProviderError("PROVIDER_SSRF_IP_BLOCKED")
        return normalized  # ALL checks finish before the FIRST socket dial

    async def validate_url(self, url):
        try:
            parts = urlsplit(url)
            port = parts.port or (443 if parts.scheme == "https" else 80)
        except ValueError:
            raise ProviderError("PROVIDER_SSRF_URL_INVALID") from None
        if (parts.scheme not in ("http","https") or not parts.hostname or parts.username or
                parts.password or parts.fragment or len(url.encode())>2048):
            raise ProviderError("PROVIDER_SSRF_URL_INVALID")
        key = (parts.scheme,parts.hostname.lower(),port)
        if not self.networks or self.whitelist_only:
            self.validate_name(parts.hostname,port,direct_url=True)
        cached = self.cache.get(key)
        if cached and self.clock()<cached[0]:
            if cached[1]:
                raise ProviderError(cached[1])
            return
        try:
            await self.resolved_addresses(parts.hostname,port,direct_url=True)
        except ProviderError as error:
            self.cache[key] = self.clock()+60,error.code
            raise
        self.cache[key] = self.clock()+60,None


class CheckedStream(httpcore.AsyncNetworkStream):
    def __init__(self, stream, hostname):
        self.stream,self.hostname = stream,hostname
    async def read(self,max_bytes,timeout=None):
        return await self.stream.read(max_bytes,timeout)
    async def write(self,buffer,timeout=None):
        return await self.stream.write(buffer,timeout)
    async def aclose(self):
        await self.stream.aclose()
    def get_extra_info(self,info):
        return self.stream.get_extra_info(info)
    async def start_tls(self,ssl_context,server_hostname=None,timeout=None):
        if (server_hostname!=self.hostname or not ssl_context.check_hostname or
                ssl_context.verify_mode!=ssl.CERT_REQUIRED):
            await self.stream.aclose()
            raise ProviderError("PROVIDER_TLS_VERIFICATION_REQUIRED")
        try:
            return await self.stream.start_tls(ssl_context,server_hostname,10 if timeout is None else min(timeout,10))
        except BaseException:
            await self.stream.aclose()
            raise


class SafeNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, policy: SSRFPolicy, *, backend=None):
        self.policy,self.backend = policy,backend or httpcore.AnyIOBackend()
    async def connect_tcp(self,host,port,timeout=None,local_address=None,socket_options=None):
        # Source DNS lookup uses the caller context; the 30s dialer timeout
        # applies separately to each validated numeric address afterward.
        addresses = await self.policy.resolved_addresses(host,port)
        last = None
        for ip in addresses:
            try:
                stream = await self.backend.connect_tcp(ip,port,30 if timeout is None else min(timeout,30),
                    local_address,socket_options)
                return CheckedStream(stream,host)
            except (httpcore.ConnectError,httpcore.ConnectTimeout) as error:
                last = error  # next validated address, never another hostname lookup
        raise last
    async def connect_unix_socket(self,*args,**kwargs):
        raise ProviderError("PROVIDER_UNIX_SOCKET_NOT_ALLOWED")
    async def sleep(self,seconds):
        await self.backend.sleep(seconds)


def _mapped_core_error(error):
    pairs = ((httpcore.ConnectTimeout,httpx.ConnectTimeout),(httpcore.ReadTimeout,httpx.ReadTimeout),
        (httpcore.WriteTimeout,httpx.WriteTimeout),(httpcore.PoolTimeout,httpx.PoolTimeout),
        (httpcore.ConnectError,httpx.ConnectError),(httpcore.ReadError,httpx.ReadError),
        (httpcore.WriteError,httpx.WriteError),(httpcore.LocalProtocolError,httpx.LocalProtocolError),
        (httpcore.RemoteProtocolError,httpx.RemoteProtocolError),(httpcore.UnsupportedProtocol,httpx.UnsupportedProtocol))
    for source,target in pairs:
        if isinstance(error,source):
            return target("provider network operation failed")
    return error


class CoreResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream):
        self.stream=stream
    async def __aiter__(self):
        try:
            async for chunk in self.stream:
                yield chunk
        except Exception as error:
            raise _mapped_core_error(error) from None
    async def aclose(self):
        await self.stream.aclose()


def environment_proxy(url, environment=None):
    proxies = getproxies_environment() if environment is None else dict(environment)
    parts = urlsplit(url)
    if proxy_bypass_environment(parts.netloc,proxies):
        return None
    return proxies.get(parts.scheme)


class SafeHTTPTransport(httpx.AsyncBaseTransport):
    """Public httpcore pool API adapter; no HTTPX private-attribute mutation.

    Proxy deployments remain explicitly blocked: a remote CONNECT destination
    cannot use our local validated IP sink without additional verified proxy
    semantics. Direct connections are safe and usable with an application gate.
    """
    def __init__(self, *, policy=None, network_backend=None, proxy_environment=None):
        if httpx.__version__!="0.28.1" or httpcore.__version__!="1.0.9":
            raise ProviderError("PROVIDER_NETWORK_RUNTIME_UNVERIFIED")
        self.policy=policy or SSRFPolicy.from_environment()
        self.backend=SafeNetworkBackend(self.policy,backend=network_backend)
        self.proxy_environment=proxy_environment
        self.pools={}
        self.ssl_context=ssl.create_default_context()

    async def _request(self,request):
        await self.policy.validate_url(str(request.url))
        if environment_proxy(str(request.url),self.proxy_environment):
            raise ProviderError("PROVIDER_PROXY_PARITY_UNIMPLEMENTED")
        key=(request.url.raw_scheme,request.url.raw_host,request.url.port)
        if key not in self.pools:
            self.pools[key]=httpcore.AsyncConnectionPool(ssl_context=self.ssl_context,
                max_connections=None,max_keepalive_connections=5,keepalive_expiry=90,
                http1=True,http2=False,retries=0,network_backend=self.backend)
        req=httpcore.Request(request.method,httpcore.URL(scheme=request.url.raw_scheme,
            host=request.url.raw_host,port=request.url.port,target=request.url.raw_path),
            headers=request.headers.raw,content=request.stream,extensions=request.extensions)
        try:
            response=await self.pools[key].handle_async_request(req)
        except Exception as error:
            raise _mapped_core_error(error) from None
        return httpx.Response(response.status,headers=response.headers,stream=CoreResponseStream(response.stream),
                              extensions=response.extensions)

    async def handle_async_request(self,request):
        original=request
        origin=(original.url.scheme,original.url.host,original.url.port)
        allowed=request.extensions.get("weknora_allowed_endpoints",())
        for count in range(10):
            try:
                response=await self._request(request)
            except ProviderError as error:
                error.sent = error.sent or count > 0
                raise
            if response.status_code not in (301,302,303,307,308) or "location" not in response.headers:
                return response
            target=urljoin(str(request.url),response.headers["location"])
            try:
                # net/http drains small redirect bodies (up to 2KiB) before
                # closing, so EndOfMessage reaches the keep-alive connection.
                consumed=0
                async for chunk in response.aiter_bytes():
                    consumed+=len(chunk)
                    if consumed>=2048:
                        break
            finally:
                await response.aclose()
            if count==9:
                raise ProviderError("PROVIDER_REDIRECT_LIMIT_EXCEEDED",sent=True)
            parts=urlsplit(target)
            if (parts.scheme,parts.hostname,parts.port or (443 if parts.scheme=="https" else 80)) != (
                    origin[0],origin[1],origin[2] or (443 if origin[0]=="https" else 80)):
                # Source refuses ANY cross-origin replay of original POST/body,
                # even when a 302 would have converted this hop into a GET.
                raise ProviderError("PROVIDER_CROSS_ORIGIN_REPLAY_BLOCKED",sent=True)
            if target not in allowed:
                raise ProviderError("MODEL_ENDPOINT_NOT_ALLOWED",sent=True)
            method=request.method
            body=request.content
            headers=request.headers.copy()
            if response.status_code in (301,302,303) and method not in ("GET","HEAD"):
                method,body="GET",b""
                headers.pop("content-length",None)
                headers.pop("transfer-encoding",None)
            request=httpx.Request(method,target,headers=headers,content=body,extensions=original.extensions)
        raise ProviderError("PROVIDER_REDIRECT_LIMIT_EXCEEDED",sent=True)

    async def aclose(self):
        for pool in self.pools.values():
            await pool.aclose()
