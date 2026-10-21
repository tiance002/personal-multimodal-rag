"""Normal FastAPI/worker plus reused passive P9 request/stage observers."""
import asyncio,hashlib,json,sys
from contextlib import asynccontextmanager
from pathlib import Path
ROOT=Path('E:/RAG-Worktrees/weknora-python-port-20261013');sys.path.insert(0,str(ROOT))
from scripts.weknora_port_p9_observed_api import observed_factory
from scripts.weknora_port_p8_1_observed_api import resolved
OUT=Path(__file__).parent

def factory(kind):
    base=observed_factory(OUT/'authority-bundle.json',OUT/(kind+'-evidence'))
    @asynccontextmanager
    async def observed(settings):
        async with base(settings) as app:
            original=app.model_factory
            async def build(config,key,gate):
                model=await resolved(original(config,key,gate))
                if config.provider=='deepseek':
                    async def response_hook(response):
                        # Caption uses non-stream JSON; reading caches the body for
                        # the unchanged decoder, not a second provider request.
                        raw=await response.aread()
                        safe=raw.replace(key.encode(),b'[REDACTED]') if key else raw
                        folder=OUT/(kind+'-evidence')/'deepseek-responses';folder.mkdir(parents=True,exist_ok=True)
                        wire=response.request.content;identity=hashlib.sha256(wire).hexdigest()
                        (folder/(identity+'.json')).write_text(json.dumps(dict(wire_sha256=identity,status_code=response.status_code,response=json.loads(safe)),ensure_ascii=False,indent=2),encoding='utf-8')
                    model.transport.client.event_hooks.setdefault('response',[]).append(response_hook)
                return model
            app.model_factory=build
            yield app
    return observed

async def main(kind):
    from backend.weknora_port.development import DevelopmentSettings
    settings=DevelopmentSettings.from_environment()
    if kind=='worker':
        from backend.weknora_port.worker import serve
        await serve(settings,runtime_factory=factory(kind))
    else:
        import uvicorn
        from backend.weknora_port.entrypoint import create_app
        await uvicorn.Server(uvicorn.Config(create_app(settings=settings,runtime_factory=factory(kind)),host='127.0.0.1',port=56425)).serve()
if __name__=='__main__':
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as r:r.run(main(sys.argv[1]))
