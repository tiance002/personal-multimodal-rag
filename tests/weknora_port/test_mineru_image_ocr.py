"""Image text supplement is source-bound, local and never a chapter guess."""
import copy
import hashlib
import io
import json
import zipfile

import pytest
from PIL import Image

from backend.weknora_port.ingestion.mineru_image_ocr import apply_image_ocr, supplement_archive
from tests.weknora_port.test_mineru import request, archive, normalize_archive, document_metadata


def fixture():
    from tests.weknora_port.test_mineru import middle
    raw = middle()
    raw['pages'][0]['blocks'] = [{'type': 'image', 'index': 0, 'bbox': [0, 0, 1, 1], 'content': [
        {'type': 'image_body', 'index': 0, 'bbox': [.1, .1, .9, .9],
         'image_path': 'images/figure.png', 'content': ''}]}]
    data = io.BytesIO(); Image.new('RGB', (100, 80)).save(data, 'PNG')
    req = request(); payload = archive(raw, {'images/figure.png': data.getvalue()})
    return req, payload


def recognized(data):
    assert data.startswith(b'\x89PNG')
    return [[[[[10, 5], [70, 5], [70, 15], [10, 15]], ['Visible heading!', .99]],
             [[[10, 25], [70, 25], [70, 35], [10, 35]], ['source body', .95]]]]


def files(payload):
    with zipfile.ZipFile(io.BytesIO(payload)) as z: return {n: z.read(n) for n in z.namelist()}


def test_empty_image_gets_same_engine_text_with_unchanged_sdk_and_separate_derived_proof():
    req, payload = fixture(); sha = hashlib.sha256(req.data).hexdigest()
    before = files(payload)
    supplemented = supplement_archive(payload, sha, recognized)
    after = files(supplemented)
    assert before['middle_json.json'] == after['middle_json.json']
    converted = normalize_archive(req, supplemented, {'source_sha256': sha})
    assert converted.parsed.content == 'Visible heading!\nsource body'
    identity = converted.parsed.metadata['mineru_source']
    assert identity['middle']['logical_path'] == 'mineru/rag_middle_json.json'
    assert identity['image_ocr_projection']['sdk_middle_sha256'] == hashlib.sha256(before['middle_json.json']).hexdigest()
    assert converted.parsed.metadata['mineru_sections'] == []  # do not infer titles from OCR text
    locator = converted.normalized.source_locators[0]
    assert locator.raw_evidence['block_type'] == 'image_body'
    assert locator.raw_evidence['image_ocr']['lines'][0]['polygon_pixels'][0] == [10, 5]
    assert locator.raw_evidence['image_ocr']['lines'][0]['end'] == len('Visible heading!')
    assert locator.quote == converted.parsed.content[locator.start:locator.end]
    from backend.weknora_port.ingestion.mineru import chunk_locators, public_chunk_locators
    public = public_chunk_locators(chunk_locators(converted.parsed, 0, len(converted.parsed.content)),
        document_metadata(converted.parsed), 'document', 'version', 0,
        len(converted.parsed.content), converted.parsed.content)
    assert public['source_locators'][0]['quote'] == converted.parsed.content
    assert public['parser_evidence']['middle']['logical_path'] == 'mineru/rag_middle_json.json'
    assert public['source_locators'][0]['raw_evidence']['image_ocr']['origin'] == 'local_mineru_image_text'
    assert public['source_locators'][0]['raw_evidence']['image_ocr']['text_offset_space'] == 'image_ocr_body_unicode'


@pytest.mark.parametrize('field', ['source_sha256', 'asset_sha256', 'text', 'page_idx', 'width'])
def test_tampered_overlay_never_becomes_source_text(field):
    req, payload = fixture(); sha = hashlib.sha256(req.data).hexdigest()
    content = files(supplement_archive(payload, sha, recognized))
    overlay = json.loads(content['image_ocr.json'])
    target = overlay if field == 'source_sha256' else overlay['records'][0]
    target[field] = 'tampered' if field not in ('page_idx', 'width') else 999
    content['image_ocr.json'] = json.dumps(overlay).encode()
    with pytest.raises((ValueError, TypeError)):
        apply_image_ocr(content, sha)


def test_already_extracted_image_text_is_not_ocrd_twice():
    req, payload = fixture(); content = files(payload); middle = json.loads(content['middle_json.json'])
    middle['pages'][0]['blocks'][0]['content'][0]['content'] = 'Already recognized'
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as z:
        for name, data in content.items():
            z.writestr(name, json.dumps(middle).encode() if name == 'middle_json.json' else data)
    def forbidden(_): raise AssertionError('duplicate OCR')
    augmented = supplement_archive(output.getvalue(), hashlib.sha256(req.data).hexdigest(), forbidden)
    assert json.loads(files(augmented)['image_ocr.json'])['records'] == []


def test_parser_titles_project_real_page_sections_without_rewriting_raw_text():
    from tests.weknora_port.test_mineru import middle, convert
    raw = middle()
    raw['pages'][0]['blocks'] = [
        {'type': 'doc_title', 'index': 0, 'bbox': [.1, .1, .9, .2], 'level': 1,
         'content': [{'type': 'text', 'content': 'Actual source title'}]},
        {'type': 'text', 'index': 1, 'bbox': [.1, .3, .9, .4], 'content': 'original body'}]
    result = convert(raw=raw)
    assert result.parsed.content == 'Actual source title\n\noriginal body'
    section = result.parsed.metadata['mineru_sections'][0]
    assert section['heading'] == result.parsed.content[section['heading_start']:section['heading_end']]
    assert section['source_version'] == 'version' and section['page_start'] == section['page_end'] == 1
    assert result.normalized.sections[0].heading == 'Actual source title'
