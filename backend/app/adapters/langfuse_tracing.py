from __future__ import annotations

import base64
import binascii
import logging
import os
import re
import sys
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_INPUT_ATTRIBUTES = frozenset(
    {
        "langfuse.observation.input",
        "langfuse.trace.input",
        "ai.prompt.messages",
        "ai.prompt",
        "ai.toolCall.args",
        "gcp.vertex.agent.llm_request",
        "gcp.vertex.agent.tool_call_args",
        "prompt",
        "lk.input_text",
        "lk.user_transcript",
        "lk.chat_ctx",
        "lk.user_input",
        "mlflow.spanInputs",
        "traceloop.entity.input",
        "input.value",
        "pydantic_ai.all_messages",
        "gen_ai.system_instructions",
        "input",
        "gen_ai.input.messages",
        "gen_ai.tool.call.arguments",
        "genkit:input",
        "tool_arguments",
    }
)
_OUTPUT_ATTRIBUTES = frozenset(
    {
        "langfuse.observation.output",
        "langfuse.trace.output",
        "ai.response.text",
        "ai.result.text",
        "ai.toolCall.result",
        "ai.response.object",
        "ai.result.object",
        "ai.response.toolCalls",
        "ai.result.toolCalls",
        "gcp.vertex.agent.llm_response",
        "gcp.vertex.agent.tool_response",
        "all_messages_events",
        "lk.function_tool.output",
        "lk.response.text",
        "mlflow.spanOutputs",
        "traceloop.entity.output",
        "output.value",
        "final_result",
        "output",
        "gen_ai.output.messages",
        "gen_ai.tool.call.result",
        "genkit:output",
        "tool_response",
    }
)
_CONTENT_PREFIXES = (
    "gen_ai.prompt",
    "llm.input_messages",
    "gen_ai.completion",
    "llm.output_messages",
)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_AUTH_VALUE = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_LANGFUSE_SECRET = re.compile(r"\bsk-lf-[A-Za-z0-9_-]+\b")


@dataclass
class LangfuseRun:
    """Per-answer trace handle. Safe summaries remain available without content capture."""

    span: Any
    run_id: str
    mode: str
    capture_content: bool

    def finish(
        self,
        *,
        answer: str,
        citations: tuple[str, ...],
        error_code: str | None,
        run_trace: dict[str, Any] | None = None,
    ) -> None:
        metadata: dict[str, Any] = {
            "run_id": self.run_id,
            "mode": self.mode,
            "status": "failed" if error_code else "completed",
            "error_code": error_code,
            "answer_characters": len(answer),
            "citation_count": len(citations),
        }
        if run_trace:
            for key in ("model_calls", "cost_microunits", "degradation_code", "reason_codes"):
                if key in run_trace:
                    metadata[key] = run_trace[key]
            steps = run_trace.get("steps")
            if isinstance(steps, list):
                metadata["tool_step_count"] = len(steps)

        fields: dict[str, Any] = {"metadata": metadata}
        if self.capture_content and not error_code:
            fields["output"] = {"answer": answer, "citations": list(citations)}
        if error_code:
            fields["level"] = "ERROR"
            fields["status_message"] = error_code
        try:
            self.span.update(**fields)
        except Exception as exc:  # Observability must not fail an answer.
            logger.warning("Langfuse trace update skipped (%s)", type(exc).__name__)


