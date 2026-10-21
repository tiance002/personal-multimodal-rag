"""Scoped deterministic Redis delivery fault schedules. Providers are stand-ins."""
import asyncio, json, time, uuid
from types import SimpleNamespace
import pytest
from tests.weknora_port.redis_fixture_identity import expected_redis_run_id
from redis.asyncio import Redis
from redis.exceptions import ConnectionError
from backend.weknora_port.storage.memory_queue import RedisMemoryExtractionQueue
from backend.weknora_port.conversation.maintenance_types import ExtractionPayload
from backend.weknora_port.conversation.types import MemoryScope

# Leases last 600s and reclaim now also needs the upstream 30s grace, so expired deliveries
# are advanced past both (600+30+1) instead of the previous lease+1 step.

async def fixture():
    client=Redis.from_url("redis://127.0.0.1:56424/15",decode_responses=True)
    assert (await client.info("server"))["run_id"]==expected_redis_run_id()
    owner=SimpleNamespace(tenant_id=9913,subject_id="queue-recovery-synthetic")
    clock=[time.time()]
    q=RedisMemoryExtractionQueue(client,"weknora-python-port:20261013:mq:"+str(uuid.uuid4()),owner,
        clock=lambda:clock[0],randbelow=lambda bound:0)
    p=ExtractionPayload(MemoryScope(owner.tenant_id,owner.subject_id),"synthetic-session","m","mock")
    await q.enqueue(p,0,2)
    return client,q,p,clock

async def cleanup(client,q):
    await q.stop()
    await client.delete(*q.keys)
    await client.aclose()

@pytest.mark.parametrize("late_failure",[False,True])
def test_late_delivery_cannot_ack_or_overwrite_successor(late_failure):
    async def run():
        client,q,p,clock=await fixture()
        gate=asyncio.Event(); entered=asyncio.Event()
        class Old:
            async def handle(self,payload):
                entered.set(); await gate.wait()
                if late_failure: raise RuntimeError("synthetic")
        class New:
            async def handle(self,payload): raise RuntimeError("synthetic")
        try:
            q.worker=Old()
            old=asyncio.create_task(q.run_due()); await entered.wait()
            clock[0]+=631; q.worker=New()
            await q.run_due()
            clock[0]+=15
            await q.run_due()
            before=(await client.hgetall(q.keys[2]),await client.zrange(q.keys[0],0,-1,withscores=True))
            gate.set(); await old
            after=(await client.hgetall(q.keys[2]),await client.zrange(q.keys[0],0,-1,withscores=True))
            assert after==before,"stale delivery overwrote successor"
        finally:
            gate.set()
            await cleanup(client,q)
    asyncio.run(run())

def test_three_expired_deliveries_archive_scoped_payload():
    async def run():
        client,q,p,clock=await fixture(); calls=[]
        class Crash:
            async def handle(self,payload):
                calls.append(payload); raise asyncio.CancelledError()
        q.worker=Crash()
        try:
            for attempt in range(3):
                with pytest.raises(asyncio.CancelledError): await q.run_due()
                clock[0]+=631
                assert not await q.run_due(),"recovery bypassed retry backoff"
                if attempt<2:
                    assert not await client.lrange(q.keys[3],0,-1)
                    clock[0]+=15+attempt**4
            assert len(calls)==3
            archived=json.loads((await client.lrange(q.keys[3],0,-1))[0])
            assert archived["payload"]["scope"]=={"tenant_id":9913,"subject_id":p.scope.subject_id}
            assert archived["payload"]["session_id"]==p.session_id
            assert set(archived)=={"id","payload","retry","attempts","failed_at","error_class"}
            assert set(archived["payload"])=={"scope","session_id","message_id","chat_model_id","language"}
            assert archived["attempts"]==3 and archived["retry"]==2
            assert abs(archived["failed_at"]-clock[0])<0.001 and archived["error_class"]=="LeaseExpired"
            assert not await client.hgetall(q.keys[2])
            assert await client.zcard(q.keys[0])==await client.zcard(q.keys[1])==0
            foreign=RedisMemoryExtractionQueue(client,q.prefix+":foreign",SimpleNamespace(tenant_id=9913,subject_id="foreign"))
            assert not await client.lrange(foreign.keys[3],0,-1)
        finally: await cleanup(client,q)
    asyncio.run(run())

