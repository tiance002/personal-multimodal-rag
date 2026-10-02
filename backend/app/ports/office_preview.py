"""Local Office preview boundary; no renderer implementation imports."""
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

class OfficePreviewUnavailable(RuntimeError):
    pass

@dataclass(frozen=True)
class OfficePreviewFile:
    path: Path
    directory: Path
    file_name: str

class OfficePreviewProtocol(Protocol):
    def is_office_document(self, file_name: str, media_type: str) -> bool: ...
    def convert_office_to_pdf(self, source: Path, file_name: str, media_type: str) -> OfficePreviewFile: ...
    def cleanup_office_preview(self, preview: OfficePreviewFile) -> None: ...
