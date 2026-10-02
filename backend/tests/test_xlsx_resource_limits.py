import zipfile
from pathlib import Path
import pytest
import openpyxl
from backend.app.adapters.parsers.xlsx import XlsxParser
from backend.app.domain.parsers import ParserError

BASE=Path(r'E:\codex_workspace\2026-10-01\task-2\m2-table\frozen-sales.xlsx')
NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'

def fixture(tmp_path, sheet=None, shared=None, workbook=None, styles=None):
    path=tmp_path/'synthetic.xlsx'
    replacements={}
    if sheet is not None: replacements['xl/worksheets/sheet1.xml']=f'<worksheet xmlns="{NS}">{sheet}</worksheet>'
    if shared is not None: replacements['xl/sharedStrings.xml']=f'<sst xmlns="{NS}">{shared}</sst>'
    if workbook is not None: replacements['xl/workbook.xml']=workbook
    if styles is not None: replacements['xl/styles.xml']=f'<styleSheet xmlns="{NS}">{styles}</styleSheet>'
    with zipfile.ZipFile(BASE) as src, zipfile.ZipFile(path,'w',zipfile.ZIP_STORED) as out:
        for name in src.namelist(): out.writestr(name,replacements.pop(name,src.read(name)))
        for name,value in replacements.items(): out.writestr(name,value)
    return path

def reject_before_load(path, monkeypatch, error='XLSX_SHEET_LIMIT',parser=None):
    calls=[]
    def loader(*args,**kwargs):
        calls.append(kwargs)
        pytest.fail('PREALLOCATION_GUARD_MISSING: loader reached')
    monkeypatch.setattr(openpyxl,'load_workbook',loader)
    with pytest.raises(ParserError,match=error): (parser or XlsxParser()).parse(path,'synthetic','v1')
    assert calls==[]
    print(f'PRELOAD_REJECT fixture={path.name} bytes={path.stat().st_size} error={error} loader_calls=0')

@pytest.mark.parametrize('ref',['A1:XFD1048576','A1:GS1','A1:B2 A3:B4'])
def test_hyperlink_preallocation(tmp_path,monkeypatch,ref):
    reject_before_load(fixture(tmp_path,f'<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>kept</t></is></c></row></sheetData><hyperlinks><hyperlink ref="{ref}" location="A1"/></hyperlinks>'),monkeypatch)

@pytest.mark.parametrize('merges',['A1:B2','A1:C3'])
def test_duplicate_or_overlap_merge_preallocation(tmp_path,monkeypatch,merges):
    reject_before_load(fixture(tmp_path,f'<sheetData/><mergeCells><mergeCell ref="A1:B2"/><mergeCell ref="{merges}"/></mergeCells>'),monkeypatch)

def test_aggregate_preallocation(tmp_path,monkeypatch):
    # Existing tiny review fixture: three 50,000-slot merge rectangles, no expansion here.
    path=Path(r'D:\codex_workspace\2026-10-01\rag-m2-table-review\RAG-M2-TABLE-REVIEW-01-rev1attempt1\fixtures\three-sheets-aggregate-blocked.xlsx')
    reject_before_load(path,monkeypatch)

@pytest.mark.parametrize('body',['<sheetData><row r="10001"><c t="inlineStr"><is><t>x</t></is></c></row></sheetData>', '<sheetData><row r="1">'+''.join('<c><v>1</v></c>' for _ in range(201))+'</row></sheetData>', '<sheetData><row r="1"><c r="XFD1048576"><v>1</v></c></row></sheetData>'])
def test_sparse_and_implicit_index_preallocation(tmp_path,monkeypatch,body):
    reject_before_load(fixture(tmp_path,body),monkeypatch)

@pytest.mark.parametrize('kind',['shared','unused_shared','rich','formula'])
def test_final_text_preallocation(tmp_path,monkeypatch,kind):
    shared='<si><t>'+('x'*4097)+'</t></si>' if 'shared' in kind else None
    data='<c r="A2" t="s"><v>0</v></c>' if kind=='shared' else '<c r="A2"><v>1</v></c>'
    if kind=='rich':data='<c r="A2" t="inlineStr"><is><r><t>'+('x'*2048)+'</t></r><r><t>'+('y'*2049)+'</t></r></is></c>'
    if kind=='formula':data='<c r="A2"><f>'+('x'*4097)+'</f><v>1</v></c>'
    reject_before_load(fixture(tmp_path,'<sheetData><row r="2">'+data+'</row></sheetData>',shared),monkeypatch,'XLSX_TEXT_LIMIT')

@pytest.mark.parametrize('kind',['shared_total','inline_total','shared_count','styles'])
def test_cumulative_resources_preallocation(tmp_path,monkeypatch,kind):
    parser=XlsxParser()
    # Deliberately reduced budgets prove accumulation with tiny fixtures.
    monkeypatch.setattr(parser,'MAX_TEXT_TOTAL',7 if kind in {'shared_total','inline_total'} else 1024*1024,raising=False)
    monkeypatch.setattr(parser,'MAX_STRINGS',2,raising=False)
    monkeypatch.setattr(parser,'MAX_STYLES',2 if kind=='styles' else 10000,raising=False)
    shared='<si><t>abcd</t></si><si><t>efgh</t></si>' if kind=='shared_total' else '<si/><si/><si/>' if kind=='shared_count' else None
    body='<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>abcd</t></is></c><c r="B1" t="inlineStr"><is><t>efgh</t></is></c></row></sheetData>' if kind=='inline_total' else '<sheetData/>'
    styles='<fonts><font/><font/><font/></fonts>' if kind=='styles' else None
    reject_before_load(fixture(tmp_path,body,shared,styles=styles),monkeypatch,'XLSX_TEXT_LIMIT' if kind!='styles' else 'XLSX_STYLE_LIMIT',parser)

def test_repeated_hyperlinks_work_preallocation(tmp_path,monkeypatch):
    parser=XlsxParser();monkeypatch.setattr(parser,'MAX_RANGE_WORK',7,raising=False)
    reject_before_load(fixture(tmp_path,'<sheetData/><hyperlinks><hyperlink ref="A1:B2" location="A1"/><hyperlink ref="A1:B2" location="A1"/></hyperlinks>'),monkeypatch,parser=parser)

def test_valid_link_and_rich_boundary_keep_text(tmp_path):
    body='<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Header</t></is></c><c r="B1" t="inlineStr"><is><t>Other</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><r><t>'+('x'*2048)+'</t></r><r><t>'+('y'*2048)+'</t></r></is></c></row></sheetData><hyperlinks><hyperlink ref="A2:B2" location="A2"/></hyperlinks>'
    document=XlsxParser().parse(fixture(tmp_path,body),'synthetic','v1')
    assert next(c for c in document.tables[0].cells if c.coordinate=='A2').value=='x'*2048+'y'*2048

def test_shared_rich_boundary_keep_text(tmp_path):
    shared='<si><r><t>'+('x'*2048)+'</t></r><r><t>'+('y'*2048)+'</t></r></si>'
    # Direct preflight also verifies unreferenced legal shared strings.
    XlsxParser()._preflight(fixture(tmp_path,'<sheetData/>',shared))
