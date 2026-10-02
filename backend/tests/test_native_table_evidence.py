"""SIMULATED offline native-table chain and safety admission tests."""
import hashlib
import io
import json
import os
from pathlib import Path
import zipfile
import pytest
from backend.app.adapters.parsers import ParserRegistry, HtmlParser, DocxParser
from backend.app.adapters.parsers.native import validate_result, run_worker
from backend.app.domain.chunking import chunk_document
from backend.app.domain.models import ChunkRecord, NormalizedDocument
from backend.app.domain.parsers import ParserError
from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.application.context_builder import ContextBuilder
from backend.app.application.retrieval import HybridRetriever, InMemoryRetrievalRepository
from backend.app.domain.scope import Scope

@pytest.fixture
def runtime():
    value=os.getenv('RAG_NATIVE_TEST_PYTHON')
    if not value:pytest.skip('explicit native test runtime required')
    return Path(value)
@pytest.fixture
def fixtures():
    value=os.getenv('RAG_NATIVE_TEST_FIXTURES')
    if not value:pytest.skip('frozen synthetic fixture directory required')
    return Path(value)

def parse_html(tmp_path,runtime,html):
    source=tmp_path/'synthetic.html';source.write_bytes(html.encode('utf-8'))
    return HtmlParser(python=runtime).parse(source,'document','v1')

def modify_docx(tmp_path,fixtures,change):
    path=tmp_path/'synthetic.docx'
    with zipfile.ZipFile(fixtures/'table.docx') as src,zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as dst:
        for name in src.namelist():dst.writestr(name,change(name,src.read(name)))
    return path

@pytest.mark.parametrize('format',['html','docx'])
def test_frozen_source_structure_chunk_context_citation(runtime,fixtures,format):
    gold=json.loads((fixtures/'expected.json').read_text(encoding='utf-8'))
    source=fixtures/f'table.{format}'
    assert hashlib.sha256(source.read_bytes()).hexdigest()==gold['files'][source.name]
    d=ParserRegistry(native_python=runtime).parse(source,'application/octet-stream','document','v1')
    t=d.tables[0];expected=gold['table']
    assert t.cell_range=='R1C1:R4C3' and len(t.cells)==10
    assert [(c.value,c.row-1,c.row-1+c.row_span,c.column-1,c.column-1+c.column_span) for c in t.cells]==[(c['text'],c['row_start'],c['row_end'],c['col_start'],c['col_end']) for c in expected['cells']]
    assert t.merged_ranges==('R1C1:R2C1','R1C2:R1C3')
    assert all(v in d.markdown_content for v in gold['context'])
    assert NormalizedDocument.model_validate_json(d.model_dump_json()).tables==d.tables
    if format=='html':
        assert t.header_rows==(1,2)
        assert [c.column_header for c in t.cells]==[c.get('column_header',False) for c in expected['cells']]
        assert t.cells[5].column_headers==('季度','Q1')
    else:
        assert t.header_rows==() and all(c.column_header is None for c in t.cells)
        assert d.parse_status=='partial' and 'DOCX_UNMARKED_HEADERS_UNKNOWN' in d.parse_warnings
    chunks=chunk_document(d,max_chars=1800)
    assert [c.chunk_type for c in chunks]==['text','table','table','table','table','text'] if format=='html' else [c.chunk_type for c in chunks]==['text','text','text','table','table','table','table','text']
    assert all(d.markdown_content[c.start:c.end]==c.content for c in chunks)
    assert ''.join(c.content for c in chunks)==d.markdown_content
    row=next(c for c in chunks if c.chunk_type=='table' and c.source_locator.cell_range.startswith('R3C1:'))
    assert '甲产品' in row.content and '-12.5' in row.content and '25%' in row.content
    locator=json.loads(row.source_locator.model_dump_json())
    assert locator['page'] is None and locator['source_format']==format
    assert locator['parse_status']==d.parse_status and locator['parse_warnings']==list(d.parse_warnings)
    assert next(c for c in locator['cells'] if c['coordinate']=='R3C2')['native_locator']
    repo=InMemoryRetrievalRepository();record=ChunkRecord('row','kb','document','v1',row.content,locator)
    repo.add(record);repo.add(ChunkRecord('private','other','document','v1',row.content,locator))
    items=HybridRetriever(repo).retrieve(Scope.from_ids(['kb']),'甲产品').items
    assert [i.chunk.chunk_id for i in items]==['row']
    service=CitationService(InMemoryCitationStore())
    context,labels=ContextBuilder().build('run',items,service)
    detail=service.resolve('run',labels[0])
    assert detail.locator==locator and detail.quote==row.content and row.content in context
    assert 'HEADER_UNAVAILABLE' in context if format=='docx' else '季度 > Q1' in context
    output=os.getenv('RAG_NATIVE_EVIDENCE_DIR')
    if output:
        evidence={'fixture_kind':'SIMULATED','normalized':d.model_dump(mode='json'),
            'chunks':[c.model_dump(mode='json') for c in chunks], 'context':context,
            'citation':{'quote':detail.quote,'locator':detail.locator,'version_id':detail.version_id}}
        (Path(output)/f'{format}-chain.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')

