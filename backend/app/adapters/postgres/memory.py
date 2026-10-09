"""All memory authority in PostgreSQL. No prompt/cache is authoritative."""
import uuid
import json
from sqlalchemy import text
from backend.app.ports.memory import MemoryDenied, subject_id, scope_value, item_value, identity

class PostgresMemoryRepository:
    def __init__(self,engine,principal):
        self.engine,self.principal=engine,subject_id(principal)

    def _subject(self,c,principal):
        if subject_id(principal)!=self.principal: raise MemoryDenied('MEMORY_PRINCIPAL_DENIED')
        c.execute(text('INSERT INTO memory_subjects(id) VALUES (:id) ON CONFLICT DO NOTHING'),dict(id=self.principal))
        return c.execute(text('SELECT * FROM memory_subjects WHERE id=:id FOR UPDATE'),dict(id=self.principal)).mappings().one()

    def settings(self,principal,patch=None):
        with self.engine.begin() as c:
            row=self._subject(c,principal)
            if patch is not None:
                if set(patch)-{'read_enabled','write_mode'} or ('read_enabled' in patch and type(patch['read_enabled']) is not bool) or patch.get('write_mode','explicit_only') not in {'auto','explicit_only'}:
                    raise MemoryDenied('MEMORY_SETTING_INVALID')
                c.execute(text('UPDATE memory_subjects SET read_enabled=:enabled,write_mode=:mode WHERE id=:id'),
                    dict(id=self.principal,enabled=patch.get('read_enabled',row['read_enabled']),mode=patch.get('write_mode',row['write_mode'])))
                row=c.execute(text('SELECT * FROM memory_subjects WHERE id=:id'),dict(id=self.principal)).mappings().one()
            return dict(row)

    def _rows(self,c,scope):
        data=scope_value(scope)
        return list(c.execute(text('SELECT * FROM long_term_memory_items WHERE subject_id=:subject AND scope_hash=:scope ORDER BY created_at,id'),
            dict(subject=self.principal,scope=identity(data))).mappings())

    def list_items(self,principal,scope,*,active_only=False):
        with self.engine.begin() as c:
            subject=self._subject(c,principal)
            if active_only and not subject['read_enabled']: return []
            rows=self._rows(c,scope)
            out=[]
            for r in rows:
                if active_only and r['status']!='active':continue
                # Do not serve a corrupt hash/scope even if an index matches.
                if r['scope']!=scope_value(scope) or item_value(r['kind'],r['fact_key'],r['content'])['content_hash']!=r['content_hash'].strip():
                    raise MemoryDenied('MEMORY_INTEGRITY_INVALID')
                row=dict(r);row['id']=str(row['id']);row['replaces_id']=str(row['replaces_id']) if row['replaces_id'] else None
                row['sources']=[dict(s) for s in c.execute(text('SELECT run_id,message_id,source,created_at FROM memory_sources WHERE item_id=:id ORDER BY created_at,id'),dict(id=r['id'])).mappings()]
                out.append(row)
            return out

    def _write(self,c,scope,value,origin,sources,target=None):
        rows=self._rows(c,scope);same=[r for r in rows if r['fact_key']==value['fact_key']]
        if origin=='extracted' and any(r['status'] in {'rejected','deleted'} for r in same): return None
        duplicate=next((r for r in reversed(same) if r['content_hash'].strip()==value['content_hash'] and r['status'] in {'active','pending'}),None)
        if target:
            old=next((r for r in rows if str(r['id'])==target),None)
            if old is None or old['status'] not in {'active','pending'}:raise MemoryDenied('MEMORY_UPDATE_DENIED')
            if old['fact_key']!=value['fact_key']:raise MemoryDenied('MEMORY_FACT_IDENTITY_CHANGED')
        if duplicate:
            item=str(duplicate['id'])
            if origin=='explicit' and duplicate['status']=='pending':
                c.execute(text("UPDATE long_term_memory_items SET status='superseded',updated_at=now() WHERE subject_id=:subject AND scope_hash=:scope AND fact_key=:key AND status='active'"),dict(subject=self.principal,scope=identity(scope_value(scope)),key=value['fact_key']))
                c.execute(text("UPDATE long_term_memory_items SET status='active',updated_at=now() WHERE id=:id"),dict(id=item))
        else:
            counts=c.execute(text("SELECT count(*) AS total,count(*) FILTER(WHERE status IN ('active','pending')) AS live FROM long_term_memory_items WHERE subject_id=:subject"),dict(subject=self.principal)).mappings().one()
            replaced=sum(r['status'] in {'active','pending'} for r in same) if origin=='explicit' else 0
            if counts['total']>=1000 or counts['live']-replaced+1>200:
                raise MemoryDenied('MEMORY_CAPACITY_EXCEEDED')
            active=next((r for r in reversed(same) if r['status']=='active'),None)
            previous=next((r for r in reversed(same) if str(r['id'])==target),None) if target else active
            if origin=='explicit':
                c.execute(text("UPDATE long_term_memory_items SET status='superseded',updated_at=now() WHERE subject_id=:subject AND scope_hash=:scope AND fact_key=:key AND status IN ('active','pending')"),dict(subject=self.principal,scope=identity(scope_value(scope)),key=value['fact_key']))
            item=str(uuid.uuid4())
            c.execute(text('''INSERT INTO long_term_memory_items(id,subject_id,scope,scope_hash,kind,fact_key,content,content_hash,origin,status,version,replaces_id)
                VALUES (:id,:subject,CAST(:scope AS jsonb),:scope_hash,:kind,:fact_key,:content,:content_hash,:origin,:status,:version,:previous)'''),
                dict(**value,id=item,subject=self.principal,scope=json.dumps(scope_value(scope)),scope_hash=identity(scope_value(scope)),origin=origin,
                    status='active' if origin=='explicit' else 'pending',version=max([r['version'] for r in same],default=0)+1,previous=str(previous['id']) if previous else None))
        for source in sources:
            c.execute(text('''INSERT INTO memory_sources(id,item_id,run_id,message_id,source,source_hash)
                VALUES (:id,:item,:run,:message,CAST(:source AS jsonb),:hash) ON CONFLICT(item_id,source_hash) DO NOTHING'''),
                dict(id=str(uuid.uuid4()),item=item,run=source.get('run_id'),message=source.get('message_id'),source=json.dumps(source),hash=identity(source)))
        return item

    def save(self,principal,scope,kind,key,content,*,target=None,source_request_id=None):
        value=item_value(kind,key,content)
        source={'type':'explicit_owner_action','content_hash':value['content_hash']}
        if source_request_id is not None:
            try:source['request_id']=str(uuid.UUID(source_request_id))
            except (ValueError,TypeError):raise MemoryDenied('MEMORY_SOURCE_REQUEST_INVALID') from None
        with self.engine.begin() as c:
            self._subject(c,principal)
            item=self._write(c,scope,value,'explicit',[source],target)
        return item

    def transition(self,principal,scope,item,status):
        if status not in {'active','rejected','deleted'}:raise MemoryDenied('MEMORY_TRANSITION_INVALID')
        with self.engine.begin() as c:
            self._subject(c,principal);rows=self._rows(c,scope)
            row=next((r for r in rows if str(r['id'])==item),None)
            if row is None:raise MemoryDenied('MEMORY_ITEM_NOT_FOUND')
            if status=='active' and row['status']!='pending':raise MemoryDenied('MEMORY_CONFIRM_DENIED')
            if status=='rejected' and row['status']!='pending':raise MemoryDenied('MEMORY_REJECT_DENIED')
            if status=='active':
                c.execute(text("UPDATE long_term_memory_items SET status='superseded',updated_at=now() WHERE subject_id=:subject AND scope_hash=:scope AND fact_key=:key AND status='active'"),dict(subject=self.principal,scope=row['scope_hash'],key=row['fact_key']))
            c.execute(text('UPDATE long_term_memory_items SET status=:state,updated_at=now() WHERE id=:id'),dict(id=item,state=status))

    def schedule(self,principal,scope,conversation,provider,model):
        """Committed complete pairs only. Source claims are the durable cursor.

        A source remains consumed for UNKNOWN/failure; no new ID can replay it.
        Scope changes never authorize historical messages from another scope.
        """
        data=scope_value(scope);scope_hash=identity(data)
        with self.engine.begin() as c:
            subject=self._subject(c,principal)
            if subject['write_mode']!='auto':raise MemoryDenied('MEMORY_AUTO_DISABLED')
            conv=c.execute(text('SELECT * FROM conversations WHERE id=:id AND deleted_at IS NULL'),dict(id=conversation)).mappings().first()
            if conv is None or set(conv['knowledge_base_scope'])!=set(data['kb']) or set(conv['document_scope'])!=set(data['documents']):raise MemoryDenied('MEMORY_SOURCE_SCOPE_DENIED')
            rows=list(c.execute(text('''SELECT r.id,r.q0,r.completed_at,u.id AS user_id,u.content AS user_content,a.id AS assistant_id,a.content AS answer
                FROM rag_runs r JOIN conversation_messages u ON u.run_id=r.id AND u.role='user'
                JOIN conversation_messages a ON a.run_id=r.id AND a.role='assistant'
                WHERE r.conversation_id=:conversation AND r.status='completed' AND r.error_code IS NULL AND r.completed_at IS NOT NULL
                AND r.knowledge_base_scope @> CAST(:kb AS jsonb) AND r.knowledge_base_scope <@ CAST(:kb AS jsonb)
                AND r.document_scope @> CAST(:docs AS jsonb) AND r.document_scope <@ CAST(:docs AS jsonb)
                AND EXISTS(SELECT 1 FROM retrieval_events e WHERE e.run_id=r.id AND e.event_type='answer.completed')
                AND (SELECT count(*) FROM conversation_messages m WHERE m.run_id=r.id)=2
                AND NOT EXISTS(SELECT 1 FROM memory_extracted_runs x WHERE x.subject_id=:subject AND x.scope_hash=:scope_hash AND x.run_id=r.id)
                ORDER BY r.completed_at,r.id LIMIT 8'''),dict(conversation=conversation,kb=json.dumps(data['kb']),docs=json.dumps(data['documents']),subject=self.principal,scope_hash=scope_hash)).mappings())
            sources=[];size=0
            for row in rows:
                if row['q0']!=row['user_content'] or not row['answer']:continue
                source=dict(run_id=str(row['id']),user_message_id=str(row['user_id']),assistant_message_id=str(row['assistant_id']),
                    question=row['q0'],answer=row['answer'],completed_at=row['completed_at'].isoformat())
                n=len(json.dumps(source,ensure_ascii=False).encode())
                if size+n>32000:
                    if not sources:raise MemoryDenied('MEMORY_SOURCE_ATOMIC_TOO_LARGE')
                    break
                size+=n;sources.append(source)
            if not sources:return None
            cursor=[{'run_id':s['run_id'],'hash':identity(s),'completed_at':s['completed_at']} for s in sources]
            job=identity(dict(purpose='memory_extraction',subject=self.principal,scope=data,cursor=cursor))
            c.execute(text('''INSERT INTO memory_extraction_jobs(id,subject_id,conversation_id,scope,scope_hash,sources,cursor,run_id,provider,model,state)
                VALUES (:id,:subject,:conv,CAST(:scope AS jsonb),:scope_hash,CAST(:sources AS jsonb),CAST(:cursor AS jsonb),:run,:provider,:model,'PENDING')'''),
                dict(id=job,subject=self.principal,conv=conversation,scope=json.dumps(data),scope_hash=scope_hash,sources=json.dumps(sources),cursor=json.dumps(cursor),run=sources[-1]['run_id'],provider=provider,model=model))
            for source in sources:
                c.execute(text('INSERT INTO memory_extracted_runs(subject_id,scope_hash,run_id,job_id) VALUES (:subject,:scope,:run,:job)'),dict(subject=self.principal,scope=scope_hash,run=source['run_id'],job=job))
            return job

    def claim(self,principal,job):
        with self.engine.begin() as c:
            subject=self._subject(c,principal)
            if subject['write_mode']!='auto':raise MemoryDenied('MEMORY_AUTO_DISABLED')
            row=c.execute(text("UPDATE memory_extraction_jobs SET state='IN_PROGRESS',updated_at=now() WHERE id=:id AND subject_id=:subject AND state='PENDING' RETURNING *"),dict(id=job,subject=self.principal)).mappings().first()
            if row is None:raise MemoryDenied('MEMORY_ATTEMPT_ALREADY_CONSUMED')
            return dict(row)

    def link_budget(self,principal,job,reservation):
        with self.engine.begin() as c:
            self._subject(c,principal)
            n=c.execute(text('''UPDATE memory_extraction_jobs j SET budget_reservation_id=:budget FROM model_calls b
                WHERE j.id=:id AND j.subject_id=:subject AND j.state='IN_PROGRESS' AND j.budget_reservation_id IS NULL
                AND b.id=:budget AND b.run_id=j.run_id AND b.provider=j.provider AND b.model_name=j.model
                AND b.purpose='memory_extraction' AND b.reservation_state='reserved' AND b.reserved_cost_microunits>0'''),dict(id=job,subject=self.principal,budget=reservation)).rowcount
            if n!=1:raise MemoryDenied('MEMORY_BUDGET_LINK_DENIED')

    def finish(self,principal,job,items,diagnostics):
        from backend.app.domain.scope import Scope
        with self.engine.begin() as c:
            self._subject(c,principal)
            row=c.execute(text("SELECT * FROM memory_extraction_jobs WHERE id=:id AND subject_id=:subject AND state='IN_PROGRESS' AND budget_reservation_id IS NOT NULL FOR UPDATE"),dict(id=job,subject=self.principal)).mappings().first()
            if row is None:raise MemoryDenied('MEMORY_FINISH_DENIED')
            scope=Scope.from_ids(row['scope']['kb'],row['scope']['documents'])
            for item in items:
                source=row['sources'][item['source_index']]
                self._write(c,scope,item_value(item['kind'],item['fact_key'],item['content']),'extracted',[
                    {'type':'extracted','job_id':job,'run_id':source['run_id'],'message_id':source['user_message_id'],
                     'source_hash':identity(source),'model':row['model'],'provider':row['provider'],'cursor':row['cursor']}])
            c.execute(text("UPDATE memory_extraction_jobs SET state='COMPLETED',diagnostics=CAST(:d AS jsonb),updated_at=now() WHERE id=:id"),dict(id=job,d=json.dumps(diagnostics)))

    def before_send(self,principal,job):
        with self.engine.begin() as c:
            subject=self._subject(c,principal)
            if subject['write_mode']!='auto':raise MemoryDenied('MEMORY_AUTO_DISABLED')
            row=c.execute(text("SELECT * FROM memory_extraction_jobs WHERE id=:id AND subject_id=:subject AND state='IN_PROGRESS' AND budget_reservation_id IS NOT NULL"),dict(id=job,subject=self.principal)).mappings().first()
            if row is None:raise MemoryDenied('MEMORY_SEND_FENCE_DENIED')
            cv=c.execute(text('SELECT * FROM conversations WHERE id=:id AND deleted_at IS NULL'),dict(id=row['conversation_id'])).mappings().first()
            if cv is None or set(cv['knowledge_base_scope'])!=set(row['scope']['kb']) or set(cv['document_scope'])!=set(row['scope']['documents']):raise MemoryDenied('MEMORY_SOURCE_SCOPE_DENIED')
            for source in row['sources']:
                values=c.execute(text('''SELECT r.q0,r.status,r.error_code,r.completed_at,u.content AS question,a.content AS answer
                    FROM rag_runs r JOIN conversation_messages u ON u.id=:user AND u.run_id=r.id AND u.role='user'
                    JOIN conversation_messages a ON a.id=:assistant AND a.run_id=r.id AND a.role='assistant'
                    WHERE r.id=:run'''),dict(run=source['run_id'],user=source['user_message_id'],assistant=source['assistant_message_id'])).mappings().first()
                if (values is None or values['status']!='completed' or values['error_code'] is not None or values['q0']!=source['question'] or values['question']!=source['question'] or values['answer']!=source['answer'] or values['completed_at'].isoformat()!=source['completed_at']):raise MemoryDenied('MEMORY_SOURCE_CHANGED')

    def fail(self,principal,job,state,code,diagnostics=None):
        if state not in {'NOT_SENT','UNKNOWN'}:raise MemoryDenied('MEMORY_FAILURE_STATE_INVALID')
        with self.engine.begin() as c:
            self._subject(c,principal)
            detail=dict(diagnostics or {},error=code,fee='UNKNOWN' if state=='UNKNOWN' else 'NOT_INCURRED')
            n=c.execute(text("UPDATE memory_extraction_jobs SET state=:state,diagnostics=CAST(:diagnostics AS jsonb),updated_at=now() WHERE id=:id AND subject_id=:subject AND state='IN_PROGRESS'"),dict(id=job,subject=self.principal,state=state,diagnostics=json.dumps(detail))).rowcount
            if n!=1:raise MemoryDenied('MEMORY_FAILURE_PERSISTENCE_UNKNOWN')
