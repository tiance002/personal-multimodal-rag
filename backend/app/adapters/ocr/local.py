from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityReport:
    provider: str
    available: bool
    reason: str


class LocalOcrProvider:
    """M2 reports capability explicitly; no unverified OCR text is fabricated."""

    def probe(self) -> CapabilityReport:
        return CapabilityReport("local-ocr", False, "OCR_UNAVAILABLE: no verified OCR adapter configured")

    def extract(self, source: bytes) -> str:
        raise RuntimeError("OCR_UNAVAILABLE")
