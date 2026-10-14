"""Actual loopback FastAPI + PG/Redis + source RAG/P5 + uniform mock wire.

Synthetic vectors, model responses, profile postprocessor and price grants are
explicit fixtures. No hosted provider request and no private/old data used.
"""
import asyncio, json, uuid, socket
from pathlib import Path
from datetime import datetime, timezone, timedelta
import httpx, pytest, uvicorn
from redis.asyncio import Redis
from dataclasses import replace

from tests.weknora_port.test_memory_storage_pg import approved_dsn, actual_pg_run, connection, EXPECTED, PROHIBITED
from backend.weknora_port.storage.connections import IsolatedConnectionFactory
from backend.weknora_port.storage.postgres import IsolationPolicy
from backend.weknora_port.storage.local_principal import LocalPrincipalStore
from backend.weknora_port.storage.configuration_migrate import apply_configuration_schema
from backend.weknora_port.storage.generation_migrate import apply_generation_schema
from backend.weknora_port.storage.send_gate_migrate import apply_send_gate_schema
from backend.weknora_port.storage.turns_migrate import apply_turn_schema
from backend.weknora_port.storage.models_migrate import apply_model_schema
from backend.weknora_port.storage.send_gate import SendAuthorization, ContentOrigin, MonthlyAuthority, ReservationEstimate, ActualCost
from backend.weknora_port.models.provider import ProviderModel
from backend.weknora_port.models.transport import AsyncHTTPTransport
from backend.weknora_port.models.transport import resolve_endpoint
from backend.weknora_port.models.types import CallContext, TrustedScope, ProviderError
from backend.weknora_port.model_registry import NeutralModelRegistry
from backend.weknora_port.streaming.manager import RedisListStreamStore
from backend.weknora_port.crypto import SecretCipher
from backend.weknora_port.storage.document_profile_migrate import apply_document_profile_schema
from backend.weknora_port.storage.image_tasks_migrate import apply_image_task_schema
from backend.weknora_port.runtime import ApplicationRuntime, ConversationSettings, LeasedSendGate
from backend.weknora_port.generation import RouterPolicy
from backend.weknora_port.documents import DocumentLifecycleService, KnowledgeBaseSettings
from backend.weknora_port.http_api import create_app


# Existing container/volume recovered after Owner-authorized Docker restart.
REDIS_RUN_ID = "586b92f4649b78508382dfd52d64d848f45b0a7c"
SOURCE = "The source handbook says blue widgets use protocol W-17. Keep the widgets dry."


class FixtureCosts:
    def __init__(self):
        now = datetime.now(timezone.utc).date().replace(day=1)
        self.authority = MonthlyAuthority(
            "resume-http-fixture-" + str(uuid.uuid4()), now, 1000000, 0,
            "KNOWN", "synthetic-new-empty-account", "synthetic-mock-wire-only")
    async def monthly(self, call): return self.authority
    async def estimate(self, call):
        return ReservationEstimate(100, 100000, "SYNTHETIC-NOT-BILLING", "fixture-conservative-bound")
    async def actual(self, call, usage, source):
        return ActualCost(10, "SYNTHETIC-NOT-BILLING")


class FixturePermissions:
    def __init__(self, leases, principal):
        self.leases, self.principal = leases, principal
        self.calls = []
    async def resolve(self, call):
        self.calls.append(call)
        assert call.context.scope.owner_id == self.principal.subject_id
        kbs = tuple(sorted(set(call.context.scope.knowledge_base_ids) | set(call.context.required_origin_kbs)))
        async with self.leases.lease() as guard:
            rows = await (await guard.connection.execute(
                "SELECT id,cloud_allowed FROM knowledge_bases WHERE tenant_id=%s "
                "AND id=ANY(%s) AND deleted_at IS NULL",
                (self.principal.tenant_id, list(kbs)))).fetchall()
        readable = frozenset(row["id"] for row in rows)
        allowed = frozenset(row["id"] for row in rows if row["cloud_allowed"])
        # This closed public fixture knows all question/evidence/history inputs
        # originate solely from these fixture KBs. It is not a production resolver.
        origins = tuple(ContentOrigin(kind, call.wire_sha256, kbs, True)
                        for kind in ("question", "evidence", "history", "memory"))
        return SendAuthorization(
            call.context.request_id, self.principal.subject_id, call.provider,
            call.model, call.model_id, call.endpoint, call.capability, call.wire_sha256,
            origins, readable, allowed, "SYNTHETIC-MOCK-WIRE-ONLY", True, True, True,
            100000, 4096, datetime.now(timezone.utc) + timedelta(minutes=5))


def stream_response(model, text, finish):
    rows = [
        {"model": model, "choices": [{"delta": {"content": text}, "finish_reason": None}]},
        {"model": model, "choices": [{"delta": {}, "finish_reason": finish}],
         "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}},
    ]
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
        content="".join("data: " + json.dumps(row) + "\n\n" for row in rows) + "data: [DONE]\n\n")


