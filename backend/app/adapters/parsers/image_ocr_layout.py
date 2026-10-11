"""Local image OCR geometry from the same native TextPage as preserved text."""
from __future__ import annotations
import math


def textpage_layout(textpage, *, text, image_size, page_rect):
    """Retain unsorted native block/line sequence; never locate text by search."""
    width, height = image_size
    x0, y0, x1, y1 = tuple(page_rect)
    if x1 <= x0 or y1 <= y0 or width <= 0 or height <= 0:
        raise ValueError('image_ocr_coordinate_basis_invalid')
    sx, sy = width/(x1-x0), height/(y1-y0)
    def pixels(bbox):
        if len(bbox) != 4 or any(not math.isfinite(v) for v in bbox):
            raise ValueError('image_ocr_bbox_invalid')
        a,b,c,d = bbox
        result = [(a-x0)*sx,(b-y0)*sy,(c-x0)*sx,(d-y0)*sy]
        if not 0 <= result[0] < result[2] <= width or not 0 <= result[1] < result[3] <= height:
            raise ValueError('image_ocr_bbox_out_of_image')
        return result
    blocks, lines, pieces, cursor = [], [], [], 0
    for block_position, block in enumerate(textpage.extractDICT(sort=False)['blocks']):
        if block.get('type') != 0:
            continue
        block_lines = []
        for line_position, line in enumerate(block['lines']):
            value = ''.join(span['text'] for span in line['spans'])
            if not value:
                continue
            record = {'text':value, 'bbox':pixels(line['bbox']), 'start':cursor,
                      'end':cursor+len(value), 'order':len(lines),
                      'native_block_position':block_position, 'native_line_position':line_position}
            lines.append(record);block_lines.append(value);pieces.append(value+'\n')
            cursor += len(value)+1
        if block_lines:
            blocks.append({'text':'\n'.join(block_lines), 'bbox':pixels(block['bbox']),
                           'order':block_position, 'category_type':'text_block',
                           'native_block_position':block_position})
    original = ''.join(pieces)
    if original.strip() != text:
        raise ValueError('image_ocr_structured_text_alignment_unavailable')
    trim = len(original)-len(original.lstrip())
    kept = []
    for line in lines:
        start, end = max(0,line['start']-trim), min(len(text),line['end']-trim)
        if start < end:
            kept.append({**line, 'start':start, 'end':end, 'text':text[start:end]})
    return {'schema':'image-ocr-layout-v1', 'coordinate_basis':'image-pixels-top-left',
            'order_basis':'PYMUPDF_UNSORTED_NATIVE_OCR_BLOCK_LINE_SEQUENCE',
            'category_basis':'OCR_TEXT_BLOCK_ONLY_NO_TABLE_FORMULA_CLASSIFIER',
            'text_alignment':'EXACT_NATIVE_TEXT', 'lines':kept, 'blocks':blocks}


def structured_image_ocr(page, *, tessdata, image_size):
    """Image-only triple; PDF _ocr tuple ABI remains untouched."""
    if not all((tessdata/f'{name}.traineddata').is_file() for name in ('eng','chi_sim')):
        return '', 'OCR_UNAVAILABLE', None
    try:
        tp = page.get_textpage_ocr(language='eng+chi_sim',dpi=150,full=True,tessdata=str(tessdata.resolve()))
        text = page.get_text(textpage=tp).strip()
    except Exception:
        return '', 'OCR_UNAVAILABLE', None
    if not text:
        return '', 'OCR_EMPTY', None
    try:
        layout = textpage_layout(tp,text=text,image_size=image_size,page_rect=page.rect)
    except (ValueError,KeyError,TypeError) as error:
        # Preserve text even when native geometry/text alignment is incomplete.
        layout = {'schema':'image-ocr-layout-v1','status':'GEOMETRY_UNAVAILABLE','reason':str(error),
                  'coordinate_basis':'image-pixels-top-left','blocks':[],'lines':[]}
    return text, None, layout
