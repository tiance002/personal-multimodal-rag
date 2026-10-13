"""Redis delayed extraction delivery; existing PG worker owns scope/lease/cursor.

Injected client/prefix only. Payload contains scoped IDs, no transcript. Jobs are
leased before execution; expired deliveries re-enter ready, max retry is source2.
This queue transport is not an Asynq byte-protocol compatibility claim.
"""
import asyncio,json,time,uuid
from dataclasses import asdict
from ..conversation.maintenance_types import ExtractionPayload
from ..conversation.types import MemoryScope

CLAIM="""
local expired=redis.call('ZRANGEBYSCORE',KEYS[2],'-inf',ARGV[1],'LIMIT',0,32)
for _,id in ipairs(expired) do redis.call('ZREM',KEYS[2],id);redis.call('ZADD',KEYS[1],ARGV[1],id) end
local ids=redis.call('ZRANGEBYSCORE',KEYS[1],'-inf',ARGV[1],'LIMIT',0,1)
if #ids==0 then return nil end
local id=ids[1];redis.call('ZREM',KEYS[1],id);redis.call('ZADD',KEYS[2],ARGV[2],id)
return redis.call('HGET',KEYS[3],id)
"""

class RedisMemoryExtractionQueue:
    def __init__(self,client,prefix,principal):
        self.client,self.prefix,self.principal=client,prefix,principal
        self.tasks=set();self.worker=None;self.pump_lock=asyncio.Lock()
    @property
    def keys(self):return tuple(self.prefix+suffix for suffix in (':ready',':leased',':jobs',':dead'))

    async def enqueue(self,payload,delay_seconds,max_retry):
        if (payload.scope.tenant_id,payload.scope.subject_id)!=(self.principal.tenant_id,self.principal.subject_id):
            raise PermissionError('MEMORY_OWNER_MISMATCH')
        if max_retry!=2:raise ValueError('MEMORY_SOURCE_RETRY_BOUND_REQUIRED')
        job={'id':str(uuid.uuid4()),'payload':asdict(payload),'max_retry':max_retry,'retry':0}
        ready,leased,jobs,dead=self.keys
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.hset(jobs,job['id'],json.dumps(job,separators=(',',':')))
            pipe.zadd(ready,{job['id']:time.time()+max(0,delay_seconds)})
            await pipe.execute()
        await self.start()

    async def start(self):
        async with self.pump_lock:
            if self.worker is not None and not any(not t.done() for t in self.tasks):
                task=asyncio.create_task(self.pump());self.tasks.add(task);task.add_done_callback(self.tasks.discard)

    async def stop(self):
        tasks=tuple(self.tasks)
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)

    async def run_due(self):
        ready,leased,jobs,dead=self.keys;now=time.time()
        raw=await self.client.eval(CLAIM,3,ready,leased,jobs,now,now+600)
        if raw is None:return False
        job=json.loads(raw);payload=dict(job['payload']);scope=MemoryScope(**payload.pop('scope'))
        error=None
        try:
            if (scope.tenant_id,scope.subject_id)!=(self.principal.tenant_id,self.principal.subject_id):
                raise PermissionError('MEMORY_OWNER_MISMATCH')
            await self.worker.handle(ExtractionPayload(scope,**payload))
        except asyncio.CancelledError:raise  # leased row retained for another worker
        except Exception as exc:error=type(exc).__name__
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.zrem(leased,job['id'])
            if error and job['retry']<job['max_retry']:
                job['retry']+=1
                pipe.hset(jobs,job['id'],json.dumps(job,separators=(',',':')))
                pipe.zadd(ready,{job['id']:time.time()+min(30,2**job['retry'])})
            else:
                pipe.hdel(jobs,job['id'])
                if error:pipe.rpush(dead,json.dumps({'id':job['id'],'retry':job['retry'],'error_class':error}))
            await pipe.execute()
        return True

    async def pump(self):
        while True:
            if await self.run_due():continue
            ready,leased,jobs,dead=self.keys
            async with self.pump_lock:
                if not await self.client.zcard(ready) and not await self.client.zcard(leased):
                    self.tasks.discard(asyncio.current_task())
                    return
            await asyncio.sleep(1)

    async def drain(self):
        while self.tasks:await asyncio.gather(*tuple(self.tasks))
