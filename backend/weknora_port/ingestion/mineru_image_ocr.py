"""Fill empty MinerU image bodies with the same local ONNX text recognizer.

The SDK middle bytes remain unchanged. Recognition and its pixel coordinates
are saved separately; a derived middle projection is explicit, never a chapter
classifier or a replacement for Caption/visual reasoning.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import io
import json
import math
from pathlib import PurePosixPath
import zipfile

SCHEMA = 'weknora.mineru-image-ocr/v1'
MAX_IMAGES = 256
MAX_PIXELS = 40_000_000


def digest(data): return hashlib.sha256(data).hexdigest()


def image_bodies(middle):
    def visit(block, path, page):
        if block.get('type') == 'image_body' and not block.get('content') and block.get('image_path'):
            yield page, path, block
        content = block.get('content')
        if isinstance(content, list):
            for index, child in enumerate(content):
                if isinstance(child, dict): yield from visit(child, path + '/content/' + str(index), page)
    for page_pos, page in enumerate(middle.get('pages', [])):
        for block_pos, block in enumerate(page.get('blocks', [])):
            yield from visit(block, f'/pages/{page_pos}/blocks/{block_pos}', page['page_idx'])


def _asset(files, path):
    parts = PurePosixPath(path)
    if (not isinstance(path, str) or not path or '\\' in path or ':' in path
            or parts.is_absolute() or any(p in ('', '.', '..') for p in path.split('/')) or path not in files):
        raise ValueError('MINERU_IMAGE_OCR_ASSET_INVALID')
    return files[path]


def _lines(raw, width, height):
    # Keep recognizer order and exact characters, without title/section guessing.
    if not isinstance(raw, list) or len(raw) != 1 or raw[0] is not None and not isinstance(raw[0], list):
        raise ValueError('MINERU_IMAGE_OCR_RESULT_INVALID')
    lines, position = [], 0
    for item in raw[0] or []:
        box, recognition = item
        text, score = recognition
        if (not isinstance(text, str) or not isinstance(score, (int, float)) or not math.isfinite(score)
                or not 0 <= score <= 1 or not isinstance(box, list) or len(box) != 4):
            raise ValueError('MINERU_IMAGE_OCR_RESULT_INVALID')
        for point in box:
            if len(point) != 2 or not all(type(v) in (int, float) and math.isfinite(v) for v in point):
                raise ValueError('MINERU_IMAGE_OCR_RESULT_INVALID')
            if not 0 <= point[0] <= width or not 0 <= point[1] <= height:
                raise ValueError('MINERU_IMAGE_OCR_COORDINATE_INVALID')
        if not text: continue
        if lines: position += 1
        lines.append({'text': text, 'confidence': score, 'polygon_pixels': box,
                      'start': position, 'end': position + len(text)})
        position += len(text)
    return lines


def supplement_archive(archive, source_sha256, recognize):
    from PIL import Image
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        files = {n: zipped.read(n) for n in zipped.namelist() if not n.endswith('/')}
    if 'image_ocr.json' in files or 'rag_middle_json.json' in files:
        raise ValueError('MINERU_IMAGE_OCR_ALREADY_PRESENT')
    middle = json.loads(files['middle_json.json'])
    records, omitted = [], []
    for page, path, block in image_bodies(middle):
        data = _asset(files, block['image_path'])
        with Image.open(io.BytesIO(data)) as image: width, height = image.size
        if len(records) >= MAX_IMAGES or width * height > MAX_PIXELS:
            omitted.append({'page_idx': page, 'block_path': path, 'reason': 'LOCAL_IMAGE_OCR_BUDGET'})
            continue
        raw = recognize(data)
        lines = _lines(raw, width, height)
        records.append({'page_idx': page, 'block_path': path, 'image_path': block['image_path'],
            'asset_sha256': digest(data), 'width': width, 'height': height, 'raw': raw, 'lines': lines,
            'text': '\n'.join(line['text'] for line in lines)})
    overlay = {'schema': SCHEMA, 'producer': {'name': 'mineru', 'version': '4.0.12'},
        'model': 'MinerU PP-OCRv6 ONNX', 'source_sha256': source_sha256,
        'sdk_middle_sha256': digest(files['middle_json.json']), 'records': records, 'omitted': omitted,
        'title_classification': 'NOT_INFERRED', 'cross_page_section_links': 'NOT_INFERRED'}
    files['image_ocr.json'] = json.dumps(overlay, ensure_ascii=False).encode('utf-8')
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as zipped:
        for name, data in files.items(): zipped.writestr(name, data)
    return output.getvalue()


def apply_image_ocr(files, source_sha256):
    """Validate the saved overlay against the immutable SDK middle and crops."""
    original = files['middle_json.json']
    middle = json.loads(original)
    if 'image_ocr.json' not in files: return middle, None
    overlay = json.loads(files['image_ocr.json'])
    if (overlay.get('schema') != SCHEMA or overlay.get('source_sha256') != source_sha256
            or overlay.get('sdk_middle_sha256') != digest(original)
            or overlay.get('producer') != {'name': 'mineru', 'version': '4.0.12'}):
        raise ValueError('MINERU_IMAGE_OCR_IDENTITY_INVALID')
    projected = deepcopy(middle)
    eligible = {path: (page, block) for page, path, block in image_bodies(projected)}
    seen = set()
    for record in overlay.get('records', []):
        path = record['block_path']
        if path not in eligible or path in seen: raise ValueError('MINERU_IMAGE_OCR_BLOCK_INVALID')
        seen.add(path)
        page, block = eligible[path]
        data = _asset(files, block['image_path'])
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image: width, height = image.size
        if (record['page_idx'] != page or record['image_path'] != block['image_path']
                or record['asset_sha256'] != digest(data) or (width, height) != (record['width'], record['height'])):
            raise ValueError('MINERU_IMAGE_OCR_ASSET_IDENTITY_INVALID')
        lines = _lines(record['raw'], width, height)
        if lines != record['lines'] or '\n'.join(line['text'] for line in lines) != record['text']:
            raise ValueError('MINERU_IMAGE_OCR_TEXT_INVALID')
        block['content'] = record['text']
        block['image_ocr'] = {'sidecar': {'logical_path': 'mineru/image_ocr.json',
            'sha256': digest(files['image_ocr.json'])}, 'asset_sha256': record['asset_sha256'],
            'width': width, 'height': height, 'lines': lines, 'coordinate_space': 'asset_pixels',
            'origin': 'local_mineru_image_text'}
    projected.setdefault('extensions', {})['weknora_image_ocr'] = {
        'schema': SCHEMA, 'sdk_middle_sha256': digest(original),
        'sidecar_sha256': digest(files['image_ocr.json']),
        'title_classification': 'NOT_INFERRED', 'cross_page_section_links': 'NOT_INFERRED'}
    derived = json.dumps(projected, ensure_ascii=False).encode('utf-8')
    files['rag_middle_json.json'] = derived
    return projected, {'logical_path': 'mineru/rag_middle_json.json', 'sha256': digest(derived),
                       'sdk_middle_sha256': digest(original), 'supplemented_images': len(seen),
                       'omitted': overlay.get('omitted', [])}


def local_recognizer():
    # Only imported in the existing isolated MinerU environment, network denied
    # by mineru_runner before imports. Reuse its configured local ONNX models.
    from mineru.model.runtime.hybrid import AtomModelSingleton, AtomicModelName
    model = AtomModelSingleton().get_atom_model(AtomicModelName.OCR, small_backend='onnx', device='cpu')
    return model.ocr
