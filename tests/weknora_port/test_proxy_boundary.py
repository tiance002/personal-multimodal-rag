"""Source proxy decisions and complete CONNECT deadlines; no real network."""
import asyncio
import json
from pathlib import Path
import httpcore
import httpx
import pytest
from backend.weknora_port.models import ProviderError
from backend.weknora_port.models.safe_network import environment_proxy, ProxyStream, SSRFPolicy, SafeHTTPTransport
from tests.weknora_port.test_provider_safe_network import Resolver, Dialer, RecordedStream, http_packet


@pytest.mark.parametrize("environment", [{"https":"http://proxy.example:8080"},{"http":"http://proxy.example:8080"}])
def test_unrecognized_environment_proxy_keys_are_ignored(environment):
    scheme = "https" if "https" in environment else "http"
    assert environment_proxy(scheme+"://api.example/v1",environment) is None


@pytest.mark.parametrize("url", ["http://api.example/v1","http://localhost/v1","http://127.0.0.2/v1"])
def test_cgi_rejection_precedes_no_proxy_and_loopback(url):
    with pytest.raises(ProviderError,match="PROVIDER_PROXY_CGI_BLOCKED"):
        environment_proxy(url,{"HTTP_PROXY":"http://proxy.example:8080","NO_PROXY":"*","REQUEST_METHOD":"GET"})


@pytest.mark.parametrize("host,rule", [("xn--fa-hia.example","faß.example"),("faß.example","xn--fa-hia.example")])
def test_no_proxy_uses_source_nontransitional_idna(host,rule):
    assert environment_proxy("https://"+host+"/v1",{"HTTPS_PROXY":"http://proxy.example:8080","NO_PROXY":rule}) is None


@pytest.mark.parametrize("raw,allowed", [("http://proxy.example:8080",True),("proxy.example:8080",False),("http://proxy.example",False)])
def test_private_proxy_bypass_requires_source_raw_host_socket_match(raw,allowed):
    async def run():
        stream=RecordedStream([b"HTTP/1.1 200 Connection Established\r\n\r\n",http_packet()])
        dialer=Dialer([stream]);resolver=Resolver(("8.8.8.8",),("127.0.0.1",))
        transport=SafeHTTPTransport(policy=SSRFPolicy(resolver=resolver),network_backend=dialer,proxy_environment={"HTTPS_PROXY":raw})
        async with httpx.AsyncClient(transport=transport,timeout=None) as client:
            if allowed: assert (await client.get("https://api.example/v1")).status_code == 200
            else:
                with pytest.raises(ProviderError,match="PROVIDER_SSRF_IP_BLOCKED"):
                    await client.get("https://api.example/v1")
                assert dialer.calls == []
    asyncio.run(run())


class Clock:
    def __init__(self): self.value=0.
    def __call__(self): return self.value


class TimedStream(RecordedStream):
    def __init__(self,clock,packets,delays=(),write_delay=0):
        super().__init__(packets);self.clock=clock;self.delays=list(delays);self.write_delay=write_delay;self.closed=False;self.budgets=[]
    async def write(self,data,timeout=None):
        self.budgets.append(("write",timeout));self.clock.value+=self.write_delay
        await super().write(data,timeout)
    async def read(self,max_bytes,timeout=None):
        self.budgets.append(("read",timeout));self.clock.value+=self.delays.pop(0) if self.delays else 0
        return await super().read(max_bytes,timeout)
    async def aclose(self): self.closed=True;await super().aclose()


def test_connect_budget_includes_write_and_all_header_reads():
    async def run():
        clock=Clock();raw=TimedStream(clock,[b"HTTP/1.1 200 OK\r\n",b"X-Fixture: yes\r\n\r\n"],[25,20],20)
        stream=ProxyStream(raw,());stream.clock=clock
        await stream.write(b"CONNECT api.example:443 HTTP/1.1\r\n\r\n")
        await stream.read(65536)
        with pytest.raises(httpcore.ReadTimeout): await stream.read(65536)
        assert raw.closed
        assert [round(budget) for _,budget in raw.budgets] == [60,40,15]
    asyncio.run(run())


def test_connect_pending_survives_status_line_until_complete_headers():
    async def run():
        raw=RecordedStream([b"HTTP/1.1 200 OK\r\n",b"X-Fixture: yes\r\n\r\n"])
        stream=ProxyStream(raw,())
        await stream.write(b"CONNECT api.example:443 HTTP/1.1\r\n\r\n")
        await stream.read(65536);assert stream.connect_pending
        await stream.read(65536);assert not stream.connect_pending
    asyncio.run(run())


