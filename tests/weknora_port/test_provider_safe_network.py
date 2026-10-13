"""Native HTTP/core protocol with fake DNS/TCP/TLS; no public connections."""
import asyncio
import json
import ssl
from dataclasses import replace
import httpcore
import httpx
import pytest
from backend.weknora_port.models import (AsyncHTTPTransport,ProviderModel,provider_config,Message,
    ChatOptions,ProviderError,ModelCallTimeout,CallCancelled)
from backend.weknora_port.models.safe_network import (SSRFPolicy,SafeNetworkBackend,SafeHTTPTransport,
    CheckedStream,is_public_ip,environment_proxy)
from tests.weknora_port.test_provider_transport import Gate,context


class Resolver:
    def __init__(self,*answers):
        self.answers=list(answers)
        self.calls=[]
    async def __call__(self,host):
        self.calls.append(host)
        return self.answers.pop(0) if len(self.answers)>1 else self.answers[0]


class RecordedStream(httpcore.AsyncMockStream):
    def __init__(self,packets):
        super().__init__(packets)
        self.written=[]
        self.tls=[]
    async def write(self,data,timeout=None):
        self.written.append(data)
    async def start_tls(self,ssl_context,server_hostname=None,timeout=None):
        self.tls.append((ssl_context.verify_mode,ssl_context.check_hostname,server_hostname,timeout))
        return self


class Dialer(httpcore.AsyncNetworkBackend):
    def __init__(self,streams=(),fail=()):
        self.streams=list(streams)
        self.calls=[]
        self.fail=set(fail)
    async def connect_tcp(self,host,port,timeout=None,local_address=None,socket_options=None):
        self.calls.append((host,port,timeout))
        if host in self.fail:
            raise httpcore.ConnectError("SIMULATED")
        return self.streams.pop(0) if self.streams else RecordedStream([])
    async def sleep(self,seconds):
        await asyncio.sleep(seconds)


def http_packet(status=200,*,body=b"",location=None):
    headers=[f"HTTP/1.1 {status} fixture",f"Content-Length: {len(body)}","Content-Type: application/json"]
    if location:
        headers.append("Location: "+location)
    return "\r\n".join(headers).encode()+b"\r\n\r\n"+body


@pytest.mark.parametrize("ip",[
    "0.0.0.0","0.1.2.3","10.1.2.3","127.0.0.1","172.16.1.1","192.168.1.1","169.254.169.254",
    "100.64.1.1","224.0.0.1","240.0.0.1","198.18.1.1","192.0.0.9","192.0.2.1","198.51.100.1","203.0.113.1",
    "::","::1","fc00::1","fe80::1","fec0::1","ff02::1","::ffff:169.254.169.254",
    "2002:a9fe:a9fe::1","64:ff9b::a9fe:a9fe","64:ff9b:1::808:808","2001::808:808","::a9fe:a9fe"])
def test_exact_upstream_denied_ip_classes(ip):
    assert not is_public_ip(ip)


@pytest.mark.parametrize("ip",["8.8.8.8","1.1.1.1","192.0.1.9","2001:4860:4860::8888",
    "2002:808:808::1","64:ff9b::808:808","::808:808","::ffff:8.8.8.8","2001:db8::1"])
def test_upstream_public_classes_are_not_replaced_with_python_is_global(ip):
    assert is_public_ip(ip)


@pytest.mark.parametrize("host",["localhost","metadata.google.internal","metadata.tencentyun.com",
    "host.docker.internal","kubernetes.default.svc.cluster.local","x.corp","x.lan","x.home","x.localdomain"])
def test_restricted_host_checks_happen_before_dns(host):
    async def run():
        resolver=Resolver(("8.8.8.8",))
        with pytest.raises(ProviderError,match="PROVIDER_SSRF_HOST_BLOCKED"):
            await SSRFPolicy(resolver=resolver).validate_url("https://"+host+"/v1")
        assert resolver.calls==[]
    asyncio.run(run())


