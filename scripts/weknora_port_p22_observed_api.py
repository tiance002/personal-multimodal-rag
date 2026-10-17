"""Passive real P22 browser acceptance observer; no business API/model mocks."""
import asyncio,json,hashlib,os,argparse,time
from pathlib import Path
from contextlib import asynccontextmanager
from scripts.weknora_port_p9_observed_api import observed_factory as base_factory
from scripts.weknora_port_p17_observed_api import RawResponse,journal,decoder_observation
from scripts.weknora_port_p8_1_observed_api import resolved
from backend.weknora_port.models.types import ProviderError

def observed_factory(authority_path,root):
 root=Path(root);plan=json.loads((root/'plan.json').read_text(encoding='utf-8'));base=base_factory(authority_path,root)
 claims={r['request_id'] for p in (root/'diagnostics').glob('*.jsonl') for line in p.read_text(encoding='utf-8').splitlines() if (r:=json.loads(line)).get('kind')=='answer_intent'}
 runs={p.stem:json.loads(p.read_text(encoding='utf-8')) for p in (root/'runs').glob('*.json')}
 @asynccontextmanager
 async def factory(settings):
  async with base(settings) as runtime,decoder_observation(root):
   original=runtime.ordinary.prepare_turn
   async def prepare(turn,progress=None):
    if turn.run_id in runs or len(runs)>=3:raise ProviderError('P22_RUN_LIMIT_OR_REPEAT_DENIED')
    expected=plan['cases'][len(runs)]
    if turn.request.content!=expected['question'] or tuple(turn.context.scope.knowledge_base_ids)!=(expected['kb'],) or turn.context.scope.document_ids:raise ProviderError('P22_FROZEN_QUESTION_SCOPE_DENIED')
    if expected['kind']=='followup':
     prev=next(v for v in runs.values() if v['kind']=='normal')
     if prev['session_id']!=turn.session_id:raise ProviderError('P22_FOLLOWUP_SESSION_MISMATCH')
    (root/'runs').mkdir(exist_ok=True)
    row=dict(expected,run_id=turn.run_id,session_id=turn.session_id,assistant=turn.assistant_message_id,started=time.time())
    with (root/'runs'/(turn.run_id+'.json')).open('x',encoding='utf-8') as f:json.dump(row,f,ensure_ascii=False)
    runs[turn.run_id]=row
    prepared=await original(turn,progress)
    if expected['kind']=='no_evidence' and prepared.request.fixed_fallback is None:raise ProviderError('P22_EXPECTED_NO_EVIDENCE_NOT_OBSERVED')
    return prepared
   runtime.ordinary.prepare_turn=prepare
   old_factory=runtime.model_factory
   async def make(config,key,gate):
    model=await resolved(old_factory(config,key,gate));original_open=model.transport.open
    @asynccontextmanager
    async def observed_open(config,path,body,context,**kwargs):
     runid=next((r for r in runs if context.request_id==r or context.request_id.startswith(r+'/')),None)
     if runid is None or tuple(context.scope.knowledge_base_ids)!=(runs[runid]['kb'],):raise ProviderError('P22_MODEL_RUN_SCOPE_DENIED')
     if '/answer/' in context.request_id:
      if runs[runid]['kind']=='no_evidence' or (config.provider,config.model)!=('siliconflow','XingChenAGI/Xing4.0-29B') or context.request_id in claims or len(claims)>=2:raise ProviderError('P22_FINAL_ANSWER_CAP_DENIED')
      claims.add(context.request_id);journal(root,context.request_id,dict(kind='answer_intent',request_id=context.request_id,maximum=2,ordinal=len(claims)))
     async with original_open(config,path,body,context,**kwargs) as handle:
      proxy=RawResponse(handle.response,root,context.request_id,kwargs['capability']);handle.response=proxy
      try:yield handle
      finally:proxy.finish()
    model.transport.open=observed_open
    return model
   runtime.model_factory=make
   yield runtime
 return factory

class NetworkCapture:
 def __init__(self,app,root):self.app,self.root=app,Path(root);self.seq=0
 async def __call__(self,scope,receive,send):
  if scope['type']!='http':return await self.app(scope,receive,send)
  self.seq+=1;n=self.seq;start=time.time();path=scope['path'];chunks=[];response=[];status=None
  async def recv():
   event=await receive()
   if event['type']=='http.request':chunks.append(event.get('body',b''))
   return event
  async def sent(event):
   nonlocal status
   if event['type']=='http.response.start':status=event['status']
   if event['type']=='http.response.body' and '/messages' in path:response.append(event.get('body',b''))
   await send(event)
  try:await self.app(scope,recv,sent)
  finally:
   folder=self.root/'http';folder.mkdir(exist_ok=True)
   record={'sequence':n,'method':scope['method'],'path':path,'query':scope['query_string'].decode(),'status':status,'started':start,'latency_seconds':time.time()-start,'headers_saved':False}
   if '/messages' in path:
    record['request_body']=b''.join(chunks).decode('utf-8',errors='replace');record['response_body']=b''.join(response).decode('utf-8',errors='replace')
   (folder/(str(n).zfill(5)+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')

def main():
 p=argparse.ArgumentParser();p.add_argument('--authority-config',required=True);p.add_argument('--evidence-dir',required=True);p.add_argument('--port',type=int,required=True);a=p.parse_args()
 from backend.weknora_port.entrypoint import create_app
 import uvicorn
 app=NetworkCapture(create_app(runtime_factory=observed_factory(a.authority_config,a.evidence_dir)),a.evidence_dir)
 with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as r:r.run(uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=a.port,access_log=False)).serve())
if __name__=='__main__':main()
