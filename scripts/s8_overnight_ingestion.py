"""Finite source-only S8 ingestion; normal lifecycle/Registry/Gate, no QA.

prepare only reads frozen public sources and local parser receipts. run consumes
root-created execution files, never signs authorization or retries an uncertain
document. The original six-pack candidate pool is not selected from Gold.
"""
from __future__ import annotations
import argparse,asyncio,copy,hashlib,json,os,re,sys,time,uuid,zipfile
from pathlib import Path
from contextlib import contextmanager
from dataclasses import replace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.weknora_port.ingestion.core import DocumentRequest,ParsedDocument,ParsedAsset,ParserRegistry,ingest_document
from backend.weknora_port.storage.chunks import DocumentScope,prepare_chunks
from backend.weknora_port.storage.indexing import sanitize_for_embedding,IndexBoundaryDenied

KBS={'scifact':'f4b6ddb2-c05c-4ece-ba73-c1dad6eea8d7','miracl-zh':'214b8b2f-84f9-4d75-ab79-eccbdcd50ec2',
    'longbench-zh':'76770fba-17c3-4704-8663-10aa669476ce','hotpotqa':'96a9eebd-ffb2-4bc6-9bea-3ed015845479',
    'omnidocbench':'b66d8ae4-8cfe-4c9b-86d8-c28cb66ca3ef'}
MODEL_ID='mdlebb9931ff609b45a70a6c29c6712caa66c6b657ce4d1f802ee114bed2e779'
BATCH_SIZE=16
def sha(raw):return hashlib.sha256(raw).hexdigest()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');temp.replace(path)
def require(ok,code):
    if not ok:raise ValueError(code)

def source_key(row):return sha(canonical([row['dataset'],row['knowledge_base_id'],row['source_sha256']]).encode())

@contextmanager
def single_instance(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0,2)
        if not stream.tell():stream.write(b'0');stream.flush()
        stream.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


class BoundedEmbeddingCatalogue:
    """Existing Registry settings seam; model/Gate/credential identity retained."""
    def __init__(self,base):self.base=base
    def resolve(self,role,model_id):
        config=self.base.resolve(role,model_id)
        if role.role=='embedding':
            require(model_id==MODEL_ID and config.provider=='siliconflow' and config.model=='BAAI/bge-m3'
                and config.dimensions==1024,'INGEST_EMBEDDING_IDENTITY_MISMATCH')
            return replace(config,embedding=replace(config.embedding,max_batch_size=BATCH_SIZE))
        return config
    def validate(self,role):return self.base.validate(role)


class NoRetryEmbedder:
    def __init__(self,base,on_operation=lambda *_:None):self.base,self.on_operation=base,on_operation
    def for_operation(self,operation_id,document_id):
        self.on_operation(operation_id,document_id)
        return NoRetryEmbedder(self.base.for_operation(operation_id,document_id),self.on_operation)
    async def batch_embed_with_pool(self,texts,*,attempt):
        require(attempt==0,'INGEST_EMBEDDING_RETRY_FORBIDDEN')
        try:return await self.base.batch_embed_with_pool(texts,attempt=attempt)
        except Exception as error:
            # The fixed native indexer retries generic errors five times. This
            # authorized finite job must never resend an uncertain operation.
            raise IndexBoundaryDenied('INGEST_SEND_FAILED_NO_RETRY:'+type(error).__name__) from None


