"""Code-owned closed protocol fixture, never an authorized hosted client."""
import json
import asyncio
import httpx
from .types import ProviderError


async def transport_handler(request):
    if request.url.host != 'fixture.invalid': raise ProviderError('SYNTHETIC_ENDPOINT_REQUIRED')
    body = json.loads(request.content)
    if 'SYNTHETIC_HOLD_AFTER_ADMISSION' in json.dumps(body):
        # Explicit closed-fixture fault injection. Admission is already committed;
        # killing this CLI must retain occupancy and must not replay a send.
        await asyncio.sleep(120)
    model = body['model']
    usage = {'prompt_tokens': 20, 'completion_tokens': min(10, body.get('max_tokens', body.get('max_completion_tokens', 10))), 'total_tokens': 0}
    usage['total_tokens'] = usage['prompt_tokens'] + usage['completion_tokens']
    if request.url.path.endswith('/embeddings'):
        return httpx.Response(200, json={'model':model, 'data':[{'index':i,'embedding':[.1,.2,.3]} for i,_ in enumerate(body['input'])], 'usage':{'prompt_tokens':20,'total_tokens':20}})
    if request.url.path.endswith('/rerank'):
        return httpx.Response(200,json={'results':[{'index':i,'relevance_score':.9} for i,_ in enumerate(body['documents'])], 'usage':{'prompt_tokens':20,'total_tokens':20}})
    prompt = '\n'.join(str(m.get('content','')) for m in body.get('messages',[]))
    if body.get('stream'):
        events = [{'model':model,'choices':[{'delta':{'content':'Blue widgets use W-17. <ref id="c1"/>'},'finish_reason':None}]},
            {'model':model,'choices':[{'delta':{},'finish_reason':'stop'}], 'usage':usage}]
        return httpx.Response(200,headers={'content-type':'text/event-stream'},content=''.join('data: '+json.dumps(e)+'\n\n' for e in events)+'data: [DONE]\n\n')
    if any(isinstance(m.get('content'),list) for m in body.get('messages',[])):
        value = 'Extracted scan: Blue widgets use protocol W-17.'
    elif 'precise document profiling expert' in prompt:
        value = json.dumps({'summary':'Blue widgets use protocol W-17.', 'gist':'Blue widget protocol','topics':['widgets'],'doc_type':'handbook','typical_question':'What protocol?'})
    elif 'You maintain a small set of long-term notes' in prompt:
        value = json.dumps({'memories':[],'topics':[]})
    else:
        value = json.dumps({'rewrite_query':'blue widgets protocol','intent':'kb_search','image_description':''})
    return httpx.Response(200,json={'model':model,'choices':[{'message':{'content':value},'finish_reason':'stop'}], 'usage':usage})
