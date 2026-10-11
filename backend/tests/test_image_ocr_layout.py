"""Native local OCR plus SIMULATED malformed geometry, no network/model calls."""
from types import SimpleNamespace
from pathlib import Path
import fitz
import pytest
from backend.app.adapters.parsers import ImageParser, PdfParser
from backend.app.adapters.parsers.image_ocr_layout import textpage_layout
from eval_center.scoring_mappers import omni_prediction_from_normalized
import hashlib


def native_dict():
    return {'blocks':[{'type':0,'bbox':[5,10,40,40], 'lines':[
        {'bbox':[5,10,40,20],'spans':[{'text':'same'}]},
        {'bbox':[5,30,40,40],'spans':[{'text':'same'}]}]}]}


def test_structured_offsets_duplicate_lines_and_pixel_transform():
    tp=SimpleNamespace(extractDICT=lambda sort:native_dict())
    layout=textpage_layout(tp,text='same\nsame',image_size=(200,200),page_rect=(0,0,100,100))
    assert [(r['start'],r['end']) for r in layout['lines']]==[(0,4),(5,9)]
    assert layout['lines'][1]['bbox']==[10,60,80,80]
    assert [r['order'] for r in layout['lines']]==[0,1]
    assert layout['blocks'][0]['bbox']==[10,20,80,80]
    with pytest.raises(ValueError,match='alignment'):
        textpage_layout(tp,text='same same',image_size=(200,200),page_rect=(0,0,100,100))


def test_no_whole_image_or_gold_fallback_on_bad_geometry():
    bad=native_dict();bad['blocks'][0]['bbox']=[-1,0,100,100]
    tp=SimpleNamespace(extractDICT=lambda sort:bad)
    with pytest.raises(ValueError,match='out_of_image'):
        textpage_layout(tp,text='same\nsame',image_size=(200,200),page_rect=(0,0,100,100))
    n={'source_locators':[{'raw_evidence':{'ocr_layout':{'schema':'image-ocr-layout-v1',
        'status':'GEOMETRY_UNAVAILABLE','blocks':[]}}}]}
    result=omni_prediction_from_normalized({'page_id':'p','image_sha256':'a'*64,'width':200,'height':200},n,
        source_image_sha256='a'*64)
    assert result['prediction'] is None


def test_actual_synthetic_image_ocr_retains_native_text_assets_and_layout(tmp_path):
    # Render an image without supplying expected text or geometry to the parser.
    with fitz.open() as pdf:
        p=pdf.new_page(width=400,height=200)
        p.insert_text((30,60),'Alpha Evidence',fontsize=24)
        p.insert_text((30,120),'Beta Evidence',fontsize=24)
        image=tmp_path/'synthetic.png';p.get_pixmap(matrix=fitz.Matrix(2,2)).save(image)
    document=ImageParser(tessdata=Path('var/tessdata'),collect_caption_geometry=True).parse(image,'d','v')
    assert 'Alpha' in document.markdown_content and 'Beta' in document.markdown_content
    layout=document.source_locators[0].raw_evidence['ocr_layout']
    assert layout['text_alignment']=='EXACT_NATIVE_TEXT' and len(layout['lines'])>=2
    assert all(document.markdown_content[r['start']:r['end']]==r['text'] for r in layout['lines'])
    assert document.source_locators[0].quote==document.markdown_content
    assert document.assets[0].source_bytes==image.read_bytes()
    assert document.assets[1].text_content==document.markdown_content
    sha=hashlib.sha256(image.read_bytes()).hexdigest()
    prediction=omni_prediction_from_normalized({'page_id':'synthetic','image_sha256':sha,'width':800,'height':400},
        document,source_image_sha256=sha)['prediction']
    assert prediction and len(prediction['blocks'])>=1
    assert all(b['poly']!=[0,0,800,0,800,400,0,400] for b in prediction['blocks'])
    assert PdfParser._ocr.__annotations__['return']=='tuple[str, str | None]'
