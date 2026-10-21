"""Two real standard CLI processes; real isolated PG/Redis, closed mock models."""
import asyncio
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta

import httpx
from PIL import Image
from tests.weknora_port.test_memory_storage_pg import actual_pg_run, approved_dsn, connection, EXPECTED
from backend.weknora_port.storage.token_migrate import apply_token_schema

ROLES = [('embedding','siliconflow','BAAI/bge-m3'),('rerank','siliconflow','BAAI/bge-reranker-v2-m3'),
    ('cheap','siliconflow','XingChenAGI/Xing4.0-29B'),('expensive','deepseek','deepseek-flash'),
    ('vlm','siliconflow','Qwen/Qwen3-VL-32B-Instruct')]


def test_two_standard_cli_processes_upload_question_worker_and_replay():
    async def scenario():
        conn, guard = await connection(approved_dsn())
        await apply_token_schema(guard)
        tenant = (await (await conn.execute('INSERT INTO tenants(name,business) VALUES(%s,%s) RETURNING id',
            ('authority-cli-'+uuid.uuid4().hex, 'isolated-test'))).fetchone())['id']
        tag = uuid.uuid4().hex[:12]
        root = Path('E:/WK/run/authority-cli-' + tag); root.mkdir()
        key = root/'synthetic.key'; key.write_bytes(b's'*32)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        cfg = dict(schema='weknora-token-authority/v1', mode='synthetic', tenant_id=tenant,
            subject_id='synthetic-'+tag, account_id='synthetic-'+tag,
            expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
            approval_source='SYNTHETIC:closed-cli-only', month=datetime.now(timezone.utc).date().replace(day=1).isoformat(),
            carry_in_tokens=0, carry_status='KNOWN', carry_source='SYNTHETIC:fresh-unique-account',
            allowed_models=[[p,m] for _,p,m in ROLES], input_limit=200000, output_limit=8192, limit_tokens=1000000)
        config = root/'authority.json';config.write_text(json.dumps(cfg),encoding='utf-8')
        env = dict(os.environ)
        env.update(WEKNORA_PORT_DATABASE_URL=approved_dsn(), WEKNORA_PORT_DATABASE_SYSTEM_ID=EXPECTED,
            WEKNORA_PORT_DATABASE_NAME='weknora_python_port_20261013', WEKNORA_PORT_REDIS_URL='redis://127.0.0.1:56424/15',
            WEKNORA_PORT_REDIS_RUN_ID=os.environ['WEKNORA_SEQ_IMAGE_REDIS_ID'], WEKNORA_PORT_REDIS_PREFIX='weknora-python-authority:'+tag,
            WEKNORA_PORT_STORAGE_ROOT=str(root/'storage'), WEKNORA_PORT_SECRET_KEY_FILE=str(key), WEKNORA_PORT_ORIGINS=origin,
            PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
        api_cmd=[sys.executable,'-B','-m','backend.weknora_port.entrypoint','--port',str(port),'--authority-config',str(config)]
        worker_cmd=[sys.executable,'-B','-m','backend.weknora_port.worker','--once','--authority-config',str(config)]
        with (root/'api.stdout').open('wb') as out,(root/'api.stderr').open('wb') as err:
            api = subprocess.Popen(api_cmd,env=env,stdout=out,stderr=err)
        try:
            async with httpx.AsyncClient(base_url=origin,headers={'Origin':origin},timeout=45,trust_env=False) as client:
                for _ in range(150):
                    if api.poll() is not None:raise AssertionError((api.returncode,(root/'api.stderr').read_text(encoding='utf-8')))
                    try:
                        ready=await client.get('/readyz')
                        if ready.status_code==200:break
                    except httpx.ConnectError: pass
                    await asyncio.sleep(.1)
                assert ready.status_code==200 and ready.json()['budget_unit']=='tokens'
                roles=[dict(role=r,provider=p,model=m,base_url='https://fixture.invalid/v1',api_key='SYNTHETIC-NOT-A-CREDENTIAL',source='remote') for r,p,m in ROLES]
                result=await client.put('/api/v1/settings/model-configuration',json={'revision':'0','roles':roles})
                assert result.status_code==200,result.text
                kb=(await client.post('/api/v1/knowledge-bases',json={'name':'closed CLI data'})).json()['data']['id']
                assert (await client.patch(f'/api/v1/knowledge-bases/{kb}/external-processing',json={'cloud_allowed':True})).status_code==200
                result=await client.post(f'/api/v1/knowledge-bases/{kb}/documents',files={'file':('protocol.txt',b'Blue widgets use protocol W-17. This handbook documents blue widget protocol.','text/plain')})
                assert result.status_code==200 and result.json()['data']['status']=='completed',result.text
                doc=result.json()['data']['id']
                session=(await client.post('/api/v1/sessions',json={'knowledge_base_scope':[kb],'document_scope':[doc]})).json()['data']['id']
                request=dict(content='What protocol do blue widgets use?',request_id=str(uuid.uuid4()),knowledge_base_scope=[kb],document_scope=[doc])
                denied=await client.post(f'/api/v1/sessions/{session}/messages',json={**request,'authority_config':{'mode':'approved','budget_unit':'money'}})
                assert denied.status_code==422,denied.text
                result=await client.post(f'/api/v1/sessions/{session}/messages',json=request)
                assert result.status_code==200,result.text
                for _ in range(150):
                    messages=(await client.get(f'/api/v1/sessions/{session}/messages')).json()['data']
                    answer=next(m for m in messages if m['role']=='assistant')
                    if answer['is_completed']:break
                    await asyncio.sleep(.1)
                assert answer['is_completed'] and answer['knowledge_references'],answer
                ref=answer['knowledge_references'][0]
                assert (await client.get('/api/v1/chunks/'+ref['id'])).status_code==200
                before=(await (await conn.execute('SELECT count(*) AS n FROM port_token_calls WHERE tenant_id=%s',(tenant,))).fetchone())['n']
                await client.post(f'/api/v1/sessions/{session}/messages',json=request)
                after=(await (await conn.execute('SELECT count(*) AS n FROM port_token_calls WHERE tenant_id=%s',(tenant,))).fetchone())['n']
                assert before==after
                # Standard worker process, not an injected function, consumes persisted image work.
                model_rows=await (await conn.execute("SELECT id FROM models WHERE tenant_id=%s AND type='VLLM'",(tenant,))).fetchall()
                configured=await client.patch(f'/api/v1/knowledge-bases/{kb}/image-processing',json={'enable_multimodal':True,'ocr_adapter':'vlm','ocr_model_id':model_rows[0]['id'],'caption_mode':'explicit','caption_model_id':model_rows[0]['id']})
                assert configured.status_code==200,configured.text
                image=io.BytesIO();Image.new('RGB',(40,40),'white').save(image,format='PNG')
                result=await client.post(f'/api/v1/knowledge-bases/{kb}/documents',files={'file':('scan.png',image.getvalue(),'image/png')})
                assert result.status_code==200 and result.json()['data']['status']=='pending',result.text
                image_id=result.json()['data']['id']
                worker=await asyncio.to_thread(subprocess.run,worker_cmd,env=env,capture_output=True,timeout=60)
                (root/'worker.stdout').write_bytes(worker.stdout);(root/'worker.stderr').write_bytes(worker.stderr)
                assert worker.returncode==0,worker.stderr.decode('utf-8','replace')
                task=await (await conn.execute('SELECT state,error_code FROM port_image_tasks WHERE tenant_id=%s AND knowledge_id=%s',(tenant,image_id))).fetchone()
                assert task['state']=='completed',task
                rows=await (await conn.execute('SELECT request_id,provider,state,reserved_input,reserved_output,held_tokens,actual_total,usage_facts FROM port_token_calls WHERE tenant_id=%s ORDER BY request_id',(tenant,))).fetchall()
                assert rows and all(r['state']=='settled' for r in rows),rows
                assert all(r['usage_facts']['simulated'] for r in rows)
                first_exit=None
                api.terminate();first_exit=await asyncio.to_thread(api.wait,timeout=15)
                with (root/'api-restart.stdout').open('wb') as out,(root/'api-restart.stderr').open('wb') as err:
                    api=subprocess.Popen(api_cmd,env=env,stdout=out,stderr=err)
                for _ in range(150):
                    if api.poll() is not None:raise AssertionError((root/'api-restart.stderr').read_text(encoding='utf-8'))
                    try:
                        restarted=await client.get('/readyz')
                        if restarted.status_code==200:break
                    except httpx.ConnectError:pass
                    await asyncio.sleep(.1)
                assert restarted.json()['authority_config_sha']==ready.json()['authority_config_sha']
                replay=await client.post(f'/api/v1/sessions/{session}/messages',json=request)
                assert replay.status_code==200,replay.text
                latest=(await (await conn.execute('SELECT count(*) AS n FROM port_token_calls WHERE tenant_id=%s',(tenant,))).fetchone())['n']
                assert latest==len(rows)
                worker_again=await asyncio.to_thread(subprocess.run,worker_cmd,env=env,capture_output=True,timeout=30)
                assert worker_again.returncode==0 and json.loads(worker_again.stdout)=={'image_ingest':False,'memory_extract':False}
                held_request={**request,'request_id':str(uuid.uuid4()),'content':'SYNTHETIC_HOLD_AFTER_ADMISSION'}
                held_response=await client.send(client.build_request('POST',f'/api/v1/sessions/{session}/messages',json=held_request),stream=True)
                assert held_response.status_code==200
                for _ in range(150):
                    uncertain=await (await conn.execute("SELECT call_id,request_id,state,outcome,held_tokens FROM port_token_calls WHERE tenant_id=%s AND state='reserved'",(tenant,))).fetchall()
                    if uncertain:break
                    await asyncio.sleep(.05)
                assert len(uncertain)==1 and uncertain[0]['held_tokens']>0
                api.terminate();crash_exit=await asyncio.to_thread(api.wait,timeout=15)
                await held_response.aclose()
                with (root/'api-after-crash.stdout').open('wb') as out,(root/'api-after-crash.stderr').open('wb') as err:
                    api=subprocess.Popen(api_cmd,env=env,stdout=out,stderr=err)
                for _ in range(150):
                    if api.poll() is not None:raise AssertionError((root/'api-after-crash.stderr').read_text(encoding='utf-8'))
                    try:
                        if (await client.get('/readyz')).status_code==200:break
                    except httpx.ConnectError:pass
                    await asyncio.sleep(.1)
                replay_response=await client.send(client.build_request('POST',f'/api/v1/sessions/{session}/messages',json=held_request),stream=True)
                assert replay_response.status_code==200
                await replay_response.aclose()
                retained=await (await conn.execute("SELECT call_id,request_id,state,outcome,held_tokens FROM port_token_calls WHERE tenant_id=%s AND state='reserved'",(tenant,))).fetchall()
                assert retained==uncertain
                count_after_crash=(await (await conn.execute('SELECT count(*) AS n FROM port_token_calls WHERE tenant_id=%s',(tenant,))).fetchone())['n']
                assert count_after_crash==len(rows)+1
                api.terminate();restart_exit=await asyncio.to_thread(api.wait,timeout=15)
                disabled_cmd=api_cmd[:-2]
                with (root/'api-disabled.stdout').open('wb') as out,(root/'api-disabled.stderr').open('wb') as err:
                    api=subprocess.Popen(disabled_cmd,env=env,stdout=out,stderr=err)
                for _ in range(150):
                    if api.poll() is not None:raise AssertionError((root/'api-disabled.stderr').read_text(encoding='utf-8'))
                    try:
                        disabled=await client.get('/readyz')
                        if disabled.status_code==503:break
                    except httpx.ConnectError:pass
                    await asyncio.sleep(.1)
                assert disabled.status_code==503 and disabled.json()['budget_unit']=='disabled'
                receipt=dict(kind='TWO_REAL_STANDARD_CLI_PROCESSES_CLOSED_MOCK_MODELS',root=str(root),tenant=tenant,
                    api_argv=api_cmd,worker_argv=worker_cmd,worker_exitcode=worker.returncode,readiness=ready.json(),
                    answer=answer,worker_task=task,ledger=rows,replay_new_claims=after-before,real_provider_calls=0,
                    api_forced_stop_exitcode=first_exit,api_restart_forced_stop_exitcode=restart_exit,
                    restart_claims=latest-len(rows),worker_replay_exitcode=worker_again.returncode,
                    disabled_api_argv=disabled_cmd,disabled_status=disabled.status_code,
                    crash_forced_exitcode=crash_exit,crash_held_rows=retained,crash_replay_new_claims=0)
                Path(os.environ['WEKNORA_SEQ_IMAGE_RECEIPT']).write_text(json.dumps(receipt,indent=2,default=str)+'\n',encoding='utf-8')
        finally:
            api.terminate()
            await asyncio.to_thread(api.wait,timeout=15)
            await conn.close()
    actual_pg_run(scenario())
