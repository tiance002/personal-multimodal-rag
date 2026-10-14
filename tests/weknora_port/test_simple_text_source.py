import base64,json
from pathlib import Path
from backend.weknora_port.ingestion.core import _decode_simple_text,DocumentRequest,parse_document


def test_source_go_simple_text_same_bytes():
    rows=[json.loads(line) for line in Path('docs/weknora-port/evidence/P2_SIMPLE_TEXT_GO_20261014.jsonl').read_text(encoding='utf-8-sig').splitlines()]
    assert len(rows)==8
    for row in rows:
        raw=base64.b64decode(row['input']);assert _decode_simple_text(raw)==row['content']
        for filename in ('source.txt','source.md'):
            _,parsed=parse_document(DocumentRequest(filename,raw))
            assert parsed.content==row['content'] and not parsed.failures
