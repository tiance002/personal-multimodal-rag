"""Read-only normal HTTP chunk readback after model fences are closed."""
import asyncio,hashlib,json,subprocess,sys,time
from pathlib import Path
from run import OUT,ROOT,read,save,client,request,environment
from scripts.weknora_port_mvp_real import connection
async def main():
    before=read('snapshots/final.json')['calls']
    cmd=[sys.executable,'-B',str(OUT/'live_service.py'),'api']
    p=subprocess.Popen(cmd,cwd=ROOT,env=environment(),stdout=(OUT/'readback-api.stdout').open('ab'),stderr=(OUT/'readback-api.stderr').open('ab'))
    save('readback-process.json',dict(pid=p.pid,command=cmd,scope='read-only chunk API; all model-month fences already closed'))
    results=[]
    try:
        async with client() as c:
            for _ in range(30):
                try:
                    health=await c.get('/healthz')
                    if health.status_code==200:break
                except Exception:pass
                await asyncio.sleep(1)
            for chunk in read('snapshots/final.json')['chunks']:
                if chunk['chunk_type'] not in ('image_ocr','image_caption'):continue
                payload=await request(c,'readback-'+chunk['id'],'GET','/api/v1/chunks/'+chunk['id'])
                value=payload['data'];metadata=chunk['metadata']
                assert value['document_id']==chunk['knowledge_id']
                assert value['knowledge_base_id']==chunk['knowledge_base_id']
                assert value['source_version']==metadata['source_version']
                assert value['page_number']==metadata['page_number']
                assert value['text']==chunk['content']
                results.append(dict(chunk_id=chunk['id'],payload=payload,scope_version_page_text_match=True))
            save('image-chunk-api-readbacks.json',results)
        conn=await connection()
        try:
            rows=await (await conn.execute('SELECT * FROM port_token_calls WHERE tenant_id=%s ORDER BY call_id',(read('state.json')['tenant'],))).fetchall()
        finally:await conn.close()
        from scripts.weknora_port_p22_fence import digest
        assert digest(rows)==digest(before),'READBACK_MADE_MODEL_CALLS'
        save('readback-zero-new-calls.json',dict(status='PASS',readbacks=len(results),provider_calls_before=len(before),provider_calls_after=len(rows),ledger_sha256=digest(rows)))
        print(json.dumps({'status':'PASS','readbacks':len(results),'new_model_calls':0}))
    finally:
        p.terminate()
        try:p.wait(timeout=5)
        except subprocess.TimeoutExpired:p.kill()
if __name__=='__main__':
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as r:r.run(main())
