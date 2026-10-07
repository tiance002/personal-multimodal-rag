"""SIMULATED caption structures; real chunk/citation code, zero model calls."""
import hashlib
import json
import os
import runpy
from pathlib import Path

import fitz
import pytest
from PIL import Image

from backend.app.adapters.parsers import ImageParser, PdfParser
from backend.app.application.citations import CitationService, InMemoryCitationStore
from backend.app.domain.chunking import chunk_document
from backend.app.domain.models import (ChunkRecord, DocumentAsset, DocumentBlock,
    DocumentSection, DocumentTable, NormalizedDocument, SourceLocator, TableCell)
from backend.app.domain.table_evidence import table_row_texts

MARKER = "【模型生成的图像描述，非原文；请对照原图】\n"


def synthetic_document(*, mixed=False, unknown=False, count=1, native_tail=False):
    native = "原生段落。\n"
    ocr = "样本LX-204需四摄氏\n度保存。\n"
    sections = [DocumentSection(section_id='native', heading='', level=0,
        start=0, end=len(native), page_start=1, page_end=1),
        DocumentSection(section_id='ocr', heading='', level=0, start=len(native),
        end=len(native+ocr), page_start=3, page_end=3, content_type='image_ocr', asset_id='ocr-source')]
    text = native + ocr
    tables = []
    blocks = [DocumentBlock(block_id='base', kind='text', start=0, end=len(text))]
    if mixed:
        cell = TableCell(coordinate='R1C1', row=1, column=1, value='80',
                         value_type='string', display='80')
        table = DocumentTable(table_id='native-table', cell_range='R1C1:R1C1',
            source_format='pdf', page=2, cells=[cell], start=len(text), end=len(text))
        rendered = ''.join(s for _, s in table_row_texts(table))
        table = table.model_copy(update={'end':len(text)+len(rendered)})
        tables.append(table)
        blocks.append(DocumentBlock(block_id='table', kind='table', table_id=table.table_id,
            start=len(text), end=len(text)+len(rendered)))
        text += rendered
    if native_tail:
        start=len(text);text+='附加原生行。\n'
        sections.append(DocumentSection(section_id='native-tail',heading='',level=0,
            start=start,end=len(text),page_start=4,page_end=4))
        blocks.append(DocumentBlock(block_id='native-tail',kind='text',start=start,end=len(text)))
    assets, locators = [], []
    for i in range(count):
        source_bytes = f'SIMULATED image bytes {i}'.encode()
        digest = hashlib.sha256(source_bytes).hexdigest()
        bbox = None if unknown else (20.0+i*100, 80.0, 90.0+i*100, 160.0)
        basis = 'UNKNOWN' if unknown else 'pdf-points-top-left-unrotated'
        source = DocumentAsset(asset_id=f'image-{i}', asset_type='source_image', page_no=4,
            source_bytes=source_bytes, source_locator={'sha256':digest,'page':4,'bbox':bbox,
                                                       'coordinate_basis':basis})
        metadata = {'schema_version':'local-caption/v1', 'evidence_kind':'model_generated_caption',
            'source_image_sha256':digest, 'input_image_sha256':digest, 'page':4, 'bbox':bbox,
            'coordinate_basis':basis, 'model_requested':'SIMULATED-model',
            'model_reported':'SIMULATED-model','model_digest':'UNKNOWN',
            'prompt_version':'local-figure-caption/v1','validation_status':'UNVERIFIED'}
        raw_caption = f'图{i}整体上升，最高月份M6。'
        caption = DocumentAsset(asset_id=f'caption-{i}', asset_type='caption', page_no=4,
            derived_from_asset_id=source.asset_id, text_content=raw_caption, source_locator=metadata)
        content = MARKER + raw_caption
        start = len(text)
        text += content
        sections.append(DocumentSection(section_id=f'caption-section-{i}',heading='',level=0,
            start=start,end=len(text),page_start=4,page_end=4,content_type='image_caption',asset_id=caption.asset_id))
        blocks.append(DocumentBlock(block_id=f'caption-block-{i}',kind='text',start=start,end=len(text)))
        locators.append(SourceLocator(kind='pdf',page=4,bbox=bbox,start=start,end=len(text),
            quote=content,asset_id=caption.asset_id,raw_evidence=metadata))
        assets.extend([source,caption])
    return NormalizedDocument(document_id='synthetic-doc',version_id='synthetic-version',title='SIMULATED',
        media_type='application/pdf',markdown_content=text,sections=sections,tables=tables,blocks=blocks,
        assets=assets,source_locators=locators,content_sha256=hashlib.sha256(text.encode()).hexdigest(),
        parser_version='SIMULATED/v1'), native, ocr


