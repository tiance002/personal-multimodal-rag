"""Real HTTPX/httpcore/h11 protocols with fake DNS, TCP and TLS only."""
import asyncio
import ssl
import httpcore
import httpx
import pytest
from backend.weknora_port.models import ProviderError
from backend.weknora_port.models.safe_network import SSRFPolicy, SafeHTTPTransport, environment_proxy
from tests.weknora_port.test_provider_safe_network import Resolver, Dialer, RecordedStream, http_packet


@pytest.mark.parametrize("url,env,want",[
    ("https://api.example/v1",{"HTTPS_PROXY":"http://proxy:8080","https_proxy":"http://other:8080"},"http://proxy:8080"),
    ("https://api.example/v1",{"ALL_PROXY":"http://proxy:8080"},None),
    ("https://api.example/v1",{"https":"http://proxy:8080","no":"example"},None),
    ("https://example/v1",{"https":"proxy","no":".example"},"proxy"),
    ("https://api.example:8443/v1",{"https":"proxy","no":".example:443"},"proxy"),
    ("https://api.example/v1",{"https":"proxy","no":".example:443"},None),
    ("https://8.8.8.8/v1",{"https":"proxy","no":"8.8.0.0/16"},None),
    ("https://localhost/v1",{"https":"proxy"},None),
    ("http://127.0.0.2/v1",{"http":"proxy"},None),
    ("https://api.example/v1",{"https":"proxy","no":"*"},None),
])
def test_go_environment_proxy_resolution(url,env,want):
    assert environment_proxy(url,env) == want


def test_go_cgi_http_proxy_refusal():
    with pytest.raises(ProviderError,match="PROVIDER_PROXY_CGI_BLOCKED"):
        environment_proxy("http://api.example",{"http":"proxy","REQUEST_METHOD":"GET"})


@pytest.mark.parametrize("scheme",["http","https"])
def test_connect_tunnel_tls_auth_never_reaches_model_headers(scheme):
    async def run():
        raw=RecordedStream([b"HTTP/1.1 200 Connection Established\r\n\r\n",http_packet(body=b"{}")])
        dns=Resolver(("8.8.8.8",),("127.0.0.1",))
        dialer=Dialer([raw])
        safe=SafeHTTPTransport(policy=SSRFPolicy(resolver=dns),network_backend=dialer,
            proxy_environment={"https":scheme+"://user:SECRET@proxy.example:8080"})
        async with httpx.AsyncClient(transport=safe,timeout=None) as client:
            response=await client.post("https://api.example/v1",headers={"Authorization":"Bearer MODEL_KEY"},content=b"{}")
            assert response.status_code == 200
        wire=b"".join(raw.written)
        assert b"CONNECT api.example:443 HTTP/1.1" in wire and b"POST /v1 HTTP/1.1" in wire
        tunnel, model=wire.split(b"POST /v1 HTTP/1.1")
        assert b"Proxy-Authorization: Basic dXNlcjpTRUNSRVQ=" in tunnel and b"MODEL_KEY" not in tunnel
        assert b"Proxy-Authorization" not in model and b"Bearer MODEL_KEY" in model
        hosts=[v[2] for v in raw.tls]
        assert hosts == (["proxy.example"] if scheme=="https" else [])+["api.example"]
        assert all(v[:2]==(ssl.CERT_REQUIRED,True) and v[3]==10 for v in raw.tls)
        assert dialer.calls == [("127.0.0.1",8080,30)]
    asyncio.run(run())


def test_http_proxy_absolute_form_and_auth():
    async def run():
        raw=RecordedStream([http_packet()]); dialer=Dialer([raw])
        safe=SafeHTTPTransport(policy=SSRFPolicy(resolver=Resolver(("8.8.8.8",))),network_backend=dialer,
            proxy_environment={"http":"proxy.example:8080"})
        async with httpx.AsyncClient(transport=safe) as client:
            assert (await client.get("http://api.example/path?q=1")).status_code == 200
        assert b"GET http://api.example/path?q=1 HTTP/1.1" in b"".join(raw.written)
        assert not raw.tls
    asyncio.run(run())


@pytest.mark.parametrize("status",[201,299,407,500])
def test_non200_connect_rejected_without_model_send_or_credential_errors(status):
    async def run():
        raw=RecordedStream([http_packet(status)]); dialer=Dialer([raw])
        safe=SafeHTTPTransport(policy=SSRFPolicy(resolver=Resolver(("8.8.8.8",))),network_backend=dialer,
            proxy_environment={"https":"http://user:SECRET@proxy.example:8080"})
        async with httpx.AsyncClient(transport=safe) as client:
            with pytest.raises(httpx.ProxyError) as error:
                await client.post("https://api.example/v1",content=b"MODEL_BODY")
            assert "SECRET" not in str(error.value) and "user" not in str(error.value)
        assert b"MODEL_BODY" not in b"".join(raw.written) and not raw.tls
    asyncio.run(run())


@pytest.mark.parametrize("scheme",["socks5","socks5h"])
@pytest.mark.parametrize("auth",[False,True])
def test_socks5_remote_dns_auth_wire_and_tls(scheme,auth):
    async def run():
        packets=[b"\x05\x02" if auth else b"\x05\x00"]
        if auth: packets.append(b"\x01\x00")
        packets.extend([b"\x05\x00\x00\x01",b"\x00\x00\x00\x00\x1f\x90",http_packet()])
        raw=RecordedStream(packets); dialer=Dialer([raw])
        safe=SafeHTTPTransport(policy=SSRFPolicy(resolver=Resolver(("8.8.8.8",))),network_backend=dialer,
            proxy_environment={"https":scheme+"://"+("u:p@" if auth else "")+"proxy.example:1080"})
        async with httpx.AsyncClient(transport=safe) as client:
            assert (await client.get("https://api.example/v1")).status_code == 200
        assert raw.written[0] == (b"\x05\x02\x00\x02" if auth else b"\x05\x01\x00")
        if auth: assert raw.written[1] == b"\x01\x01u\x01p"
        assert b"\x05\x01\x00\x03\x0bapi.example\x01\xbb" in raw.written
        assert raw.tls[0][2] == "api.example"
    asyncio.run(run())


def test_proxy_keeps_destination_ssrf_and_connect_time_proxy_dns_pin():
    async def run():
        dialer=Dialer(); resolver=Resolver(("127.0.0.1",))
        safe=SafeHTTPTransport(policy=SSRFPolicy(resolver=resolver),network_backend=dialer,
            proxy_environment={"https":"http://proxy.example:8080"})
        async with httpx.AsyncClient(transport=safe) as client:
            with pytest.raises(ProviderError,match="PROVIDER_SSRF_IP_BLOCKED"):
                await client.get("https://api.example/v1")
        assert dialer.calls == [] and resolver.calls == ["api.example"]
    asyncio.run(run())
