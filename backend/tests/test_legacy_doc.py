"""SIMULATED conversion transport; DOCX semantics use the real frozen worker."""
import hashlib
import json
import os
from pathlib import Path
import pytest
from backend.app.adapters.parsers import ParserRegistry
from backend.app.adapters.parsers.native import DocxParser
from backend.app.adapters.parsers.doc import DocParser, LibreOfficeDocConverter, ConvertedDocx, validate_ole
from backend.app.domain.parsers import ParserError
from backend.app.domain.chunking import chunk_document
from backend.app.domain.models import NormalizedDocument
from backend.app.application.context_builder import ContextBuilder
from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.application.retrieval import InMemoryRetrievalRepository, HybridRetriever
from backend.app.domain.models import ChunkRecord
from backend.app.domain.scope import Scope

def ole():
    b=bytearray(1024);b[:8]=bytes.fromhex('d0cf11e0a1b11ae1')
    b[26:28]=(3).to_bytes(2,'little');b[28:30]=b'\xfe\xff';b[30:32]=(9).to_bytes(2,'little');b[32:34]=(6).to_bytes(2,'little')
    return bytes(b)

@pytest.mark.parametrize('raw',[b'',b'RTF',b'PK'+bytes(1022),bytes.fromhex('d0cf11e0a1b11ae1'),bytes.fromhex('d0cf11e0a1b11ae1')+bytes(1016)])
def test_reject_false_ole_before_conversion(tmp_path,raw):
    path=tmp_path/'source.doc';path.write_bytes(raw)
    with pytest.raises(ParserError,match='DOC_OLE_SIGNATURE_UNSUPPORTED'):DocParser().parse(path,'d','v')
    assert path.read_bytes()==raw

@pytest.mark.parametrize('media',['application/msword','application/octet-stream'])
def test_registry_doc_gate_preserves_source(tmp_path,media):
    path=tmp_path/'source.doc';path.write_bytes(ole())
    with pytest.raises(ParserError,match='DOC_CONVERSION_UNSUPPORTED'):ParserRegistry().parse(path,media,'d','v')
    assert path.read_bytes()==ole()

def test_real_converter_gate_precedes_runtime(tmp_path):
    converter=LibreOfficeDocConverter(binary=Path('/untrusted'),temp_root=tmp_path)
    with pytest.raises(ParserError,match='DOC_CONVERSION_SAFETY_NOT_VERIFIED'):converter.convert(ole())
    assert list(tmp_path.iterdir())==[]

@pytest.mark.parametrize('timeout',[0,61,float('inf')])
def test_timeout_budget_rejected(tmp_path,timeout):
    with pytest.raises(ValueError):LibreOfficeDocConverter(binary=Path('/usr/bin/soffice'),temp_root=tmp_path,timeout=timeout)

@pytest.mark.parametrize('raw',[b'',b'not-docx'])
def test_invalid_converted_output_is_explicit_failure(tmp_path,raw):
    class Converter:
        def convert(self,source):return ConvertedDocx(raw,'SIMULATED/25.2.3.2')
    p=tmp_path/'old.doc';p.write_bytes(ole())
    runtime=Path(os.environ['RAG_NATIVE_TEST_PYTHON'])
    with pytest.raises(ParserError,match='DOC_CONVERSION_FAILED'):DocParser(python=runtime,converter=Converter()).parse(p,'d','v')

def test_input_limit_precedes_converter(tmp_path):
    p=tmp_path/'old.doc';p.write_bytes(ole()+bytes(8*1024*1024))
    with pytest.raises(ParserError,match='DOC_INPUT_LIMIT'):DocParser().parse(p,'d','v')

def test_profile_explicitly_disables_macros_and_updates(tmp_path):
    import xml.etree.ElementTree as ET
    from backend.app.adapters.parsers.doc import write_profile
    profile=tmp_path/'profile';write_profile(profile)
    tree=ET.parse(profile/'user/registrymodifications.xcu')
    ns='{http://openoffice.org/2001/registry}'
    props={p.get(ns+'name'):p.find('value').text for p in tree.iter('prop')}
    assert props['DisableMacrosExecution']=='true' and props['DisableActiveContent']=='true'
    assert props['Link']=='2' and props['Field']=='false' and props['Chart']=='false'
    assert props['MacroSecurityLevel']=='3'

