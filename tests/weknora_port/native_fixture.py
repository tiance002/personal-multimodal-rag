"""Reuse the verified isolated assembly without running its test assertions/server."""
import ast,inspect,asyncio
from contextlib import asynccontextmanager
from . import test_runtime_pg as fixture


class IsolatedFixturePool:
    """Bounded reusable TCP clients; each factory lease still verifies PG identity."""
    def __init__(self,open_connection,maximum=32):
        self.open_connection=open_connection;self.maximum=maximum
        self.condition=asyncio.Condition();self.idle=[];self.count=0;self.closed=False
    async def acquire(self):
        async with self.condition:
            while not self.idle and self.count>=self.maximum and not self.closed:
                await self.condition.wait()
            if self.closed:raise RuntimeError('FIXTURE_POOL_CLOSED')
            if self.idle:return self.idle.pop()
            self.count+=1
        try:return await self.open_connection()
        except BaseException:
            async with self.condition:
                self.count-=1;self.condition.notify_all()
            raise
    async def release(self,connection):
        try:
            if not connection.closed:
                from psycopg.pq import TransactionStatus
                if connection.info.transaction_status!=TransactionStatus.IDLE:
                    await connection.rollback()
        except BaseException:
            await connection.close()
        async with self.condition:
            if self.closed or connection.closed:
                await connection.close();self.count-=1
            else:self.idle.append(connection)
            self.condition.notify_all()
    async def close(self):
        async with self.condition:
            self.closed=True;idle=self.idle;self.idle=[];self.count-=len(idle);self.condition.notify_all()
        for connection in idle:await connection.close()


@asynccontextmanager
async def isolated_mock_application(*,synthetic_monthly_limit=None):
    tree=ast.parse(inspect.getsource(fixture.test_actual_loopback_upload_rag_upgrade_sources_history_redis_replay_timeout_no_upgrade))
    scenario=next(n for n in tree.body[0].body if isinstance(n,ast.AsyncFunctionDef) and n.name=='scenario')
    body=[]
    for node in scenario.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='app' for t in node.targets):break
        body.append(node)
    source='\n'.join(ast.unparse(node) for node in body)
    assert 'httpx.MockTransport(handler)' in source and 'fixture.invalid' in source
    # Replace only the verified fixture's connection opening/closing strategy.
    # IsolatedConnectionFactory still constructs a fresh scoped identity guard.
    first=source.index('async def acquire():');last=source.index('leases = IsolatedConnectionFactory',first)
    source=source[:first]+"async def open_connection():\n    conn, _ = await connection(dsn)\n    return conn\npool = IsolatedFixturePool(open_connection)\nacquire = pool.acquire\nrelease = pool.release\n"+source[last:]
    source+='\nruntime.fixture_connection_pool=pool\nreturn runtime,redis,wire,root,pool'
    program='async def build_native_fixture():\n'+'\n'.join('    '+line for line in source.splitlines())
    namespace=dict(fixture.__dict__);namespace['dsn']=fixture.approved_dsn();namespace['IsolatedFixturePool']=IsolatedFixturePool
    if synthetic_monthly_limit is not None:
        from dataclasses import replace
        if type(synthetic_monthly_limit) is not int or not 1000000<=synthetic_monthly_limit<=10000000:
            raise ValueError('SYNTHETIC_CAPACITY_OUT_OF_BOUND')
        class FullCorpusFixtureCosts(fixture.FixtureCosts):
            def __init__(self):
                super().__init__()
                self.authority=replace(self.authority,monthly_budget_microunits=synthetic_monthly_limit,
                    approval_source='SYNTHETIC-FULL-CORPUS-MOCK-WIRE-ONLY-NOT-REAL-SPENDING')
        namespace['FixtureCosts']=FullCorpusFixtureCosts
    exec(compile(program,'<verified-isolated-native-assembly>','exec'),namespace)
    import backend.weknora_port.documents as doc_module
    previous_safe=doc_module._safe_error_code
    def public_fixture_diagnostic(error,fallback):
        import traceback
        print('PUBLIC_FULL_CORPUS_ERROR',type(error).__name__,str(error),
            [(row.filename,row.lineno,row.name) for row in traceback.extract_tb(error.__traceback__)[-5:]],flush=True)
        return previous_safe(error,fallback)
    doc_module._safe_error_code=public_fixture_diagnostic
    application,redis,wire,root,pool=await namespace['build_native_fixture']()
    try:yield application,wire,root
    finally:
        await application.stop_background_workers()
        await redis.aclose()
        await pool.close()
        doc_module._safe_error_code=previous_safe
