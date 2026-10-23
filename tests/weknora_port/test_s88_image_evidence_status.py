"""Incomplete image derivatives remain viewable but cannot reenter evidence."""
import json

import pytest

from backend.weknora_port.retrieval.context import enriched_chat_passage
from backend.weknora_port.retrieval.merge import _parent_image_infos
from backend.weknora_port.retrieval.rerank import enriched_passage, image_infos
from backend.weknora_port.retrieval.types import SearchResult


@pytest.mark.parametrize('status', ['failed', 'pending', 'unavailable', 'cancelled'])
def test_incomplete_caption_is_excluded_from_rerank_context_and_parent_aggregation(status):
    raw = json.dumps([{'url': 'image.png', 'caption': 'INCOMPLETE_CAPTION',
                       'caption_status': {'status': status, 'finish_reason': 'length'},
                       'ocr_text': 'valid OCR', 'ocr_status': {'status': 'completed'}}])
    row = SearchResult('chunk', 'source ![chart](image.png)', .5, image_info=raw)
    assert image_infos(raw)[0]['caption'] == ''
    assert 'INCOMPLETE_CAPTION' not in enriched_passage(row)
    assert 'INCOMPLETE_CAPTION' not in enriched_chat_passage(row)
    assert 'valid OCR' in enriched_passage(row) and 'valid OCR' in enriched_chat_passage(row)
    assert _parent_image_infos(raw)[0]['caption'] == ''
    assert _parent_image_infos(raw)[0]['ocr_text'] == 'valid OCR'
    assert json.loads(raw)[0]['caption'] == 'INCOMPLETE_CAPTION'


def test_failed_ocr_is_excluded_independently_and_legacy_completed_text_is_preserved():
    raw = json.dumps([{'caption': 'valid caption', 'caption_status': {'status': 'completed'},
                       'ocr_text': 'INCOMPLETE_OCR', 'ocr_status': {'status': 'failed'}},
                      {'caption': 'legacy caption', 'ocr_text': 'legacy OCR'},
                      {'caption': 'legacy no status value', 'caption_status': {}}])
    for decode in [image_infos, _parent_image_infos]:
        actual = decode(raw)
        assert actual[0]['caption'] == 'valid caption' and actual[0]['ocr_text'] == ''
        assert actual[1]['caption'] == 'legacy caption' and actual[1]['ocr_text'] == 'legacy OCR'
        assert actual[2]['caption'] == 'legacy no status value'
