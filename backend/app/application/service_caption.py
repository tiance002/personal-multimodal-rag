"""Normal VLM derivatives, scoped to one owned ingestion Job/version.

The provider performs the single token/cost reservation. Existing local caption
validation keeps generated text UNVERIFIED and preserves the original bytes.
"""
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib

from backend.app.application.local_caption import LocalCaptionEnricher
from backend.app.domain.scope import Scope
from backend.app.ports.model_access import model_access
from backend.app.ports.providers import ProviderUnavailable


@dataclass(frozen=True)
class _CaptionJob:
    scope: Scope
    document_id: str
    version_id: str
    job_id: str
    permission_check: object
    lease_check: object


_JOB = ContextVar('normal_service_caption_job', default=None)


class ServiceCaptionEnricher(LocalCaptionEnricher):
    def __init__(self, provider):
        if getattr(provider, 'service_binding', None) is None:
            raise ValueError('SERVICE_VISION_BINDING_REQUIRED')
        super().__init__(provider, enabled=True)

    def enrich_for_ingestion(self, document, *, scope, job_id, permission_check, lease_check):
        if (type(scope) is not Scope or not scope.knowledge_base_ids
                or scope.document_ids != frozenset({document.document_id})
                or not job_id or not callable(permission_check) or not callable(lease_check)):
            raise ValueError('SERVICE_VISION_JOB_SCOPE_REQUIRED')
        token = _JOB.set(_CaptionJob(scope, document.document_id, document.version_id,
                                    job_id, permission_check, lease_check))
        try:
            return super().enrich(document, should_continue=lease_check)
        finally:
            _JOB.reset(token)

    def _admission_error(self, document):
        job = _JOB.get()
        if job is None or (job.document_id, job.version_id) != (document.document_id, document.version_id):
            return 'VLM_UNAVAILABLE'
        try:
            allowed = job.permission_check()
        except Exception:
            return 'VLM_UNAVAILABLE'
        return None if allowed is True else 'VLM_EGRESS_DENIED'

    def _preflight(self, document, timeout):
        error = self._admission_error(document)
        if error:
            raise ProviderUnavailable(error)
        with model_access('vision', allowed=True, scope=_JOB.get().scope):
            self.provider.caption_preflight(timeout)

    def _call(self, document, source, timeout):
        error = self._admission_error(document)
        if error:
            raise ProviderUnavailable(error)
        job = _JOB.get()
        identity = hashlib.sha256(('service-caption\0' + job.job_id + '\0' + job.version_id
            + '\0' + source.asset_id + '\0' + hashlib.sha256(source.source_bytes).hexdigest()).encode()).hexdigest()
        with model_access('vision', allowed=True, scope=job.scope):
            return self.provider.caption_image(source.source_bytes, timeout,
                request_id=identity, lease_check=lambda: job.lease_check() is True and job.permission_check() is True)
