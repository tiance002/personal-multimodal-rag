"""Fixed WeKnora append/replay semantics with exact owner CAS and PG sequences.

No Redis-only duplicate or billing authority. No automatic connection retries.
"""
import hashlib
import json
from redis import Redis
from redis.retry import Retry
from redis.backoff import NoBackoff


class StreamUnavailable(RuntimeError):
    pass


class RedisStreamManager:
    def __init__(self, client: Redis, *, live_ttl: int = 60, event_ttl: int = 86400, namespace: str = 'rag:stream:v1',
                 max_event_bytes: int = 262144, max_run_events: int = 1024, max_run_bytes: int = 4194304):
        if type(live_ttl) is not int or live_ttl < 3 or type(event_ttl) is not int or event_ttl < 1:
            raise ValueError('STREAM_TTL_INVALID')
        self.client, self.live_ttl, self.event_ttl, self.namespace = client, live_ttl, event_ttl, namespace
        if any(type(v) is not int or v<1 for v in (max_event_bytes,max_run_events,max_run_bytes)):
            raise ValueError('STREAM_CACHE_LIMIT_INVALID')
        self.max_event_bytes,self.max_run_events,self.max_run_bytes=max_event_bytes,max_run_events,max_run_bytes

    @classmethod
    def from_url(cls, url: str, **kwargs):
        return cls(Redis.from_url(url, decode_responses=True, socket_timeout=2, socket_connect_timeout=2,
                                  retry=Retry(NoBackoff(), 0), retry_on_timeout=False), **kwargs)

    def _key(self, session_id, run_id=None):
        identity = hashlib.sha256(str(session_id).encode()).hexdigest()
        return f'{self.namespace}:session:{identity}:' + ('live' if run_id is None else 'events:'+str(run_id))

    @staticmethod
    def _value(run_id, owner):
        return json.dumps({'run_id':str(run_id),'owner':str(owner)}, sort_keys=True, separators=(',', ':'))

    def _call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            raise StreamUnavailable('STREAM_UNAVAILABLE') from exc

    def set_live_run(self, session_id, run_id, owner):
        # An existing marker is never replaced, even by an identical caller.
        return bool(self._call(self.client.set, self._key(session_id), self._value(run_id,owner), nx=True, ex=self.live_ttl))

    def get_live_run(self, session_id):
        raw = self._call(self.client.get, self._key(session_id))
        if raw is None:
            return None
        try:
            value = json.loads(raw)
            if set(value) != {'run_id','owner'} or not all(isinstance(v,str) and v for v in value.values()):
                raise ValueError()
            return value
        except Exception as exc:
            raise StreamUnavailable('STREAM_MARKER_INVALID') from exc

    def _cas(self, session_id, run_id, owner, operation):
        script = "if redis.call('GET',KEYS[1]) ~= ARGV[1] then return 0 end; " + operation
        return bool(self._call(self.client.eval, script, 1, self._key(session_id), self._value(run_id,owner), self.live_ttl))

    def clear_live_run(self, session_id, run_id, owner):
        return self._cas(session_id,run_id,owner,"return redis.call('DEL',KEYS[1])")

    def renew_live_run(self, session_id, run_id, owner):
        return self._cas(session_id,run_id,owner,"return redis.call('EXPIRE',KEYS[1],ARGV[2])")

    def append_event(self, session_id, run_id, event):
        seq = event['seq']
        if type(seq) is not int or not 0 < seq <= 2**53-1:
            raise ValueError('STREAM_SEQUENCE_INVALID')
        encoded=json.dumps({'seq':seq,'event':event['event'],'data':event['data']},sort_keys=True,ensure_ascii=False,separators=(',',':'))
        if len(encoded.encode('utf8'))>self.max_event_bytes:
            raise StreamUnavailable('STREAM_CACHE_LIMIT')
        # NX protects a member, not its score. Compare the unique seq atomically
        # before insert, then enforce both count and UTF-8 byte limits.
        script='''local existing=redis.call('ZRANGEBYSCORE',KEYS[1],ARGV[1],ARGV[1]);
            if #existing>0 then
                if #existing~=1 or existing[1]~=ARGV[2] then return -1 end;
                redis.call('EXPIRE',KEYS[1],ARGV[3]); return 1;
            end;
            if redis.call('ZCARD',KEYS[1])>=tonumber(ARGV[4]) then return -2 end;
            local size=string.len(ARGV[2]);
            for _,value in ipairs(redis.call('ZRANGE',KEYS[1],0,-1)) do size=size+string.len(value) end;
            if size>tonumber(ARGV[5]) then return -2 end;
            redis.call('ZADD',KEYS[1],ARGV[1],ARGV[2]); redis.call('EXPIRE',KEYS[1],ARGV[3]); return 1'''
        result=self._call(self.client.eval,script,1,self._key(session_id,run_id),seq,encoded,self.event_ttl,self.max_run_events,self.max_run_bytes)
        if result!=1:
            raise StreamUnavailable('STREAM_EVENT_CONFLICT' if result==-1 else 'STREAM_CACHE_LIMIT')

    def get_events(self, session_id, run_id, after_seq):
        if type(after_seq) is not int or after_seq < 0:
            raise ValueError('STREAM_SEQUENCE_INVALID')
        rows = self._call(self.client.zrangebyscore,self._key(session_id,run_id),f'({after_seq}','+inf')
        try:
            return [json.loads(row) for row in rows]
        except Exception as exc:
            raise StreamUnavailable('STREAM_EVENT_INVALID') from exc