@pytest.mark.parametrize("operation", ["write","read"])
def test_connect_blocked_io_has_native_deadline_and_closes_stream(operation):
    async def run():
        class Blocked(RecordedStream):
            closed=False
            async def write(self,data,timeout=None):
                if operation=="write":await asyncio.Event().wait()
            async def read(self,size,timeout=None): await asyncio.Event().wait()
            async def aclose(self):self.closed=True
        raw=Blocked([]);stream=ProxyStream(raw,());stream.connect_timeout=.02
        async def exchange():
            await stream.write(b"CONNECT api.example:443 HTTP/1.1\r\n\r\n")
            if operation=="read":await stream.read(65536)
        with pytest.raises(httpcore.WriteTimeout if operation=="write" else httpcore.ReadTimeout):
            await asyncio.wait_for(exchange(),.5)
        assert raw.closed
    asyncio.run(run())


def test_fixed_go_environment_decisions_and_system_proxy_fixture():
    import backend.weknora_port.models.safe_network as network
    fixture=json.loads((Path(__file__).parent/"fixtures/proxy_go_oracle.json").read_text(encoding="utf-8"))
    assert fixture["real_network_calls"] == 0
    for item in fixture["cases"]:
        if "after" in item["input"]: continue
        value=item["input"];want=item["observed"]["first"]
        if want["error"]:
            with pytest.raises(ProviderError):environment_proxy(value["url"],value["env"])
        else: assert environment_proxy(value["url"],value["env"]) == want["proxy"],value["name"]
    for item in fixture["trust"]:
        value=item["input"]
        assert network.is_system_proxy(value["address"],value["env"]) == item["observed"]["system_proxy"]


def test_default_environment_snapshot_matches_fixed_go_first_call(monkeypatch):
    import backend.weknora_port.models.safe_network as network
    fixture=json.loads((Path(__file__).parent/"fixtures/proxy_go_oracle.json").read_text(encoding="utf-8"))
    for item in fixture["cases"]:
        value=item["input"]
        if "after" not in value:continue
        with monkeypatch.context() as patch:
            for name in ("HTTP_PROXY","http_proxy","HTTPS_PROXY","https_proxy","NO_PROXY","no_proxy","ALL_PROXY","all_proxy","REQUEST_METHOD","http","https","no"):
                patch.delenv(name,raising=False)
            for name,raw in value["env"].items():patch.setenv(name,raw)
            network._system_proxy_environment.cache_clear()
            assert environment_proxy(value["url"]) == item["observed"]["first"]["proxy"]
            for name,raw in value["after"].items():patch.setenv(name,raw)
            assert environment_proxy(value["url"]) == item["observed"]["second"]["proxy"]
            network._system_proxy_environment.cache_clear()


@pytest.mark.parametrize("mode", ["write-block","headers-block","trickle","short-parent","long-parent","malformed","complete","non200-lf-201","non200-lf-299"])
def test_actual_httpcore_connect_boundaries_match_go_oracle(monkeypatch,mode):
    import backend.weknora_port.models.safe_network as network
    fixture=json.loads((Path(__file__).parent/"fixtures/proxy_go_oracle.json").read_text(encoding="utf-8"))
    want=next(case for case in fixture["connect"] if case["case"]==mode)
    async def run():
        class BoundaryStream(RecordedStream):
            closed=False;tls_requested=False;reads=0
            async def write(self,data,timeout=None):
                if mode=="write-block":await asyncio.Event().wait()
                await super().write(data,timeout)
            async def read(self,size,timeout=None):
                self.reads+=1
                if mode=="trickle":await asyncio.sleep(.04 if self.reads==1 else .06)
                if mode in ("headers-block","short-parent","long-parent") and self.reads>1:
                    await asyncio.Event().wait()
                return await super().read(size,timeout)
            async def start_tls(self,*args,**kwargs):
                self.tls_requested=True
                raise httpcore.ConnectError("FIXTURE_COMPLETE_HEADERS_STOP_BEFORE_REAL_TLS")
            async def aclose(self):self.closed=True
        packets=[b"HTTP/1.1 200 OK\r\n",b"X-Fixture: yes\r\n\r\n"]
        if mode.startswith("non200-lf-"):packets=[f"HTTP/1.1 {mode.rsplit('-',1)[1]} Fixture\n\n".encode()]
        if mode=="malformed":packets=[b"HTTP/1.1 200 OK\r\nbroken\r\n\r\n"]
        raw=BoundaryStream(packets)
        original=network.ProxyStream
        class ShortBudget(original):
            def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);self.connect_timeout=.08
        monkeypatch.setattr(network,"ProxyStream",ShortBudget)
        safe=SafeHTTPTransport(policy=SSRFPolicy(resolver=Resolver(("8.8.8.8",),("127.0.0.1",))),network_backend=Dialer([raw]),proxy_environment={"HTTPS_PROXY":"http://proxy.example:8080"})
        async with httpx.AsyncClient(transport=safe,timeout=None) as client:
            with pytest.raises((httpx.HTTPError,TimeoutError)):
                async with asyncio.timeout(.02 if mode=="short-parent" else .4):
                    await client.post("https://api.example/v1",content=b"MODEL_BODY")
        assert raw.tls_requested == want["callback_after_complete_headers"]
        assert raw.closed and b"MODEL_BODY" not in b"".join(raw.written)
        assert want["request_failed_before_target_tls"] and want["real_network_calls"]==0
    asyncio.run(run())


