"""Native six-dataset ingestion/pairing/scoring; Gold never enters runtime calls.

The caller owns the scoped ApplicationRuntime, send gate and transport authority.
This module neither loads credentials nor authorizes hosted model calls.
"""
from __future__ import annotations
import asyncio,hashlib,inspect,time,uuid
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from backend.weknora_port.service import OrdinaryTurnRequest
from .weknora_source_packages import checked_bytes
from .weknora_paired import execute_native_pair,MODES
from .weknora_scoring import project_native_documents,score_native_qa,score_native_omni


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


class NativeSixRunner:
    def __init__(self,application,*,execution_kind,prepare_kb,on_record=None,ingest_workers=1):
        if execution_kind not in ('ISOLATED_MOCK','REAL_PROVIDER'):
            raise ValueError('NATIVE_EXECUTION_KIND_REQUIRED')
        if type(ingest_workers) is not int or not 1<=ingest_workers<=8:
            raise ValueError('NATIVE_INGESTION_CONCURRENCY_OUT_OF_BOUND')
        self.ingest_workers=ingest_workers
        self.app=application;self.kind=execution_kind
        self.prepare_kb=prepare_kb;self.on_record=on_record
        self.ingested={};self.records=[]

    async def record(self,row):
        self.records.append(row)
        if self.on_record:await _resolve(self.on_record(row))

    async def _upload(self,kb,source_id,filename,media,raw):
        digest=hashlib.sha256(raw).hexdigest();tick=time.perf_counter()
        document=await self.app.upload_document(self.app.principal,kb,filename,media,raw)
        if document.get('status')!='completed':
            raise RuntimeError('NATIVE_INGESTION_NOT_COMPLETED:'+str(document.get('error_code','')))
        readback=await self.app.read_document(self.app.principal,kb,document['id'])
        if hashlib.sha256(readback.content).hexdigest()!=digest:
            raise ValueError('NATIVE_ORIGINAL_READBACK_CHANGED')
        receipt={'source_id':source_id,'native_document_id':document['id'],'knowledge_base_id':kb,
            'sha256':digest,'bytes':len(raw),'ingestion_seconds':time.perf_counter()-tick,
            'status':'NATIVE_INGESTED_ORIGINAL_READBACK_VERIFIED'}
        await self.record({'event':'source_ingested',**receipt})
        return receipt

    async def ingest_dataset(self,dataset):
        if dataset.name in self.ingested:raise ValueError('NATIVE_DATASET_ALREADY_INGESTED')
        kb=(await self.app.create_knowledge_base(self.app.principal,'native-'+dataset.name+'-'+str(uuid.uuid4())))['id']
        # Caller supplies public fixture consent/model setup or approved real setup.
        # No automatic egress consent or production authority is invented here.
        await _resolve(self.prepare_kb(self.app,kb,dataset.name))
        bindings={};receipts={}
        pending=[]
        for doc in dataset.documents:
            raw=(doc.title+'\n'+doc.text).encode('utf-8')
            pending.append((doc.doc_id,hashlib.sha256(doc.doc_id.encode()).hexdigest()+'.txt','text/plain',raw))
        for file in dataset.files:
            raw=checked_bytes(Path(file['path']),file['sha256'],file.get('bytes'))
            pending.append((file['doc_id'],file['doc_id'],'application/pdf',raw))
        if len({row[0] for row in pending})!=len(pending):raise ValueError('NATIVE_SOURCE_ID_DUPLICATE')
        semaphore=asyncio.Semaphore(self.ingest_workers)
        async def ingest(row):
            async with semaphore:
                receipt=await self._upload(kb,*row)
                receipts[row[0]]=receipt;bindings[receipt['native_document_id']]=row[0]
        async with asyncio.TaskGroup() as group:
            for row in pending:group.create_task(ingest(row))
        if len(receipts)!=len(dataset.documents)+len(dataset.files):raise ValueError('NATIVE_FULL_CORPUS_NOT_INGESTED')
        state={'kb':kb,'bindings':bindings,'receipts':receipts,'dataset_version':dataset.version}
        self.ingested[dataset.name]=state
        await self.record({'event':'dataset_ingested','dataset':dataset.name,'version':dataset.version,
            'source_documents':len(receipts),'knowledge_base_id':kb,'full_frozen_corpus':True})
        return state

    async def _citation(self,reference,state):
        cid=reference['id'];source=await self.app.read_source_chunk(self.app.principal,cid)
        if source['knowledge_base_id']!=state['kb'] or source['document_id'] not in state['bindings']:
            raise ValueError('NATIVE_CITATION_OUTSIDE_EVALUATION_SCOPE')
        original_id=state['bindings'][source['document_id']];receipt=state['receipts'][original_id]
        original=await self.app.read_document(self.app.principal,state['kb'],source['document_id'])
        if hashlib.sha256(original.content).hexdigest()!=receipt['sha256']:
            raise ValueError('NATIVE_CITATION_SOURCE_VERSION_CHANGED')
        return {'chunk_id':cid,'source_id':original_id,'source_sha256':receipt['sha256'],
            'readable':bool(source['text']),'quote_matches_chunk':reference.get('content','')==source['text'],
            'chunk_type':reference.get('chunk_type'),'semantic_support':'NOT_EVALUATED'}

    async def execute_case(self,dataset,case):
        state=self.ingested[dataset.name];kb=state['kb']
        # Whole dataset scope. Gold qrels never restrict candidate retrieval.
        session=await self.app.create_session(self.app.principal,SimpleNamespace(
            knowledge_base_scope=[kb],document_scope=[],title='native '+dataset.name+' '+case.qid))
        turns=[]
        for mode in MODES:
            turns.append(await self.app.turns.accept(OrdinaryTurnRequest(session['id'],case.question,
                str(uuid.uuid4()),knowledge_base_scope=(kb,)),self.app.principal))
        paired=await execute_native_pair(self.app,turns,execution_kind=self.kind)
        ranks={stage:project_native_documents(rows,state['bindings']) for stage,rows in paired.rankings}
        modes=[]
        for outcome in paired.outcomes:
            if outcome['status']!='COMPLETED':
                modes.append({**outcome,'quality':{'status':'NOT_RUN_NATIVE_GENERATION_FAILED','quality_metrics':None}});continue
            result=outcome['result'];citations=[await self._citation(ref,state) for ref in result.references]
            quality=score_native_qa(case,dataset=dataset.name,prediction=result.answer,
                ranked_source_ids=ranks['context'],citations=[r['readable'] for r in citations],execution_kind=self.kind)
            modes.append({**outcome,'result':asdict(result),'citations':citations,'quality':quality})
        row={'event':'native_case','dataset':dataset.name,'dataset_version':dataset.version,'qid':case.qid,
            'execution_kind':self.kind,'capture_sha256':paired.capture_sha256,'prompt_sha256':paired.prompt_sha256,
            'context_sha256':paired.context_sha256,'ranked_original_documents':ranks,'modes':modes,
            'auxiliary_calls':paired.auxiliary_calls,'preparation_seconds':paired.preparation_seconds,
            'model_quality_status':paired.model_quality_status,'gold_indexed':False,'stage_acceptance':'NOT_CLAIMED'}
        await self.record(row);return row

    async def execute_omni(self,pages,gold,*,native_prediction=None):
        kb=(await self.app.create_knowledge_base(self.app.principal,'native-omnidocbench-'+str(uuid.uuid4())))['id']
        await _resolve(self.prepare_kb(self.app,kb,'omnidocbench'))
        result=[]
        for page in pages:
            raw=checked_bytes(Path(page['image_path']),page['image_sha256'])
            suffix=Path(page['document_id']).suffix.lower();media='image/png' if suffix=='.png' else 'image/jpeg'
            receipt=await self._upload(kb,page['page_id'],page['document_id'],media,raw)
            # Geometry cannot be manufactured from Gold or guessed from text.
            prediction=await _resolve(native_prediction(self.app,kb,receipt,page)) if native_prediction else None
            kind='ISOLATED_MOCK' if self.kind=='ISOLATED_MOCK' else 'RECORDED_NATIVE_OCR'
            score=score_native_omni(page,gold[page['page_id']],prediction,execution_kind=kind)
            row={'event':'native_omni_page','page_id':page['page_id'],'source':receipt,'parsing':score,
                'independent_qa':'NOT_RUN_OVERLAY_UNAVAILABLE','gold_indexed':False}
            await self.record(row);result.append(row)
        return result

    async def run(self,frozen,*,native_prediction=None):
        for dataset in frozen.datasets:
            await self.ingest_dataset(dataset)
            for case in dataset.cases:await self.execute_case(dataset,case)
        await self.execute_omni(frozen.omni_pages,frozen.omni_gold,native_prediction=native_prediction)
        return {'schema':'native-six-execution-v1','execution_kind':self.kind,
            'manifest_sha256':frozen.manifest_sha256,'records':self.records,
            'real_model_quality':'NOT_RUN' if self.kind=='ISOLATED_MOCK' else 'REQUIRES_RECEIPT_REVIEW',
            'stage_acceptance':'NOT_CLAIMED'}