def test_unconfigured_and_unavailable_runtime_fail_closed(tmp_path,monkeypatch):
    monkeypatch.delenv('RAG_NATIVE_TABLE_PYTHON',raising=False)
    path=tmp_path/'source.html';path.write_text('<p>preserved</p>')
    before=path.read_bytes()
    with pytest.raises(ParserError,match='NOT_CONFIGURED'):HtmlParser().parse(path,'d','v')
    with pytest.raises(ParserError,match='UNAVAILABLE'):HtmlParser(python=tmp_path/'absent.exe').parse(path,'d','v')
    assert path.read_bytes()==before

@pytest.mark.parametrize('suffix,media,text',[('.txt','text/plain','literal zero 0'),('.md','text/markdown','# heading\nChinese 文本')])
def test_text_markdown_registry_unchanged(tmp_path,suffix,media,text):
    p=tmp_path/('source'+suffix);p.write_text(text,encoding='utf-8')
    d=ParserRegistry().parse(p,media,'d','v')
    assert d.markdown_content==text and not d.tables and chunk_document(d)

@pytest.mark.parametrize('html,code',[
    ('<table><tr><td><table><tr><td>x</td></tr></table></td></tr></table>','NESTED'),
    ('<table><tr><td rowspan="999999">x</td></tr></table>','SPAN'),
    ('<table><tr><td>x</td><td>y</td></tr><tr><td>z</td></tr></table>','RAGGED'),
    ('<table><tr><td>'+('x'*4097)+'</td></tr></table>','TEXT_LIMIT'),
])
def test_html_rejections(tmp_path,runtime,html,code):
    with pytest.raises(ParserError,match=code):parse_html(tmp_path,runtime,html)

def test_html_inert_sources_and_whitespace(tmp_path,runtime):
    d=parse_html(tmp_path,runtime,'<p>before</p><script>secret()</script><img src="file:///never-read"><iframe src="https://never-fetch"></iframe><table><tr><th scope="col"> Label </th></tr><tr><td>  0 \n </td></tr><tr><td></td></tr></table><p>after</p>')
    assert d.tables[0].cells[1].value=='  0 \n ' and d.tables[0].cells[2].value==''
    assert 'secret' not in d.markdown_content and d.parse_status=='partial'
    assert 'before' in chunk_document(d)[0].content and 'after' in chunk_document(d)[-1].content

@pytest.mark.parametrize('kind',['dtd','external','macro','field','nested','bomb'])
def test_docx_preflight_rejections(tmp_path,runtime,fixtures,kind):
    def change(name,data):
        if kind=='dtd' and name=='word/document.xml':return b'<!DOCTYPE x [<!ENTITY e "boom">]><x>&e;</x>'
        if kind=='external' and name=='word/_rels/document.xml.rels':return data.replace(b'</Relationships>',b'<Relationship Id="evil" Type="test" Target="file:///never-read" TargetMode="External"/></Relationships>')
        if kind=='macro' and name=='[Content_Types].xml':return data.replace(b'</Types>',b'<Override PartName="/word/vbaProject.bin" ContentType="test"/></Types>')
        if kind=='field' and name=='word/document.xml':return data.replace(b'<w:body>',b'<w:body><w:p><w:fldSimple w:instr="test"/></w:p>')
        if kind=='nested' and name=='word/document.xml':return data.replace(b'<w:tc>',b'<w:tc><w:tbl/>',1)
        if kind=='bomb' and name=='word/document.xml':return b'0'*1000000
        return data
    path=modify_docx(tmp_path,fixtures,change)
    if kind=='macro':
        with zipfile.ZipFile(path,'a') as z:z.writestr('word/vbaProject.bin',b'inert')
    with pytest.raises(ParserError):DocxParser(python=runtime).parse(path,'d','v')

