"""Actual dedicated Redis delivery boundaries; no supplier or old Redis."""
import asyncio,json,time,uuid
from types import SimpleNamespace
import pytest
from tests.weknora_port.redis_fixture_identity import expected_redis_run_id
from redis.asyncio import Redis
from backend.weknora_port.storage.memory_queue import RedisMemoryExtractionQueue
from backend.weknora_port.conversation.maintenance_types import ExtractionPayload
from backend.weknora_port.conversation.types import MemoryScope


def test_actual_redis_retry_two_foreign_scope_and_expired_claim():
    async def scenario():
        client=Redis.from_url('redis://127.0.0.1:56424/15',decode_responses=True)
        assert (await client.info('server'))['run_id']==expected_redis_run_id()
        principal=SimpleNamespace(tenant_id=9913,subject_id='public-queue-fixture')
        prefix='weknora-python-port:20261013:mq-regression:'+str(uuid.uuid4())
        clock=[time.time()]
        queue=RedisMemoryExtractionQueue(client,prefix,principal,clock=lambda:clock[0],randbelow=lambda bound:29)
        payload=ExtractionPayload(MemoryScope(principal.tenant_id,principal.subject_id),'session','message','model')
        calls=[]
        class Failure:
            async def handle(self,value):calls.append(value);raise RuntimeError('synthetic')
        with pytest.raises(PermissionError):
            await queue.enqueue(ExtractionPayload(MemoryScope(9913,'foreign'),'s','m'),0,2)
        await queue.enqueue(payload,0,2)
        queue.worker=Failure()
        assert await queue.run_due()
        first=(await client.zrange(queue.keys[0],0,-1,withscores=True))[0]
        assert first[1]-clock[0]==44  # n=0 BEFORE increment
        assert not await queue.run_due()
        clock[0]+=44
        assert await queue.run_due()
        second=(await client.zrange(queue.keys[0],0,-1,withscores=True))[0]
        assert second[1]-clock[0]==74  # n=1 BEFORE increment
        assert not await queue.run_due()
        clock[0]+=74
        assert await queue.run_due()
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
        assert not await queue.run_due()
        clock[0]+=44
        assert await queue.run_due()
        assert len(calls)==4 and calls[-1].session_id=='recovery'
        assert await client.hget(queue.keys[2],job['id']) is None
        assert await client.zcard(queue.keys[1])==0
        await client.delete(*queue.keys);await client.aclose()
    asyncio.run(scenario())


def test_source_retry_same_fixtures_go_oracle():
    from pathlib import Path
    from backend.weknora_port.storage.memory_queue import source_retry_delay
    rows=[json.loads(row) for row in Path('docs/weknora-port/evidence/P4_RETRY_GO_20261014.jsonl').read_text(encoding='utf-8-sig').splitlines()]
    assert len(rows)==90
    for row in rows:
        assert source_retry_delay(row['retried'],lambda bound:row['jitter'])==row['seconds']