@pytest.mark.parametrize('mixed',[False,True])
def test_caption_provenance_and_original_native_ocr_quote_readback(mixed):
    doc,native,ocr = synthetic_document(mixed=mixed)
    before = doc.model_dump_json()
    chunks = chunk_document(doc)
    assert doc.model_dump_json() == before
    assert ''.join(c.content for c in chunks) == doc.markdown_content
    assert doc.markdown_content.startswith(native+ocr)
    assert ''.join(c.content for c in chunks if c.chunk_type!='image_caption' and c.chunk_type!='table') == native+ocr
    caption = next(c for c in chunks if c.chunk_type=='image_caption')
    assert caption.content.startswith(MARKER)
    assert caption.source_locator.kind == 'pdf' and caption.source_locator.page == 4
    assert caption.source_locator.asset_id == 'caption-0'
    assert caption.source_locator.bbox == (20,80,90,160)
    assert caption.source_locator.raw_evidence['evidence_kind'] == 'model_generated_caption'
    assert not caption.source_locator.cells and caption.source_locator.table_id is None
    record = ChunkRecord('caption-chunk','kb',doc.document_id,doc.version_id,caption.content,
                         json.loads(caption.source_locator.model_dump_json()))
    service = CitationService(InMemoryCitationStore())
    frozen = service.freeze('run',record)
    reread = service.resolve('run',frozen.citation_id)
    assert reread.quote == caption.content and reread.locator == record.locator
    assert doc.markdown_content[caption.start:caption.end] == reread.quote


def test_unknown_bbox_stays_unknown_without_inventing_region():
    doc,_,_ = synthetic_document(unknown=True)
    caption = next(c for c in chunk_document(doc) if c.chunk_type=='image_caption')
    assert caption.source_locator.bbox is None
    assert caption.source_locator.raw_evidence['coordinate_basis']=='UNKNOWN'


def test_same_page_multiple_assets_keep_their_own_regions_and_hashes():
    doc,_,_ = synthetic_document(count=2)
    captions = [c for c in chunk_document(doc) if c.chunk_type=='image_caption']
    assert [c.source_locator.asset_id for c in captions]==['caption-0','caption-1']
    assert captions[0].source_locator.bbox != captions[1].source_locator.bbox
    assert captions[0].source_locator.raw_evidence['source_image_sha256'] != captions[1].source_locator.raw_evidence['source_image_sha256']


@pytest.mark.parametrize('fault',['hash','source_bytes','derived','bbox','page','table','marker','failed'])
def test_invalid_caption_source_contract_is_rejected(fault):
    doc,_,_ = synthetic_document()
    if fault=='hash':
        altered=dict(doc.assets[1].source_locator);altered['source_image_sha256']='0'*64
        doc=doc.model_copy(update={'assets':[doc.assets[0],doc.assets[1].model_copy(update={'source_locator':altered})]})
    elif fault=='source_bytes':
        doc=doc.model_copy(update={'assets':[doc.assets[0].model_copy(update={'source_bytes':b'changed'}),doc.assets[1]]})
    elif fault=='derived':
        doc=doc.model_copy(update={'assets':[doc.assets[0],doc.assets[1].model_copy(update={'derived_from_asset_id':'absent'})]})
    elif fault=='bbox':
        doc=doc.model_copy(update={'source_locators':[doc.source_locators[0].model_copy(update={'bbox':(1,2,3,4)})]})
    elif fault=='page':
        doc=doc.model_copy(update={'source_locators':[doc.source_locators[0].model_copy(update={'page':3})]})
    elif fault=='table':
        doc=doc.model_copy(update={'source_locators':[doc.source_locators[0].model_copy(update={'kind':'table','table_id':'invented'})]})
    elif fault=='marker':
        doc=doc.model_copy(update={'markdown_content':doc.markdown_content.replace(MARKER,'X'*len(MARKER))})
    else:
        doc=doc.model_copy(update={'assets':[doc.assets[0],doc.assets[1].model_copy(update={'status':'failed'})]})
    with pytest.raises(ValueError,match='CAPTION_'):
        chunk_document(doc)


def test_caption_is_not_split_into_unmarked_chunks():
    doc,_,_=synthetic_document()
    with pytest.raises(ValueError,match='CAPTION_SECTION_TOO_LARGE'):
        chunk_document(doc,max_chars=20,overlap=0)


