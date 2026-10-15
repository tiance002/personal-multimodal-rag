"""Scoped Redis extraction delivery; PG retains its lease/cursor and model identities.

Delivery tokens fence every completion. Expired deliveries consume the same bounded
retry policy as handler failures. Only scoped IDs are retained in terminal records.
"""
import asyncio
import json
import math
import secrets
import time
import uuid
from dataclasses import asdict

from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError
from ..conversation.maintenance_types import ExtractionPayload
from ..conversation.types import MemoryScope

# An expired lease stays out of the reclaim scan until it is at least this old.
# Upstream asynq v0.26.0 recoverer.go: cutoff := time.Now().Add(-30 * time.Second);
# internal/rdb/rdb.go ListLeaseExpired scans ZRANGEBYSCORE lease "-inf" ARGV[1]=cutoff.Unix(), and
# recoverer.go archives only when msg.Retried >= msg.Retry. Both the lease score and the cutoff are
# Unix whole seconds upstream, so eligibility is floor(score) <= floor(now-30).
RECOVERY_GRACE_SECONDS = 30

# Both transitions use the same keys: ready, leased, jobs, dead.
ARCHIVE_LUA = """
local function archive(job, now, error_class)
    local payload = job.payload
    local record = {id=job.id, payload={
        scope={tenant_id=payload.scope.tenant_id, subject_id=payload.scope.subject_id},
        session_id=payload.session_id, message_id=payload.message_id,
        chat_model_id=payload.chat_model_id, language=payload.language},
        retry=job.retry, attempts=job.retry+1, failed_at=now, error_class=error_class}
    redis.call('RPUSH',KEYS[4],cjson.encode(record))
    redis.call('HDEL',KEYS[3],job.id)
end
"""
CLAIM = ARCHIVE_LUA + """
local now=tonumber(ARGV[1])
local jitter=cjson.decode(ARGV[4])
-- Expired at "-inf..now"; reclaimable only in the integer-second domain, passed as the exclusive
-- upper bound ARGV[5]="(floor(now-30)+1)" i.e. floor(score) <= floor(now-30). Upstream stores lease
-- and cutoff as Unix whole seconds, so sub-second skew must not decide eligibility. The float
-- deadline stays verbatim for the ACK/COMPLETE fence.
local expired=redis.call('ZRANGEBYSCORE',KEYS[2],'-inf',ARGV[5],'LIMIT',0,32)
for index,id in ipairs(expired) do
    redis.call('ZREM',KEYS[2],id)
    local raw=redis.call('HGET',KEYS[3],id)
    if raw then
        local job=cjson.decode(raw)
        job.delivery_token=nil
        if job.retry < job.max_retry then
            local delay=job.retry^4+15+jitter[index]*(job.retry+1)
            job.retry=job.retry+1
            redis.call('HSET',KEYS[3],id,cjson.encode(job))
            redis.call('ZADD',KEYS[1],now+delay,id)
        else
            archive(job,now,'LeaseExpired')
        end
    end
end
local ids=redis.call('ZRANGEBYSCORE',KEYS[1],'-inf',now,'LIMIT',0,1)
if #ids==0 then return nil end
local id=ids[1]
redis.call('ZREM',KEYS[1],id)
local raw=redis.call('HGET',KEYS[3],id)
if not raw then return nil end
local job=cjson.decode(raw)
job.delivery_token=ARGV[3]
local encoded=cjson.encode(job)
redis.call('HSET',KEYS[3],id,encoded)
redis.call('ZADD',KEYS[2],ARGV[2],id)
return encoded
"""
COMPLETE = ARCHIVE_LUA + """
local raw=redis.call('HGET',KEYS[3],ARGV[1])
if not raw then return 0 end
local job=cjson.decode(raw)
local until_time=redis.call('ZSCORE',KEYS[2],ARGV[1])
local now=tonumber(ARGV[3])
if job.delivery_token~=ARGV[2] or not until_time or tonumber(until_time)<=now then return 0 end
redis.call('ZREM',KEYS[2],job.id)
job.delivery_token=nil
if ARGV[4]=='' then
    redis.call('HDEL',KEYS[3],job.id)
elseif job.retry < job.max_retry then
    job.retry=job.retry+1
    redis.call('HSET',KEYS[3],job.id,cjson.encode(job))
    redis.call('ZADD',KEYS[1],now+tonumber(ARGV[5]),job.id)
else
    archive(job,now,ARGV[4])
end
return 1
"""