def test_actual_loopback_upload_rag_upgrade_sources_history_redis_replay_timeout_no_upgrade(monkeypatch):
    dsn = approved_dsn()
    import backend.weknora_port.documents as doc_module
    original_safe_code = doc_module._safe_error_code
    def fixture_diagnostic(exc, fallback):
        import traceback
        print("PUBLIC_SYNTHETIC_DIAGNOSTIC", type(exc).__name__, str(exc),
              [(row.filename, row.lineno, row.name) for row in traceback.extract_tb(exc.__traceback__)[-3:]])
        return original_safe_code(exc, fallback)
    monkeypatch.setattr(doc_module, "_safe_error_code", fixture_diagnostic)
    from backend.weknora_port.service import RuntimeService
    original_run_code = RuntimeService._safe_code
    def runtime_fixture_diagnostic(exc):
        import traceback
        print("PUBLIC_SYNTHETIC_RUNTIME_DIAGNOSTIC", type(exc).__name__, str(exc),
              [(row.filename, row.lineno, row.name) for row in traceback.extract_tb(exc.__traceback__)[-4:]])
        return original_run_code(exc)
    monkeypatch.setattr(RuntimeService, "_safe_code", staticmethod(runtime_fixture_diagnostic))
    from backend.weknora_port.runtime import LeasedKnowledgeRepository
    original_repository_method = LeasedKnowledgeRepository.__getattr__
    def repository_fixture_diagnostic(self, name):
        invoke = original_repository_method(self, name)
        async def observed(*args, **kwargs):
            try:
                return await invoke(*args, **kwargs)
            except Exception as exc:
                import traceback
                print("PUBLIC_SYNTHETIC_RETRIEVAL_DIAGNOSTIC", name, type(exc).__name__, str(exc),
                      [(row.filename, row.lineno, row.name) for row in traceback.extract_tb(exc.__traceback__)[-4:]])
                raise
        return observed
    monkeypatch.setattr(LeasedKnowledgeRepository, "__getattr__", repository_fixture_diagnostic)

    async def scenario():
        async def acquire():
            conn, _ = await connection(dsn)
            return conn
        async def release(conn): await conn.close()
        leases = IsolatedConnectionFactory(acquire, release, IsolationPolicy(EXPECTED, PROHIBITED))
        async with leases.lease() as guard:
            for apply in (apply_configuration_schema, apply_generation_schema,
                          apply_send_gate_schema, apply_turn_schema, apply_model_schema, apply_document_profile_schema, apply_image_task_schema):
                await apply(guard)
        persistent_principal = await LocalPrincipalStore(leases).bootstrap()
        # Each integration fixture has its own public synthetic owner namespace;
        # preserve persistent bootstrap and all previous failed/UNKNOWN rows.
        from backend.weknora_port.storage.history import LocalPrincipal
        async with leases.lease() as fixture_guard:
            fixture_tenant=(await (await fixture_guard.connection.execute(
                'INSERT INTO tenants(name,business) VALUES(%s,%s) RETURNING id',
                ('resume-public-fixture-'+str(uuid.uuid4()),'isolated-test'))).fetchall())[0]['id']
        principal=LocalPrincipal(fixture_tenant,'fixture-owner:'+str(uuid.uuid4()))
        redis = Redis.from_url("redis://127.0.0.1:56424/0", decode_responses=True)
        assert (await redis.info("server"))["run_id"] == REDIS_RUN_ID
        prefix = "weknora-resume:20261013:" + str(uuid.uuid4())
        store = RedisListStreamStore(redis, prefix=prefix)
        wire = []
        fail_cheap = False

        def handler(request):
            nonlocal fail_cheap
            assert request.url.host == "fixture.invalid"
            body = json.loads(request.content)
            wire.append({"path": request.url.path, "body": body})
            if request.url.path.endswith("/embeddings"):
                values = body["input"]
                return httpx.Response(200, json={"model": body["model"],
                    "data": [{"index": i, "embedding": [.1, .2, .3]} for i, _ in enumerate(values)],
                    "usage": {"prompt_tokens": 10, "total_tokens": 10}})
            if request.url.path.endswith("/rerank"):
                return httpx.Response(200, json={"results": [
                    {"index": i, "relevance_score": .9} for i, _ in enumerate(body["documents"])]})
            if body.get('model')=='Qwen/Qwen3-VL-32B-Instruct':
                parts=body['messages'][0]['content']
                assert isinstance(parts,list)
                prompt=next(p['text'] for p in parts if p['type']=='text')
                image=next(p['image_url']['url'] for p in parts if p['type']=='image_url')
                assert image.startswith('data:image/') and ';base64,' in image
                content='Extracted scan: Blue widgets use protocol W-17.' if 'OCR' in prompt else 'A blue widget protocol sheet.'
                if 'OCR' in prompt:assert 'BUSINESS_ONLY_CAPTION' not in prompt
                else:assert 'BUSINESS_ONLY_CAPTION' in prompt and 'Chinese (Simplified)' in prompt
                return httpx.Response(200,json={'model':body['model'],'choices':[{'message':{'content':content},'finish_reason':'stop'}],
                    'usage':{'prompt_tokens':20,'completion_tokens':10,'total_tokens':30}})
            if body.get('messages',[{}])[0].get('content','').startswith('You maintain a small set of long-term notes'):
                assert body['model']=='XingChenAGI/Xing4.0-29B'
                assert 'I use blue widgets' in body['messages'][1]['content']
                return httpx.Response(200,json={'model':body['model'],'choices':[{'message':{
                    'content':json.dumps({'memories':[{'action':'add','kind':'fact','topic':'Widget usage',
                        'content':'User uses blue widgets.','importance':4,'source':3,'inferred':False}],
                        'topics':[]})},'finish_reason':'stop'}],
                    'usage':{'prompt_tokens':20,'completion_tokens':10,'total_tokens':30}})
            if body.get("stream"):
                if "Xing" in body["model"]:
                    if fail_cheap:
                        raise httpx.ReadTimeout("synthetic fixture timeout", request=request)
                    return stream_response(body["model"], "CHEAP_REJECTED_DRAFT", "length")
                return stream_response(body["model"], "Blue widgets use W-17. <ref id=\"c1\"/>", "stop")
            if 'precise document profiling expert' in body.get('messages',[{}])[0].get('content',''):
                assert 'source_version' not in body['messages'][1]['content']
                assert 'local_subject_id' not in body['messages'][1]['content']
                return httpx.Response(200,json={'model':body['model'],
                    'choices':[{'message':{'content':json.dumps({'summary':SOURCE,
                        'gist':'Blue widget protocol','topics':['widgets','protocol'],'doc_type':'handbook',
                        'typical_question':'Which protocol do blue widgets use?'})},'finish_reason':'stop'}],
                    'usage':{'prompt_tokens':20,'completion_tokens':10,'total_tokens':30}})
            return httpx.Response(200, json={"model": body["model"],
                "choices": [{"message": {"content": json.dumps(
                    {"rewrite_query": "blue widgets protocol", "intent": "kb_search", "image_description": ""})},
                    "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}})

        def model_factory(config, key, gate):
            endpoints = [resolve_endpoint(config, suffix) for suffix in
                         ("/chat/completions", "/embeddings", "/rerank")]
            transport = AsyncHTTPTransport(
                allowed_endpoints=endpoints, gate=gate, credential=lambda _: key,
                http_transport=httpx.MockTransport(handler))
            return ProviderModel(config, transport)

        box = {}
        async def settings(who):
            runtime = box["runtime"]
            async with leases.lease() as guard:
                snapshot = await runtime._snapshot(guard)
            return KnowledgeBaseSettings(
                snapshot.model_id("embedding"), snapshot.model_id("cheap"), snapshot.model_id("rerank"),
                {"chunk_size": 128, "chunk_overlap": 0},
                {"vector_enabled": True, "keyword_enabled": True, "wiki_enabled": False, "graph_enabled": False}, {"enable_multimodal": False})

        async def embedder_for_kb(who, kb):
            runtime = box["runtime"]
            context = CallContext(str(uuid.uuid4()), TrustedScope(who.subject_id, (kb["id"],)))
            gate = LeasedSendGate(runtime)
            registry = NeutralModelRegistry(
                runtime.models, lambda config, key: model_factory(config, key, gate),
                owner_id=who.subject_id, catalogue=runtime.catalogue)
            class Embedder:
                async def batch_embed_with_pool(self, texts, *, attempt):
                    model = await registry.embedding(kb["embedding_model_id"], context)
                    try:
                        return (await model.embed(texts, context.child("upload-embed-" + str(attempt)))).vectors
                    finally:
                        await registry.close()
            return Embedder()

        root = Path("E:/RAG-Tests/runtime-source-" + str(uuid.uuid4()))
        documents = DocumentLifecycleService(
            leases, root, principal=principal, resolve_kb_settings=settings,
            embedder_for_kb=embedder_for_kb)
        costs = FixtureCosts()
        permissions = FixturePermissions(leases, principal)
        runtime = ApplicationRuntime(
            leases, principal, store, documents, model_factory=model_factory,
            cipher=SecretCipher(b"s" * 32), policy=RouterPolicy(mode="DYNAMIC"),
            tokenizer_asset="D:/RAG-Tokenizer-Assets/cl100k_base.tiktoken",
            permissions=permissions, costs=costs)
        box["runtime"] = runtime
        roles = [
            ("embedding", "siliconflow", "BAAI/bge-m3"),
            ("rerank", "siliconflow", "BAAI/bge-reranker-v2-m3"),
            ("cheap", "siliconflow", "XingChenAGI/Xing4.0-29B"),
            ("expensive", "deepseek", "deepseek-flash"),
            ("vlm", "siliconflow", "Qwen/Qwen3-VL-32B-Instruct"),
        ]
        current = await runtime.read_model_configuration(principal)
        await runtime.update_model_configuration(principal, current["revision"], [
            {"role": role, "provider": provider, "model": name, "source":"remote",
             "base_url": "https://fixture.invalid/v1", "api_key": "SYNTHETIC-NOT-A-KEY"}
            for role, provider, name in roles])
        app = create_app(runtime, resolve_principal=lambda request: principal,
                         allowed_origins=("http://127.0.0.1:56425",))
        with socket.socket() as check:
            assert check.connect_ex(("127.0.0.1", 56425)) != 0, "fixture port already occupied"
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=56425,
                                              log_level="error", access_log=False))
        task = asyncio.create_task(server.serve())
        try:
            for _ in range(200):
                if server.started: break
                if task.done(): await task
                await asyncio.sleep(.01)
            assert server.started
            async with httpx.AsyncClient(base_url="http://127.0.0.1:56425",
                    headers={"Origin": "http://127.0.0.1:56425"}, timeout=20) as client:
                assert (await client.get("/api/v1/runtime")).status_code == 200
                created = await client.post("/api/v1/knowledge-bases", json={"name": "HTTP public synthetic source"})
                assert created.status_code == 200, created.text
                kb = created.json()["data"]["id"]
                enabled = await client.patch(f"/api/v1/knowledge-bases/{kb}/external-processing",
                                             json={"cloud_allowed": True})
                assert enabled.status_code == 200, enabled.text
                uploaded = await client.post(f"/api/v1/knowledge-bases/{kb}/documents",
                                              files={"file": ("source.txt", SOURCE, "text/plain")})
                assert uploaded.status_code == 200, uploaded.text
                document = uploaded.json()["data"]
                assert document["status"] == "completed", document.get("error_code")
                async with leases.lease() as profile_guard:
                    profile_rows=await (await profile_guard.connection.execute(
                        'SELECT description,profile,summary_status,custom_metadata FROM knowledges WHERE id=%s',
                        (document['id'],))).fetchall()
                    assert profile_rows[0]['description']==SOURCE
                    assert profile_rows[0]['profile']['gist']=='Blue widget protocol'
                    assert profile_rows[0]['summary_status']=='completed' and profile_rows[0]['custom_metadata']=={}
                    summary_rows=await (await profile_guard.connection.execute(
                        'SELECT c.id,c.parent_chunk_id,e.source_type,e.is_enabled FROM chunks c JOIN embeddings e '
                        'ON e.chunk_id=c.id WHERE c.knowledge_id=%s AND c.chunk_type=%s',
                        (document['id'],'summary'))).fetchall()
                    assert len(summary_rows)==1 and summary_rows[0]['parent_chunk_id']
                    assert summary_rows[0]['source_type']==0 and summary_rows[0]['is_enabled']
                # Actual mixed text/scanned PDF and standalone-image native uploads.
                import io,fitz
                from PIL import Image,ImageDraw
                image_buffer=io.BytesIO();image=Image.new('RGB',(800,500),'white')
                ImageDraw.Draw(image).text((60,80),'Blue widgets use protocol W-17.',fill='black');image.save(image_buffer,format='PNG')
                image_bytes=image_buffer.getvalue()
                image_kb_reply=await client.post('/api/v1/knowledge-bases',json={'name':'synthetic-image-main'})
                image_kb=image_kb_reply.json()['data']['id']
                await runtime.update_knowledge_base_consent(principal,image_kb,True)
                async with leases.lease() as image_guard:
                    image_roles=dict((await runtime._snapshot(image_guard)).model_ids)
                configured=await client.patch(f'/api/v1/knowledge-bases/{image_kb}/image-processing',json={
                    'enable_multimodal':True,'ocr_adapter':'vlm','ocr_model_id':image_roles['vlm'],
                    'caption_mode':'explicit','caption_model_id':image_roles['vlm'],
                    'custom_instructions':'BUSINESS_ONLY_CAPTION'})
                assert configured.status_code==200,configured.text
                pdf=fitz.open();pdf.new_page(width=800,height=500).insert_text((60,80),'A source text page about protocol W-17.')
                page=pdf.new_page(width=800,height=500);page.insert_image(page.rect,stream=image_bytes)
                pdf_bytes=pdf.tobytes();pdf.close()
                image_documents=[]
                for filename,content,mime in [('fixture-scan.pdf',pdf_bytes,'application/pdf'),('fixture-image.png',image_bytes,'image/png')]:
                    uploaded=await client.post(f'/api/v1/knowledge-bases/{image_kb}/documents',files={'file':(filename,content,mime)})
                    assert uploaded.status_code==200,uploaded.text
                    image_document=uploaded.json()['data'];assert image_document['status']=='pending',image_document
                    # Background task is formal main entry; keep all subsequent witnesses.
                    deadline=asyncio.get_running_loop().time()+30
                    while image_document['status'] in ('pending','processing'):
                        rows=(await client.get(f'/api/v1/knowledge-bases/{image_kb}/documents')).json()['data']
                        image_document=next(row for row in rows if row['id']==image_document['id'])
                        if image_document['status'] in ('completed','failed','cancelled'):break
                        assert asyncio.get_running_loop().time()<deadline,'IMAGE_COMPLETION_DEADLINE'
                        await asyncio.sleep(.02)
                    assert image_document['status']=='completed',image_document
                    original_reply=await client.get(f"/api/v1/knowledge-bases/{image_kb}/documents/{image_document['id']}/source")
                    assert original_reply.status_code==200 and original_reply.content==content
                    image_documents.append(image_document)
                    async with leases.lease() as image_guard:
                        image_rows=await (await image_guard.connection.execute('SELECT c.id,c.chunk_type,c.content,c.parent_chunk_id,c.image_info,e.is_enabled FROM chunks c LEFT JOIN embeddings e ON e.chunk_id=c.id WHERE c.tenant_id=%s AND c.knowledge_id=%s ORDER BY c.chunk_type',
                            (principal.tenant_id,image_document['id']))).fetchall()
                        profile_input=await (await image_guard.connection.execute('SELECT summary_status,profile,metadata FROM knowledges WHERE tenant_id=%s AND id=%s',
                            (principal.tenant_id,image_document['id']))).fetchone()
                    multimodal=[r for r in image_rows if r['chunk_type'] in ('image_ocr','image_caption')]
                    assert {r['chunk_type'] for r in multimodal}=={'image_ocr','image_caption'}
                    assert all(r['parent_chunk_id'] and r['image_info'] and r['is_enabled'] for r in multimodal)
                    assert any('Extracted scan' in r['content'] for r in multimodal)
                    assert profile_input['summary_status']=='completed' and profile_input['profile']
                    image_document['fixture_image_rows']=image_rows
                    image_document['fixture_parser_metadata']=profile_input['metadata']['parser_metadata']
                scan=image_documents[0]['fixture_parser_metadata']
                assert scan['text_page_count']==1 and scan['scanned_page_count']==1
                vlm_prompts=[next(p['text'] for p in w['body']['messages'][0]['content'] if p['type']=='text') for w in wire if w['body'].get('model')=='Qwen/Qwen3-VL-32B-Instruct']
                assert any('scanned PDF document' in prompt for prompt in vlm_prompts)
                assert any('OCR assistant' in prompt and 'scanned PDF document' not in prompt for prompt in vlm_prompts)
                # Verify the finalizer JOIN refuses both foreign tenants and
                # wrong KBs, using disabled physical embeddings as witnesses.
                foreign_kb, foreign_doc, foreign_source = [str(uuid.uuid4()) for _ in range(3)]
                sibling_kb, sibling_doc, sibling_source = [str(uuid.uuid4()) for _ in range(3)]
                async with leases.lease() as guard:
                    foreign_tenant = (await (await guard.connection.execute(
                        "INSERT INTO tenants(name,business) VALUES(%s,%s) RETURNING id",
                        ("foreign isolated fixture", "isolated-test"))).fetchone())["id"]
                    for tenant, kid, did, eid in (
                            (foreign_tenant, foreign_kb, foreign_doc, foreign_source),
                            (principal.tenant_id, sibling_kb, sibling_doc, sibling_source)):
                        await guard.connection.execute(
                            "INSERT INTO knowledge_bases(id,tenant_id,name,embedding_model_id,summary_model_id,rerank_model_id) "
                            "VALUES(%s,%s,'scope witness','','','')", (kid, tenant))
                        await guard.connection.execute(
                            "INSERT INTO knowledges(id,tenant_id,knowledge_base_id,type,title,source,parse_status) "
                            "VALUES(%s,%s,%s,'text','scope witness','fixture','processing')", (did, tenant, kid))
                        await guard.connection.execute(
                            "INSERT INTO embeddings(source_id,source_type,knowledge_id,knowledge_base_id,dimension,is_enabled) "
                            "VALUES(%s,0,%s,%s,3,FALSE)", (eid, did, kid))
                from backend.weknora_port.documents import DocumentLifecycleError
                with pytest.raises(DocumentLifecycleError):
                    await documents._finalize(principal, foreign_kb, foreign_doc,
                                              "scope-fixture", {}, enabled=True)
                with pytest.raises(DocumentLifecycleError):
                    await documents._finalize(principal, kb, sibling_doc,
                                              "scope-fixture", {}, enabled=True)
                async with leases.lease() as guard:
                    untouched = await (await guard.connection.execute(
                        "SELECT source_id,is_enabled FROM embeddings WHERE source_id=ANY(%s) ORDER BY source_id",
                        ([foreign_source, sibling_source],))).fetchall()
                assert len(untouched) == 2 and all(not row["is_enabled"] for row in untouched)
                wrong_scope = await client.post("/api/v1/sessions",
                    json={"knowledge_base_scope": [foreign_kb], "document_scope": []})
                assert wrong_scope.status_code == 403, wrong_scope.text
                session = await client.post("/api/v1/sessions",
                    json={"title": "integration", "knowledge_base_scope": [kb], "document_scope": [document["id"]]})
                assert session.status_code == 200, session.text
                sid = session.json()["data"]["id"]
                request = {"content": "What protocol do blue widgets use?", "request_id": str(uuid.uuid4()),
                           "knowledge_base_scope": [kb], "document_scope": [document["id"]]}
                answer = await client.post(f"/api/v1/sessions/{sid}/messages", json=request)
                assert answer.status_code == 200, answer.text
                await runtime.drain_background()
                assert "CHEAP_REJECTED_DRAFT" not in answer.text
                assert "Blue widgets use W-17." in answer.text, answer.text
                frames = [json.loads(line[6:]) for line in answer.text.splitlines()
                          if line.startswith("data: ")]
                assert frames[-1]["response_type"] == "complete"
                assert sum(frame["response_type"] == "complete" for frame in frames) == 1
                messages = (await client.get(f"/api/v1/sessions/{sid}/messages")).json()["data"]
                assistant = next(row for row in messages if row["role"] == "assistant")
                assert assistant["is_completed"] and assistant["knowledge_references"]
                ref = assistant["knowledge_references"][0]
                readback = await client.get("/api/v1/chunks/" + ref["id"])
                assert readback.status_code == 200 and SOURCE in readback.text
                count = len(wire)
                replay = await client.post(f"/api/v1/sessions/{sid}/messages", json=request)
                assert replay.status_code == 200 and len(wire) == count
                restored = await client.get(f"/api/v1/sessions/continue-stream/{sid}",
                                              params={"message_id": assistant["id"]})
                assert restored.status_code == 200 and "Blue widgets use W-17." in restored.text
                assert len(wire) == count
                fail_cheap = True
                timeout_request = {**request, "request_id": str(uuid.uuid4())}
                failure = await client.post(f"/api/v1/sessions/{sid}/messages", json=timeout_request)
                assert failure.status_code == 200 and "MODEL_CALL_TIMEOUT" in failure.text
                await runtime.drain_background()
                async with leases.lease() as guard:
                    attempts = await (await guard.connection.execute(
                        "SELECT a.role,a.status,a.error_code,a.usage FROM generation_attempts a "
                        "JOIN generation_runs r ON r.run_id=a.run_id AND r.tenant_id=a.tenant_id "
                        "AND r.subject_id=a.subject_id WHERE a.tenant_id=%s AND a.subject_id=%s "
                        "AND r.session_id=%s ORDER BY a.started_at,a.ordinal",
                        (principal.tenant_id, principal.subject_id, sid))).fetchall()
                    ledger = await (await guard.connection.execute(
                        "SELECT capability,state,usage FROM send_calls WHERE account_id=%s ORDER BY created_at",
                        (costs.authority.account_id,))).fetchall()
                assert [row["role"] for row in attempts] == ["cheap", "expensive", "cheap"]
                assert attempts[-1]["error_code"] == "MODEL_CALL_TIMEOUT"
                assert ledger[-1]["state"] == "unknown" and ledger[-1]["usage"]["status"] == "UNKNOWN"
                # Native paired entry: history/memory/retrieval captured once,5stream calls across4modes.
                from eval_center.weknora_paired import execute_native_pair,MODES
                from backend.weknora_port.service import OrdinaryTurnRequest
                fail_cheap=False
                pair_session=(await client.post('/api/v1/sessions',json={'title':'native-paired-capture',
                    'knowledge_base_scope':[kb],'document_scope':[document['id']]})).json()['data']['id']
                pair_turns=[]
                for mode in MODES:
                    pair_turns.append(await runtime.turns.accept(OrdinaryTurnRequest(pair_session,'extract source',
                        str(uuid.uuid4()),knowledge_base_scope=(kb,),document_scope=(document['id'],)),principal))
                original_prepare=runtime.ordinary.prepare_turn;preparations=[]
                async def measured_prepare(*args,**kwargs):
                    value=await original_prepare(*args,**kwargs);preparations.append(value);return value
                runtime.ordinary.prepare_turn=measured_prepare
                before_pair=len(wire)
                paired=await execute_native_pair(runtime,pair_turns,execution_kind='ISOLATED_MOCK')
                runtime.ordinary.prepare_turn=original_prepare
                assert len(preparations)==1 and len(paired.outcomes)==4
                assert [o['mode'] for o in paired.outcomes]==list(MODES)
                assert all(o['status']=='COMPLETED' for o in paired.outcomes),paired.outcomes
                assert [o['result'].selected_role for o in paired.outcomes]==['baseline','cheap','expensive','expensive']
                assert [o['result'].upgraded for o in paired.outcomes]==[False,False,False,True]
                assert all(o['result'].context_sha256==paired.context_sha256 and o['result'].prompt_sha256==paired.prompt_sha256 for o in paired.outcomes)
                paired_streams=[w for w in wire[before_pair:] if w['body'].get('stream')]
                assert len(paired_streams)==5 and all(w['body']['messages']==paired_streams[0]['body']['messages'] for w in paired_streams)
                assert len({o['run_id'] for o in paired.outcomes})==4 and not runtime._runs
                assert paired.auxiliary_calls and sum(len(o['model_calls']) for o in paired.outcomes)==5
                assert paired.model_quality_status=='NOT_RUN_SIMULATED_MODELS'
                pair_messages=(await client.get(f'/api/v1/sessions/{pair_session}/messages')).json()['data']
                assert sum(m['role']=='assistant' and m['is_completed'] for m in pair_messages)==4
                pair_evidence={'capture_sha256':paired.capture_sha256,'prompt_sha256':paired.prompt_sha256,'context_sha256':paired.context_sha256,
                    'preparations':len(preparations),'outcomes':[{'mode':o['mode'],'run_id':o['run_id'],'role':o['result'].selected_role,
                        'upgraded':o['result'].upgraded,'generation_seconds':o['generation_seconds'],'model_calls':o['model_calls']} for o in paired.outcomes],
                    'auxiliary_calls':paired.auxiliary_calls,'preparation_seconds':paired.preparation_seconds,
                    'model_quality':paired.model_quality_status,'real_provider_calls':0,'persisted_assistants':4,'same_messages_all5streams':True}
                # Enable source automatic memory only for this new public fixture tenant.
                async with leases.lease() as memory_guard:
                    roles_ids=dict((await runtime._snapshot(memory_guard)).model_ids)
                    await memory_guard.connection.execute('UPDATE tenants SET memory_config=%s::jsonb WHERE id=%s',
                        (json.dumps({'enabled':True,'write_mode':'auto','extract_delay_seconds':5,
                         'extract_min_interval_seconds':1,'embedding_model_id':roles_ids['embedding'],
                         'extract_model_id':roles_ids['cheap']}),principal.tenant_id))
                fail_cheap=False
                memory_request={**request,'request_id':str(uuid.uuid4()),'content':'I use blue widgets. extract source'}
                memory_turn=await client.post(f'/api/v1/sessions/{sid}/messages',json=memory_request)
                assert memory_turn.status_code==200 and 'MODEL_CALL_TIMEOUT' not in memory_turn.text
                await runtime.drain_background()
                assert not runtime.memory_hook_errors
                from backend.weknora_port.conversation.types import MemoryContext
                memory_context=MemoryContext(principal.tenant_id,principal.subject_id)
                recall=await runtime.memory.recall(memory_context,'blue widgets')
                print('PUBLIC_SYNTHETIC_MEMORY_DIAGNOSTIC', {'dead':await redis.lrange(runtime.memory_enqueuer.keys[3],0,-1),
                    'memory_wire_calls':sum('You maintain a small set of long-term notes' in json.dumps(w.get('body')) for w in wire)})
                assert 'User uses blue widgets.' in recall.prompt
                async with leases.lease() as memory_guard:
                    saved_memories=await (await memory_guard.connection.execute(
                        'SELECT id,source_session_id,source_message_id FROM memory_items WHERE tenant_id=%s '
                        'AND subject_id=%s AND status=%s',(principal.tenant_id,principal.subject_id,'active'))).fetchall()
                assert len(saved_memories)==1 and saved_memories[0]['source_session_id']==sid
                listing=await client.get('/api/v1/memory/items',params={'status':'active'})
                assert listing.status_code==200 and listing.json()['total']==1
                assert listing.json()['data'][0]['id']==saved_memories[0]['id']
                settings_reply=await client.get('/api/v1/memory/settings')
                assert settings_reply.status_code==200 and settings_reply.json()['data']['effective'] is True
                # Ordinary public-KB turn must also gate whole-subject memory origins.
                private_reply=await client.post('/api/v1/knowledge-bases',json={'name':'synthetic-private-memory-origin'})
                assert private_reply.status_code==200
                private_kb=private_reply.json()['data']['id']
                private_session=await client.post('/api/v1/sessions',json={'title':'synthetic-memory-origin',
                    'knowledge_base_scope':[private_kb],'document_scope':[]})
                assert private_session.status_code==200
                before_memory_origin=len(wire)
                origin_request={**request,'request_id':str(uuid.uuid4()),'content':'What about my blue widgets? extract source'}
                origin_reply=await client.post(f'/api/v1/sessions/{sid}/messages',json=origin_request)
                await runtime.drain_background()
                assert origin_reply.status_code==200 and '"error_code":"ORDINARY_CHAIN_FAILED"' in origin_reply.text,origin_reply.text
                assert len(wire)==before_memory_origin
                denied_calls=[call for call in permissions.calls if private_kb in call.context.required_origin_kbs]
                assert denied_calls and all(call.context.scope.knowledge_base_ids==(kb,) for call in denied_calls)
                denied_authorization=await permissions.resolve(denied_calls[-1])
                assert private_kb in denied_authorization.readable_origin_kbs and private_kb not in denied_authorization.cloud_allowed_origin_kbs
                from backend.weknora_port.storage.send_gate import P1SendGate
                with pytest.raises(ProviderError,match='MODEL_ORIGIN_KB_EGRESS_DENIED'):
                    P1SendGate.__new__(P1SendGate)._permission(denied_calls[-1],denied_authorization)
                memory_origin_result={'private_origin':private_kb,'retrieval_kb':kb,'wire_before':before_memory_origin,
                    'wire_after':len(wire),'mandatory_origins':denied_calls[-1].context.required_origin_kbs,
                    'gate_error':'MODEL_ORIGIN_KB_EGRESS_DENIED','http_error':'ORDINARY_CHAIN_FAILED',
                    'retrieval_scope_unchanged':True}
                # Derived memory retains KB external-processing consent and tenant owner.
                from backend.weknora_port.conversation.maintenance_types import ModelRequest
                from backend.weknora_port.conversation.types import MemoryScope
                scope=MemoryScope(principal.tenant_id,principal.subject_id)
                await runtime.update_knowledge_base_consent(principal,kb,False)
                before_denied=len(wire)
                with pytest.raises(ProviderError,match='MODEL_ORIGIN_KB_EGRESS_DENIED'):
                    await runtime.memory_models.complete(ModelRequest(scope,roles_ids['cheap'],'public system','public memory','{}',100))
                assert len(wire)==before_denied
                with pytest.raises(ProviderError,match='MEMORY_OWNER_MISMATCH'):
                    await runtime.memory_models.complete(ModelRequest(MemoryScope(principal.tenant_id,'other-subject'),roles_ids['cheap'],'system','memory','{}',100))
                assert len(wire)==before_denied
                await runtime.update_knowledge_base_consent(principal,kb,True)
                denied_delete=await client.delete('/api/v1/memory/items/'+str(uuid.uuid4()))
                assert denied_delete.status_code==404
                removed=await client.delete('/api/v1/memory/items/'+saved_memories[0]['id'])
                assert removed.status_code==200
                await runtime.memory.drain_background()
                after_forget=await runtime.memory.recall(memory_context,'blue widgets')
                assert 'User uses blue widgets.' not in after_forget.prompt
                async with leases.lease() as memory_guard:
                    residual=await (await memory_guard.connection.execute('SELECT count(*) AS n FROM memory_item_embeddings '
                        'WHERE tenant_id=%s AND subject_id=%s AND item_id=%s',
                        (principal.tenant_id,principal.subject_id,saved_memories[0]['id']))).fetchall()
                assert residual[0]['n']==0
                evidence = {"kind": "ACTUAL_LOOPBACK_HTTP_PG_REDIS_MOCK_PROVIDER",
                    "system_identifier": EXPECTED, "redis_run_id": REDIS_RUN_ID, "redis_prefix": prefix,
                    "knowledge_base": kb, "document": document["id"], "session": sid,
                    "assistant": assistant["id"], "client_request_id": request["request_id"],
                    "server_run_id": assistant["request_id"], "attempts": attempts, "ledger": ledger,
                    "mock_wire_count": len(wire), "real_provider_calls": 0,
                    "assertions": "PASS", "cross_tenant_and_wrong_kb_index_activation": untouched,
                    "foreign_kb_http": wrong_scope.status_code, "profile_postprocessor": "SOURCE_TEXT_ADAPTER_WITH_MOCK_PROVIDER_GO_ORACLE_NOT_RUN",
                    "p7_native_paired":pair_evidence,
                    "p2_native_image_documents":image_documents,
                    "ordinary_memory_origin_gate":memory_origin_result,
                    "automatic_memory": {"queued_actual_redis": True,"actual_pg_saved": saved_memories,
                        "recalled_before_forget": True,"absent_after_forget": True,"embedding_residual": residual[0]["n"],"private_origin_denies_wire":True,"foreign_subject_denies_wire":True,"delete_http":removed.status_code},
                    "retained": "synthetic source + UNKNOWN fixture; old DB untouched"}
                output = Path("docs/weknora-port/evidence/P7_NATIVE_PAIRED_CAUSAL_CHAIN_20261014.json")
                output.write_text(json.dumps(evidence, default=str, indent=2), encoding="utf-8")
        finally:
            server.should_exit = True
            await task
            await redis.aclose()

    actual_pg_run(scenario())