def test_default_off_matches_saved_parser_and_chunker(tmp_path):
    root=Path(os.environ['RAG_CAPTION_A_BEFORE'])
    old_parser=runpy.run_path(str(root/'backend/app/adapters/parsers/__init__.py'))['PdfParser']
    old_chunker=runpy.run_path(str(root/'backend/app/domain/chunking.py'))['chunk_document']
    path=tmp_path/'native.pdf'
    with fitz.open() as pdf:
        pdf.new_page().insert_text((40,70),'Native text 123')
        pdf.save(path)
    a=old_parser(tessdata=tmp_path/'missing').parse(path,'doc','v')
    b=PdfParser(tessdata=tmp_path/'missing',collect_caption_geometry=False).parse(path,'doc','v')
    assert a.model_dump_json()==b.model_dump_json()
    assert [c.model_dump_json() for c in old_chunker(a)]==[c.model_dump_json() for c in chunk_document(b)]


def test_geometry_collection_is_explicit_and_never_generates_caption(tmp_path):
    image=tmp_path/'synthetic.png';Image.new('RGB',(20,10),'white').save(image)
    path=tmp_path/'mixed.pdf'
    with fitz.open() as pdf:
        p=pdf.new_page();p.insert_text((40,70),'Native layer')
        p.insert_image(fitz.Rect(50,100,250,200),filename=str(image))
        pdf.save(path)
    doc=PdfParser(tessdata=tmp_path/'missing',collect_caption_geometry=True).parse(path,'doc','v')
    source=next(a for a in doc.assets if a.asset_type=='source_image')
    assert tuple(source.source_locator['bbox'])==(50,100,250,200)
    assert source.source_locator['coordinate_basis']=='pdf-points-top-left-unrotated'
    assert source.source_locator['sha256']==hashlib.sha256(source.source_bytes).hexdigest()
    assert all(a.asset_type!='caption' for a in doc.assets)
    assert all(c.chunk_type!='image_caption' for c in chunk_document(doc))


def merged_caption_blocks(doc, *, include_native=False):
    blocks=list(doc.blocks)
    index=next(i for i,b in enumerate(blocks) if b.block_id=='caption-block-0')
    first=index-1 if include_native else index
    merged=DocumentBlock(block_id='merged-review-block',kind='text',
        start=blocks[first].start,end=blocks[first+1].end)
    return doc.model_copy(update={'blocks':blocks[:first]+[merged]+blocks[first+2:]})


@pytest.mark.parametrize('mixed',[False,True])
@pytest.mark.parametrize('fault',['derived','hash','marker','long'])
def test_review_merged_blocks_cannot_hide_invalid_caption(mixed,fault):
    doc,_,_=synthetic_document(mixed=mixed,count=2)
    doc=merged_caption_blocks(doc)
    if fault=='derived':
        assets=list(doc.assets);assets[1]=assets[1].model_copy(update={'derived_from_asset_id':'absent'})
        doc=doc.model_copy(update={'assets':assets})
    elif fault=='hash':
        assets=list(doc.assets);metadata=dict(assets[1].source_locator);metadata['source_image_sha256']='0'*64
        assets[1]=assets[1].model_copy(update={'source_locator':metadata})
        doc=doc.model_copy(update={'assets':assets})
    elif fault=='marker':
        doc=doc.model_copy(update={'markdown_content':doc.markdown_content.replace(MARKER,'X'*len(MARKER),1)})
    with pytest.raises(ValueError,match='CAPTION_'):
        chunk_document(doc,max_chars=20 if fault=='long' else 1200,overlap=0)


@pytest.mark.parametrize('mixed',[False,True])
@pytest.mark.parametrize('boundary',['caption','native'])
def test_review_caption_intersection_never_falls_back_to_plain_text(mixed,boundary):
    doc,_,_=synthetic_document(mixed=mixed,count=2,native_tail=boundary=='native')
    doc=merged_caption_blocks(doc,include_native=boundary=='native')
    with pytest.raises(ValueError,match='CAPTION_'):
        chunk_document(doc)


@pytest.mark.parametrize('mixed',[False,True])
def test_review_overlapping_native_section_cannot_swallow_caption(mixed):
    doc,_,_=synthetic_document(mixed=mixed,native_tail=True)
    caption=next(s for s in doc.sections if s.content_type=='image_caption')
    sections=[s.model_copy(update={'end':caption.end}) if s.section_id=='native-tail' else s for s in doc.sections]
    doc=doc.model_copy(update={'sections':sections})
    with pytest.raises(ValueError,match='CAPTION_'):
        chunk_document(doc)


