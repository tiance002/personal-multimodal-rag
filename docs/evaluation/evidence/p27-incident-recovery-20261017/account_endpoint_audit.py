"""Read-only official metadata endpoints, not generation or Token consumption."""
import asyncio, hashlib, json
from pathlib import Path
import httpx

def local_key():
    for line in Path('E:/RAG quention/.env.local').read_text(encoding='utf-8-sig').splitlines():
        name,sep,value=line.partition('=')
        if sep and name.strip()=='DEEPSEEK_API_KEY':
            return value.strip().strip('\"').strip("'")
    raise RuntimeError('KEY_CONFIGURATION_MISSING')

async def main():
    key=local_key()
    result={'schema':'p27-read-only-provider-account-endpoints/v1','generation_calls':0,
        'credential_source':'existing .env.local / DEEPSEEK_API_KEY; value not exported',
        'credential_sha256':hashlib.sha256(key.encode()).hexdigest(),'requests':[]}
    async with httpx.AsyncClient(trust_env=False,follow_redirects=False,timeout=15) as client:
        for path in ['/models','/user/balance']:
            item={'method':'GET','endpoint':'https://api.deepseek.com'+path}
            try:
                response=await client.get(item['endpoint'],headers={'Authorization':'Bearer '+key})
                item['status']=response.status_code
                item['response_sha256']=hashlib.sha256(response.content).hexdigest()
                env=response.json()
                item['top_level_fields']=sorted(env) if isinstance(env,dict) else []
                if path=='/models' and response.status_code==200:
                    item['models']=[{k:m[k] for k in ('id','owned_by','name','max_output_tokens','input_modalities') if k in m} for m in env.get('data',[])]
                if path=='/user/balance' and response.status_code==200:
                    item['is_available']=env.get('is_available')
                    # Do not record money or infer token usage from money.
                    item['identity_fields_present']=[k for k in ('uid','user_id','account_id','organization_id') if k in env]
            except Exception as exc: item['error_type']=type(exc).__name__
            result['requests'].append(item)
    result['billing_account_association']='UNVERIFIED: metadata/balance cannot establish UID-to-key association or exclusivity'
    with (Path(__file__).parent/'account-endpoint-audit.json').open('x',encoding='utf-8') as f: json.dump(result,f,indent=2)
    print(json.dumps(result,indent=2))

if __name__=='__main__':
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner: runner.run(main())
