"""Durable, owner-scoped image work, separate from memory extraction.

PostgreSQL owns delivery and the claim fence.  Fixed-source image tasks use
MaxRetry(3); a recorded uncertain external send is terminal UNKNOWN instead of
being replayed merely because a worker restarted.
"""
from __future__ import annotations

import asyncio
import json
import hashlib
import re
import logging
import secrets
import uuid
from dataclasses import dataclass,field

from .documents import DocumentLifecycleError
from .image_configuration import effective_image_config,resolve_image_selection
from .models.types import CallContext, TrustedScope
from .models.types import ProviderError
from .storage.configuration import ModelConfigurationError
from .storage.memory_queue import source_retry_delay

_LOG=logging.getLogger(__name__)


def unresolved_image_send(outcomes):
    """A terminal task label cannot erase an unresolved external dispatch."""
    if isinstance(outcomes,str):outcomes=json.loads(outcomes)
    return any(isinstance(value,dict) and isinstance(value.get(kind),dict)
               and value[kind].get('status')=='sending'
               for value in (outcomes or {}).values()
               for kind in ('ocr_text','caption','embedding'))


@dataclass(frozen=True)
class ImageTask:
    id: str
    tenant_id: int
    subject_id: str
    knowledge_base_id: str
    knowledge_id: str
    source_version: str
    selection: dict
    config: dict
    attempt: int
    claim_token: str
    outcomes: dict
    source_sha256: str = ""
    retry: dict = field(default_factory=dict)


