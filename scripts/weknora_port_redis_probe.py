"""Actual PG + dedicated Redis replay probe. No HTTP or provider is claimed."""
import argparse
import asyncio
import json
from pathlib import Path
import selectors
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.weknora_port.storage.history import LocalPrincipal,PostgresHistoryRepository
from backend.weknora_port.storage.postgres import IsolationPolicy,PostgresRetriever
from backend.weknora_port.streaming.events import StreamEvent,to_sse
from backend.weknora_port.streaming.manager import RedisListStreamStore
from backend.weknora_port.streaming.replay import ReplayService,FinalCommit,finalize_turn


async def run(args):
    import psycopg
    from psycopg.rows import dict_row
    from redis.asyncio import Redis
    previous = json.loads(Path(args.storage_report).read_text(encoding='utf-8'))
    assert previous['status'] == 'PASS'
    policy = IsolationPolicy(previous['system_identifier'],frozenset(args.prohibited_system_identifier))
    redis = Redis.from_url(args.redis_url,decode_responses=True)
    try:
        server = await redis.info('server')
        if server['run_id'] != args.expected_redis_run_id:
            raise PermissionError('ISOLATED_REDIS_IDENTITY_REQUIRED')
        store = RedisListStreamStore(redis,prefix='weknora-python-port:20261013:stream:events')
        ids = previous['fixture_ids']
        async with await psycopg.AsyncConnection.connect(args.dsn,autocommit=True,row_factory=dict_row) as connection:
            guard = PostgresRetriever(connection,policy)
            await guard.verify_identity()
            principal = LocalPrincipal(10001,'local-pg-probe')
            history = PostgresHistoryRepository(guard,principal)
            await history.validate_assistant_message(ids['session'],ids['a'])
            if (await store.get_events(ids['session'],ids['a'],0))[1] != 0:
                raise ValueError('PROBE_ALREADY_PERSISTED_DO_NOT_APPEND_AGAIN')
            metadata = {'assistant_message_id':ids['a'],'request_id':ids['request'],'session_id':ids['session']}
            await store.append_event(ids['session'],ids['a'],StreamEvent(ids['request'],'agent_query',data=metadata))
            for text,done in (('fixture ',False),('answer',False),('',True)):
                await store.append_event(ids['session'],ids['a'],StreamEvent(ids['request'],'answer',text,done,data={'event_id':'answer-1'}))
            segment,offset = await store.get_events(ids['session'],ids['a'],0)
            assert segment[-1].done and all(event.type != 'complete' for event in segment)
            saved = previous['persisted_answer']
            commit = FinalCommit(ids['session'],ids['a'],ids['request'],saved['content'],saved['knowledge_references'],saved['usage'])
            await finalize_turn(store,history,commit)
            events = [event async for event in ReplayService(store,history).replay(ids['session'],ids['a'],principal)]
            assert events[0].data == metadata and events[-1].type == 'complete'
            assert ''.join(event.content for event in events if event.type == 'answer') == 'fixture answer'
            assert {event.event_id for event in events} == {ids['request']}
            key = store._key(ids['session'],ids['a'])
            ttl = await redis.ttl(key)
            assert 0 < ttl <= 86400
            evidence = {'status':'PASS','kind':'ACTUAL_ISOLATED_PG_REDIS_REPLAY',
                        'database':previous['database'],'system_identifier':previous['system_identifier'],
                        'redis_run_id':server['run_id'],'redis_version':server['redis_version'],
                        'key':key,'ttl':ttl,'raw_before_complete_offset':offset,
                        'replayed_event_types':[event.type for event in events],
                        'wire_frames':[to_sse(event,ids['request']).decode() for event in events],
                        'generation_posts':0,'real_provider_calls':0,'old_database_writes':0,
                        'not_run':['HTTP reconnect','browser reconnect','full RAG generation']}
            Path(args.report).write_text(json.dumps(evidence,indent=2)+'\n',encoding='utf-8')
            print(json.dumps(evidence,indent=2))
    finally:
        await redis.aclose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dsn',required=True)
    parser.add_argument('--redis-url',required=True)
    parser.add_argument('--expected-redis-run-id',required=True)
    parser.add_argument('--prohibited-system-identifier',action='append',required=True)
    parser.add_argument('--storage-report',required=True)
    parser.add_argument('--report',required=True)
    args = parser.parse_args()
    if sys.platform == 'win32':
        asyncio.run(run(args),loop_factory=lambda:asyncio.SelectorEventLoop(selectors.SelectSelector()))
    else: asyncio.run(run(args))