class SavedMinerUReceiptParser:
    """Rebind a real saved OCR projection, never synthesize a missing SDK file."""
    def __init__(self,rows):self.rows={r['image_sha256']:r for r in rows}
    def __call__(self,request):
        digest=sha(request.data);old=self.rows.get(digest)
        require(old is not None and old['status']=='COMPLETE' and old['parser_engine']=='mineru-basic'
            and old['parser_version']=='4.0.12' and old.get('gold_was_sent_to_parser') is False,'MINERU_CACHE_IDENTITY_INVALID')
        text=old['raw_ocr_text']
        require(sha(text.encode())==old['derived_text_sha256']==old['mineru_source']['derived_text_sha256'],
                'MINERU_CACHE_BODY_HASH_MISMATCH')
        # Exact saved projection is the sidecar; old middle/crops were hashes
        # only and are explicitly unavailable, not reproduced from guessed text.
        receipt=canonical(old).encode();ref={'logical_path':'mineru/saved_receipt_projection.json','sha256':sha(receipt)}
        identity=copy.deepcopy(old['mineru_source'])
        identity.update(document_id=request.document_id,source_version=request.source_version,middle=ref,
            reuse_kind='historical_mineru_receipt_projection',historical_middle=old['mineru_source']['middle'],
            historical_document_id=old['document_id'],historical_source_version=old['source_version'],
            raw_sdk_sidecars_available=False)
        locators=copy.deepcopy(old['source_locators'])
        for locator in locators:
            a,b=locator['start'],locator['end']
            require(type(a) is int and type(b) is int and 0<=a<=b<=len(text)
                and locator['quote']==text[a:b],'MINERU_CACHE_OFFSET_INVALID')
            proof=locator['raw_evidence'];require(proof['source_sha256']==digest,'MINERU_CACHE_SOURCE_MISMATCH')
            proof['middle']=ref
            # Crop bytes are absent. Preserve their historical digest in the
            # immutable projection while this locator points back to original.
            if 'asset' in proof:proof['historical_asset_unavailable']=proof.pop('asset')
            if 'image_ocr' in proof:proof['historical_image_ocr']=proof.pop('image_ocr')
        assets=(ParsedAsset(ref['logical_path'],receipt,'application/json','mineru_evidence',
                    metadata={'processing_role':'parser_evidence','source_sha256':digest,'sha256':ref['sha256']}),
            ParsedAsset('mineru/original'+Path(request.filename).suffix.lower(),request.data,
                    old.get('media_type','image/png'),'original_image',1,
                    {'processing_role':'parser_evidence','source_sha256':digest,'sha256':digest}))
        return ParsedDocument(text,assets,{'filename':request.filename,'parser_engine':'mineru-basic',
            'status':'parsed','asset_processing':'parser_complete','mineru_source':identity,
            'source_locators':locators,'mineru_blocks':copy.deepcopy(old['mineru_blocks']),
            'cache_limitations':['Original SDK middle/archive/crop bytes not retained; original image and exact saved OCR projection retained.']})


