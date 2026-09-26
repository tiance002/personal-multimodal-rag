from __future__ import annotations

import json
import time
from contextlib import nullcontext
from collections.abc import Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from backend.app.ports.providers import EmbeddingResult, ProviderUnavailable, QueryGatewayResult


class OllamaGateway:
    def __init__(
        self,
        base_url: str,
        chat_model: str,
        embedding_model: str,
        *,
        observability: Any | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.chat_model = chat_model
        self.embedding_model = embedding_model
        self.observability = observability

    def _post(self, path: str, payload: dict[str, Any], timeout_seconds: float) -> tuple[dict[str, Any], float]:
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise ProviderUnavailable(f"ollama request failed: {type(exc).__name__}") from exc
        if not isinstance(body, dict):
            raise ProviderUnavailable("ollama returned a non-object response")
        return body, round((time.perf_counter() - started) * 1000, 1)

    def query_expand(self, question: str, timeout_seconds: float) -> QueryGatewayResult:
        with self._observe(
            "ollama-query-expansion",
            as_type="generation",
            model=self.chat_model,
            input={"question": question},
        ) as observation:
            response, latency_ms = self._post(
                "/api/chat",
                {
                    "model": self.chat_model,
                    "messages": [{"role": "user", "content": f"Return JSON {{\"expansions\":[string]}} for this query. Keep it short and do not answer it: {question}"}],
                    "stream": False,
                    "think": False,
                    "format": "json",
                    "options": {"temperature": 0, "num_predict": 96},
                },
                timeout_seconds,
            )
            content = response.get("message", {}).get("content")
            try:
                decoded = json.loads(content) if isinstance(content, str) else content
                expansions = decoded.get("expansions", []) if isinstance(decoded, dict) else []
                if not isinstance(expansions, list) or not all(isinstance(item, str) for item in expansions):
                    raise ValueError("invalid expansions")
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ProviderUnavailable("ollama query expansion schema invalid") from exc
            if observation is not None:
                fields: dict[str, Any] = {
                    "metadata": {"latency_ms": latency_ms, "expansion_count": len(expansions)},
                }
                if self._captures_content():
                    fields["output"] = {"expansions": expansions[:8]}
                usage = self._usage_details(response)
                if usage:
                    fields["usage_details"] = usage
                observation.update(**fields)
            return QueryGatewayResult(tuple(expansions[:8]))

    def embed(self, texts: Sequence[str], timeout_seconds: float) -> EmbeddingResult:
        input_texts = list(texts)
        with self._observe(
            "ollama-embedding",
            as_type="embedding",
            model=self.embedding_model,
            input={"texts": input_texts},
            metadata={"input_count": len(input_texts)},
        ) as observation:
            response, latency_ms = self._post(
                "/api/embed",
                {"model": self.embedding_model, "input": input_texts},
                timeout_seconds,
            )
            vectors = response.get("embeddings")
            if not isinstance(vectors, list) or not all(isinstance(vector, list) for vector in vectors):
                raise ProviderUnavailable("ollama embedding response schema invalid")
            dimensions = len(vectors[0]) if vectors else 0
            if dimensions != 1024:
                raise ProviderUnavailable(f"embedding dimension mismatch: {dimensions}")
            if observation is not None:
                observation.update(
                    output={"input_count": len(input_texts), "dimensions": dimensions},
                    metadata={"latency_ms": latency_ms, "input_count": len(input_texts), "dimensions": dimensions},
                )
            return EmbeddingResult(vectors=vectors, model=self.embedding_model, dimensions=dimensions, latency_ms=latency_ms)

    def answer(self, prompt: str, timeout_seconds: float) -> str:
        with self._observe(
            "ollama-answer-generation",
            as_type="generation",
            model=self.chat_model,
            input={"prompt": prompt},
        ) as observation:
            response, latency_ms = self._post(
                "/api/chat",
                {
                    "model": self.chat_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0, "num_predict": 512},
                },
                timeout_seconds,
            )
            content = response.get("message", {}).get("content")
            if not isinstance(content, str) or not content.strip():
                raise ProviderUnavailable("ollama answer was empty")
            answer = content.strip()
            if observation is not None:
                fields = {"metadata": {"latency_ms": latency_ms, "answer_characters": len(answer)}}
                if self._captures_content():
                    fields["output"] = {"answer": answer}
                usage = self._usage_details(response)
                if usage:
                    fields["usage_details"] = usage
                observation.update(**fields)
            return answer

    def _observe(self, name: str, **kwargs: Any):
        if self.observability is None:
            return nullcontext(None)
        return self.observability.observation(name=name, **kwargs)

    def _captures_content(self) -> bool:
        return bool(getattr(self.observability, "capture_content", False))

    @staticmethod
    def _usage_details(response: dict[str, Any]) -> dict[str, int]:
        details: dict[str, int] = {}
        if isinstance(response.get("prompt_eval_count"), int):
            details["input"] = response["prompt_eval_count"]
        if isinstance(response.get("eval_count"), int):
            details["output"] = response["eval_count"]
        return details