def source_retry_delay(retried, randbelow=secrets.randbelow):
    """Pinned Asynq v0.26.0 formula; retried is the PRE-increment count."""
    if isinstance(retried, bool) or not isinstance(retried, int) or retried < 0:
        raise ValueError("MEMORY_RETRY_COUNT_INVALID")
    return retried**4 + 15 + randbelow(30) * (retried + 1)


class RedisMemoryExtractionQueue:
    def __init__(self, client, prefix, principal, *, clock=time.time, randbelow=secrets.randbelow, autostart_on_enqueue=True):
        self.client, self.prefix, self.principal = client, prefix, principal
        self.clock, self.randbelow = clock, randbelow
        self.autostart_on_enqueue = autostart_on_enqueue
        self.tasks = set()
        self.worker = None
        self.pump_lock = asyncio.Lock()
        self._stopped = False

    @property
    def keys(self):
        return tuple(self.prefix + suffix for suffix in (":ready", ":leased", ":jobs", ":dead"))

    async def enqueue(self, payload, delay_seconds, max_retry):
        if (payload.scope.tenant_id, payload.scope.subject_id) != (self.principal.tenant_id, self.principal.subject_id):
            raise PermissionError("MEMORY_OWNER_MISMATCH")
        if max_retry != 2:
            raise ValueError("MEMORY_SOURCE_RETRY_BOUND_REQUIRED")
        job = {"id": str(uuid.uuid4()), "payload": asdict(payload), "max_retry": max_retry, "retry": 0}
        ready, leased, jobs, dead = self.keys
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.hset(jobs, job["id"], json.dumps(job, separators=(",", ":")))
            pipe.zadd(ready, {job["id"]: self.clock() + max(0, delay_seconds)})
            await pipe.execute()
        if self.autostart_on_enqueue:
            await self.start(resume=False)

    async def start(self, *, resume=True):
        async with self.pump_lock:
            # Enqueue persists work but cannot revive an explicitly stopped pump.
            # Only application lifecycle start is an explicit restart.
            if self._stopped and not resume:
                return
            self._stopped = False
            if self.worker is not None and not any(not task.done() for task in self.tasks):
                task = asyncio.create_task(self.pump())
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)

    async def stop(self):
        async with self.pump_lock:
            self._stopped = True
            tasks = tuple(self.tasks)
            for task in tasks:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run_due(self):
        now = self.clock()
        # Exclusive integer upper bound == floor(score) <= floor(now-30); keeps the float deadline intact.
        reclaim_max = "({0}".format(math.floor(now - RECOVERY_GRACE_SECONDS) + 1)
        raw = await self.client.eval(CLAIM, 4, *self.keys, now, now + 600,
            str(uuid.uuid4()), json.dumps([self.randbelow(30) for _ in range(32)]), reclaim_max)
        if raw is None:
            return False
        job = json.loads(raw)
        payload = dict(job["payload"])
        scope = MemoryScope(**payload.pop("scope"))
        error = ""
        try:
            if (scope.tenant_id, scope.subject_id) != (self.principal.tenant_id, self.principal.subject_id):
                raise PermissionError("MEMORY_OWNER_MISMATCH")
            # Keep payload, operation IDs and runtime UNKNOWN/idempotency gates unchanged.
            await self.worker.handle(ExtractionPayload(scope, **payload))
        except asyncio.CancelledError:
            raise  # Unacknowledged delivery remains recoverable.
        except Exception as exc:
            error = type(exc).__name__
        delay = source_retry_delay(job["retry"], self.randbelow) if error and job["retry"] < job["max_retry"] else 0
        await self.client.eval(COMPLETE, 4, *self.keys, job["id"], job["delivery_token"], self.clock(), error, delay)
        return True

    async def pump(self):
        failures = 0
        while not self._stopped:
            try:
                delivered = await self.run_due()
                failures = 0
                if delivered:
                    continue
                ready, leased, jobs, dead = self.keys
                async with self.pump_lock:
                    if not await self.client.zcard(ready) and not await self.client.zcard(leased):
                        self.tasks.discard(asyncio.current_task())
                        return
                await asyncio.sleep(1)
            except (RedisConnectionError, RedisTimeoutError):
                # No task ACK, retry-counter reset, or reservation/UNKNOWN mutation here.
                failures = min(failures + 1, 6)
                await asyncio.sleep(min(0.25 * 2 ** (failures - 1), 5))

    async def drain(self):
        while self.tasks:
            await asyncio.gather(*tuple(self.tasks))