def package_sources(package_root,snapshot,mineru_receipts):
    root=Path(package_root);index=read(root/'six-package-manifest.json')
    identities=snapshot['identities'][0];require(identities['tenant_id']==332,'SOURCE_TENANT_MISMATCH')
    valid_kbs=set(KBS.values())|{'fd444069-cc1a-42d0-9617-e95fa90b6941','7f1d720d-9561-446f-a8cc-6a562c339d23'}
    docs=[d for d in identities['documents'] if d['knowledge_base_id'] in valid_kbs
        and d['parse_status']=='completed' and d['enable_status']=='enabled']
    existing={d['file_hash']:d for d in docs};cache=SavedMinerUReceiptParser(mineru_receipts)
    registry=ParserRegistry();registry.register('mineru-basic',('.png','.jpg','.jpeg'),cache)
    output=[]
    for package in index['packages']:
        dataset=package['dataset'];archive=root/package['archive']['path'];manifest_path=root/package['manifest']['path']
        require(sha(archive.read_bytes())==package['archive']['sha256'] and sha(manifest_path.read_bytes())==package['manifest']['sha256'],
                'FROZEN_PACKAGE_HASH_MISMATCH')
        manifest=read(manifest_path)
        with zipfile.ZipFile(archive) as zipped:
            for unit in manifest['units']:
                source=unit['source'];artifact=source['artifact'];raw=zipped.read(artifact['path'])
                require(len(raw)==artifact['bytes'] and sha(raw)==artifact['sha256'],'FROZEN_SOURCE_HASH_MISMATCH')
                prior=existing.get(artifact['sha256']);kb=prior['knowledge_base_id'] if prior else KBS.get(dataset)
                require(bool(kb),'MML_MISSING_SOURCE_REQUIRES_SEPARATE_NATIVE_PDF_PLAN')
                row={'dataset':dataset,'unit_id':unit['unit_id'],'source_document_id':source['source_document_id'],
                    'filename':source['file_name'],'media_type':source['media_type'],'source_sha256':artifact['sha256'],
                    'archive':package['archive']['path'],'archive_sha256':package['archive']['sha256'],
                    'member':artifact['path'],'source_bytes':len(raw),'knowledge_base_id':kb,
                    'status':'REUSE_MIGRATED' if prior and dataset!='omnidocbench' else 'NEW_SOURCE',
                    'existing_document':prior,'chunking_config':{},'parser_engine':'mineru-basic' if dataset=='omnidocbench' else '',
                    'embedding_model_id':MODEL_ID}
                if row['status']=='NEW_SOURCE':
                    request=DocumentRequest(row['filename'],raw,'offline-owner',kb,'offline-document','offline-version',
                        parser_engine=row['parser_engine'])
                    ingest=ingest_document(request,registry)
                    require(ingest.parsed.ok and bool(ingest.parsed.content.strip()),'OFFLINE_SOURCE_PARSE_FAILED')
                    plan=prepare_chunks(DocumentScope(332,'offline-owner',kb,'offline-document','offline-version'),
                        ingest.chunks,title=Path(row['filename']).stem)
                    texts=[sanitize_for_embedding(r.content) for r in plan.indexed]
                    require(bool(texts),'NO_VALID_SOURCE_CHUNKS')
                    # A bounded normal Adapter config changes only batching,
                    # never source text/model/dimension. UTF-8 JSON bytes are a
                    # conservative token bound; each send adds 4096 overhead.
                    bodies=[{'model':'BAAI/bge-m3','input':texts[i:i+BATCH_SIZE]}
                        for i in range(0,len(texts),BATCH_SIZE)]
                    row.update(parsed_sha256=sha(ingest.parsed.content.encode()),child_count=len(texts),
                        embedding_input_chars=sum(map(len,texts)),embedding_calls_upper=len(bodies),
                        token_reservation_upper=sum(len(json.dumps(body,ensure_ascii=False).encode())+4096 for body in bodies))
                output.append(row)
    return output


def prepare(args):
    snapshot=read(args.snapshot);receipt_raw=Path(args.mineru_receipts).read_bytes()
    receipts=[json.loads(line) for line in receipt_raw.decode('utf-8-sig').splitlines() if line.strip()]
    rows=package_sources(args.package_root,snapshot,receipts)
    groups={}
    for dataset in sorted({r['dataset'] for r in rows}):
        group=[r for r in rows if r['dataset']==dataset];new=[r for r in group if r['status']=='NEW_SOURCE']
        groups[dataset]={'candidate_sources':len(group),'reused_sources':len(group)-len(new),'new_sources':len(new),
            'new_child_chunks':sum(r['child_count'] for r in new),'provider_calls_upper':sum(r['embedding_calls_upper'] for r in new),
            'reservation_tokens_upper':sum(r['token_reservation_upper'] for r in new)}
    plan={'schema':'s8-overnight-ingestion/v1','status':'OFFLINE_PREPARED_NOT_AUTHORIZED',
        'tenant_id':332,'subject_sha256':snapshot['identities'][0]['subject_sha256'],
        'source_snapshot_sha256':sha(Path(args.snapshot).read_bytes()),'mineru_receipts_sha256':sha(receipt_raw),
        'embedding':{'provider':'siliconflow','model':'BAAI/bge-m3','model_id':MODEL_ID,'dimension':1024,
            'max_batch_size':BATCH_SIZE,'max_input_chars':0},
        'settings':{'summary_model_id':None,'caption':'disabled','graph':False,'wiki':False,'question_generation':False,
            'note':'Explicit isolated target KB configuration, never Windows/global defaults. no_summary_model is a real terminal failed summary status, not a successful Summary.'},
        'datasets':groups,'sources':rows,'gold_sent_to_parser':False,'new_provider_calls':0}
    write(args.output,plan)
    print(json.dumps({'datasets':groups,'new_provider_calls':0,'plan':str(args.output)},ensure_ascii=False))


