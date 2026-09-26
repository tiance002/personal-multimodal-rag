from __future__ import annotations

from pathlib import Path
from typing import Protocol

from backend.app.domain.models import NormalizedDocument


class ParserError(ValueError):
    """A source cannot be parsed under the declared media type/signature."""


class OCRUnavailable(ParserError):
    """The source is preserved but a verified OCR adapter is not available."""


class DocumentParser(Protocol):
    def parse(self, path: Path, document_id: str, version_id: str) -> NormalizedDocument: ...
