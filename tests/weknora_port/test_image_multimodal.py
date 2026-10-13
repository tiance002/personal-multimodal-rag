import json
from pathlib import Path
import pytest
from backend.weknora_port.image_multimodal import PROMPTS,caption_prompt,sanitize_ocr,enrich_summary_images
from backend.weknora_port.documents import DocumentLifecycleError

def test_pinned_go_image_functions_same_fixture():
    value=json.loads((Path(__file__).parent/'fixtures/image_functions_go_oracle.json').read_text(encoding='utf-8'))
    assert value['exit_code']==0 and value['provider_calls']==0 and len(value['rows'])==11
    for row in value['rows']:
        if row['kind']=='ocr':actual=sanitize_ocr(value['cases'][row['index']])
        elif row['kind']=='ocr_prompt':actual=PROMPTS['vlmOCRScannedPDFPrompt' if row['source']=='scanned_pdf' else 'vlmOCRPrompt']
        elif row['kind']=='caption':actual=caption_prompt('Chinese (Simplified)',' CAPTION ONLY ')
        else:actual=enrich_summary_images('body ![alt](image.png)',value['infos'],row['full'])
        assert actual==row['output']

def test_html_ocr_converter_explicitly_unavailable():
    with pytest.raises(DocumentLifecycleError,match='OCR_HTML_SOURCE_CONVERTER_REQUIRED'):
        sanitize_ocr('<p>This is substantial HTML OCR content.</p>')
