"""SIMULATED SQLite unit fixture; never a production lifecycle fallback.

Final safety evidence uses real isolated PostgreSQL and Redis, not this helper.
"""
from contextlib import contextmanager
from types import SimpleNamespace
from threading import Lock, Event
import sqlite3
from backend.app.ports.run_lifecycle import current_execution, execution_owner, RunClaim


class SimulatedAttemptRepository:
    def __init__(self):
        self.connection=sqlite3.connect(':memory:',check_same_thread=False)
        self.lock=Lock()
        self.connection.executescript('CREATE TABLE runs(id TEXT PRIMARY KEY); CREATE TABLE attempts(id TEXT PRIMARY KEY,run TEXT,role TEXT,ordinal INTEGER,state TEXT,data TEXT, UNIQUE(run,role), UNIQUE(run,ordinal));')

    def claim_run(self, identity):
        with self.lock:
            try:self.connection.execute('INSERT INTO runs VALUES (?)',(identity,));self.connection.commit();return True
            except sqlite3.IntegrityError:return False

    def claim_attempt(self,**kw):
        with self.lock:
            self.connection.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?)',(kw['attempt_id'],kw['run_id'],kw['role'],kw['ordinal'],'IN_PROGRESS','{}'))
            self.connection.commit()

    def finish_attempt(self, run, owner, attempt, state, data):
        import json
        with self.lock:
            self.connection.execute('UPDATE attempts SET state=?,data=? WHERE id=?',(state,json.dumps(data),attempt));self.connection.commit()

    def link_budget(self,*args):pass


def install_simulated_lifecycle(chain):
    repository=SimulatedAttemptRepository();original=chain.invoke
    streams=SimpleNamespace(renew_live_run=lambda *args:True)
    lifecycle=SimpleNamespace(repository=repository,streams=streams)
    def invoke(question,scope,**kw):
        # Real integration installs the real lifecycle through AnswerService.
        if current_execution.get() is not None:
            return original(question,scope,**kw)
        settings=kw.get('settings');policy=getattr(settings,'router_policy',None) or chain.router_policy
        identity=kw.get('run_id')
        if policy.mode=='OFF' or identity is None:
            return original(question,scope,**kw)
        if not repository.claim_run(identity):
            return chain._error_result(identity,chain.knowledge_gateway.plan(question),'DUPLICATE_REQUEST')
        claim=RunClaim(identity,'SIMULATED_OWNER',True,'IN_PROGRESS')
        token=current_execution.set((claim,lifecycle,Event(),'SIMULATED_SESSION'))
        owner_token=execution_owner.set((identity,claim.owner))
        try:return original(question,scope,**kw)
        finally:current_execution.reset(token);execution_owner.reset(owner_token)
    chain.invoke=invoke
    return repository
