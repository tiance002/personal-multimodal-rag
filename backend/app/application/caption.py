"""Provider-neutral derivative orchestration; no implicit cloud fallback."""
from backend.app.application.local_caption import LocalCaptionEnricher
from backend.app.ports.providers import CaptionProvider, CaptionUsageGuard, ProviderUnavailable
from backend.app.ports.model_access import model_access


class CaptionEnricher(LocalCaptionEnricher):
    def __init__(self, provider: CaptionProvider, *, usage_guard: CaptionUsageGuard | None = None,
                 enabled: bool = False, clock=None):
        super().__init__(provider, enabled=enabled, clock=clock)
        self.usage_guard = usage_guard

    def _admission_error(self, document):
        kind = getattr(self.provider, 'provider_kind', None)
        if kind == 'local':
            return None
        if kind != 'cloud' or self.usage_guard is None:
            return 'VLM_UNAVAILABLE'
        try:
            allowed = self.usage_guard.allowed(document.document_id, document.version_id)
        except Exception:
            return 'VLM_UNAVAILABLE'
        return None if allowed is True else 'VLM_EGRESS_DENIED'

    def _preflight(self, document, timeout):
        error = self._admission_error(document)
        if error:
            raise ProviderUnavailable(error)
        with model_access('vision', allowed=True):
            super()._preflight(document, timeout)

    def _call(self, document, source, timeout):
        error = self._admission_error(document)
        if error:
            raise ProviderUnavailable(error)
        if self.provider.provider_kind == 'local':
            return super()._call(document, source, timeout)
        reservation = self.usage_guard.reserve(document.document_id, document.version_id)
        result = None
        try:
            with model_access('vision', allowed=True):
                result = super()._call(document, source, timeout)
            return result
        finally:
            self.usage_guard.settle(reservation, getattr(result, 'usage_actual', None))
