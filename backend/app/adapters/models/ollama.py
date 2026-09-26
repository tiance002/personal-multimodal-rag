from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from backend.app.application.model_policy import QueryGatewayResult
from backend.app.ports.providers import EmbeddingResult, ProviderUnavailable


class OllamaGateway:
    def __init__(self, base_url: str, chat_model: str, embedding_model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.chat_model = chat_model
        self.embedding_model = embedding_model

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
        response, _ = self._post(
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
        return QueryGatewayResult(tuple(expansions[:8]))

    def embed(self, texts: Sequence[str], timeout_seconds: float) -> EmbeddingResult:
        response, latency_ms = self._post(
            "/api/embed",
            {"model": self.embedding_model, "input": list(texts)},
            timeout_seconds,
        )
        vectors = response.get("embeddings")
        if not isinstance(vectors, list) or not all(isinstance(vector, list) for vector in vectors):
            raise ProviderUnavailable("ollama embedding response schema invalid")
        dimensions = len(vectors[0]) if vectors else 0
        if dimensions != 1024:
            raise ProviderUnavailable(f"embedding dimension mismatch: {dimensions}")
        return EmbeddingResult(vectors=vectors, model=self.embedding_model, dimensions=dimensions, latency_ms=latency_ms)

    def answer(self, prompt: str, timeout_seconds: float) -> str:
        response, _ = self._post(
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
        return content.strip()
