"""Image-ingest continuation and memory extraction; never the legacy ingestion worker."""
import argparse,asyncio,json
from .development import DevelopmentSettings,assemble

async def run_once(runtime):
    return {'image_ingest':await runtime.image_tasks.run_due(),'memory_extract':await runtime.memory_enqueuer.run_due()}

async def serve(settings,once=False,*,runtime_factory=None):
    async with (runtime_factory or assemble)(settings) as runtime:
        if once:return await run_once(runtime)
        # A queue pump may exit while empty; a standalone worker must keep polling
        # for jobs enqueued later by the separate API process.
        while True:
            result=await run_once(runtime)
            if not any(result.values()):await asyncio.sleep(1)

def main():
    parser=argparse.ArgumentParser(description='Consume persisted WeKnora image-ingest and memory-extraction jobs; text ingest runs in API')
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    try:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:result=runner.run(serve(DevelopmentSettings.from_environment(),args.once))
        if result is not None:print(json.dumps(result))
    except KeyboardInterrupt:pass

if __name__=='__main__':main()