def test_docx_declared_header_and_no_page(tmp_path,runtime,fixtures):
    def change(name,data):
        if name=='word/document.xml':return data.replace(b'<w:tr>',b'<w:tr><w:trPr><w:tblHeader/></w:trPr>',1)
        return data
    d=DocxParser(python=runtime).parse(modify_docx(tmp_path,fixtures,change),'d','v')
    assert d.tables[0].header_rows==(1,) and d.tables[0].cells[0].column_header is True
    assert all(c.source_locator.page is None for c in chunk_document(d,max_chars=1800))

@pytest.mark.parametrize('payload',[{}, {'schema_version':2}, {'schema_version':True}])
def test_invalid_schema_is_rejected(payload):
    with pytest.raises(ParserError,match='SCHEMA'):validate_result(json.dumps(payload).encode(),b'source','html')

def test_input_budget_rejected_before_launch(tmp_path,runtime,monkeypatch):
    import backend.app.adapters.parsers.native as adapter
    monkeypatch.setattr(adapter,'MAX_INPUT',10)
    monkeypatch.setattr(adapter,'run_worker',lambda *a:pytest.fail('must not launch'))
    source=tmp_path/'source.html';source.write_bytes(b'x'*11)
    with pytest.raises(ParserError,match='INPUT_LIMIT'):HtmlParser(python=runtime).parse(source,'d','v')

def test_transport_timeout_and_output_budget(runtime,monkeypatch):
    import backend.app.adapters.parsers.native as adapter
    with pytest.raises(ParserError,match='TIMEOUT'):run_worker(runtime,b'<p>x</p>','html',0.000001)
    monkeypatch.setattr(adapter,'MAX_OUTPUT',16)
    with pytest.raises(ParserError,match='OUTPUT_LIMIT'):run_worker(runtime,b'<p>x</p>','html',20)

@pytest.mark.parametrize('mutation',['version-bool','hash','format','overlap','missing-block','page','cell-extra','span'])
def test_full_neutral_schema_boundary(mutation):
    raw=b'synthetic'
    c={'text':'x','row_start':0,'row_end':1,'col_start':0,'col_end':1,'column_header':False,'row_header':False,'header_evidence':{},'locator':{}}
    payload={'schema_version':1,'format':'html','source_sha256':hashlib.sha256(raw).hexdigest(),'blocks':[{'kind':'table','table_index':0}], 'tables':[{'num_rows':1,'num_cols':1,'cells':[c],'caption':None}],'warnings':[]}
    if mutation=='version-bool':payload['schema_version']=True
    if mutation=='hash':payload['source_sha256']='0'*64
    if mutation=='format':payload['format']='docx'
    if mutation=='overlap':payload['tables'][0]['cells'].append(dict(c))
    if mutation=='missing-block':payload['blocks']=[]
    if mutation=='page':payload['tables'][0]['page']=1
    if mutation=='cell-extra':c['shell']='never execute'
    if mutation=='span':c['row_end']=2
    with pytest.raises(ParserError,match='SCHEMA'):validate_result(json.dumps(payload).encode(),raw,'html')

# Review-01 P1 regressions: SIMULATED source documents, no answer generation.
def merge_docx(tmp_path,fixtures,full_row=False,hmerge=False,marked=False):
    def tc(text,properties=''):
        return '<w:tc><w:tcPr>'+properties+'</w:tcPr><w:p><w:r><w:t>'+text+'</w:t></w:r></w:p></w:tc>'
    if hmerge:
        rows='<w:tr>'+('<w:trPr><w:tblHeader/></w:trPr>' if marked else '')+tc('Widget','<w:hMerge w:val="restart"/>')+tc('','<w:hMerge/>')+'</w:tr>'
    elif full_row:
        rows='<w:tr>'+tc('Widget','<w:vMerge w:val="restart"/>')+tc('20','<w:vMerge w:val="restart"/>')+'</w:tr><w:tr>'+tc('','<w:vMerge/>')+tc('','<w:vMerge/>')+'</w:tr>'
    else:
        rows='<w:tr>'+tc('Widget','<w:vMerge w:val="restart"/>')+tc('Q1=10')+'</w:tr><w:tr>'+tc('','<w:vMerge/>')+tc('Q2=20')+'</w:tr>'
    xml=('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl><w:tblGrid><w:gridCol w:w="2000"/><w:gridCol w:w="2000"/></w:tblGrid>'+rows+'</w:tbl></w:body></w:document>').encode()
    return modify_docx(tmp_path,fixtures,lambda name,data:xml if name=='word/document.xml' else data)

