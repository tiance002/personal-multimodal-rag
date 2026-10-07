"""Strict monthly numeric witnesses from one immutable source row.

No neighboring chunk, inferred semantic header, or lexical co-occurrence fallback.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib,json,re
from typing import Any

from backend.app.application.query_router import EvidenceTarget

_ENTITIES=frozenset({'主体','名称','门店','店铺','店名','项目','产品','方案','客户'})
_MONTHS=frozenset({'月份','统计月份','年月','期间'})
_MONEY=re.compile(r'营业额|收入|支出|金额|费用|成本|价格|预算')
_UNITS=frozenset({'元','千元','万元','亿元','美元'})
_NUMBER=re.compile(r'[+-]?\d{1,20}(?:\.\d{1,12})?\Z')

@dataclass(frozen=True)
class RowFact:
    value: Decimal
    unit: str
    row: int
    table: str
    value_coordinate: str
    row_start: int | None = None
    row_end: int | None = None


def _field(header: str, target: EvidenceTarget) -> str | None:
    match=re.fullmatch(re.escape(target.attribute)+r'[（(]([^（）()]{1,8})[）)]',header)
    if not match or not _MONEY.search(target.attribute):return None
    unit=match[1]
    return unit if unit in _UNITS and (target.unit is None or target.unit==unit) else None


def _columns(headers: list[str],target: EvidenceTarget) -> tuple[int,int,int,str] | None:
    entity=[i for i,h in enumerate(headers) if h in _ENTITIES]
    month=[i for i,h in enumerate(headers) if h in _MONTHS]
    values=[(i,u) for i,h in enumerate(headers) if (u:=_field(h,target)) is not None]
    if len(entity)!=1 or len(month)!=1 or len(values)!=1:return None
    value,unit=values[0]
    if len({entity[0],month[0],value})!=3:return None
    return entity[0],month[0],value,unit


def _decimal(text: str) -> Decimal | None:
    if not isinstance(text,str) or not _NUMBER.fullmatch(text):return None
    try:return Decimal(text)
    except InvalidOperation:return None


def _coordinate(value: str) -> tuple[int,int] | None:
    native=re.fullmatch(r'R([1-9]\d{0,3})C([1-9]\d{0,2})',value)
    if native:return int(native[1]),int(native[2])
    sheet=re.fullmatch(r'([A-Z]{1,3})([1-9]\d{0,3})',value)
    if not sheet:return None
    column=0
    for char in sheet[1]:column=column*26+ord(char)-64
    return int(sheet[2]),column


def _table_rows(content: str, locator: dict[str,Any],target: EvidenceTarget) -> list[RowFact]:
    table=locator.get('table_id');cell_range=locator.get('cell_range')
    if not isinstance(table,str) or not table or not isinstance(cell_range,str):return []
    source=locator.get('source_format');policy=locator.get('header_detection')
    docx=source=='docx' and policy=='docx-declared-simple-header-v1'
    native=source=='html' and policy=='html-source-policy-v1' or docx
    sheet=source is None and policy in {'inferred_first_multi_text_row','explicit_first_row'} and isinstance(locator.get('sheet'),str)
    if not (native or sheet):return []
    header_rows=locator.get('header_rows')
    if not isinstance(header_rows,(list,tuple)) or len(header_rows)!=1:return []
    endpoints=cell_range.split(':')
    if len(endpoints)!=2:return []
    first,last=map(_coordinate,endpoints)
    if first is None or last is None or first[0]!=last[0] or first[1]!=1 or last[1]>32:return []
    row=first[0];header=header_rows[0]
    if type(header) is not int or header>=row or docx and header!=1:return []
    cells=locator.get('cells')
    if not isinstance(cells,(tuple,list)) or len(cells)!=2*last[1]:return []
    indexed={}
    origin_sources=set()
    for c in cells:
        if not isinstance(c,dict):return []
        key=(c.get('row'),c.get('column'))
        if key in indexed or key[0] not in {header,row} or type(key[1]) is not int or not 1<=key[1]<=last[1]:return []
        if _coordinate(c.get('coordinate',''))!=key or c.get('row_span',1)!=1 or c.get('column_span',1)!=1 or c.get('merged_anchor') is not None:return []
        if c.get('formula') is not None or c.get('cache_status','not_applicable')!='not_applicable':return []
        if docx:
            proof=c.get('header_evidence');loc=c.get('native_locator')
            match=re.fullmatch(r'docx-table-(0|[1-9]\d*)',table)
            if not isinstance(proof,dict) or not isinstance(loc,dict) or match is None:return []
            origin=loc.get('origin')
            if not isinstance(origin,dict) or loc.get('continuations')!=[]:return []
            if proof.get('policy')!=policy or proof.get('header_row')!=0 or proof.get('tblHeader') is not (key[0]==header):return []
            source_hash=proof.get('source_sha256');source_doc=proof.get('source_document_id');source_version=proof.get('source_version_id')
            if not isinstance(source_hash,str) or not re.fullmatch(r'[a-f0-9]{64}',source_hash):return []
            if not isinstance(source_doc,str) or not source_doc or not isinstance(source_version,str) or not source_version:return []
            origin_sources.add((source_hash,source_doc,source_version))
            if len(origin_sources)!=1:return []
            if (origin.get('table_index')!=int(match[1]) or origin.get('row_index')!=key[0]-1
                    or origin.get('grid_col_start')!=key[1]-1 or origin.get('grid_col_end')!=key[1]
                    or origin.get('xml_cell_index')!=key[1]-1 or origin.get('gridSpan')!=1 or origin.get('vMerge') is not None):return []
        indexed[key]=c
    headers=[];data=[]
    for col in range(1,last[1]+1):
        h=indexed.get((header,col));c=indexed.get((row,col))
        if h is None or c is None:return []
        label=h.get('display')
        if not isinstance(label,str) or not label or h.get('value')!=label:return []
        if list(h.get('column_headers',[]))!=[label] or list(c.get('column_headers',[]))!=[label]:return []
        if native and (h.get('column_header') is not True or c.get('column_header') is not False):return []
        if not isinstance(c.get('display'),str):return []
        headers.append(label);data.append(c)
    columns=_columns(headers,target)
    if columns is None:return []
    entity,month,amount,unit=columns
    if data[entity].get('value')!=target.subject or data[entity]['display']!=target.subject:return []
    if data[month].get('value')!=target.period or data[month]['display']!=target.period:return []
    value=_decimal(data[amount]['display'])
    if value is None or type(data[amount].get('value')) is bool:return []
    raw=data[amount].get('value')
    if isinstance(raw,(int,float,str)):
        try:
            if Decimal(str(raw))!=value:return []
        except InvalidOperation:return []
    else:return []
    pieces=[]
    for c,label in zip(data,headers):
        if native:pieces.append(f"{c['coordinate']} [{label}]={json.dumps(c['display'],ensure_ascii=False)}; span=1x1")
        else:pieces.append(f"{c['coordinate']} [{label}]={c['display']}; format={c.get('number_format','General')}")
    body=' | '.join(pieces)+'\n'
    if native:
        prefix=f'Document format: {source}; table: {table}; row: {row}; range: {cell_range}; headers: {policy}\n'
        # The optional source caption is metadata, never a header or row fact.
        remainder=content[len(prefix):] if content.startswith(prefix) else ''
        if remainder!=body and not (remainder.startswith('Caption: ') and len(remainder.splitlines())==2 and remainder.split('\n',1)[1]==body):return []
    else:
        if policy=='explicit_first_row':
            from backend.app.domain.models import DocumentTable,TableCell
            from backend.app.domain.table_evidence import key_value_row
            try:
                original=[TableCell.model_validate(c) for c in cells]
            except (ValueError,TypeError):return []
            source_table=DocumentTable(table_id=table,sheet=locator['sheet'],cell_range=cell_range,cells=original,start=0,end=len(content))
            if content!=key_value_row(source_table,[c for c in original if c.row==row]):return []
        else:
            prefix=f"Sheet: {locator['sheet']}; table: {table}; range: {cell_range}\n"
            if content!=prefix+body:return []
    return [RowFact(value,unit,row,table,data[amount]['coordinate'])]


def _tsv_rows(content: str,target: EvidenceTarget) -> list[RowFact]:
    lines=content.splitlines(keepends=True)
    indexes=[i for i,line in enumerate(lines) if '\t' in line]
    # One complete contiguous explicitly delimited table in this one source chunk.
    if len(indexes)<2 or len(indexes)>64 or indexes!=list(range(indexes[0],indexes[-1]+1)):return []
    grid=[lines[i].rstrip('\r\n').split('\t') for i in indexes]
    width=len(grid[0])
    if not 3<=width<=32 or any(len(r)!=width for r in grid):return []
    if len(set(grid[0]))!=width:return []
    columns=_columns(grid[0],target)
    if columns is None:return []
    entity,month,amount,unit=columns;out=[]
    for line_index,row in zip(indexes[1:],grid[1:]):
        if row[entity]!=target.subject or row[month]!=target.period:continue
        value=_decimal(row[amount])
        if value is None:return []
        start=sum(map(len,lines[:line_index]));end=start+len(lines[line_index])
        out.append(RowFact(value,unit,line_index+1,f'tsv@{sum(map(len,lines[:indexes[0]]))}',f'R{line_index+1}C{amount+1}',start,end))
    return out


def row_facts(content: str,locator: dict[str,Any],target: EvidenceTarget,content_sha256: str | None, *, document_id: str | None=None, version_id: str | None=None) -> tuple[RowFact,...]:
    if target.period is None or not isinstance(locator,dict) or len(content)>8000:return ()
    if locator.get('parse_status')!='complete' or locator.get('quote')!=content:return ()
    if locator.get('source_format')=='docx':
        cells=locator.get('cells')
        if not isinstance(cells,(tuple,list)):return ()
        for cell in cells:
            if not isinstance(cell,dict):return ()
            proof=cell.get('header_evidence')
            if not isinstance(proof,dict):return ()
            if document_id is not None and proof.get('source_document_id')!=document_id:return ()
            if version_id is not None and proof.get('source_version_id')!=version_id:return ()
    if content_sha256 is None or hashlib.sha256(content.encode('utf-8')).hexdigest()!=content_sha256:return ()
    if locator.get('kind')=='table':facts=_table_rows(content,locator,target)
    elif locator.get('kind') in {'text','markdown'} and not locator.get('cells'):facts=_tsv_rows(content,target)
    else:return ()
    return tuple(facts)


def subject_period_pattern(target: EvidenceTarget) -> str:
    """Exact identity; a bounded horizontal separator is allowed before a month."""
    if target.period is None:
        return re.escape(target.subject)
    return r'(?<!\w)' + re.escape(target.subject) + r'[ \t]{0,8}' + re.escape(target.period) + r'(?![\d-])'


def claim_amount(clause: str,target: EvidenceTarget) -> tuple[Decimal,str] | None:
    """One explicit requested numeric claim, including its source unit."""
    units='|'.join(map(re.escape,sorted(_UNITS,key=len,reverse=True)))
    pattern=re.compile(subject_period_pattern(target)+r'[^。；，,！？\n]{0,8}'+re.escape(target.attribute)+r'(?:\s|的|为|是|:|：|=){0,8}([+-]?\d{1,20}(?:\.\d{1,12})?)\s*('+units+r')(?![\w])')
    matches=list(pattern.finditer(clause))
    if len(matches)!=1:return None
    # A second numeric assertion in this same clause is not covered by this row.
    remainder=clause[:matches[0].start()]+clause[matches[0].end():]
    if re.search(r'\d',remainder):return None
    value=_decimal(matches[0][1]);unit=matches[0][2]
    if value is None or target.unit is not None and unit!=target.unit:return None
    return value,unit
