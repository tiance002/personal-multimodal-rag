import asyncio, hashlib, json, subprocess, sys
from pathlib import Path
ROOT=Path('E:/RAG-Worktrees/weknora-python-port-20261013')
sys.path[:0]=[str(ROOT),str(ROOT/'var/weknora-port/runtime-deps')]
from scripts.weknora_port_mvp_real import connection
from backend.weknora_port.models.types import ModelConfig, Message, ChatOptions
from backend.weknora_port.models.request import build_chat_body

async def main():
    conn=await connection()
    try:
        async with conn.transaction():
            await conn.execute('SET TRANSACTION READ ONLY')
            row=await (await conn.execute("SELECT call_id,request_id,wire_sha256,wire_bytes,endpoint,state,outcome,reserved_input,reserved_output,held_tokens,actual_total,usage_facts FROM port_token_calls WHERE account_id='deepseek-owner-main' AND request_id='real/answer/1/cheap'")).fetchone()
        # The historical renderer is executed in a read-only namespace, without a
        # transport or credentials. All imports refer to unchanged data types.
        src=subprocess.check_output(['git','show','561e3bc^:backend/weknora_port/models/request.py'],cwd=ROOT).decode('utf-8')
        ns={'__name__':'backend.weknora_port.models.historical_request','__package__':'backend.weknora_port.models'}
        exec(compile(src,'<historical-request>','exec'),ns)
        cfg=ModelConfig(provider='deepseek',model='deepseek-flash',model_id='ds-chat',base_url='https://api.deepseek.com',source='remote')
        messages=[Message('system','Answer only from the user sentence.'),Message('user','What protocol do blue widgets use?')]
        body=ns['build_chat_body'](cfg,messages,ChatOptions(max_tokens=64))
        wire=json.dumps(body,ensure_ascii=False,separators=(',',':'),sort_keys=True,allow_nan=False).encode('utf-8')
        result={'schema':'p27-historical-wire-audit/v1','access':'read-only','provider_calls':0,
          'request_body':body,'reconstructed_wire':wire.decode(),'wire_sha256':hashlib.sha256(wire).hexdigest(),
          'wire_bytes':len(wire),'ledger':row,'historical_renderer_commit':'561e3bc^',
          'thinking_on_wire':body.get('thinking'),'reasoning_effort_on_wire':body.get('reasoning_effort'),
          'response_body_retained':False,'response_usage_retained':True}
        result['matches_durable_wire_identity']=result['wire_sha256']==row['wire_sha256'] and len(wire)==row['wire_bytes']
        assert result['matches_durable_wire_identity'],'WIRE_RECONSTRUCTION_NOT_CONFIRMED'
        with (Path(__file__).parent/'old-wire-audit.json').open('x',encoding='utf-8') as f: json.dump(result,f,indent=2,default=str)
        print(json.dumps(result,indent=2,default=str))
    finally: await conn.close()

if __name__=='__main__':
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner: runner.run(main())
