"""DTO/config bridge for the fixed DOC chain; no model or arbitrary command API."""
import base64
import os
from types import SimpleNamespace
from ._docx_runtime_adapter import BaseParser, DocumentModel, _get_int
from .upstream_chain_parser import FirstParser
from .upstream_docx_parser import DocxParser


def _proxy_setting():
    for name in ("DOCREADER_EXTERNAL_HTTPS_PROXY", "EXTERNAL_HTTPS_PROXY"):
        if name in os.environ:
            return os.environ[name]
    return ""


CONFIG = SimpleNamespace(external_https_proxy=_proxy_setting(),
                         markitdown_max_workers=_get_int("DOCREADER_MARKITDOWN_MAX_WORKERS", 1))


class MarkitdownParser(BaseParser):
    def parse_into_text(self, content):
        # Preserve original file_name/type: fixed DOC conversion does not change
        # the hint to .docx before invoking FirstParser.
        from .core import DocumentRequest, _parse_markitdown
        parsed = _parse_markitdown(DocumentRequest(self.file_name, content), file_type=self.file_type)
        return DocumentModel(parsed.content,
                             {asset.path: base64.b64encode(asset.data).decode() for asset in parsed.assets})


class Docx2Parser(FirstParser):
    _parser_cls = (MarkitdownParser, DocxParser)
