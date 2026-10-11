"""Versioned interpretation, never knowledge evidence or a scope grant."""
from dataclasses import dataclass, asdict
from backend.app.ports.context_budget import ContextDenied

class QueryUnderstandDenied(ContextDenied):
    pass

@dataclass(frozen=True)
class QueryTask:
    text: str
    start: int
    end: int
    operation: str

@dataclass(frozen=True)
class QueryUnderstanding:
    q0: str
    needs_clarification: bool
    rewrite_query: str
    intent: str
    tasks: tuple[QueryTask,...]
    history_run_ids: tuple[str,...] = ()
    memory_identities: tuple[tuple[str,int],...] = ()
    checkpoint_identity: str | None = None
    reason: str = 'INDEPENDENT'
    task_status: str = 'COMPLETE'
    model_status: str = 'NOT_CALLED'
    attempt_id: str | None = None
    diagnostics: tuple[tuple[str,object],...] = ()
    referents: tuple[str,...] = ()

    def public(self):
        return {**asdict(self),'schema_version':'query-understand/v1',
            'diagnostics':dict(self.diagnostics)}

    def resolution(self):
        from backend.app.application.follow_up import FollowUpResolution
        used=not self.needs_clarification and self.rewrite_query!=self.q0
        return FollowUpResolution(self.rewrite_query if used else self.q0,used,self.reason,
            clarification_required=self.needs_clarification,query_original=self.q0,
            decision='clarify' if self.needs_clarification else 'resolve' if used else 'independent',
            source_run_ids=self.history_run_ids,referent_quotes=self.referents)