def parent_state(path,plan):
    from backend.weknora_port.execution import _validate_ledger
    parent=read(path)
    require(parent.get('schema')=='s8-overnight-owner-authorization/v1' and parent.get('status')=='OPEN'
        and parent.get('owner_goal_sha256') and parent.get('plan_sha256')==sha(canonical(plan).encode()),
        'NEW_INGESTION_OWNER_AUTHORIZATION_REQUIRED')
    require(parent.get('tenant_id')==332 and parent.get('subject_sha256')==plan['subject_sha256'],
        'INGESTION_PARENT_PRINCIPAL_MISMATCH')
    executions=parent.get('executions',{});require(isinstance(executions,dict) and executions,'ROOT_EXECUTION_FILES_REQUIRED')
    require(set(executions)=={source_key(r) for r in plan['sources'] if r['status']=='NEW_SOURCE'},
        'ROOT_EXACT_FROZEN_SOURCE_SET_REQUIRED')
    paths=list(executions.values());require(len(paths)==len(set(paths)),'DUPLICATE_EXECUTION_FILE')
    calls=tokens=0
    for filename in paths:
        data=read(filename);_validate_ledger(data)
        require(data['status']!='STOP' and all(r['status']=='SETTLED' for r in data['entries']),
            'INGESTION_UNKNOWN_OR_INFLIGHT_STOP')
        calls+=len(data['entries']);tokens+=sum(r['held_tokens'] for r in data['entries'])
    require(calls<=parent['max_calls'] and tokens<=parent['max_tokens'],'INGESTION_PARENT_LIMIT_DENIED')
    return parent,calls,tokens


def verify_execution(parent,plan,row,principal,calls,tokens):
    filename=parent['executions'].get(source_key(row));require(bool(filename),'SOURCE_NOT_AUTHORIZED')
    data=read(filename)
    model={'provider':'siliconflow','model':'BAAI/bge-m3','model_id':MODEL_ID,'capability':'embedding'}
    require(data['status']=='OPEN' and data['tenant_id']==332 and data['subject_id']==principal.subject_id
        and data['allowed_kbs']==[row['knowledge_base_id']] and data['allowed_models']==[model]
        and '*' in data['allowed_request_prefixes'],'INGESTION_EXECUTION_SCOPE_DENIED')
    require(not data['entries'],'INGESTION_OPERATION_ALREADY_SENT_NO_RETRY')
    require(row['embedding_calls_upper']<=data['max_calls']<=12
        and row['token_reservation_upper']<=data['max_tokens']<=100000,'INGESTION_FILE_CAPACITY_DENIED')
    require(calls+data['max_calls']<=parent['max_calls'] and tokens+data['max_tokens']<=parent['max_tokens'],
        'INGESTION_PARENT_REMAINING_LIMIT_DENIED')
    return Path(filename)


async def existing_documents(runtime,row):
    return await runtime.documents._rows('SELECT id,parse_status,enable_status,metadata,summary_status FROM knowledges '
        'WHERE tenant_id=%s AND knowledge_base_id=%s AND file_hash=%s AND deleted_at IS NULL ORDER BY created_at,id',
        (332,row['knowledge_base_id'],row['source_sha256']))


