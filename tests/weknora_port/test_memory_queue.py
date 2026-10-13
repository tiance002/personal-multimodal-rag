"""Actual dedicated Redis delivery boundaries; no supplier or old Redis."""
import asyncio,json,time,uuid
from types import SimpleNamespace
import pytest
from redis.asyncio import Redis
from backend.weknora_port.storage.memory_queue import RedisMemoryExtractionQueue
from backend.weknora_port.conversation.maintenance_types import ExtractionPayload
from backend.weknora_port.conversation.types import MemoryScope


def test_actual_redis_retry_two_foreign_scope_and_expired_claim():
    async def scenario():
        client=Redis.from_url('redis://127.0.0.1:56424/0',decode_responses=True)
        assert (await client.info('server'))['run_id']=='c7d5aaff50b2743adea96a426718c5df3483c80f'
        principal=SimpleNamespace(tenant_id=9913,subject_id='public-queue-fixture')
        prefix='weknora-resume:queue-fixture:'+str(uuid.uuid4())
        queue=RedisMemoryExtractionQueue(client,prefix,principal)
        payload=ExtractionPayload(MemoryScope(principal.tenant_id,principal.subject_id),'session','message','model')
        calls=[]
        class Failure:
            async def handle(self,value):calls.append(value);raise RuntimeError('synthetic')
        queue.worker=Failure()
        with pytest.raises(PermissionError):
            await queue.enqueue(ExtractionPayload(MemoryScope(9913,'foreign'),'s','m'),0,2)
        await queue.enqueue(payload,0,2)
        await queue.drain()
        assert len(calls)==3
        assert all(row==payload for row in calls)
        assert len(await client.lrange(queue.keys[3],0,-1))==1
        assert await client.zcard(queue.keys[0])==await client.zcard(queue.keys[1])==0
        class Success:
            async def handle(self,value):calls.append(value)
        queue.worker=Success()
        job={'id':str(uuid.uuid4()),'payload':{'scope':{'tenant_id':9913,'subject_id':principal.subject_id},
            'session_id':'recovery','message_id':'m','chat_model_id':'model','language':''},'max_retry':2,'retry':0}
        await client.hset(queue.keys[2],job['id'],json.dumps(job))
        await client.zadd(queue.keys[1],{job['id']:time.time()-1})
        await queue.start();await queue.drain()
        assert len(calls)==4 and calls[-1].session_id=='recovery'
        assert await client.hget(queue.keys[2],job['id']) is None
        assert await client.zcard(queue.keys[1])==0
        await client.aclose()
    asyncio.run(scenario())