class ImageTaskQueue:
    def __init__(self, leases, principal, documents, model_store, catalogue, *,
                 randbelow=secrets.randbelow, interval=1.0):
        self.leases, self.principal, self.documents = leases, principal, documents
        self.model_store, self.catalogue = model_store, catalogue
        self.randbelow, self.interval = randbelow, interval
        self._pump = None

    async def create(self, principal, kb, document_id, source_version, override=None, source_sha256=""):
        if principal != self.principal:
            raise PermissionError('IMAGE_TASK_OWNER_MISMATCH')
        context=CallContext(source_version+'/image/config',
            TrustedScope(principal.subject_id,(kb['id'],),(document_id,)))
        selection=await resolve_image_selection(kb,override,self.model_store,self.catalogue,context)
        cfg={key:value for key,value in effective_image_config(kb,override).items() if key in {
            'description_language','custom_instructions','ocr_adapter','ocr_model_id',
            'caption_mode','caption_model_id','reasoning_mode','max_output_tokens','config_version'} and value not in (None,'')}
        cfg['enable_multimodal']=True
        snapshot={"selection":selection.public(),"override":cfg,"source_sha256":source_sha256}
        task_id=str(uuid.uuid4())
        async with self.leases.lease(writable_knowledge_base_ids=frozenset({kb['id']})) as guard:
            guard.require_verified()
            async with guard.connection.transaction():
                rows=await (await guard.connection.execute(
                    'INSERT INTO port_image_tasks(id,tenant_id,subject_id,knowledge_base_id,knowledge_id,source_version,selected_config) '
                    'VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT(tenant_id,knowledge_base_id,knowledge_id,source_version,retry_key) '
                    'DO NOTHING RETURNING id',
                    (task_id,principal.tenant_id,principal.subject_id,kb['id'],document_id,source_version,
                     json.dumps(snapshot,ensure_ascii=False)))).fetchall()
                if len(rows)!=1:raise DocumentLifecycleError('IMAGE_TASK_CREATE_CONFLICT')
                changed=await (await guard.connection.execute(
                    "UPDATE knowledges SET parse_status='pending',error_message='',updated_at=now() "
                    "WHERE id=%s AND tenant_id=%s AND knowledge_base_id=%s AND deleted_at IS NULL "
                    "AND metadata->>'source_version'=%s AND parse_status='processing' RETURNING id",
                    (document_id,principal.tenant_id,kb['id'],source_version))).fetchall()
                if len(changed)!=1:raise DocumentLifecycleError('IMAGE_SOURCE_VERSION_CHANGED')
        return task_id

    async def create_selected_retry(self,principal,kb,document_id,*,source_version,
                                    asset_path,asset_sha256,kind,idempotency_key,override=None):
        if principal!=self.principal:raise PermissionError('IMAGE_TASK_OWNER_MISMATCH')
        if kind not in ('caption','ocr_text'):raise DocumentLifecycleError('IMAGE_RETRY_KIND_INVALID')
        if not isinstance(idempotency_key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}',idempotency_key):
            raise DocumentLifecycleError('IMAGE_RETRY_IDEMPOTENCY_INVALID')
        asset=await self.documents.read_document_asset(principal,kb['id'],document_id,
            asset_path=asset_path,source_version=source_version)
        if hashlib.sha256(asset.content).hexdigest()!=asset_sha256:
            raise DocumentLifecycleError('IMAGE_ASSET_REPLAY_MISMATCH')
        context=CallContext(source_version+'/image/retry-config',TrustedScope(principal.subject_id,(kb['id'],),(document_id,)))
        selection=await resolve_image_selection(kb,override,self.model_store,self.catalogue,context)
        cfg=effective_image_config(kb,override)
        retry={'asset_path':asset_path,'asset_sha256':asset_sha256,'kind':kind}
        snapshot={'selection':selection.public(),'override':cfg,'retry':retry}
        retry_key=hashlib.sha256(idempotency_key.encode()).hexdigest()
        snapshot['configuration_sha256']=hashlib.sha256(json.dumps(snapshot,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        async with self.leases.lease(writable_knowledge_base_ids=frozenset({kb['id']})) as guard:
            guard.require_verified()
            async with guard.connection.transaction():
                # Serialize admission with final switch and source deletion/version changes.
                current=await (await guard.connection.execute(
                    "SELECT metadata,parse_status FROM knowledges WHERE id=%s AND tenant_id=%s AND knowledge_base_id=%s AND deleted_at IS NULL FOR UPDATE",
                    (document_id,principal.tenant_id,kb['id']))).fetchall()
                if len(current)!=1 or current[0]['parse_status']!='completed' or current[0]['metadata'].get('source_version')!=source_version:
                    raise DocumentLifecycleError('IMAGE_SOURCE_VERSION_CHANGED')
                rows=await (await guard.connection.execute(
                    'SELECT id,state,retry_key,selected_config,outcomes FROM port_image_tasks WHERE tenant_id=%s AND subject_id=%s AND knowledge_base_id=%s AND knowledge_id=%s AND source_version=%s',
                    (principal.tenant_id,principal.subject_id,kb['id'],document_id,source_version))).fetchall()
                previous=next((r for r in rows if r['retry_key']==retry_key),None)
                if previous:
                    if previous['selected_config']!=snapshot:raise DocumentLifecycleError('IMAGE_RETRY_IDEMPOTENCY_CONFLICT')
                    return {'id':previous['id'],'state':previous['state'],'replay':True}
                if any(r['state'] in ('unknown','pending','processing','retry') or unresolved_image_send(r.get('outcomes')) for r in rows):
                    raise DocumentLifecycleError('IMAGE_RETRY_ACTIVE_OR_UNKNOWN')
                task_id=str(uuid.uuid4())
                await guard.connection.execute(
                    'INSERT INTO port_image_tasks(id,tenant_id,subject_id,knowledge_base_id,knowledge_id,source_version,retry_key,selected_config) VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb)',
                    (task_id,principal.tenant_id,principal.subject_id,kb['id'],document_id,source_version,retry_key,json.dumps(snapshot)))
        return {'id':task_id,'state':'pending','replay':False}

    async def _claim(self):
        async with self.leases.lease() as guard:
            guard.require_verified()
            async with guard.connection.transaction():
                rows=await (await guard.connection.execute(
                    "SELECT * FROM port_image_tasks WHERE tenant_id=%s AND subject_id=%s "
                    "AND ((state IN ('pending','retry') AND next_attempt_at<=now()) "
                    "OR (state='processing' AND lease_until<now())) "
                    'ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1',
                    (self.principal.tenant_id,self.principal.subject_id))).fetchall()
                if not rows:return None
                row=rows[0]
                outcomes=row['outcomes'] if isinstance(row['outcomes'],dict) else json.loads(row['outcomes'])
                if unresolved_image_send(outcomes):
                    await guard.connection.execute(
                        "UPDATE port_image_tasks SET state='unknown',error_code='IMAGE_REQUEST_OUTCOME_UNKNOWN',"
                        'claim_token=NULL,lease_until=NULL,updated_at=now() WHERE id=%s', (row['id'],))
                    await guard.connection.execute(
                        "UPDATE knowledges SET parse_status='failed',error_message='IMAGE_REQUEST_OUTCOME_UNKNOWN',"
                        "updated_at=now() WHERE id=%s AND tenant_id=%s AND knowledge_base_id=%s "
                        "AND metadata->>'source_version'=%s AND deleted_at IS NULL "
                        "AND parse_status IN ('pending','processing')",
                        (row['knowledge_id'],row['tenant_id'],row['knowledge_base_id'],row['source_version']))
                    return None
                if row['attempt']>=row['max_retry']+1:
                    await guard.connection.execute(
                        "UPDATE port_image_tasks SET state='failed',error_code='IMAGE_RETRY_EXHAUSTED',"
                        'claim_token=NULL,lease_until=NULL,updated_at=now() WHERE id=%s', (row['id'],))
                    await guard.connection.execute(
                        "UPDATE knowledges SET parse_status='failed',error_message='IMAGE_RETRY_EXHAUSTED',"
                        "updated_at=now() WHERE id=%s AND tenant_id=%s AND knowledge_base_id=%s "
                        "AND metadata->>'source_version'=%s AND deleted_at IS NULL "
                        "AND parse_status IN ('pending','processing')",
                        (row['knowledge_id'],row['tenant_id'],row['knowledge_base_id'],row['source_version']))
                    return None
                token=str(uuid.uuid4())
                await guard.connection.execute(
                    "UPDATE port_image_tasks SET state='processing',attempt=attempt+1,claim_token=%s,"
                    "lease_until=now()+interval '30 minutes',updated_at=now() WHERE id=%s",
                    (token,row['id']))
                selected=row['selected_config'] if isinstance(row['selected_config'],dict) else json.loads(row['selected_config'])
                # P27 snapshots stay intact in storage; their retired selector
                # must not constrain a resumed full-document image task.
                override=dict(selected['override'])
                override.pop('image_pages',None)
                legacy_scope=selected.get('page_scope')
                digest=selected.get('source_sha256','')
                if not digest and isinstance(legacy_scope,dict):
                    digest=legacy_scope.get('source_sha256','')
                return ImageTask(row['id'],row['tenant_id'],row['subject_id'],row['knowledge_base_id'],
                    row['knowledge_id'],row['source_version'],selected['selection'],override,
                    row['attempt']+1,token,outcomes,digest,selected.get('retry',{}))

    async def cancel(self,principal,knowledge_base_id,knowledge_id):
        if principal!=self.principal:raise PermissionError('IMAGE_TASK_OWNER_MISMATCH')
        async with self.leases.lease(writable_knowledge_base_ids=frozenset({knowledge_base_id})) as guard:
            guard.require_verified()
            async with guard.connection.transaction():
                rows=await (await guard.connection.execute(
                    'SELECT id,state,source_version,outcomes FROM port_image_tasks WHERE tenant_id=%s '
                    'AND subject_id=%s AND knowledge_base_id=%s AND knowledge_id=%s '
                    'ORDER BY created_at DESC FOR UPDATE LIMIT 1',
                    (principal.tenant_id,principal.subject_id,knowledge_base_id,knowledge_id))).fetchall()
                if not rows:raise LookupError('IMAGE_TASK_NOT_FOUND')
                row=rows[0]
                uncertain=unresolved_image_send(row.get('outcomes'))
                terminal=row['state'] in ('completed','failed','cancelled','orphaned','unknown')
                target='unknown' if uncertain else row['state'] if terminal else 'cancelled'
                if uncertain or not terminal:
                    code='IMAGE_REQUEST_OUTCOME_UNKNOWN' if uncertain else 'IMAGE_TASK_CANCELLED'
                    await guard.connection.execute(
                        "UPDATE port_image_tasks SET state=%s,claim_token=NULL,lease_until=NULL,"
                        "error_code=%s,updated_at=now() WHERE id=%s",(target,code,row['id']))
                    await guard.connection.execute(
                        "UPDATE knowledges SET parse_status=%s,error_message=%s,"
                        "updated_at=now() WHERE id=%s AND tenant_id=%s AND knowledge_base_id=%s "
                        "AND metadata->>'source_version'=%s AND deleted_at IS NULL "
                        "AND parse_status IN ('pending','processing')",
                        ('failed' if uncertain else 'cancelled',code,knowledge_id,principal.tenant_id,
                         knowledge_base_id,row['source_version']))
                return {'id':row['id'],'state':target}

    async def _update(self, task, *, state=None, error='', outcomes=None, delay=0):
        async with self.leases.lease(writable_knowledge_base_ids=frozenset({task.knowledge_base_id})) as guard:
            guard.require_verified()
            rows=await (await guard.connection.execute(
                'UPDATE port_image_tasks SET state=COALESCE(%s::varchar,state),error_code=%s,'
                'outcomes=COALESCE(%s::jsonb,outcomes),next_attempt_at=now()+(%s::double precision * interval \'1 second\'),'
                'claim_token=CASE WHEN %s::varchar IS NULL THEN claim_token ELSE NULL END,'
                'lease_until=CASE WHEN %s::varchar IS NULL THEN lease_until ELSE NULL END,updated_at=now() '
                "WHERE id=%s AND tenant_id=%s AND subject_id=%s AND claim_token=%s AND state='processing' RETURNING id",
                (state,error,json.dumps(outcomes,ensure_ascii=False) if outcomes is not None else None,
                 delay,state,state,task.id,task.tenant_id,task.subject_id,task.claim_token))).fetchall()
        if len(rows)!=1:raise DocumentLifecycleError('IMAGE_TASK_CLAIM_LOST')

    async def before(self, task, index, kind):
        await self.documents._assert_current_image_task(self.principal,task)
        outcomes={**task.outcomes}
        entry={**outcomes.get(str(index),{})}
        entry[kind]={'status':'sending'}
        outcomes[str(index)]=entry
        await self._update(task,outcomes=outcomes)
        task.outcomes.clear();task.outcomes.update(outcomes)

    async def result(self, task, index, info):
        outcomes={**task.outcomes}
        outcomes[str(index)]={**outcomes.get(str(index),{}),
            'ocr_text':info.get('ocr_status',{}),'caption':info.get('caption_status',{}),
            'ocr_result':info.get('ocr_text',''),'caption_result':info.get('caption',''),
            'ocr_layout':info.get('ocr_layout',{}),'asset_path':info.get('url',''),
            'asset_sha256':info.get('asset_sha256','')}
        await self._update(task,outcomes=outcomes)
        task.outcomes.clear();task.outcomes.update(outcomes)

    async def run_due(self):
        task=await self._claim()
        if task is None:return False
        heartbeat=asyncio.create_task(self._heartbeat(task))
        try:
            row=await self.documents.process_image_task(self.principal,task,self)
            if row['status']=='pending':
                state='failed' if task.attempt>=4 else 'retry'
                if state=='failed':
                    await self.documents.fail_image_task(self.principal,task,row.get('error_code') or 'IMAGE_TASK_PENDING_EXHAUSTED')
                await self._update(task,state=state,error=row.get('error_code',''),
                    delay=source_retry_delay(task.attempt-1,self.randbelow) if state=='retry' else 0)
            elif row['status']!='completed':
                await self._update(task,state='cancelled' if row['status']=='cancelled' else 'failed',
                    error=row.get('error_code',''))
        except asyncio.CancelledError:
            raise  # Retain lease; restart sees uncertain sends and does not resend.
        except ProviderError as exc:
            state='unknown' if exc.sent else 'failed'
            code='IMAGE_REQUEST_OUTCOME_UNKNOWN' if exc.sent else exc.code
            await self.documents.fail_image_task(self.principal,task,code)
            await self._update(task,state=state,error=code)
        except ModelConfigurationError as exc:
            code=str(exc) if str(exc).isupper() else 'IMAGE_MODEL_CONFIGURATION_INVALID'
            await self.documents.fail_image_task(self.principal,task,code)
            await self._update(task,state='failed',error=code)
        except DocumentLifecycleError as exc:
            terminal=exc.code in {'IMAGE_SOURCE_VERSION_CHANGED','IMAGE_TASK_ORPHANED','IMAGE_TASK_CLAIM_LOST'}
            invalid=exc.code.startswith(('IMAGE_VISION_','IMAGE_MODEL_','IMAGE_ADAPTER_',
                                          'IMAGE_CAPTION_','IMAGE_OCR_ADAPTER_','IMAGE_TASK_CONFIG_'))
            state='orphaned' if terminal else 'failed' if invalid or task.attempt>=4 else 'retry'
            delay=0 if state!='retry' else source_retry_delay(task.attempt-1,self.randbelow)
            if state=='failed':
                await self.documents.fail_image_task(self.principal,task,exc.code)
            await self._update(task,state=state,error=exc.code,delay=delay)
        except Exception:
            state='failed' if task.attempt>=4 else 'retry'
            delay=0 if state!='retry' else source_retry_delay(task.attempt-1,self.randbelow)
            if state=='failed':
                await self.documents.fail_image_task(self.principal,task,'IMAGE_TASK_PROCESSING_FAILED')
            await self._update(task,state=state,error='IMAGE_TASK_PROCESSING_FAILED',delay=delay)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat,return_exceptions=True)
        return True

    async def _heartbeat(self,task):
        while True:
            await asyncio.sleep(30)
            async with self.leases.lease() as guard:
                guard.require_verified()
                rows=await (await guard.connection.execute(
                    "UPDATE port_image_tasks SET lease_until=now()+interval '30 minutes',updated_at=now() "
                    "WHERE id=%s AND tenant_id=%s AND subject_id=%s AND claim_token=%s "
                    "AND state='processing' RETURNING id",
                    (task.id,task.tenant_id,task.subject_id,task.claim_token))).fetchall()
            if len(rows)!=1:return

    async def _loop(self):
        while True:
            try:
                if await self.run_due():continue
            except Exception as exc:
                _LOG.error('image worker cycle failed: %s',type(exc).__name__)
            await asyncio.sleep(self.interval)

    async def start(self):
        if self._pump is None or self._pump.done():
            self._pump=asyncio.create_task(self._loop())

    async def stop(self):
        if self._pump is not None:
            self._pump.cancel()
            await asyncio.gather(self._pump,return_exceptions=True)
            self._pump=None
