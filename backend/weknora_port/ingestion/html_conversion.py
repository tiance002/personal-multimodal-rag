"""Python semantic port of fixed OCR/table business rules; pinned renderer library.

Only primitive HTML rendering is a local hash-bound library codec, analogous to
PDFium's native dependency. It is not the Go source oracle/application backend.
No provider, network, filesystem instruction or Agent-tool interface exists.
"""
import hashlib,json,os,re,subprocess
from pathlib import Path
from ..retrieval.query import go_strip

TABLE=re.compile(r'<table\b[^>]*>.*?</table>',re.I|re.S|re.ASCII)
FENCE=re.compile(r'(?:```|~~~)[^\n]*\n.*?(?:```|~~~)',re.S)
LAYOUT=re.compile(r"\s+(?:style|class|align|valign|width|height|bgcolor)\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)",re.I|re.S|re.ASCII)
SPAN=re.compile(r"\b(?:row|col)span\s*=\s*[\"']?(?:[2-9]|\d{2,})",re.I|re.ASCII)
ROW=re.compile(r'<tr\b',re.I|re.ASCII)
SEPARATOR=re.compile(r'^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$',re.M|re.ASCII)
IMAGE=re.compile(r'!\\\[(.*?)\\?\]\(([^()\n]*(?:\([^)]*\)[^()\n]*)*)\)')


class ConversionFailed(Exception):pass


def convert_html(content,*,table=False):
    from ..documents import DocumentLifecycleError
    # Server environment only; upload/model text cannot select a program or arguments.
    name=os.environ.get('WEKNORA_HTML_CODEC_PATH','');expected=os.environ.get('WEKNORA_HTML_CODEC_SHA256','')
    if not name or not re.fullmatch('[0-9a-f]{64}',expected):raise DocumentLifecycleError('OCR_HTML_SOURCE_CONVERTER_REQUIRED')
    path=Path(name)
    if not path.is_absolute() or not path.is_file():raise DocumentLifecycleError('OCR_HTML_SOURCE_CONVERTER_REQUIRED')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise DocumentLifecycleError('OCR_HTML_CODEC_HASH_MISMATCH')
    options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
    try:
        result=subprocess.run([str(path)],input=json.dumps({'html':content,'table':table},ensure_ascii=False).encode(),
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15,shell=False,**options)
    except (OSError,subprocess.TimeoutExpired):raise DocumentLifecycleError('OCR_HTML_CODEC_UNAVAILABLE') from None
    if result.returncode:raise DocumentLifecycleError('OCR_HTML_CODEC_FAILED')
    try:value=json.loads(result.stdout)
    except (ValueError,UnicodeError):raise DocumentLifecycleError('OCR_HTML_CODEC_RESPONSE_INVALID') from None
    if value.get('error')=='CONVERSION_FAILED':raise ConversionFailed()
    if not isinstance(value.get('text'),str):raise DocumentLifecycleError('OCR_HTML_CODEC_RESPONSE_INVALID')
    return value['text']


def split_table_rows(block):
    output=[];last=0
    for match in ROW.finditer(block):
        output.append(block[last:match.start()])
        if match.start()==0 or block[match.start()-1]!='\n':output.append('\n')
        output.append(match[0]);last=match.end()
    output.append(block[last:]);return go_strip(''.join(output))


def normalize_one_table(block):
    def fallback():return split_table_rows(LAYOUT.sub('',block))
    if SPAN.search(block):return fallback()
    try:text=convert_html(block,table=True)
    except ConversionFailed:return fallback()
    text=IMAGE.sub(lambda m:'!['+m[1]+']('+m[2]+')',go_strip(text))
    if not text or not SEPARATOR.search(text):return fallback()
    return text


def normalize_html_tables(markdown):
    if '<table' not in markdown.lower():return markdown
    matches=list(TABLE.finditer(markdown))
    if not matches:return markdown
    fences=[(match.start(),match.end()) for match in FENCE.finditer(markdown)]
    output='';last=0
    for match in matches:
        output+=markdown[last:match.start()]
        if any(a<=match.start()<b for a,b in fences):
            output+=match[0];last=match.end();continue
        output=output.rstrip('\n')+'\n\n'+normalize_one_table(match[0])
        last=match.end()
        while last<len(markdown) and markdown[last]=='\n':last+=1
        output+='\n\n'
    return output+markdown[last:]