def is_current_projection(document,row):
    if document['parse_status']!='completed' or document['enable_status']!='enabled':return False
    if row['dataset']!='omnidocbench':return True
    metadata=document.get('metadata') or {};metadata=read_json_object(metadata)
    parser=metadata.get('parser_metadata',{});identity=parser.get('mineru_source',{})
    return (identity.get('reuse_kind')=='historical_mineru_receipt_projection'
        and identity.get('derived_text_sha256')==row['parsed_sha256'])


def read_json_object(value):return json.loads(value) if isinstance(value,str) else value


async def verify_document(runtime,row,document):
    original=await runtime.read_document(runtime.principal,row['knowledge_base_id'],document['id'])
    require(sha(original.content)==row['source_sha256'],'TARGET_ORIGINAL_HASH_MISMATCH')
    chunks=await runtime.documents._rows('SELECT c.id,c.chunk_type,c.metadata,e.dimension,e.embedding IS NOT NULL AS nonnull '
        'FROM chunks c LEFT JOIN embeddings e ON e.chunk_id=c.id AND e.knowledge_id=c.knowledge_id '
        'AND e.knowledge_base_id=c.knowledge_base_id AND e.is_enabled=true '
        "WHERE c.tenant_id=%s AND c.knowledge_base_id=%s AND c.knowledge_id=%s AND c.deleted_at IS NULL AND c.is_enabled=true AND c.chunk_type='text' ORDER BY c.chunk_index,c.id",
        (332,row['knowledge_base_id'],document['id']))
    require(bool(chunks) and all(c['dimension']==1024 and c['nonnull'] for c in chunks),'TARGET_VECTOR_INDEX_NOT_READY')
    first=await runtime.read_source_chunk(runtime.principal,chunks[0]['id'])
    metadata=read_json_object(document.get('metadata') or {})
    return {'document_id':document['id'],'source_version':metadata.get('source_version'),
        'original_sha256':row['source_sha256'],'child_count':len(chunks),'embedding_count':len(chunks),
        'dimension':1024,'first_source_chunk_id':chunks[0]['id'],'first_source_readable':bool(first),
        'summary_status':document.get('summary_status'),'ocr_kind':'SAVED_MINERU_RECEIPT_PROJECTION' if row['dataset']=='omnidocbench' else 'NATIVE_TEXT'}


