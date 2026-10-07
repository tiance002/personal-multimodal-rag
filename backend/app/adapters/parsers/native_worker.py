"""Bounded stdin-only native parser. Run with an explicitly selected Python runtime."""
from __future__ import annotations
import io
import json
import sys
import zipfile
import hashlib
from defusedxml.ElementTree import fromstring
from bs4 import BeautifulSoup, NavigableString
from docx import Document
from docx.oxml.ns import qn
from docx.table import _Cell, Table
from docx.text.paragraph import Paragraph

MAX_INPUT = 8 * 1024 * 1024
MAX_OUTPUT = 4 * 1024 * 1024
MAX_GRID = 50000
MAX_CELLS = 20000
MAX_TEXT = 1024 * 1024

class Rejected(ValueError):
    pass

def require(ok, code):
    if not ok:
        raise Rejected(code)

def offline(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo', 'urllib.Request', 'subprocess.Popen'}:
        raise Rejected('NATIVE_EXTERNAL_ACTION_DENIED')
sys.addaudithook(offline)

def preflight_docx(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        infos = z.infolist()
        require(len(infos) <= 2000 and sum(i.file_size for i in infos) <= 32*1024*1024, 'NATIVE_ZIP_LIMIT')
        require(len({i.filename for i in infos}) == len(infos), 'NATIVE_ZIP_DUPLICATE')
        require('word/document.xml' in z.namelist(), 'NATIVE_DOCX_INVALID')
        for i in infos:
            name = i.filename.lower()
            require(not i.flag_bits & 1 and i.file_size <= MAX_INPUT and i.file_size/max(1,i.compress_size) <= 100, 'NATIVE_ZIP_LIMIT')
            require(not name.startswith('/') and '..' not in name.split('/') and '\\' not in name, 'NATIVE_ZIP_PATH')
            require(not any(x in name for x in ('vbaproject', 'activex', 'embeddings/')), 'NATIVE_ACTIVE_CONTENT')
            if name.endswith(('.xml', '.rels')):
                root = fromstring(z.read(i), forbid_dtd=True, forbid_entities=True, forbid_external=True)
                require(sum(1 for _ in root.iter()) <= 100000, 'NATIVE_XML_LIMIT')
                if name.endswith('.rels'):
                    require(not any(n.get('TargetMode','').lower() == 'external' for n in root), 'NATIVE_EXTERNAL_RELATIONSHIP')
                require(not any(n.tag.rsplit('}',1)[-1] in {'altChunk','object','fldSimple','instrText'} for n in root.iter()), 'NATIVE_ACTIVE_CONTENT')

def cell(text, r, re, c, ce, header, row_header, evidence, locator):
    require(len(text) <= 4096, 'NATIVE_TEXT_LIMIT')
    return dict(text=text,row_start=r,row_end=re,col_start=c,col_end=ce,column_header=header,row_header=row_header,header_evidence=evidence,locator=locator)

def validate_tables(tables):
    require(len(tables) <= 64, 'NATIVE_TABLE_LIMIT')
    count = grid = total_text = 0
    for t in tables:
        nr,nc=t['num_rows'],t['num_cols']
        require(0 < nr <= 1000 and 0 < nc <= 200, 'NATIVE_TABLE_LIMIT')
        grid += nr*nc
        count += len(t['cells'])
        require(grid <= MAX_GRID and count <= MAX_CELLS, 'NATIVE_TABLE_LIMIT')
        occupied=set()
        for c in t['cells']:
            require(0 <= c['row_start'] < c['row_end'] <= nr and 0 <= c['col_start'] < c['col_end'] <= nc, 'NATIVE_SPAN_INVALID')
            for r in range(c['row_start'],c['row_end']):
                for col in range(c['col_start'],c['col_end']):
                    require((r,col) not in occupied, 'NATIVE_SPAN_OVERLAP')
                    occupied.add((r,col))
            total_text += len(c['text'])
        require(len(occupied)==nr*nc, 'NATIVE_RAGGED_TABLE_UNSUPPORTED')
    require(total_text <= MAX_TEXT, 'NATIVE_TEXT_LIMIT')

def html(raw):
    soup=BeautifulSoup(raw.decode('utf-8-sig'), 'html.parser')
    require(len(soup.find_all(True)) <= 100000, 'NATIVE_HTML_LIMIT')
    warnings=[]
    for e in list(soup.find_all(['script','style','noscript','template','iframe','object','embed','img','link'])):
        warnings.append('HTML_INERT_CONTENT_IGNORED')
        e.decompose()
    tables=[];blocks=[];parts=[]
    def flush():
        if parts:
            text=''.join(parts)
            if text.strip(): blocks.append({'kind':'text','text':text})
            parts.clear()
    def extract(t):
        require(not t.find('table'), 'NATIVE_NESTED_TABLE_UNSUPPORTED')
        rows=t.find_all('tr'); require(0 < len(rows) <= 1000,'NATIVE_TABLE_LIMIT')
        occupied=set();cells=[];band=True;ti=len(tables)
        for r,tr in enumerate(rows):
            tags=tr.find_all(['td','th'],recursive=False)
            if any(e.name=='td' for e in tags):band=False
            col=0
            for ci,e in enumerate(tags):
                while (r,col) in occupied: col+=1
                rs=int(e.get('rowspan',1));cs=int(e.get('colspan',1))
                require(1<=rs<=1000 and 1<=cs<=200 and r+rs<=len(rows) and col+cs<=200, 'NATIVE_SPAN_INVALID')
                scope=e.get('scope');require(scope in {None,'col','colgroup','row','rowgroup'},'NATIVE_HEADER_SCOPE_UNSUPPORTED')
                ch=e.name=='th' and (scope in {'col','colgroup'} or (scope is None and band))
                rh=e.name=='th' and (scope in {'row','rowgroup'} or (scope is None and not band))
                cells.append(cell(e.get_text(),r,r+rs,col,col+cs,ch,rh,{'tag':e.name,'scope':scope,'policy':'leading-all-th-v1'}, {'table_index':ti,'tr_index':r,'cell_index':ci}))
                for rr in range(r,r+rs):
                    for cc in range(col,col+cs):
                        require((rr,cc) not in occupied,'NATIVE_SPAN_OVERLAP');occupied.add((rr,cc))
                        require(len(occupied)<=MAX_GRID,'NATIVE_TABLE_LIMIT')
                col+=cs
        caption=t.find('caption',recursive=False)
        tables.append(dict(num_rows=len(rows),num_cols=max((c['col_end'] for c in cells),default=0),cells=cells,caption=caption.get_text() if caption else None))
        blocks.append({'kind':'table','table_index':ti})
    def visit(node,depth=0):
        require(depth<=200,'NATIVE_HTML_DEPTH_LIMIT')
        if isinstance(node,NavigableString):
            # Comments and processing instructions are not source text.
            if type(node) is NavigableString:parts.append(str(node))
        elif getattr(node,'name',None)=='table':flush();extract(node)
        elif getattr(node,'name',None):
            if node.name in {'p','div','h1','h2','h3','h4','h5','h6','li','br'}:parts.append('\n')
            for child in node.children:visit(child,depth+1)
            if node.name in {'p','div','h1','h2','h3','h4','h5','h6','li'}:parts.append('\n')
    visit(soup.body or soup);flush()
    return blocks,tables,sorted(set(warnings))

def docx(raw):
    preflight_docx(raw)
    doc=Document(io.BytesIO(raw));blocks=[];tables=[];warnings=[];pi=0
    for element in doc.element.body:
        if element.tag==qn('w:p'):
            paragraph=Paragraph(element,doc)
            if paragraph.text:blocks.append({'kind':'text','text':paragraph.text+'\n'})
            pi+=1
        elif element.tag==qn('w:tbl'):
            t=Table(element,doc);ti=len(tables);cells=[];active={}
            require(not any(tc.findall('.//'+qn('w:tbl')) for row in t._tbl.tr_lst for tc in row.tc_lst),'NATIVE_NESTED_TABLE_UNSUPPORTED')
            nr=len(t._tbl.tr_lst);nc=len(t.columns)
            require(0<nr<=1000 and 0<nc<=200 and nr*nc<=MAX_GRID,'NATIVE_TABLE_LIMIT')
            for r,row in enumerate(t._tbl.tr_lst):
                require(not (row.trPr is not None and any(row.trPr.find(qn('w:'+k)) is not None for k in ('gridBefore','gridAfter'))),'NATIVE_RAGGED_TABLE_UNSUPPORTED')
                marker=None if row.trPr is None else row.trPr.find(qn('w:tblHeader'))
                marked=marker is not None and marker.get(qn('w:val'),'true').lower() not in {'0','false','off'}
                col=0;next_active={}
                for ci,tc in enumerate(row.tc_lst):
                    # Legacy hMerge is not normalized by this bounded gridSpan/vMerge adapter.
                    # Never present it as complete, unmerged evidence.
                    require(tc.tcPr is None or tc.tcPr.find(qn('w:hMerge')) is None,
                            'NATIVE_HMERGE_UNSUPPORTED')
                    span=tc.grid_span;vm=tc.vMerge
                    require(1<=span<=200 and col+span<=nc,'NATIVE_SPAN_INVALID')
                    loc={'table_index':ti,'row_index':r,'xml_cell_index':ci,'grid_col_start':col,'grid_col_end':col+span,'vMerge':vm,'gridSpan':span}
                    if vm=='continue':
                        prior=active.get(col)
                        require(prior is not None and prior['col_start']==col and prior['col_end']==col+span,'NATIVE_MERGE_INVALID')
                        prior['row_end']=r+1;prior['locator']['continuations'].append(loc)
                        for cc in range(col,col+span):next_active[cc]=prior
                    else:
                        c=cell(_Cell(tc,t).text,r,r+1,col,col+span,True if marked else None,None,{'tblHeader':marked,'unmarked_semantics':'UNKNOWN'}, {'origin':loc,'continuations':[]})
                        cells.append(c)
                        if vm=='restart':
                            for cc in range(col,col+span):next_active[cc]=c
                    col+=span
                require(col==nc,'NATIVE_RAGGED_TABLE_UNSUPPORTED')
                active=next_active
            # Confirm only a simple complete grid with one explicit first-row
            # w:tblHeader declaration. Ordinary visual/style-only layouts retain
            # UNKNOWN; merged, multi-header and conflicting declarations do too.
            first=[c for c in cells if c['row_start']==0]
            declared=(nr>1 and len(cells)==nr*nc and len(first)==nc
                      and all(c['row_end']==c['row_start']+1 and c['col_end']==c['col_start']+1 for c in cells)
                      and all(c['column_header'] is True and c['text'].strip() for c in first)
                      and len({c['text'] for c in first})==nc
                      and all(c['column_header'] is None for c in cells if c['row_start']>0))
            if declared:
                for c in cells:
                    c['column_header']=c['row_start']==0
                    c['header_evidence'].update(policy='docx-declared-simple-header-v1',header_row=0,source_sha256=hashlib.sha256(raw).hexdigest(),
                                               unmarked_semantics='data-below-declared-header' if c['row_start'] else 'DECLARED')
            tables.append(dict(num_rows=nr,num_cols=nc,cells=cells,caption=None));blocks.append({'kind':'table','table_index':ti})
            if any(c['column_header'] is None for c in cells):warnings.append('DOCX_UNMARKED_HEADERS_UNKNOWN')
    return blocks,tables,sorted(set(warnings))

def xls(raw, first_row_as_header=False):
    """xlrd does not execute macros/formulas or retrieve external links.

    Cached BIFF values do not prove original formula state; mark every legacy
    workbook partial so they cannot become strict numeric witnesses.
    """
    import xlrd
    require(raw[:8]==bytes.fromhex('d0cf11e0a1b11ae1'), 'NATIVE_XLS_SIGNATURE_INVALID')
    try:
        workbook=xlrd.open_workbook(file_contents=raw,formatting_info=True,on_demand=True)
    except xlrd.biffh.XLRDError as exc:
        raise Rejected('NATIVE_PASSWORD_PROTECTED' if 'encrypted' in str(exc).lower() else 'NATIVE_XLS_CORRUPT') from exc
    blocks=[];tables=[];grid=0
    try:
        require(workbook.nsheets<=32,'NATIVE_TABLE_LIMIT')
        for sheet in workbook.sheets():
            if not sheet.nrows or not sheet.ncols:continue
            nr,nc=sheet.nrows,sheet.ncols;grid+=nr*nc
            require(nr<=1000 and nc<=200 and grid<=MAX_GRID,'NATIVE_TABLE_LIMIT')
            merged={};covered=set()
            for r,re,c,ce in sheet.merged_cells:
                require(0<=r<re<=nr and 0<=c<ce<=nc,'NATIVE_SPAN_INVALID')
                merged[(r,c)]=(re,ce)
                for rr in range(r,re):
                    for cc in range(c,ce):
                        require((rr,cc) not in covered,'NATIVE_MERGE_INVALID');covered.add((rr,cc))
            origins=set(merged);cells=[]
            for r in range(nr):
                for c in range(nc):
                    if (r,c) in covered and (r,c) not in origins:continue
                    v=sheet.cell_value(r,c);kind=sheet.cell_type(r,c)
                    if kind==xlrd.XL_CELL_ERROR:v='UNKNOWN_CELL_ERROR'
                    text=str(v) if kind not in (xlrd.XL_CELL_EMPTY,xlrd.XL_CELL_BLANK) else ''
                    re,ce=merged.get((r,c),(r+1,c+1))
                    cells.append(cell(text,r,re,c,ce,True if first_row_as_header and nr>=2 and r==0 else False,None,
                        {'formula_state':'UNKNOWN','cache_state':'UNVERIFIED'}, {'sheet':sheet.name,'row':r+1,'column':c+1}))
            tables.append(dict(num_rows=nr,num_cols=nc,cells=cells,caption=sheet.name))
            blocks.append({'kind':'table','table_index':len(tables)-1})
    finally:workbook.release_resources()
    return blocks,tables,['XLS_FORMULA_CACHE_UNVERIFIED']


def main():
    require(len(sys.argv)==2 and sys.argv[1] in {'html','docx','xls','xls-header'},'NATIVE_FORMAT_INVALID')
    raw=sys.stdin.buffer.read(MAX_INPUT+1);require(len(raw)<=MAX_INPUT,'NATIVE_INPUT_LIMIT')
    selected=sys.argv[1]
    blocks,tables,warnings=(html(raw) if selected=='html' else docx(raw) if selected=='docx' else xls(raw,selected=='xls-header'))
    validate_tables(tables)
    require(sum(len(b.get('text','')) for b in blocks)<=MAX_TEXT,'NATIVE_TEXT_LIMIT')
    payload={'schema_version':1,'format':'xls' if selected.startswith('xls') else selected,'source_sha256':hashlib.sha256(raw).hexdigest(),'blocks':blocks,'tables':tables,'warnings':warnings}
    encoded=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode('utf-8')
    require(len(encoded)<=MAX_OUTPUT,'NATIVE_OUTPUT_LIMIT')
    sys.stdout.buffer.write(encoded)
if __name__=='__main__':
    try:main()
    except Exception as exc:
        # Never echo input, paths, environment or dependency exception text.
        code=str(exc) if isinstance(exc,Rejected) else 'NATIVE_PARSE_REJECTED'
        sys.stderr.write(code);sys.exit(2)
