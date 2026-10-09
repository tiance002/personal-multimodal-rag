"""Narrow real isolated integration runner; no external model/secret access.

Only task-owned loopback PG/Redis ports. Child processes invoke this same
guarded runner. Test transport is SIMULATED, never a provider network call.
"""
import os,sys,json,time,hashlib,socket,platform
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT);sys.path.insert(0,str(ROOT))
for key in list(os.environ):
    if key.startswith(('RAG_','SILICONFLOW_','DEEPSEEK_','LANGFUSE_','OLLAMA_')):os.environ.pop(key)
os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1';os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ['P55A_ISOLATED_AUTHORIZED']='p5-redis-lifecycle-r1'
sys.dont_write_bytecode=True
if sys.platform=='win32':
    v=sys.getwindowsversion();platform._uname_cache=platform.uname_result('Windows','',str(v.major),f'{v.major}.{v.minor}.{v.build}','UNKNOWN')
target=json.loads((ROOT/'var/reports/p5-redis-lifecycle-r1/isolated-target.json').read_text())
assert (target['database'],target['pg_port'],target['redis_port'],target['system_identifier'])==('p55a_lifecycle_test',52352,52355,'7694635764170571814')
blocked=dict(network=0,secret=0,subprocess=0,database=0,stdlib_socketpair=0)
def guard(event,args):
    if event=='socket.getaddrinfo':
        if args[0]!='127.0.0.1' or int(args[1]) not in {52352,52355}:blocked['network']+=1;raise RuntimeError('ISOLATED_NETWORK_BLOCKED')
    if event in {'socket.connect','socket.sendto'}:
        address=args[1]
        # Windows asyncio's documented socketpair fallback connects exactly
        # to the listener created in this stdlib frame, not another service.
        frame=sys._getframe(1)
        while frame is not None:
            if (event=='socket.connect' and frame.f_code.co_name=='_fallback_socketpair'
                    and Path(frame.f_code.co_filename).resolve()==Path(socket.__file__).resolve()):
                listener=frame.f_locals.get('lsock')
                if listener is not None and listener.getsockname()==address and address[0]=='127.0.0.1':
                    blocked['stdlib_socketpair']+=1
                    return
            frame=frame.f_back
        if not isinstance(address,tuple) or address[0]!='127.0.0.1' or address[1] not in {52352,52355}:blocked['network']+=1;raise RuntimeError('ISOLATED_NETWORK_BLOCKED')
    if event=='open' and isinstance(args[0],(str,bytes,os.PathLike)):
        path=os.fsdecode(args[0]).replace('\\','/').lower();name=path.rsplit('/',1)[-1]
        if name.startswith('.env') or name in {'auth.json','token'} or path=='e:/codex_workspace/2026-10-01/task-2/deepseek-call-ledger.json':blocked['secret']+=1;raise RuntimeError('ISOLATED_SECRET_BLOCKED')
    if event in {'os.system','os.posix_spawn'}:blocked['subprocess']+=1;raise RuntimeError('ISOLATED_SUBPROCESS_BLOCKED')
    if event=='subprocess.Popen':
        command=args[1]
        if not isinstance(command,str) or str(ROOT/'scripts/verify_p55a_isolated.py') not in command or '--worker' not in command or str(Path(sys.executable)) not in command:
            blocked['subprocess']+=1;raise RuntimeError('ISOLATED_SUBPROCESS_BLOCKED')
sys.addaudithook(guard)
import psycopg
from psycopg.conninfo import conninfo_to_dict
original=psycopg.Connection.connect.__func__
def safe_connect(cls,conninfo='',**kw):
    opts=conninfo_to_dict(conninfo,**{k:v for k,v in kw.items() if k in {'host','port','dbname','user','password'}})
    if (opts.get('host'),str(opts.get('port')),opts.get('dbname'),opts.get('user'))!=('127.0.0.1','52352','p55a_lifecycle_test','p55a'):
        blocked['database']+=1;raise RuntimeError('ISOLATED_DATABASE_BLOCKED')
    return original(cls,conninfo,**kw)
psycopg.Connection.connect=classmethod(safe_connect);psycopg.connect=psycopg.Connection.connect
from sqlalchemy import text
os.environ['P55B_ISOLATED_AUTHORIZED']='p55b-context-r1'
label=sys.argv[1]
if not label.replace('-','').isalnum():raise ValueError('ATTEMPT_LABEL_INVALID')
out=ROOT/'var/reports/p55b-context-r1'/label;out.mkdir(exist_ok=False)
from sqlalchemy import create_engine
url='postgresql+psycopg://p55a:SIMULATED_TEST_ONLY@127.0.0.1:52352/p55a_lifecycle_test'
engine=create_engine(url)
def snapshot():return {str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for d in ('backend/app','backend/tests','scripts','alembic/versions') for p in (ROOT/d).rglob('*.py')}
before=snapshot();(out/'source-before.json').write_text(json.dumps(before,indent=2))
with engine.begin() as c:
    db=c.execute(text('SELECT current_database(),oid,(SELECT system_identifier FROM pg_control_system()) FROM pg_database WHERE datname=current_database()')).one()
    assert db==('p55a_lifecycle_test',16384,7694635764170571814)
    revision=c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
    assert revision in {'0018_run_attempt_lifecycle','0019_context_checkpoints'}
    old_chat_constraint=c.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='attempt_role_ordinal_ck'")).scalar_one()
    schema_before={'identity':list(db),'revision':revision,'chat_constraint':old_chat_constraint}
    if revision=='0018_run_attempt_lifecycle':
        import importlib.util
        from alembic.runtime.migration import MigrationContext
        from alembic.operations import Operations
        path=ROOT/'alembic/versions/0019_context_checkpoints.py'
        spec=importlib.util.spec_from_file_location('context_migration',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        assert module.revision=='0019_context_checkpoints' and module.down_revision==revision
        with Operations.context(MigrationContext.configure(c)):module.upgrade()
        c.execute(text("UPDATE alembic_version SET version_num='0019_context_checkpoints' WHERE version_num='0018_run_attempt_lifecycle'"))
    assert c.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='attempt_role_ordinal_ck'")).scalar_one()==old_chat_constraint
    schema_after={'identity':list(db),'revision':c.execute(text('SELECT version_num FROM alembic_version')).scalar_one(),
        'constraints':[dict(x) for x in c.execute(text("SELECT conname,pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE conrelid IN ('context_checkpoints'::regclass,'context_compaction_attempts'::regclass,'context_run_protocol'::regclass) ORDER BY conname")).mappings()]}
(out/'schema.json').write_text(json.dumps({'before':schema_before,'after':schema_after},indent=2))
with engine.begin() as c:c.execute(text('CREATE TABLE IF NOT EXISTS p55a_simulated_sends(id bigserial PRIMARY KEY,run_id uuid NOT NULL,role text NOT NULL)'))
import pytest
started=time.perf_counter()
argv=['-q','-p','no:cacheprovider','--basetemp',str(out/'tmp'),'--junitxml',str(out/'junit.xml'),
      *(sys.argv[2:] or ['backend/tests/test_p55b_context_integration.py'])]
code=pytest.main(argv);after=snapshot()
(out/'source-after.json').write_text(json.dumps(after,indent=2))
(out/'execution.json').write_text(json.dumps(dict(argv=argv,pytest_exit_code=int(code),source_equal=before==after,
    elapsed_seconds=time.perf_counter()-started,guards=blocked,kind='REAL_ISOLATED_PG_REDIS_SIMULATED_PROVIDERS',real_model_calls=0),indent=2))
raise SystemExit(code)
