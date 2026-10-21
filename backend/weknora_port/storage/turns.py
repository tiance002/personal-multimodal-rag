"""Durable pre-P3 ordinary turn admission; fresh verified lease per operation."""
import asyncio,hashlib,json,re,uuid
from dataclasses import dataclass,field

from ..models.types import CallContext,TrustedScope,ProviderError
from ..conversation.types import MemoryContext
from ..service import AcceptedTurn,OrdinaryTurnRequest
from .history import LocalPrincipal,PostgresHistoryRepository


def _json(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def _tuple(value):return tuple(json.loads(value) if isinstance(value,str) else value)


@dataclass(frozen=True)
class ScopeSnapshot:
    q0: str=field(repr=False)
    knowledge_base_ids: tuple[str,...]
    document_ids: tuple[str,...]

    def public(self):return {'knowledge_base_ids':self.knowledge_base_ids,'document_ids':self.document_ids}


@dataclass(frozen=True)
class TurnState:
    status: str
    error_code: str=''


class PostgresTurnRepository:
    def __init__(self,lease_factory,principal: LocalPrincipal):
        if not isinstance(principal,LocalPrincipal):raise ProviderError('LOCAL_PRINCIPAL_REQUIRED')
        self.leases,self.principal=lease_factory,principal

    def _owner(self,principal):
        if not isinstance(principal,LocalPrincipal) or principal!=self.principal:raise ProviderError('TURN_OWNER_MISMATCH')
        return principal.tenant_id,principal.subject_id

    def _accepted_owner(self,accepted):
        # Memory enablement is a server policy, not part of principal identity.
        # A disabled-memory turn still needs durable failure/cancel cleanup.
        if (not isinstance(accepted,AcceptedTurn) or not isinstance(accepted.memory_context,MemoryContext)
                or type(accepted.memory_context.tenant_id) is not int
                or accepted.memory_context.tenant_id != self.principal.tenant_id
                or accepted.memory_context.principal_storage_id != self.principal.subject_id
                or type(accepted.memory_context.agent_memory_allowed) is not bool
                or not isinstance(accepted.context,CallContext) or not isinstance(accepted.context.scope,TrustedScope)
                or accepted.context.scope.owner_id != self.principal.subject_id):
            raise ProviderError('TURN_OWNER_MISMATCH')

    async def _rows(self,guard,sql,params=()):
        guard.require_verified();return await (await guard.connection.execute(sql,params)).fetchall()

    async def _session(self,guard,session_id,principal):
        rows=await self._rows(guard,'SELECT * FROM sessions WHERE id=%s AND tenant_id=%s AND subject_id=%s AND deleted_at IS NULL FOR UPDATE',(session_id,*self._owner(principal)))
        if len(rows)!=1:raise ProviderError('TURN_SESSION_UNAVAILABLE')
        return rows[0]

    async def _resources(self,guard,kbs,docs):
        if not kbs:raise ProviderError('TURN_KB_SCOPE_REQUIRED')
        rows=await self._rows(guard,'SELECT id FROM knowledge_bases WHERE tenant_id=%s AND id=ANY(%s) AND deleted_at IS NULL FOR SHARE',(self.principal.tenant_id,list(kbs)))
        if {r['id'] for r in rows}!=set(kbs):raise ProviderError('TURN_KB_SCOPE_DENIED')
        if docs:
            rows=await self._rows(guard,'SELECT id FROM knowledges WHERE tenant_id=%s AND id=ANY(%s) AND knowledge_base_id=ANY(%s) AND deleted_at IS NULL FOR SHARE',(self.principal.tenant_id,list(docs),list(kbs)))
            if {r['id'] for r in rows}!=set(docs):raise ProviderError('TURN_DOCUMENT_SCOPE_DENIED')

    async def _resolve(self,guard,request,session):
        ceiling=_tuple(session['knowledge_base_scope'])
        if not ceiling and session.get('knowledge_base_id'):ceiling=(session['knowledge_base_id'],)
        kbs=tuple(sorted(set(request.knowledge_base_scope or ceiling)))
        if not set(kbs).issubset(ceiling):raise ProviderError('TURN_RAW_SCOPE_EXPANSION_DENIED')
        doc_ceiling=_tuple(session['document_scope'])
        docs=tuple(sorted(set(request.document_scope or doc_ceiling)))
        if doc_ceiling and not set(docs).issubset(doc_ceiling):raise ProviderError('TURN_RAW_SCOPE_EXPANSION_DENIED')
        await self._resources(guard,kbs,docs)
        return ScopeSnapshot(request.content,kbs,docs)

    @staticmethod
    def _request_configuration(request, *, include_order=True):
        value={name:getattr(request,name) for name in ('summary_model_id','reasoning_effort','accept_language') if getattr(request,name)}
        ordered=tuple(dict.fromkeys(request.knowledge_base_scope))
        if include_order and len(ordered)>1:
            value['knowledge_base_order']=list(ordered)
        return value

    def _payload(self,request,snapshot,*,include_order=True):
        payload={'version':'ordinary-turn/v1','session':request.session_id,'mode':'quick','q0':snapshot.q0,'knowledge_bases':snapshot.knowledge_base_ids,'documents':snapshot.document_ids}
        options=self._request_configuration(request,include_order=include_order)
        # Empty new options preserve exact historical v1 payloads and hashes.
        if options:payload.update(version='ordinary-turn/v2',request_configuration=options)
        return hashlib.sha256(_json(payload).encode()).hexdigest()

    def _accepted(self,row,*,new=False):
        kbs,docs=_tuple(row['knowledge_base_scope']),_tuple(row['document_scope'])
        options=row.get('request_configuration') or {}
        if isinstance(options,str):options=json.loads(options)
        options=dict(options)
        ordered=tuple(options.pop('knowledge_base_order',kbs))
        if set(ordered)!=set(kbs):raise ProviderError('TURN_KB_ORDER_SNAPSHOT_INVALID')
        request=OrdinaryTurnRequest(row['session_id'],row['q0'],row['run_id'],'quick',ordered,docs,**options)
        context=CallContext(row['run_id'],TrustedScope(self.principal.subject_id,kbs,docs),cancelled=asyncio.Event(),session_id=row['session_id'])
        return AcceptedTurn(row['run_id'],row['session_id'],row['user_message_id'],row['assistant_message_id'],context,
                            MemoryContext(self.principal.tenant_id,self.principal.subject_id),request,new,not new,row['client_request_id'])

    async def accept(self,request: OrdinaryTurnRequest,principal: LocalPrincipal):
        self._owner(principal)
        if not isinstance(request,OrdinaryTurnRequest) or request.mode!='quick':raise ProviderError('ORDINARY_MODE_REQUIRED')
        if len(request.request_id)>36 or not request.content.strip():raise ProviderError('TURN_REQUEST_ID_OR_CONTENT_INVALID')
        try:request.content.encode('utf-8')
        except UnicodeError:raise ProviderError('TURN_CONTENT_ENCODING_INVALID') from None
        if '\x00' in request.content:raise ProviderError('TURN_CONTENT_ENCODING_INVALID')
        async with self.leases.lease() as guard:
            guard.require_verified();conn=guard.connection
            async with conn.transaction():
                session=await self._session(guard,request.session_id,principal)
                snapshot=await self._resolve(guard,request,session);payload=self._payload(request,snapshot)
                rows=await self._rows(guard,'SELECT * FROM port_turns WHERE tenant_id=%s AND subject_id=%s AND session_id=%s AND client_request_id=%s FOR UPDATE',(*self._owner(principal),request.session_id,request.request_id))
                if rows:
                    row=rows[0]
                    if row['payload_sha256']!=payload:
                        # v1 history did not capture order; preserve equivalent replay, never resend.
                        if row.get('request_configuration') or self._request_configuration(request,include_order=False) or row['payload_sha256']!=self._payload(request,snapshot,include_order=False):
                            raise ProviderError('TURN_REQUEST_PAYLOAD_CONFLICT')
                    await self._messages(guard,row)
                    return self._accepted(row)
                # Recover source-existing messages without starting their run again.
                old=await self._rows(guard,"SELECT id,role,content,knowledge_base_scope,document_scope FROM messages WHERE tenant_id=%s AND subject_id=%s AND session_id=%s AND request_id=%s AND deleted_at IS NULL",(*self._owner(principal),request.session_id,request.request_id))
                new=not old
                run_id=str(uuid.uuid4()) if new else request.request_id
                user_id,assistant_id=str(uuid.uuid4()),str(uuid.uuid4())
                if old:
                    users=[r for r in old if r['role']=='user'];answers=[r for r in old if r['role']=='assistant']
                    if len(users)!=1 or len(answers)!=1 or users[0]['content']!=snapshot.q0 or _tuple(users[0]['knowledge_base_scope'])!=snapshot.knowledge_base_ids or _tuple(users[0]['document_scope'])!=snapshot.document_ids:
                        raise ProviderError('TURN_REQUEST_PAYLOAD_CONFLICT')
                    user_id,assistant_id=users[0]['id'],answers[0]['id']
                row=dict(tenant_id=principal.tenant_id,subject_id=principal.subject_id,session_id=request.session_id,client_request_id=request.request_id,run_id=run_id,user_message_id=user_id,assistant_message_id=assistant_id,q0=snapshot.q0,knowledge_base_scope=snapshot.knowledge_base_ids,document_scope=snapshot.document_ids,payload_sha256=payload,request_configuration=self._request_configuration(request))
                if new:
                    for id,role,content in ((user_id,'user',snapshot.q0),(assistant_id,'assistant','')):
                        await conn.execute('INSERT INTO messages(id,request_id,session_id,tenant_id,subject_id,role,content,knowledge_base_scope,document_scope,is_completed,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,clock_timestamp())',(id,run_id,request.session_id,*self._owner(principal),role,content,_json(snapshot.knowledge_base_ids),_json(snapshot.document_ids),role=='user'))
                await conn.execute('INSERT INTO port_turns(tenant_id,subject_id,session_id,client_request_id,run_id,user_message_id,assistant_message_id,q0,payload_sha256,knowledge_base_scope,document_scope,request_configuration) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb)',(*self._owner(principal),request.session_id,request.request_id,run_id,user_id,assistant_id,snapshot.q0,payload,_json(snapshot.knowledge_base_ids),_json(snapshot.document_ids),_json(self._request_configuration(request))))
                return self._accepted(row,new=new)

    async def _messages(self,guard,row):
        rows=await self._rows(guard,"SELECT id,role FROM messages WHERE tenant_id=%s AND subject_id=%s AND session_id=%s AND request_id=%s AND id=ANY(%s) AND deleted_at IS NULL",(*self._owner(self.principal),row['session_id'],row['run_id'],[row['user_message_id'],row['assistant_message_id']]))
        if {(r['id'],r['role']) for r in rows}!={(row['user_message_id'],'user'),(row['assistant_message_id'],'assistant')}:raise ProviderError('TURN_MESSAGES_UNAVAILABLE')

    async def _row(self,guard,session_id,assistant_id):
        rows=await self._rows(guard,'SELECT * FROM port_turns WHERE tenant_id=%s AND subject_id=%s AND session_id=%s AND assistant_message_id=%s FOR UPDATE',(*self._owner(self.principal),session_id,assistant_id))
        if len(rows)!=1:raise ProviderError('TURN_RUN_UNAVAILABLE')
        return rows[0]

    async def validate_run(self,session_id,assistant_message_id,principal):
        self._owner(principal)
        async with self.leases.lease() as guard:
            async with guard.connection.transaction():
                session=await self._session(guard,session_id,principal)
                row=await self._row(guard,session_id,assistant_message_id)
                snapshot=await self._resolve(guard,self._accepted(row).request,session)
                if snapshot.knowledge_base_ids!=_tuple(row['knowledge_base_scope']) or snapshot.document_ids!=_tuple(row['document_scope']):
                    raise ProviderError('TURN_SESSION_SCOPE_CHANGED')
                await self._messages(guard,row)
                return self._accepted(row)

    async def commit_failure(self,accepted,safe_code) -> bool:
        self._accepted_owner(accepted)
        if not re.fullmatch('[A-Z][A-Z0-9_]{0,127}',safe_code):raise ProviderError('TURN_FAILURE_CODE_INVALID')
        async with self.leases.lease() as guard:
            async with guard.connection.transaction():
                await self._session(guard,accepted.session_id,self.principal)
                row=await self._row(guard,accepted.session_id,accepted.assistant_message_id)
                if row['run_id']!=accepted.run_id:raise ProviderError('TURN_RUN_IDENTITY_MISMATCH')
                messages=await self._rows(guard,"SELECT is_completed FROM messages WHERE id=%s AND request_id=%s AND session_id=%s AND tenant_id=%s AND subject_id=%s AND role='assistant' AND deleted_at IS NULL FOR UPDATE",(accepted.assistant_message_id,accepted.run_id,accepted.session_id,*self._owner(self.principal)))
                if len(messages)!=1:raise ProviderError('TURN_MESSAGES_UNAVAILABLE')
                if messages[0]['is_completed']:return False
                if row['status']=='error':
                    if row['error_code']!=safe_code:raise ProviderError('TURN_FAILURE_CONFLICT')
                    return True
                await guard.connection.execute("UPDATE messages SET turn_status='error',turn_error_code=%s,updated_at=now() WHERE id=%s AND request_id=%s AND session_id=%s AND tenant_id=%s AND subject_id=%s AND role='assistant' AND is_completed=false",(safe_code,accepted.assistant_message_id,accepted.run_id,accepted.session_id,*self._owner(self.principal)))
                await guard.connection.execute("UPDATE port_turns SET status='error',error_code=%s,updated_at=now() WHERE tenant_id=%s AND subject_id=%s AND session_id=%s AND run_id=%s",(safe_code,*self._owner(self.principal),accepted.session_id,accepted.run_id))
                return True

    async def commit_cancelled(self,accepted,answer,thinking,references) -> bool:
        self._accepted_owner(accepted)
        if not isinstance(answer,str) or not isinstance(thinking,str):raise ProviderError('TURN_CANCELLED_CONTENT_INVALID')
        encoded=_json(references)
        async with self.leases.lease() as guard:
            async with guard.connection.transaction():
                await guard.connection.execute("SET LOCAL statement_timeout='4500ms'")
                await guard.connection.execute("SET LOCAL lock_timeout='4500ms'")
                await self._session(guard,accepted.session_id,self.principal)
                row=await self._row(guard,accepted.session_id,accepted.assistant_message_id)
                if row['run_id']!=accepted.run_id:raise ProviderError('TURN_RUN_IDENTITY_MISMATCH')
                messages=await self._rows(guard,"SELECT is_completed,agent_steps FROM messages WHERE id=%s AND request_id=%s AND session_id=%s AND tenant_id=%s AND subject_id=%s AND role='assistant' AND deleted_at IS NULL FOR UPDATE",
                    (accepted.assistant_message_id,accepted.run_id,accepted.session_id,*self._owner(self.principal)))
                if len(messages)!=1:raise ProviderError('TURN_MESSAGES_UNAVAILABLE')
                if messages[0]['is_completed'] or row['status']!='pending':return False
                for reference in references:
                    if reference.get('knowledge_source','').lower()=='web_search':continue
                    if reference.get('knowledge_base_id') not in accepted.context.scope.knowledge_base_ids or (accepted.context.scope.document_ids and reference.get('knowledge_id') not in accepted.context.scope.document_ids):raise ProviderError('TURN_CANCELLED_REFERENCE_SCOPE_MISMATCH')
                    source=await self._rows(guard,"SELECT content,source_content FROM chunks WHERE id=%s AND tenant_id=%s AND knowledge_base_id=%s AND knowledge_id=%s AND deleted_at IS NULL",
                        (reference.get('id'),self.principal.tenant_id,reference.get('knowledge_base_id'),reference.get('knowledge_id')))
                    if len(source)!=1 or (source[0]['source_content'] or source[0]['content'])!=reference.get('content'):raise ProviderError('TURN_CANCELLED_REFERENCE_UNAVAILABLE')
                steps=messages[0]['agent_steps'] or []
                if thinking:
                    from datetime import datetime,timezone
                    steps=list(steps)
                    if not steps:steps=[{'iteration':0,'timestamp':datetime.now(timezone.utc).isoformat(),'tool_calls':[]}]
                    steps[0]={**steps[0],'reasoning_content':thinking}
                await guard.connection.execute("UPDATE messages SET content=%s,rendered_content=%s,knowledge_references=%s::jsonb,agent_steps=%s::jsonb,is_completed=true,turn_status='cancelled',turn_error_code='MODEL_CALL_CANCELLED',updated_at=now() WHERE id=%s AND request_id=%s AND session_id=%s AND tenant_id=%s AND subject_id=%s AND role='assistant'",
                    (answer,answer,encoded,_json(steps),accepted.assistant_message_id,accepted.run_id,accepted.session_id,*self._owner(self.principal)))
                # Existing coarse terminal CHECK stays unchanged; error_code/message status distinguish cancel from success.
                await guard.connection.execute("UPDATE port_turns SET status='error',error_code='MODEL_CALL_CANCELLED',updated_at=now() WHERE tenant_id=%s AND subject_id=%s AND session_id=%s AND run_id=%s",
                    (*self._owner(self.principal),accepted.session_id,accepted.run_id))
                return True

    async def status(self,session_id,assistant_id,principal=None):
        principal=self.principal if principal is None else principal
        self._owner(principal)
        async with self.leases.lease() as guard:
            rows=await self._rows(guard,"SELECT t.status,t.error_code,m.is_completed,m.turn_status FROM port_turns t JOIN messages m ON m.id=t.assistant_message_id AND m.tenant_id=t.tenant_id AND m.subject_id=t.subject_id JOIN sessions s ON s.id=t.session_id AND s.tenant_id=t.tenant_id AND s.subject_id=t.subject_id WHERE t.tenant_id=%s AND t.subject_id=%s AND t.session_id=%s AND t.assistant_message_id=%s AND m.deleted_at IS NULL AND s.deleted_at IS NULL",(*self._owner(principal),session_id,assistant_id))
            if len(rows)!=1:raise ProviderError('TURN_RUN_UNAVAILABLE')
            if rows[0]['turn_status']=='cancelled':return TurnState('cancelled',rows[0]['error_code'])
            if rows[0]['status']=='error':return TurnState('error',rows[0]['error_code'])
            return TurnState('completed','') if rows[0]['is_completed'] else TurnState(rows[0]['status'],rows[0]['error_code'])

    async def commit_final(self,commit):
        """Adapter callback around root history atomic final; no copied logic."""
        async with self.leases.lease() as guard:
            async with guard.connection.transaction():
                await self._session(guard,commit.session_id,self.principal)
                row=await self._row(guard,commit.session_id,commit.message_id)
                if row['run_id']!=commit.request_id:raise ProviderError('TURN_RUN_IDENTITY_MISMATCH')
                if row['status']=='error':raise ProviderError('TURN_ALREADY_FAILED')
                await PostgresHistoryRepository(guard,self.principal).commit_final(commit)
                await guard.connection.execute("UPDATE port_turns SET status='completed',error_code='',updated_at=now() WHERE tenant_id=%s AND subject_id=%s AND session_id=%s AND run_id=%s",(*self._owner(self.principal),commit.session_id,commit.request_id))
