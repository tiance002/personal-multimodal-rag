"""Scoped deterministic Redis delivery fault schedules. Providers are stand-ins."""
import asyncio, json, time, uuid
from types import SimpleNamespace
import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError
from backend.weknora_port.storage.memory_queue import RedisMemoryExtractionQueue
from backend.weknora_port.conversation.maintenance_types import ExtractionPayload
from backend.weknora_port.conversation.types import MemoryScope

async def fixture():
    client=Redis.from_url("redis://127.0.0.1:56424/15",decode_responses=True)
    assert (await client.info("server"))["run_id"]=="586b92f4649b78508382dfd52d64d848f45b0a7c"
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
            clock[0]+=601; q.worker=New()
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
                clock[0]+=601
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
            clock[0]+=601; q.worker=New()
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
