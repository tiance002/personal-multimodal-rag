"""Opt-in local derivatives. No production wiring, persistence or cloud fallback."""
from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable

from backend.app.adapters.models.ollama import validate_caption_image
from backend.app.domain.chunking import CAPTION_EVIDENCE_PREFIX, chunk_document
from backend.app.domain.models import DocumentAsset, DocumentBlock, DocumentSection, NormalizedDocument, SourceLocator
from backend.app.ports.providers import CaptionResult, LocalCaptionProvider, ProviderUnavailable


_CAPTION_LOCK = threading.Lock()


class LocalCaptionEnricher:
    """At most four serial generations; originals stay intact on every failure."""

    def __init__(self, provider: LocalCaptionProvider, *, enabled: bool = False,
                 clock: Callable[[], float] | None = None) -> None:
        self.provider = provider
        self.enabled = enabled
        self.clock = clock or time.monotonic

    def _admission_error(self, document):
        return None if getattr(self.provider, 'provider_kind', None) == 'local' else 'CAPTION_LOCAL_PROVIDER_REQUIRED'

    def _preflight(self, document, timeout):
        self.provider.caption_preflight(timeout)

    def _call(self, document, source, timeout):
        return self.provider.caption_image(source.source_bytes, timeout)

    def enrich(self, document: NormalizedDocument, *,
               should_continue: Callable[[], bool] = lambda: True) -> NormalizedDocument:
        all_sources = [asset for asset in document.assets if asset.asset_type in {"source_image", "scanned_page"}]
        # Ready and failed derivatives are both immutable attempts. Apply the
        # document cap before skipping them, so repeated calls cannot admit 5+.
        already_derived = {asset.derived_from_asset_id for asset in document.assets if asset.asset_type == "caption"}
        sources = [asset for asset in all_sources[:4] if asset.asset_id not in already_derived]
        if not self.enabled or not sources:
            return document
        started = self.clock()
        result = document
        warnings = list(document.parse_warnings)
        if len(all_sources) > 4:
            warnings.append("CAPTION_DOC_LIMIT")
        admission_error = self._admission_error(document)
        if admission_error:
            for source in sources[:4]:
                result = self._failed(result, source, admission_error)
            return self._partial(result, warnings)

        def state() -> str | None:
            if not should_continue():
                return "CAPTION_CANCELLED"
            if self.clock() - started >= 90:
                return "CAPTION_DEADLINE"
            return None

        admitted = []
        total_bytes = 0
        for source in sources:
            try:
                digest = validate_caption_image(source.source_bytes)
                if source.status != "ready" or digest != source.source_locator.get("sha256"):
                    raise ProviderUnavailable("CAPTION_SOURCE_HASH_MISMATCH")
                total_bytes += len(source.source_bytes)
                if total_bytes > 16 * 1024 * 1024:
                    raise ProviderUnavailable("CAPTION_DOC_LIMIT")
                admitted.append(source)
            except ProviderUnavailable as exc:
                result = self._failed(result, source, self._error(exc))
        if not admitted:
            return self._partial(result, warnings)
        # Queue time is inside the document budget; cancellation is checked while waiting.
        queue_started = time.monotonic()
        acquired = False
        while not acquired:
            failure = state()
            if failure is None and time.monotonic() - queue_started >= 90:
                failure = "CAPTION_DEADLINE"
            if failure:
                for source in admitted:
                    result = self._failed(result, source, failure)
                return self._partial(result, warnings)
            acquired = _CAPTION_LOCK.acquire(timeout=.05)
        try:
            try:
                failure = state()
                if failure:
                    raise ProviderUnavailable(failure)
                self._preflight(document, min(45, 90 - (self.clock() - started)))
                failure = state()
                if failure:
                    raise ProviderUnavailable(failure)
            except Exception as exc:
                for source in admitted:
                    result = self._failed(result, source, self._error(exc))
                return self._partial(result, warnings)
            halt = None
            for source in admitted:
                failure = halt or state()
                if failure:
                    result = self._failed(result, source, failure)
                    continue
                call_started = self.clock()
                timeout = min(45, 90 - (call_started - started))
                observed = None
                try:
                    observed = self._call(document, source, timeout)
                    failure = state()
                    if failure is None and self.clock() - call_started > timeout:
                        failure = "CAPTION_TIMEOUT"
                    if failure:
                        raise ProviderUnavailable(failure)
                    result = self._append(result, source, observed)
                except Exception as exc:
                    code = self._error(exc)
                    result = self._failed(result, source, code, observed)
                    if code in {"CAPTION_CANCELLED", "CAPTION_TIMEOUT", "CAPTION_DEADLINE"}:
                        halt = code
        finally:
            _CAPTION_LOCK.release()
        return self._partial(result, warnings)

    @staticmethod
    def _error(exc: Exception) -> str:
        if isinstance(exc, TimeoutError):
            return "CAPTION_TIMEOUT"
        # Only stable internal codes enter durable diagnostics, never arbitrary text.
        allowed = {"VLM_UNAVAILABLE", "VLM_EGRESS_DENIED", "CAPTION_CANCELLED", "CAPTION_TIMEOUT", "CAPTION_DEADLINE", "CAPTION_RESOURCE_BUSY",
            "CAPTION_VISION_UNAVAILABLE", "CAPTION_LOCAL_ENDPOINT_REQUIRED", "CAPTION_LIMIT_INVALID",
            "CAPTION_RESPONSE_LIMIT", "CAPTION_RESPONSE_INVALID", "CAPTION_REQUEST_FAILED", "CAPTION_REDIRECT_DENIED",
            "CAPTION_IMAGE_BYTES_LIMIT", "CAPTION_IMAGE_PIXELS_LIMIT", "CAPTION_IMAGE_INVALID",
            "CAPTION_SOURCE_HASH_MISMATCH", "CAPTION_DOC_LIMIT", "CAPTION_OUTPUT_INVALID", "CAPTION_OUTPUT_INCOMPLETE",
            "CAPTION_CONTRACT_INVALID"}
        return str(exc) if isinstance(exc, ProviderUnavailable) and str(exc) in allowed else "CAPTION_UNAVAILABLE"

    @staticmethod
    def _id(document: NormalizedDocument, source: DocumentAsset) -> str:
        # Fail closed on re-enrichment/id conflicts instead of overwriting history.
        value = "caption-" + hashlib.sha256((document.version_id + "\0" + source.asset_id).encode()).hexdigest()
        if any(asset.asset_id == value for asset in document.assets):
            raise ProviderUnavailable("CAPTION_CONTRACT_INVALID")
        return value

    def _failed(self, document: NormalizedDocument, source: DocumentAsset, code: str,
                observed: CaptionResult | None = None) -> NormalizedDocument:
        metadata = {"evidence_kind": "model_generated_caption", "validation_status": "UNVERIFIED"}
        if isinstance(observed, CaptionResult):
            metadata["usage_actual"] = observed.usage_actual
        asset = DocumentAsset(asset_id=self._id(document, source), asset_type="caption", status="failed",
            derived_from_asset_id=source.asset_id, page_no=source.page_no, error_code=code,
            text_content=None, source_locator=metadata)
        return document.model_copy(update={"assets": [*document.assets, asset]})

    def _append(self, document: NormalizedDocument, source: DocumentAsset, observed: CaptionResult) -> NormalizedDocument:
        if (not isinstance(observed, CaptionResult) or observed.finish_reason != "stop"
                or not isinstance(observed.text, str) or not observed.text.strip()
                or len(observed.text) > 1100
                or observed.input_image_sha256 != source.source_locator.get("sha256")
                or not observed.model_requested or not observed.model_reported):
            raise ProviderUnavailable("CAPTION_OUTPUT_INVALID")
        asset_id = self._id(document, source)
        metadata = {"schema_version": "local-caption/v1", "evidence_kind": "model_generated_caption",
            "validation_status": "UNVERIFIED", "source_image_sha256": source.source_locator.get("sha256"),
            "input_image_sha256": observed.input_image_sha256, "model_requested": observed.model_requested,
            "model_reported": observed.model_reported, "model_digest": observed.model_digest or "UNKNOWN",
            "prompt_version": observed.prompt_version, "usage_actual": observed.usage_actual,
            "latency_ms": observed.latency_ms, "page": source.page_no,
            "bbox": source.source_locator.get("bbox"), "coordinate_basis": source.source_locator.get("coordinate_basis")}
        content = CAPTION_EVIDENCE_PREFIX + observed.text
        start = len(document.markdown_content)
        end = start + len(content)
        asset = DocumentAsset(asset_id=asset_id, asset_type="caption", derived_from_asset_id=source.asset_id,
            page_no=source.page_no, text_content=observed.text, source_locator=metadata)
        section = DocumentSection(section_id=asset_id, heading="", level=0, start=start, end=end,
            page_start=source.page_no, page_end=source.page_no, content_type="image_caption", asset_id=asset_id)
        locator = SourceLocator(kind="pdf" if document.media_type == "application/pdf" else "image",
            page=source.page_no, bbox=metadata["bbox"], start=start, end=end, quote=content, asset_id=asset_id,
            raw_evidence=metadata)
        text = document.markdown_content + content
        candidate = document.model_copy(update={"markdown_content": text,
            "assets": [*document.assets, asset], "sections": [*document.sections, section],
            "blocks": [*document.blocks, DocumentBlock(block_id=asset_id, kind="text", start=start, end=end)],
            "source_locators": [*document.source_locators, locator],
            "content_sha256": hashlib.sha256(text.encode()).hexdigest()})
        try:
            chunk_document(candidate)
        except ValueError as exc:
            raise ProviderUnavailable("CAPTION_CONTRACT_INVALID") from exc
        return candidate

    @staticmethod
    def _partial(document: NormalizedDocument, warnings: list[str]) -> NormalizedDocument:
        # parse_status belongs to original parser evidence. Derivative status
        # stays on caption assets/UNVERIFIED provenance, with namespaced summary
        # warnings; it must not demote valid native rows or promote partial ones.
        return document.model_copy(update={
            "parse_warnings": tuple(dict.fromkeys([*warnings, "CAPTION_MODEL_GENERATED_UNVERIFIED"]))})
