"""Explicit local native-runtime bridge; no service and no flat-text fallback."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from backend.app.domain.parsers import ParserError
from backend.app.domain.models import NormalizedDocument, DocumentTable, TableCell, DocumentBlock, DocumentSection, SourceLocator
from backend.app.domain.table_evidence import table_row_texts

MAX_INPUT=8*1024*1024
MAX_OUTPUT=4*1024*1024

class Wire(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
class NativeCell(Wire):
    text: str=Field(max_length=4096)
    row_start: int=Field(ge=0,lt=1000)
    row_end: int=Field(gt=0,le=1000)
    col_start: int=Field(ge=0,lt=200)
    col_end: int=Field(gt=0,le=200)
    column_header: bool | None
    row_header: bool | None
    header_evidence: dict[str,object]
    locator: dict[str,object]
class NativeTable(Wire):
    num_rows: int=Field(gt=0,le=1000)
    num_cols: int=Field(gt=0,le=200)
    cells: list[NativeCell]=Field(min_length=1,max_length=20000)
    caption: str | None=Field(max_length=4096)
class NativeBlock(Wire):
    kind: Literal['text','table']
    text: str | None=Field(default=None,max_length=1024*1024)
    table_index: int | None=Field(default=None,ge=0,lt=64)
class NativeResult(Wire):
    schema_version: Literal[1]
    format: Literal['html','docx']
    source_sha256: str=Field(pattern=r'^[a-f0-9]{64}$')
    blocks: list[NativeBlock]=Field(max_length=100000)
    tables: list[NativeTable]=Field(max_length=64)
    warnings: list[str]=Field(max_length=16)

def run_worker(python: Path, raw: bytes, format: str, timeout: float) -> bytes:
    if not python.is_absolute() or not python.is_file():
        raise ParserError('NATIVE_RUNTIME_UNAVAILABLE')
    # Do not inherit secrets, PYTHONPATH or user-site configuration.
    env={k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','TEMP','TMP'}}
    env['PYTHONDONTWRITEBYTECODE']='1'
    command=[str(python),'-I','-B',str(Path(__file__).with_name('native_worker.py')),format]
    try:
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,shell=False)
    except OSError as exc:
        raise ParserError('NATIVE_RUNTIME_UNAVAILABLE') from exc
    buffers=[bytearray(),bytearray()];overflow=threading.Event();io_error=threading.Event()
    def read(stream,index,limit):
        try:
            while True:
                part=stream.read(65536)
                if not part:break
                if len(buffers[index])+len(part)>limit:
                    overflow.set();process.kill();break
                buffers[index].extend(part)
        except OSError:io_error.set()
        finally:stream.close()
    def write():
        try:process.stdin.write(raw);process.stdin.close()
        except (OSError,ValueError):
            io_error.set()
            process.stdin.close()
    threads=[threading.Thread(target=read,args=(process.stdout,0,MAX_OUTPUT)),threading.Thread(target=read,args=(process.stderr,1,8192)),threading.Thread(target=write)]
    for t in threads:t.start()
    timed_out=False
    try:process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out=True;process.kill();process.wait()
    finally:
        for t in threads:t.join()
    if timed_out:raise ParserError('NATIVE_TIMEOUT')
    if overflow.is_set():raise ParserError('NATIVE_OUTPUT_LIMIT')
    if process.returncode != 0:
        diagnostic=buffers[1].decode('ascii',errors='ignore')
        # Worker codes only; dependency tracebacks and raw text never enter errors.
        import re
        if not re.fullmatch(r'NATIVE_[A-Z_]+',diagnostic):diagnostic='NATIVE_RUNTIME_OR_PARSE_FAILED'
        raise ParserError(diagnostic)
    if io_error.is_set():raise ParserError('NATIVE_IO_FAILED')
    return bytes(buffers[0])

def coordinate(row,col):return f'R{row}C{col}'

def validate_result(encoded: bytes, raw: bytes, format: str) -> NativeResult:
    try:
        decoded=json.loads(encoded)
        if not isinstance(decoded,dict) or type(decoded.get('schema_version')) is not int:raise ValueError()
        result=NativeResult.model_validate(decoded)
        if result.format!=format or result.source_sha256!=hashlib.sha256(raw).hexdigest():raise ValueError()
        indices=[];text_total=0;grid_total=0;cell_total=0
        for b in result.blocks:
            if b.kind=='text':
                if b.text is None or b.table_index is not None:raise ValueError()
                text_total+=len(b.text)
            else:
                if b.text is not None or b.table_index is None:raise ValueError()
                indices.append(b.table_index)
        if indices!=list(range(len(result.tables))):raise ValueError()
        for t in result.tables:
            grid_total+=t.num_rows*t.num_cols;cell_total+=len(t.cells)
            if grid_total>50000 or cell_total>20000:raise ValueError()
            occupied=set()
            for c in t.cells:
                if not (c.row_start<c.row_end<=t.num_rows and c.col_start<c.col_end<=t.num_cols):raise ValueError()
                text_total+=len(c.text)
                for r in range(c.row_start,c.row_end):
                    for col in range(c.col_start,c.col_end):
                        if (r,col) in occupied:raise ValueError()
                        occupied.add((r,col))
            if len(occupied)!=t.num_rows*t.num_cols:raise ValueError()
        if text_total>1024*1024:raise ValueError()
        return result
    except (ValidationError,ValueError,TypeError) as exc:raise ParserError('NATIVE_SCHEMA_INVALID') from exc

class NativeParser:
    def __init__(self, *, python: Path | None=None, timeout: float=20):
        selected=python if python is not None else os.getenv('RAG_NATIVE_TABLE_PYTHON')
        self.python=Path(selected) if selected else None
        if not 0<timeout<=60:raise ValueError('native timeout must be within 60 seconds')
        self.timeout=timeout
    def parse(self,path: Path,document_id: str,version_id: str) -> NormalizedDocument:
        if self.python is None:raise ParserError('NATIVE_RUNTIME_NOT_CONFIGURED')
        try:
            with path.open('rb') as stream:raw=stream.read(MAX_INPUT+1)
        except OSError as exc:raise ParserError('NATIVE_SOURCE_UNREADABLE') from exc
        if len(raw)>MAX_INPUT:raise ParserError('NATIVE_INPUT_LIMIT')
        result=validate_result(run_worker(self.python,raw,self.format,self.timeout),raw,self.format)
        tables=[];blocks=[];sections=[];pieces=[];offset=0
        for b in result.blocks:
            if b.kind=='text':
                text=b.text
                if not text:continue
                block=DocumentBlock(block_id=f'block-{len(blocks)}',kind='text',start=offset,end=offset+len(text))
                sections.append(DocumentSection(section_id=block.block_id,heading='',start=block.start,end=block.end,level=0))
            else:
                t=result.tables[b.table_index];cells=[]
                headers=[c for c in t.cells if c.column_header is True]
                header_rows=tuple(sorted({c.row_start+1 for c in headers}))
                for c in t.cells:
                    labels=tuple(h.text for h in headers if h.col_start<=c.col_start<h.col_end and h.row_start<=c.row_start)
                    cells.append(TableCell(coordinate=coordinate(c.row_start+1,c.col_start+1),row=c.row_start+1,column=c.col_start+1,
                        value=c.text,value_type='text' if c.text else 'empty',display=c.text,number_format='source_text',
                        column_headers=labels,row_span=c.row_end-c.row_start,column_span=c.col_end-c.col_start,
                        column_header=c.column_header,row_header=c.row_header,header_evidence=c.header_evidence,native_locator=c.locator))
                merged=tuple(f'{coordinate(c.row_start+1,c.col_start+1)}:{coordinate(c.row_end,c.col_end)}' for c in t.cells if c.row_end-c.row_start>1 or c.col_end-c.col_start>1)
                table=DocumentTable(table_id=f'{self.format}-table-{b.table_index}',cell_range=f'R1C1:R{t.num_rows}C{t.num_cols}',
                    header_rows=header_rows,header_detection='html-source-policy-v1' if self.format=='html' else 'declared-tblHeader-otherwise-UNKNOWN',
                    source_format=self.format,caption=t.caption,merged_ranges=merged,cells=cells,start=offset,end=offset)
                text=''.join(content for _,content in table_row_texts(table))
                table=table.model_copy(update={'end':offset+len(text)});tables.append(table)
                block=DocumentBlock(block_id=f'block-{len(blocks)}',kind='table',table_id=table.table_id,start=offset,end=table.end)
            pieces.append(text);blocks.append(block);offset+=len(text)
        content=''.join(pieces)
        if not content:raise ParserError('NATIVE_EMPTY_CONTENT')
        return NormalizedDocument(document_id=document_id,version_id=version_id,title=path.stem,media_type=self.media_type,
            markdown_content=content,sections=sections,tables=tables,blocks=blocks,
            source_locators=[SourceLocator(kind='text' if b.kind=='text' else 'table',start=b.start,end=b.end,quote=content[b.start:b.end],table_id=b.table_id,source_format=self.format) for b in blocks],
            content_sha256=hashlib.sha256(content.encode()).hexdigest(),parser_version=f'{self.format}/native-v1',
            parse_status='partial' if result.warnings else 'complete',parse_warnings=tuple(result.warnings))
class HtmlParser(NativeParser):
    format='html'
    media_type='text/html'
class DocxParser(NativeParser):
    format='docx'
    media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