@pytest.mark.parametrize("url",["https://8.8.8.8/v1","https://[2001:4860::1]/v1",
    "https://2130706433/v1","https://0177.0.0.1/v1","https://0x7f.0x0.0x0.0x1/v1",
    "https://0x7f000001/v1","https://safe.example:5432/v1","file://safe.example/v1"])
def test_direct_ip_obfuscation_ports_and_scheme_blocked(url):
    async def run():
        resolver=Resolver(("8.8.8.8",))
        with pytest.raises(ProviderError):
            await SSRFPolicy(resolver=resolver).validate_url(url)
        assert resolver.calls==[]
    asyncio.run(run())


def test_numeric_pinned_dial_and_original_hostname_sni_certificate_checks():
    async def run():
        resolver=Resolver(("8.8.8.8","1.1.1.1"))
        raw=RecordedStream([])
        dialer=Dialer([raw],fail=("8.8.8.8",))
        backend=SafeNetworkBackend(SSRFPolicy(resolver=resolver),backend=dialer)
        stream=await backend.connect_tcp("api.fixture.example",443,30)
        await stream.start_tls(ssl.create_default_context(),"api.fixture.example",30)
        assert resolver.calls==["api.fixture.example"]
        assert dialer.calls==[("8.8.8.8",443,30),("1.1.1.1",443,30)]
        assert raw.tls==[(ssl.CERT_REQUIRED,True,"api.fixture.example",10)]
        assert all(host!="api.fixture.example" for host,_,_ in dialer.calls)
    asyncio.run(run())


@pytest.mark.parametrize("addresses",[("8.8.8.8","127.0.0.1"),("10.0.0.1",),("203.0.113.1",),()])
def test_all_dns_answers_validated_before_any_socket_even_mixed_public_private(addresses):
    async def run():
        dialer=Dialer()
        backend=SafeNetworkBackend(SSRFPolicy(resolver=Resolver(addresses)),backend=dialer)
        with pytest.raises(ProviderError):
            await backend.connect_tcp("api.fixture.example",443)
        assert dialer.calls==[]
    asyncio.run(run())


def test_rebinding_between_url_preflight_and_connection_is_blocked_at_tcp_sink():
    async def run():
        resolver=Resolver(("8.8.8.8",),("169.254.169.254",))
        policy=SSRFPolicy(resolver=resolver)
        await policy.validate_url("https://api.fixture.example/v1")
        # Cached 60s URL validation is not treated as TCP dialing authority.
        await policy.validate_url("https://api.fixture.example/other")
        dialer=Dialer()
        with pytest.raises(ProviderError,match="PROVIDER_SSRF_IP_BLOCKED"):
            await SafeNetworkBackend(policy,backend=dialer).connect_tcp("api.fixture.example",443)
        assert len(resolver.calls)==2 and dialer.calls==[]
    asyncio.run(run())


@pytest.mark.parametrize("wrong_sni,insecure",[(True,False),(False,True)])
def test_tls_cannot_disable_verification_or_replace_sni(wrong_sni,insecure):
    async def run():
        raw=RecordedStream([])
        context=ssl.create_default_context()
        if insecure:
            context.check_hostname=False
            context.verify_mode=ssl.CERT_NONE
        with pytest.raises(ProviderError,match="PROVIDER_TLS_VERIFICATION_REQUIRED"):
            await CheckedStream(raw,"original.example").start_tls(context,"attacker.example" if wrong_sni else "original.example")
        assert raw.tls==[] and raw._closed
    asyncio.run(run())