async def run_ingestion(args):
    from backend.weknora_port.development import DevelopmentSettings,assemble
    from backend.weknora_port.storage.local_principal import LocalPrincipal
    from backend.weknora_port.execution import LocalExecution
    plan=read(args.plan);require(plan['schema']=='s8-overnight-ingestion/v1','INGESTION_PLAN_INVALID')
    require(sha(Path(args.mineru_receipts).read_bytes())==plan['mineru_receipts_sha256'],'MINERU_RECEIPT_FILE_CHANGED')
    principal_state=read(args.principal_state);principal=LocalPrincipal(332,principal_state['subject'])
    require(sha(principal.subject_id.encode())==plan['subject_sha256'],'INGESTION_SUBJECT_MISMATCH')
    settings=DevelopmentSettings.from_environment()
    require(settings.cloud_allowed is True and not settings.graph_enabled and not settings.rerank_top1_floorless,
        'INGESTION_RUNTIME_SCOPE_DENIED')
    parent,calls,tokens=parent_state(args.authorization,plan)
    require(settings.system_id==parent['resource_identity']['postgres_system_id']
        and settings.database==parent['resource_identity']['database']
        and str(settings.storage.resolve())==parent['storage_root'],'INGESTION_TARGET_IDENTITY_MISMATCH')
    checkpoint_path=args.output/'checkpoint.json';plan_sha=sha(canonical(plan).encode())
    checkpoint=read(checkpoint_path) if checkpoint_path.exists() else {
        'schema':'s8-overnight-ingestion-checkpoint/v1','run_id':parent['run_id'],'plan_sha256':plan_sha,'documents':{}}
    require(checkpoint['plan_sha256']==plan_sha and checkpoint['run_id']==parent['run_id'],'INGESTION_CHECKPOINT_MISMATCH')
    rows=sorted(plan['sources'],key=lambda r:({'hotpotqa':0,'omnidocbench':1}.get(r['dataset'],2),r['dataset'],r['unit_id']))
    with single_instance(args.output/'runner.lock'):
        async with assemble(settings,principal=principal) as runtime:
            runtime.catalogue=BoundedEmbeddingCatalogue(runtime.catalogue)
            receipts=[json.loads(x) for x in Path(args.mineru_receipts).read_text(encoding='utf-8-sig').splitlines() if x.strip()]
            runtime.documents.parser_registry.register('mineru-basic',('.png','.jpg','.jpeg'),SavedMinerUReceiptParser(receipts))
            original_embedder=runtime.documents.embedder_for_kb
            active={'record':None}
            def on_operation(operation,document_id):
                active['record'].update(document_id=document_id,source_version=operation.split('/')[0],state='EMBEDDING_ENTERED')
                write(checkpoint_path,checkpoint)
            async def resolve_embedder(who,kb):
                value=original_embedder(who,kb)
                if hasattr(value,'__await__'):value=await value
                return NoRetryEmbedder(value,on_operation)
            runtime.documents.embedder_for_kb=resolve_embedder
            for row in rows:
                key=source_key(row);record=checkpoint['documents'].get(key)
                if record and record['state'] in ('INDEXED','REUSED_INDEXED'):continue
                parent,calls,tokens=parent_state(args.authorization,plan)
                candidates=await existing_documents(runtime,row)
                complete=next((d for d in candidates if is_current_projection(d,row)),None)
                if complete:
                    proof=await verify_document(runtime,row,complete)
                    checkpoint['documents'][key]={'state':'REUSED_INDEXED','dataset':row['dataset'],'unit_id':row['unit_id'],**proof}
                    write(checkpoint_path,checkpoint);continue
                if row['status']=='REUSE_MIGRATED':
                    checkpoint['documents'][key]={'state':'BLOCKED_MIGRATION_MISSING','dataset':row['dataset'],'unit_id':row['unit_id']}
                    write(checkpoint_path,checkpoint);continue
                if record or candidates and row['dataset']!='omnidocbench':
                    checkpoint['documents'][key]={'state':'BLOCKED_EXISTING_UNCERTAIN_NO_RETRY','dataset':row['dataset'],
                        'unit_id':row['unit_id'],'original_record':record,'existing_document_ids':[d['id'] for d in candidates]}
                    write(checkpoint_path,checkpoint);continue
                kb=await runtime.documents._kb(principal,row['knowledge_base_id'])
                strategy=read_json_object(kb.get('indexing_strategy') or {})
                image=read_json_object(kb.get('image_processing_config') or {})
                require(kb.get('summary_model_id') in ('',None) and not image.get('enable_multimodal')
                    and not strategy.get('graph_enabled') and not strategy.get('wiki_enabled')
                    and not (read_json_object(kb.get('question_generation_config') or {})).get('enabled')
                    and kb.get('embedding_model_id')==MODEL_ID and kb.get('cloud_allowed') is True
                    and strategy.get('vector_enabled') is True and strategy.get('keyword_enabled') is True
                    and read_json_object(kb.get('chunking_config') or {})==row['chunking_config'],
                    'ISOLATED_SOURCE_ONLY_KB_CONFIGURATION_REQUIRED')
                execution=verify_execution(parent,plan,row,principal,calls,tokens)
                runtime.execution=LocalExecution(execution)
                record={'state':'CLAIMED','dataset':row['dataset'],'unit_id':row['unit_id'],
                    'source_sha256':row['source_sha256'],'knowledge_base_id':row['knowledge_base_id'],'execution_file':str(execution)}
                checkpoint['documents'][key]=record;active['record']=record;write(checkpoint_path,checkpoint)
                archive=Path(args.package_root)/row['archive']
                require(sha(archive.read_bytes())==row['archive_sha256'],'SOURCE_ARCHIVE_CHANGED')
                with zipfile.ZipFile(archive) as zipped:raw=zipped.read(row['member'])
                require(sha(raw)==row['source_sha256'],'SOURCE_BYTES_CHANGED')
                started=time.monotonic()
                result=await runtime.upload_document(principal,row['knowledge_base_id'],row['filename'],row['media_type'],raw,
                    {'parser_engine':row['parser_engine']} if row['parser_engine'] else None)
                record.update(document_id=result['id'],processing_status=result['status'],error_code=result.get('error_code',''),
                    elapsed_seconds=time.monotonic()-started)
                data=read(execution)
                if any(e['status']!='SETTLED' for e in data['entries']):
                    record['state']='UNKNOWN_OR_UNSETTLED_STOP';write(checkpoint_path,checkpoint)
                    parent.update(status='STOP',stop_reason='NEW_UNKNOWN_OR_UNSETTLED',stopped_source_key=key)
                    write(args.authorization,parent)
                    raise ValueError('INGESTION_UNKNOWN_OR_UNSETTLED_STOP')
                if result['status']=='completed':
                    current=next(d for d in await existing_documents(runtime,row) if d['id']==result['id'])
                    record.update(await verify_document(runtime,row,current));record['state']='INDEXED'
                else:record['state']='FAILED_NO_AUTOMATIC_RETRY'
                record.update(provider_calls=len(data['entries']),reported_tokens=sum(e['usage']['total_tokens'] for e in data['entries']))
                with runtime.execution._ledger() as settled:settled['status']='CLOSED'
                write(checkpoint_path,checkpoint)
                print(json.dumps({'run_id':parent['run_id'],'dataset':row['dataset'],'unit_id':row['unit_id'],
                    'state':record['state'],'provider_calls':record['provider_calls'],'reported_tokens':record['reported_tokens']},ensure_ascii=False),flush=True)
            parent,calls,tokens=parent_state(args.authorization,plan)
            checkpoint.update(state='INDEX_READY' if len(checkpoint['documents'])==len(rows) and
                all(r['state'] in ('INDEXED','REUSED_INDEXED') for r in checkpoint['documents'].values()) else 'PARTIAL',
                provider_calls=calls,reported_tokens=tokens)
            write(checkpoint_path,checkpoint);write(args.output/'ingestion-results.json',checkpoint)
            for filename in parent['executions'].values():
                with LocalExecution(filename)._ledger() as settled:settled['status']='CLOSED'
            parent.update(status='CLOSED',actual_calls=calls,actual_reported_tokens=tokens,
                unused_calls_withdrawn=parent['max_calls']-calls,unused_token_space_withdrawn=parent['max_tokens']-tokens)
            write(args.authorization,parent)
            return checkpoint


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('prepare')
    for name in ('package-root','snapshot','mineru-receipts','output'):p.add_argument('--'+name,type=Path,required=True)
    p=sub.add_parser('run')
    for name in ('plan','package-root','mineru-receipts','principal-state','authorization','output'):
        p.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    if args.command=='prepare':prepare(args)
    else:
        try:asyncio.run(run_ingestion(args))
        except Exception as error:
            args.output.mkdir(parents=True,exist_ok=True)
            write(args.output/'runner-stopped.json',{'state':'STOP','error_type':type(error).__name__,
                'code':str(error) if re.fullmatch('[A-Z][A-Z0-9_]{0,100}',str(error)) else 'SEE_DURABLE_CHECKPOINT_NO_RESEND',
                'checkpoint_preserved':True})
            raise SystemExit(1) from None


if __name__=='__main__':main()