def test_transient_redis_failure_recovers_without_enqueue_and_stop_stays_stopped():
    async def run():
        client,q,p,clock=await fixture(); done=asyncio.Event()
        class Worker:
            async def handle(self,payload): done.set()
        class Fault:
            def __init__(self): self.calls=0; self.fail=True
            def __getattr__(self,key): return getattr(client,key)
            async def eval(self,*args):
                self.calls+=1
                if self.fail: raise ConnectionError("synthetic outage")
                return await client.eval(*args)
        fault=Fault(); q.client=fault; q.worker=Worker()
        try:
            await q.start(); await asyncio.sleep(.05)
            fault.fail=False
            await asyncio.wait_for(done.wait(),2)
            await q.stop()
            done.clear(); fault.fail=True
            await q.start(); await asyncio.sleep(.05); await q.stop()
            observed=fault.calls; fault.fail=False
            await asyncio.sleep(.4)
            assert fault.calls==observed and not q.tasks
        finally: await cleanup(client,q)
    asyncio.run(run())

@pytest.mark.parametrize("late_failure",[False,True])
def test_late_completion_cannot_remove_active_successor_lease(late_failure):
    async def run():
        client,q,p,clock=await fixture()
        old_gate=asyncio.Event(); new_gate=asyncio.Event()
        old_entered=asyncio.Event(); new_entered=asyncio.Event()
        class Old:
            async def handle(self,payload):
                old_entered.set(); await old_gate.wait()
                if late_failure: raise RuntimeError("synthetic")
        class New:
            async def handle(self,payload): new_entered.set(); await new_gate.wait()
        old=new=None
        try:
            q.worker=Old(); old=asyncio.create_task(q.run_due()); await old_entered.wait()
            clock[0]+=631; q.worker=New()
            assert not await q.run_due()
            clock[0]+=15
            new=asyncio.create_task(q.run_due()); await new_entered.wait()
            before=(await client.hgetall(q.keys[2]),await client.zrange(q.keys[1],0,-1,withscores=True))
            old_gate.set(); await old
            assert (await client.hgetall(q.keys[2]),await client.zrange(q.keys[1],0,-1,withscores=True))==before
            new_gate.set(); await new
            assert not await client.hgetall(q.keys[2])
        finally:
            old_gate.set(); new_gate.set()
            if old: await old
            if new: await new
            await cleanup(client,q)
    asyncio.run(run())

def test_pump_backoff_is_bounded_and_cancelable(monkeypatch):
    async def run():
        delays=[]
        class Down:
            async def eval(self,*args): raise ConnectionError("synthetic")
        async def sleep(delay):
            delays.append(delay)
            if len(delays)==8: raise asyncio.CancelledError()
        q=RedisMemoryExtractionQueue(Down(),"offline",SimpleNamespace(tenant_id=1,subject_id="synthetic"))
        monkeypatch.setattr(asyncio,"sleep",sleep)
        with pytest.raises(asyncio.CancelledError): await q.pump()
        assert delays==[.25,.5,1,2,4,5,5,5]
    asyncio.run(run())

def test_lost_ack_response_does_not_redeliver_completed_job():
    async def run():
        client,q,p,clock=await fixture(); calls=[]; failed=asyncio.Event()
        from backend.weknora_port.storage.memory_queue import COMPLETE
        class Worker:
            async def handle(self,payload): calls.append(payload)
        class LostResponse:
            lost=False
            def __getattr__(self,key): return getattr(client,key)
            async def eval(self,script,*args):
                result=await client.eval(script,*args)
                if script==COMPLETE and not self.lost:
                    self.lost=True; failed.set()
                    raise ConnectionError("synthetic lost ACK response")
                return result
        q.worker=Worker(); q.client=LostResponse()
        try:
            await q.start()
            await asyncio.wait_for(failed.wait(),2)
            await asyncio.wait_for(q.drain(),2)
            assert calls==[p] and not await client.hgetall(q.keys[2])
            assert not await client.lrange(q.keys[3],0,-1)
        finally: await cleanup(client,q)
    asyncio.run(run())


