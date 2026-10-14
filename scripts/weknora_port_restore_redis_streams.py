"""Restore only explicit existing synthetic SSE keys to a fresh prefix; no jobs run."""
import argparse,asyncio,base64,hashlib,json,subprocess,time
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from backend.weknora_port.streaming.manager import RedisListStreamStore
ROOT=Path(__file__).resolve().parents[1]
DSN='postgresql://port_fixture:synthetic-isolated-fixture@127.0.0.1:56423/weknora_python_port_20261013'
CONTAINER='weknora-python-resume-redis-20261013'
CID='692b84d8fee031b145e6b1b0037625a955f0bb3cdb321854d11a6bbb03b02664'
RID='586b92f4649b78508382dfd52d64d848f45b0a7c'
SNAPSHOT="return {redis.call('DUMP',KEYS[1]),redis.call('PTTL',KEYS[1]),redis.call('LRANGE',KEYS[1],0,-1)}"
def sha(value):return hashlib.sha256(value).hexdigest()
def main(args):
 out=Path(args.output).resolve()
 if not out.is_relative_to(Path('E:/WK/restore').resolve()) or out.exists():raise ValueError('NEW_REDIS_RESTORE_PATH_REQUIRED')
 if not args.prefix.startswith('wk-restore:20261014:') or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:-' for c in args.prefix):raise ValueError('NEW_REDIS_PREFIX_REQUIRED')
 assert subprocess.check_output(['docker','inspect','--format','{{.Id}}',CONTAINER],text=True).strip()==CID
 fixture=json.loads((ROOT/'docs/weknora-port/evidence/P6_CANCEL_PARTIAL_HTTP_20261014.json').read_text())
 assert fixture['real_provider_calls']==0
 with psycopg.connect(DSN,autocommit=True,row_factory=dict_row) as c:
  assert str(c.execute('SELECT system_identifier FROM pg_control_system()').fetchone()['system_identifier'])=='7696088403504697382'
  messages=[]
  for case in fixture['cases']:
   row=c.execute("SELECT m.tenant_id,m.subject_id,m.session_id,m.id,m.request_id,t.business FROM messages m JOIN tenants t ON t.id=m.tenant_id WHERE m.id=%s AND m.role='assistant'",(case['assistant'],)).fetchone()
   assert row and row['business']=='isolated-test' and row['subject_id'].startswith('fixture-owner:') and row['request_id']==case['request']
   messages.append(dict(row,case=case['case']))
 client=Redis.from_url('redis://127.0.0.1:56424/0',decode_responses=False)
 assert client.info('server')['run_id']==RID
 snapshots=[];prefixes=set()
 for m in messages:
  suffix=':'+m['session_id']+':'+m['id']
  keys=list(client.scan_iter(match='weknora-resume:20261013:*'+suffix,count=100))
  if len(keys)!=1:raise ValueError('EXPLICIT_SOURCE_STREAM_UNAVAILABLE')
  key=keys[0].decode();assert key.endswith(suffix);prefix=key[:-len(suffix)];prefixes.add(prefix)
  dump,ttl,rows=client.eval(SNAPSHOT,1,key)
  if not dump or not rows or ttl==0 or ttl < -1:raise ValueError('EXPIRED_SOURCE_STREAM')
  assert client.type(key)==b'list'
  target=args.prefix+suffix
  if client.exists(target):raise ValueError('RETAIN_EXISTING_REDIS_TARGET')
  snapshots.append({'case':m['case'],'session':m['session_id'],'assistant':m['id'],'source_key':key,'target_key':target,'dump':base64.b64encode(dump).decode(),'dump_sha256':sha(dump),'rows':[v.decode() for v in rows],'row_sha256':sha(b'\n'.join(rows)),'source_expire_at_ms':int(time.time()*1000)+ttl if ttl>=0 else None})
 assert len(prefixes)==1 and len({m['tenant_id'] for m in messages})==1
 out.mkdir(parents=True);(out/'stream-snapshot.json').write_text(json.dumps(snapshots,indent=2),encoding='utf-8')
 (out/'owned-target.json').write_text(json.dumps({'container_id':CID,'source_run_id':RID,'prefix':args.prefix,'phase':'RESTORE_PENDING'}),encoding='utf-8')
 for row in snapshots:
  expire=row['source_expire_at_ms'];ttl=max(1,expire-int(time.time()*1000)) if expire is not None else 0
  if expire is not None and expire<=int(time.time()*1000):raise ValueError('SOURCE_EXPIRY_ELAPSED_BEFORE_RESTORE')
  client.restore(row['target_key'],ttl,base64.b64decode(row['dump']),replace=False)
  values=client.lrange(row['target_key'],0,-1)
  assert values==[v.encode() for v in row['rows']]
  assert sha(client.dump(row['target_key']))==row['dump_sha256']
  if expire is not None:assert abs(client.pttl(row['target_key'])-(expire-int(time.time()*1000)))<1000
 async def native():
  redis=AsyncRedis.from_url('redis://127.0.0.1:56424/0',decode_responses=True)
  try:
   assert (await redis.info('server'))['run_id']==RID
   before=RedisListStreamStore(redis,prefix=next(iter(prefixes)))
   after=RedisListStreamStore(redis,prefix=args.prefix);checks=[]
   for row in snapshots:
    original,offset=await before.get_events(row['session'],row['assistant'],0)
    restored,restored_offset=await after.get_events(row['session'],row['assistant'],0)
    assert [v.as_dict() for v in original]==[v.as_dict() for v in restored]
    assert offset==restored_offset==len(row['rows'])
    assert sum(v.type=='complete' for v in restored)==1
    tail,end=await after.get_events(row['session'],row['assistant'],offset)
    assert not tail and end==offset
    checks.append({'case':row['case'],'events':offset,'terminal_complete':1,'native_replay_equal':True,'offset_at_end_empty':True,'dump_sha256':row['dump_sha256']})
   return checks
  finally:await redis.aclose()
 with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:checks=runner.run(native())
 report={'kind':'ACTUAL_SYNTHETIC_REDIS_SSE_RESTORE_NEW_PREFIX_NATIVE_REPLAY','tenant':messages[0]['tenant_id'],'source_prefix':next(iter(prefixes)),'target_prefix':args.prefix,'redis_container':CID,'redis_run_id':RID,'snapshot':str(out/'stream-snapshot.json'),'snapshot_sha256':sha((out/'stream-snapshot.json').read_bytes()),'keys':checks,'expiry_preserved':True,'source_keys_modified':False,'old_business_database_accessed':False,'real_provider_calls':0,'worker_jobs_restored':'NOT_CLAIMED','cutover':'NOT_RUN'}
 (ROOT/'docs/weknora-port/evidence/P8_REDIS_SSE_RESTORE_20261014.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
 (out/'owned-target.json').write_text(json.dumps({'container_id':CID,'source_run_id':RID,'prefix':args.prefix,'phase':'RESTORE_VERIFIED'}),encoding='utf-8')
 print(json.dumps(report,indent=2));client.close()
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--prefix',required=True);main(parser.parse_args())
