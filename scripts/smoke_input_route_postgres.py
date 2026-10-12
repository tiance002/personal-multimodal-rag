"""Real isolated PG attempt uniqueness, with zero model/network dispatches."""
import os
import json
import uuid
from threading import Event
from types import SimpleNamespace
from sqlalchemy import create_engine, text
from backend.app.adapters.postgres.run_lifecycle import PostgresRunLifecycle
from backend.app.ports.run_lifecycle import current_execution, execution_owner, LifecycleDenied
from backend.app.ports.session_attempts import request_identity
from backend.app.adapters.postgres.knowledge_repository import PostgresKnowledgeRepository
from backend.app.adapters.storage import ContentAddressedStorage
from pathlib import Path


def main():
    url=os.environ['RAG_DATABASE_URL']
    if not url.endswith('/rag_restore'):raise RuntimeError('ISOLATED_DB_REQUIRED')
    engine=create_engine(url)
    repo=PostgresRunLifecycle(engine)
    with engine.begin() as c:
        session=str(uuid.uuid4())
        c.execute(text("INSERT INTO conversations(id,title,knowledge_base_scope,document_scope) VALUES (:id,'SIMULATED','[]','[]')"),{'id':session})
    store=PostgresKnowledgeRepository(engine,ContentAddressedStorage(Path('/tmp/SIMULATED-input-route-history')))
    for kind in ('rag','chat','failed'):
        fixture=store.create_run(session,[],[],'SIMULATED-'+kind)
        store.append_message(session,'user','SIMULATED-'+kind,run_id=fixture)
        if kind!='failed':
            store.append_message(session,'assistant','SIMULATED reply',run_id=fixture)
            store.append_event(fixture,'run.metrics',{'execution_mode':kind})
            store.append_event(fixture,'answer.completed',{'error_code':None,'citations':[]})
        store.complete_run(fixture,'failed' if kind=='failed' else 'completed','SIMULATED_FAILURE' if kind=='failed' else None)
    claim=repo.claim_run(session,[],[],'PUBLIC SYNTHETIC','quick',str(uuid.uuid4()))
    context=current_execution.set((claim,SimpleNamespace(repository=repo),Event(),session))
    owner=execution_owner.set((claim.run_id,claim.owner))
    try:
        rows=[]
        for purpose,role,ordinal in [('input.route','chat_cheap',1),('rag','chat_cheap',1),('rag','chat_expensive',2)]:
            attempt=request_identity(claim.run_id,'input.route' if purpose=='input.route' else 'quick.router.'+role,ordinal)
            args=dict(run_id=claim.run_id,owner=claim.owner,attempt_id=attempt,role=role,ordinal=ordinal,
                envelope_hash='a'*64,provider='SIMULATED',model='SIMULATED')
            if purpose!='rag':args['purpose']=purpose
            repo.claim_attempt(**args)
            try:repo.claim_attempt(**args)
            except LifecycleDenied:pass
            else:raise AssertionError('DUPLICATE_ACCEPTED')
            repo.finish_attempt(claim.run_id,claim.owner,attempt,'COMPLETED',{'purpose':purpose})
            rows.append(attempt)
        with engine.connect() as c:
            actual=c.execute(text('SELECT role,ordinal,purpose,state FROM rag_model_attempts WHERE run_id=:id ORDER BY purpose,ordinal'),{'id':claim.run_id}).mappings().all()
        assert len(actual)==3 and all(r['state']=='COMPLETED' for r in actual)
        for purpose in ('follow_up','context'):
            history=store.completed_history_context(session,[],[],current_run_id=claim.run_id,purpose=purpose)
            assert len(history['turns'])==1 and history['turns'][0]['q0']=='SIMULATED-rag'
        assert store.last_completed_question_context(session,[],[])['q0']=='SIMULATED-rag'
        print(json.dumps({'status':'CHECKS_PASSED','attempts':3,'duplicate_denials':3,'provider_calls':0,
            'history_readers_checked':3,'chat_and_failed_excluded':True}))
    finally:
        execution_owner.reset(owner);current_execution.reset(context)
        repo.fail_run(claim.run_id,claim.owner)
        repo.finish_run(claim.run_id,claim.owner)
        engine.dispose()

if __name__=='__main__':main()