def test_connect_cancellation_closes_socket_without_retry():
    async def run():
        entered=asyncio.Event()
        class Stream(RecordedStream):
            closed=False
            async def read(self,*args):entered.set();await asyncio.Event().wait()
            async def aclose(self):self.closed=True
        raw=Stream([]);stream=ProxyStream(raw,())
        async def exchange():
            await stream.write(b"CONNECT api.example:443 HTTP/1.1\r\n\r\n")
            await stream.read(65536)
        task=asyncio.create_task(exchange());await entered.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert raw.closed and len(raw.written)==1
    asyncio.run(run())


@pytest.mark.parametrize("deny", [False,True])
def test_unified_provider_native_proxy_entry_retains_gate_and_unknown_usage(deny):
    from backend.weknora_port.models import AsyncHTTPTransport,ProviderModel,provider_config,Message,ChatOptions
    from tests.weknora_port.test_provider_transport import Gate,context,response
    async def run():
        config=provider_config("openai-compatible","fixture",base_url="https://api.example/v1")
        raw=RecordedStream([b"HTTP/1.1 200 Connection Established\r\n\r\n",http_packet(body=json.dumps(response(config,usage=None)).encode())])
        dialer=Dialer([raw]);gate=Gate(deny=deny)
        safe=SafeHTTPTransport(policy=SSRFPolicy(resolver=Resolver(("8.8.8.8",),("127.0.0.1",))),network_backend=dialer,proxy_environment={"HTTPS_PROXY":"http://proxy.example:8080"})
        transport=AsyncHTTPTransport(allowed_endpoints=["https://api.example/v1/chat/completions"],gate=gate,credential=lambda _:"SYNTHETIC-ONLY",http_transport=safe)
        model=ProviderModel(config,transport)
        try:
            if deny:
                with pytest.raises(ProviderError,match="MODEL_EGRESS_DENIED"):
                    await model.chat((Message("user","fixture"),),ChatOptions(),context())
                assert dialer.calls==[] and gate.calls==[]
            else:
                answer=await model.chat((Message("user","fixture"),),ChatOptions(),context())
                assert answer.content=="answer" and answer.usage.status=="UNKNOWN"
                assert len(gate.calls)==len(gate.finishes)==1 and gate.finishes[0][2].status=="UNKNOWN"
                assert raw.tls[0][2]=="api.example"
        finally:await transport.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("raw", ["http://proxy.example:8080/path","http://proxy.example:8080?query=x","http://proxy.example:8080#fragment"])
def test_owner_pending_proxy_url_restrictions_are_retained(raw):
    from backend.weknora_port.models.safe_network import _proxy_parts
    with pytest.raises(ProviderError,match="PROVIDER_PROXY_CONFIGURATION_INVALID"):_proxy_parts(raw)


@pytest.mark.parametrize("host", ["faß.example","xn--fa-hia.example"])
def test_proxy_target_tls_uses_ascii_idna_for_unicode_and_punycode(host):
    async def run():
        raw=RecordedStream([b"HTTP/1.1 200 Connection Established\r\n\r\n",http_packet()])
        safe=SafeHTTPTransport(policy=SSRFPolicy(resolver=Resolver(("8.8.8.8",),("127.0.0.1",))),network_backend=Dialer([raw]),proxy_environment={"HTTPS_PROXY":"http://proxy.example:8080"})
        async with httpx.AsyncClient(transport=safe,timeout=None) as client:
            assert (await client.get("https://"+host+"/v1")).status_code==200
        assert raw.tls[0][2]=="xn--fa-hia.example"
    asyncio.run(run())


@pytest.mark.parametrize("status", [201,299])
def test_lf_only_non200_connect_cannot_reach_target_tls_or_body(status):
    async def run():
        raw=RecordedStream([f"HTTP/1.1 {status} Fixture\n\n".encode(),http_packet()])
        safe=SafeHTTPTransport(policy=SSRFPolicy(resolver=Resolver(("8.8.8.8",),("127.0.0.1",))),network_backend=Dialer([raw]),proxy_environment={"HTTPS_PROXY":"http://proxy.example:8080"})
        async with httpx.AsyncClient(transport=safe,timeout=None) as client:
            with pytest.raises(httpx.ProxyError):await client.post("https://api.example/v1",content=b"MODEL_BODY")
        assert raw.tls==[] and b"MODEL_BODY" not in b"".join(raw.written)
    asyncio.run(run())


@pytest.mark.parametrize("host", ["[::ffff:8.8.8.8]","8.8.8.8"])
def test_mapped_ipv6_no_proxy_cidr_matches_equivalent_ipv4(host):
    assert environment_proxy("https://"+host+"/",{"HTTPS_PROXY":"http://proxy.example:8080","NO_PROXY":"::ffff:8.8.0.0/112"}) is None
