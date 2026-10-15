"""Default development API factory. Importing this module starts no infrastructure."""
from contextlib import asynccontextmanager
import argparse,os
from .development import DevelopmentSettings,assemble


def create_app(*,settings=None,runtime_factory=None):
    from .http_api import create_app as api
    settings=settings or DevelopmentSettings.from_environment()
    factory=runtime_factory or assemble
    class DeferredRuntime:
        target=None
        def __getattr__(self,name):
            if self.target is None:raise RuntimeError('RUNTIME_NOT_STARTED')
            return getattr(self.target,name)
    proxy=DeferredRuntime()
    async def principal(_):return proxy.target.principal
    app=api(proxy,resolve_principal=principal,allowed_origins=settings.origins,manage_background_workers=False)
    old=app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(app):
        async with factory(settings) as runtime:
            proxy.target=runtime
            try:
                async with old(app):yield
            finally:proxy.target=None
    app.router.lifespan_context=lifespan
    @app.get('/healthz')
    async def health():return {'service':'weknora-python-port','status':'ok'}
    @app.get('/readyz')
    async def ready():
        from fastapi.responses import JSONResponse
        runtime=proxy.target
        token=getattr(runtime,'token_authority',None)
        authorized=runtime.permissions is not None and (runtime.costs is not None or token is not None)
        if token is not None:
            from datetime import datetime,timezone
            from .authority import ConfiguredTokenAuthority,ApprovedTokenAuthority,ServerPermissionResolver
            now=datetime.now(timezone.utc)
            authorized=(isinstance(token,(ConfiguredTokenAuthority,ApprovedTokenAuthority)) and isinstance(runtime.permissions,ServerPermissionResolver)
                and token.settings.expires_at>now and token.settings.month==now.date().replace(day=1)
                and token.settings.subject_id==runtime.principal.subject_id and token.settings.tenant_id==runtime.principal.tenant_id)
        return JSONResponse({'implementation':'weknora-python-port','model_send_authority':'configured' if authorized else 'DENY_UNCONFIGURED',
            'budget_unit':'tokens' if token is not None else 'money' if authorized else 'disabled','authority_config_sha':getattr(runtime,'authority_config_sha',None)},status_code=200 if authorized else 503)
    return app


def main():
    parser=argparse.ArgumentParser(description='WeKnora Python development API; explicit isolated environment required')
    parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=8000)
    parser.add_argument('--authority-config')
    args=parser.parse_args()
    import uvicorn
    import asyncio
    from .authority import runtime_factory
    app=create_app(runtime_factory=runtime_factory(args.authority_config))
    server=uvicorn.Server(uvicorn.Config(app,host=args.host,port=args.port))
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:runner.run(server.serve())

if __name__=='__main__':main()