@pytest.mark.parametrize('fault',['basis','bool_bbox','unknown_basis','string_bbox','nonfinite_bbox'])
def test_review_source_coordinates_need_matching_units_and_numeric_bounds(fault):
    doc,_,_=synthetic_document(unknown=fault=='unknown_basis')
    assets=list(doc.assets);source_metadata=dict(assets[0].source_locator)
    if fault=='basis':source_metadata['coordinate_basis']='image-pixels-top-left'
    elif fault=='unknown_basis':source_metadata['coordinate_basis']='pdf-points-top-left-unrotated'
    elif fault=='bool_bbox':
        source_metadata['bbox']=(False,False,90,160)
        metadata=dict(assets[1].source_locator);metadata['bbox']=(0,0,90,160)
        assets[1]=assets[1].model_copy(update={'source_locator':metadata})
        doc=doc.model_copy(update={'source_locators':[doc.source_locators[0].model_copy(
            update={'bbox':(0,0,90,160),'raw_evidence':metadata})]})
    elif fault=='string_bbox':source_metadata['bbox']=('20','80','90','160')
    else:source_metadata['bbox']=(20,80,float('inf'),160)
    assets[0]=assets[0].model_copy(update={'source_locator':source_metadata})
    doc=doc.model_copy(update={'assets':assets})
    with pytest.raises(ValueError,match='CAPTION_'):
        chunk_document(doc)


def canonical_asset_ids(document, value):
    encoded=json.dumps(value,ensure_ascii=False,sort_keys=True)
    for i,asset in enumerate(document.assets):encoded=encoded.replace(asset.asset_id,f'asset-{i}')
    return encoded


@pytest.mark.parametrize('mixed',[False,True])
def test_review_legal_native_ocr_and_caption_sections_keep_types(mixed):
    doc,native,ocr=synthetic_document(mixed=mixed,count=2)
    blocks=[DocumentBlock(block_id='native',kind='text',start=0,end=len(native)),
        DocumentBlock(block_id='ocr',kind='text',start=len(native),end=len(native+ocr)),*doc.blocks[1:]]
    doc=doc.model_copy(update={'blocks':blocks})
    chunks=chunk_document(doc)
    expected=['text','image_ocr']+(['table'] if mixed else [])+['image_caption','image_caption']
    assert [c.chunk_type for c in chunks]==expected
    assert chunks[0].content==native and chunks[1].content==ocr
    assert all(c.content.startswith(MARKER) for c in chunks if c.chunk_type=='image_caption')


@pytest.mark.parametrize('kind',['multiple_pdf_images','standalone_image','saved_ocr'])
def test_review_default_off_matches_before_for_multimodal_sources(tmp_path,kind):
    root=Path(os.environ['RAG_CAPTION_A_BEFORE'])
    previous=runpy.run_path(str(root/'backend/app/adapters/parsers/__init__.py'))
    old_chunker=runpy.run_path(str(root/'backend/app/domain/chunking.py'))['chunk_document']
    if kind=='saved_ocr':
        fixture=root.parent.parent/'multimodal-acceptance-fixture-20261004/ocr-smoke/product-normalized.json'
        doc=NormalizedDocument.model_validate_json(fixture.read_text(encoding='utf-8'))
        assert '四摄氏\n度' in doc.markdown_content and '二O〇' in doc.markdown_content
        assert [c.model_dump_json() for c in old_chunker(doc)]==[c.model_dump_json() for c in chunk_document(doc)]
        return
    image=tmp_path/'synthetic.png';Image.new('RGB',(20,10),'white').save(image)
    if kind=='standalone_image':
        a=previous['ImageParser'](tessdata=tmp_path/'missing').parse(image,'doc','v')
        b=ImageParser(tessdata=tmp_path/'missing',collect_caption_geometry=False).parse(image,'doc','v')
    else:
        path=tmp_path/'multiple.pdf'
        with fitz.open() as pdf:
            p=pdf.new_page();p.insert_text((40,70),'Native layer')
            p.insert_image(fitz.Rect(50,100,250,200),filename=str(image))
            p.insert_image(fitz.Rect(300,200,500,300),filename=str(image))
            pdf.save(path)
        a=previous['PdfParser'](tessdata=tmp_path/'missing').parse(path,'doc','v')
        b=PdfParser(tessdata=tmp_path/'missing',collect_caption_geometry=False).parse(path,'doc','v')
        assert len([asset for asset in b.assets if asset.asset_type=='source_image'])>=2
    assert canonical_asset_ids(a,a.model_dump(mode='json'))==canonical_asset_ids(b,b.model_dump(mode='json'))
    assert canonical_asset_ids(a,[c.model_dump(mode='json') for c in old_chunker(a)])==canonical_asset_ids(b,[c.model_dump(mode='json') for c in chunk_document(b)])