def test_enqueue_after_explicit_stop_does_not_restart_pump():
    async def run():
        client,q,p,clock=await fixture()
        class Worker:
            async def handle(self,payload):raise AssertionError('stopped queue sent work')
        try:
            q.worker=Worker()
            await q.stop()
            await q.enqueue(p,0,2)
            assert q._stopped and not q.tasks
            assert await client.zcard(q.keys[0])==2
        finally:await cleanup(client,q)
    asyncio.run(run())


# Upstream asynq v0.26.0 recoverer.go: cutoff := time.Now().Add(-30 * time.Second);
# internal/rdb/rdb.go ListLeaseExpired scans ZRANGEBYSCORE lease "-inf" ARGV[1]=cutoff.Unix().
# Lease expiry and cutoff both come from Unix whole seconds, so eligibility is
# floor(lease_score) <= floor(now-30), i.e. lease_score < floor(now-30)+1 (exclusive upper bound).
UPSTREAM_RECOVERY_GRACE_SECONDS=30

async def leased_fixture(retry,lease_until):
    client=Redis.from_url("redis://127.0.0.1:56424/15",decode_responses=True)
    assert (await client.info("server"))["run_id"]==expected_redis_run_id()
    owner=SimpleNamespace(tenant_id=9913,subject_id="queue-grace-synthetic")
    clock=[1700000000.0]
    q=RedisMemoryExtractionQueue(client,"weknora-python-port:20261013:mq-grace:"+str(uuid.uuid4()),owner,
        clock=lambda:clock[0],randbelow=lambda bound:0)
    job={"id":str(uuid.uuid4()),"payload":{"scope":{"tenant_id":owner.tenant_id,"subject_id":owner.subject_id},
        "session_id":"grace","message_id":"m","chat_model_id":"mock","language":""},"max_retry":2,"retry":retry}
    await client.hset(q.keys[2],job["id"],json.dumps(job,separators=(",",":")))
    await client.zadd(q.keys[1],{job["id"]:lease_until})
    return client,q,job,clock

@pytest.mark.parametrize("offset_from_cutoff,reclaimed",[
    (29.0,False),   # lease expired 1s ago: inside the 30s grace, must stay leased
    (1.0,False),    # lease = floor(now-30)+1: first whole second past the cutoff
    (0.001,True),   # sub-second above the cutoff second floors back onto it (integer domain)
    (0.0,True),     # exactly the cutoff second (inclusive)
    (-0.001,True),  # just below the cutoff second
    (-1.0,True),    # a full second below the cutoff
])
def test_reclaim_cutoff_matches_upstream_inclusive_grace(offset_from_cutoff,reclaimed):
    async def run():
        client=q=None
        try:
            client,q,job,clock=await leased_fixture(0,1700000000.0-UPSTREAM_RECOVERY_GRACE_SECONDS+offset_from_cutoff)
            assert not await q.run_due(),"a reclaimed job is re-scheduled, never delivered in the same scan"
            leased=await client.zrange(q.keys[1],0,-1)
            ready=await client.zrange(q.keys[0],0,-1,withscores=True)
            dead=await client.lrange(q.keys[3],0,-1)
            stored=json.loads(await client.hget(q.keys[2],job["id"]))
            if reclaimed:
                assert leased==[] and not dead
                assert [member for member,_ in ready]==[job["id"]]
                assert ready[0][1]-clock[0]==15,"retry 0 keeps the existing n^4+15+jitter*(n+1) formula"
                assert stored["retry"]==1
            else:
                assert leased==[job["id"]] and not ready and not dead
                assert stored["retry"]==0
        finally:
            if q is not None:
                await client.delete(*q.keys); await client.aclose()
    asyncio.run(run())

def test_reclaimed_lease_consumes_retry_then_archives_after_grace():
    async def run():
        client=q=None
        try:
            client,q,job,clock=await leased_fixture(2,1700000000.0-UPSTREAM_RECOVERY_GRACE_SECONDS-1)
            assert not await q.run_due()
            assert not await client.zrange(q.keys[1],0,-1)
            archived=json.loads((await client.lrange(q.keys[3],0,-1))[0])
            assert archived["retry"]==2 and archived["attempts"]==3 and archived["error_class"]=="LeaseExpired"
            assert not await client.hgetall(q.keys[2])
        finally:
            if q is not None:
                await client.delete(*q.keys); await client.aclose()
    asyncio.run(run())

