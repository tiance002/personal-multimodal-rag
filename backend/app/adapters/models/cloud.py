from __future__ import annotations

from backend.app.ports.providers import ProviderUnavailable


class CloudDisabledGateway:
    """Explicit boundary for V1; no network provider is silently selected."""

    def answer(self, prompt: str, timeout_seconds: float) -> str:
        raise ProviderUnavailable("cloud provider is disabled in V1")