@pytest.mark.parametrize('format',['html','docx'])
@pytest.mark.parametrize('full_row',[False,True],ids=['label-continuation','fully-covered-row'])
def test_review_vertical_merge_row_context_and_origin_citation(tmp_path,runtime,fixtures,format,full_row):
    if format=='docx':
        path=merge_docx(tmp_path,fixtures,full_row=full_row)
        d=DocxParser(python=runtime).parse(path,'document','v1')
    else:
        html=('<table><tr><td rowspan="2">Widget</td><td rowspan="2">20</td></tr><tr></tr></table>' if full_row else '<table><tr><th scope="col">Product</th><th scope="col">Quarter</th><th scope="col">Revenue</th></tr><tr><td rowspan="2">Widget</td><td>Q1</td><td>10</td></tr><tr><td>Q2</td><td>20</td></tr></table>')
        d=parse_html(tmp_path,runtime,html)
    table=d.tables[0]
    expected_rows=2 if format=='docx' or full_row else 3
    origin=next(c for c in table.cells if c.value=='Widget')
    assert sum(c.value=='Widget' for c in table.cells)==1 and origin.row_span==2
    chunks=chunk_document(d,max_chars=1800)
    row=chunks[-1]
    locator=json.loads(row.source_locator.model_dump_json())
    repo=InMemoryRetrievalRepository();repo.add(ChunkRecord('continuation','kb','document','v1',row.content,locator))
    service=CitationService(InMemoryCitationStore())
    context,labels=ContextBuilder().build('review-p1',HybridRetriever(repo).retrieve(Scope.from_ids(['kb']),'20').items,service)
    detail=service.resolve('review-p1',labels[0])
    output=os.getenv('RAG_NATIVE_EVIDENCE_DIR')
    if output:
        evidence={'fixture_kind':'SIMULATED','normalized':d.model_dump(mode='json'),'chunks':[c.model_dump(mode='json') for c in chunks],'context':context,'citation':detail.__dict__}
        (Path(output)/f'p1-{format}-{full_row}.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    assert len(chunks)==expected_rows
    assert 'Widget' in context and '20' in context
    if not full_row:assert 'Q2' in context
    assert locator['cell_range'].startswith(f'R{expected_rows}C1:')
    projected=next(c for c in locator['cells'] if c['coordinate']==origin.coordinate)
    assert projected==origin.model_dump(mode='json')
    assert projected['row']==origin.row and projected['row_span']==2
    if format=='docx':assert len(projected['native_locator']['continuations'])==1
    assert detail.locator==locator and detail.quote==row.content
    assert all(d.markdown_content[c.start:c.end]==c.content for c in chunks)
    assert ''.join(c.content for c in chunks)==d.markdown_content

@pytest.mark.parametrize('marked',[False,True],ids=['unknown-header','declared-header'])
def test_review_hmerge_fails_explicitly_not_complete(tmp_path,runtime,fixtures,marked):
    path=merge_docx(tmp_path,fixtures,hmerge=True,marked=marked)
    before=path.read_bytes()
    with pytest.raises(ParserError,match='^NATIVE_HMERGE_UNSUPPORTED$'):
        DocxParser(python=runtime).parse(path,'document','v1')
    assert path.read_bytes()==before

@pytest.mark.parametrize('row_span,column_span',[(1001,1),(1,201),(1000,51)],ids=['row-bound','column-bound','projection-work'])
def test_review_projection_work_is_bounded(row_span,column_span):
    from backend.app.domain.models import DocumentTable,TableCell
    from backend.app.domain.table_evidence import table_row_texts
    cell=TableCell(coordinate='R1C1',row=1,column=1,value='Widget',value_type='text',display='Widget',row_span=row_span,column_span=column_span)
    table=DocumentTable(table_id='synthetic',source_format='html',cell_range='synthetic',cells=[cell],start=0,end=0)
    with pytest.raises(ValueError,match='^NATIVE_TABLE_ROW_PROJECTION_LIMIT$'):table_row_texts(table)

def test_review_xlsx_row_projection_preserves_original_rendering_and_chunk_contract():
    import runpy
    from backend.tests.test_xlsx_table_evidence import parsed
    from backend.app.domain.table_evidence import table_row_texts
    before=os.getenv('RAG_NATIVE_REPAIR_BEFORE')
    if not before:pytest.skip('explicit pre-repair renderer required for byte-exact comparison')
    old=runpy.run_path(str(Path(before)/'backend/app/domain/table_evidence.py'))['table_row_texts']
    d=parsed()
    for table in d.tables:
        assert table.source_format is None and table_row_texts(table)==old(table)
    chunks=chunk_document(d)
    assert ''.join(c.content for c in chunks)==d.markdown_content
    assert all(c.chunk_type=='table' and c.source_locator.sheet for c in chunks)
    assert all(d.markdown_content[c.start:c.end]==c.content for c in chunks)
