"""P19 passive normal-RAG evidence, preserving production strategies."""
import argparse
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from scripts.weknora_port_p9_observed_api import observed_factory as base_factory
from scripts.weknora_port_p8_1_observed_api import resolved
from scripts.weknora_port_p17_observed_api import RawResponse, journal, decoder_observation


def observed_factory(authority_path, evidence_dir):
    root=Path(evidence_dir); factory=base_factory(authority_path,evidence_dir)
    claims={r['request_id'] for p in (root/'diagnostics').glob('*.jsonl')
            for line in p.read_text(encoding='utf-8').splitlines()
            if (r:=json.loads(line)).get('kind')=='answer_intent'}
    @asynccontextmanager
    async def assemble(settings):
        async with factory(settings) as runtime, decoder_observation(root):
            original_factory=runtime.model_factory
            async def model_factory(config,key,gate):
                model=await resolved(original_factory(config,key,gate))
                original_open=model.transport.open; original_stream=model.chat_stream
                @asynccontextmanager
                async def observed_open(config,path,body,context,**kwargs):
                    answer='/answer/' in context.request_id
                    if answer:
                        from backend.weknora_port.models.types import ProviderError
                        if ((config.provider,config.model)!=('siliconflow','XingChenAGI/Xing4.0-29B')
                                or context.request_id in claims or len(claims)>=17):
                            raise ProviderError('P19_ANSWER_CAP_OR_IDENTITY_DENIED')
                        claims.add(context.request_id)
                        journal(root,context.request_id,{'kind':'answer_intent','request_id':context.request_id,'maximum':17})
                    async with original_open(config,path,body,context,**kwargs) as handle:
                        raw=RawResponse(handle.response,root,context.request_id,kwargs['capability']) if answer or kwargs['capability']=='rerank' else None
                        if raw:handle.response=raw
                        try:yield handle
                        finally:
                            if raw:raw.finish()
                async def stream(messages,options,context):
                    async for event in original_stream(messages,options,context):
                        if '/answer/' in context.request_id:
                            journal(root,context.request_id,{'kind':'adapter_event','request_id':context.request_id,
                                'content':event.content,'response_type':event.response_type,'done':event.done,
                                'is_final':event.is_final,'finish_reason':event.finish_reason})
                        yield event
                model.transport.open=observed_open;model.chat_stream=stream
                return model
            runtime.model_factory=model_factory
            yield runtime
    return assemble


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',type=int,required=True)
    p.add_argument('--authority-config',required=True);p.add_argument('--evidence-dir',required=True);a=p.parse_args()
    from backend.weknora_port.entrypoint import create_app
    import uvicorn
    server=uvicorn.Server(uvicorn.Config(create_app(runtime_factory=observed_factory(a.authority_config,a.evidence_dir)),host='127.0.0.1',port=a.port))
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as r:r.run(server.serve())


if __name__=='__main__':main()