class LangfuseObservability:
    """Fail-closed, opt-in Langfuse adapter for a local-first RAG application."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        cloud_egress_enabled: bool = False,
        capture_content: bool = False,
        base_url: str = "https://us.cloud.langfuse.com",
    ) -> None:
        self.enabled = enabled
        self.cloud_egress_enabled = cloud_egress_enabled
        self.capture_content = capture_content
        self.base_url = base_url.rstrip("/")
        self._client: Any | None = None
        self._public_key: str | None = None
        self._warned: set[str] = set()

    @contextmanager
    def trace(
        self,
        *,
        run_id: str,
        conversation_id: str,
        mode: str,
        question: str,
        knowledge_base_ids: list[str],
        cloud_allowed_by_kb: dict[str, bool],
    ) -> Iterator[LangfuseRun | None]:
        if (
            not self.enabled
            or not self.cloud_egress_enabled
            or not knowledge_base_ids
            or any(not cloud_allowed_by_kb.get(kb_id, False) for kb_id in knowledge_base_ids)
        ):
            yield None
            return

        client = self._get_client()
        if client is None:
            yield None
            return

        trace_name = f"rag-{mode}-answer"
        metadata: dict[str, Any] = {
            "run_id": run_id,
            "mode": mode,
            "knowledge_base_count": len(knowledge_base_ids),
            "question_characters": len(question),
            "content_capture": self.capture_content,
        }
        params: dict[str, Any] = {"as_type": "span", "name": trace_name, "metadata": metadata}
        if self.capture_content:
            params["input"] = {"question": question}

        stack = ExitStack()
        try:
            root = stack.enter_context(client.start_as_current_observation(**params))
            from langfuse import propagate_attributes

            stack.enter_context(
                propagate_attributes(
                    session_id=conversation_id,
                    trace_name=trace_name,
                    tags=["personal-rag", f"mode:{mode}"],
                )
            )
        except Exception as exc:
            self._close_stack(stack)
            self._warn_once("trace-start", exc)
            yield None
            return

        try:
            yield LangfuseRun(root, run_id, mode, self.capture_content)
        except BaseException:
            self._close_stack(stack, sys.exc_info())
            raise
        else:
            self._close_stack(stack)

    @contextmanager
    def observation(
        self,
        *,
        name: str,
        as_type: str = "span",
        model: str | None = None,
        input: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[Any | None]:
        client = self._active_client()
        if client is None:
            yield None
            return

        params: dict[str, Any] = {"as_type": as_type, "name": name}
        if model:
            params["model"] = model
        if metadata:
            params["metadata"] = metadata
        if self.capture_content and input is not None:
            params["input"] = input

        stack = ExitStack()
        try:
            observation = stack.enter_context(client.start_as_current_observation(**params))
        except Exception as exc:
            self._close_stack(stack)
            self._warn_once("observation-start", exc)
            yield None
            return

        try:
            yield observation
        except BaseException:
            self._close_stack(stack, sys.exc_info())
            raise
        else:
            self._close_stack(stack)

    def langchain_callback(self) -> Any | None:
        if self._active_client() is None or self._public_key is None:
            return None
        try:
            from langfuse.langchain import CallbackHandler

            return CallbackHandler(public_key=self._public_key)
        except Exception as exc:
            self._warn_once("langchain-callback", exc)
            return None

    def shutdown(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.shutdown()
        except Exception as exc:
            logger.warning("Langfuse shutdown skipped (%s)", type(exc).__name__)

    def _active_client(self) -> Any | None:
        client = self._client
        if client is None:
            return None
        try:
            return client if client.get_current_trace_id() is not None else None
        except Exception:
            return None

    def _get_client(self) -> Any | None:
        if self._client is not None:
            return self._client

        public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
        secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
        if not (public_key and secret_key):
            public_key, secret_key = self._credentials_from_authorization()
        if not (public_key and secret_key):
            self._warn_once("credentials", None)
            return None

        try:
            from langfuse import Langfuse

            self._client = Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                base_url=self.base_url,
                mask_otel_spans=self._mask_otel_spans,
            )
            self._public_key = public_key
            return self._client
        except Exception as exc:
            self._warn_once("client-init", exc)
            return None

    def _credentials_from_authorization(self) -> tuple[str | None, str | None]:
        authorization = os.getenv("LANGFUSE_AUTHORIZATION", "").strip()
        try:
            scheme, encoded = authorization.split(None, 1)
            if scheme.lower() != "basic":
                return None, None
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
            public_key, secret_key = decoded.split(":", 1)
            return (public_key, secret_key) if public_key and secret_key else (None, None)
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return None, None

    def _mask_otel_spans(self, *, params: Any) -> Any:
        from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

        patches: dict[Any, Any] = {}
        content_keys = _INPUT_ATTRIBUTES | _OUTPUT_ATTRIBUTES
        for identifier, span in params.spans.items():
            if not self.capture_content:
                delete_attributes = tuple(
                    key
                    for key in span.attributes
                    if key in content_keys or key.startswith(_CONTENT_PREFIXES)
                )
                if delete_attributes:
                    patches[identifier] = OtelSpanPatch(delete_attributes=delete_attributes)
                continue

            replacements: dict[str, Any] = {}
            for key, value in span.attributes.items():
                if isinstance(value, str):
                    masked = self._redact(value)
                    if masked != value:
                        replacements[key] = masked
                elif isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
                    masked_items = [self._redact(item) for item in value]
                    if list(value) != masked_items:
                        replacements[key] = masked_items
            if replacements:
                patches[identifier] = OtelSpanPatch(set_attributes=replacements)

        return MaskOtelSpansResult(span_patches=patches) if patches else None

    @staticmethod
    def _redact(value: str) -> str:
        value = _EMAIL.sub("[REDACTED EMAIL]", value)
        value = _AUTH_VALUE.sub(lambda match: f"{match.group(1)} [REDACTED]", value)
        return _LANGFUSE_SECRET.sub("[REDACTED LANGFUSE SECRET]", value)

    def _warn_once(self, key: str, exc: Exception | None) -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        if key == "credentials":
            logger.warning("Langfuse tracing is enabled but credentials are missing or invalid; tracing skipped")
        else:
            logger.warning("Langfuse tracing operation skipped (%s)", type(exc).__name__ if exc else key)

    def _close_stack(self, stack: ExitStack, exc_info: tuple[Any, Any, Any] | None = None) -> None:
        try:
            if exc_info is None:
                stack.close()
            else:
                stack.__exit__(*exc_info)
        except Exception as exc:
            self._warn_once("span-close", exc)


__all__ = ["LangfuseObservability", "LangfuseRun"]
