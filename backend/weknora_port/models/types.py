"""Neutral model types. Semantic port of WeKnora internal/models/api.

Upstream: Tencent/WeKnora@3e8b0bfc (MIT); see NOTICE in this directory.
No application, database, historical grant or role-specific adapter imports.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import Any, AsyncIterator, Mapping, Protocol, Sequence


class ProviderError(Exception):
    """Safe error code only; raw provider messages never escape this boundary."""

    def __init__(self, code: str, *, status_code: int | None = None, sent: bool = False):
        super().__init__(code)
        self.code, self.status_code, self.sent = code, status_code, sent


class ModelCallTimeout(ProviderError):
    def __init__(self, *, sent: bool = False):
        super().__init__("MODEL_CALL_TIMEOUT", sent=sent)


class CallCancelled(ProviderError):
    def __init__(self, *, sent: bool = False):
        super().__init__("MODEL_CALL_CANCELLED", sent=sent)


class RunDeadlineExceeded(ProviderError):
    def __init__(self, *, sent: bool = False):
        super().__init__("RUN_DEADLINE_EXCEEDED", sent=sent)


@dataclass(frozen=True)
class TrustedScope:
    owner_id: str
    knowledge_base_ids: tuple[str, ...]
    document_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CallContext:
    request_id: str
    scope: TrustedScope
    deadline: float | None = None  # absolute asyncio loop.time(), not wall time
    cancelled: asyncio.Event | None = field(default=None, repr=False, compare=False)
    session_id: str = ""
    deadline_kind: str = "model"  # run deadlines must never trigger model upgrade
    # Additional server-resolved derived-content sources; never retrieval scope.
    required_origin_kbs: tuple[str, ...] = ()
    source_facts: Any = field(default=None, repr=False, compare=False)

    def child(self, suffix: str) -> CallContext:
        return replace(self, request_id=f"{self.request_id}/{suffix}")


@dataclass(frozen=True)
class FunctionCall:
    name: str = ""
    arguments: str = ""


@dataclass(frozen=True)
class ToolCall:
    id: str = ""
    type: str = "function"
    function: FunctionCall = field(default_factory=FunctionCall)
    provider_metadata: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str = ""
    parameters: Mapping[str, Any] | None = None
    type: str = "function"


@dataclass(frozen=True)
class Message:
    role: str
    content: str = ""
    multi_content: tuple[Mapping[str, Any], ...] = ()
    images: tuple[str, ...] = ()
    name: str = ""
    tool_call_id: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    reasoning_content: str = ""
    reasoning_metadata: Mapping[str, Any] = field(default_factory=dict, repr=False)
    kind: str = ""  # internal history fields never go on the wire
    turn_id: str = ""
    reasoning_signature: str = field(default="", repr=False)


@dataclass(frozen=True)
class ChatOptions:
    temperature: float = 0
    top_p: float = 0
    seed: int = 0
    max_tokens: int = 0
    max_completion_tokens: int = 0
    frequency_penalty: float = 0
    presence_penalty: float = 0
    thinking: bool | None = None
    reasoning_effort: str = ""
    thinking_budget_tokens: int = 0
    tools: tuple[Tool, ...] = ()
    tool_choice: str = ""
    parallel_tool_calls: bool | None = None
    format: str = ""
    prompt_cache_key: str = ""
    cache_retention: str = ""

    def completion_budget(self) -> int:
        return self.max_completion_tokens if self.max_completion_tokens > 0 else self.max_tokens

    def reasoning(self) -> tuple[str, bool]:
        aliases = {"none": "off", "false": "off", "disabled": "off", "true": "auto",
                   "enabled": "auto", "default": "auto", "on": "auto"}
        value = aliases.get(self.reasoning_effort, self.reasoning_effort)
        if value in ("off", "auto", "minimal", "low", "medium", "high", "xhigh", "max"):
            return value, True
        if self.thinking is not None:
            return ("auto" if self.thinking else "off"), True
        return "", False


@dataclass(frozen=True)
class ChatSettings:
    max_tokens_field: str = "max_completion_tokens"
    thinking_format: str = "none"
    thinking_enabled_value: str = "enabled"
    thinking_always_send: bool = False
    thinking_disable_on_non_stream: bool = False
    thinking_budget_field: str = ""
    thinking_budget_excludes_effort: bool = False
    reasoning_effort_field: str = "reasoning_effort"
    supports_reasoning_effort: bool = False
    supports_developer_role: bool = False
    supports_store: bool = False
    supports_usage_in_streaming: bool = True
    supports_temperature: bool = True
    fixed_temperature: float | None = None
    supports_seed: bool = True
    tool_choice_modes: tuple[str, ...] = ("none", "auto", "required", "function")
    supports_parallel_tool_calls: bool = True
    supports_response_format: bool = True
    supports_multi_content: bool = True
    replay_reasoning_content: bool = True
    prompt_cache_key: bool = False
    cache_control_format: str = ""
    prompt_cache_accounting: bool = False
    tool_call_extra_fields: tuple[str, ...] = ()
    extra_body: Mapping[str, Any] = field(default_factory=dict)
    reasoning_fields: tuple[str, ...] = ("reasoning_content", "reasoning", "reasoning_text")


@dataclass(frozen=True)
class EmbeddingSettings:
    path: str = "/embeddings"
    send_encoding_format: bool = False
    dimensions_field: str = ""
    input_type_field: str = ""
    input_type_values: Mapping[str, str] = field(default_factory=dict)
    truncate_field: str = ""
    truncate_value: str = ""
    max_batch_size: int = 0
    max_input_chars: int = 0
    accepts_truncate_prompt_tokens: bool = False
    truncate_prompt_tokens: int = 0
    request_timeout: float = 60
    extra_body: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RerankSettings:
    path: str = "/rerank"
    send_top_n: bool = False
    send_return_documents: bool = False
    score_scale: str = "probability"
    max_documents: int = 0
    max_query_chars: int = 0
    max_document_chars: int = 0
    max_request_chars: int = 0
    request_timeout: float = 0
    accepts_truncate_prompt_tokens: bool = False
    truncate_prompt_tokens: int = 0
    extra_body: Mapping[str, Any] = field(default_factory=dict)
    truncate: str = ""
    max_concurrency: int = 0
    unsupported_reason: str = ""


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    model_id: str
    base_url: str
    context_window: int = 0
    max_output_tokens: int = 0
    reasoning: bool = False
    supports_vision: bool = False
    chat: ChatSettings = field(default_factory=ChatSettings)
    thinking_levels: Mapping[str, str | None] = field(default_factory=dict)
    embedding: EmbeddingSettings = field(default_factory=EmbeddingSettings)
    reranking: RerankSettings = field(default_factory=RerankSettings)
    dimensions: int = 0
    endpoint_url: str = ""
    query: Mapping[str, str] = field(default_factory=dict)
    custom_headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    requires_auth: bool = True
    api: str = "openai-completions"
    embedding_api: str = "openai-embeddings"
    rerank_api: str = "cohere-rerank"
    auth_style: str = "bearer"
    protocol_settings: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    extra_config: Mapping[str, str] = field(default_factory=dict, repr=False)
    cataloged: bool = False
    model_type: str = "KnowledgeQA"
    supports_dimension_override: bool = False
    source: str = ''

    def __post_init__(self):
        if self.source not in ('','local','remote'):raise ProviderError('MODEL_SOURCE_INVALID')
        if not self.provider or not self.model or not self.model_id:
            raise ProviderError("MODEL_CONFIGURATION_INVALID")
        if any(type(n) is not int or n < 0 for n in (self.context_window, self.max_output_tokens, self.dimensions)):
            raise ProviderError("MODEL_CAPACITY_CONFIGURATION_INVALID")
        if self.chat.max_tokens_field not in ("max_tokens", "max_completion_tokens", ""):
            raise ProviderError("MODEL_PROTOCOL_CONFIGURATION_INVALID")


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    status: str = "UNKNOWN"
    cached_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cache_miss_tokens: int = 0
    cache_reported: bool = False
    cache_status: str = "unreported"
    provider_usage_facts: Any = field(default=None, repr=False)


@dataclass(frozen=True)
class ChatResponse:
    content: str = ""
    reasoning_content: str = ""
    finish_reason: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: TokenUsage = field(default_factory=TokenUsage)
    reasoning_metadata: Mapping[str, Any] = field(default_factory=dict, repr=False)
    model: str = ""  # missing response identity stays missing, never inferred
    reasoning_signature: str = field(default="", repr=False)

    @property
    def truncated(self) -> bool:
        return self.finish_reason == "length"


@dataclass(frozen=True)
class StreamResponse:
    response_type: str = "answer"
    content: str = ""
    done: bool = False
    tool_calls: tuple[ToolCall, ...] = ()
    usage: TokenUsage | None = None
    finish_reason: str = ""
    data: Mapping[str, Any] | None = field(default=None, repr=False)
    is_final: bool = False  # distinguishes protocol Done delta from final usage chunk

    @property
    def truncated(self) -> bool:
        return self.finish_reason == "length"


@dataclass(frozen=True)
class EmbeddingResponse:
    vectors: tuple[tuple[float, ...], ...]
    usage: TokenUsage = field(default_factory=TokenUsage)


@dataclass(frozen=True)
class RerankResult:
    index: int
    score: float
    text: str = ""


@dataclass(frozen=True)
class RerankResponse:
    results: tuple[RerankResult, ...]
    usage: TokenUsage = field(default_factory=TokenUsage)
    score_scale: str = "probability"


@dataclass(frozen=True)
class PreparedCall:
    context: CallContext
    provider: str
    model: str
    model_id: str
    capability: str
    endpoint: str
    wire_sha256: str
    wire_bytes: int
    output_budget: int | None
    bound_facts: Any = field(default=None, repr=False)


class SendGate(Protocol):
    """Application owns durable identity, scope/egress and cost authority.

    authorize must atomically reserve/claim before each actual HTTP send.
    finish must retain unknown occupancy for incomplete/unreported usage.
    This interface cannot modify a prompt, generation budget or routing score.
    """

    async def authorize(self, call: PreparedCall) -> None: ...
    # True acknowledges durable persistence. Legacy gates may return None;
    # bounded local execution must refuse to settle without explicit True.
    async def finish(self, call: PreparedCall, *, outcome: str, usage: TokenUsage) -> bool | None: ...


class ChatModel(Protocol):
    async def chat(self, messages: Sequence[Message], options: ChatOptions | None, context: CallContext) -> ChatResponse: ...
    def chat_stream(self, messages: Sequence[Message], options: ChatOptions | None, context: CallContext) -> AsyncIterator[StreamResponse]: ...


class EmbeddingModel(Protocol):
    async def embed(self, texts: Sequence[str], context: CallContext, *, kind: str = "document") -> EmbeddingResponse: ...


class RerankModel(Protocol):
    async def rerank(self, query: str, documents: Sequence[str], context: CallContext) -> RerankResponse: ...


class VisionModel(Protocol):
    async def vision(self, messages: Sequence[Message], options: ChatOptions | None, context: CallContext) -> ChatResponse: ...
