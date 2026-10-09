"""Current-run context and heartbeat; no process-local historical identity sets."""
from contextlib import contextmanager
from threading import Event, Thread
from backend.app.ports.run_lifecycle import execution_owner, LifecycleDenied, current_execution


def read_committed_events(read_run, streams, store, run_id, session, kbs, docs, after_seq):
    saved=read_run(run_id,session,kbs,docs)
    events=store.list_events(run_id,after_seq)
    terminal={'completed','failed','cancelled'}
    # A final TX can commit between the status read and the event read. Re-read
    # before publishing its terminal event, so reconnect gets committed text.
    if saved['status'] not in terminal and any(e['event'] in {'answer.completed','run.failed'} for e in events):
        saved=read_run(run_id,session,kbs,docs)
    events=[e for e in events if (e['event']!='answer.completed' or saved['status']=='completed')
            and (e['event']!='run.failed' or saved['status'] in {'failed','cancelled'})]
    if streams is not None:
        try:
            cached=streams.get_events(session,run_id,after_seq)
            projection=[{'seq':e['seq'],'event':e['event'],'data':e['data']} for e in events]
            if cached==projection:events=cached
        except Exception:pass
        for event in events:
            try:streams.append_event(session,run_id,event)
            except Exception:break
    for event in events:
        if event['event'] in {'answer.completed','run.failed'}:
            event['data']={**event['data'],'answer':saved['answer'],'citations':list(saved['citations'])}
    return events,saved


class RunLifecycle:
    def __init__(self, repository, streams, event_store=None):
        self.repository, self.streams = repository, streams
        self.event_store = event_store
        if event_store is not None:
            event_store.event_sink = self.cache_committed_event

    def cache_committed_event(self, session, run_id, event):
        try:
            self.streams.append_event(session,run_id,event)
        except Exception:
            # The cache is disposable. PG commit and billing stay authoritative.
            pass

    def cache_terminal_events(self, session, run_id):
        if self.event_store is not None:
            try:
                for event in self.event_store.list_events(run_id):
                    self.cache_committed_event(session,run_id,event)
            except Exception:
                pass

    def events(self, store, run_id, session, kbs, docs, after_seq):
        return read_committed_events(self.repository.read_run,self.streams,store,run_id,session,kbs,docs,after_seq)

    def cleanup_terminal(self, run_id):
        identity = self.repository.cleanup_identity(run_id)
        if identity is None:
            return
        session, owner = identity
        self.repository.finish_run(run_id,owner)
        self.cache_terminal_events(session,run_id)
        try:
            self.streams.clear_live_run(session,run_id,owner)
            marker = self.streams.get_live_run(session)
            if marker != {'run_id':run_id,'owner':owner}:
                self.repository.cleanup_done(run_id,owner)
        except Exception:
            pass

    @contextmanager
    def executing(self, claim, session):
        stop, lost = Event(), Event()
        try:
            if not self.streams.set_live_run(session,claim.run_id,claim.owner):
                raise LifecycleDenied('SESSION_RUN_IN_PROGRESS')
        except Exception:
            self.repository.finish_run(claim.run_id,claim.owner,not_sent=True)
            self.cleanup_terminal(claim.run_id)
            raise LifecycleDenied('STREAM_ADMISSION_DENIED') from None
        owner_token = execution_owner.set((claim.run_id,claim.owner))
        context_token = current_execution.set((claim,self,lost,session))
        def heartbeat():
            while not stop.wait(min(self.repository.lease_seconds,self.streams.live_ttl)/3):
                try:
                    if not self.streams.renew_live_run(session,claim.run_id,claim.owner):
                        raise LifecycleDenied('RUN_LEASE_LOST')
                    self.repository.renew(claim.run_id,claim.owner)
                except Exception:
                    lost.set()
                    return
        thread = Thread(target=heartbeat, name='rag-live-run', daemon=True)
        thread.start()
        try:
            yield
        except Exception:
            try:
                self.repository.fail_run(claim.run_id,claim.owner)
            except Exception:
                raise LifecycleDenied('RUN_FAILURE_PERSISTENCE_FAILED') from None
            raise
        finally:
            stop.set(); thread.join(timeout=5)
            current_execution.reset(context_token); execution_owner.reset(owner_token)
            # PG facts first. Any failure retains cleanup_pending and does not resend.
            self.repository.finish_run(claim.run_id,claim.owner)
            self.cache_terminal_events(session,claim.run_id)
            try:
                cleared = self.streams.clear_live_run(session,claim.run_id,claim.owner)
                marker = None if cleared else self.streams.get_live_run(session)
                if cleared or marker is None or marker != {'run_id':claim.run_id,'owner':claim.owner}:
                    self.repository.cleanup_done(claim.run_id,claim.owner)
            except Exception:
                pass

    @staticmethod
    def assert_current():
        context = current_execution.get()
        if context is None or context[2].is_set():
            raise LifecycleDenied('RUN_LIFECYCLE_UNAVAILABLE')
        return context
