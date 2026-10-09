"""Durable run/attempt authority, independent of Redis and provider receipts."""
import hashlib
import json
import uuid
from sqlalchemy import text

from backend.app.ports.run_lifecycle import execution_owner, LifecycleDenied, RunClaim


def request_hash(session, kbs, docs, content, mode):
    return hashlib.sha256(json.dumps([session,kbs,docs,content,mode],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def check_fence(conn, run_id, owner=None):
    """Caller holds rag_runs lock first; renew/attempt/final commit use same order."""
    row = conn.execute(text('SELECT owner,lease_until>clock_timestamp() AS valid,state FROM rag_run_leases WHERE run_id=:id FOR UPDATE'), {'id':run_id}).mappings().first()
    if row is None:
        if owner is not None:
            raise LifecycleDenied('RUN_LEASE_MISSING')
        return
    if owner is None:
        from backend.app.ports.run_lifecycle import current_execution
        context = current_execution.get()
        if context is None or context[2].is_set():
            raise LifecycleDenied('RUN_LIFECYCLE_UNAVAILABLE')
    identity = execution_owner.get() if owner is None else (str(run_id),str(owner))
    if identity != (str(run_id),str(row['owner'])) or not row['valid'] or row['state'] != 'IN_PROGRESS':
        raise LifecycleDenied('RUN_LEASE_LOST')


class PostgresRunLifecycle:
    def __init__(self, engine, *, lease_seconds=60):
        if type(lease_seconds) is not int or lease_seconds < 3:
            raise ValueError('RUN_LEASE_INVALID')
        self.engine, self.lease_seconds = engine, lease_seconds

    def claim_run(self, session, kbs, docs, content, mode, request_id):
        run_id, owner = str(uuid.UUID(request_id)), str(uuid.uuid4())
        identity = request_hash(session,kbs,docs,content,mode)
        with self.engine.begin() as conn:
            inserted = conn.execute(text('''INSERT INTO rag_runs(id,conversation_id,knowledge_base_scope,document_scope,q0,status)
                VALUES (:id,:session,CAST(:kbs AS jsonb),CAST(:docs AS jsonb),:q0,'running')
                ON CONFLICT(id) DO NOTHING RETURNING id'''),dict(id=run_id,session=session,kbs=json.dumps(kbs),docs=json.dumps(docs),q0=content)).scalar()
            conn.execute(text('SELECT id FROM rag_runs WHERE id=:id FOR UPDATE'),dict(id=run_id)).one()
            if inserted is None:
                row = conn.execute(text('SELECT l.request_hash,l.owner,l.state,l.lease_until>clock_timestamp() AS valid,r.status FROM rag_run_leases l JOIN rag_runs r ON r.id=l.run_id WHERE l.run_id=:id'),dict(id=run_id)).mappings().first()
                if row is None:
                    raise LifecycleDenied('LEGACY_RUN_NOT_REEXECUTABLE')
                if row['request_hash'] != identity:
                    raise LifecycleDenied('REQUEST_ID_CONFLICT')
                state = {'completed':'COMPLETED','failed':'FAILED','cancelled':'CANCELLED'}.get(row['status'],row['state'])
                if state == 'IN_PROGRESS' and not row['valid']:
                    conn.execute(text("UPDATE rag_run_leases SET state='UNKNOWN' WHERE run_id=:id"),dict(id=run_id))
                    conn.execute(text("UPDATE rag_model_attempts SET state='UNKNOWN',updated_at=clock_timestamp() WHERE run_id=:id AND state='IN_PROGRESS'"),dict(id=run_id))
                    state = 'UNKNOWN'
                return RunClaim(run_id,str(row['owner']),False,state)
            conn.execute(text("""INSERT INTO rag_run_leases(run_id,request_hash,owner,lease_until,state)
                VALUES (:id,:hash,:owner,clock_timestamp()+:seconds * interval '1 second','IN_PROGRESS')"""),
                dict(id=run_id,hash=identity,owner=owner,seconds=self.lease_seconds))
        return RunClaim(run_id,owner,True,'IN_PROGRESS')

    def renew(self, run_id, owner):
        with self.engine.begin() as conn:
            conn.execute(text('SELECT id FROM rag_runs WHERE id=:id FOR UPDATE'),dict(id=run_id)).one()
            check_fence(conn,run_id,owner)
            conn.execute(text("UPDATE rag_run_leases SET lease_until=clock_timestamp()+:seconds * interval '1 second' WHERE run_id=:id"),dict(id=run_id,seconds=self.lease_seconds))

    def claim_attempt(self, *, run_id, owner, attempt_id, role, ordinal, envelope_hash, provider, model):
        if role not in {'chat_cheap','chat_expensive'} or type(ordinal) is not int or ordinal not in {1,2} or (ordinal==2 and role!='chat_expensive'):
            raise LifecycleDenied('ATTEMPT_IDENTITY_INVALID')
        from backend.app.ports.session_attempts import request_identity
        if attempt_id != request_identity(run_id,'quick.router.'+role,ordinal):
            raise LifecycleDenied('ATTEMPT_IDENTITY_INVALID')
        with self.engine.begin() as conn:
            conn.execute(text('SELECT id FROM rag_runs WHERE id=:id FOR UPDATE'),dict(id=run_id)).one()
            check_fence(conn,run_id,owner)
            row = conn.execute(text('''INSERT INTO rag_model_attempts(id,run_id,owner,role,ordinal,envelope_hash,provider,model,state)
                VALUES (:attempt,:id,:owner,:role,:ordinal,:hash,:provider,:model,'IN_PROGRESS')
                ON CONFLICT DO NOTHING RETURNING id'''),dict(attempt=attempt_id,id=run_id,owner=owner,role=role,ordinal=ordinal,hash=envelope_hash,provider=provider,model=model)).scalar()
            if row is None:
                raise LifecycleDenied('DUPLICATE_ATTEMPT')

    def finish_attempt(self, run_id, owner, attempt_id, state, diagnostics):
        if state not in {'NOT_SENT','COMPLETED','UNKNOWN'}:
            raise ValueError('ATTEMPT_STATE_INVALID')
        with self.engine.begin() as conn:
            # Preserve observations even after cancellation; no result publication or lease revival.
            count = conn.execute(text('''UPDATE rag_model_attempts SET state=:state,diagnostics=diagnostics || CAST(:data AS jsonb),updated_at=clock_timestamp()
                WHERE id=:attempt AND run_id=:id AND owner=:owner AND state='IN_PROGRESS' '''),dict(attempt=attempt_id,id=run_id,owner=owner,state=state,data=json.dumps(diagnostics))).rowcount
            if count != 1:
                raise LifecycleDenied('ATTEMPT_FINALIZATION_DENIED')

    def link_budget(self, run_id, owner, attempt_id, reservation):
        with self.engine.begin() as conn:
            count = conn.execute(text("""UPDATE rag_model_attempts SET diagnostics=diagnostics || jsonb_build_object('budget_reservation_id',CAST(:reservation AS text))
                WHERE id=:attempt AND run_id=:id AND owner=:owner AND state='IN_PROGRESS' """),
                dict(id=run_id,owner=owner,attempt=attempt_id,reservation=reservation)).rowcount
            if count != 1:
                raise LifecycleDenied('ATTEMPT_BUDGET_LINK_DENIED')

    def finish_run(self, run_id, owner, *, not_sent=False):
        with self.engine.begin() as conn:
            row = conn.execute(text('SELECT status FROM rag_runs WHERE id=:id FOR UPDATE'),dict(id=run_id)).scalar_one()
            if not_sent and row in {'created','running'}:
                # A reliable admission denial is a terminal failed Run, not a
                # forever-running history barrier. Only its exact owner may
                # perform this transition; no model/budget history is changed.
                lease = conn.execute(text('SELECT owner,state FROM rag_run_leases WHERE run_id=:id FOR UPDATE'),dict(id=run_id)).mappings().one()
                if str(lease['owner'])!=owner or lease['state']!='IN_PROGRESS':
                    raise LifecycleDenied('RUN_ADMISSION_TERMINAL_DENIED')
                seq=conn.execute(text("UPDATE rag_runs SET status='failed',error_code='STREAM_ADMISSION_DENIED',completed_at=clock_timestamp(),next_event_seq=next_event_seq+1 WHERE id=:id RETURNING next_event_seq-1"),dict(id=run_id)).scalar_one()
                conn.execute(text("INSERT INTO retrieval_events(id,run_id,seq,event_type,payload) VALUES (:event,:id,:seq,'run.failed',CAST(:payload AS jsonb))"),
                    dict(event=str(uuid.uuid4()),id=run_id,seq=seq,payload=json.dumps({'error_code':'STREAM_ADMISSION_DENIED','citations':[]})))
                row='failed'
            terminal = {'completed':'COMPLETED','failed':'FAILED','cancelled':'CANCELLED'}.get(row)
            state = 'NOT_SENT' if not_sent else terminal or 'UNKNOWN'
            conn.execute(text("UPDATE rag_run_leases SET state=:state WHERE run_id=:id AND owner=:owner AND state='IN_PROGRESS'"),dict(id=run_id,owner=owner,state=state))
            conn.execute(text("UPDATE rag_model_attempts SET state='UNKNOWN' WHERE run_id=:id AND owner=:owner AND state='IN_PROGRESS'"),dict(id=run_id,owner=owner))
            return state

    def cleanup_identity(self, run_id):
        with self.engine.connect() as conn:
            row = conn.execute(text('SELECT l.owner,r.conversation_id,r.status FROM rag_run_leases l JOIN rag_runs r ON r.id=l.run_id WHERE l.run_id=:id'),dict(id=run_id)).mappings().first()
            if row and row['status'] in {'completed','failed','cancelled'}:
                return str(row['conversation_id']),str(row['owner'])
            return None

    def fail_run(self, run_id, owner):
        """Atomically close this owner's logical Run; uncertainty is not refunded."""
        with self.engine.begin() as conn:
            status=conn.execute(text('SELECT status FROM rag_runs WHERE id=:id FOR UPDATE'),dict(id=run_id)).scalar_one()
            lease=conn.execute(text('SELECT owner FROM rag_run_leases WHERE run_id=:id FOR UPDATE'),dict(id=run_id)).mappings().one()
            if str(lease['owner']) != str(owner):
                raise LifecycleDenied('RUN_FAILURE_OWNER_MISMATCH')
            if status not in {'created','running'}:
                return
            seq=conn.execute(text("UPDATE rag_runs SET status='failed',error_code='RUN_EXECUTION_FAILED',completed_at=clock_timestamp(),next_event_seq=next_event_seq+1 WHERE id=:id RETURNING next_event_seq-1"),dict(id=run_id)).scalar_one()
            conn.execute(text("INSERT INTO retrieval_events(id,run_id,seq,event_type,payload) VALUES (:event,:id,:seq,'run.failed',CAST(:payload AS jsonb))"),
                dict(event=str(uuid.uuid4()),id=run_id,seq=seq,payload=json.dumps({'error_code':'RUN_EXECUTION_FAILED','citations':[]})))
            conn.execute(text("UPDATE rag_run_leases SET state='FAILED' WHERE run_id=:id AND owner=:owner"),dict(id=run_id,owner=owner))
            # Same RAG/lease/Agent lock order as final commit. Only this logical
            # Run's running Agent is terminalized; Attempt/fee history survives.
            conn.execute(text("UPDATE agent_runs SET status='failed',error_code='RUN_EXECUTION_FAILED',completed_at=clock_timestamp() WHERE id=:id AND status='running'"),dict(id=run_id))
            conn.execute(text("UPDATE rag_model_attempts SET state='UNKNOWN',updated_at=clock_timestamp() WHERE run_id=:id AND owner=:owner AND state='IN_PROGRESS'"),dict(id=run_id,owner=owner))
            if conn.execute(text("SELECT to_regclass('context_compaction_attempts') IS NOT NULL")).scalar_one():
                conn.execute(text("UPDATE context_compaction_attempts SET state='UNKNOWN' WHERE run_id=:id AND owner=:owner AND state='IN_PROGRESS'"),dict(id=run_id,owner=owner))

    def cleanup_done(self, run_id, owner):
        with self.engine.begin() as conn:
            conn.execute(text('UPDATE rag_run_leases SET cleanup_pending=false WHERE run_id=:id AND owner=:owner'),dict(id=run_id,owner=owner))

    def read_run(self, run_id, session, kbs, docs):
        with self.engine.connect() as conn:
            row = conn.execute(text('SELECT * FROM rag_runs WHERE id=:id AND conversation_id=:session'),dict(id=run_id,session=session)).mappings().first()
            conversation = conn.execute(text('SELECT knowledge_base_scope,document_scope,deleted_at FROM conversations WHERE id=:session'),dict(session=session)).mappings().first()
            if row is None or conversation is None or conversation['deleted_at'] is not None or row['knowledge_base_scope']!=kbs or row['document_scope']!=docs or conversation['knowledge_base_scope']!=kbs or conversation['document_scope']!=docs:
                raise LifecycleDenied('RUN_SCOPE_MISMATCH')
            answer = conn.execute(text("SELECT content FROM conversation_messages WHERE run_id=:id AND role='assistant' ORDER BY created_at LIMIT 1"),dict(id=run_id)).scalar()
            labels=[]
            if row['status']=='completed':
                payload=conn.execute(text("SELECT payload FROM retrieval_events WHERE run_id=:id AND event_type='answer.completed' ORDER BY seq DESC LIMIT 1"),dict(id=run_id)).scalar()
                labels=payload.get('citations') if isinstance(payload,dict) else None
                if not isinstance(labels,list) or any(not isinstance(label,str) for label in labels) or len(set(labels))!=len(labels):
                    raise LifecycleDenied('RUN_CITATION_INTEGRITY_ERROR')
                evidence=conn.execute(text('''SELECT ae.label,ae.quote,ae.quote_sha256,c.content FROM answer_evidence ae
                    JOIN chunks c ON c.id=ae.chunk_id AND c.version_id=ae.version_id WHERE ae.run_id=:id'''),dict(id=run_id)).mappings().all()
                by_label={e['label']:e for e in evidence}
                for label in labels:
                    e=by_label.get(label)
                    if e is None or hashlib.sha256(e['quote'].encode('utf8')).hexdigest()!=e['quote_sha256'] or e['quote'] not in e['content']:
                        raise LifecycleDenied('RUN_CITATION_INTEGRITY_ERROR')
            return dict(run_id=str(row['id']),status=row['status'],error_code=row['error_code'],answer=answer or '',citations=tuple(labels))
