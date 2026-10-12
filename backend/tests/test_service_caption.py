"""SIMULATED provider and image fixtures; no DB or real provider calls."""
import pytest

from backend.app.application.service_caption import ServiceCaptionEnricher
from backend.app.domain.scope import Scope
from backend.app.ports.model_access import request_scope, scope_allows
from backend.tests.test_local_caption_enricher import document, FakeProvider


class SimulatedServiceVision(FakeProvider):
    provider_kind = 'cloud'
    service_binding = object()

    def __init__(self):
        super().__init__()
        self.identities = []

    def caption_preflight(self, timeout_seconds):
        assert scope_allows('vision')
        assert request_scope('vision') == Scope.from_ids(['kb'], ['doc'])
        super().caption_preflight(timeout_seconds)

    def caption_image(self, image_bytes, timeout_seconds, *, request_id, lease_check):
        assert scope_allows('vision')
        assert request_scope('vision') == Scope.from_ids(['kb'], ['doc'])
        assert lease_check() is True
        self.identities.append(request_id)
        return super().caption_image(image_bytes, timeout_seconds)


def enrich(provider, doc, **changes):
    return ServiceCaptionEnricher(provider).enrich_for_ingestion(doc,
        scope=Scope.from_ids(['kb'], ['doc']), job_id='SIMULATED-job',
        permission_check=changes.get('permission_check', lambda: True),
        lease_check=changes.get('lease_check', lambda: True))


def test_normal_vision_preserves_original_provenance_and_scoped_identity():
    provider = SimulatedServiceVision()
    original = document(2)
    before = original.model_dump_json()
    result = enrich(provider, original)
    captions = [asset for asset in result.assets if asset.asset_type == 'caption']
    assert len(provider.calls) == len(captions) == 2
    assert len(set(provider.identities)) == 2
    assert original.model_dump_json() == before
    assert all(asset.source_locator['validation_status'] == 'UNVERIFIED' for asset in captions)
    assert request_scope('vision') is None and not scope_allows('vision')
    enrich(provider, result)
    assert len(provider.calls) == 2


@pytest.mark.parametrize('failure', ['permission', 'revoked', 'lease'])
def test_denied_or_revoked_permission_and_lost_lease_never_send(failure):
    provider = SimulatedServiceVision()
    allowed = {'value': failure != 'permission'}
    if failure == 'revoked':
        provider.preflight_error = None
        old = provider.caption_preflight
        def preflight(timeout):
            old(timeout)
            allowed['value'] = False
        provider.caption_preflight = preflight
    result = enrich(provider, document(), permission_check=lambda: allowed['value'],
                    lease_check=lambda: failure != 'lease')
    assert provider.calls == []
    assert all(asset.status == 'failed' for asset in result.assets if asset.asset_type == 'caption')


def test_vision_without_owned_job_or_wrong_document_scope_is_closed():
    provider = SimulatedServiceVision()
    enricher = ServiceCaptionEnricher(provider)
    result = enricher.enrich(document())
    assert provider.calls == []
    assert next(asset for asset in result.assets if asset.asset_type == 'caption').error_code == 'VLM_UNAVAILABLE'
    with pytest.raises(ValueError, match='SERVICE_VISION_JOB_SCOPE_REQUIRED'):
        enricher.enrich_for_ingestion(document(), scope=Scope.from_ids(['kb'], ['other-doc']),
            job_id='SIMULATED-job', permission_check=lambda: True, lease_check=lambda: True)
