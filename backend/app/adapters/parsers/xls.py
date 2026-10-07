"""Inert legacy workbook parsing in the bounded stdin-only native worker."""
from backend.app.adapters.parsers.native import NativeParser


class XlsParser(NativeParser):
    format = 'xls'
    media_type = 'application/vnd.ms-excel'

    def __init__(self, *, python=None, first_row_as_header=False):
        super().__init__(python=python)
        if type(first_row_as_header) is not bool:
            raise ValueError('first_row_as_header must be a boolean')
        self.worker_format = 'xls-header' if first_row_as_header else 'xls'
