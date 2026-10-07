"""Non-factual explanation for a narrowly diagnosed unknown table header.

This never returns facts, validates an answer, changes admission, or creates a
source header. Raw rows are inspected only to avoid misleading format advice.
"""
import hashlib,re
from backend.app.application.structured_evidence import _columns,_coordinate,_decimal

from backend.app.domain.evidence_hint import REASON, valid_hint

def header_hint(chunks,targets):
    if not targets or any(t.period is None for t in targets):return None
    groups={}
    for chunk in chunks:
        loc=chunk.locator;fmt=loc.get('source_format');warnings=loc.get('parse_warnings')
        if (not chunk.is_current or loc.get('kind')!='table' or fmt not in {'pdf','docx'}
                or loc.get('parse_status')!='partial' or loc.get('quote')!=chunk.content
                or chunk.content_sha256!=hashlib.sha256(chunk.content.encode()).hexdigest()
                or not isinstance(warnings,(list,tuple)) or not warnings):continue
        policy=('pymupdf-inferred-semantic-UNKNOWN' if fmt=='pdf' else 'declared-tblHeader-otherwise-UNKNOWN')
        if loc.get('header_detection')!=policy:continue
        if not all(isinstance(w,str) and (re.fullmatch(r'PDF_HEADERS_INFERRED_UNKNOWN:page=[1-9]\d*',w) if fmt=='pdf' else w=='DOCX_UNMARKED_HEADERS_UNKNOWN') for w in warnings):continue
        cells=loc.get('cells');table=loc.get('table_id')
        if not isinstance(table,str) or not table or not isinstance(cells,(list,tuple)) or not 1<=len(cells)<=64:continue
        if any(not isinstance(c,dict) or type(c.get('row')) is not int or type(c.get('column')) is not int
               or not 1<=c['column']<=32 or _coordinate(c.get('coordinate',''))!=(c['row'],c['column'])
               or c.get('row_span',1)!=1 or c.get('column_span',1)!=1 or c.get('column_header') is not None
               or not isinstance(c.get('display'),str) or c.get('value')!=c.get('display') for c in cells):continue
        key=(chunk.document_id,chunk.version_id,table,fmt)
        group=groups.setdefault(key,{'cells':{},'conflict':False})
        for cell in cells:
            point=(cell['row'],cell['column'])
            if point in group['cells'] and group['cells'][point]!=cell:group['conflict']=True
            group['cells'][point]=cell
    formats=set()
    for target in targets:
        matched=False
        for key,group in groups.items():
            if group['conflict']:continue
            rows={}
            for (r,c),cell in group['cells'].items():rows.setdefault(r,{})[c]=cell
            for hr,headers in rows.items():
                columns=sorted(headers)
                if columns!=list(range(1,len(columns)+1)):continue
                roles=_columns([headers[c]['display'] for c in columns],target)
                if roles is None:continue
                entity,month,amount,_=roles
                for r,data in rows.items():
                    if r<=hr or sorted(data)!=columns:continue
                    if (data[entity+1]['display']==target.subject and data[month+1]['display']==target.period
                            and _decimal(data[amount+1]['display']) is not None):
                        formats.add(key[3]);matched=True
            if matched:break
        # Avoid explaining a mixed unrelated refusal solely as a header problem.
        if not matched:return None
    names='/'.join(f.upper() for f in sorted(formats))
    text=(f'检索到相关 {names} 表格，但目前无法可靠确认表头与数据列、单位的对应关系，因此无法核验本题的表格证据。'
          '请将相关表格另存为 XLSX 后上传，再提问；转换不保证能够答对。')
    return {'kind':'evidence_hint','reason_code':REASON,'source_formats':sorted(formats),'text':text}
