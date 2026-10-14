"""Actual React fixture reuses native assembly/MockTransport, no pytest PASS."""
import ast,asyncio,inspect,json,sys,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests/weknora_port'));sys.path.insert(0,str(ROOT))
import test_runtime_pg as fixture
source=ast.parse(inspect.getsource(fixture.test_actual_loopback_upload_rag_upgrade_sources_history_redis_replay_timeout_no_upgrade))
scenario=next(n for n in source.body[0].body if isinstance(n,ast.AsyncFunctionDef) and n.name=='scenario')
from backend.weknora_port.storage.history import LocalPrincipal
reuse=None;reuse_storage=None;reuse_prefix=None
if '--reuse-fixture' in sys.argv:
    previous=json.loads((ROOT/'var/weknora-port/browser-fixture/ready.json').read_text())
    assert previous['kind']=='PUBLIC_ISOLATED_MOCK_PROVIDER_FIXTURE' and previous['system_identifier']==fixture.EXPECTED
    assert previous['principal_subject'].startswith('fixture-owner:')
    reuse=LocalPrincipal(previous['principal_tenant'],previous['principal_subject'])
    reuse_storage=Path(previous['storage_root'])
    reuse_prefix=previous['redis_prefix']
setup=[]
for node in scenario.body:
    if isinstance(node,ast.Try):break
    setup.append(node)
text='\n'.join(ast.unparse(n) for n in setup)
text=text.replace("principal = LocalPrincipal(fixture_tenant, 'fixture-owner:' + str(uuid.uuid4()))","principal = REUSE or LocalPrincipal(fixture_tenant, 'fixture-owner:' + str(uuid.uuid4()))")
text=text.replace("root = Path('E:/RAG-Tests/runtime-source-' + str(uuid.uuid4()))","root = REUSE_STORAGE or Path('E:/RAG-Tests/runtime-source-' + str(uuid.uuid4()))")
text=text.replace("prefix = 'weknora-resume:20261013:' + str(uuid.uuid4())","prefix = REUSE_PREFIX or ('weknora-resume:20261013:' + str(uuid.uuid4()))")
text=text.replace('create_app(runtime, resolve_principal=lambda _request: principal)',
    "create_app(runtime, resolve_principal=lambda _request: principal,allowed_origins=('http://127.0.0.1:56426',))")
text=text.replace("allowed_origins=('http://127.0.0.1:56425',)",
    "allowed_origins=('http://127.0.0.1:56425','http://127.0.0.1:56426')")
if '--ui-scenarios' in sys.argv:
    assert reuse is None, 'UI_DELETE_REQUIRES_FRESH_SYNTHETIC_OWNER'
    text=text.replace('def handler(request):','async def handler(request):')
    text=text.replace('wire.append(', 'wire.append(',1)
    text=text.replace("    if body.get('stream'):","    if body.get('stream'):\n        await asyncio.sleep(15)",1)
