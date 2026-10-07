from __future__ import annotations

from pathlib import Path
from typing import Protocol

from backend.app.domain.models import NormalizedDocument


class ParserError(ValueError):
    """A source cannot be parsed under the declared media type/signature."""

    @property
    def category(self) -> str:
        """Stable P2 category while legacy error codes remain readable."""
        code = str(self)
        if 'PASSWORD' in code:return 'PASSWORD_PROTECTED'
        if code in {'OCR_UNAVAILABLE','OCR_EMPTY','VLM_UNAVAILABLE'}:return code
        if 'EMPTY' in code or code=='NO_SEARCHABLE_CONTENT':return 'NO_SEARCHABLE_CONTENT'
        if 'UNSUPPORTED_MEDIA' in code:return 'UNSUPPORTED_FORMAT'
        if any(v in code for v in ('UNAVAILABLE','NOT_CONFIGURED','CONVERSION_UNSUPPORTED')):return 'PARSER_UNAVAILABLE'
        if 'PARTIAL' in code:return 'PARTIAL_PARSE'
        return 'SOURCE_CORRUPT'


class OCRUnavailable(ParserError):
    """The source is preserved but a verified OCR adapter is not available."""


class DocumentParser(Protocol):
    def parse(self, path: Path, document_id: str, version_id: str) -> NormalizedDocument: ...
