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
        authorized=proxy.target.permissions is not None and proxy.target.costs is not None
        return JSONResponse({'implementation':'weknora-python-port','model_send_authority':'configured' if authorized else 'DENY_UNCONFIGURED'},status_code=200 if authorized else 503)
    return app


def main():
    parser=argparse.ArgumentParser(description='WeKnora Python development API; explicit isolated environment required')
    parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=8000)
    args=parser.parse_args()
    import uvicorn
    import asyncio
    server=uvicorn.Server(uvicorn.Config('backend.weknora_port.entrypoint:create_app',factory=True,host=args.host,port=args.port))
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:runner.run(server.serve())

if __name__=='__main__':main()
