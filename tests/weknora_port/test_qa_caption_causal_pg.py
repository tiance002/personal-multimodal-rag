"""Byte-exact baseline caption modules in the same actual isolated HTTP assembly."""
import asyncio,hashlib,io,json,subprocess,types
from pathlib import Path
from PIL import Image
from tests.weknora_port.native_fixture import isolated_mock_application
from tests.weknora_port.test_memory_storage_pg import actual_pg_run
from tests.weknora_port.test_qa_followup_fallback_pg import http_client,kb,REDIS_ID


def test_caption_language_assertion_task_and_send_ledger_causal_comparison():
    async def scenario():
        evidence=[]
        image=io.BytesIO();Image.new('RGB',(40,40),'white').save(image,format='PNG');raw=image.getvalue()
        for baseline in ('2bc2a063a11d1cf3a9e42a9ecfcd55d4124ce3df','1530af360736451a96d6faf6d2c200f2ab99f3ff'):
            path='backend/weknora_port/image_multimodal.py'
            source=subprocess.check_output(['C:/Program Files/Git/cmd/git.exe','show',baseline+':'+path])
            module=types.ModuleType('backend.weknora_port._qa_caption_'+baseline[:7])
            module.__package__='backend.weknora_port'
            module.__file__=str(Path(path).resolve())
            exec(compile(source,module.__file__,'exec'),module.__dict__)
            async with isolated_mock_application(expected_redis_run_id=REDIS_ID,redis_db=15) as (runtime,wire,root):
                # Only this source module varies. Real HTTP, task queue, gates and PG stay active.
                runtime.documents.asset_processor=module.SourceImageProcessor(runtime)
                runtime.image_tasks.randbelow=lambda bound:0  # deterministic source retry jitter; base delay stays 15s
                async with http_client(runtime) as client:
                    kbid=await kb(client,'QA caption causal '+baseline[:7])
                    async with runtime.leases.lease() as guard:
                        roles=dict((await runtime._snapshot(guard)).model_ids)
                    configured=await client.patch(f'/api/v1/knowledge-bases/{kbid}/image-processing',json={
                        'enable_multimodal':True,'ocr_adapter':'vlm','ocr_model_id':roles['vlm'],
                        'caption_mode':'explicit','caption_model_id':roles['vlm'],'custom_instructions':'BUSINESS_ONLY_CAPTION'})
                    assert configured.status_code==200,configured.text
                    reply=await client.post(f'/api/v1/knowledge-bases/{kbid}/documents',files={'file':('qa-language.png',raw,'image/png')})
                    assert reply.status_code==200,reply.text
                    did=reply.json()['data']['id'];deadline=asyncio.get_running_loop().time()+35
                    while True:
                        tasks=await runtime._rows('SELECT id,state,error_code,outcomes,attempt FROM port_image_tasks WHERE tenant_id=%s AND subject_id=%s AND knowledge_id=%s',
                            (runtime.principal.tenant_id,runtime.principal.subject_id,did))
                        assert len(tasks)==1
                        if tasks[0]['state'] not in ('pending','processing','retry'):break
                        assert asyncio.get_running_loop().time()<deadline,tasks
                        await asyncio.sleep(.05)
                    prompts=[part['text'] for row in wire if row['body'].get('model')=='Qwen/Qwen3-VL-32B-Instruct'
                        for part in row['body']['messages'][0]['content'] if part['type']=='text']
                    caption=[text for text in prompts if 'BUSINESS_ONLY_CAPTION' in text]
                    assert len(caption)==1
                    docs=(await client.get(f'/api/v1/knowledge-bases/{kbid}/documents')).json()['data']
                    doc=next(row for row in docs if row['id']==did)
                    original=await client.get(f'/api/v1/knowledge-bases/{kbid}/documents/{did}/source')
                    assert original.status_code==200 and original.content==raw
                    ledger=await runtime._rows('SELECT capability,state,usage FROM send_calls WHERE account_id=%s ORDER BY created_at',
                        (runtime.costs.authority.account_id,))
                    row={'baseline':baseline,'source_module_sha256':hashlib.sha256(source).hexdigest(),
                         'assembly':'current QA assembly; only byte-exact caption module swapped',
                         'task':tasks[0],'document':doc,'caption_prompt':caption[0],'ledger':ledger,
                         'source_readback_http':original.status_code,'original_sha256':hashlib.sha256(raw).hexdigest(),
                         'redis_db':15,'real_provider_calls':0,'retry_jitter':0,'retry_base_seconds':15}
                    evidence.append(row)
        output=Path('docs/weknora-port/evidence/QA_ENTRY_20261015/caption.causal.'+__import__('uuid').uuid4().hex+'.json')
        assert not output.exists()
        output.write_text(json.dumps(evidence,indent=2,default=str)+'\n',encoding='utf-8')
        old,new=evidence
        assert 'Chinese (Simplified)' not in old['caption_prompt'] and 'in .' in old['caption_prompt']
        assert 'Chinese (Simplified)' in new['caption_prompt']
        assert old['task']['state']=='unknown' and any(row['state']=='unknown' for row in old['ledger'])
        assert new['task']['state']=='completed' and new['document']['status']=='completed'
        vision=[row for row in new['ledger'] if row['capability']=='vision']
        assert len(vision)==2 and all(row['state']=='settled' for row in vision)
        # Existing embedding fixture omits completion_tokens: UNKNOWN must stay occupied.
        embedding=[row for row in new['ledger'] if row['capability']=='embedding']
        assert embedding and all(row['state']=='unknown' for row in embedding)
    actual_pg_run(scenario())
