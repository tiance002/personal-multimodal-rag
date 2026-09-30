from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionRoute:
    path: str
    reason: str
    error_code: str | None = None


class ExecutionRouter:
    """Explicit LOCAL/CLOUD contract; no unvalidated automatic cloud policy."""

    def choose(self, *, prefer_cloud: bool, cloud_enabled: bool,
               cloud_allowed: bool, cloud_provider_available: bool) -> ExecutionRoute:
        if not prefer_cloud or not cloud_enabled:
            return ExecutionRoute("LOCAL", "LOCAL_DEFAULT" if not prefer_cloud else "CLOUD_DISABLED")
        if not cloud_allowed:
            return ExecutionRoute("LOCAL", "CLOUD_EGRESS_DISABLED", "CLOUD_EGRESS_DISABLED")
        if not cloud_provider_available:
            return ExecutionRoute("LOCAL", "CLOUD_PROVIDER_UNAVAILABLE", "CLOUD_PROVIDER_UNAVAILABLE")
        # Caller still must reserve the budget before making the provider call.
        return ExecutionRoute("CLOUD", "EXPLICIT_CLOUD_REQUEST")
