"""Immutable P5 request/attempt/finalization contracts; safety adapters injected."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, Any
from ..models.types import ChatOptions, TokenUsage, CallContext, ProviderError
from ..retrieval.context import FinalizedContext
from .context import Evidence, History, DEFAULT_SYSTEM_PROMPT
from .router import RouterPolicy, ValidatedRewrite

class GenerationError(ProviderError):
    pass

class GenerationReplay(GenerationError):

    def __init__(self):
        super().__init__('GENERATION_REPLAY_ATTACH_EXISTING')

@dataclass(frozen=True)
class GenerationRequest:
    run_id: str
    session_id: str
    message_id: str
    q0: str = field(repr=False)
    finalized: FinalizedContext = field(repr=False)
    evidence: tuple[Evidence, ...] = field(default=(), repr=False)
    history: tuple[History, ...] = field(default=(), repr=False)
    memory_prompt: str = field(default='', repr=False)
    language: str = 'Chinese'
    system_prompt: str = field(default=DEFAULT_SYSTEM_PROMPT, repr=False)
    is_fallback: bool = False
    fixed_fallback: str | None = field(default=None,repr=False)
    fallback_response: str = field(default='Sorry, I am unable to answer this question.',repr=False)
    supports_vision: bool = False
    citations_enabled: bool = False
    faq_priority_enabled: bool = False
    options: ChatOptions = field(default_factory=lambda: ChatOptions(temperature=0.3, max_completion_tokens=1024))
    policy: RouterPolicy = field(default_factory=RouterPolicy)
    understanding: ValidatedRewrite | None = field(default=None, repr=False)
    rendered_at: datetime | None = field(default=None,repr=False)

    def __post_init__(self):
        if self.rendered_at is not None and not isinstance(self.rendered_at,datetime):
            raise GenerationError('GENERATION_RENDER_TIME_INVALID')
        if not all((isinstance(x, str) and x for x in (self.run_id, self.session_id, self.message_id, self.q0))):
            raise GenerationError('GENERATION_IDENTITY_REQUIRED')
        if not isinstance(self.finalized, FinalizedContext):
            raise GenerationError('FINALIZED_CONTEXT_REQUIRED')
        if not all(isinstance(e,Evidence) for e in self.evidence) or not all(isinstance(h,History) for h in self.history):
            raise GenerationError('GENERATION_INPUT_NOT_IMMUTABLE')
        if not isinstance(self.evidence, tuple) or not isinstance(self.history, tuple):
            raise GenerationError('GENERATION_INPUT_NOT_IMMUTABLE')
        if self.understanding is not None and self.understanding.q0 != self.q0:
            raise GenerationError('ROUTER_Q0_MISMATCH')

@dataclass(frozen=True)
class RoleBinding:
    role: str
    provider: str
    model_requested: str
    endpoint: str
    model: Any = field(repr=False, compare=False)

@dataclass(frozen=True)
class AttemptRecord:
    attempt_id: str
    ordinal: int
    role: str
    provider: str
    model_requested: str
    endpoint: str
    envelope_sha256: str
    prompt_sha256: str
    context_sha256: str
    model_reported: str | None = None
    status: str = 'STARTED'
    finish_reason: str = ''
    usage: TokenUsage = field(default_factory=TokenUsage)
    objective: tuple[str, ...] = ()
    upgrade_reason: str | None = None
    cost_status: str = 'UNKNOWN'
    error_code: str | None = None
    invalid_handle_count: int = 0

@dataclass(frozen=True)
class GenerationResult:
    answer: str = field(repr=False)
    thinking: str = field(repr=False)
    references: tuple[dict[str, Any], ...] = field(repr=False)
    selected_role: str
    selected_attempt_id: str
    attempts: tuple[AttemptRecord, ...]
    route: dict[str, Any] | None
    usage: TokenUsage
    finish_reason: str
    truncated: bool
    upgraded: bool
    prompt_sha256: str
    context_sha256: str

class AttemptLifecycle(Protocol):

    async def claim_run(self, run_id: str, payload_sha256: str, context: CallContext) -> None:
        ...

    async def begin_attempt(self, record: AttemptRecord, context: CallContext) -> None:
        ...

    async def finish_attempt(self, record: AttemptRecord, context: CallContext) -> None:
        ...

class ClosedLifecycle:

    async def claim_run(self, *args):
        raise GenerationError('GENERATION_LIFECYCLE_UNAVAILABLE')

    async def begin_attempt(self, *args):
        raise GenerationError('GENERATION_LIFECYCLE_UNAVAILABLE')

    async def finish_attempt(self, *args):
        raise GenerationError('GENERATION_LIFECYCLE_UNAVAILABLE')

@dataclass(frozen=True)
class TokenEstimate:
    tokens: int | None = None
    estimator: str = 'UNKNOWN'

class PromptTokenEstimator(Protocol):

    def estimate(self, messages: tuple, options: ChatOptions) -> TokenEstimate:
        ...
