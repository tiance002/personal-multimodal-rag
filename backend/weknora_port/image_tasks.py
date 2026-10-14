"""Durable, owner-scoped image work, separate from memory extraction.

PostgreSQL owns delivery and the claim fence.  Fixed-source image tasks use
MaxRetry(3); a recorded uncertain external send is terminal UNKNOWN instead of
being replayed merely because a worker restarted.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import uuid
from dataclasses import dataclass

from .documents import DocumentLifecycleError
from .image_configuration import effective_image_config,resolve_image_selection
from .models.types import CallContext, TrustedScope
from .models.types import ProviderError
from .storage.configuration import ModelConfigurationError
from .storage.memory_queue import source_retry_delay

_LOG=logging.getLogger(__name__)


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


class ImageTaskQueue:
    def __init__(self, leases, principal, documents, model_store, catalogue, *,
                 randbelow=secrets.randbelow, interval=1.0):
        self.leases, self.principal, self.documents = leases, principal, documents
        self.model_store, self.catalogue = model_store, catalogue
        self.randbelow, self.interval = randbelow, interval
        self._pump = None

    async def create(self, principal, kb, document_id, source_version, override=None):
        if principal != self.principal:
            raise PermissionError('IMAGE_TASK_OWNER_MISMATCH')
        context=CallContext(source_version+'/image/config',
            TrustedScope(principal.subject_id,(kb['id'],),(document_id,)))
        selection=await resolve_image_selection(kb,override,self.model_store,self.catalogue,context)
        cfg={key:value for key,value in effective_image_config(kb,override).items() if key in {
            'description_language','custom_instructions','ocr_adapter','ocr_model_id',
            'caption_mode','caption_model_id'} and value not in (None,'')}
        cfg['enable_multimodal']=True
        task_id=str(uuid.uuid4())
        async with self.leases.lease(writable_knowledge_base_ids=frozenset({kb['id']})) as guard:
            guard.require_verified()
            async with guard.connection.transaction():
                rows=await (await guard.connection.execute(
                    'INSERT INTO port_image_tasks(id,tenant_id,subject_id,knowledge_base_id,knowledge_id,source_version,selected_config) '
                    'VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT(tenant_id,knowledge_base_id,knowledge_id,source_version) '
                    'DO NOTHING RETURNING id',
                    (task_id,principal.tenant_id,principal.subject_id,kb['id'],document_id,source_version,
                     json.dumps({'selection':selection.public(),'override':cfg},ensure_ascii=False)))).fetchall()
                if len(rows)!=1:raise DocumentLifecycleError('IMAGE_TASK_CREATE_CONFLICT')
                changed=await (await guard.connection.execute(
                    "UPDATE knowledges SET parse_status='pending',error_message='',updated_at=now() "
                    "WHERE id=%s AND tenant_id=%s AND knowledge_base_id=%s AND deleted_at IS NULL "
                    "AND metadata->>'source_version'=%s AND parse_status='processing' RETURNING id",
                    (document_id,principal.tenant_id,kb['id'],source_version))).fetchall()
                if len(changed)!=1:raise DocumentLifecycleError('IMAGE_SOURCE_VERSION_CHANGED')
        return task_id

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
                if any(value.get(kind,{}).get('status')=='sending' for value in outcomes.values()
                       for kind in ('ocr_text','caption')):
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
                return ImageTask(row['id'],row['tenant_id'],row['subject_id'],row['knowledge_base_id'],
                    row['knowledge_id'],row['source_version'],selected['selection'],selected['override'],
                    row['attempt']+1,token,outcomes)

    async def cancel(self,principal,knowledge_base_id,knowledge_id):
        if principal!=self.principal:raise PermissionError('IMAGE_TASK_OWNER_MISMATCH')
        async with self.leases.lease(writable_knowledge_base_ids=frozenset({knowledge_base_id})) as guard:
            guard.require_verified()
            async with guard.connection.transaction():
                rows=await (await guard.connection.execute(
                    'SELECT id,state,source_version FROM port_image_tasks WHERE tenant_id=%s '
                    'AND subject_id=%s AND knowledge_base_id=%s AND knowledge_id=%s '
                    'ORDER BY created_at DESC FOR UPDATE LIMIT 1',
                    (principal.tenant_id,principal.subject_id,knowledge_base_id,knowledge_id))).fetchall()
                if not rows:raise LookupError('IMAGE_TASK_NOT_FOUND')
                row=rows[0]
                if row['state'] not in ('completed','failed','cancelled','orphaned','unknown'):
                    await guard.connection.execute(
                        "UPDATE port_image_tasks SET state='cancelled',claim_token=NULL,lease_until=NULL,"
                        "error_code='IMAGE_TASK_CANCELLED',updated_at=now() WHERE id=%s",(row['id'],))
                    await guard.connection.execute(
                        "UPDATE knowledges SET parse_status='cancelled',error_message='IMAGE_TASK_CANCELLED',"
                        "updated_at=now() WHERE id=%s AND tenant_id=%s AND knowledge_base_id=%s "
                        "AND metadata->>'source_version'=%s AND deleted_at IS NULL "
                        "AND parse_status IN ('pending','processing')",
                        (knowledge_id,principal.tenant_id,knowledge_base_id,row['source_version']))
                return {'id':row['id'],'state':'cancelled' if row['state'] not in
                        ('completed','failed','cancelled','orphaned','unknown') else row['state']}

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
            'ocr_layout':info.get('ocr_layout',{})}
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
