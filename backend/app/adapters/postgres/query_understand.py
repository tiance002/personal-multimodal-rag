"""Independent, append-only query attempts on existing authoritative run events.

The run row lock and execution lease fence serialize claims. One purpose per
Run, regardless of new inputs/model names. No new schema or Chat ordinal reuse.
"""
import json
import uuid
from sqlalchemy import text
from backend.app.adapters.postgres.context_checkpoint import PostgresContextStore,scope_data
from backend.app.ports.query_understand import QueryUnderstandDenied

class PostgresQueryAttemptStore:
    def __init__(self,engine):self.engine=engine;self.fence=PostgresContextStore(engine)

    def _events(self,c,run):
        return list(c.execute(text("SELECT payload FROM retrieval_events WHERE run_id=:run AND event_type='query_understand.attempt' ORDER BY seq"),{'run':run}).scalars())

    def _append(self,c,run,payload):
        seq=c.execute(text('UPDATE rag_runs SET next_event_seq=next_event_seq+1 WHERE id=:id RETURNING next_event_seq-1'),{'id':run}).scalar_one()
        c.execute(text("INSERT INTO retrieval_events(id,run_id,seq,event_type,payload) VALUES (:id,:run,:seq,'query_understand.attempt',CAST(:payload AS jsonb))"),
            {'id':str(uuid.uuid4()),'run':run,'seq':seq,'payload':json.dumps(payload)})

    def claim(self,attempt,run,conversation,scope,payload):
        with self.engine.begin() as c:
            row,owner=self.fence._fence(c,run)
            if (str(row['conversation_id'])!=conversation or sorted(row['knowledge_base_scope'])!=scope_data(scope)['kb']
                or sorted(row['document_scope'])!=scope_data(scope)['documents'] or payload['scope']!=scope_data(scope)):
                raise QueryUnderstandDenied('QUERY_SCOPE_MISMATCH')
            if self._events(c,run):raise QueryUnderstandDenied('QUERY_ATTEMPT_ALREADY_CONSUMED')
            self._append(c,run,{'attempt_id':attempt,'purpose':'query_understand','owner':owner,'state':'CLAIMED',**payload})

    def _current(self,c,attempt,run):
        _,owner=self.fence._fence(c,run)
        events=self._events(c,run)
        if not events or events[-1]['attempt_id']!=attempt:raise QueryUnderstandDenied('QUERY_ATTEMPT_MISSING')
        if events[-1]['owner']!=owner:raise QueryUnderstandDenied('QUERY_ATTEMPT_OWNER_MISMATCH')
        return events[-1]

    def reserve(self,attempt,run,reservation):
        with self.engine.begin() as c:
            row=self._current(c,attempt,run)
            if row['state']!='CLAIMED':raise QueryUnderstandDenied('QUERY_ATTEMPT_ALREADY_CONSUMED')
            budget=c.execute(text('SELECT run_id,provider,model_name,purpose,reservation_state FROM model_calls WHERE id=:id'),{'id':reservation}).mappings().one()
            if (str(budget['run_id']),budget['provider'],budget['model_name'],budget['purpose'],budget['reservation_state'])!=(run,row['provider'],row['model'],'query_understand','reserved'):
                raise QueryUnderstandDenied('QUERY_BUDGET_IDENTITY_MISMATCH')
            self._append(c,run,{**row,'state':'RESERVED','reservation_id':str(reservation)})

    def before_send(self,attempt,run,scope):
        with self.engine.begin() as c:
            row=self._current(c,attempt,run)
            if row['state']!='RESERVED' or row['scope']!=scope_data(scope):raise QueryUnderstandDenied('QUERY_SEND_DENIED')
            self._append(c,run,{**row,'state':'DISPATCHING'})

    def finish(self,attempt,run,state,diagnostics):
        if state not in {'COMPLETED','NOT_SENT','UNKNOWN'}:raise QueryUnderstandDenied('QUERY_TERMINAL_INVALID')
        with self.engine.begin() as c:
            row=self._current(c,attempt,run)
            if row['state'] not in {'CLAIMED','RESERVED','DISPATCHING'}:raise QueryUnderstandDenied('QUERY_ATTEMPT_ALREADY_CONSUMED')
            self._append(c,run,{**row,'state':state,'diagnostics':diagnostics})
