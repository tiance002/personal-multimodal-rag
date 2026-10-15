"""Small DTO/config bridge for the attributed fixed-source DOCX parser."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import io
import os
from types import SimpleNamespace


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


CONFIG = SimpleNamespace(docx_max_pages=_get_int("DOCREADER_DOCX_MAX_PAGES", 0))


@dataclass
class DocumentModel:
    content: str = ""
    images: dict[str, str] = field(default_factory=dict)
    chunks: list[object] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


    def is_valid(self) -> bool:
        return self.content != ""


class BaseParser:
    def __init__(self, file_name: str = "", file_type: str | None = None, **_kwargs: object):
        self.file_name = file_name
        self.file_type = file_type or os.path.splitext(file_name)[1].lstrip(".")


def decode_image(image: object) -> str:
    from PIL import Image

    if isinstance(image, str):
        with open(image, "rb") as stream:
            return base64.b64encode(stream.read()).decode()
    if isinstance(image, bytes):
        return base64.b64encode(image).decode()
    if isinstance(image, Image.Image):
        stream = io.BytesIO()
        image.save(stream, format=image.format or "PNG")
        return base64.b64encode(stream.getvalue()).decode()
    raise ValueError(f"Unsupported image type: {type(image)}")


endecode = SimpleNamespace(decode_image=decode_image, encode_bytes=lambda text: text.encode("utf-8"))
