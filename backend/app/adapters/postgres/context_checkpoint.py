"""PostgreSQL is authoritative. Redis never owns history/checkpoints."""
import hashlib
import json
from sqlalchemy import text
from backend.app.ports.run_lifecycle import current_execution
from backend.app.ports.context_budget import ContextDenied, identity, protocol_messages


def scope_data(scope):
    return {'kb': sorted(scope.knowledge_base_ids), 'documents': sorted(scope.document_ids)}


class PostgresContextStore:
    def __init__(self, engine): self.engine = engine

    def _fence(self, conn, run):
        context = current_execution.get()
        if context is None or context[0].run_id != run or context[2].is_set():
            raise ContextDenied('CONTEXT_EXECUTION_OWNER_REQUIRED')
        r = conn.execute(text('SELECT * FROM rag_runs WHERE id=:run FOR UPDATE'), {'run': run}).mappings().one()
        lease = conn.execute(text('SELECT * FROM rag_run_leases WHERE run_id=:run FOR UPDATE'), {'run': run}).mappings().one()
        if (r['status'] != 'running' or str(lease['owner']) != str(context[0].owner)
                or lease['state'] != 'IN_PROGRESS'):
            raise ContextDenied('CONTEXT_EXECUTION_FENCE_LOST')
        expired = conn.execute(text('SELECT lease_until<=clock_timestamp() FROM rag_run_leases WHERE run_id=:run'), {'run': run}).scalar_one()
        if expired: raise ContextDenied('CONTEXT_EXECUTION_FENCE_LOST')
        return r, str(lease['owner'])

    def claim_compaction(self, attempt, run, conversation, scope, window, covered, kind):
        with self.engine.begin() as c:
            row, owner = self._fence(c, run)
            data = scope_data(scope)
            if (str(row['conversation_id']) != conversation or sorted(row['knowledge_base_scope']) != data['kb'] or sorted(row['document_scope']) != data['documents']):
                raise ContextDenied('CONTEXT_SCOPE_MISMATCH')
            inserted = c.execute(text('''INSERT INTO context_compaction_attempts
                (id,run_id,conversation_id,owner,provider,model,scope,covered,kind,state)
                VALUES (:id,:run,:conversation,:owner,:provider,:model,CAST(:scope AS jsonb),CAST(:covered AS jsonb),:kind,'IN_PROGRESS')
                ON CONFLICT(id) DO NOTHING RETURNING id'''), dict(id=attempt, run=run, conversation=conversation,
                    owner=owner, provider=window.provider, model=window.model, scope=json.dumps(data), covered=json.dumps(covered), kind=kind)).scalar()
            if inserted is None: raise ContextDenied('CONTEXT_COMPACTION_ALREADY_CONSUMED')

    def link_compaction_budget(self, attempt, reservation):
        with self.engine.begin() as c:
            row = c.execute(text('SELECT run_id FROM context_compaction_attempts WHERE id=:id'), {'id': attempt}).scalar_one()
            _, owner = self._fence(c, str(row))
            matched = c.execute(text('''SELECT count(*) FROM context_compaction_attempts a JOIN model_calls b
                ON b.id=:budget AND b.run_id=a.run_id AND b.provider=a.provider AND b.model_name=a.model
                WHERE a.id=:id AND b.purpose='context_compaction' AND b.reservation_state='reserved'
                  AND b.reserved_cost_microunits>0'''),dict(id=attempt,budget=reservation)).scalar_one()
            if matched != 1: raise ContextDenied('CONTEXT_BUDGET_IDENTITY_MISMATCH')
            count = c.execute(text('''UPDATE context_compaction_attempts SET budget_reservation_id=:budget
                WHERE id=:id AND owner=:owner AND state='IN_PROGRESS' AND budget_reservation_id IS NULL'''),
                dict(id=attempt, budget=reservation, owner=owner)).rowcount
            if count != 1: raise ContextDenied('CONTEXT_BUDGET_LINK_DENIED')

    def before_compaction_send(self, attempt, run):
        with self.engine.begin() as c:
            _, owner = self._fence(c, run)
            ok = c.execute(text("SELECT count(*) FROM context_compaction_attempts WHERE id=:id AND run_id=:run AND owner=:owner AND state='IN_PROGRESS' AND budget_reservation_id IS NOT NULL"),dict(id=attempt,run=run,owner=owner)).scalar_one()
            if ok != 1: raise ContextDenied('CONTEXT_SEND_FENCE_DENIED')

    def finish_compaction(self, attempt, state, diagnostics):
        with self.engine.begin() as c:
            run = str(c.execute(text('SELECT run_id FROM context_compaction_attempts WHERE id=:id'), {'id': attempt}).scalar_one())
            _, owner = self._fence(c, run)
            count = c.execute(text("UPDATE context_compaction_attempts SET state=:state,diagnostics=CAST(:diagnostics AS jsonb) WHERE id=:id AND owner=:owner AND state='IN_PROGRESS'"),dict(id=attempt,owner=owner,state=state,diagnostics=json.dumps(diagnostics))).rowcount
            if count != 1: raise ContextDenied('CONTEXT_ATTEMPT_FINISH_DENIED')

    def fail_compaction(self, attempt, state, code):
        # Failure of checkpoint persistence must not erase a completed model
        # call. Record the storage diagnostic separately; no refund or re-send.
        with self.engine.begin() as c:
            run = str(c.execute(text('SELECT run_id FROM context_compaction_attempts WHERE id=:id'), {'id': attempt}).scalar_one())
            context = current_execution.get()
            if context is None or context[0].run_id != run:
                raise ContextDenied('CONTEXT_EXECUTION_OWNER_REQUIRED')
            c.execute(text("UPDATE context_compaction_attempts SET state=CASE WHEN state='COMPLETED' THEN state ELSE :state END,diagnostics=diagnostics||CAST(:d AS jsonb) WHERE id=:id AND owner=:owner"),dict(id=attempt,owner=context[0].owner,state=state,d=json.dumps({'error_code':code,'fee':'UNKNOWN' if state=='UNKNOWN' else 'NOT_SENT'})))

    def save_checkpoint(self, attempt, conversation, scope, run, covered, summary, kind):
        with self.engine.begin() as c:
            self._fence(c, run)
            a = c.execute(text('SELECT * FROM context_compaction_attempts WHERE id=:id'),dict(id=attempt)).mappings().one()
            if (a['state'] != 'COMPLETED' or a['covered'] != covered or a['scope'] != scope_data(scope)
                    or str(a['conversation_id']) != conversation or str(a['run_id']) != run or a['kind'] != kind):
                raise ContextDenied('CONTEXT_CHECKPOINT_BINDING_INVALID')
            c.execute(text('''INSERT INTO context_checkpoints (attempt_id,conversation_id,run_id,scope,covered,kind,summary,summary_sha256)
                VALUES (:id,:conversation,:run,CAST(:scope AS jsonb),CAST(:covered AS jsonb),:kind,:summary,:sha)'''),
                dict(id=attempt,conversation=conversation,run=run,scope=json.dumps(scope_data(scope)),covered=json.dumps(covered),kind=kind,summary=summary,sha=hashlib.sha256(summary.encode()).hexdigest()))

    def load_checkpoint(self, conversation, scope, run):
        with self.engine.connect() as c:
            row = c.execute(text('''SELECT cp.* FROM context_checkpoints cp
                JOIN context_compaction_attempts a ON a.id=cp.attempt_id AND a.state='COMPLETED'
                  AND a.covered=cp.covered AND a.scope=cp.scope AND a.kind=cp.kind
                JOIN rag_runs source ON source.id=cp.run_id
                JOIN rag_runs current ON current.id=:run
                JOIN conversations cv ON cv.id=current.conversation_id AND cv.deleted_at IS NULL
                WHERE cp.conversation_id=:conversation AND cp.kind='SESSION'
                  AND cp.scope=CAST(:scope AS jsonb) AND cp.schema_version='context-checkpoint/v1'
                  AND current.conversation_id=cp.conversation_id
                  AND current.knowledge_base_scope=cv.knowledge_base_scope AND current.document_scope=cv.document_scope
                  AND current.knowledge_base_scope @> CAST(:kb AS jsonb) AND current.knowledge_base_scope <@ CAST(:kb AS jsonb)
                  AND current.document_scope @> CAST(:docs AS jsonb) AND current.document_scope <@ CAST(:docs AS jsonb)
                  AND (source.id=current.id OR (source.status IN ('completed','failed','cancelled') AND source.completed_at<=current.created_at))
                ORDER BY cp.created_at DESC,cp.attempt_id DESC LIMIT 1'''),
                dict(run=run,conversation=conversation,scope=json.dumps(scope_data(scope)),kb=json.dumps(list(scope.knowledge_base_ids)),docs=json.dumps(list(scope.document_ids)))).mappings().first()
        if row is None: return None
        if hashlib.sha256(row['summary'].encode()).hexdigest() != row['summary_sha256']:
            raise ContextDenied('CONTEXT_CHECKPOINT_INTEGRITY_INVALID')
        return dict(row)

    def save_protocol(self, run, messages):
        messages = protocol_messages(messages)
        if any(m['role'] == 'system' for m in messages): raise ContextDenied('CONTEXT_PROTOCOL_ROLE_INVALID')
        with self.engine.begin() as c:
            self._fence(c, run)
            previous = c.execute(text('SELECT messages,sha256 FROM context_run_protocol WHERE run_id=:run'),dict(run=run)).mappings().first()
            if previous:
                if identity(previous['messages']) != previous['sha256']:
                    raise ContextDenied('CONTEXT_PROTOCOL_INTEGRITY_INVALID')
                # Append only new complete tool groups; compaction cannot erase
                # earlier original reasoning/tool payloads. Repeated IDs must be
                # byte-equivalent, never silently replaced.
                old = previous['messages']
                old_calls = {x['id']: m for m in old for x in m.get('tool_calls', ())}
                new = []
                duplicate_group = False
                for m in messages:
                    if m['role'] != 'tool':
                        calls = m.get('tool_calls', [])
                        duplicate_group = bool(calls) and all(x['id'] in old_calls for x in calls)
                        if duplicate_group and any(old_calls[x['id']] != m for x in calls):
                            raise ContextDenied('CONTEXT_PROTOCOL_ID_CONFLICT')
                    if duplicate_group:
                        if m['role'] == 'tool' and m not in old:
                            raise ContextDenied('CONTEXT_PROTOCOL_ID_CONFLICT')
                    elif m not in old:
                        new.append(m)
                messages = old + new
                protocol_messages(messages)
            c.execute(text('''INSERT INTO context_run_protocol(run_id,messages,sha256) VALUES (:run,CAST(:messages AS jsonb),:sha)
                ON CONFLICT(run_id) DO UPDATE SET messages=EXCLUDED.messages,sha256=EXCLUDED.sha256'''),dict(run=run,messages=json.dumps(messages),sha=identity(messages)))