work=ROOT/'var/weknora-port/browser-fixture' ;work.mkdir(parents=True,exist_ok=True)
stop=work/('stop-'+str(uuid.uuid4()))
extra='''
    ui_backup=None
    if UI_SCENARIOS:
        import hashlib
        from dataclasses import asdict
        from backend.weknora_port.conversation.types import MemoryContext,MemoryItem,MemoryScope
        context=MemoryContext(principal.tenant_id,principal.subject_id)
        async with leases.lease() as guard:
            await guard.connection.execute('UPDATE tenants SET memory_config=%s::jsonb WHERE id=%s',
                (json.dumps({'enabled':True,'write_mode':'explicit_only'}),principal.tenant_id))
        item=await runtime.memory_manager.remember(context,MemoryItem(str(uuid.uuid4()),principal.tenant_id,
            principal.subject_id,'fact','This is a recoverable synthetic browser memory.',topic='Browser fixture memory',origin='manual'))
        async with leases.lease() as guard:
            record=await (await guard.connection.execute('SELECT * FROM memory_items WHERE tenant_id=%s AND subject_id=%s AND id=%s',
                (principal.tenant_id,principal.subject_id,item.id))).fetchone()
        ui_backup={'kind':'NEW_SYNTHETIC_MEMORY_RECOVERY_ONLY','item':record,'tenant':principal.tenant_id,'subject':principal.subject_id}
        backup_path=WORK/('ui-memory-backup-'+str(principal.tenant_id)+'.json')
        assert not backup_path.exists()
        backup_path.write_text(json.dumps(ui_backup,default=str,indent=2),encoding='utf-8')
        backup_hash=hashlib.sha256(backup_path.read_bytes()).hexdigest()
        @app.post('/_fixture/restore-memory')
        async def restore_memory():
            assert hashlib.sha256(backup_path.read_bytes()).hexdigest()==backup_hash
            assert ui_backup['item']['tenant_id']==principal.tenant_id and ui_backup['item']['subject_id']==principal.subject_id
            async with leases.lease() as guard:
                count=(await (await guard.connection.execute('SELECT count(*) AS n FROM memory_items WHERE id=%s',(item.id,))).fetchone())['n']
                assert count==0
                await guard.connection.execute('INSERT INTO memory_items SELECT * FROM jsonb_populate_record(NULL::memory_items,%s::jsonb)',
                    (json.dumps(ui_backup['item'],default=str),))
                await guard.connection.execute("UPDATE memory_subjects SET block_text='',cache_revision=cache_revision+1 WHERE tenant_id=%s AND subject_id=%s",
                    (principal.tenant_id,principal.subject_id))
            await runtime.memory_manager._rebuild(MemoryScope(principal.tenant_id,principal.subject_id))
            return {'success':True,'restored_id':item.id,'backup_sha256':backup_hash,'tombstone_preserved':True}
    if UI_SCENARIOS:
        @app.post('/_fixture/memory-review-seed')
        async def seed_memory_review():
            confirm=await runtime.memory_manager.remember(context,MemoryItem('',0,'','fact','Synthetic review confirm content',topic='Review confirm fixture',inferred=True))
            reject=await runtime.memory_manager.remember(context,MemoryItem('',0,'','fact','Synthetic review reject content',topic='Review reject fixture',inferred=True))
            assert confirm.status==reject.status=='pending'
            return {'success':True,'data':{'confirm_id':confirm.id,'reject_id':reject.id,'synthetic_owner_only':True}}
    @app.post('/_fixture/fail-next-cheap')
    async def fail_next_cheap():
        nonlocal fail_cheap
        fail_cheap=True
        return {'kind':'PUBLIC_ISOLATED_MOCK_PROVIDER_FIXTURE','next_cheap_timeout':True}
    @app.get('/_fixture/metrics')
    async def fixture_metrics():
        return {'kind':'PUBLIC_ISOLATED_MOCK_PROVIDER_FIXTURE','wire_calls':len(wire),
                'answer_stream_calls':sum(bool(w['body'].get('stream')) for w in wire),
                'real_provider_calls':0,'database_system_identifier':EXPECTED,'redis_run_id':REDIS_RUN_ID}
    try:
        for _ in range(200):
            if server.started:break
            if task.done():await task
            await asyncio.sleep(.01)
        assert server.started
        ready={'kind':'PUBLIC_ISOLATED_MOCK_PROVIDER_FIXTURE','url':'http://127.0.0.1:56425',
               'stop_file':STOP,'system_identifier':EXPECTED,'redis_run_id':REDIS_RUN_ID,
               'source_text':SOURCE,'provider_calls_real':0,
               'ui_memory_backup':str(backup_path) if UI_SCENARIOS else None,'ui_memory_id':item.id if UI_SCENARIOS else None,
               'principal_tenant':principal.tenant_id,'principal_subject':principal.subject_id,'storage_root':str(root),'redis_prefix':prefix}
        (WORK/'ready.json').write_text(json.dumps(ready,indent=2),encoding='utf-8')
        print(json.dumps(ready),flush=True)
        while not Path(STOP).exists():await asyncio.sleep(.25)
    finally:
        server.should_exit=True
        await task
        await redis.aclose()
'''
namespace=dict(fixture.__dict__);namespace.update(dsn=fixture.approved_dsn(),STOP=str(stop),WORK=work,REUSE=reuse,REUSE_STORAGE=reuse_storage,REUSE_PREFIX=reuse_prefix,UI_SCENARIOS='--ui-scenarios' in sys.argv)
program='async def browser_fixture():\n'+ '\n'.join('    '+line for line in text.splitlines())+extra
(work/'generated_runtime.py').write_text(program,encoding='utf-8')
exec(compile(program,str(work/'generated_runtime.py'),'exec'),namespace)
fixture.actual_pg_run(namespace['browser_fixture']())