def test_native_model_http_round_trip_uses_actual_httpcore_with_fake_tcp_tls_only():
    async def run():
        data={"model":"m","choices":[{"message":{"content":"answer"},"finish_reason":"stop"}],
            "usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}
        raw=RecordedStream([http_packet(body=json.dumps(data).encode())])
        resolver=Resolver(("8.8.8.8",))
        dialer=Dialer([raw])
        safe=SafeHTTPTransport(policy=SSRFPolicy(resolver=resolver),network_backend=dialer,proxy_environment={})
        gate=Gate()
        transport=AsyncHTTPTransport(allowed_endpoints=["https://api.fixture.example/v1/chat/completions"],
            gate=gate,credential=lambda p:"SIMULATED-NOT-A-KEY",http_transport=safe)
        model=ProviderModel(provider_config("openai-compatible","m",base_url="https://api.fixture.example/v1"),transport)
        try:
            out=await model.chat((Message("user","q"),),ChatOptions(max_tokens=1024),context())
            assert out.content=="answer" and out.usage.total_tokens==5
            assert transport.live_ready and dialer.calls==[("8.8.8.8",443,30)]
            assert raw.tls[0][2:]==("api.fixture.example",10)
            request=b"".join(raw.written)
            assert b"Host: api.fixture.example" in request
            assert b"api.fixture.example" not in request.split(b"\r\n",1)[0]
            assert len(gate.calls)==len(gate.finishes)==1
        finally:
            await transport.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("status,method",[(301,"GET"),(302,"GET"),(303,"GET"),(307,"POST"),(308,"POST")])
def test_source_same_origin_redirect_method_and_payload_semantics(status,method):
    async def run():
        raw=RecordedStream([http_packet(status,location="/next"),http_packet(body=b'{}')])
        safe=SafeHTTPTransport(policy=SSRFPolicy(resolver=Resolver(("8.8.8.8",))),
            network_backend=Dialer([raw]),proxy_environment={})
        client=httpx.AsyncClient(transport=safe,timeout=None)
        try:
            request=client.build_request("POST","https://api.fixture.example/start",content=b"BODY",
                headers={"Authorization":"Bearer SIMULATED"})
            request.extensions["weknora_allowed_endpoints"]=("https://api.fixture.example/start","https://api.fixture.example/next")
            result=await client.send(request)
            assert result.status_code==200
            written=b"".join(raw.written)
            assert (method+" /next HTTP/1.1").encode() in written
            assert written.count(b"BODY")== (1 if method=="GET" else 2)
            assert written.count(b"Authorization: Bearer SIMULATED")==2
        finally:
            await client.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("target,code",[("https://other.example/next","PROVIDER_CROSS_ORIGIN_REPLAY_BLOCKED"),
    ("http://api.fixture.example/next","PROVIDER_CROSS_ORIGIN_REPLAY_BLOCKED"),
    ("https://api.fixture.example:444/next","PROVIDER_CROSS_ORIGIN_REPLAY_BLOCKED"),
    ("https://api.fixture.example/unconfigured","MODEL_ENDPOINT_NOT_ALLOWED")])
def test_redirect_cannot_leak_payload_credentials_or_bypass_endpoint_scope(target,code):
    async def run():
        raw=RecordedStream([http_packet(302,location=target)])
        resolver=Resolver(("8.8.8.8",))
        dialer=Dialer([raw])
        safe=SafeHTTPTransport(policy=SSRFPolicy(resolver=resolver),network_backend=dialer,proxy_environment={})
        client=httpx.AsyncClient(transport=safe,timeout=None)
        try:
            request=client.build_request("POST","https://api.fixture.example/start",content=b"BODY")
            request.extensions["weknora_allowed_endpoints"]=("https://api.fixture.example/start",target)
            if "unconfigured" in target:
                request.extensions["weknora_allowed_endpoints"]=("https://api.fixture.example/start",)
            with pytest.raises(ProviderError,match=code):
                await client.send(request)
            assert len(dialer.calls)==1 and b"/next" not in b"".join(raw.written)
        finally:
            await client.aclose()
    asyncio.run(run())


def test_redirect_limit_matches_go_len_via_ge_ten():
    async def run():
        raw=RecordedStream([http_packet(307,location="/loop") for _ in range(10)])
        safe=SafeHTTPTransport(policy=SSRFPolicy(resolver=Resolver(("8.8.8.8",))),
            network_backend=Dialer([raw]),proxy_environment={})
        client=httpx.AsyncClient(transport=safe,timeout=None)
        try:
            request=client.build_request("POST","https://api.fixture.example/loop",content=b"BODY")
            request.extensions["weknora_allowed_endpoints"]=(str(request.url),)
            with pytest.raises(ProviderError,match="PROVIDER_REDIRECT_LIMIT_EXCEEDED"):
                await client.send(request)
            assert b"".join(raw.written).count(b"POST /loop HTTP/1.1")==10
        finally:
            await client.aclose()
    asyncio.run(run())


def test_cancel_during_native_dns_stops_before_numeric_socket_dial():
    async def run():
        entered=asyncio.Event()
        async def resolver(host):
            entered.set()
            await asyncio.sleep(100)
        dialer=Dialer()
        safe=SafeHTTPTransport(policy=SSRFPolicy(resolver=resolver),network_backend=dialer,proxy_environment={})
        transport=AsyncHTTPTransport(allowed_endpoints=["https://api.fixture.example/v1/chat/completions"],
            gate=Gate(),credential=lambda p:"SIMULATED",http_transport=safe)
        cancelled=asyncio.Event()
        try:
            model=ProviderModel(provider_config("openai-compatible","m",base_url="https://api.fixture.example/v1"),transport)
            task=asyncio.create_task(model.chat((Message("user","q"),),None,replace(context(),cancelled=cancelled)))
            await entered.wait();cancelled.set()
            with pytest.raises(CallCancelled):
                await task
            assert dialer.calls==[]
        finally:
            await transport.aclose()
    asyncio.run(run())


def test_whitelist_only_never_resolves_unlisted_and_explicit_admin_private_whitelist_pins():
    async def run():
        resolver=Resolver(("127.0.0.1",))
        policy=SSRFPolicy(resolver=resolver,whitelist=("*.controlled.example",),whitelist_only=True)
        with pytest.raises(ProviderError,match="PROVIDER_HOST_NOT_WHITELISTED"):
            await policy.validate_url("https://notlisted.example/v1")
        assert resolver.calls==[]
        await policy.validate_url("https://controlled.example/v1")
        dialer=Dialer()
        await SafeNetworkBackend(policy,backend=dialer).connect_tcp("sub.controlled.example",443)
        assert dialer.calls==[("127.0.0.1",443,30)]
    asyncio.run(run())


def test_cidr_admin_whitelist_source_semantics_and_error_cache():
    async def run():
        resolver=Resolver(("10.0.0.1",))
        policy=SSRFPolicy(resolver=resolver,whitelist=("10.0.0.0/8",))
        await policy.validate_url("https://service.internal:5432/v1")
        await policy.validate_url("https://10.0.0.1/v1")
        clock=[0]
        denied_resolver=Resolver(("127.0.0.1",),("8.8.8.8",))
        denied=SSRFPolicy(resolver=denied_resolver,clock=lambda:clock[0])
        for _ in range(2):
            with pytest.raises(ProviderError):
                await denied.validate_url("https://api.fixture.example/v1")
        assert len(denied_resolver.calls)==1
        clock[0]=61
        await denied.validate_url("https://api.fixture.example/v1")
        assert len(denied_resolver.calls)==2
    asyncio.run(run())


def test_proxy_deployment_limitation_fails_closed_and_no_proxy_direct_case_works():
    async def run():
        dialer=Dialer()
        safe=SafeHTTPTransport(policy=SSRFPolicy(resolver=Resolver(("8.8.8.8",))),network_backend=dialer,
            proxy_environment={"https":"http://proxy.example:8080"})
        client=httpx.AsyncClient(transport=safe,timeout=None)
        try:
            with pytest.raises(ProviderError,match="PROVIDER_PROXY_PARITY_UNIMPLEMENTED"):
                await client.post("https://api.fixture.example/v1")
            assert dialer.calls==[]
            assert environment_proxy("https://api.fixture.example",{"https":"proxy","no":".fixture.example"}) is None
        finally:
            await client.aclose()
    asyncio.run(run())