def test_simulated_doc_conversion_reuses_real_table_citation_chain(tmp_path):
    data=(Path(os.environ['RAG_NATIVE_TEST_FIXTURES'])/'table.docx').read_bytes()
    class Converter:
        def convert(self,source):
            assert source==ole()
            return ConvertedDocx(data,'SIMULATED/LibreOffice25.2.3.2')
    source=tmp_path/'original.doc';source.write_bytes(ole())
    d=ParserRegistry(native_python=Path(os.environ['RAG_NATIVE_TEST_PYTHON']),doc_converter=Converter()).parse(source,'application/msword','d','v')
    assert d.media_type=='application/msword' and d.title=='original' and d.parse_status=='partial'
    lineage=d.conversion_lineage
    assert lineage['original_sha256']==hashlib.sha256(ole()).hexdigest()
    assert lineage['converted_sha256']==hashlib.sha256(data).hexdigest()
    assert lineage['parser_version']=='docx/native-v1'
    assert 'DOC_CONVERTED_LAYOUT_UNVERIFIED' in d.parse_warnings
    assert d.tables[0].source_format=='docx' and len(d.tables[0].cells)==10
    golden=DocxParser(python=Path(os.environ['RAG_NATIVE_TEST_PYTHON'])).parse(Path(os.environ['RAG_NATIVE_TEST_FIXTURES'])/'table.docx','d','v')
    assert d.markdown_content==golden.markdown_content and d.blocks==golden.blocks
    assert [(t.start,t.end,t.cells,t.source_format) for t in d.tables]==[(t.start,t.end,t.cells,t.source_format) for t in golden.tables]
    assert all(loc.source_format=='docx' for loc in d.source_locators)
    assert NormalizedDocument.model_validate_json(d.model_dump_json()).conversion_lineage==lineage
    chunks=chunk_document(d,max_chars=1800)
    assert ''.join(c.content for c in chunks)==d.markdown_content
    assert all(c.source_locator.page is None and c.source_locator.conversion_lineage==lineage for c in chunks)
    row=next(c for c in chunks if c.chunk_type=='table' and '-12.5' in c.content)
    repo=InMemoryRetrievalRepository();repo.add(ChunkRecord('row','kb','d','v',row.content,row.source_locator.model_dump(mode='json')))
    items=HybridRetriever(repo).retrieve(Scope.from_ids(['kb']),'-12.5').items
    service=CitationService(InMemoryCitationStore());context,labels=ContextBuilder().build('run',items,service)
    citation=service.resolve('run',labels[0])
    assert citation.locator['conversion_lineage']==lineage and citation.locator['page'] is None
    assert lineage['original_format']=='ole-doc' and lineage['converted_format']=='docx'
    assert lineage['coordinate_basis']=='converted-docx-not-original-pagination'
    assert citation.locator['source_format']=='docx'
    assert d.markdown_content[citation.locator['start']:citation.locator['end']]==citation.quote
    assert all(c['native_locator']['origin']['table_index']==0 for c in citation.locator['cells'])
    assert next(c for c in citation.locator['cells'] if c['coordinate']=='R3C2')['native_locator']['origin']['row_index']==2
    assert citation.quote==row.content and row.content in context
    assert source.read_bytes()==ole()
    evidence=os.getenv('RAG_NATIVE_EVIDENCE_DIR')
    if evidence:
        (Path(evidence)/'doc-lineage-chain.json').write_text(json.dumps({'fixture_kind':'SIMULATED_CONVERSION_REAL_DOCX_WORKER','original_to_converted':lineage,'normalized':d.model_dump(mode='json'),'chunks':[c.model_dump(mode='json') for c in chunks],'context':context,'citation':{'quote':citation.quote,'locator':citation.locator}},ensure_ascii=False,indent=2),encoding='utf-8')