@pytest.mark.parametrize("lease_score,now,reclaimed",[
    (1700000599.0,1700000630.20,True),   # integer below the cutoff second
    (1700000600.0,1700000630.20,True),   # exactly the cutoff second (inclusive)
    (1700000601.0,1700000630.20,False),  # first integer past the cutoff
    (1700000600.75,1700000630.20,True),  # upstream counterexample E=...600.75, N=...630.20
    (1700000600.999,1700000630.20,True),
    (1700000601.999,1700000630.20,False),
    (1700000580.95,1700000610.90,True),  # other fractional phase on the scan clock
    (1700000581.00,1700000610.90,False),
])
def test_reclaim_uses_integer_second_domain(lease_score,now,reclaimed):
    async def run():
        client=q=None
        try:
            client=Redis.from_url("redis://127.0.0.1:56424/15",decode_responses=True)
            assert (await client.info("server"))["run_id"]==expected_redis_run_id()
            owner=SimpleNamespace(tenant_id=9913,subject_id="queue-grace-synthetic")
            clock=[now]
            q=RedisMemoryExtractionQueue(client,"weknora-python-port:20261013:mq-grace:"+str(uuid.uuid4()),owner,
                clock=lambda:clock[0],randbelow=lambda bound:0)
            job={"id":str(uuid.uuid4()),"payload":{"scope":{"tenant_id":owner.tenant_id,"subject_id":owner.subject_id},
                "session_id":"int-domain","message_id":"m","chat_model_id":"mock","language":""},"max_retry":2,"retry":0}
            await client.hset(q.keys[2],job["id"],json.dumps(job,separators=(",",":")))
            await client.zadd(q.keys[1],{job["id"]:lease_score})
            assert not await q.run_due(),"a reclaimed job is re-scheduled into ready, never delivered here"
            leased=await client.zrange(q.keys[1],0,-1)
            stored=json.loads(await client.hget(q.keys[2],job["id"]))
            if reclaimed:
                assert leased==[] and stored["retry"]==1
            else:
                assert leased==[job["id"]] and stored["retry"]==0
        finally:
            if q is not None:
                await client.delete(*q.keys); await client.aclose()
    asyncio.run(run())

def test_reclaim_of_real_float_lease_matches_integer_cutoff():
    async def run():
        client=q=None
        try:
            client=Redis.from_url("redis://127.0.0.1:56424/15",decode_responses=True)
            assert (await client.info("server"))["run_id"]==expected_redis_run_id()
            owner=SimpleNamespace(tenant_id=9913,subject_id="queue-grace-synthetic")
            clock=[1700000000.75]
            q=RedisMemoryExtractionQueue(client,"weknora-python-port:20261013:mq-grace:"+str(uuid.uuid4()),owner,
                clock=lambda:clock[0],randbelow=lambda bound:0)
            class Crash:
                async def handle(self,payload): raise asyncio.CancelledError()
            p=ExtractionPayload(MemoryScope(owner.tenant_id,owner.subject_id),"grace","m","mock")
            await q.enqueue(p,0,2)   # no pump: worker is unset during enqueue
            q.worker=Crash()
            with pytest.raises(asyncio.CancelledError): await q.run_due()
            leased=await client.zrange(q.keys[1],0,-1,withscores=True)
            assert len(leased)==1 and abs(leased[0][1]-1700000600.75)<1e-6,"float deadline kept verbatim for ACK"
            clock[0]=1700000630.20   # upstream counterexample scan clock
            assert not await q.run_due()
            assert not await client.zrange(q.keys[1],0,-1),"integer-domain cutoff must reclaim ...600.75"
            stored=json.loads(await client.hget(q.keys[2],leased[0][0])); assert stored["retry"]==1
        finally:
            if q is not None:
                await q.stop(); await client.delete(*q.keys); await client.aclose()
    asyncio.run(run())
