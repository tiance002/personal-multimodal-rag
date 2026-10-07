"""Presentation contract for a non-factual table header diagnostic."""
REASON='TABLE_HEADER_UNCONFIRMED'
def valid_hint(value):
    if not isinstance(value,dict) or value.get('kind')!='evidence_hint' or value.get('reason_code')!=REASON:return False
    formats=value.get('source_formats');text=value.get('text')
    return (isinstance(formats,list) and 1<=len(formats)<=2 and all(f in {'pdf','docx'} for f in formats)
            and len(set(formats))==len(formats) and isinstance(text,str) and 0<len(text.strip())<=600)
